# WLED-Preset-Integration

Die optionale WLED-Integration ordnet den Zuständen eines Druckers vorhandene WLED-Presets zu. Farben, Effekte, Paletten, Segmentaufteilung und Playlists werden ausschließlich in WLED erstellt und gepflegt. Bambuddy speichert nur die numerische Preset-ID.

## Voraussetzungen

- Ein im lokalen Netzwerk erreichbares WLED-Gerät mit aktivierter JSON-API
- Mindestens ein zuvor direkt in WLED angelegtes Preset
- Die Basis-URL des Geräts, zum Beispiel `http://wled.local` oder `http://192.168.1.50`

Für die direkte Integration sind keine Bambuddy-API-Zugangsdaten und keine separate Bridge erforderlich.

## Einrichtung

1. Unter **Einstellungen → WLED** den gewünschten Drucker auswählen.
2. Die WLED-URL eintragen und die Verbindung testen.
3. Für gemeinsam genutzte WLED-Geräte jedes Preset direkt in WLED nur mit dem vorgesehenen Zielsegment und aktivierter Option **Checked segments only** speichern. Andere Segmente dürfen nicht Teil des Presets sein – auch nicht mit `"on": false`, da WLED sie sonst beim Laden ausschaltet.
4. Jedem gewünschten Druckerstatus ein vorhandenes Preset zuordnen.
5. Nicht benötigte Zuordnungen auf **Deaktiviert – kein Preset** lassen.
6. Die Integration aktivieren und speichern.

Die Preset-Liste wird nur auf Anforderung geladen. Ist WLED nicht erreichbar oder wurde ein Preset dort später gelöscht, bleibt eine bereits gespeicherte ID erhalten und wird in der Oberfläche als derzeit nicht verfügbar angezeigt.

## Statuszuordnung

| Bambuddy-Zielstatus | Interner Druckerzustand bzw. Bedingung |
|---|---|
| Bereit | `IDLE` |
| Vorbereitung | `PREPARE`, `SLICING` |
| Druckt | `RUNNING`, `PRINTING` |
| Pausiert | `PAUSE` |
| Fertig | `FINISH` |
| Fehlgeschlagen / Fehler | `FAILED` |
| Wartet auf freie Druckplatte | `IDLE` mit `awaiting_plate_clear` |
| Filamentproblem | `PAUSE` während eines AMS-/Filamentwechsels |
| HMS-Fehler | vorhandene HMS-Fehlermeldung |
| Offline | keine aktive Druckerverbindung |

Spezifische Störungen haben Vorrang vor dem allgemeinen Druckerzustand: Offline, Filamentproblem und HMS-Fehler werden daher nicht als normales Pausiert oder Bereit behandelt.

## Finished-Timeout

Ein optionaler Timeout kann nach dem Finished-Preset automatisch das konfigurierte Idle-Preset aktivieren. Der Wert wird in Sekunden angegeben; `0` deaktiviert die Funktion. Der Wechsel erfolgt nur, wenn ein Idle-Preset gesetzt ist und der Drucker seit dem Finished-Ereignis keinen anderen Status angenommen hat. Bei einem Statuswechsel wird der Timer abgebrochen.

## Netzwerk- und Fehlerverhalten

Bambuddy aktiviert ein Preset asynchron über WLEDs `POST /json/state` und sendet ausschließlich `{"ps": <ID>}`. Die Segmentisolierung ergibt sich aus dem Inhalt des in WLED angelegten Presets; Bambuddy übermittelt zur Laufzeit keine Segment-ID. Dieses Verhalten wurde mit WLED 16.0.0 verifiziert. Der alternative Aufruf `{"seg": [{"id": <SEGMENT-ID>, "ps": <ID>}]}` wurde dort zwar akzeptiert, aber ignoriert.

Die Preset-Namen werden lesend über `/presets.json` abgefragt. Kurze Netzwerk-Timeouts verhindern, dass ein nicht erreichbares WLED-Gerät Ressourcen bindet.

WLED ist eine reine Komfortintegration: Verbindungsfehler, HTTP-Fehler und ungültige Antworten werden protokolliert, beeinflussen aber weder die Druckerkommunikation noch Druckaufträge oder andere Bambuddy-Funktionen. Derselbe effektive Zielstatus wird pro Drucker nur einmal gesendet; Temperatur- und Telemetrie-Updates erzeugen keine wiederholten Preset-Aufrufe.

## Dokumentations-PR

Nach `CONTRIBUTING.md` benötigt diese Funktion zusätzlich einen begleitenden PR im Repository `bambuddy-wiki`. Dieses Dokument ist als Inhalt dafür vorbereitet; im Rahmen dieser lokalen Umsetzung wurde außerhalb dieses Repositories nichts veröffentlicht.
