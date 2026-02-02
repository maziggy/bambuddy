# Changelog

All notable changes to Bambuddy will be documented in this file.

## [0.1.7b] - Not released

### Enhancements
- **Location Filter for Queue** (Issue #220):
  - Filter queue jobs by printer location in the Queue page
  - "Any {Model}" queue assignments can now specify a target location (e.g., "Any X1C in Workshop")
  - Location filter dropdown shows all unique locations from printers and queue items
  - Location is saved with queue items and displayed in the queue list
- **Ownership-Based Permissions** (Issue #205):
  - Users can now only update/delete their own items unless they have elevated permissions
  - Update/delete permissions split into `*_own` and `*_all` variants:
    - `queue:update_own` / `queue:update_all`
    - `queue:delete_own` / `queue:delete_all`
    - `archives:update_own` / `archives:update_all`
    - `archives:delete_own` / `archives:delete_all`
    - `archives:reprint_own` / `archives:reprint_all`
    - `library:update_own` / `library:update_all`
    - `library:delete_own` / `library:delete_all`
  - Administrators group gets `*_all` permissions (can modify any items)
  - Operators group gets `*_own` permissions (can only modify their own items)
  - Ownerless items (legacy data without creator) require `*_all` permission
  - Bulk operations skip items user doesn't have permission to modify
  - User deletion now offers choice: delete user's items or keep them (become ownerless)
  - Backend enforces permissions on all API endpoints (not just frontend UI)
  - Automatic migration upgrades existing groups to new permission model
- **User Tracking for Archives, Library & Queue** (Issue #206):
  - Track and display who uploaded each archive file
  - Track and display who uploaded each library file (File Manager)
  - Track and display who added each print job to the queue
  - Shows username on archive cards, library files, queue items, and printer cards (while printing)
  - Works when authentication is enabled; gracefully hidden when auth is disabled
  - Database migration adds `created_by_id` columns to `print_archives`, `library_files`, and `print_queue` tables
- **Separate AMS RFID Permission** (Issue #204):
  - Added new `printers:ams_rfid` permission for re-reading AMS RFID tags
  - Allows granting RFID re-read access without full printer control permissions
  - Operators group includes this permission by default
  - Available in Settings > Users > Group Editor as a toggleable permission
- **Schedule Button on Archive Cards** (Issue #208):
  - Added "Schedule" button next to "Reprint" on archive cards for quick access to print scheduling
  - Previously only available in the context menu (right-click)
  - Respects `queue:create` permission for users with restricted access
- **Streaming Overlay Improvements** (Issue #164):
  - **Configurable FPS**: Add `?fps=30` parameter to control camera frame rate (1-30, default 15)
  - **Status-only mode**: Add `?camera=false` parameter to hide camera and show only status overlay on black background
  - Increased default camera FPS from 10 to 15 for smoother video across all camera views
- **Simplified Backup/Restore System**:
  - Complete backup now creates a single ZIP file containing the entire database and all data directories
  - Includes: database, archives, library files, thumbnails, timelapses, icons, projects, and plate calibration data
  - Portable backups: works across different installations and data directories
  - Faster backup/restore: direct file copy instead of JSON export/import
  - Progress indicator and navigation blocking during backup/restore operations
  - Removed ~2000 lines of legacy JSON-based backup/restore code

### Fixes
- **JWT secret key not persistent across restarts** - Fixed JWT secret key generation to properly use data directory, ensuring tokens remain valid across container restarts
- **Images/thumbnails returning 401 when auth enabled** - Fixed auth middleware to allow public access to image/media endpoints (thumbnails, photos, QR codes, timelapses, camera streams) since browser elements like `<img>` don't send Authorization headers
- **Library thumbnails missing after restore** - Fixed library files using absolute paths that break after restore on different systems:
  - Library now stores relative paths in database for portability
  - Automatic migration converts existing absolute paths to relative on startup
  - Thumbnails and files now display correctly after restoring backups
- **File uploads failing with authentication enabled** - Fixed all file upload functions (archives, photos, timelapses, library files, etc.) not sending authentication headers when auth is enabled

## [0.1.6-final] - 2026-01-31

### New Features
- **Group-Based Permissions** - Granular access control with user groups:
  - Create custom groups with specific permissions (50+ granular permissions)
  - Default system groups: Administrators (full access), Operators (control printers), Viewers (read-only)
  - Users can belong to multiple groups with additive permissions
  - Permission-based UI: buttons/features disabled when user lacks permission
  - Groups management page in Settings → Users → Groups tab
  - Change password: users can change their own password from sidebar
  - Included in backup/restore
- **STL Thumbnail Generation** - Auto-generate preview thumbnails for STL files (Issue #156):
  - Checkbox option when uploading STL files to generate thumbnails automatically
  - Batch generate thumbnails for existing STL files via "Generate Thumbnails" button
  - Individual file thumbnail generation via context menu (three-dot menu)
  - Works with ZIP extraction (generates thumbnails for all STL files in archive)
  - Uses trimesh and matplotlib for 3D rendering with Bambu green color theme
  - Thumbnails auto-refresh in UI after generation
  - Graceful handling of complex/invalid STL files
- **Streaming Overlay for OBS** - Embeddable overlay page for live streaming with camera and print status (Issue #164):
  - All-in-one page at `/overlay/:printerId` combining camera feed with status overlay
  - Real-time print progress, ETA, layer count, and filename display
  - Bambuddy logo branding (links to GitHub)
  - Customizable via query parameters: `?size=small|medium|large` and `?show=progress,layers,eta,filename,status,printer`
  - No authentication required - designed for OBS browser source embedding
  - Gradient overlay at bottom for readable text over camera feed
  - Auto-reconnect on camera stream errors
- **MQTT Smart Plug Support** - Add smart plugs that subscribe to MQTT topics for energy monitoring (Issue #173):
  - New "MQTT" plug type alongside Tasmota and Home Assistant
  - Subscribe to any MQTT topic (Zigbee2MQTT, Shelly, Tasmota discovery, etc.)
  - **Separate topics per data type**: Configure different MQTT topics for power, energy, and state
  - Configurable JSON paths for data extraction (e.g., `power_l1`, `data.power`)
  - **Separate multipliers**: Individual multiplier for power and energy (e.g., mW→W, Wh→kWh)
  - **Custom ON value**: Configure what value means "ON" for state (e.g., "ON", "true", "1")
  - Monitor-only: displays power/energy data without control capabilities
  - Reuses existing MQTT broker settings from Settings → Network
  - Energy data included in statistics and per-print tracking
  - Full backup/restore support for MQTT plug configurations
- **Disable Printer Firmware Checks** - New toggle in Settings → General → Updates to disable printer firmware update checks:
  - Prevents Bambuddy from checking Bambu Lab servers for firmware updates
  - Useful for users who prefer to manage firmware manually or have network restrictions
- **Archive Plate Browsing** - Browse plate thumbnails directly in archive cards (Issue #166):
  - Hover over archive card to reveal plate navigation for multi-plate files
  - Left/right arrows to cycle through plate thumbnails
  - Dot indicators show current plate (clickable to jump to specific plate)
  - Lazy-loads plate data only when user hovers
- **GitHub Profile Backup** - Automatically backup your Cloud profiles, K-profiles and settings to a GitHub repository:
  - Configure GitHub repository URL and Personal Access Token
  - Schedule backups hourly, daily, or weekly
  - Manual on-demand backup trigger
  - Backs up K-profiles (per-printer), cloud profiles, and app settings
  - Skip unchanged commits (only creates commit when data changes)
  - Real-time progress tracking during backup
  - Backup history log with status and commit links
  - Requires Bambu Cloud login for full profile access
  - New Settings → Backup & Restore tab (local backup/restore moved here)
  - Included in local backup/restore (except PAT for security)
- **Plate Not Empty Notification** - Dedicated notification category for build plate detection:
  - New toggle in notification provider settings (enabled by default)
  - Sends immediately (bypasses quiet hours and digest mode)
  - Separate from general printer errors for granular control
- **USB Camera Support** - Connect USB webcams directly to your Bambuddy host:
  - New "USB Camera (V4L2)" option in external camera settings
  - Auto-detection of available USB cameras via V4L2
  - API endpoint to list connected USB cameras (`GET /api/v1/printers/usb-cameras`)
  - Works with any V4L2-compatible camera on Linux
  - Uses ffmpeg for frame capture and streaming
- **Build Plate Empty Detection** - Automatically detect if objects are on the build plate before printing:
  - Per-printer toggle to enable/disable plate detection
  - Multi-reference calibration: Store up to 5 reference images of empty plates (different plate types)
  - Automatic print pause when objects detected on plate at print start
  - Push notification and WebSocket alert when print is paused due to plate detection
  - ROI (Region of Interest) calibration UI with sliders to focus detection on build plate area
  - Reference management: View thumbnails, add labels, delete references
  - Works with both built-in and external cameras
  - Uses buffered camera frames when stream is active (no blocking)
  - Split button UI: Main button toggles detection on/off, chevron opens calibration modal
  - Green visual indicator when plate detection is enabled
  - Included in backup/restore
- **Project Import/Export** - Export and import projects with full file support (Issue #152):
  - Export single project as ZIP (includes project settings, BOM, and all files from linked library folders)
  - Export all projects as JSON for metadata-only backup
  - Import from ZIP (with files) or JSON (metadata only)
  - Linked folders and files are automatically created on import
  - Useful for sharing complete project bundles or migrating between instances
- **BOM Item Editing** - Bill of Materials items are now fully editable:
  - Edit name, quantity, price, URL, and remarks after creation
  - Pencil icon on each BOM item to enter edit mode
- **Prometheus Metrics Endpoint** - Export printer telemetry for external monitoring systems (Issue #161):
  - Enable via Settings → Network → Prometheus Metrics
  - Endpoint: `GET /api/v1/metrics` (Prometheus text format)
  - Optional bearer token authentication for security
  - Printer metrics: connection status, state, temperatures (bed, nozzle, chamber), fans, WiFi signal
  - Print metrics: progress, remaining time, layer count
  - Statistics: total prints by status, filament used, print time
  - Queue metrics: pending and active jobs
  - System metrics: connected printers count
  - Labels include printer_id, printer_name, serial for filtering
  - Ready for Grafana dashboards
- **External Link for Archives** - Add custom external links to archives for non-MakerWorld sources (Issue #151):
  - Link archives to Printables, Thingiverse, or any other URL
  - Globe button opens external link when set, falls back to auto-detected MakerWorld URL
  - Edit via archive edit modal
  - Included in backup/restore
- **External Network Camera Support** - Add external cameras (MJPEG, RTSP, HTTP snapshot) to replace built-in printer cameras (Issue #143):
  - Configure per-printer external camera URL and type in Settings → Camera
  - Live streaming uses external camera when enabled
  - Finish photo capture uses external camera
  - Layer-based timelapse: captures frame on each layer change, stitches to MP4 on print completion
  - Test connection button to verify camera accessibility
- **Recalculate Costs Button** - New button on Dashboard to recalculate all archive costs using current filament prices (Issue #120)
- **Create Folder from ZIP** - New option in File Manager upload to automatically create a folder named after the ZIP file (Issue #121)
- **Multi-File Selection in Printer Files** - Printer card file browser now supports multiple file selection (Issue #144):
  - Checkbox selection for individual files
  - Select All / Deselect All buttons
  - Bulk download as ZIP when multiple files selected
  - Bulk delete for multiple files at once
- **Queue Bulk Edit** - Select and edit multiple queue items at once (Issue #159):
  - Checkbox selection for pending queue items
  - Select All / Deselect All in toolbar
  - Bulk edit: printer assignment, print options, queue options
  - Bulk cancel selected items
  - Tri-state toggles: unchanged / on / off for each setting

### Fixes
- **Multi-Plate Thumbnail in Queue** - Fixed queue items showing wrong thumbnail for multi-plate files (Issue #166):
  - Queue now displays the correct plate thumbnail based on selected plate
  - Previously always showed plate 1 thumbnail regardless of selection
- **A1/A1 Mini Shows Printing Instead of Idle** - Fixed incorrect status display for A1 series printers (Issue #168):
  - Some A1/A1 Mini firmware versions incorrectly report stage 0 ("Printing") when idle
  - Now checks gcode_state to correctly display "Idle" for affected printers
  - Fix only applies to A1 models with the specific buggy condition
- **HMS Error Notifications** - Get notified when printer errors occur (Issue #84):
  - Automatic notifications for HMS errors (AMS issues, nozzle problems, etc.)
  - Human-readable error messages (853 error codes translated)
  - Friendly error type names (Print/Task, AMS/Filament, Nozzle/Extruder, Motion Controller, Chamber)
  - Deduplication prevents spam from repeated error messages
  - Publishes to MQTT relay for home automation integrations
  - New "Printer Error" toggle in notification provider settings
- **Plate Calibration Persistence** - Fixed plate detection reference images not persisting after restart in Docker deployments
- **Telegram Notification Parsing** - Fixed Telegram markdown parsing errors when messages contain underscores (e.g., error codes)
- **Settings API PATCH Method** - Added PATCH support to `/api/settings` for Home Assistant rest_command compatibility (Issue #152)
- **P2S Empty Archive Tiles** - Fixed FTP file search for printers without SD card (Issue #146):
  - Added root folder `/` to search paths when looking for 3MF files
  - Printers without SD card store files in root instead of `/cache`
- **Empty AMS Slot Not Recognized** - Fixed bug where removed spools still appeared in Bambuddy (Issue #147):
  - Old AMS: Now properly applies empty values from tray data updates
  - New AMS (AMS 2 Pro): Now checks `tray_exist_bits` bitmask to detect and clear empty slots
- **Reprint Cost Tracking** - Reprinting an archive now adds the cost to the existing total, so statistics accurately reflect total filament expenditure across all prints
- **HA Energy Sensors Not Detected** - Home Assistant energy sensors with lowercase units (w, kwh) are now properly detected; unit matching is now case-insensitive (Issue #119)
- **File Manager Upload** - Upload modal now accepts all file types, not just ZIP files
- **Camera Zoom & Pan Improvements** - Enhanced camera viewer zoom/pan functionality (Issue #132):
  - Pan range now based on actual container size, allowing full navigation of zoomed image
  - Added pinch-to-zoom support for mobile/touch devices
  - Added touch-based panning when zoomed in
  - Both embedded camera viewer and standalone camera page updated
- **Progress Milestone Time** - Fixed milestone notifications showing wrong time (e.g., "17m" instead of "17h 47m") by converting remaining_time from minutes to seconds (Issue #157)
- **File Manager Folder Navigation** - Improved handling of long folder names (Issue #160):
  - Resizable sidebar: Drag the edge to adjust width (200-500px), double-click to reset
  - Text wrap toggle: "Wrap" button in header to wrap long names instead of truncating
  - Both settings persist in localStorage
  - Tooltip shows full name on hover
- **K-Profiles Backup Status** - Fixed GitHub backup settings showing incorrect printer connection count (e.g., "1/2 connected" when both printers are connected); now fetches status from API instead of relying on WebSocket cache
- **GitHub Backup Timestamps** - Removed volatile timestamps from GitHub backup files so git diffs only show actual data changes
- **Model-Based Queue AMS Mapping** - Fixed "Any [Model]" queue jobs failing at filament loading on H2D Pro and other printers (Issue #192):
  - Scheduler now computes AMS mapping after printer assignment for model-based jobs
  - Previously, no AMS mapping was sent because the specific printer wasn't known at queue time
  - Auto-matches required filaments to available AMS slots by type and color

### Maintenance
- Upgraded vitest from 2.x to 3.x to resolve npm audit security vulnerabilities in dev dependencies

## [0.1.6b11] - 2026-01-22

### New Features
- **Camera Zoom & Fullscreen** - Enhanced camera viewer controls:
  - Fullscreen mode for embedded camera viewer (new button in header)
  - Zoom controls (100%-400%) for both embedded and window modes
  - Pan support when zoomed in (click and drag)
  - Mouse wheel zoom support
  - Zoom resets on mode switch, refresh, or fullscreen toggle
- **Searchable HA Entity Selection** - Improved Home Assistant smart plug configuration:
  - Entity dropdown replaced with searchable combobox
  - Type to search across all HA entities (not just switch/light/input_boolean)
  - Energy sensor dropdowns (Power, Energy Today, Total) are now searchable
  - Find sensors with non-standard naming that don't match the switch entity name
- **Home Assistant Energy Sensor Support** - HA smart plugs can now use separate sensor entities for energy monitoring:
  - Configure dedicated power sensor (W), today's energy (kWh), and total energy (kWh) sensors
  - Supports plugs where energy data is exposed as separate sensor entities (common with Tapo, IKEA Zigbee2mqtt, etc.)
  - Energy sensors are selectable from all available HA sensors with power/energy units
  - Falls back to switch entity attributes if no sensors configured
  - Print energy tracking now works correctly for HA plugs (not just Tasmota)
  - New API endpoint: `GET /api/v1/smart-plugs/ha/sensors` to list available energy sensors
- **Finish Photo in Notifications** - Camera snapshot URL available in notification templates (Issue #126):
  - New `{finish_photo_url}` template variable for print_complete, print_failed, print_stopped events
  - Photo is captured before notification is sent (ensures image is available)
  - New "External URL" setting in Settings → Network (auto-detects from browser)
  - Full URL constructed for external notification services (Telegram, Email, Discord, etc.)
- **ZIP File Support in File Manager** - Upload and extract ZIP files directly in the library (Issue #121):
  - Drop or select ZIP files to automatically extract contents
  - Option to preserve folder structure from ZIP or extract flat
  - Extracts thumbnails and metadata from 3MF/gcode files inside ZIP
  - Progress indicator shows number of files extracted

### Fixed
- **Print time stats using slicer estimates** - Quick Stats "Print Time" now uses actual elapsed time (`completed_at - started_at`) instead of slicer estimates; cancelled prints only count time actually printed (Issue #137)
- **Skip objects modal overflow** - Modal now has max height (85vh) with scrollable object list when printing many items on the bed (Issue #134)
- **Filament cost using wrong default** - Statistics now correctly uses the "Default filament cost (per kg)" setting instead of hardcoded €25 value (Issue #120)
- **Spoolman tag field not auto-created** - The required "tag" extra field is now automatically created in Spoolman on first connect, fixing sync failures for fresh Spoolman installs (Issue #123)
- **P2S/X1E/H2 completion photo not captured** - Internal model codes (N7, C13, O1D, etc.) from MQTT/SSDP are now recognized for RTSP camera support (Issue #127)
- **Mattermost/Slack webhook 400 error** - Added "Slack / Mattermost" payload format option that sends `{"text": "..."}` instead of custom fields (Issue #133)
- **Subnet scan serial number** - Fixed A1 Mini subnet discovery showing "unknown-*" placeholder; serial field is now cleared so users know to enter it manually (Issue #140)

## [0.1.6b10] - 2026-01-21

### New Features
- **Unified Print Modal** - Consolidated three separate modals into one unified component:
  - Single modal handles reprint, add-to-queue, and edit-queue-item operations
  - Consistent UI/UX across all print operations
  - Reduced code duplication (~1300 LOC removed)
- **Multi-Printer Selection** - Send prints or queue items to multiple printers at once:
  - Checkbox selection for multiple printers in reprint and add-to-queue modes
  - "Select all" / "Clear" buttons for quick selection
  - Progress indicator during multi-printer submission
  - Ideal for print farms with identical filament configurations
- **Per-Printer AMS Mapping** - Configure filament slot mapping individually for each printer:
  - Enable "Custom mapping" checkbox under each selected printer
  - Auto-configure uses RFID data to match filaments automatically
  - Manual override for specific slot assignments
  - Match status indicator shows exact/partial/missing matches
  - Re-read button to refresh printer's loaded filaments
  - New setting in Settings → Filament to expand custom mapping by default
- **Enhanced Add-to-Queue** - Now includes plate selection and print options:
  - Configure all print settings upfront instead of editing afterward
  - Filament mapping with manual override capability
- **Print from File Manager** - Full print configuration when printing from library files:
  - Plate selection for multi-plate 3MF files with thumbnails
  - Filament slot mapping with comparison to loaded filaments
  - All print options (bed levelling, flow calibration, etc.)
- **File Manager Print Button** - Print directly from multi-selection toolbar:
  - "Print" button appears when exactly one sliced file is selected
  - Opens full PrintModal with plate selection and print options
  - "Add to Queue" button now uses Clock icon for clarity
- **Multiple Embedded Camera Viewers** - Open camera streams for multiple printers simultaneously in embedded mode:
  - Each viewer has its own remembered position and size
  - New viewers are automatically offset to prevent stacking
  - Printer-specific persistence in localStorage
  - **Navigation persistence** - Open cameras stay open when navigating away and back to Printers page
- **Application Log Viewer** - View and filter application logs in real-time from System Information page:
  - Start/Stop live streaming with 2-second auto-refresh
  - Filter by log level (DEBUG, INFO, WARNING, ERROR)
  - Text search across messages and logger names
  - Clear logs with one click
  - Expandable multi-line log entries (stack traces, etc.)
  - Auto-scroll to follow new entries
- **Deferred archive creation** - Queue items from File Manager no longer create archives upfront:
  - Queue items store `library_file_id` directly
  - Archives are created automatically when prints start
  - Reduces clutter in Archives from unprinted queued files
  - Queue displays library file name, thumbnail, and print time
- **Expandable Color Picker** - Configure AMS Slot modal now has an expandable color palette:
  - 8 basic colors shown by default (White, Black, Red, Blue, Green, Yellow, Orange, Gray)
  - Click "+" to expand 24 additional colors (Cyan, Magenta, Purple, Pink, Brown, Beige, Navy, Teal, Lime, Gold, Silver, Maroon, Olive, Coral, Salmon, Turquoise, Violet, Indigo, Chocolate, Tan, Slate, Charcoal, Ivory, Cream)
  - Click "-" to collapse back to basic colors
- **File Manager Sorting** - Printer file manager now has sorting options:
  - Sort by name (A-Z or Z-A)
  - Sort by size (smallest or largest first)
  - Sort by date (oldest or newest first)
  - Directories always sorted first
- **Camera View Mode Setting** - Choose how camera streams open:
  - "New Window" (default): Opens camera in a separate browser window
  - "Embedded": Shows camera as a floating overlay on the main screen
  - Embedded viewer is draggable and resizable with persistent position/size
  - Configure in Settings → General → Camera section
- **File Manager Rename** - Rename files and folders directly in File Manager:
  - Right-click context menu "Rename" option for files and folders
  - Inline rename button in list view
  - Validates filenames (no path separators allowed)
- **File Manager Mobile Accessibility** - Improved touch device support:
  - Three-dot menu button always visible on mobile (hover-only on desktop)
  - Selection checkbox always visible on mobile devices
  - Better PWA experience for file management
- **Optional Authentication** - Secure your Bambuddy instance with user authentication:
  - Enable/disable authentication via Setup page or Settings → Users
  - Role-based access control: Admin and User roles
  - Admins have full access; Users can manage prints but not settings
  - JWT-based authentication with 7-day token expiration
  - User management page for creating, editing, and deleting users
  - Backward compatible: existing installations work without authentication
  - Settings page restricted to admin users when auth is enabled

### Changed
- **Edit Queue Item modal** - Single printer selection only (reassigns item, doesn't duplicate)
- **Edit Queue Item button** - Changed from "Print to X Printers" to "Save"

### Fixed
- **File Manager folder navigation** - Fixed bug where opening a folder would briefly show files then jump back to root:
  - Removed `selectedFolderId` from useEffect dependency array that was causing a reset loop
  - Folder navigation now works correctly without resetting
- **Queue items with library files** - Fixed 500 errors when listing/updating queue items from File Manager
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
