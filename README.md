v∆v

<p align="center">
  <img src="static/img/bambusy_logo_dark.png" alt="Bambusy Logo" width="300">
</p>

<p align="center">
  <strong>A self-hosted print archive and management system for Bambu Lab 3D printers</strong>
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#screenshots">Screenshots</a> •
  <a href="#installation">Installation</a> •
  <a href="#configuration">Configuration</a> •
  <a href="#usage">Usage</a> •
  <a href="#contributing">Contributing</a>
</p>

---

## Features

- **Multi-Printer Support** - Connect and monitor multiple Bambu Lab printers (H2D and X1C testet. H2C, H2S, X1, X1C, P1P, P1S, A1 and A1 Mini needs to be testet)
- **Automatic Print Archiving** - Automatically saves 3MF files with full metadata extraction
- **3D Model Preview** - Interactive Three.js viewer for archived prints
- **Real-time Monitoring** - Live printer status via WebSocket with print progress, temperatures, layer count, and more
- **Print Queue & Scheduling** - Schedule prints for specific times with powerful automation:
  - Queue multiple prints per printer
  - Schedule prints for a specific date/time
  - Auto power-on printer before scheduled print starts
  - Auto power-off after print completes (with nozzle cooldown)
  - Stop active prints with one click
  - Drag-and-drop queue reordering
- **Smart Plug Integration** - Control Tasmota-based smart plugs with automation:
  - Auto power-on when print starts
  - Auto power-off when print completes
  - Time-based delay (1-60 minutes)
  - Temperature-based delay (waits for nozzle to cool down)
- **Print Statistics Dashboard** - Customizable dashboard with drag-and-drop widgets
  - Print success rates
  - Filament usage trends
  - Print activity calendar
  - Cost tracking
  - Time accuracy tracking
- **Print Time Accuracy** - Compare estimated vs actual print times
  - Color-coded badges (green=accurate, blue=faster, orange=slower)
  - Per-printer accuracy statistics
  - Dashboard widget showing average accuracy
- **Duplicate Detection** - Automatically detect when models have been printed before
  - SHA256 content hash for exact matching
  - Purple badge indicator on archive cards
  - "Duplicates" collection filter to find all duplicates
  - View duplicate history when viewing archive details
- **HMS Error Monitoring** - Real-time Health Management System status
  - Always-visible status indicator on printer cards
  - Green "OK" when healthy, orange/red when errors detected
  - Severity-based coloring (fatal, serious, common, info)
- **MQTT Debug Logging** - Built-in debugging tool for printer communication
  - Start/stop logging with one click
  - Real-time message capture with auto-refresh
  - View incoming and outgoing MQTT messages
  - Expandable JSON payloads for detailed inspection
- **Filament Cost Tracking** - Track costs per print with customizable filament database
- **Photo Attachments** - Attach photos to archived prints for documentation
  - Automatic finish photo capture when print completes (via printer camera)
  - Manual photo uploads
- **Print Metadata** - Rich metadata extracted from 3MF files:
  - Print time estimate and actual time comparison
  - Filament usage (grams) and type
  - Total layer count and layer height
  - Nozzle/bed temperatures
  - Multi-color support with color swatches
- **Failure Analysis** - Document failed prints with notes and photos
- **Project Page Editor** - View and edit embedded MakerWorld project pages with images, descriptions, and designer info
- **K-Profiles (Pressure Advance)** - Manage pressure advance settings directly on your printers
  - View, edit, add, and delete K-profiles per printer
  - Filter by nozzle size (0.2, 0.4, 0.6, 0.8mm) and flow type (High Flow, Standard)
  - Search by profile name or filament
  - Dual-nozzle support for H2 series (auto-detected from MQTT)
  - Left/Right extruder column layout for dual-nozzle printers
- **Push Notifications** - Get notified about print events via multiple channels:
  - WhatsApp (via CallMeBot)
  - ntfy (self-hosted or ntfy.sh)
  - Pushover
  - Telegram
  - Email (SMTP with TLS/SSL/plain options)
  - Configurable event triggers (start, complete, failed, stopped, progress milestones)
  - Quiet hours to suppress notifications during sleep
  - Per-printer filtering
- **Spoolman Integration** - Sync AMS filament data with your Spoolman server
  - Automatic or manual sync modes
  - Per-printer or all-printer sync
  - Auto-creates spools and filaments in Spoolman
  - Matches Bambu Lab spools by unique tray UUID
  - Tracks filament usage during prints
  - Third-party spools (SpoolEase, etc.) gracefully skipped
- **Cloud Profiles Sync** - Access your Bambu Cloud slicer presets
- **File Manager** - Browse and manage files on your printer's SD card
- **Re-print** - Send archived prints back to any connected printer
- **Dark/Light Theme** - Easy on the eyes, day or night
- **Keyboard Shortcuts** - Quick navigation with keyboard shortcuts
- **Multi-language Support** - Interface available in English and German
  - Browser language auto-detection
  - Manual language override in settings
  - Separate notification language setting
- **Auto Updates** - Automatic update checking and one-click updates from GitHub releases
- **Maintenance Tracker** - Track and schedule printer maintenance
  - Customizable maintenance types (nozzle changes, lubrication, belt tension, etc.)
  - Interval-based reminders (print hours or calendar days)
  - Maintenance history logging
  - Push notifications when maintenance is due

## Screenshots

<!-- Add your screenshots here -->
<p align="center">
  <img src="docs/screenshots/statistics.png" alt="Statustics" width="800">
  <br><em>Customizable Dashboard with drag-and-drop widgets</em>
</p>

<p align="center">
  <img src="docs/screenshots/printers.png" alt="Printers" width="800">
  <br><em>Real-time printer monitoring</em>
</p>

<p align="center">
  <img src="docs/screenshots/archives.png" alt="Archives" width="800">
  <br><em>Print archive with grid and list views</em>
</p>

<p align="center">
  <img src="docs/screenshots/queue.png" alt="Queue" width="800">
  <br><em>Queueing and scheduling</em>
</p>

<p align="center">
  <img src="docs/screenshots/k_profiles.png" alt="k Profiles" width="800">
  <br><em>k Profiles</em>
</p>

<p align="center">
  <img src="docs/screenshots/cloud_profiles.png" alt="Cloud Profiles" width="800">
  <br><em>Cloud Profiles</em>
</p>

<p align="center">
  <img src="docs/screenshots/maintenance.png" alt="Maintenance" width="800">
  <br><em>Maintenance</em>
</p>

<p align="center">
  <img src="docs/screenshots/settings.png" alt="Settings" width="800">
  <br><em>Settings</em>
</p>

## Requirements

### System Requirements
- **Python 3.10+** (3.11 or 3.12 recommended)
- **Node.js 18+** (only needed if building frontend from source)
- **Git** (for cloning the repository)

### Network Requirements
- Bambu Lab printer with **LAN Mode** enabled
- Printer and Bambusy server must be on the same local network
- Ports used: 8883 (MQTT/TLS), 990 (FTPS)

### Supported Printers
- Bambu Lab H2C / H2D / H2S
- Bambu Lab X1 / X1 Carbon
- Bambu Lab P1P / P1S
- Bambu Lab A1 / A1 Mini

## Installation

### Option 1: Quick Install (Linux/macOS)

```bash
# Clone the repository
git clone https://github.com/maziggy/bambusy.git
cd bambusy

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# Start the server
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 in your browser.

### Option 2: Detailed Install (All Platforms)

#### Step 1: Install Prerequisites

**macOS:**
```bash
# Install Homebrew if not installed
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Python and Node.js
brew install python@3.12 node
```

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip nodejs npm git
```

**Windows:**
1. Download and install [Python 3.12](https://www.python.org/downloads/) (check "Add to PATH")
2. Download and install [Node.js LTS](https://nodejs.org/)
3. Download and install [Git](https://git-scm.com/download/win)

#### Step 2: Clone the Repository

```bash
git clone https://github.com/maziggy/bambusy.git
cd bambusy
```

#### Step 3: Set Up Python Environment

**Linux/macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

**Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

**Windows (Command Prompt):**
```cmd
python -m venv venv
venv\Scripts\activate.bat
pip install --upgrade pip
pip install -r requirements.txt
```

#### Step 4: Build Frontend (Optional)

The repository includes pre-built frontend files in `/static`. If you want to build from source:

```bash
cd frontend
npm install
npm run build
cd ..
```

#### Step 5: Run the Application

```bash
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 in your browser.

### Running as a Service (Linux)

Create a systemd service for automatic startup:

```bash
sudo nano /etc/systemd/system/bambusy.service
```

Add the following content (adjust paths as needed):

```ini
[Unit]
Description=Bambusy Print Archive
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/bambusy
Environment="PATH=/home/YOUR_USERNAME/bambusy/venv/bin"
ExecStart=/home/YOUR_USERNAME/bambusy/venv/bin/uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable bambusy
sudo systemctl start bambusy

# Check status
sudo systemctl status bambusy

# View logs
sudo journalctl -u bambusy -f
```

### Running with Docker (Coming Soon)

```bash
docker run -d \
  --name bambusy \
  -p 8000:8000 \
  -v bambusy_data:/app/data \
  -v bambusy_archive:/app/archive \
  maziggy/bambusy:latest
```

### Updating Bambusy

```bash
cd bambusy
git pull origin main

# Activate virtual environment
source venv/bin/activate  # Linux/macOS
# or: .\venv\Scripts\Activate.ps1  # Windows PowerShell

# Update dependencies
pip install -r requirements.txt

# Rebuild frontend (if needed)
cd frontend
npm install
npm run build
cd ..

# Restart the application
```

## Configuration

### Enabling LAN Mode on Your Printer

To connect Bambusy to your printer, you need to enable LAN Mode:

1. On your printer, go to **Settings** > **Network** > **LAN Mode**
2. Enable **LAN Mode** (this requires Developer Mode to be enabled first)
3. Note down the **Access Code** displayed
4. Find your printer's **IP Address** in network settings
5. Find your printer's **Serial Number** in device info

### Adding a Printer in Bambusy

1. Go to the **Printers** page
2. Click **Add Printer**
3. Enter:
   - **Name**: A friendly name for your printer
   - **IP Address**: Your printer's local IP
   - **Access Code**: The code from LAN Mode settings
   - **Serial Number**: Your printer's serial number
4. Click **Save**

The printer should connect automatically and show real-time status.

### Environment Variables

Bambusy can be configured using environment variables or a `.env` file in the project root. Copy `.env.example` to `.env` and adjust as needed:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|----------|---------|-------------|
| `DEBUG` | `false` | Enable debug mode (verbose logging, SQL queries) |
| `LOG_LEVEL` | `INFO` | Log level when DEBUG=false (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_TO_FILE` | `true` | Write logs to `logs/bambutrack.log` |

**Production (default):**
- INFO level logging
- SQLAlchemy and HTTP library noise suppressed
- Logs written to `logs/bambutrack.log` (5MB rotating, 3 backups)

**Development (`DEBUG=true`):**
- DEBUG level logging (verbose)
- All SQL queries logged
- Useful for troubleshooting printer connections

Example `.env` for development:
```bash
DEBUG=true
LOG_TO_FILE=true
```

## Usage

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `1` | Go to Printers |
| `2` | Go to Archives |
| `3` | Go to Statistics |
| `4` | Go to Cloud Profiles |
| `5` | Go to Settings |
| `?` | Show keyboard shortcuts |

### Dashboard Widgets

The statistics dashboard features draggable, resizable widgets:
- **Drag** widgets by the grip handle to reorder
- **Resize** widgets by clicking the resize icon (cycles through 1/4, 1/2, full width)
- **Hide** widgets by clicking the eye icon
- **Reset** layout using the Reset Layout button

Your layout preferences are saved automatically.

### Archiving Prints

Prints are automatically archived when they complete. You can also:
- Manually add photos to any archive
- Add failure analysis notes for failed prints
- Re-print any archived 3MF to a connected printer
- Export archives for backup

### Print Queue & Scheduling

The print queue allows you to schedule prints for later execution with smart automation.

#### Adding to Queue

1. Go to the **Archives** page
2. Right-click an archive and select **Schedule**, or click the calendar icon
3. Choose a printer and optional scheduled time
4. Configure options:
   - **Scheduled Time**: Leave empty for "next available" or pick a specific date/time
   - **Auto Power Off**: Automatically turn off the printer when the print completes

#### Queue Management

- **View Queue**: Click the queue icon on any printer card, or go to the Queue page
- **Reorder**: Drag and drop queue items to change print order
- **Cancel Pending**: Click the X button on any pending queue item
- **Stop Active Print**: Click the stop button on an actively printing item (with confirmation)

#### Automation Flow

When a scheduled print is ready to start:
1. **Power On**: If linked to a smart plug, the printer powers on automatically
2. **Wait for Connection**: System waits for the printer to connect (up to 2 minutes)
3. **Upload & Start**: The 3MF file is uploaded via FTP and the print starts
4. **Monitor**: Progress is tracked in real-time
5. **Completion**: When the print finishes:
   - Queue item is marked complete/failed
   - If "Auto Power Off" was enabled, waits for nozzle to cool below 50°C
   - Printer powers off automatically

### Project Page

3MF files downloaded from MakerWorld contain embedded project pages with model information. To view:
1. Right-click any archive in the Archives page
2. Select "Project Page" from the context menu
3. View title, description, designer info, license, and images
4. Click "Edit" to modify the project page metadata
5. Changes are saved directly to the 3MF file

### K-Profiles (Pressure Advance)

K-profiles store pressure advance (Linear Advance) settings for different filament and nozzle combinations. Bambusy lets you view and manage these settings directly on your printers.

#### Viewing K-Profiles

1. Go to **Settings** > **K-Profiles**
2. Select a connected printer from the dropdown
3. Choose a nozzle size (0.2, 0.4, 0.6, or 0.8mm)
4. Profiles are displayed with:
   - K-value (pressure advance factor)
   - Profile name and filament
   - Flow type (HF = High Flow, S = Standard)

#### Dual-Nozzle Printers (H2 Series)

For dual-nozzle printers (H2D, H2C, H2S), Bambusy automatically detects the nozzle configuration and displays:
- **Left/Right columns** showing profiles for each extruder
- **Extruder filter** to show profiles for one extruder only
- **Extruder selector** when adding new profiles

The nozzle count is auto-detected from MQTT temperature data when the printer connects.

#### Editing K-Profiles

1. Click on any profile card to open the edit modal
2. Modify the K-value (typical ranges: 0.01-0.06 for PLA, 0.02-0.10 for PETG)
3. Click **Save** to update the profile on the printer
4. Click the trash icon to delete a profile (with confirmation)

#### Adding K-Profiles

1. Click **Add Profile** in the header
2. Select a filament from the dropdown (populated from existing profiles on the printer)
3. Choose flow type (High Flow or Standard) and nozzle size
4. For dual-nozzle printers, select Left or Right extruder
5. Enter the K-value and click **Save**

**Note:** Filaments must first be calibrated in Bambu Studio to appear in the dropdown. Bambusy reads the filament list from existing K-profiles on the printer.

#### Filtering and Search

- **Search**: Type to filter by profile name or filament ID
- **Extruder filter** (dual-nozzle only): Show All, Left Only, or Right Only
- **Flow type filter**: Show All, HF Only, or S Only

### Smart Plug Integration

Bambusy supports Tasmota-based smart plugs for automated power control. This is useful for:
- Automatically turning on your printer when a print starts
- Safely turning off the printer after it cools down
- Energy savings by powering off idle printers

#### Supported Devices

Any smart plug running [Tasmota](https://tasmota.github.io/docs/) firmware with HTTP API enabled. Popular compatible devices include:
- Sonoff S31 / S26
- Gosund / Teckin / Treatlife smart plugs
- Any ESP8266/ESP32-based plug with Tasmota

#### Setting Up a Smart Plug

1. Go to **Settings** > **Smart Plugs**
2. Click **Add Plug**
3. Enter the plug's IP address and click **Test** to verify connection
4. Give it a name (auto-filled from device if available)
5. Optionally add username/password if your Tasmota requires authentication
6. Link it to a printer for automation
7. Click **Add**

#### Automation Options

Once linked to a printer, you can configure:

| Setting | Description |
|---------|-------------|
| **Enabled** | Master toggle for all automation |
| **Auto On** | Turn on plug when a print starts |
| **Auto Off** | Turn off plug when print completes |
| **Delay Mode** | Choose how to delay the power-off |

**Delay Modes:**
- **Time-based**: Wait a fixed number of minutes (1-60) after print completes
- **Temperature-based**: Wait until nozzle temperature drops below threshold (default 70°C)

#### Manual Control

Each plug card shows:
- Current status (ON/OFF/Offline)
- On/Off buttons for manual control
- Expandable settings panel

### Push Notifications

Bambusy can send push notifications when print events occur. Notifications are useful for monitoring prints remotely without checking the app constantly.

#### Supported Providers

| Provider | Description | Setup Required |
|----------|-------------|----------------|
| **WhatsApp** | Via [CallMeBot](https://www.callmebot.com/blog/free-api-whatsapp-messages/) | Free API key from CallMeBot |
| **ntfy** | Self-hosted or [ntfy.sh](https://ntfy.sh) | Just a topic name (no account needed for public server) |
| **Pushover** | [Pushover](https://pushover.net/) push notifications | Pushover account + app token |
| **Telegram** | Via Telegram Bot | Bot token from @BotFather |
| **Email** | SMTP email | SMTP server credentials |

#### Adding a Notification Provider

1. Go to **Settings** > **Notifications**
2. Click **Add Provider**
3. Select a provider type and enter the required configuration
4. Click **Send Test** to verify the configuration works
5. Configure which events should trigger notifications
6. Click **Add**

#### Event Triggers

Configure which events send notifications:

| Event | Description |
|-------|-------------|
| **Print Started** | When a print job begins |
| **Print Completed** | When a print finishes successfully |
| **Print Failed** | When a print fails or errors out |
| **Print Stopped** | When you manually stop/cancel a print |
| **Progress Milestones** | At 25%, 50%, and 75% progress |
| **Printer Offline** | When a printer disconnects |
| **Printer Error** | When HMS errors are detected |
| **Low Filament** | When filament is running low |

#### Quiet Hours

Enable quiet hours to suppress notifications during sleep or work hours:

1. Enable **Quiet Hours** toggle
2. Set start time (e.g., 22:00)
3. Set end time (e.g., 07:00)

Notifications during quiet hours are silently skipped.

#### Per-Printer Filtering

By default, notifications are sent for all printers. To limit notifications to a specific printer:

1. Open the notification provider settings
2. Select a printer from the **Printer** dropdown
3. Only events from that printer will trigger notifications

### Spoolman Integration

Bambusy integrates with [Spoolman](https://github.com/Donkie/Spoolman) for filament inventory management. When enabled, AMS filament data syncs with your Spoolman server, allowing you to track remaining filament across all your spools.

#### Prerequisites

- A running Spoolman server (self-hosted or Docker)
- Bambu Lab spools with original RFID tags in your AMS
- Spools registered in Spoolman with matching filament types

#### Setting Up Spoolman

1. Go to **Settings** > scroll to **Spoolman Integration**
2. Enable the **Enable Spoolman** toggle
3. Enter your Spoolman server URL (e.g., `http://192.168.1.100:7912`)
4. Click **Save**
5. Click **Connect** to establish the connection

#### Sync Modes

| Mode | Description |
|------|-------------|
| **Automatic** | AMS data syncs automatically when changes are detected (filament loaded/unloaded, usage during prints) |
| **Manual Only** | Only sync when you click the Sync button |

#### Manual Sync

When connected:
1. Select a specific printer from the dropdown, or "All Printers"
2. Click **Sync** to sync AMS data to Spoolman
3. Results show how many trays were synced

#### How Syncing Works

Bambusy matches AMS spools to Spoolman spools using the **tray UUID** - a unique 32-character identifier that Bambu Lab assigns to each original spool. This ensures consistent matching across different printer models.

**What gets synced:**
- Remaining filament weight (from AMS sensor)
- Filament usage during prints (deducted from Spoolman inventory)

**Auto-Creation:**
- When a Bambu Lab spool is detected that doesn't exist in Spoolman, it's automatically created
- Filament types are matched by material and color, or created if needed
- The "Bambu Lab" vendor is auto-created if it doesn't exist
- New spools include a comment noting they were auto-created

**Limitations:**
- Only **Bambu Lab original spools** can be synced (they have valid tray UUIDs)
- Third-party spools (SpoolEase, refilled spools, etc.) are gracefully skipped - they won't cause errors

#### Troubleshooting Spoolman

**Third-party spools showing in AMS:**
- SpoolEase and other third-party spools are automatically skipped during sync
- This is normal behavior - they don't have Bambu Lab tray UUIDs

**Connection issues:**
- Verify the Spoolman URL is accessible from your Bambusy server
- Check that no firewall is blocking port 7912 (or your custom port)
- Ensure Spoolman is running and healthy

#### Provider Setup Guides

**WhatsApp (CallMeBot):**
1. Add CallMeBot to your contacts: +34 644 51 95 23
2. Send "I allow callmebot to send me messages" via WhatsApp
3. You'll receive an API key
4. Enter your phone number (with country code) and API key in Bambusy

**ntfy:**
1. Choose a unique topic name (e.g., `my-printer-alerts-xyz123`)
2. Subscribe to it on your phone using the ntfy app or web interface
3. Enter the topic name in Bambusy (server defaults to ntfy.sh)

**Pushover:**
1. Create an account at [pushover.net](https://pushover.net/)
2. Create an application to get an API token
3. Enter your user key and app token in Bambusy

**Telegram:**
1. Message @BotFather on Telegram to create a bot
2. Get your chat ID by messaging @userinfobot
3. Enter the bot token and chat ID in Bambusy

**Email:**
1. Configure your SMTP server settings
2. For Gmail, use an App Password (not your regular password)
3. Choose security mode: STARTTLS (port 587), SSL (port 465), or None (port 25)
4. Enable/disable authentication as needed

## Tech Stack

- **Backend**: Python / FastAPI
- **Frontend**: React / TypeScript / Tailwind CSS
- **Database**: SQLite
- **3D Viewer**: Three.js
- **Printer Communication**: MQTT (TLS) + FTPS

## Project Structure

```
bambusy/
├── backend/
│   └── app/
│       ├── api/routes/      # API endpoints
│       ├── core/            # Config, database
│       ├── models/          # SQLAlchemy models
│       ├── schemas/         # Pydantic schemas
│       └── services/        # Business logic (MQTT, FTP, etc.)
├── frontend/                # React application
├── static/                  # Built frontend + images
├── archive/                 # Stored 3MF files
└── bambusy.db              # SQLite database
```

## API Documentation

Once running, API documentation is available at:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## Troubleshooting

### Printer won't connect

1. **Check LAN Mode is enabled** on your printer (Settings > Network > LAN Mode)
2. **Verify the Access Code** - it changes when you toggle LAN Mode
3. **Check network connectivity** - printer and server must be on the same network
4. **Firewall issues** - ensure ports 8883 (MQTT) and 990 (FTPS) are not blocked
5. **Correct Serial Number** - find it in printer settings under Device Info

### "Connection refused" errors

- The printer may be in sleep mode - wake it up and try again
- Check if another application is connected (only one MQTT connection allowed)
- Restart the printer if issues persist

### 3MF files not archiving automatically

- Ensure the printer is connected (green status indicator)
- Check that the print completed successfully
- Look at the server logs for any error messages

### Frontend not loading

- Make sure you built the frontend: `cd frontend && npm run build`
- Check that the `/static` folder contains `index.html` and `/assets`
- Clear browser cache and hard refresh (Ctrl+Shift+R)

### Database errors

The SQLite database (`bambusy.db`) is created automatically. If you encounter issues:

```bash
# Backup and reset database
mv bambusy.db bambusy.db.backup
# Restart the application - a new database will be created
```

### View server logs

Bambusy writes logs to `bambutrack.log` in the application directory (rotating, max 5MB × 3 files).

```bash
# View live log file
tail -f bambutrack.log

# If running directly with verbose output
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --log-level debug

# If running as systemd service
sudo journalctl -u bambusy -f
```

### Smart plug not responding

1. **Check the IP address** - Make sure the plug is on the same network and the IP hasn't changed
2. **Test via browser** - Visit `http://<plug-ip>/cm?cmnd=Power` to test directly
3. **Check Tasmota web interface** - Access `http://<plug-ip>` to verify Tasmota is running
4. **Authentication** - If Tasmota has a password set, configure it in the plug settings
5. **Firewall** - Ensure port 80 is accessible between Bambusy server and the plug

### Auto power-off not working

1. **Check plug is linked** - The plug must be linked to a printer for automation
2. **Verify automation is enabled** - Check the Enabled, Auto On, and Auto Off toggles
3. **Temperature mode issues** - If using temperature mode, ensure the printer is still connected so Bambusy can read the nozzle temperature

### Scheduled print not starting

1. **Check printer connection** - The printer must be able to connect after power-on
2. **Verify smart plug** - If using auto power-on, ensure the smart plug is configured and working
3. **Check queue status** - Look at the queue page for error messages
4. **Time zone issues** - Scheduled times are in your local time zone; ensure your system clock is correct
5. **View logs** - Check `bambutrack.log` for detailed error messages

### Print queue shows "Failed to start"

Common causes:
- **Printer not ready** - The printer needs to be idle and connected
- **File upload failed** - Check FTP connectivity to the printer
- **HMS errors** - Check the printer for any health system errors that prevent printing

### Timelapse not attaching automatically

**The Problem:**
When printers run in **LAN-only mode** (disconnected from Bambu Cloud), they cannot sync time via NTP. This causes the printer's internal clock to drift significantly (sometimes days or weeks off). Bambusy matches timelapses by comparing the print completion time with the timelapse file's modification time - when the printer's clock is wrong, this matching fails.

**Symptoms:**
- "Scan for Timelapse" shows "No matching timelapse found"
- Timelapse files exist on the printer but don't auto-attach
- Printer shows incorrect date/time in its settings

**Workaround - Manual Selection:**
When automatic matching fails, Bambusy now offers manual timelapse selection:

1. Right-click the archive and select **"Scan for Timelapse"**
2. If no match is found, a dialog appears showing all available timelapse files on the printer
3. Files are sorted by date (newest first) with size information
4. Select the correct timelapse and click to attach it

**Permanent Fix:**
To fix the printer's clock:
1. Temporarily connect the printer to the internet (via router or mobile hotspot)
2. Wait for the printer to sync time via NTP
3. Return to LAN-only mode - the clock should remain accurate until the next power cycle

**Note:** Some users report the clock resets after power cycling. In this case, you'll need to either:
- Periodically connect to the internet to sync time
- Use the manual timelapse selection feature

## Known Issues / Roadmap

### Beta Limitations
- [ ] Docker support not yet available
- [ ] No user authentication (single-user only)
- [ ] Limited to local network printers

### Planned Features
- [x] Timelapse video integration
- [x] Smart plug integration (Tasmota)
- [x] Print time accuracy tracking
- [x] Duplicate detection
- [x] HMS error monitoring
- [x] MQTT debug logging
- [x] Embedded project page editor
- [x] QR code labels
- [x] Energy monitoring and statistics
- [x] Print scheduling and queuing
- [x] Automatic finish photo capture
- [x] K-Profiles management (pressure advance)
- [x] Push notifications (WhatsApp, ntfy, Pushover, Telegram, Email)
- [x] Spoolman integration (filament inventory sync)
- [x] Maintenance tracker
- [x] Multi-language support (English, German)
- [x] Auto updates from GitHub releases
- [ ] Full printer control
- [ ] Mobile-optimized UI
- [ ] docs: readme -> wiki

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- [Bambu Lab](https://bambulab.com/) for making great printers
- The reverse engineering community for documenting the Bambu Lab protocol
- All contributors and testers

---

<p align="center">
  Made with ❤️ for the 3D printing community
</p>
