# Changelog

All notable changes to Bambuddy will be documented in this file.

## [0.1.6b4] - 2026-01-01

### Added
- **Docker update detection** - Automatically detects Docker installations and shows appropriate update instructions instead of failing with git errors
- **Camera popup window improvements**
  - Auto-resize to fit video resolution on first open
  - Persist window size and position to localStorage
  - Restore saved window state for subsequent opens
- **Slicer protocol handler** - OS detection for correct protocol (Windows: `bambustudio://`, macOS/Linux: `bambustudioopen://`)

### Fixed
- **Maintenance duration display** - Show weeks instead of imprecise months for better countdown precision
- **Print hours display** - Convert large hour values to readable units (e.g., 478h → 3w, 100h → 4d)
- **Printer hour counter** - Fixed runtime_seconds not incrementing during prints (first timestamp was set but never committed)

### Changed
- **Printer cards** - Refactored AMS section for better visual grouping and spacing

## [0.1.6b3] - 2025-12-31

### Added
- **Customizable Theme System** - Comprehensive theme customization with independent settings for dark and light modes:
  - **Style**: Classic (clean shadows), Glow (accent-colored glow effects), Vibrant (dramatic deep shadows)
  - **Background**: Neutral, Warm, Cool (light mode) + OLED, Slate, Forest (dark mode only)
  - **Accent Colors**: Green, Teal, Blue, Orange, Purple, Red
  - All combinations work together (e.g., Glow style + Forest background + Teal accent)
  - Settings sync across devices via database
  - Live preview in Settings → Appearance

### Fixed
- **Printer hour counter** - Fixed bug in printer's hour counter display

### Changed
- **Sidebar power switch** - Added confirmation modal to sidebar's quick power switch

## [0.1.6b2] - 2025-12-29

### Added
- **Virtual Printer** - Bambuddy now emulates a Bambu Lab printer on your network! Send prints directly from Bambu Studio or Orca Slicer without needing a physical printer connection. Features include:
  - SSDP discovery (printer appears automatically in slicer)
  - Secure TLS/MQTT communication with auto-generated certificates
  - Queue mode (prints go to pending uploads) or auto-start mode
  - Configurable access code for authentication
  - Works with Docker (requires `network_mode: host`)
  - Persistent certificates across container rebuilds via volume mount

### Fixed
- **Backup/restore for virtual printer settings** - Virtual printer settings (enabled, access code, mode) now correctly persist after restore without being overwritten by auto-save

## [0.1.6b] - 2025-12-28

### Added
- **Tasmota device discovery** - Automatically discover Tasmota smart plugs on your network. Click "Discover Tasmota Devices" in the Add Smart Plug modal to scan your local subnet. Supports devices with and without authentication.
- **Switchbar for quick smart plug access** - New sidebar widget for controlling smart plugs without leaving the current page. Enable "Show in Switchbar" for any plug to add it to the quick access panel. Shows real-time status, power consumption, and on/off controls.
- **Timelapse editor** - Edit timelapse videos with trim, speed adjustment (0.25x-4x), and music overlay. Uses FFmpeg for server-side processing with browser-based preview.
- **AMS filament preview** - Reprint modal shows filament comparison between what the print requires and what's currently loaded in the AMS. Compares both type and color with visual indicators (green=match, yellow=color mismatch, orange=type mismatch). Includes Re-read button to refresh AMS status from printer, fuzzy color matching for similar colors, and shows AMS slot location for each matched filament.
- **File type badge** - Archive cards now show GCODE (green) or SOURCE (orange) badge to indicate whether the file is a sliced print-ready file or source-only.
- **Docker printer discovery** - Subnet scanning for discovering printers when running in Docker with `network_mode: host`. Automatically detects Docker environment and shows subnet input field in Add Printer dialog.
- **Printer model mapping** - Discovery now shows friendly model names (X1C, H2D, P1S) instead of raw SSDP codes (BL-P001, O1D, C11).
- **Discovery API tests** - Comprehensive test coverage for discovery endpoints.
- **Project filament colors** - Project cards now display filament color swatches from assigned archives.
- **BOM filter** - Hide completed BOM items with "Hide done" toggle on project detail page.
- **Projects in backup/restore** - Projects, BOM items, and attachments now included in database backup/restore.
- **Attachment file validation** - File type validation for project attachments (images, documents, 3D files, archives, scripts, configs).

### Changed
- **Timelapse viewer** - Default playback speed changed from 2x to 1x.
- **GitHub issue template** - Added mandatory printer firmware version field and LAN-only mode checkbox for better bug reports.
- **Docker compose** - Clearer comments explaining `network_mode: host` requirement for printer discovery and camera streaming.
- **Project card design** - Enhanced visual polish with gradients, shadows, and glow effects on hover.
- **Project page layout** - Improved spacing and padding on project list and detail pages.
- **Delete confirmations** - Replaced browser confirm dialogs with styled confirmation modals.

### Fixed
- **Notification module** - Fixed bug where notifications were sent even when printer was offline.
- **Attachment uploads** - Fixed file attachments not persisting due to SQLAlchemy JSON column mutation detection.
- **Camera stream stability** - Fixed stream stopping after a few minutes by increasing ffmpeg read timeout (10s→30s), adding buffer options, and implementing auto-reconnection with exponential backoff in the frontend.

## [0.1.5] - 2025-12-19

### Fixed
- **Browser freeze on print completion** - Fixed freeze when camera stream was open during print completion by using buffered camera frames instead of spawning duplicate ffmpeg processes
- **Printer status "timelapse" effect** - Fixed issue where navigating to printer page after print showed metrics animating slowly from mid-print values to final state; printer_status messages now bypass the throttled queue
- **Timelapse auto-download** - Complete rewrite with retry mechanism and multiple path support
- **Timelapse detection for H2D** - H2D sends timelapse status in ipcam.timelapse field, not xcam.timelapse
- **Reprint from archive** - Fixed bug where print button sent slicer source file instead of sliced gcode
- **Import shadowing bugs** - Fixed ArchiveService import shadowing causing "cannot access local variable" error
- **Timelapse race condition** - xcam data was parsed before print state was set

### Added
- **Failure reason detection** - Auto-detects failure reasons from HMS errors:
  - Filament runout (Module 0x07)
  - Layer shift (Module 0x0C)
  - Clogged nozzle (Module 0x05)
- **Hide failed prints filter** - Toggle to hide failed/aborted prints with localStorage persistence
- **Docker test suite** - Comprehensive tests for build, backend, frontend, and integration
- **Pre-commit hooks** - Ruff linter and formatter for code quality
- **Code quality tests** - Static analysis to catch import shadowing bugs automatically

### Changed
- **Timelapse viewer** - Default playback speed changed from 0.5x to 2x
- **Archive badges** - Shows "cancelled" for aborted prints, "failed" for failed prints
- **WebSocket optimization** - Removed large raw_data field from print_complete message; reduced throttle to 100ms for smoother updates

### Docker
- Added ffmpeg to Docker image
- Fixed build warnings (debconf, pip root user)
- Added comprehensive Docker documentation to README
- Added `--pull` flag to ensure fresh base images

## [0.1.5b6] - 2025-12-12

  Notifications:
  - Separate AMS and AMS-HT notification switches (one per device type)
  - Fix notification variables not showing (duration, filament, estimated_time)
  - Add fallback values for empty notification variables ("Unknown" instead of blank)

  Settings:
  - Fix API keys badge count only showing after visiting tab
  - Move External Links card to third column above Updates
  - Add Release Notes modal for viewing full notes before updating

  Statistics:
  - Fix filament usage trends not showing (wrong API parameters)
  - Move dashboard controls (Hidden, Reset Layout) to header row

  Camera:
  - Fix ffmpeg processes not killed when closing webcam window
  - Add /camera/stop endpoint with POST support for sendBeacon
  - Track active streams and proper cleanup on disconnect

  Documentation:
  - Update README with missing features (camera streaming, AMS/AMS-HT monitoring,
    chamber control, printer control, AI detection, calibration, energy tracking,
    database backup/restore, system info dashboard)

## [0.1.5b5] - 2025-12-11

### Added
- Anonymous telemetry system with opt-out support
- System info page with database and resource statistics

## [0.1.5b4] - 2025-12-11

New Features

    Mobile PWA Support - Progressive Web App support for mobile devices
    AMS Humidity/Temperature History - Clickable indicators open charts with 6h/24h/48h/7d history, min/max/avg statistics, and threshold reference lines
    Webhooks & API Keys - API key authentication with granular permissions for external integrations
    System Info Page - New page showing system information
    Multi-plate Cover Image - Archive cards now show cover image of the printed plate for multi-plate files
    Quick Notification Disable - Button to quickly disable notifications
    Projects / Print Grouping - Group related prints into projects with progress tracking
    Full-Text Search (FTS5) - Efficient search across print names, filenames, tags, notes, designer, and filament type
    Failure Analysis - Dashboard widget showing failure rate with correlations and trends
    Archive Comparison - Compare 2-5 archives side-by-side with highlighted differences
    CSV/Excel Export - Export archives and statistics with current filters

Improvements

    Improved archive card context menu with submenu support
    Improved notification scheduler and templates
    Improved auto power off scheduler
    Improved email notification provider
    Configurable AMS data retention (default 30 days)

Bug Fixes

    Fixed bug where not all AMS spools were synced to Spoolman
    Fixed bug where external links were not respected by hotkeys
    Fixed context menu submenu not showing
    Fixed project card thumbnails using correct API endpoint
    Fixed archive PATCH 500 error (FTS5 index rebuild)
    Fixed clipboard API fallback for HTTP contexts

Infrastructure

    Added comprehensive automated testing (pytest, vitest, playwright)
    GitHub Actions CI/CD workflow for automated testing
    Removed PWA push notifications

## [0.1.5b4] - 2025-12-10

### Added
- Docker support with containerized deployment
- Comprehensive mobile support with responsive navigation
  - Hamburger drawer navigation for mobile (< 768px)
  - Touch gesture context menus with long press support
  - WCAG-compliant touch targets (44px minimum)
  - Safe area insets support for notched devices
- External links can be embedded into sidebar navigation
- External links included in backup/restore module
- Filament spool fill levels on printer cards
- Issue and pull request templates

### Changed
- Improved external link module with better icon layout
- Documentation moved to separate repository

### Fixed
- Notification module now properly saves newly added notification types
- External link icons layout improvements

## [0.1.5b3] - 2025-12-09

### Added
- Comprehensive backup/restore module improvements

### Fixed
- Switched off printers no longer incorrectly show as active
- os.path issue in update module

## [0.1.5b2] - 2025-12-09

### Added
- User options to backup module

### Changed
- App renamed to "Bambuddy"

### Fixed
- HTTP 500 error in backup module

## [0.1.5b] - 2025-12-08

### Added
- Smart plug monitoring and scheduling
- Daily digest notifications
- Notification template system
- Maintenance interval type: calendar days
- Cloud Profiles template visibility and preset diff view
- AMS humidity/temperature indicators with configurable thresholds
- Printer image on printer card
- WiFi signal strength indicator on printer card
- Power switch dropdown for offline printers
- MQTT debug viewer with filter and search
- Total printer hours display on printer card
- AMS discovery module
- Dual-nozzle AMS wiring visualization

### Changed
- Redesigned AMS section with BambuStudio-style device icons
- Tabbed design and auto-save for settings page
- Replaced camera settings with WiFi signal in top bar
- Completely refactored K-profile module
- Refactored maintenance settings

### Fixed
- HMS module bug
- Camera buttons appearance in light theme

### Removed
- Control page (removed all related code)

## [0.1.4] - 2025-12-01

### Added
- Multi-language support
- Auto app update functionality
- Maintenance module with notifications
- Spoolman support for adding unknown Bambu Lab spools
- Source 3MF file upload to archive cards

### Fixed
- K profiles retrieval from printer

## [0.1.3] - 2025-11-30

### Added
- Push notification support (WhatsApp, ntfy, Pushover, Telegram, Email)
- K profile management
- Configurable logging with log levels
- Sidebar item reordering
- Default view settings
- Option to track energy per print or in total
- Timelapse viewer improvements

### Fixed
- WebSocket connection stability
- Power stage updates not reflecting in frontend

## [0.1.2-bugfix] - 2025-11-30

### Fixed
- WebSocket disconnection issues

## [0.1.2-final] - 2025-11-29

### Added
- Print scheduling and queueing system
- Power consumption cost calculation
- HMS (Health Management System) error handling on printer cards
- Camera snapshot on print completion
- Power switch and automation controls on printer card
- Timelapse video player with speed controls
- Print time accuracy calculation
- Duplicate detection and filtering

### Changed
- Unified printer card layout

### Fixed
- Auto poweroff feature improvements
- Archive file handling on print start
- Statistics display issues

## [0.1.2] - 2025-11-28

### Added
- HMS health status monitoring
- MQTT debug log window
- Tasmota smart power plug support with automation
- Project page viewer and editor
- Button to show/hide disconnected printers
- Favicons

### Fixed
- Collapsed sidebar layout

## [0.1.1] - 2025-11-28

### Added
- Initial public release
- Multi-printer support via MQTT
- Real-time printer status monitoring
- Print archives with history tracking
- Statistics and analytics dashboard
- Timelapse video support
- Light and dark theme support

---

For more information, visit the [Bambuddy GitHub repository](https://github.com/maziggy/bambuddy).
