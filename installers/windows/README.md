# Bambuddy Windows Installer

Builds a self-contained Windows installer (`.exe`) for Bambuddy: embedded
Python 3.13 distribution + pre-built frontend + NSSM-supervised Windows
service. No Python or Node installation required on the target machine.

## Architecture

- **Install target:** `C:\Program Files\Bambuddy\`
- **Data target:** `C:\ProgramData\Bambuddy\data\` (preserved on uninstall by default)
- **Logs target:** `C:\ProgramData\Bambuddy\logs\`
- **Service:** registered via NSSM, runs as `LocalSystem`, autostart on boot
- **Service command:** `python.exe -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --loop asyncio` (`--loop asyncio` avoids a uvloop TLS bug that can truncate VP FTP uploads, #1896)
- **Bundled binaries:** Python 3.13 embeddable, NSSM, ffmpeg static build

Browser is the UI. Start Menu shortcut opens `http://localhost:8000`.

## Why these choices

See `memory/windows-installer-decision.md` for the full reasoning. Short
version: PowerShell install scripts can't survive environmental drift
across the Windows host fleet, so we ship a self-contained bundle that
depends on nothing on the host. Inno Setup + embedded Python is the
lowest-maintenance path that delivers native-app UX. No Tauri/Electron
launcher in v1 — browser-as-UI matches every other Bambuddy platform.

## Build prerequisites

The build runs on Windows (or in a Windows GitHub Actions runner). Cross-
building from Linux is possible via Wine but not officially supported.

- Windows 10/11 x64 (or `windows-latest` GitHub Actions runner)
- Python 3.11+ (for running `build.py`; the embedded Python that ships
  in the installer is downloaded fresh by the build script)
- Node.js 22 LTS + npm (for building the frontend bundle)
- [Inno Setup 6](https://jrsoftware.org/isdl.php) (for compiling
  `bambuddy.iss` → `.exe`)

The build script downloads everything else automatically (embedded Python,
NSSM, ffmpeg).

## Build steps

```cmd
:: From the repo root on a Windows machine
cd installers\windows
python build.py
:: Then open bambuddy.iss in Inno Setup Compiler and click Build → Compile
:: (or invoke ISCC.exe directly:)
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" bambuddy.iss
```

Output: `installers\windows\build\output\bambuddy-windows-setup.exe`

## Testing without signing

The installer can be built and run unsigned. Windows SmartScreen will
show "Windows protected your PC" on first run. Click **More info** →
**Run anyway** to proceed. This is expected and harmless for testing.
Production builds will be signed via SignPath OSS (application in
flight as of 2026-06-10) and won't show this warning after reputation
accrues.

## CI build

See `.github/workflows/windows-installer.yml` for the automated build.
The workflow runs on every tag matching `v*` and uploads the installer
as a release asset.

## Known limitations / open questions

- **VP feature on Windows:** the Virtual Printer needs to bind 322/990/8883
  (privileged ports). Service runs as LocalSystem which can bind these
  ports, but the user's Windows Firewall will prompt on first VP enable.
  Documenting this is TBD.
- **Spoolman:** explicitly NOT bundled in v1. Users who want Spoolman
  install it separately. Bambuddy internal-inventory mode is the default
  on Windows.
- **Bundle size:** estimated 250–350MB installed (mostly opencv +
  ffmpeg + matplotlib). Acceptable for a v1; can investigate slimming
  later if users complain.
- **Updates:** v1 ships as a fresh install / uninstall + install cycle.
  In-place upgrade via the same installer is supported by Inno Setup but
  needs end-to-end testing before we promise it.
