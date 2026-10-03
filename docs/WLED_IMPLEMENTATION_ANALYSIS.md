# WLED-Preset-Integration: Implementierungsanalyse

Status: ausschließlich Analyse; auf diesem Branch existiert noch kein WLED-Produktionscode.

Analysegrundlage:

- GitHub Issue [#1528](https://github.com/maziggy/bambuddy/issues/1528)
- Branch `feature/wled-presets`
- `upstream/dev` auf Commit `309e64b8a2ba5a194bc91d8e844f1f33df71a89c`
- Der Branch-HEAD entspricht demselben Commit; es gibt keinen bereits vorhandenen Branch-Diff.

## Umfang und bewusste Einschränkungen

Die gewünschte Umsetzung ist schlanker als die im Issue beschriebenen Alternativen. Bambuddy soll ausschließlich semantische Druckerzustände WLED-Preset-IDs zuordnen. WLED bleibt für Ein-/Ausschaltzustand, Farben, Helligkeit, Effekte, Segmente, Übergänge und Playlists verantwortlich. MQTT, Webhooks, fortschrittsabhängige Farben, das Auslesen von WLED-Presets und die Bearbeitung der WLED-Konfiguration liegen außerhalb des Umfangs.

Die Konfiguration erfolgt pro physischem Drucker. Jede Zuordnung ist optional. WLED wird ausschließlich nach dem Best-Effort-Prinzip angesteuert und darf weder die Drucker-MQTT-Verarbeitung, Warteschlangenverarbeitung, Archivierung, Benachrichtigungen noch WebSocket-Aktualisierungen verzögern oder fehlschlagen lassen.

## Bestehende Implementierungsoberfläche

Eine explizite Repository-Suche auf `upstream/dev` ergab kein WLED-Modul, Konfigurationsfeld, UI, keinen Test, keine Dokumentation und keine sonstigen WLED-spezifischen Reste. Für diese Prüfung dürfen keine bloßen Teilzeichenfolgen-Suchen verwendet werden, da beispielsweise „acknowledged“ die Buchstabenfolge `wled` über eine Wortgrenze hinweg enthält.

### Druckerpersistenz und Migrationen

- `backend/app/models/printer.py` enthält das SQLAlchemy-Modell `Printer`. Druckerspezifische Integrationen wie externe Kameras und Druckplattenerkennung werden direkt in dieser Zeile gespeichert.
- `backend/app/core/database.py` erstellt aktuelle Tabellen mit `Base.metadata.create_all()` und aktualisiert bestehende SQLite-/PostgreSQL-Datenbanken über die einzelne idempotente Funktion `run_migrations()`. Das Projekt verwendet weder Alembic noch versionierte Migrationsdateien. Bestehende Druckererweiterungen verwenden `_safe_execute(..., "ALTER TABLE printers ADD COLUMN ...")`.
- `backend/app/models/__init__.py` importiert `Printer`, damit das Modell vor der Metadatenerstellung registriert ist.

### Drucker-API und Schemas

- `backend/app/schemas/printer.py` definiert `PrinterBase`, `PrinterCreate`, `PrinterUpdate`, `PrinterResponse` und die geheimnishaltige Variante `PrinterResponseWithSecret`.
- `backend/app/api/routes/printers.py` stellt Listen-, Erstellen-, Abrufen-, PATCH-, Löschen- und Live-Status-Routen bereit. `PATCH /printers/{printer_id}` überträgt `PrinterUpdate.model_dump(exclude_unset=True)` auf die ORM-Zeile. Änderungen der Verbindungseinstellungen lösen eine erneute MQTT-Verbindung aus; reine WLED-Änderungen dürfen dies nicht tun.
- Schreibzugriffe auf Drucker verwenden die vorhandene Berechtigung `printers:update`. Für die Konfiguration sind weder ein neuer Endpunkt noch eine neue Berechtigung erforderlich.
- `frontend/src/api/client.ts` bildet die Übertragungstypen nochmals als `Printer` und `PrinterCreate` ab; `api.updatePrinter()` sendet ein partielles `PrinterCreate` an die PATCH-Route.

### Druckereinstellungen im Frontend

- `frontend/src/pages/PrintersPage.tsx` enthält sowohl `AddPrinterModal` als auch das private `EditPrinterModal` im selben großen Seitenmodul.
- Das Bearbeitungsmodal ist der kleinste geeignete Ort für einen optionalen WLED-Bereich. Dadurch wird die Einrichtung eines unabhängigen LAN-Geräts nicht mit der bestehenden verpflichtenden Vorabprüfung der Druckerverbindung vermischt.
- Alle sichtbaren Texte müssen `react-i18next` verwenden. Die Locale-Dateien liegen unter `frontend/src/i18n/locales/` (14 Dateien); `en.ts` ist die Referenz und `npm run check:i18n` erzwingt strukturelle Übereinstimmung sowie echte Übersetzungen.
- Zu den relevanten Tests gehört `frontend/src/__tests__/components/EditPrinterPreflight.test.tsx`. Ein gezielter WLED-Test des Bearbeitungsformulars sollte separat bleiben.

### Aufnahme und Weitergabe des Status

Der tatsächliche Ablauf lautet:

1. `backend/app/services/bambu_mqtt.py` empfängt einen Bambu-MQTT-Bericht und verändert einen im Speicher gehaltenen `PrinterState`.
2. `_process_message()` verarbeitet `gcode_state`, Stages, AMS-/Tray-Felder, `hms[]` und `print_error`. Am Ende jedes verarbeiteten Statusberichts wird `on_state_change(self.state)` aufgerufen, nicht nur bei einer Änderung von `gcode_state`.
3. `backend/app/services/printer_manager.py::connect_printer()` ergänzt diesen Callback um die Drucker-ID und plant den Anwendungs-Callback auf dem asyncio-Loop ein.
4. `backend/app/main.py::on_printer_status_change()` verarbeitet Verbindungsübergänge, Wiederherstellung, Benachrichtigungen, MQTT-Relay und WebSocket-Ausgabe.
5. Der zugehörige Schlüssel `_last_status_broadcast` enthält Fortschritt, Temperaturen, Lüfter, Trays und weitere Telemetriedaten. Er reduziert den WebSocket-Verkehr, ist jedoch absichtlich keine Deduplizierung semantischer Druckerzustände und kann deshalb nicht für WLED wiederverwendet werden.
6. `backend/app/services/printer_manager.py::printer_state_to_dict()` erzeugt den REST-/WebSocket-Status. `frontend/src/pages/PrintersPage.tsx` leitet daraus getrennt Anzeigegruppen ab.

Callbacks für den Drucklebenszyklus unterscheiden sich von Status-Callbacks. `bambu_mqtt.py` löst `on_print_start` bei einem passenden Übergang zu `RUNNING` und `on_print_complete` bei `FINISH`, `FAILED` oder einem Abbruchübergang zu `IDLE` aus. WLED sollte nicht daran angebunden werden: Diese Callbacks decken weder Pausen, HMS-Änderungen, den anfänglichen Idle-Zustand noch Änderungen der Warteschlange beziehungsweise Druckplattenfreigabe ab.

`awaiting_plate_clear` gehört nicht zu Bambu-MQTT. Es ist ein in `Printer` persistierter Bambuddy-Zustand, wird in `PrinterManager._awaiting_plate_clear` zwischengespeichert und über `PrinterManager.set_awaiting_plate_clear()` geändert. Diese Methode gibt bereits über `_emit_plate_clear_change()` einen expliziten Zustandsübergang aus. Die WLED-Integration für `queue_waiting` muss denselben Übergang verwenden.

## Tatsächliche interne Statuswerte

Es gibt kein kanonisches Bambuddy-Enum, das alle gewünschten WLED-Zustände abdeckt:

| Gewünschter WLED-Zustand | Bestehende Quelle | Tatsächlicher Wert / tatsächliches Signal |
|---|---|---|
| `printing` | `PrinterState.state` aus MQTT-`gcode_state` | normalerweise `RUNNING`; einige Verbraucher berücksichtigen zusätzlich `PRINTING`; aktive Vorbereitungszustände umfassen `PREPARE` und `SLICING` |
| `paused` | `PrinterState.state` | `PAUSE` (nicht `PAUSED`) |
| `finished` | `PrinterState.state` | `FINISH` (nicht `FINISHED`) |
| `idle` | `PrinterState.state` | `IDLE`; der Initialwert lautet kleingeschrieben `unknown` |
| `error` | `PrinterState.state` | `FAILED`; dies kann auch einen benutzerseitigen Stopp oder Abbruch bedeuten und ist kein Beweis für einen Hardwarefehler |
| `hms_error` | `PrinterState.hms_errors` | ausgewertete aktive Fehler aus `hms[]` und `print_error`; normale Abbruch-/Statuscodes werden bereits herausgefiltert |
| `filament_problem` | kein einzelnes kanonisches Feld | stärkster Hinweis auf Filamentende ist `PAUSE` mit `stg_cur == 6` und/oder auflösbarem `tray_tar`-/`tray_pre`-Signal; einige Filamentfehler erscheinen außerdem in `hms_errors` |
| `queue_waiting` | Bambuddy-seitige Sicherheitssperre | `PrinterManager.is_awaiting_plate_clear(printer_id)` / persistiertes `Printer.awaiting_plate_clear` |

Die Frontend-Gruppierung ist kein Backend-Vertrag. Das WLED-Modul sollte seine eigene dokumentierte Zuordnung zentral im Backend vornehmen, statt UI-Hilfsfunktionen zu kopieren.

Empfohlene Priorität von hoch nach niedrig:

1. getrennt oder unbekannt: keine WLED-Anfrage; der letzte Gerätezustand bleibt bestehen
2. konkretes Filamentproblem-Signal während einer Pause: `filament_problem`
3. alle übrigen aktiven `hms_errors`: `hms_error`
4. `FAILED`: `error`
5. `PAUSE`: `paused`
6. `FINISH`: `finished`
7. `awaiting_plate_clear` bei ansonsten inaktivem Drucker: `queue_waiting`
8. `PREPARE`, `SLICING`, `RUNNING` oder der Kompatibilitätswert `PRINTING`: `printing`
9. `IDLE`: `idle`
10. jeder unbekannte Wert: keine Anfrage und ein Debug-Log, kein erfundener Fallback

Bleibt `FINISH` vor `awaiting_plate_clear`, wird das Finished-Preset nicht sofort ersetzt, wenn der Abschluss-Callback die Druckplattenfreigabe sperrt. Wechselt die Firmware später zu `IDLE`, während die Sperre aktiv bleibt, wird der semantische Zustand zu `queue_waiting`. Dadurch erhält auch der Finished-Timeout ein eindeutig definiertes Zeitfenster.

Die erste Umsetzung sollte die Filamenterkennung konservativ halten: `state == "PAUSE"` und (`stg_cur == 6` oder ein Runout-Tray-Signal, das kein Sentinelwert ist). Nicht jeder AMS-/HMS-Modulfehler darf als Filamentproblem eingeordnet werden. Unbekannte Fälle bleiben `hms_error` oder `paused`.

## Bestehende Muster für externe HTTP-Dienste

- `httpx>=0.26.0` ist bereits eine Laufzeitabhängigkeit in `requirements.txt`. Es wird keine neue Abhängigkeit benötigt.
- `backend/app/services/tasmota.py`, `rest_smart_plug.py`, `homeassistant.py`, `notification_service.py` und `spoolman.py` zeigen asynchrones `httpx`, begrenzte Timeouts, `raise_for_status()` und die Umwandlung von Ausnahmen in Logeinträge.
- `backend/app/api/routes/_url_safety.py::assert_safe_lan_service_url()` ist die bestehende Richtlinie für benutzerkonfigurierte HTTP-Dienste im privaten LAN. Sie erlaubt legitime LAN-Adressen, weist aber unsichere Protokolle und Ziele zurück. Die WLED-Konfiguration sollte sie wiederverwenden.
- Da WLED für mehrere Drucker genutzt werden kann und ausdrücklich als Modul vorgesehen ist, eignet sich ein einzelner verzögert erstellter beziehungsweise injizierbarer `httpx.AsyncClient` mit kurzem Timeout und `aclose()`-Hook. Dies bleibt schlank und ermöglicht Unit-Tests mit `httpx.MockTransport`.

## Vorgeschlagene minimale Architektur

Ein fokussiertes Modul `backend/app/services/wled.py` sollte enthalten:

- die endliche Menge semantischer Statusnamen und die reine Funktion `derive_wled_state(...)`;
- einen `WLEDManager`-Singleton mit ausschließlich flüchtigem Zustand pro Drucker: letzter semantischer Zustand, zuletzt angefordertes Preset, Generationsnummer, laufender Versand-Task und Finished-Timeout-Task;
- einen asynchronen HTTP-Sender für genau ein Preset;
- Einstiegspunkte für Druckerstatus, Druckplattenfreigabe-Übergänge, Konfigurationsänderungen, Bereinigung bei Druckerlöschung/-trennung und Herunterfahren.

Es sollen weder Interface/Factory, Plugin-Registry, generische Automation-Engine, Event-Bus, Repository-Schicht noch Farb-/Effektmodell eingeführt werden. Die vorhandenen Callbacks reichen aus.

Integrationspunkte:

- `backend/app/main.py::on_printer_status_change()` reicht einen Snapshot zusammen mit `is_awaiting_plate_clear()` an den `WLEDManager` weiter. Der Manager leitet den Zustand ab, dedupliziert und plant die Netzwerkkommunikation als Hintergrund-Task; der Status-Callback wartet niemals auf WLED-Netzwerklatenz.
- `backend/app/services/printer_manager.py::_emit_plate_clear_change()` reicht nach der bestehenden MQTT-/Benachrichtigungslogik den aktuellen Druckerzustand weiter. Ein lokaler Import verhindert einen Modulzyklus. Dies ist für den nicht aus MQTT stammenden `queue_waiting`-Übergang erforderlich.
- `backend/app/api/routes/printers.py::update_printer()` benachrichtigt den Manager nach einer erfolgreichen WLED-Konfigurationsänderung. Die Deduplizierung wird zurückgesetzt und der aktuelle Status neu ausgewertet.
- Beim Löschen oder Trennen eines Druckers werden ausstehende WLED-Tasks abgebrochen. Beim Herunterfahren werden Timer und laufende Tasks abgebrochen und der HTTP-Client geschlossen. Die Trennung selbst sendet kein Preset.

Das Modul erhält normalisierte einfache Konfigurationsdaten und behält keine ORM-Instanzen über eine Datenbanksitzung hinaus. Es öffnet aus dem hochfrequenten Callback keine Datenbanksitzung. API- und Startpfad laden die Konfiguration in einen kleinen druckerspezifischen Cache; Änderungen aktualisieren genau einen Eintrag.

## Änderungen an Datenmodell und Schema

Empfohlen wird eine nullable SQLAlchemy-`JSON`-Spalte in `Printer`:

```json
{
  "enabled": true,
  "base_url": "http://wled-printer-1.local",
  "presets": {
    "idle": 1,
    "printing": 3,
    "paused": 7,
    "finished": 9,
    "error": null,
    "queue_waiting": null,
    "filament_problem": null,
    "hms_error": null
  },
  "finished_timeout_seconds": 120
}
```

Vorgeschlagener Feldname: `wled_config`. `NULL` bedeutet „nicht konfiguriert“. Eine JSON-Spalte hält dieses optionale Modul aus der bereits breiten Druckertabelle heraus, benötigt eine additive Migration und ermöglicht weitere Zuordnungen ohne eine neue Spalte je Status. Bei Aktualisierungen wird das gesamte verschachtelte Objekt ersetzt, wodurch Probleme bei der Änderungserkennung von JSON-Inhalten vermieden werden.

In `backend/app/schemas/printer.py` werden die Modelle `WLEDConfig` und `WLEDPresets` ergänzt und `wled_config: WLEDConfig | None` in `PrinterBase`/Antworten und `PrinterUpdate` bereitgestellt. Die Validierung sollte erzwingen:

- `enabled` ist standardmäßig `false`;
- `base_url` ist bei aktivierter Integration erforderlich, hat eine angemessene Längenbegrenzung, wird ohne abschließenden Schrägstrich normalisiert und besteht `assert_safe_lan_service_url()`;
- jedes Preset ist `null` oder eine Ganzzahl von 1 bis 250 entsprechend dem offiziellen WLED-Presetbereich;
- `finished_timeout_seconds` ist `null`/0 oder eine begrenzte positive Ganzzahl, vorgeschlagen 1 bis 86400;
- der Timeout wird nur ausgeführt, wenn `finished` und `idle` gesetzt sind. Das Backend darf unvollständige Konfigurationen speichern, plant dann aber keinen Timeout.

In Phase eins werden keine Zugangsdaten ergänzt. Die lokale WLED-JSON-API benötigt normalerweise keine, und URL-Zugangsdaten könnten über Validierungsfehler oder Logs offengelegt werden.

## API-Verhalten

- GET-/Listen-Antworten enthalten `wled_config`.
- PATCH akzeptiert die vollständige verschachtelte `wled_config` oder `null`, um sie zu entfernen.
- Die Erstellung kann das geerbte Feld technisch akzeptieren, die erste UI bietet WLED jedoch erst nach dem Anlegen des Druckers an.
- Validierungsfehler bleiben normale Pydantic-422-Antworten.
- Ein öffentlicher Endpunkt zum Setzen eines Presets ist unnötig. Ein späterer Verbindungstest-Endpunkt ist optional; das Speichern darf nicht von der Erreichbarkeit von WLED abhängen.

`printers:update` bleibt die richtige Autorisierungsgrenze. WLED-Angaben sind keine Zugangsdaten und dürfen über `printers:read` zurückgegeben werden.

## WLED-HTTP-Anfrage

Verwendet wird die offizielle WLED-JSON-API:

```http
POST {base_url}/json/state
Content-Type: application/json

{"ps": 3, "v": true}
```

WLED dokumentiert partielle Zustände per POST an `/json/state`; `ps` wählt ein Preset. `v: true` fordert die vollständige Statusantwort für eine grundlegende Validierung an. Referenzen: [WLED JSON API](https://kno.wled.ge/interfaces/json-api/) und [WLED-Presets](https://kno.wled.ge/features/presets/).

Der Sender sollte:

- einen kurzen expliziten Timeout verwenden, beispielsweise 2 s für den Verbindungsaufbau und 3 s für Lesen, Schreiben und Pool;
- Weiterleitungen deaktivieren;
- `raise_for_status()` aufrufen;
- JSON auswerten und ein Objekt verlangen; ein abweichendes numerisches `ps` wird als Warnung protokolliert;
- Drucker-ID/-Name, semantischen Status, Zielhost und Preset-ID protokollieren, aber niemals den vollständigen Antwortinhalt oder Zugangsdaten;
- in Phase eins keine automatischen Wiederholungen durchführen, da diese Zustände umsortieren oder einen ausgefallenen Dienst zusätzlich belasten könnten.

Die Anfrage enthält keine Felder für Farbe, Effekt, Segment, Playlist, Helligkeit oder Übergang.

## Deduplizierung und Nebenläufigkeit

Dedupliziert wird anhand des semantischen Zustands und des aufgelösten Presets, nicht anhand des rohen `PrinterState` oder des telemetriereichen `status_key` aus `main.py`.

Für jeden Drucker gilt:

1. genau einen semantischen WLED-Zustand ableiten;
2. den Finished-Timer abbrechen, sobald `finished` verlassen wird;
3. das optionale Preset aus der Konfiguration auflösen;
4. bei unverändertem Zustand und Preset nichts tun;
5. den neuen Zustand vor der Planung der Netzwerkkommunikation speichern, damit gleichzeitige Telemetrie keine Duplikate erzeugt;
6. die Generation erhöhen und die HTTP-Anfrage im Hintergrund planen;
7. unmittelbar vor dem Senden die aktuelle Generation prüfen. Vorgänge werden pro Drucker serialisiert oder ein älterer wartender Task wird ersetzt, damit schnelle Wechsel wie `RUNNING -> PAUSE -> RUNNING` nicht mit einer veralteten Anfrage enden.

Das zuletzt angeforderte Preset wird auch bei einem WLED-Fehler gespeichert. Sonst würde jedes Telemetriepaket den unerreichbaren Dienst erneut anfragen. Pro versuchtem Zustandsübergang wird einmal gewarnt; ein späterer echter Wechsel versucht es erneut.

Konfigurationsänderungen setzen semantische und Preset-Deduplizierung zurück. Wird ein neuer semantischer Zustand demselben Preset zugeordnet, sollte keine redundante Anfrage erfolgen, sofern dieses Preset bereits angefordert wurde. Zustand und Timeout-Verwaltung werden dennoch aktualisiert.

## Finished-Timeout

Der Timeout gehört ins Backend und gilt ausschließlich für `finished`:

1. beim Eintritt das Finished-Preset senden;
2. bei Timeout > 0 und vorhandenem Idle-Preset genau einen Task erstellen;
3. nach Ablauf prüfen, ob Generation und Zustand weiterhin `finished` sind;
4. das Idle-Preset senden und als zuletzt angefordert speichern;
5. den tatsächlichen Bambuddy-/MQTT-Zustand nicht verändern.

Ein Wechsel weg von `finished`, eine Konfigurationsänderung, Druckerlöschung oder das Herunterfahren bricht den Task ab. Wiederholte `FINISH`-Telemetrie startet den Timer nicht neu. Schlägt die Finished-Anfrage fehl, darf der Timeout dennoch Idle anfordern; beide Vorgänge sind unabhängige Best-Effort-Befehle.

Wechselt die Firmware zu `IDLE`, während `awaiting_plate_clear` aktiv ist, wird der Finished-Timer abgebrochen und gegebenenfalls `queue_waiting` angewendet. Bleibt sie auf `FINISH`, erzeugt der Timer nach der konfigurierten Dauer den Idle-Fallback.

Timer verwenden `asyncio.create_task` beziehungsweise die bestehende Hintergrund-Task-Konvention. Sie müssen nicht persistiert werden: Nach einem Neustart wird der aktuelle Zustand neu ausgewertet; dauerhafter Planungszustand wäre für eine Komfortfunktion nicht gerechtfertigt.

## Fehlerbehandlung und Isolation

Im WLED-Hintergrund-Task werden mindestens `httpx.TimeoutException`, `httpx.HTTPStatusError`, `httpx.RequestError`, JSON-/Wertefehler und unerwartete Ausnahmen abgefangen. Nichts davon darf bis `on_printer_status_change`, `_emit_plate_clear_change` oder zur API-Persistenz gelangen.

Log-Level:

- Info/Debug bei erfolgreicher Anwendung;
- Warning bei Timeout, Verbindungsfehler, Nicht-2xx-Antwort, fehlerhaftem JSON, Preset-Abweichung oder ungültiger persistierter Konfiguration;
- Exception/Error nur bei unerwarteten Programmier- oder Laufzeitfehlern, jeweils mit Druckerkontext.

WLED-Fehler dürfen weder den Drucker als offline markieren noch MQTT neu verbinden, den Warteschlangenzustand verändern, ein PATCH nach dem Datenbank-Commit fehlschlagen lassen oder Druckervorgänge wiederholen. Eine ungültige direkt gespeicherte Konfiguration wird übersprungen und protokolliert, statt den Start zu verhindern.

## Migration

In `backend/app/models/printer.py` wird `wled_config = mapped_column(JSON, nullable=True)` und in `backend/app/core/database.py` eine idempotente Anweisung für bestehende Datenbanken ergänzt. Beide unterstützten Dialekte werden geprüft. Bestehende Zeilen erhalten `NULL`; das Feature bleibt dadurch deaktiviert.

Es sind weder Backfill, Tabellenneuschreibung, destruktive Migration noch Downgrade-Pfad nötig. Ein gezielter Test erstellt eine alte `printers`-Tabelle, führt die Migration aus, prüft Spalte und Standardwert `NULL` und führt sie erneut aus, um die Idempotenz zu bestätigen.

## Frontend-Änderungen

In `frontend/src/api/client.ts` werden `WLEDConfig`, `WLEDPresets` und `Printer.wled_config`/`PrinterCreate.wled_config` ergänzt.

Das `EditPrinterModal` erhält einen getrennten WLED-Bereich mit:

- Checkbox zum Aktivieren der Integration;
- Basis-URL;
- je einem numerischen Preset-Feld für `idle`, `printing`, `paused`, `finished`, `error`, `queue_waiting`, `filament_problem` und `hms_error`;
- Aktivierungs-Checkbox pro Zuordnung oder einfacher „leer = deaktiviert“-Semantik mit eindeutigem Hilfetext;
- optionalem Finished-Timeout in Sekunden, nur aktiv bei vorhandener Finished- und Idle-Zuordnung;
- Hinweis, dass Effekte und Farben in WLED konfiguriert werden.

Native URL-/Zahleneingaben (`type="url"`, `type="number"`, `min`, `max`) werden mit Backend-Validierung kombiniert. Presets werden nicht abgerufen und die WLED-Oberfläche nicht eingebettet. Gespeichert wird mit dem bestehenden Drucker-PATCH; WLED-Erreichbarkeit gehört weder zur MQTT-Vorabprüfung noch darf sie das Speichern verhindern.

In allen Locale-Dateien werden Übersetzungsschlüssel ergänzt. Die sichtbare Änderung erfordert Vorher-/Nachher-Screenshots für den späteren Pull Request.

## Tests

Backend-Unit-Tests (`backend/tests/unit/services/test_wled.py`):

- Statuszuordnung und Priorität, einschließlich Filamentende bei `PAUSE` gegenüber allgemeiner Pause/HMS;
- unbekannte/getrennte Zustände und deaktivierte Zuordnungen erzeugen keinen Befehl;
- wiederholte Telemetrie desselben Zustands sendet nur einmal;
- ein Zustandswechsel sendet das neue Preset;
- schnelle Wechsel können keine ältere Anfrage als letzten Befehl hinterlassen;
- Timeout, Verbindungs-/HTTP-Fehler, fehlerhaftes JSON und abweichende Antworten bleiben isoliert;
- der Finished-Timeout sendet Idle einmalig, startet bei Telemetrie nicht neu und wird bei Status-/Konfigurationsänderung abgebrochen;
- erneute Konfigurationsanwendung setzt die Deduplizierung zurück;
- URL-Validierung blockiert dieselben Fälle wie `test_outbound_url_ssrf_guards.py`.

Backend-Integrations-/Schema-Tests:

- `backend/tests/unit/test_printer_schema.py` für URL, Presetbereich, optionale Zuordnungen und Timeout-Grenzen erweitern;
- `backend/tests/integration/test_printers_api.py` um Roundtrips für Erstellen/Abrufen/PATCH/Null-Konfiguration erweitern und bestätigen, dass ein reines WLED-PATCH keine Neuverbindung auslöst;
- den beschriebenen Migrations-/Idempotenztest ergänzen.

Frontend-Vitest-Tests:

- vorhandene Werte werden ins Bearbeitungsformular übernommen;
- eine leere Zuordnung wird als `null` serialisiert und jede Zuordnung kann deaktiviert werden;
- aktivierte Konfiguration speichert numerische IDs im verschachtelten Objekt;
- der Timeout ist nur mit Finished- und Idle-Zuordnung nutzbar;
- Speichern löst keine WLED-Anfrage im Browser aus;
- i18n-Parität besteht.

Die Prüfung umfasst zuerst gezielte Pytest-/Vitest-Tests, danach `ruff check`, `ruff format --check`, Frontend-ESLint/Typprüfung, `npm run test:run` und den produktiven Frontend-Build. Die vollständigen Skripte sind `test_backend.sh`, `test_frontend.sh` und `test_all.sh`.

## Risiken und Grenzfälle

- Bambu besitzt kein einheitliches Status-Enum. Neue Firmwarewerte werden sicher übersprungen, bis sie ausdrücklich zugeordnet sind.
- `FAILED` umfasst Abbruch und Fehler. Die Zuordnung zu `error` kann nach einem benutzerseitigen Stopp ein Fehler-Preset anzeigen. Ein separater Abbruchzustand würde Lebenszykluskontext benötigen.
- Filamentprobleme überschneiden sich mit HMS-Fehlern. Die konservative Priorisierung kann einige modellspezifische Runout-Codes zunächst unter `hms_error` belassen.
- Druckplattenfreigabe-Sperre und `FINISH` können gleichzeitig bestehen. Die Priorität erhält zunächst das Finished-Preset und erlaubt `queue_waiting`, sobald der Drucker Idle wird.
- Mehrere Drucker können denselben WLED-Controller verwenden. Deduplizierung pro Drucker koordiniert konkurrierende Befehle an dieselbe URL nicht; der letzte gewinnt. Druckerübergreifende Koordination bleibt außerhalb des Umfangs.
- Zwei Bambuddy-Prozesse können ebenfalls konkurrieren; ein verteilter Lock ist für diese optionale Integration nicht gerechtfertigt.
- DNS-Rebinding bleibt eine Einschränkung der gemeinsamen LAN-URL-Richtlinie. Sie erlaubt bewusst symbolische LAN-Hosts und verzichtet auf DNS-/TOCTOU-Prüfungen.
- Weiterleitungen müssen deaktiviert bleiben, damit eine sichere URL nicht auf ein unzulässiges Ziel umleitet.
- In WLED gelöschte oder geänderte Presets werden als Auffälligkeit protokolliert, aber Bambuddy versucht nicht, WLED zu reparieren.
- Neustarts verlieren Deduplizierungs- und Timeout-Zustand. Eine einmalige erneute Anwendung ist erwünscht; Finished-Timer beginnen bewusst neu.
- Direkte SQL-Änderungen können Pydantic umgehen. Das Laden des Caches benötigt daher defensive Struktur-/Werteprüfung und Fail-open-Verhalten.

## Kleine Implementierungsphasen

1. **Persistenz und Vertrag:** Verschachtelte Schemas, JSON-Modellspalte, additive Migration, API-Roundtrip und gezielte Tests ergänzen. Noch keine WLED-Aufrufe.
2. **Backend-Modul:** Statusableitung, Preset-HTTP-Sender, Deduplizierung/Nebenläufigkeit, URL-Sicherheit und Unit-Tests mit `httpx.MockTransport` ergänzen.
3. **Laufzeit-Anbindung:** MQTT-Status, Druckplattenfreigabe, Konfigurationsaktualisierung, Löschung, Start und Herunterfahren anbinden; Isolation und Finished-Timeout testen.
4. **Frontend-Einstellungen:** Typisierte Bearbeitungsfelder und alle Übersetzungen mit gezielter Vitest-Abdeckung ergänzen.
5. **Prüfung und Contribution-Artefakte:** Prüfungen ausführen, UI-Screenshots aufnehmen, das Bambuddy-Wiki in einem begleitenden PR aktualisieren und das PR-Template für das Ziel `dev` ausfüllen.

Jede Phase ist unabhängig prüfbar. Preset-Erkennung, manuelle Testendpunkte, Wiederholungen/Backoff, gemeinsame Controller-Koordination, Fortschrittseffekte, MQTT, Webhooks oder ein generisches Integrationsframework werden erst ergänzt, wenn konkretes Feedback den Bedarf belegt.

## Anforderungen für Contributions und Pull Requests

`CONTRIBUTING.md` ist für diese Arbeit maßgeblich. Sie verlangt vor Implementierung beziehungsweise Pull Request ein bestehendes Issue, Zustimmung der Maintainer und eine Zuweisung; Issue #1528 zeigt derzeit keine zugewiesene Person. Feature-Branches basieren auf `dev`, und der Pull Request muss `dev` als Ziel haben. Die aktuelle `.github/workflows/ci.yml` ist für Pull Requests auf `main` konfiguriert. Diese Inkonsistenz sollte im Pull Request erwähnt werden, ändert aber nicht das vorgeschriebene Ziel `dev`.

Für das spätere Feature werden benötigt:

- ein begleitender Dokumentations-PR für `bambuddy-wiki`;
- gegebenenfalls ein Website-PR, nur wenn öffentliche Feature-Aussagen oder -Listen geändert werden;
- Verknüpfungen der Dokumentations-PRs in `.github/PULL_REQUEST_TEMPLATE.md`;
- Tests, Lint-/Typ-/Build-Prüfungen, ein fokussierter PR, das verknüpfte Issue und Vorher-/Nachher-Screenshots;
- CODEOWNER-Review durch `@maziggy`.

Diese Analyse umfasst weder Commit noch Push.
