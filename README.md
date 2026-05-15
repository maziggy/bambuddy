# Bambuddy Telegram Command Bot

Diese Erweiterung ergaenzt Bambuddy um einen kleinen Telegram-Chatbot fuer
lesende Statusabfragen.

Sie ist kein eigenstaendiges System, sondern ein Patch fuer eine bestehende
Bambuddy-Installation.

## Funktionen

- Druckerstatus per Telegram abfragen
- Kompaktes Dashboard aller Drucker abrufen
- Restzeiten laufender Drucke anzeigen
- HMS-Warnungen und Fehler gesammelt anzeigen
- AMS/Tray-Status inklusive Feuchte, Temperatur und Restwerten abrufen
- Niedrige Filamentbestaende aus dem Bambuddy-Inventar anzeigen
- Letzte Drucke aus der Druckhistorie abrufen
- Wartungsfaelligkeiten je Drucker anzeigen
- Kamera-Snapshot eines Druckers per Telegram anfordern
- Warteschlange per Telegram anzeigen
- Aktive Drucker auflisten
- Bestehende Telegram-Benachrichtigungsanbieter in Bambuddy weiterverwenden
- Zugriff auf die konfigurierte Telegram `chat_id` begrenzen
- Keine Schreibbefehle: kein Start, Pause, Resume, Abbruch oder Lichtsteuerung

## Befehle

```text
/help
/printers
/dashboard
/status
/status <drucker>
/eta
/eta <drucker>
/errors
/errors <drucker>
/ams <drucker>
/filament [gramm]
/history [anzahl]
/maintenance
/maintenance <drucker>
/photo <drucker>
/foto <drucker>
/queue
```

Beispiele:

```text
/status rocketman
/photo a1 mini
/filament 150
/history 10
/queue
```

## Voraussetzungen

- Laufende Bambuddy-Installation
- Telegram-Bot-Token von `@BotFather`
- Telegram `chat_id`
- Bambuddy muss `https://api.telegram.org` erreichen koennen
- Fuer Fotos muss die Kamera in Bambuddy funktionieren

## Aktivierung in Bambuddy

1. Bambuddy oeffnen.
2. `Settings` -> `Notifications` oeffnen.
3. Telegram Provider erstellen oder bearbeiten.
4. `Bot Token` und `Chat ID` eintragen.
5. `Telegram chat commands` aktivieren.
6. Speichern.
7. In Telegram `/help` an den Bot senden.

Weitere Details stehen in der vollstaendigen Anleitung:

[docs/telegram-command-bot.md](docs/telegram-command-bot.md)

## Installation auf einem Raspberry Pi

Diese Erweiterung muss in eine Bambuddy-Installation eingebaut werden, die aus
Quellcode gebaut wird. Ein Raspberry Pi, auf dem nur `docker compose up -d` mit
dem fertigen `ghcr.io/maziggy/bambuddy:latest` Image laeuft, kann nicht dauerhaft
per einzelner Datei gepatcht werden.

Empfohlener Weg: Dieses Repository als vollstaendigen Bambuddy-Fork auf GitHub
veroeffentlichen und auf dem Raspberry Pi daraus lokal bauen.

### Direkt auf dem Raspberry Pi installieren

Per SSH auf den Raspberry Pi verbinden:

```bash
ssh <ssh-user>@<raspberry-pi-ip>
```

Falls Bambuddy bereits in `~/bambuddy` laeuft, in den Ordner wechseln:

```bash
cd ~/bambuddy
```

Dann den Quellcode aus deinem GitHub-Repo holen und neu bauen:

```bash
curl -L https://github.com/<github-user>/<repo>/archive/refs/heads/main.zip -o /tmp/bambuddy-telegram.zip
python3 - <<'PY'
import shutil
import zipfile
from pathlib import Path

archive = Path("/tmp/bambuddy-telegram.zip")
target = Path("/tmp/bambuddy-telegram-src")
if target.exists():
    shutil.rmtree(target)
target.mkdir(parents=True)
with zipfile.ZipFile(archive) as zf:
    zf.extractall(target)
source = next(path for path in target.iterdir() if path.is_dir())
destination = Path.home() / "bambuddy"
preserve = {"data", "logs", "virtual_printer", ".env", "docker-compose.override.yml"}
destination.mkdir(exist_ok=True)
for item in source.iterdir():
    if item.name in preserve:
        continue
    dest = destination / item.name
    if dest.exists():
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()
    if item.is_dir():
        shutil.copytree(item, dest)
    else:
        shutil.copy2(item, dest)
(destination / ".git").mkdir(exist_ok=True)
(destination / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
PY
cd ~/bambuddy
docker compose up -d --build
```

Platzhalter ersetzen:

- `<github-user>`: GitHub Benutzer oder Organisation
- `<repo>`: Name deines Bambuddy-Forks
- `<ssh-user>`: SSH-Benutzer auf dem Raspberry Pi
- `<raspberry-pi-ip>`: IP-Adresse des Raspberry Pi

### Optional: von einem Windows-PC deployen

Nur fuer Entwickler, die den vollstaendigen Bambuddy-Quellcode lokal auf dem PC
haben:

```powershell
cd C:\path\to\bambuddy
.\deploy\deploy-custom-to-pi.ps1 -HostName <raspberry-pi-ip> -User <ssh-user>
```

Beide Wege bauen das Bambuddy-Docker-Image neu und starten den bestehenden
Container neu. Vorhandene Bambuddy-Daten bleiben bei der normalen Docker-Compose
Installation in den Docker-Volumes erhalten.

## Sicherheit

- Der Bot akzeptiert nur Nachrichten aus der konfigurierten `chat_id`.
- Der Bot ist absichtlich read-only.
- Bot-Token geheim halten.
- In Telegram-Gruppen koennen alle Mitglieder der Gruppe die lesenden Befehle
  nutzen, wenn die Gruppen-`chat_id` eingetragen ist.

## Performance

- Der Bot nutzt Telegram Long Polling und wartet blockierend auf neue Nachrichten.
- Wenn kein Telegram Provider aktiv ist, schlaeft der Bot 30 Sekunden zwischen Pruefungen.
- Datenbankabfragen sind begrenzt, z. B. Queue- und History-Ausgaben mit Limits.
- Kamera-Snapshots werden nur auf ausdruecklichen `/photo`-Befehl geladen.
- Es werden keine Hintergrund-Scans von Kameras, Druckdateien oder Inventar gestartet.

## GitHub-Veroeffentlichung

Empfohlene Dateien fuer einen sauberen Commit:

```powershell
git add backend/app/services/telegram_bot.py
git add backend/app/main.py
git add backend/app/schemas/notification.py
git add frontend/src/components/AddNotificationModal.tsx
git add frontend/src/api/client.ts
git add frontend/src/i18n/locales/en.ts
git add frontend/src/i18n/locales/de.ts
git add docs/telegram-command-bot.md
git add README.md
```

Optional, wenn das Raspberry-Pi-Deployment-Skript mit veroeffentlicht werden
soll:

```powershell
git add deploy/deploy-custom-to-pi.ps1
git add deploy/install-from-github-on-pi.sh
```

Branch erstellen, committen und pushen:

```powershell
git checkout -b codex/telegram-command-bot
git commit -m "Add Telegram command bot"
git push -u origin codex/telegram-command-bot
```

Danach kann auf GitHub ein Pull Request erstellt werden.
