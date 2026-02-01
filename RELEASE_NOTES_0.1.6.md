# Bambuddy v0.1.6 - Final Release

**Release Date:** January 31, 2026

After 11 beta releases and extensive community testing, we're excited to announce **Bambuddy 0.1.6-final** - our biggest release yet! This release brings optional authentication, build plate detection, external camera support, model-based queue scheduling, and much more.

---

## Highlights

### Optional Authentication & User Management
Secure your Bambuddy instance for the first time:
- Enable/disable authentication via Settings
- **Role-based access**: Admin (full access) and User (prints only) roles
- **Group-based permissions**: 50+ granular permissions with custom groups
- JWT-based authentication with user management UI
- Users can change their own password from the sidebar

### Build Plate Empty Detection
Never start a print with objects on the bed again:
- Per-printer toggle for plate detection
- Multi-reference calibration (up to 5 plate types)
- Automatic print pause when objects detected
- Push notifications and WebSocket alerts
- ROI calibration for precise detection area

### External & USB Camera Support
Use any camera with your printers:
- **External cameras**: MJPEG, RTSP, HTTP snapshot support
- **USB cameras**: V4L2 webcam support on Linux
- Layer-based timelapse with external cameras
- Finish photo capture from external sources

### Model-Based Queue Assignment
Perfect for print farms:
- Queue items to "Any X1C", "Any P1S", etc.
- Auto-assigns to available printer when ready
- Automatic filament validation and AMS mapping
- Matches required filaments to loaded spools

### GitHub Profile Backup
Automated backup of your settings to GitHub:
- Schedule hourly, daily, or weekly backups
- Backs up K-profiles, cloud profiles, and app settings
- Skip unchanged commits (only commit when data changes)
- Backup history log with commit links

### Prometheus Metrics
Export printer telemetry for external monitoring:
- Endpoint: `GET /api/v1/metrics`
- Printer temps, fans, WiFi, print progress
- Ready for Grafana dashboards
- Optional bearer token authentication

---

## New Features

### Authentication & Security
- **Optional Authentication** - Secure your Bambuddy instance with JWT-based user authentication
- **Group-Based Permissions** - 50+ granular permissions with custom groups (Administrators, Operators, Viewers)
- **Change Password** - Users can update their own password from sidebar
- **API Keys** - API key authentication with granular permissions

### Virtual Printer
- **Virtual Printer** - Emulates a Bambu Lab printer on your network for Bambu Studio/Orca Slicer
- **Virtual Printer Queue Mode** - Auto-archive and queue prints from slicer
- **Virtual Printer Model Selection** - Choose which printer model to emulate
- **TLS 1.3 Encryption** - Secure MQTT + FTPS with auto-generated certificates

### Print Queue & Scheduling
- **Model-Based Queue Assignment** - Queue to "Any X1C", "Any P1S" with auto filament matching
- **Multi-Printer Selection** - Send prints to multiple printers at once
- **Per-Printer AMS Mapping** - Configure filament mapping individually per printer
- **Queue Bulk Edit** - Select and edit multiple queue items at once
- **Queue Only Mode** - Stage prints without auto-start, release when ready
- **Unassigned Queue Items** - Queue items without assigned printer
- **Add to Queue from File Manager** - Queue sliced files directly from library
- **Print Queue Plate Selection** - Full print configuration in queue modal
- **Deferred Archive Creation** - Archives created when prints start, not when queued

### Smart Plugs & Automation
- **MQTT Smart Plug Support** - Monitor energy from Zigbee2MQTT, Shelly, Tasmota
- **Home Assistant Integration** - Control any HA switch/light as a smart plug
- **HA Energy Sensors** - Use separate sensor entities for power monitoring
- **Tasmota Discovery** - Auto-discover Tasmota devices on network
- **Switchbar Widget** - Quick power toggle in sidebar
- **Tasmota Admin Link** - Quick access to plug web interface

### Camera & Streaming
- **External Camera Support** - MJPEG, RTSP, HTTP snapshot cameras
- **USB Camera Support** - V4L2 webcam support on Linux
- **Build Plate Empty Detection** - AI-powered detection with multi-reference calibration
- **OBS Streaming Overlay** - Embeddable page at `/overlay/:printerId`
- **Camera Zoom & Fullscreen** - 100%-400% zoom with pan support
- **Multiple Embedded Viewers** - Open multiple camera streams simultaneously
- **Camera View Mode** - Choose between new window or embedded overlay
- **Layer-Based Timelapse** - External camera timelapse on layer change
- **Finish Photo in Notifications** - `{finish_photo_url}` template variable

### File Manager
- **STL Thumbnail Generation** - Auto-generate 3D previews for STL files
- **ZIP File Support** - Upload and extract ZIP files directly
- **Create Folder from ZIP** - Auto-create folder named after ZIP file
- **File Manager Sorting** - Sort by name, size, or date
- **File Manager Rename** - Rename files and folders directly
- **File Manager Print Button** - Print directly from selection toolbar
- **Resizable Sidebar** - Drag to adjust width (200-500px)
- **Text Wrap Toggle** - Wrap long folder names instead of truncating
- **Mobile Accessibility** - Touch-friendly with always-visible menus

### Archives & Projects
- **Multi-Plate Selection** - Select which plate to print from multi-plate 3MF
- **Archive Plate Browsing** - Navigate plate thumbnails in archive cards
- **External Links** - Link archives to Printables, Thingiverse, etc.
- **Fusion 360 Attachments** - Attach F3D design files to archives
- **Project Import/Export** - Export/import projects as ZIP with all files
- **BOM Item Editing** - Edit Bill of Materials items after creation
- **Bulk Project Assignment** - Assign multiple archives to project at once
- **Project Parts Tracking** - Track parts separately from plates
- **Tag Management** - Create, edit, and apply tags to archives
- **Archive Comparison** - Compare 2-5 archives side-by-side
- **AMS Filament Preview** - Preview filament colors in archive cards

### Printer Controls
- **Printer Controls** - Stop and Pause/Resume buttons with confirmation
- **Skip Objects** - Skip individual objects without canceling print
- **Chamber Light Control** - Light toggle button on printer cards
- **Resizable Printer Cards** - Four sizes (S/M/L/XL)
- **H2D Pro Support** - Full support for H2D Pro printer model

### AMS & Filament
- **AMS Color Mapping** - Manual slot selection with auto-matching
- **Expandable Color Picker** - 32 colors in configurable palette
- **AMS Slot RFID Re-read** - Re-read filament info via hover menu
- **Print Options in Modals** - Bed leveling, flow cal, vibration cal, timelapse toggles

### Backup & Monitoring
- **GitHub Profile Backup** - Scheduled backup to GitHub repository
- **Prometheus Metrics** - Export telemetry for Grafana
- **MQTT Publishing** - Publish events to external MQTT brokers
- **Application Log Viewer** - Real-time log viewing with filters
- **Support Bundle** - Debug logging with ZIP generation
- **Comprehensive Backup/Restore** - All settings, users, groups included

### Notifications
- **HMS Error Notifications** - 853 error codes translated to human-readable messages
- **Plate Not Empty Notification** - Dedicated category for plate detection
- **Daily Digest** - Consolidated daily notification summary
- **Notification Templates** - Customizable message templates
- **Slack/Mattermost Format** - Proper payload format support

### Statistics & Dashboard
- **Failure Analysis Widget** - Failure rate with correlations and trends
- **Statistics Improvements** - Size-aware responsive widgets
- **Recalculate Costs** - Button to recalculate all archive costs
- **Time Format Setting** - Configurable date/time format
- **Print Quantity Tracking** - Track items per print for progress

### Other Improvements
- **Firmware Update Helper** - Check versions against Bambu Lab servers
- **Disable Firmware Checks** - Toggle to prevent update checks
- **Printer Discovery** - Docker subnet scanning, model mapping
- **FTP Reliability** - Configurable retry with SSL fixes
- **Pre-built Docker Images** - Pull from GitHub Container Registry
- **One-Shot Install Scripts** - Simple `curl | bash` installation
- **Mobile PWA** - Full mobile support with touch gestures
- **Timelapse Editor** - Trim, speed adjustment, music overlay
- **Sidebar Badge Indicators** - Queue and upload counts

---

## Bug Fixes

### Print Queue & Scheduling
- **Home Assistant Auto-On for Queued Prints** - Fixed smart plug not turning on for queue-started prints (Issue #200)
- **AMS Mapping for Model-Based Queue** - Fixed "Any [Model]" queue jobs failing at filament loading (Issue #192)
- **Queue prints on A1** - Fixed "MicroSD Card read/write exception error" when starting prints from queue
- **Multi-Plate Queue Thumbnails** - Queue now shows correct plate thumbnail (Issue #166)
- **Queue items with library files** - Fixed 500 errors when listing/updating queue items from File Manager

### Printer Status & Display
- **A1/A1 Mini Status Display** - Fixed incorrect "Printing" status when idle (Issue #168)
- **Chamber temp on A1/P1S** - Fixed regression where chamber temperature appeared on printers without sensors
- **Active AMS slot display** - Fixed for H2D printers with multiple AMS units
- **Printer hour counter** - Fixed not incrementing during prints and inconsistency between views

### AMS & Filament
- **Empty AMS Slot Recognition** - Fixed removed spools still appearing in Bambuddy (Issue #147)
- **Spoolman Sync for Transparent Spools** - Fixed sync failures for natural/transparent filaments (Issue #190)
- **Spoolman tag field** - Now auto-created on first connect, fixing fresh installs (Issue #123)
- **Spoolman 400 Bad Request** - Fixed when creating spools
- **AMS filament matching** - Fixed in reprint modal
- **User preset AMS configuration** - Fixed user presets showing empty fields in Bambu Studio

### Notifications & Webhooks
- **Progress Milestone Notifications** - Fixed showing wrong time (e.g., "17m" instead of "17h 47m") (Issue #157)
- **Mattermost/Slack Webhooks** - Added proper payload format support (Issue #133)
- **Telegram Notification Parsing** - Fixed markdown errors with underscores in error codes
- **HMS Error Notifications** - 853 error codes now translated to human-readable messages
- **Notifications sent when printer offline** - Fixed

### Camera & Streaming
- **Camera stream reconnection** - Automatic recovery from stalled streams
- **Camera zoom & pan** - Fixed pan range and added pinch-to-zoom for mobile (Issue #132)
- **P2S/X1E/H2 completion photo** - Fixed internal model codes not recognized (Issue #127)
- **Browser freeze** - Fixed on print completion when camera stream was open
- **ffmpeg processes** - Fixed not being killed when closing webcam window

### File Manager & Archives
- **P2S Empty Archive Tiles** - Fixed FTP search for printers without SD card (Issue #146)
- **File Manager folder navigation** - Fixed folder opening then jumping back to root (Issue #160)
- **File Manager upload** - Now accepts all file types, not just ZIP
- **Multi-plate 3MF metadata** - Single-plate exports now show correct thumbnail
- **Archive card cache** - Fixed wrong cover image bug
- **Archive delete safety** - Added checks to prevent deleting parent directories

### Statistics & Tracking
- **Print time stats** - Now uses actual elapsed time instead of slicer estimates (Issue #137)
- **Filament cost** - Now uses "Default filament cost" setting instead of hardcoded €25 (Issue #120)
- **Reprint cost tracking** - Now adds cost to existing total instead of replacing
- **K-Profiles backup status** - Fixed showing incorrect printer connection count

### Smart Plugs
- **HA Energy Sensors** - Fixed sensors with lowercase units (w, kwh) not detected (Issue #119)

### UI & UX
- **Skip objects modal overflow** - Fixed modal going above browser window (Issue #134)
- **Project card filament badges** - Fixed showing duplicates and raw color codes
- **Subnet scan serial number** - Fixed A1 Mini showing "unknown-*" placeholder (Issue #140)
- **Slicer protocol** - Fixed OS detection (Windows vs macOS/Linux)

### API & Backend
- **Settings API PATCH Method** - Added for Home Assistant rest_command compatibility (Issue #152)
- **GitHub Backup Timestamps** - Removed volatile timestamps for cleaner git diffs
- **Plate Calibration Persistence** - Fixed reference images not persisting in Docker
- **Update module** - Fixed for Docker-based installations

---

## Maintenance

- Upgraded vitest from 2.x to 3.x for security improvements
- Added security scanning (pip-audit, npm audit) to CI pipeline
- Replaced python-jose with PyJWT to eliminate ecdsa vulnerability
- Improved test coverage (796 backend tests, 518 frontend tests)

---

## Thank You!

This release wouldn't be possible without our amazing community. A huge thank you to everyone who contributed code, reported bugs, tested beta releases, and provided feedback!

### Code Contributors

| Contributor | Contribution |
|-------------|--------------|
| **[@maziggy](https://github.com/maziggy)** (MartinNYHC) | Lead developer, core features |
| **[@MisterBeardy](https://github.com/MisterBeardy)** (Wesley Reaves) | STL thumbnail generation |
| **[@JesseFPV](https://github.com/JesseFPV)** (Jesse Hulswit) | Optional authentication system |

### Issue Reporters & Testers

Special thanks to everyone who reported issues, tested beta releases, and provided valuable feedback:

- **[@Locxion](https://github.com/Locxion)** (Markus Bender) - A1 Mini status bug, log viewer feature
- **[@cadtoolbox](https://github.com/cadtoolbox)** (Thomas Rambach) - H2D Pro support, model-based queue
- **[@Twilek-de](https://github.com/Twilek-de)** - Empty AMS slots, Mattermost webhooks, progress milestones
- **[@elit3ge](https://github.com/elit3ge)** - Archive tiles, completion photos, upload improvements
- **[@opensourcefan](https://github.com/opensourcefan)** - Subnet scan serial, status colors
- **[@1nv4lidus3r](https://github.com/1nv4lidus3r)** - Print time stats, skip objects modal
- **[@joaorgoncalves](https://github.com/joaorgoncalves)** (João Gonçalves) - Filament cost settings, HA energy sync
- **[@beardofbeespool](https://github.com/beardofbeespool)** (Morton Likely) - STL thumbnail feature request
- **[@PeterXQChen](https://github.com/PeterXQChen)** (Peter Chen) - File manager folder navigation
- **[@Robnex](https://github.com/Robnex)** - External links, external spool sync
- **[@caco3](https://github.com/caco3)** (CaCO3) - Disable firmware checks
- **[@sbcrumb](https://github.com/sbcrumb)** - Camera zoom feature
- **[@fcps3](https://github.com/fcps3)** - Spoolman transparent spool sync
- **[@LucHeart](https://github.com/LucHeart)** - MQTT connection issues
- **[@ouihq](https://github.com/ouihq)** (Jonas) - File manager queue bug
- **[@Schuermi7](https://github.com/Schuermi7)** - AMS mapping, cloud 2FA
- **[@stubbers](https://github.com/stubbers)** (Joseph Stubberfield) - X1C sync issues
- **[@nvdmedianl](https://github.com/nvdmedianl)** (Nathan) - Spoolman Bambu spool errors
- **[@lbeumer-bit](https://github.com/lbeumer-bit)** - Notification photos
- **[@IROKILLER](https://github.com/IROKILLER)** - Home Assistant automations
- **[@fgrfn](https://github.com/fgrfn)** (Florian) - Dynamic electricity cost
- **[@JasonSwindle](https://github.com/JasonSwindle)** (Jason Swindle) - Smart plug text overflow
- **[@Cassiopeia1980](https://github.com/Cassiopeia1980)** - Connection issues

And many more community members who tested, provided feedback, and helped make Bambuddy better!

---

## Upgrade Notes

### From 0.1.5.x or earlier
- Database migrations run automatically on startup
- User authentication is optional and disabled by default
- Existing installations will continue to work without changes

### From 0.1.6 beta
- All beta migrations are included in the final release
- No action required - just update and restart

---

## What's Next?

We're already planning 0.1.7 with more exciting features. Stay tuned and keep the feedback coming!

- [GitHub Issues](https://github.com/MisterBeardy/bambuddy/issues) - Report bugs and request features
- [GitHub Discussions](https://github.com/MisterBeardy/bambuddy/discussions) - Join the conversation

---

**Happy Printing!** 🎉

*— The Bambuddy Team*
