<p align="center">
  <img src="static/img/bambuddy_logo_dark.png" alt="Bambuddy Logo" width="300">
</p>

<h1 align="center">Bambuddy</h1>

<p align="center">
  <strong>Self-hosted print archive and management system for Bambu Lab 3D printers</strong>
</p>

<p align="center">
  <a href="https://github.com/maziggy/bambuddy/releases"><img src="https://img.shields.io/github/v/release/maziggy/bambuddy?style=flat-square&color=blue" alt="Release"></a>
  <a href="https://github.com/maziggy/bambuddy/blob/main/LICENSE"><img src="https://img.shields.io/github/license/maziggy/bambuddy?style=flat-square" alt="License"></a>
  <a href="https://github.com/maziggy/bambuddy/stargazers"><img src="https://img.shields.io/github/stars/maziggy/bambuddy?style=flat-square" alt="Stars"></a>
  <a href="https://github.com/maziggy/bambuddy/issues"><img src="https://img.shields.io/github/issues/maziggy/bambuddy?style=flat-square" alt="Issues"></a>
  <img src="[https://api.visitorbadge.io/api/visitors?path=https%3A%2F%2Fgithub.com%2Fmaziggy%2Fbambuddy&labelColor=%23555555&countColor=%2379C83D&label=visitors&style=flat-square](https://camo.githubusercontent.com/54a267c3fa4c2d5e5eaa0df1cde45a31b0605e8a0e8a8c69aea49c7cbff4c3c6/68747470733a2f2f6170692e76697369746f7262616467652e696f2f6170692f76697369746f72733f706174683d68747470732533412532462532466769746875622e636f6d2532466d617a6967677925324662616d6275646479266c6162656c436f6c6f723d25323335353535353526636f756e74436f6c6f723d253233373943383344266c6162656c3d76697369746f7273267374796c653d666c61742d737175617265)" alt="Visitors">
</p>

<p align="center">
  <a href="#-features">Features</a> •
  <a href="#-screenshots">Screenshots</a> •
  <a href="#-quick-start">Quick Start</a> •
  <a href="http://wiki.bambuddy.cool">Documentation</a> •
  <a href="#-contributing">Contributing</a>
</p>

---

> **Testers Needed!** I only have X1C and H2D devices. Help make Bambuddy work with all Bambu Lab printers by [reporting your experience](https://github.com/maziggy/bambuddy/issues)!

## Why Bambuddy?

- **Own your data** — All print history stored locally, no cloud dependency
- **Works offline** — Uses Developer Mode for direct printer control via local network
- **Full automation** — Schedule prints, auto power-off, get notified when done
- **Multi-printer support** — Manage your entire print farm from one interface

---

## ✨ Features

<table>
<tr>
<td width="50%" valign="top">

### 📦 Print Archive
- Automatic 3MF archiving with metadata
- 3D model preview (Three.js)
- Duplicate detection & full-text search
- Photo attachments & failure analysis
- Timelapse editor (trim, speed, music)
- Re-print to any connected printer with AMS mapping (auto-match or manual slot selection)
- Archive comparison (side-by-side diff)

### 📊 Monitoring & Control
- Real-time printer status via WebSocket
- Live camera streaming (MJPEG) & snapshots
- Fan status monitoring (part cooling, auxiliary, chamber)
- Printer control (stop, pause, resume, chamber light)
- Resizable printer cards (S/M/L/XL)
- Skip objects during print
- AMS slot RFID re-read
- HMS error monitoring with history
- Print success rates & trends
- Filament usage tracking
- Cost analytics & failure analysis
- CSV/Excel export

### ⏰ Scheduling & Automation
- Print queue with drag-and-drop
- Scheduled prints (date/time)
- Queue Only mode (stage without auto-start)
- Smart plug integration (Tasmota)
- Energy consumption tracking
- Auto power-on before print
- Auto power-off after cooldown

### 📁 Projects
- Group related prints (e.g., "Voron Build")
- Track progress with target counts
- Quantity tracking for batch prints
- Color-coded project badges
- Bulk assign archives via multi-select toolbar

</td>
<td width="50%" valign="top">

### 🔔 Notifications
- WhatsApp, Telegram, Discord
- Email, Pushover, ntfy
- Custom webhooks
- Quiet hours & daily digest
- Customizable message templates

### 🔧 Integrations
- [Spoolman](https://github.com/Donkie/Spoolman) filament sync
- MQTT publishing for Home Assistant, Node-RED, etc.
- Bambu Cloud profile management
- K-profiles (pressure advance)
- External sidebar links
- Webhooks & API keys
- Interactive API browser with live testing

### 🖨️ Virtual Printer
- Emulates a Bambu Lab printer on your network
- Send prints directly from Bambu Studio/Orca Slicer
- Configurable printer model (X1C, P1S, A1, H2D, etc.)
- Queue mode or auto-start mode
- SSDP discovery (appears in slicer automatically)
- Secure TLS/MQTT communication

### 🛠️ Maintenance & Support
- Maintenance scheduling & tracking
- Interval reminders (hours/days)
- Print time accuracy stats
- File manager for printer storage
- Firmware update helper (LAN-only printers)
- Debug logging toggle with live indicator
- Support bundle generator (privacy-filtered)

</td>
</tr>
</table>

**Plus:** Customizable themes (style, background, accent) • Mobile responsive • Keyboard shortcuts • Multi-language (EN/DE) • Auto updates • Database backup/restore • System info dashboard

---

## 📸 Screenshots

<details>
<summary><strong>Click to expand screenshots</strong></summary>

<p align="center">
  <img src="docs/screenshots/printers.png" alt="Printers" width="800">
  <br><em>Real-time printer monitoring with AMS status</em>
</p>

<p align="center">
  <img src="docs/screenshots/archives.png" alt="Archives" width="800">
  <br><em>Print archive with 3D preview and project assignment</em>
</p>

<p align="center">
  <img src="docs/screenshots/reprint_ams_mapping.png" alt="Reprint AMS Mapping" width="800">
  <br><em>Re-print with AMS filament mapping preview</em>
</p>

<p align="center">
  <img src="docs/screenshots/edit-timelapse.png" alt="Timelapse Editor" width="800">
  <br><em>Built-in timelapse editor with trim, speed, and music</em>
</p>

<p align="center">
  <img src="docs/screenshots/projects.png" alt="Projects" width="800">
  <br><em>Group related prints into projects</em>
</p>

<p align="center">
  <img src="docs/screenshots/project-detail-1.png" alt="Project Detail" width="800">
  <br><em>Project detail view with assigned archives</em>
</p>

<p align="center">
  <img src="docs/screenshots/project-detail-2.png" alt="Project Detail Timeline" width="800">
  <br><em>Project timeline and print history</em>
</p>

<p align="center">
  <img src="docs/screenshots/print-queue.png" alt="Queue" width="800">
  <br><em>Print scheduling and queue management</em>
</p>

<p align="center">
  <img src="docs/screenshots/schedule-print.png" alt="Schedule Print" width="800">
  <br><em>Schedule prints for specific date and time</em>
</p>

<p align="center">
  <img src="docs/screenshots/statistics.png" alt="Statistics" width="800">
  <br><em>Customizable statistics dashboard</em>
</p>

<p align="center">
  <img src="docs/screenshots/maintenance-1.png" alt="Maintenance" width="800">
  <br><em>Maintenance tracking per printer</em>
</p>

<p align="center">
  <img src="docs/screenshots/maintenance-2.png" alt="Maintenance Settings" width="800">
  <br><em>Configure maintenance types and intervals</em>
</p>

<p align="center">
  <img src="docs/screenshots/cloud_profiles-1.png" alt="Cloud Profiles" width="800">
  <br><em>Bambu Cloud filament profiles</em>
</p>

<p align="center">
  <img src="docs/screenshots/cloud_profiles-2.png" alt="Cloud Profiles Edit" width="800">
  <br><em>Edit filament preset settings</em>
</p>

<p align="center">
  <img src="docs/screenshots/k_profiles-1.png" alt="K-Profiles" width="800">
  <br><em>Pressure advance (K-factor) profiles</em>
</p>

<p align="center">
  <img src="docs/screenshots/k_profiles-2.png" alt="K-Profiles Edit" width="800">
  <br><em>Edit K-factor profile settings</em>
</p>

<p align="center">
  <img src="docs/screenshots/settings-general.png" alt="Settings" width="800">
  <br><em>General configuration and integrations</em>
</p>

<p align="center">
  <img src="docs/screenshots/settings-powerplugs.png" alt="Smart Plugs" width="800">
  <br><em>Smart plug control and energy monitoring</em>
</p>

<p align="center">
  <img src="docs/screenshots/settings_notifications.png" alt="Notifications" width="800">
  <br><em>Multi-provider notification system</em>
</p>

<p align="center">
  <img src="docs/screenshots/settings_api_keys.png" alt="API Keys" width="800">
  <br><em>API keys and webhook endpoints</em>
</p>

<p align="center">
  <img src="docs/screenshots/settings-virtual-printer.png" alt="Virtual Printer Settings" width="800">
  <br><em>Virtual printer configuration</em>
</p>

<p align="center">
  <img src="docs/screenshots/slicer-virtual-printer.png" alt="Slicer Virtual Printer" width="800">
  <br><em>Virtual printer appears in Bambu Studio/Orca Slicer</em>
</p>

<p align="center">
  <img src="docs/screenshots/mqtt-debug-log.png" alt="MQTT Debug Log" width="800">
  <br><em>MQTT debug logging for troubleshooting</em>
</p>

<p align="center">
  <img src="docs/screenshots/quick_power_plug_sidebar.png" alt="Quick Power Plug" width="400">
  <br><em>Quick power plug control in sidebar</em>
</p>

</details>

---

## 🚀 Quick Start

### Requirements
- Python 3.10+ (3.11/3.12 recommended)
- Bambu Lab printer with **Developer Mode** enabled (see below)
- Same local network as printer

### Installation

#### Docker (Recommended)

**Option A: Pre-built image (fastest)**
```bash
mkdir bambuddy && cd bambuddy
curl -O https://raw.githubusercontent.com/maziggy/bambuddy/main/docker-compose.yml
docker compose up -d
```

**Option B: Build from source**
```bash
git clone https://github.com/maziggy/bambuddy.git
cd bambuddy
docker compose up -d --build
```

Open **http://localhost:8000** in your browser.

> **Multi-architecture support:** Pre-built images are available for `linux/amd64` and `linux/arm64` (Raspberry Pi 4/5).

> **macOS/Windows users:** Docker Desktop doesn't support `network_mode: host`. Edit docker-compose.yml: comment out `network_mode: host` and uncomment the `ports:` section. Printer discovery won't work - add printers manually by IP.

<details>
<summary><strong>Docker Configuration & Commands</strong></summary>

**Environment Variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `TZ` | `UTC` | Your timezone (e.g., `America/New_York`, `Europe/Berlin`) |
| `DEBUG` | `false` | Enable debug logging |
| `LOG_LEVEL` | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

**Data Persistence:**

| Volume | Purpose |
|--------|---------|
| `bambuddy.db` | SQLite database with all your print data |
| `archive/` | Archived 3MF files and thumbnails |
| `logs/` | Application logs |

**Updating:**

```bash
# Pre-built image: just pull the latest
docker compose pull && docker compose up -d

# From source: rebuild after pulling changes
cd bambuddy && git pull && docker compose up -d --build
```

**Useful Commands:**

```bash
# View logs
docker compose logs -f

# Stop/Start
docker compose down
docker compose up -d

# Shell access
docker compose exec bambuddy /bin/bash
```

**Custom Port:**

```yaml
ports:
  - "3000:8000"  # Access on port 3000
```

**Reverse Proxy (Nginx):**

```nginx
server {
    listen 443 ssl http2;
    server_name bambuddy.yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;
    }
}
```

> **Note:** WebSocket support is required for real-time printer updates.

**Network Mode Host** (required for printer discovery and camera streaming):

```yaml
services:
  bambuddy:
    build: .
    network_mode: host
```

> **Note:** Docker's default bridge networking cannot receive SSDP multicast packets for automatic printer discovery. When using `network_mode: host`, Bambuddy can discover printers via subnet scanning - enter your network range (e.g., `192.168.1.0/24`) in the Add Printer dialog.

</details>

#### Manual Installation

```bash
# Clone and setup
git clone https://github.com/maziggy/bambuddy.git
cd bambuddy
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** and add your printer!

> **Need detailed instructions?** See the [Installation Guide](http://wiki.bambuddy.cool/getting-started/installation/)

### Enabling Developer Mode

Developer Mode allows third-party software like Bambuddy to control your printer over the local network.

1. On printer: **Settings** → **Network** → **LAN Only Mode** → Enable
2. Enable **Developer Mode** (appears after LAN Only Mode is enabled)
3. Note the **Access Code** displayed
4. Find IP address in network settings
5. Find Serial Number in device info

> **Note:** Developer Mode disables cloud features but provides full local control. Standard LAN Mode (without Developer Mode) only allows read-only monitoring.

---

## 📚 Documentation

Full documentation available at **[wiki.bambuddy.cool](http://wiki.bambuddy.cool)**:

- [Installation](http://wiki.bambuddy.cool/getting-started/installation/) — All installation methods
- [Getting Started](http://wiki.bambuddy.cool/getting-started/) — First printer setup
- [Features](http://wiki.bambuddy.cool/features/) — Detailed feature guides
- [Troubleshooting](http://wiki.bambuddy.cool/reference/troubleshooting/) — Common issues & solutions
- [API Reference](http://wiki.bambuddy.cool/reference/api/) — REST API documentation

---

## 🖨️ Supported Printers

| Series | Models |
|--------|--------|
| H2 | H2D, H2S |
| X1 | X1, X1 Carbon |
| P1 | P1P, P1S, P2S |
| A1 | A1, A1 Mini |

---

## 🛠️ Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | Python, FastAPI, SQLAlchemy |
| Frontend | React, TypeScript, Tailwind CSS |
| Database | SQLite |
| 3D Viewer | Three.js |
| Communication | MQTT (TLS), FTPS |

---

## 🤝 Contributing

Contributions welcome! Here's how to help:

1. **Test** — Report issues with your printer model
2. **Translate** — Add new languages
3. **Code** — Submit PRs for bugs or features
4. **Document** — Improve wiki and guides

```bash
# Development setup
git clone https://github.com/maziggy/bambuddy.git
cd bambuddy

# Backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
DEBUG=true uvicorn backend.app.main:app --reload

# Frontend (separate terminal)
cd frontend && npm install && npm run dev
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 🙏 Acknowledgments

- [Bambu Lab](https://bambulab.com/) for amazing printers
- The reverse engineering community for protocol documentation
- All testers and contributors

---

<p align="center">
  Made with ❤️ for the 3D printing community
  <br><br>
  <a href="https://github.com/maziggy/bambuddy/issues">Report Bug</a> •
  <a href="https://github.com/maziggy/bambuddy/issues">Request Feature</a> •
  <a href="http://wiki.bambuddy.cool">Documentation</a>
</p>
