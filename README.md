<p align="center">
  <img src="static/img/bambuddy_logo_dark.png" alt="Bambuddy Logo" width="300">
</p>

<h1 align="center">Bambuddy</h1>

<p align="center">
  <strong>Self-hosted print archive and management system for Bambu Lab 3D printers</strong>
</p>

<p align="center">
  <a href="https://github.com/maziggy/bambuddy/releases"><img src="https://img.shields.io/github/v/release/maziggy/bambuddy?style=flat-square&color=blue" alt="Release"></a>
  <img src="https://github.com/maziggy/bambuddy/actions/workflows/ci.yml/badge.svg?branch=main">
  <img src="https://github.com/maziggy/bambuddy/actions/workflows/github-code-scanning/codeql/badge.svg">
  <img src="https://github.com/maziggy/bambuddy/actions/workflows/security.yml/badge.svg">
  <a href="https://github.com/maziggy/bambuddy/blob/main/LICENSE"><img src="https://img.shields.io/github/license/maziggy/bambuddy?style=flat-square" alt="License"></a>
  <a href="https://github.com/maziggy/bambuddy/stargazers"><img src="https://img.shields.io/github/stars/maziggy/bambuddy?style=flat-square" alt="Stars"></a>
  <a href="https://github.com/maziggy/bambuddy/issues"><img src="https://img.shields.io/github/issues/maziggy/bambuddy?style=flat-square" alt="Issues"></a>
  <a href="https://discord.gg/aFS3ZfScHM"><img src="https://img.shields.io/discord/1461241694715645994?style=flat-square&logo=discord&logoColor=white&label=Discord&color=5865F2" alt="Discord"></a>
  <a href="https://forum.bambuddy.cool"><img src="https://img.shields.io/badge/Forum-bambuddy.cool-00adef?style=flat-square&logo=discourse&logoColor=white" alt="Forum"></a>
  <a href="https://ko-fi.com/maziggy"><img src="https://img.shields.io/badge/Ko--fi-Support-ff5e5b?style=flat-square&logo=ko-fi&logoColor=white" alt="Ko-fi" target=_blank></a>
</p>

<p align="center">
  <a href="#-features">Features</a> •
  <a href="#-screenshots">Screenshots</a> •
  <a href="#-quick-start">Quick Start</a> •
  <a href="http://wiki.bambuddy.cool">Documentation</a> •
  <a href="https://forum.bambuddy.cool">Forum</a> •
  <a href="https://discord.gg/aFS3ZfScHM">Discord</a> •
  <a href="#-contributing">Contributing</a>
</p>

---

## 📣 Contributors Wanted — Help Shape Bambuddy

Bambuddy is a community-driven project and I'm **actively looking for contributors** — especially for two areas I can't cover alone:

- 📝 **Documentation writers** — help improve the wiki, guides, and feature docs so new users have a smooth onboarding
- ⚙️ **Discourse admin** — our **Discourse forum** is now live at [forum.bambuddy.cool](https://forum.bambuddy.cool) but still needs to be configured, themed, and tuned (categories, permissions, SSO, email, plugins, backups). If you know Discourse or want to dig in, I'd love your help.
- 💬 **Forum moderators** — help welcome newcomers, answer questions, and keep discussions healthy on the new forum

You don't need to be a developer for the docs or moderator roles. If you enjoy writing, helping others, or keeping a community friendly, you're exactly who we're looking for.

**Get in touch:**
- 🗣️ [Forum](https://forum.bambuddy.cool) — chats, longer discussions, guides, and community Q&A
- 💬 [Discord](https://discord.gg/aFS3ZfScHM) — fastest way to chat
- 🐙 [GitHub Discussions](https://github.com/maziggy/bambuddy/discussions) — open a thread
- 📧 **martin@bambuddy.cool** — email Martin directly (no GitHub or Discord needed)

---

## 🌐 NEW: Remote Printing with Proxy Mode

<p align="center">
  <img src="docs/images/proxy-mode-diagram.png" alt="Proxy Mode Architecture" width="800">
</p>

**Print from anywhere in the world** — Bambuddy's new Proxy Mode acts as a secure relay between your slicer and printer:

- 🔒 **End-to-end TLS encryption** — FTP, file transfer, and camera are transparently proxied with the printer's real TLS certificate
- 🛡️ **Optional Tailscale integration** — per-VP toggle + Docker socket mount brings Bambuddy into your tailnet, so virtual printers are reachable from any tailnet device over a private WireGuard tunnel without port forwarding ([setup](https://wiki.bambuddy.cool/features/virtual-printer/)). Bambuddy's self-signed CA import is still required for the slicer side because both Bambu Studio and OrcaSlicer only accept IP addresses in the Add Printer dialog — the Tailscale benefit here is the tunnel, not cert-import elimination.
- 🌍 **No cloud dependency** — Direct connection through your own Bambuddy server
- 🔑 **Uses printer's access code** — No additional credentials needed
- ⚡ **Full-speed printing** — Transparent TCP proxy, only MQTT is decrypted for IP rewriting

Perfect for remote print farms, traveling makers, or accessing your home printer from work.

👉 **[Setup Guide →](https://wiki.bambuddy.cool/features/virtual-printer/#proxy-mode-new-in-017)**

---

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
- Timelapse editor (trim, speed, music) with automatic AVI-to-MP4 conversion for P1-series printers, manual upload & remove
- Re-print to any connected printer with AMS mapping (auto-match or manual slot selection, multi-plate support, nozzle-aware matching for dual-nozzle H2D/H2D Pro)
- Plate thumbnail browsing for multi-plate archives (hover to navigate between plates)
- Archive comparison (side-by-side diff)
- Tag management (rename/delete across all archives)
- **Print Log** — Chronological table view of all print activity with columns for date/time, print name, printer, user, status, duration, and filament. Filterable by search, printer, user, status, and date range. Pagination with configurable page size. Clear button removes log entries without affecting archives.

### 📊 Monitoring & Control
- Real-time printer status via WebSocket
- Live camera streaming (MJPEG) & snapshots with multi-viewer support — most Bambu printers only allow one upstream connection, so Bambuddy fans out a single shared stream to all browser tabs / cards / overlays
- **Long-lived camera tokens** for Home Assistant / Frigate / kiosks — mint a token from Settings → API Keys, paste it once, capped at 365 days, revocable at any time (no infinite tokens — leaked permanent tokens are unsafe by design)
- **Streaming overlay for OBS** - Embeddable page with camera + status for live streaming (`/overlay/:printerId`), configurable FPS (`?fps=30`), status-only mode (`?camera=false`)
- External camera support (MJPEG, RTSP, HTTP snapshot, USB/V4L2) with layer-based timelapse
- **Build plate empty detection** - Auto-pause print if objects detected on plate (multi-reference calibration, ROI adjustment)
- Fan status monitoring (part cooling, auxiliary, chamber)
- Printer control (stop, pause, resume, chamber light, print speed, **airduct mode** for P2S/H2*, **build-plate Z-jog** with Studio-style not-homed warning)
- **Status badges on printer card**: SD Card (green / red), Enclosure Door (green / yellow — X1/P1S/P2S/H2*), Airduct Mode (cooling / heating)
- **Force Refresh** menu item — request a full status push from the printer without reconnecting
- Bulk printer actions (multi-select cards, then stop/pause/resume/clear all — select by state or location)
- Printer search and filters — live search by name/model/location/serial plus status and location dropdown filters (WebSocket-reactive, mobile-friendly)
- Resizable printer cards (S/M/L/XL)
- Skip objects during print
- AMS slot RFID re-read
- AMS slot configuration (model-filtered presets, K profiles, color picker, pre-population for configured slots)
- AMS info card (hover for serial number, firmware version) with custom friendly names that persist across printers
- **AMS remote drying** — Start, monitor, and stop drying sessions for AMS 2 Pro and AMS-HT directly from the Printers page with filament-based temperature/duration presets, optional spool rotation; automatic PSU detection and HMS power error reporting
- **Queue auto-drying** — Automatically dry filament between scheduled prints when humidity exceeds threshold; configurable presets per filament type, optional blocking mode
- **Ambient drying** — Automatically keep filament dry on idle printers based on humidity, regardless of whether prints are queued
- Configurable drying presets per filament type (temperature & duration for AMS 2 Pro and AMS-HT)
- Dual external spool support for H2D (Ext-L / Ext-R)
- HMS error monitoring with history and clear errors
- Print success rates & trends
- Filament usage tracking
- Cost analytics & failure analysis
- **AI print-failure detection** — Optional integration with a self-hosted [Obico](https://github.com/TheSpaghettiDetective/obico-server) ML API: watches each running print's camera feed, smooths scores over time (30-frame warmup + EWM + rolling means), and fires a configurable action once per print (notify / pause / pause-and-off)
- Per-user statistics filtering (admin permission gated)
- CSV/Excel export

### ⏰ Scheduling & Automation
- **Background print dispatch** — FTP uploads and print-start commands run in the background with real-time WebSocket progress toasts (per-job upload bars, status badges, cancel button)
- Print queue with drag-and-drop and timeline schedule view
- Multi-printer selection (send to multiple printers at once)
- Batch print quantity (print multiple copies — set quantity in the print/schedule dialog, first copy prints immediately, rest are queued)
- Staggered batch start (start printers in groups with configurable interval to avoid power spikes — works in both Print and Queue dialogs)
- Configurable default print options (bed levelling, flow/vibration calibration, first layer inspection, timelapse) in Settings → Workflow
- Model-based queue assignment (send to "any X1C" for load balancing) with location filtering
- Filament override for model-based queue (swap filament colors/types before scheduling)
- Filament validation (only assign to printers with required filaments)
- Prefer lowest remaining filament (consume partial spools first when multiple match)
- Per-printer AMS mapping (individual slot configuration for print farms)
- Scheduled prints (date/time)
- Shortest Job First scheduling (SJF toggle on queue page — scheduler picks shorter prints first, with starvation guard)
- Queue Only mode (stage without auto-start)
- Clear plate confirmation between queued prints (can be disabled in settings for farm workflows)
- Auto-print G-code injection (per-model start/end snippets for Farmloop, SwapMod, AutoClear, Printflow 3D — toggle per queue item)
- Smart plug integration (Tasmota, Home Assistant, MQTT, REST/Webhook)
- REST smart plugs: Control any device with an HTTP API (openHAB, ioBroker, FHEM, Node-RED) with separate power/energy URLs and unit multipliers
- MQTT smart plugs: Subscribe to Zigbee2MQTT, Shelly, or any MQTT topic for energy monitoring
- Energy consumption tracking (per-print kWh and cost) — restart-resilient: mid-print backend restarts no longer lose per-print energy
- Energy statistics by date range (Today / Week / Month / …) in total-consumption mode via hourly lifetime-counter snapshots
- HA energy sensor support (for plugs with separate power/energy sensors)
- Auto power-on before print
- Auto power-off after cooldown

### 📁 File Manager (Library)
- Upload and organize sliced files (3MF, gcode, STL)
- **External folder mounting** - Mount host directories (NAS, USB, network shares) without copying files
- **STL thumbnail generation** - Auto-generate previews for STL files on upload or batch generate for existing files
- ZIP file extraction with folder structure preservation
- Option to create folder from ZIP filename
- Folder structure with drag-and-drop
- Rename files and folders via context menu
- Print directly to any printer with full options
- Add to queue without creating archive upfront
- Plate selection for multi-plate 3MF files
- Duplicate detection via file hash
- Mobile-friendly with always-visible action buttons
- **Server-side Slice button** (optional) — slice STL/3MF without a desktop slicer when the [`slicer-api/` Compose stack](slicer-api/README.md) is running; the result lands as a new `.gcode.3mf` in the same folder, with progress shown via a toast tracker that follows the job to completion

### 🌍 MakerWorld Integration
- Paste any `makerworld.com/models/…` URL → preview, plate picker, and import without leaving Bambuddy
- Per-plate **Save** or **Save & Slice in Bambu Studio / OrcaSlicer** (your preferred slicer from Settings)
- **Import all plates** button for multi-plate models
- Auto-creates a "MakerWorld" folder in File Manager; override with any existing folder via the picker
- Per-plate image gallery with keyboard-navigable lightbox
- Recent imports sidebar — last 10 MakerWorld imports with one-click jump to File Manager or slicer
- Remove-from-library for imported plates with confirm modal (no LAN cookie paste, no browser extension)
- Reuses your existing Bambu Cloud login — no separate OAuth flow or browser extension to install

### 📁 Projects
- Group related prints (e.g., "Voron Build")
- Track plates (print jobs) and parts separately
- Auto-detect parts count from 3MF files
- Color-coded project badges
- **Project URL + cover photo** — paste a MakerWorld/Printables/Thingiverse link and upload a hero image so each card is immediately recognisable; the URL renders as a one-click link beside the project name
- Bulk assign archives via multi-select toolbar
- Import/Export projects as ZIP (includes files) or JSON
- Print or queue files from linked library folders directly in the project view (resulting archive auto-linked to the project)

</td>
<td width="50%" valign="top">

### 🔔 Notifications
- WhatsApp, Telegram, Discord
- Email, Pushover, ntfy (with per-event priority — Min / Low / Default / High / Urgent)
- Home Assistant persistent notifications
- Custom webhooks
- Quiet hours & daily digest
- Customizable message templates with per-filament usage details
- Print finish photo URL in notifications
- Filament usage and progress in failed/cancelled print notifications
- **Missing spool assignment warning** — Toast and push notification when a print starts with unassigned AMS trays
- HMS error alerts (AMS, nozzle, etc.)
- Build plate detection alerts
- First layer complete alert (with camera snapshot)
- Bed cooled alerts (configurable threshold)
- Queue events (waiting, skipped, failed)

### 🧵 Spool Inventory
- Built-in spool inventory with AMS slot assignment, usage tracking, and remaining weight management
- Automatic filament consumption tracking: 3MF slicer estimates for all spools (primary), AMS remain% delta as fallback
- Mid-print spool reassignment support: uses live assignment if changed during print, snapshot otherwise
- Per-layer gcode accuracy for partial prints (failed/cancelled), with linear scaling fallback
- **Per-spool cost tracking** — Set cost/kg on each spool; costs are automatically calculated at print completion and aggregated to archives. Print modal shows real-time cost preview. Configurable default cost and currency in Settings.
- **Bulk spool addition** — Add multiple identical spools at once (quantity 1–100) with a single form submission. Quick Add mode for stock spools that only need material, color, and weight.
- Spool catalog, color catalog, PA profile matching, and low-stock alerts
- **Multi-colour gradients, transparency, and visual effects** — Paste a comma-separated hex list (e.g. from 3dfilamentprofiles.com) to render a spool as a gradient or conic colour wheel; transparency shows through a checkerboard so the alpha you set is the alpha you see; pick a visual effect (sparkle, wood, marble, glow, matte) for the swatch overlay. Same fields are editable on the colour catalog so combos can be reused across spools.

### 🔧 Integrations
- [Spoolman](https://github.com/Donkie/Spoolman) filament sync with per-filament usage tracking and fill level display
- MQTT publishing for Home Assistant, Node-RED, etc.
- **Prometheus metrics** - Export printer telemetry for Grafana dashboards
- Bambu Cloud profile management
- **Local Profiles** - Import OrcaSlicer presets (`.orca_filament`, `.bbscfg`, `.bbsflmt`, `.zip`, `.json`) without Bambu Cloud
- K-profiles (pressure advance)
- **GitHub backup** - Schedule automatic backups of cloud profiles, k profiles and settings to GitHub
- **Scheduled local backups** - Automatic backup snapshots on hourly/daily/weekly schedule with retention management and NAS-mountable output
- External sidebar links
- Webhooks & API keys
- Interactive API browser with live testing

### 🖨️ Virtual Printer & Remote Printing
- **🌐 Proxy Mode (NEW!)** — Print remotely from anywhere via secure TLS relay
- Emulates a Bambu Lab printer on your network
- Send prints directly from Bambu Studio/Orca Slicer
- Configurable printer model (X1C, P1S, A1, H2D, etc.)
- Archive mode, Review mode, Queue mode, or Proxy mode
- SSDP discovery (same LAN) or manual IP entry (VPN/remote)
- Network interface override for multi-NIC/Docker/VPN setups
- Secure TLS/MQTT/FTP communication

### 🛠️ Maintenance & Support
- Maintenance scheduling & tracking
- Interval reminders (hours/days)
- Print time accuracy stats
- File manager for printer storage
- Firmware update helper with version badge (LAN-only printers) — lists all announced versions with Usable/Unavailable/Installed badges and supports rollback to older firmware
- Debug logging toggle with live indicator
- Live application log viewer with filtering
- Support bundle generator with comprehensive diagnostics (privacy-filtered)
- **In-app bug reporting** — Submit bug reports directly from the UI with optional screenshot (upload, paste, or drag & drop), interactive debug log capture (start logging, reproduce at your own pace, stop & submit), and system info. Reports create GitHub issues via a secure relay. Privacy-first: all logs are sanitized and sensitive data (IPs, serials, credentials) is never included.

### 🔒 Optional Authentication
- Enable/disable authentication any time
- Group-based permissions (80+ granular permissions)
- Default groups: Administrators, Operators, Viewers
- JWT tokens with secure password hashing
- Comprehensive API protection (200+ endpoints secured)
- User management (create, edit, delete, groups)
- User activity tracking (who uploaded archives, library files, queued prints, started prints)
- **Per-user Bambu Cloud accounts** — Each user has their own independent Cloud login for profiles
- **Advanced Auth via Email** — SMTP integration for automated user onboarding and self-service password resets
- Admin creates users with email — system sends secure random password automatically
- Users can reset their own password from the login screen (no admin needed)
- Customizable email templates (welcome email, password reset)
- **Two-Factor Authentication (TOTP + Email OTP)** — Per-user opt-in 2FA compatible with Google Authenticator, Authy, 2FAS and any standard TOTP app, or a 6-digit code delivered by email. Each user gets 10 single-use backup codes. Brute-force-protected (per-user + per-IP rate limits), replay-protected (same code cannot be accepted twice in the same 30 s window), and the pre-auth token is a single-use DB-backed challenge bound to the browser session via an HttpOnly cookie.
- **Single Sign-On (OIDC / SSO)** — Log in via PocketID, Authentik, Keycloak, or any standards-compliant OIDC provider. PKCE (S256) for public clients, `email_verified` gating, issuer & `aud`/`nonce` validation, opt-in account linking via verified email, optional auto-provisioning of new BamBuddy accounts, and strict SSRF hardening on every URL pulled from the OIDC discovery document (scheme + private/loopback/link-local IP checks).
- **Per-user email notifications** — Users receive email alerts for their own print jobs (start, complete, failed, stopped) with individual toggle controls

</td>
</tr>
</table>

**Plus:** Configurable slicer (Bambu Studio / OrcaSlicer) • Customizable themes (style, background, accent) • Mobile responsive • Keyboard shortcuts • Multi-language (EN/DE/JA/IT) • Auto updates • Database backup/restore • System info dashboard

---

## 🎬 Demo

<p align="center">
  <a href="https://youtu.be/bmq2Z0lEXeo">
    <img src="https://img.youtube.com/vi/bmq2Z0lEXeo/maxresdefault.jpg" alt="Bambuddy Demo Video" width="800">
  </a>
  <br><em>Click to watch the demo on YouTube</em>
</p>

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
- **"Store sent files on external storage"** enabled in Bambu Studio/OrcaSlicer
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

> **Linux users:** If you get "permission denied" errors, either prefix commands with `sudo` (e.g., `sudo docker compose up -d`) or [add your user to the docker group](https://docs.docker.com/engine/install/linux-postinstall/).

<details>
<summary><strong>Docker Configuration & Commands</strong></summary>

**Environment Variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `TZ` | `UTC` | Your timezone (e.g., `America/New_York`, `Europe/Berlin`) |
| `PORT` | `8000` | Port BamBuddy runs on (with host networking mode) |
| `DEBUG` | `false` | Enable debug logging |
| `LOG_LEVEL` | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

**Data Persistence:**

| Volume | Purpose |
|--------|---------|
| `bambuddy.db` | SQLite database with all your print data (not used with PostgreSQL) |
| `archive/` | Archived 3MF files and thumbnails |
| `logs/` | Application logs |

**Updating:**

```bash
# Pre-built image: just pull the latest
docker compose pull && docker compose up -d

# From source: rebuild after pulling changes
cd bambuddy && git pull && docker compose up -d --build
```

**Daily Beta Builds:**

Beta builds with the latest fixes are pushed regularly to the same beta version tag:

```bash
# Pull the current beta
docker pull ghcr.io/maziggy/bambuddy:0.2.2b1
# or from Docker Hub
docker pull maziggy/bambuddy:0.2.2b1
```

Use [Watchtower](https://containrrr.dev/watchtower/) to automatically update when new daily builds are pushed.

> **Note:** Beta builds use version tags like `0.2.2b1` — they are never tagged as `latest`. Your stable installation won't auto-update to a beta unless you explicitly pull a beta tag.

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

> **Note:** Docker's default bridge networking cannot receive SSDP multicast packets for automatic printer discovery. When using `network_mode: host`, Bambuddy auto-detects your network subnet and can discover printers via subnet scanning in the Add Printer dialog.

</details>

#### Manual Installation (Linux/macOS)

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

### Slicer Settings

In Bambu Studio or OrcaSlicer, enable **"Store sent files on external storage"** so that print files (3MF) are saved to the printer's SD card. Bambuddy needs these files to extract thumbnails and 3D model previews.

1. Open **Bambu Studio** or **OrcaSlicer**
2. Go to the **Device** tab for your printer
3. In **Print Options**, enable **Store Sent Files on External Storage**

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
| X1 | X1, X1 Carbon, X1E |
| X2 | X2D |
| H2 | H2D, H2D Pro, H2C, H2S |
| P1 | P1P, P1S |
| P2 | P2S |
| A1 | A1, A1 Mini |

---

## 🛠️ Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | Python, FastAPI, SQLAlchemy |
| Frontend | React, TypeScript, Tailwind CSS |
| Database | SQLite (default) or PostgreSQL |
| 3D Viewer | Three.js |
| Communication | MQTT (TLS), FTPS |

---

## 🤝 Contributing

Contributions welcome! **I'm especially looking for help with documentation and our new [Discourse forum](https://forum.bambuddy.cool)** — see [Contributors Wanted](#-contributors-wanted--help-shape-bambuddy) above. Other ways to help:

1. **📝 Document** — Improve the wiki and guides *(urgently needed!)*
2. **⚙️ Admin Discourse** — Help configure and tune the [forum](https://forum.bambuddy.cool) *(urgently needed!)*
3. **💬 Moderate** — Welcome newcomers and keep [forum](https://forum.bambuddy.cool) discussions healthy *(urgently needed!)*
4. **Test** — Report issues with your printer model
5. **Translate** — Add new languages
6. **Code** — Submit PRs for bugs or features

Not sure where to start? Reach out on [Discord](https://discord.gg/aFS3ZfScHM), post on the [forum](https://forum.bambuddy.cool), or email **martin@bambuddy.cool** — I'll help you find something that fits.

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

AGPL-3.0 License — see [LICENSE](LICENSE) for details.

---

## 🙏 Acknowledgments

- [SpoolEase](https://github.com/yanshay/SpoolEase) by yanshay — early inspiration for NFC-based spool tracking and AMS inventory concepts
- [Bambu Lab](https://bambulab.com/) for amazing printers
- The reverse engineering community for protocol documentation
- All testers and contributors

---

If you like Bambuddy and want to support it, you can <a href="https://ko-fi.com/maziggy" target=_blank>buy Martin a coffee</a>.

---

<p align="center">
  Made with ❤️ for the 3D printing community
  <br><br>
  <a href="https://forum.bambuddy.cool">Forum</a> •
  <a href="https://discord.gg/aFS3ZfScHM">Join our Discord</a> •
  <a href="https://github.com/maziggy/bambuddy/issues">Report Bug</a> •
  <a href="https://github.com/maziggy/bambuddy/issues">Request Feature</a> •
  <a href="http://wiki.bambuddy.cool">Documentation</a>
</p>
