# Changelog

All notable changes to Bambuddy will be documented in this file.

## [0.1.6b10] - 2026-01-20

### New Features
- **Expandable Color Picker** - Configure AMS Slot modal now has an expandable color palette:
  - 8 basic colors shown by default (White, Black, Red, Blue, Green, Yellow, Orange, Gray)
  - Click "+" to expand 24 additional colors (Cyan, Magenta, Purple, Pink, Brown, Beige, Navy, Teal, Lime, Gold, Silver, Maroon, Olive, Coral, Salmon, Turquoise, Violet, Indigo, Chocolate, Tan, Slate, Charcoal, Ivory, Cream)
  - Click "-" to collapse back to basic colors
- **File Manager Sorting** - Printer file manager now has sorting options:
  - Sort by name (A-Z or Z-A)
  - Sort by size (smallest or largest first)
  - Sort by date (oldest or newest first)
  - Directories always sorted first

### Fixed
- **User preset AMS configuration** - Fixed user presets (inheriting from Bambu presets) showing empty fields in Bambu Studio after configuration:
  - Now correctly derives `tray_info_idx` from the preset's `base_id` when `filament_id` is null
  - User presets that inherit from Bambu presets (e.g., "# Overture Matte PLA @BBL H2D") now work correctly
- **Faster AMS slot updates** - Frontend now updates immediately after configuring AMS slots:
  - Added WebSocket broadcast to AMS change callback for instant UI updates
  - Removed unnecessary delayed refetch that was causing slow updates

## [0.1.6b9] - 2026-01-19

### New Features
- **Add to Queue from File Manager** - Queue sliced files directly from File Manager:
  - New "Add to Queue" toolbar button appears when sliced files are selected
  - Context menu and list view button options for individual files
  - Supports multiple file selection for batch queueing
  - Only accepts sliced files (.gcode or .gcode.3mf)
  - Creates archive and queue item in one action
- **Print Queue plate selection and options** - Full print configuration in queue edit modal:
  - Plate selection grid with thumbnails for multi-plate 3MF files
  - Print options section (bed levelling, flow calibration, vibration calibration, layer inspect, timelapse, use AMS)
  - Options saved with queue item and used when print starts
- **Multi-plate 3MF plate selection** - When reprinting multi-plate 3MF files (exported with "All sliced file"), users can now select which plate to print:
  - Plate selection grid with thumbnails, names, and print times
  - Filament requirements filtered to show only selected plate's filaments
  - Prevents incorrect filament mapping across plates
  - Closes [#93](https://github.com/maziggy/bambuddy/issues/93)
- **Home Assistant smart plug integration** - Control any Home Assistant switch/light entity as a smart plug:
  - Configure HA connection (URL + Long-Lived Access Token) in Settings → Network
  - Add HA-controlled plugs via Settings → Plugs → Add Smart Plug → Home Assistant tab
  - Entity dropdown shows all available switch/light/input_boolean entities
  - Full automation support: auto-on, auto-off, scheduling, power alerts
  - Works alongside existing Tasmota plugs
  - Closes [#91](https://github.com/maziggy/bambuddy/issues/91)
- **Fusion 360 design file attachments** - Attach F3D files to archives for complete design tracking:
  - Upload F3D files via archive context menu ("Upload F3D" / "Replace F3D")
  - Cyan badge on archive card indicates attached F3D file (next to source 3MF badge)
  - Click badge to download, or use "Download F3D" in context menu
  - F3D files included in backup/restore
  - API tests for F3D endpoints

### Fixed
- **Multi-plate 3MF metadata extraction** - Single-plate exports from multi-plate projects now show correct thumbnail and name:
  - Extracts plate index from slice_info.config metadata
  - Uses correct plate thumbnail (e.g., plate_5.png instead of plate_1.png)
  - Appends "Plate N" to print name for plates > 1
  - Closes [#92](https://github.com/maziggy/bambuddy/issues/92)

## [0.1.6b8] - 2026-01-17

### Added
- **MQTT Publishing** - Publish BamBuddy events to external MQTT brokers for integration with Home Assistant, Node-RED, and other automation platforms:
  - New "Network" tab in Settings for MQTT configuration
  - Configure broker, port, credentials, TLS, and topic prefix
  - Real-time connection status indicator
  - Topics: printer status, print lifecycle, AMS changes, queue events, maintenance alerts, smart plug states, archive events
- **Virtual Printer Queue Mode** - New mode that archives files and adds them directly to the print queue:
  - Three modes: Archive (immediate), Review (pending list), Queue (print queue)
  - Queue mode creates unassigned items that can be assigned to a printer later
- **Unassigned Queue Items** - Print queue now supports items without an assigned printer:
  - "Unassigned" filter option on Queue page
  - Unassigned items highlighted in orange
  - Assign printer via edit modal
- **Sidebar Badge Indicators** - Visual indicators on sidebar icons:
  - Queue icon: yellow badge with pending item count
  - Archive icon: blue badge with pending uploads count
  - Auto-updates every 5 seconds and on window focus
- **Project Parts Tracking** - Track individual parts/objects separately from print plates:
  - "Target Parts" field alongside "Target Plates"
  - Separate progress bars for plates vs parts
  - Parts count auto-detected from 3MF files

### Fixed
- Chamber temp on A1/P1S - Fixed regression where chamber temperature appeared on printers without sensors in multi-printer setups
- Queue prints on A1 - Fixed "MicroSD Card read/write exception error" when starting prints from queue
- Spoolman sync - Fixed Bambu Lab spool detection and AMS tray data persistence
- FTP downloads - Fixed downloads failing for .3mf files without .gcode extension
- Project statistics - Fixed inconsistent display between project list and detail views
- Chamber light state - Fixed WebSocket broadcasts not including light state changes
- Backup/restore - Improved handling of nullable fields and AMS mapping data

## [0.1.6b7] - 2026-01-12

### Added
- **AMS Color Mapping** - Manual AMS slot selection in ReprintModal, AddToQueueModal, EditQueueItemModal:
  - Dropdown to override auto-matched AMS slots with any loaded filament
  - Blue ring indicator distinguishes manual selections from auto-matches
  - Status indicators: green (match), yellow (type only), orange (not found)
  - Shared color utility with ~200 Bambu color mappings
  - Fixed AMS mapping format to match Bambu Studio exactly
- **Print Options in Reprint Modal** - Bed leveling, flow calibration, vibration calibration, first layer inspection, timelapse toggles
- **Time Format Setting** - New date utilities applied to 12 components, fixes archive times showing in UTC
- **Statistics Dashboard Improvements** - Size-aware rendering for PrintCalendar, SuccessRateWidget, TimeAccuracyWidget, FilamentTypesWidget, FailureAnalysisWidget
- **Firmware Update Helper** - Check firmware versions against Bambu Lab servers for LAN-only printers with one-click upload
- **FTP Reliability** - Configurable retry (1-10 attempts, 1-30s delay), A1/A1 Mini SSL fix, configurable timeout
- **Bulk Project Assignment** - Assign multiple archives to a project at once from multi-select toolbar
- **Chamber Light Control** - Light toggle button on printer cards
- **Support Bundle Feature** - Debug logging toggle with ZIP generation for issue reporting
- **Archive Improvements** - List view with full parity, object count display, cross-view highlighting, context menu button
- **Maintenance Improvements** - wiki_url field for documentation links, model-specific Bambu Lab wiki URLs
- **Spoolman Integration** - Clear location when spools removed from AMS during sync

### Fixed
- Browser freeze from CameraPage WebSocket
- Project card filament badges showing duplicates and raw color codes
- Print object label positioning in skip objects modal
- Printer hour counter not updated on backend restart
- Virtual printer excluded from discovery
- Print cover fetch in Docker environments
- Archive delete safety checks prevent deleting parent dirs

## [0.1.6b6] - 2026-01-04

### Added
- **Resizable Printer Cards** - Four sizes (S/M/L/XL) with +/- buttons in toolbar
- **Queue Only Mode** - Stage prints without auto-start, release when ready with purple "Staged" badge
- **Virtual Printer Model Selection** - Choose which Bambu printer model to emulate
- **Tasmota Admin Link** - Quick access to smart plug web interface with auto-login
- **Pending Upload Delete Confirmation** - Confirmation modal when discarding pending uploads

### Fixed
- Camera stream reconnection with automatic recovery from stalled streams
- Active AMS slot display for H2D printers with multiple AMS units
- Spoolman sync matching only Bambu Lab vendor filaments
- Skip objects modal object ID markers positioning
- Virtual printer model codes, serial prefixes, startup model, certificate persistence
- Archive card context menu positioning

## [0.1.6b5] - 2026-01-02

### Added
- **Pre-built Docker Images** - Pull directly from GitHub Container Registry (ghcr.io)
- **Printer Controls** - Stop and Pause/Resume buttons on printer cards with confirmation modals
- **Skip Objects** - Skip individual objects during print without canceling entire job
- **Spoolman Improvements** - Link Spool, UUID Display, Sync Feedback
- **AMS Slot RFID Re-read** - Re-read filament info via hover menu
- **Print Quantity Tracking** - Track items per print for project progress

### Fixed
- Spoolman 400 Bad Request when creating spools
- Update module for Docker based installations

## [0.1.6b4] - 2026-01-01

### Changed
- Refactored AMS section for better visual grouping and spacing

### Fixed
- Printer hour counter not incrementing during prints
- Slicer protocol OS detection (Windows: bambustudio://, macOS/Linux: bambustudioopen://)
- Camera popup window auto-resize and position persistence
- Maintenance page duration display with better precision
- Docker update detection for in-app updates

## [0.1.6b3] - 2025-12-31

### Added
- Confirmation modal for quick power switch in sidebar

### Fixed
- Printer hour counter inconsistency between card and maintenance page
- Improved printer hour tracking accuracy with real-time runtime counter
- Add Smart Plug modal scrolling on lower resolution screens
- Excluded virtual printer from discovery results
- Bottom sidebar layout

## [0.1.6b2] - 2025-12-29

### Added
- **Virtual Printer** - Emulates a Bambu Lab printer on your network:
  - Auto-discovery via SSDP protocol
  - Send prints directly from Bambu Studio/Orca Slicer
  - Queue mode or Auto-start mode
  - TLS 1.3 encrypted MQTT + FTPS with auto-generated certificates
- Persistent archive page filters

### Fixed
- AMS filament matching in reprint modal
- Archive card cache bug with wrong cover image
- Queueing module re-queue modal

## [0.1.6b] - 2025-12-28

### Added
- **Smart Plugs** - Tasmota device discovery and Switchbar quick access widget
- **Timelapse Editor** - Trim, speed adjustment (0.25x-4x), and music overlay
- **Printer Discovery** - Docker subnet scanning, printer model mapping, detailed status stages
- **Archives & Projects** - AMS filament preview, file type badges, project filament colors, BOM filter
- **Maintenance** - Custom maintenance types with manual per-printer assignment
- Delete printer options to keep or delete archives

### Fixed
- Notifications sent when printer offline
- Camera stream stopping with auto-reconnection
- A1/P1 camera streaming with extended timeouts
- Attachment uploads not persisting
- Total print hours calculation

## [0.1.5] - 2025-12-19

### Added
- **Docker Support** - One-command deployment with docker compose
- **Mobile PWA** - Full mobile support with responsive navigation and touch gestures
- **Projects** - Group related prints with progress tracking
- **Archive Comparison** - Compare 2-5 archives side-by-side
- **Smart Plug Automation** - Tasmota integration with auto power-on/off
- **Telemetry Dashboard** - Anonymous usage statistics (opt-out available)
- **Full-Text Search** - Efficient search across print names, filenames, tags, notes, designer, filament type
- **Failure Analysis** - Dashboard widget showing failure rate with correlations and trends
- **CSV/Excel Export** - Export archives and statistics with current filters
- **AMS Humidity/Temperature History** - Clickable indicators with charts and statistics
- **Daily Digest Notifications** - Consolidated daily summary
- **Notification Template System** - Customizable message templates
- **Webhooks & API Keys** - API key authentication with granular permissions
- **System Info Page** - Database and resource statistics
- **Comprehensive Backup/Restore** - Including user options and external links

### Changed
- Redesigned AMS section with BambuStudio-style device icons
- Tabbed design and auto-save for settings page
- Improved archive card context menu with submenu support
- WebSocket throttle reduced to 100ms for smoother updates

### Fixed
- Browser freeze on print completion when camera stream was open
- Printer status "timelapse" effect after print completion
- Complete rewrite of timelapse auto-download with retry mechanism
- Reprint from archive sending slicer source file instead of sliced gcode
- Import shadowing bugs causing "cannot access local variable" error
- Archive PATCH 500 error
- ffmpeg processes not killed when closing webcam window

### Removed
- Control page
- PWA push notifications (replaced with standard notification providers)
