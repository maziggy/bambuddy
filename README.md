# Bambusy

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

- **Multi-Printer Support** - Connect and monitor multiple Bambu Lab printers (X1, X1C, P1P, P1S, A1, A1 Mini)
- **Automatic Print Archiving** - Automatically saves 3MF files when prints complete
- **3D Model Preview** - Interactive Three.js viewer for archived prints
- **Real-time Monitoring** - Live printer status via WebSocket with print progress, temperatures, and more
- **Print Statistics Dashboard** - Customizable dashboard with drag-and-drop widgets
  - Print success rates
  - Filament usage trends
  - Print activity calendar
  - Cost tracking
- **Filament Cost Tracking** - Track costs per print with customizable filament database
- **Photo Attachments** - Attach photos to archived prints for documentation
- **Failure Analysis** - Document failed prints with notes and photos
- **Cloud Profiles Sync** - Access your Bambu Cloud slicer presets
- **File Manager** - Browse and manage files on your printer's SD card
- **Re-print** - Send archived prints back to any connected printer
- **Dark/Light Theme** - Easy on the eyes, day or night
- **Keyboard Shortcuts** - Quick navigation with keyboard shortcuts

## Screenshots

<!-- Add your screenshots here -->
<p align="center">
  <img src="docs/screenshots/dashboard.png" alt="Dashboard" width="800">
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
  <img src="docs/screenshots/3d-preview.png" alt="3D Preview" width="800">
  <br><em>Interactive 3D model preview</em>
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

```bash
# If running directly
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --log-level debug

# If running as systemd service
sudo journalctl -u bambusy -f
```

## Known Issues / Roadmap

### Beta Limitations
- [ ] Docker support not yet available
- [ ] No user authentication (single-user only)
- [ ] Limited to local network printers

### Planned Features
- [ ] Docker deployment
- [ ] Multi-user support with authentication
- [ ] Print queue management
- [ ] Timelapse video integration
- [ ] Mobile-responsive improvements
- [ ] Printer groups/organization

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
