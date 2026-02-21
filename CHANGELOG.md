# Changelog

All notable changes to Bambuddy will be documented in this file.

## [0.2.1b2] - Unreleased

### Fixed
- **Non-Actionable HMS Errors Triggering Notifications** ([#470](https://github.com/maziggy/bambuddy/issues/470)) — Infrastructure and auth-related HMS error codes (like `0500_0007` "MQTT command verification failed") were triggering printer error notifications even though they don't indicate actual print problems. For example, a device with incorrect bind settings sending unauthorized MQTT commands caused repeated false-alarm nozzle/extruder error notifications with camera snapshots of perfectly fine prints. Now suppresses notifications for known non-actionable error codes: `0500_0007` (MQTT auth failure), `0500_4001` (Bambu Cloud connection failure), and `0500_400E` (print cancelled by user).
- **Support Bundle Leaking Personal Data** ([#473](https://github.com/maziggy/bambuddy/issues/473)) — The support bundle's log sanitizer only used regex patterns, which can't detect arbitrary user-chosen strings like printer names and usernames. Now queries the database for known sensitive values (printer names, serial numbers, auth usernames, Bambu Cloud email) and does exact-string replacement before the regex pass. Serial number regex no longer leaks the first 3 characters (was using a capture group for partial redaction). Tasmota smart plug credentials embedded in URLs (`http://user:pass@host`) were logged verbatim by httpx; now uses httpx's `auth` parameter for HTTP Basic auth so credentials never appear in the URL. Added `username` and `path` to the settings key filter to redact `smtp_username` and `slicer_binary_path` from the support info JSON. A URL credentials regex provides defense-in-depth for any remaining `user:pass@` patterns in logs. IP addresses are no longer redacted from the bundle as they are needed for connectivity debugging. Updated the frontend privacy disclaimer and wiki documentation to reflect the new behavior.
- **Spool Usage Lost When Spool Runs Empty Mid-Print** ([#459](https://github.com/maziggy/bambuddy/issues/459)) — When a spool ran empty during a print and the AMS auto-switched to a backup spool, the `on_ams_change` handler eagerly deleted the empty spool's `SpoolAssignment` record (fingerprint mismatch). When `on_print_complete` later ran, it queried `SpoolAssignment` live from the database, found nothing, and silently dropped usage. Now snapshots all spool assignments at print start into the `PrintSession`, so usage is correctly attributed at completion regardless of mid-print AMS changes.
- **K-Profile Response Race Condition Crash** ([#462](https://github.com/maziggy/bambuddy/issues/462)) — An unsolicited or late K-profile MQTT response could crash the MQTT handler with `AttributeError: 'NoneType' object has no attribute 'set'`. The MQTT callback thread checked `self._pending_kprofile_response` (not None) at line 2698, but between that check and the `.set()` call, the asyncio thread's `finally` block in `get_kprofiles()` could clear the attribute to `None` after a timeout — a classic TOCTOU race. Fixed by capturing the event reference in a local variable before the check.
- **Queue Stuck on "Busy" for "Any Model" Jobs** ([#435](https://github.com/maziggy/bambuddy/issues/435)) — When a print was queued with "Any [Model]" (e.g., "Any P1S"), it was created with `printer_id=NULL` and `target_model="P1S"`. After the assigned printer finished, the queue widget queried only for items matching `printer_id=X`, missing the next pending model-based item (`printer_id IS NULL`). With no next item found, the "Clear Plate & Start Next" button never appeared, leaving the scheduler stuck reporting "Busy". The queue API now accepts an optional `target_model` parameter; when combined with `printer_id`, it uses OR logic to also return unassigned items whose `target_model` matches the printer's model. The frontend passes the printer's model through to this query. Additionally, the backend now resolves the printer's model server-side from the database when the frontend doesn't provide `target_model` (e.g., when the printer was added without selecting a model), ensuring the OR logic works regardless of whether the client knows the printer's model.
- **Queue "Any Model" Jobs Stuck in "Waiting" After Plate Clear** ([#435](https://github.com/maziggy/bambuddy/issues/435)) — After the queue visibility fix above, "Any Model" jobs were correctly assigned to an idle printer but immediately crashed with `'>=' not supported between instances of 'str' and 'int'` when computing AMS filament mapping. MQTT raw data returns AMS unit and tray IDs as strings, but `_build_loaded_filaments()` compared them to integers without casting. The crash prevented the assignment from committing, so the scheduler retried every 30 seconds in an infinite loop. Cast `ams_id` and `tray_id` to `int()` to match the pattern already used for external spool IDs.
- **SD Card Cleanup After Print Never Runs** ([#374](https://github.com/maziggy/bambuddy/issues/374)) — The post-print SD card cleanup (which deletes uploaded gcode from the printer root to prevent phantom prints on power cycle) used `printer_manager.get_printer()`, which returns a `PrinterInfo` with only `name` and `serial_number`. Accessing `.ip_address`, `.access_code`, and `.model` raised `AttributeError`, silently caught by the outer exception handler. Replaced with a DB query for the `Printer` model, matching the pattern used everywhere else in `on_print_complete()`.
- **ntfy Notifications Fail With "Illegal header value"** ([#466](https://github.com/maziggy/bambuddy/issues/466)) — When sending ntfy notifications with image attachments (progress, error events), the message body was placed in an HTTP `Message` header. Multi-line messages (e.g., printer name + remaining time) contain newline characters, which are illegal in HTTP headers. Test notifications worked because they are single-line with no image. Now escapes newlines to literal `\n` in the header, which ntfy interprets and renders as actual line breaks.
- **Inventory Date Format Ignores Settings** ([#463](https://github.com/maziggy/bambuddy/issues/463)) — The inventory page used a local `formatDate()` that hardcoded the `en-GB` locale, always displaying dates in a fixed format regardless of the date format setting. Now fetches the `date_format` setting and uses the shared `formatDateInput()` utility which formats as MM/DD/YYYY, DD/MM/YYYY, YYYY-MM-DD, or browser locale based on the user's choice.
- **Inventory Location Shows Garbled Characters for AMS-HT Slots** ([#463](https://github.com/maziggy/bambuddy/issues/463)) — The inventory location column computed slot letters via `String.fromCharCode(65 + ams_id)`, which produced accented characters (e.g., `Á`) for AMS-HT units (ams_id ≥ 128). Now uses the shared `formatSlotLabel()` utility which correctly handles AMS-HT and external spool slots.

### New Features
- **Filament Cost Tracking** ([#454](https://github.com/maziggy/bambuddy/pull/454), [#452](https://github.com/maziggy/bambuddy/issues/452)) — Track per-spool filament costs and see cost breakdowns for every print. Each spool can have a `cost_per_kg` value; when a print completes, the usage tracker calculates the cost from actual filament consumption and stores it in the usage history. Archive costs are automatically aggregated from spool usage records. A global `default_filament_cost` setting (Settings → Filament) provides a fallback when spools don't have individual costs set. The print modal shows a real-time cost preview based on loaded filaments. Archive cards display the total cost. The inventory table includes a sortable cost/kg column. The recalculate-costs endpoint can retroactively update all archive costs when filament prices change. Contributed by @Keybored02.
- **Background Print Dispatch** ([#408](https://github.com/maziggy/bambuddy/pull/408), [#112](https://github.com/maziggy/bambuddy/issues/112)) — Printing from archives and the file manager now runs in the background via an async dispatch service. FTP uploads and print-start commands are decoupled from API request latency, so the UI responds immediately. Real-time progress is streamed to all clients via WebSocket, rendered as a persistent toast with per-job upload progress bars, status badges (dispatched/processing/completed/failed/cancelled), and a cancel button. The dispatcher supports concurrent uploads to different printers with per-printer queuing to prevent conflicts. Cancellation is cooperative — uploads abort at the next chunk boundary and clean up partial files on the printer. Batch progress tracking shows overall completion across multi-printer dispatches. Translations added for all 6 locales (en, de, fr, it, ja, pt-BR).
- **Include Beta Updates Setting** — New toggle in Settings → Updates to opt in to beta/prerelease update notifications. Default: off (stable only). The update checker now fetches `/releases` instead of `/releases/latest` and filters by `parse_version()` prerelease detection (not GitHub's `prerelease` flag, which may not be set correctly). Users on the Docker `latest` tag will no longer see notifications for beta releases they can't install.

### Improved
- **Filament Cost Tracking Test Coverage** — Added 2 backend unit tests for archive cost aggregation (zero-cost guard preserves existing costs, positive-cost updates archive correctly). Added 2 frontend unit tests for spool form cost_per_kg persistence. Fixed missing `archive_id` database migration, SQLAlchemy `is None` → `.is_(None)` in where clauses, duplicate archive cost write, and unconditional zero-cost overwrite.
- **Spool Assignment Snapshot Test Coverage** — Added 7 backend unit tests covering spool assignment snapshotting at print start, snapshot-preferred spool lookup in both 3MF and AMS delta paths, fallback to live query for pre-upgrade sessions, and the core mid-print unlink scenario from #459.
- **Background Dispatch Test Coverage** — Added 5 backend unit tests for dispatch cancel races (single-lock TOCTOU fix), batch counter reset re-check, and job lifecycle. Added 2 FTP regression tests for voidresp error handling (upload-loop prevention) and A1 model voidresp skip. Added 1 frontend test for reprint toast suppression.
- **Frontend Pre-Commit Hooks** ([#458](https://github.com/maziggy/bambuddy/issues/458)) — Added `frontend-typecheck` (`tsc --noEmit`) and `frontend-lint` (`eslint .`) hooks to the pre-commit config. Both hooks only trigger when `frontend/src/**/*.{ts,tsx}` files are staged.

## [0.2.1b] - 2026-02-19

### Fixed
- **PAUSED State Never Matched** ([#447](https://github.com/maziggy/bambuddy/issues/447)) — Removed dead `PAUSED` checks across frontend and backend. The printer only sends `PAUSE` via MQTT `gcode_state`, so `PAUSED` comparisons were unreachable code.
- **Nozzle Mapping Uses Wrong Source in 3MF Files** — The `extract_nozzle_mapping_from_3mf()` function used `filament_nozzle_map` (user preference) as the primary source for nozzle assignments. BambuStudio's "Auto For Flush" mode overrides user preferences at slice time, so the actual assignment lives in the `group_id` attribute on `<filament>` elements in `slice_info.config`. Now uses `group_id` as the primary source and falls back to `filament_nozzle_map` only when `group_id` is not present.
- **Print Scheduler Hard-Filters Nozzle When No Trays on Target Nozzle** — On dual-nozzle printers, the scheduler enforced a strict nozzle filter when matching filaments. If a slicer filament was assigned to a nozzle with no AMS trays (e.g., only external spool on left nozzle), the match failed even though the filament existed on the other nozzle. Now falls back to unfiltered matching when no trays exist on the target nozzle.
- **Print Scheduler External Spool Ignores Nozzle Assignment** — The external spool fallback in the scheduler always mapped to extruder 0 (right), ignoring the slicer's nozzle assignment. Now uses the 3MF nozzle mapping to select the correct extruder for external spool matches.
- **ams_extruder_map Race Condition on Printer Status API** — The `/printers/{id}/status` endpoint read `ams_extruder_map` from the MQTT state without checking if the AMS data had been received yet. On fresh connections before the first AMS push-all, this returned an empty map — causing the frontend nozzle filter to show all trays as unfiltered. Now returns an empty object gracefully and the frontend disables nozzle filtering until the map is populated.
- **Filament Mapping Frontend Ignores Nozzle for External Spools** — The `useFilamentMapping` hook always set `extruder_id: 0` for external spool matches. Now uses the nozzle mapping from the 3MF file to determine the correct extruder.
- **AMS-HT Global Tray ID Computed Wrong on Printer Card** — The PrintersPage computed AMS-HT tray IDs using `ams_id * 4 + slot` (giving 512+), but AMS-HT units use their raw `ams_id` (128-135) as the global tray ID. Now uses `ams_id` directly for AMS-HT units.
- **Filament Mapping Dropdown Shows Wrong Nozzle Trays** — The FilamentMapping dropdown filtered by `extruder_id` using strict equality, but `extruder_id` could be `undefined` for printers that hadn't reported their AMS extruder map yet. This caused all trays to be hidden. Now skips nozzle filtering when `extruder_id` is undefined.
- **Cancelled Print Usage Tracking Uses Stale Progress/Layer** — When a print was cancelled, the usage tracker read `mc_percent` and `layer_num` from the printer's MQTT state — but by the time the `on_print_complete` callback ran, the printer had already reset these to 0. Now captures the last valid progress and layer values during printing, and the usage tracker reads these captured values on cancellation for accurate partial usage.
- **H2D Tray Disambiguation Triggers on Single-Nozzle Printers** — The `tray_now <= 3` check for H2D dual-nozzle disambiguation matched any printer loading from AMS 0 (trays 0-3). On P2S, X1C, and X1E with multiple AMS units, this caused warning log spam every second. Now uses a persistent `_is_dual_nozzle` flag detected from `device.extruder.info` (>= 2 entries), which only dual-nozzle printers (H2D, H2D Pro) report.
- **AMS-HT Snow Slot Mismatch Log Spam on H2D** — The snow-based tray_now disambiguation computed `snow_slot = -1` for AMS-HT trays (IDs 128-135), causing a "slot mismatch" debug log on every MQTT update even though the result was correct. Now correctly computes `snow_slot = 0` for AMS-HT single-slot units.
- **H2D Tray Disambiguation Produces Bogus tray_now for AMS-HT** ([#364](https://github.com/maziggy/bambuddy/issues/364)) — When the snow field hadn't arrived yet on H2D dual-nozzle printers, the `ams_extruder_map` fallback computed `ams_id * 4 + slot` for all AMS types — including AMS-HT units (IDs 128-135) which have a single slot and use their unit ID as the global tray ID. This produced bogus values like 512+ that briefly appeared in the UI and could pollute `last_loaded_tray`. Now correctly returns the AMS-HT unit ID for single-slot units, handles AMS-HT in multi-AMS matching, filters AMS-HT candidates when slot > 0, and tightens `last_loaded_tray` to only accept physically valid tray IDs (0-15, 128-135, 254).
- **Color Tooltip Clipped Behind Adjacent Swatches** — Color swatch hover tooltips in the spool form were rendered behind neighboring swatches due to missing z-index on the hover state. Added `hover:z-20` and tooltip `z-20` classes.
- **Print Queue Shows UUID Hash Instead of Filename** ([#438](https://github.com/maziggy/bambuddy/issues/438)) — When printing a library file, the Print Queue and archive displayed the UUID-hex disk filename (e.g., `c65887535303404eba1525176a0f78dc`) instead of the original human-readable name. Library files are stored on disk with UUID filenames for uniqueness, but `archive_print()` used the disk path as the display name. Now passes the original `LibraryFile.filename` through to `archive_print()` from both the print scheduler and the direct-print-from-library flow, so the archive's `filename`, `print_name`, and directory name all use the human-readable name.

- **Usage Tracking Wrong Spool on Dual-Nozzle / Multi-AMS Printers** ([#364](https://github.com/maziggy/bambuddy/issues/364)) — On H2C, H2D Pro, and other dual-nozzle printers with multiple AMS units, the usage tracker attributed filament consumption to the wrong spools. The MQTT `mapping` field — a per-print array that maps slicer filament slots to physical AMS trays — was preserved in state but never parsed or used. The tracker fell back to `slot_id - 1` as the global tray ID, which is incorrect when AMS hardware IDs differ from sequential indices (e.g., AMS-HT units with ID 128). Now decodes the MQTT mapping field from its snow encoding (`ams_hw_id * 256 + local_slot`) into bambuddy global tray IDs and uses it as a universal mapping source — working for all printer models and all print sources (slicer, queue, reprint) without relying on `tray_now` disambiguation. For printers that don't provide the MQTT mapping field (A1, A1 Mini, P1S, P2S), a color-matching fallback compares 3MF filament slot colors against AMS tray colors to resolve the correct slot-to-tray mapping. Gracefully returns no match when colors are ambiguous (duplicate tray colors) or unavailable.
- **AMS Slot Config: PFUS Preset IDs Cause Slicer to Reset Slots** — When assigning a spool with a user-local `PFUS*` preset ID (from BambuStudio's custom filament profiles), the slicer didn't recognize the ID and actively reset the AMS slot configuration. Now replaces `PFUS*` IDs with generic Bambu filament IDs (e.g., `GFL99` for PLA). When the slot already has a recognized cloud-synced preset for the same material (e.g., `P4d64437`), it is reused to preserve K-profile calibration associations. Applies to both the slot configure endpoint and the inventory spool assignment flow.
- **Fill Level Bar Missing for Brand New Spools** — Spools with `weight_used = 0` (brand new, never printed) showed no fill level bar on the printer card. The condition checked `weight_used > 0` instead of `weight_used != null`, excluding zero-usage spools. Now correctly shows 100% fill for new spools while still hiding the bar when weight data is unavailable (`null`).
- **npm audit: suppress moderate ajv ReDoS finding** — Added `audit-level=high` to `frontend/.npmrc` so `npm audit` exits cleanly. The ajv@6 ReDoS (GHSA-2g4f-4pwh-qvx6) is a transitive dependency of eslint@9 with no patched v6 release; ajv@8 override breaks eslint. The vulnerability requires crafted `$data` schema input — not an attack vector in a linting config.
- **npm audit: fix minimatch ReDoS finding** — Added an npm override for `minimatch@^10.2.1` in `package.json` to resolve the high-severity ReDoS (GHSA-3ppc-4f35-3m26) affecting minimatch@3.x/9.x pulled in transitively by eslint@9, typescript-eslint, and @vitest/coverage-v8. Eslint@9 pins minimatch@3.x with no patched release; eslint@10 upgrades to minimatch@10 but is not yet available. The override forces the patched version across the tree. Verified lint, build, and all tests pass.
- **Spool Form Allows Empty Brand & Subtype** ([#417](https://github.com/maziggy/bambuddy/issues/417)) — The spool add/edit modal did not require Brand or Subtype fields, allowing spools to be saved without them. When such a spool was assigned to an AMS slot, the `tray_sub_brands` sent to the printer was incomplete (e.g., just "PETG" instead of "PETG Basic"), causing BambuStudio to not recognize the filament profile. Brand and Subtype are now mandatory fields with validation errors shown on submit.
- **Open in Slicer Fails When Authentication Enabled** ([#421](https://github.com/maziggy/bambuddy/issues/421)) — The "Open in Slicer" buttons for BambuStudio and OrcaSlicer failed with "importing failed" when authentication was enabled. Slicer protocol handlers (`bambustudio://`, `orcaslicer://`) launch the slicer app which fetches the file via HTTP — but cannot send authentication headers, so the global auth middleware returned 401. Additionally, the URL format was wrong on Linux (used the macOS-only `bambustudioopen://` scheme instead of `bambustudio://open?file=`). Fixed with short-lived, single-use download tokens: the frontend fetches a token via an authenticated POST endpoint, then builds a `/dl/{token}/{filename}` URL that the slicer can access without auth headers. The token is validated server-side (5-minute expiry, single-use). Platform-specific URL formats now match the actual slicer source code: macOS uses `bambustudioopen://` with URL encoding, Windows/Linux use `bambustudio://open?file=`, and OrcaSlicer uses `orcaslicer://open?file=`.

### New Features
- **Multiple Virtual Printers** — Run multiple virtual printers per Bambuddy installation. Each virtual printer gets a dedicated bind IP address with completely independent FTP, MQTT, SSDP, and Bind servers — no shared services or SNI routing. Full CRUD API (`/api/virtual-printers`) and React UI for creating, editing, and deleting virtual printers. Each instance supports all four modes (Immediate, Review, Print Queue, Proxy), any of the 11 supported printer models, per-instance TLS certificates (shared CA), and individual network interface override. Database-backed with auto-incremented serial suffixes.
- **Virtual Printer: Dual Bind/Detect Ports** ([#445](https://github.com/maziggy/bambuddy/issues/445)) — The slicer bind/detect handshake now listens on both ports 3000 and 3002. Different BambuStudio/OrcaSlicer versions use different ports for this handshake, so Bambuddy accepts connections on either. Applies to both server mode (BindServer) and proxy mode (SlicerProxyManager).
- **Clear Plate Permission** ([#446](https://github.com/maziggy/bambuddy/issues/446)) — New `printers:clear_plate` permission allows admins to grant users the ability to confirm a plate is cleared for the next queued print without granting full `printers:control` (which also allows stopping prints, configuring AMS, toggling lights, etc.). Existing groups with `printers:control` automatically receive the new permission on startup. The Operators default group includes it by default.
- **Full-Page Group Permission Editor** ([#446](https://github.com/maziggy/bambuddy/issues/446)) — Replaced the cramped permission modal with a dedicated full-page editor at `/groups/:id/edit`. Features a responsive 2-column grid of always-expanded category cards, permission search/filtering, Select All / Clear All bulk actions, category-level checkboxes with partial state, and a fixed bottom action bar. The old `GroupsPage.tsx` dead code has been removed.

### Changed
- **Filament Catalog API Renamed** ([#427](https://github.com/maziggy/bambuddy/issues/427)) — Renamed `/api/v1/filaments/` to `/api/v1/filament-catalog/` to avoid confusion with the inventory spools page (labeled "Filament" in the UI). The old endpoint managed material type definitions (cost, temperature, density), not physical spools — the shared name caused users to expect the API to return their spool inventory.

### Improved
- **AMS Mapping Test Coverage** — Added 63 backend tests for scheduler AMS mapping (nozzle filtering, external spool extruder assignment, fallback behavior) and 43 frontend tests for `useFilamentMapping` hook (nozzle-aware matching, AMS-HT handling, external spool extruder logic).
- **Tray Now Disambiguation Test Coverage** — Added 28 MQTT message replay tests covering all `tray_now` disambiguation paths: single-nozzle passthrough (X1E/P2S), H2D dual-nozzle snow field, pending target, `ams_extruder_map` fallback, active extruder switching, and full multi-color print lifecycles.
- **Tray Info Idx Resolution Test Coverage** — Added 12 backend integration tests for PFUS→generic tray_info_idx resolution across both the slot configure and inventory assignment endpoints, plus 10 frontend unit tests for the fill level calculation logic.


## [0.2.0] - 2026-02-17

### New Features
- **Bed Cooled Notification** ([#378](https://github.com/maziggy/bambuddy/issues/378)) — New notification event that fires when the print bed cools below a configurable threshold (default 35°C) after a print completes. Useful for knowing when it's safe to remove parts. A background task polls the bed temperature every 15 seconds after print completion and sends a notification when it drops below the threshold. Automatically cancels if a new print starts or the printer disconnects. The threshold is configurable in Settings → Notifications. Includes a customizable notification template with printer name, bed temperature, and threshold variables.
- **Spool Inventory — AMS Slot Assignment** — Assign inventory spools to AMS slots for filament tracking. Hover over any non-Bambu-Lab AMS slot to assign or unassign spools. The assign modal filters out Bambu Lab spools (tracked via RFID) and spools already assigned to other slots. Bambu Lab spool slots automatically hide assign/unassign UI since they are managed by the AMS. When a Bambu Lab spool is inserted into a slot with a manual assignment, the assignment is automatically unlinked.
- **Spool Inventory — Remaining Weight Editing** — Edit the remaining filament weight when adding or editing a spool. The new "Remaining Weight" field in the Additional section shows current weight (label weight minus consumed) with a max reference. Edits are stored as `weight_used` internally.
- **Spool Inventory — Unified 3MF-Based Usage Tracking** ([#336](https://github.com/maziggy/bambuddy/issues/336)) — All spools (Bambu Lab and third-party) now use 3MF slicer estimates as the primary tracking source. Per-filament `used_g` data from the archived 3MF file provides precise per-spool consumption. For failed or aborted prints, per-layer G-code analysis provides accurate partial usage up to the exact failure layer, with linear progress scaling as fallback. AMS remain% delta is the final fallback for G-code-only prints without an archived 3MF. Slot-to-tray mapping uses queue `ams_mapping` for queue-initiated prints and the printer's `tray_now` state for single-filament non-queue prints, ensuring the correct physical spool is always tracked.
- **Notification Templates — Filament Usage Variables** ([#336](https://github.com/maziggy/bambuddy/issues/336)) — `print_complete`, `print_failed`, and `print_stopped` notification events now expose `{filament_grams}` (total grams, scaled by progress for partial prints), `{filament_details}` (per-filament breakdown with AMS slot info, e.g. "AMS-A T1 PLA: 12.4g | AMS-A T3 PETG: 2.8g"), and `{progress}` (completion percentage for failed/stopped prints). The `{filament_details}` variable includes the AMS unit and tray position for each filament used, with "Ext" shown for external spool holders. Falls back to type-only format (e.g. "PLA: 10.0g") when usage tracking data is unavailable. Webhook payloads include `filament_used`, `filament_details`, and `progress` fields. Per-slot filament data is stored in archive `extra_data` for downstream use.
- **Printer Status Summary Bar — Next Available & Availability Count** ([#354](https://github.com/maziggy/bambuddy/issues/354)) — The status bar on the Printers page now shows an availability count ("X available") alongside the printing/offline counts, and a "Next available" indicator showing which printing printer will finish soonest — with printer name, mini progress bar, completion percentage, and remaining time. Useful for print farms to quickly identify the next free printer. Updates in real-time via WebSocket. Translated in all 4 locales (en, de, ja, it).
- **Nozzle-Aware AMS Filament Mapping for Dual-Nozzle Printers** ([#318](https://github.com/maziggy/bambuddy/issues/318)) — On dual-nozzle printers (H2D, H2D Pro), each AMS unit is physically connected to either the left or right nozzle. Bambuddy now reads nozzle assignments from the 3MF file (`filament_nozzle_map` + `physical_extruder_map` in `project_settings.config`) and constrains filament matching to only AMS trays connected to the correct nozzle via `ams_extruder_map`. Applies to the print scheduler, reprint modal, queue modal, and multi-printer selection. Falls back gracefully to unfiltered matching when no trays exist on the target nozzle. The filament mapping UI shows L/R nozzle badges for dual-nozzle prints. Translated in all 4 locales (en, de, ja, it).
- **Dual External Spool Support for H2D** — H2-series printers with two external spool holders (Ext-L and Ext-R) are now fully supported. The external spool section renders as a grid with both slots, each showing filament type, color, fill level, and hover card details. Previously only a single external spool was displayed. Applies to the printer card, filament mapping, print scheduler, usage tracking, and inventory assignment. The `vt_tray` field is now an array across the entire stack (MQTT, API, WebSocket, frontend).
- **AMS Slot Configuration — Model Filtering & Pre-Population** — The Configure AMS Slot modal now filters filament presets by the connected printer model. Only presets matching the printer (e.g., "@BBL X1C" presets for X1C printers) and generic presets without a model suffix are shown. Local presets are filtered by their `compatible_printers` field. When re-configuring an already-configured slot, the modal pre-selects the saved preset, pre-populates the color, and auto-selects the active K-profile. The preset list auto-scrolls to the selected item. All modal strings are now fully translated in 5 locales (en, de, fr, it, ja).
- **K-Profiles View — Accurate Filament Name Resolution** — K-profile filament names are now resolved from builtin filament tables and user cloud presets (via new `/cloud/filament-id-map` endpoint) instead of showing raw IDs like "GFU99" or "P4d64437". Falls back to extracting names from the profile name field.
- **Print Log** — New view mode on the Archives page showing a chronological table of all print activity. Columns include date/time, print name, printer, user, status, duration, and filament. Supports filtering by search text, printer, user, status, and date range. Pagination with configurable page size. A dedicated clear button deletes only log entries without affecting archives. Data is stored in a separate `print_log_entries` database table.
- **Sync Spool Weights from AMS** — New button in Settings → Filament Tracking (built-in inventory mode) to force-sync all inventory spool weights from the live AMS remain% values of connected printers. Overwrites the database weight data with current sensor readings. Useful for recovering from corrupted weight data (e.g., after a power-off event zeroed all fill levels). Requires printers to be online. Includes a confirmation modal.
- **Notification Thumbnails for Telegram & ntfy** ([#372](https://github.com/maziggy/bambuddy/issues/372)) — Print thumbnail images are now attached to Telegram and ntfy notifications (previously only Pushover and Discord). Telegram uses the `sendPhoto` API with the image as caption attachment. ntfy sends the image as a binary PUT with `Filename` and `Message` headers. No configuration needed — images are sent automatically when available.
- **Clear HMS Errors** — New "Clear Errors" button in the HMS error modal sends a `clean_print_error` MQTT command to dismiss stale `print_error` values that persist after print cancellation or transient events. Locally clears the error list for immediate UI feedback. Permission-gated to `printers:control`. The button only appears when there are active errors.

### Fixed
- **Firmware Upload Uses Wrong Filename on Cache Hit** — The firmware update uploader cached downloaded firmware files under a mangled name (e.g., `X1C_01_09_00_10.bin`) instead of the original filename from Bambu Lab's CDN. On the first download the correct filename was uploaded to the SD card, but on subsequent attempts the cached file with the wrong name was used — causing the printer to not recognize the firmware file. Now caches using the original filename so the SD card always receives the correct file.
- **Update Check Runs When Disabled** ([#367](https://github.com/maziggy/bambuddy/issues/367)) — The Settings page triggered an update check on every visit even when "Check for updates" was disabled, causing error popups on air-gapped systems with no internet. The backend `/updates/check` endpoint also ignored the setting entirely. Now the backend returns early without making GitHub API calls when the setting is disabled, the Settings page respects the `check_updates` flag before auto-fetching, and the printer card firmware badge shows a neutral version-only display instead of disappearing when firmware update checks are off.
- **Stale Inventory Assignments Persist After Switching to Spoolman Mode** — When switching from built-in inventory to Spoolman mode, existing spool-to-AMS-slot assignments were not cleaned up. The printer card hover cards continued showing "Assign Spool" buttons that opened the internal inventory modal, and any prior assignments remained visible. Now bulk-deletes all `SpoolAssignment` records when enabling Spoolman, invalidates the frontend cache so printer cards update immediately, and hides the inventory assign/unassign UI on printer cards while in Spoolman mode.
- **Bulk Archive Delete Leaves Orphaned Database Records** — When bulk-deleting archives, the files were removed from disk before the database commit. If concurrent SQLite writes caused a lock timeout, the commit failed and rolled back — leaving database records pointing to deleted files (broken thumbnails, 404 errors). Fixed by deleting the database record first and only removing files after a successful commit.
- **Model-Specific Maintenance Tasks for Carbon Rods vs Linear Rails** ([#351](https://github.com/maziggy/bambuddy/issues/351)) — Maintenance tasks "Clean Carbon Rods" and "Lubricate Linear Rails" were shown for all printers regardless of motion system. H2 and A1 series use linear rails (not carbon rods), and X1/P1/P2S series use carbon rods (not linear rails). Maintenance types are now classified by rod/rail type: "Lubricate Carbon Rods" and "Clean Carbon Rods" for X1/P1/P2S, "Lubricate Linear Rails" and "Clean Linear Rails" for A1/H2. Stale and duplicate system types are automatically cleaned up on startup. Includes model-specific wiki links and i18n keys for all 4 locales.
- **AMS Slot Configuration Overwritten on Startup** — Bambuddy was resetting AMS slot filament presets on every startup and reconnection. The `on_ams_change` callback unconditionally unlinked Bambu Lab spool assignments on each MQTT push-all response, then re-assigned them by sending `ams_filament_setting` without a `setting_id`, which cleared the printer's filament preset. Now compares spool RFID identifiers (`tray_uuid` / `tag_uid`) before unlinking — if the same spool is still in the slot, the assignment is preserved and no `ams_filament_setting` command is sent.
- **Bambu Lab Spool Detection False Positives** — The `is_bambu_lab_spool()` function (backend) and `isBambuLabSpool()` (frontend) incorrectly identified third-party spools as Bambu Lab spools when they used Bambu generic filament presets (e.g., "Generic PLA"). The `tray_info_idx` field (e.g., "GFA00") identifies the filament *type*, not the spool manufacturer — third-party spools using Bambu presets also have GF-prefixed values. Removed `tray_info_idx` from detection logic; now uses only hardware RFID identifiers (`tray_uuid` and `tag_uid`) which are physically embedded in genuine Bambu Lab spools.
- **FTP Disconnect Raises EOFError When Server Dies** — `BambuFTPClient.disconnect()` only caught `OSError` and `ftplib.Error`, but `quit()` raises `EOFError` when the server has closed the connection mid-session. `EOFError` is not a subclass of either, so it propagated to callers. Now caught alongside the other exception types for clean best-effort disconnect.
- **RFID Spool Data Erased by Periodic AMS Updates** — Periodic MQTT push-all responses cleared `tag_uid` and `tray_uuid` fields because they were included in the "always update" list. These fields are now preserved during updates and only cleared when a spool is physically removed (slot clearing detected by empty `tray_type`). This fixes the AMS "eye" icon disappearing for RFID spools after startup.
- **AMS Slot Configuration Overwrites RFID Spool State** — Configuring an AMS slot for an RFID-detected Bambu Lab spool sent `ams_set_filament_setting`, which replaced the firmware's RFID-managed filament config with a manual one — causing the slicer's "eye" icon to change to a "pen" icon. Now detects RFID spools and skips the filament setting command, only sending K-profile selection.
- **K-Profile Selection Corrupts Existing Profiles on X1C/P1S** — The `extrusion_cali_sel` command included a `setting_id` field that BambuStudio never sends, causing firmware to mislink calibration data. The `extrusion_cali_set` command was sent unconditionally, overwriting existing profile metadata. Now `setting_id` is removed from selection commands, and `extrusion_cali_set` is only sent when no existing profile is selected (`cali_idx < 0`).
- **AMS Slot Configure — Black Filament Color Not Pre-Populated** — When re-opening the Configure AMS Slot modal for a slot with black filament, the color field was empty despite the preset and K-profile being correctly pre-selected. The color pre-population logic excluded hex `000000` (black) as a guard against empty slots, but empty slots already skip color data entirely. Removed the unnecessary check so black is now pre-populated like any other color.
- **Archive List View Not Labeling Failed Prints** ([#365](https://github.com/maziggy/bambuddy/issues/365)) — The archive grid view displayed a red "Failed" / "Cancelled" badge on failed and aborted prints, but the list view had no equivalent indicator. Now shows an inline status badge next to the print name in list view.
- **Reprint Fails with SD Card Error for Archives Without 3MF File** ([#376](https://github.com/maziggy/bambuddy/issues/376)) — When a print was sent from an external slicer and Bambuddy couldn't download the 3MF from the printer during auto-archiving, the fallback archive had no file. Attempting to reprint such an archive tried to upload the data directory as a file, causing a confusing "SD card error." The backend now returns a clear error for file-less archives, and the frontend disables Print/Schedule/Open in Slicer buttons with a tooltip explaining that the 3MF file is unavailable.
- **Inventory Spool Weight Resets After Print Completes** — After a print, the usage tracker correctly updated `weight_used` (e.g., +1.6g), but periodic AMS status updates recalculated `weight_used` from the AMS remain% sensor and overwrote the precise value. For small prints on large spools (e.g., 1.6g on 1000g), the AMS remain% stays at 100% (integer resolution = 10g steps), resetting `weight_used` back to 0. The AMS weight sync now only increases `weight_used`, never decreases it, preserving precise values from the usage tracker.
- **All Spool Fill Levels Drop to Zero When Printers Power Off** — When a printer powers off, the AMS sensor can report `remain=0` for all trays while `tray_type` is still populated. The weight sync treated 0% remain as "100% consumed," computing `weight_used = label_weight` (e.g., 1000g). The "only increase" guard passed because `label_weight > current_used + 1`, marking every assigned spool as fully consumed. The AMS weight sync now skips `remain=0` entirely — a physically empty spool is tracked by the usage tracker during the print, not by a transient AMS sensor reading.
- **Spool Edit Form Overwrites Usage-Tracked Weight** — Editing any spool field (note, color, material, etc.) sent the full form data back to the server, including `weight_used`. If the frontend cache was stale (e.g., loaded before the last print completed), saving the form would silently reset `weight_used` to the pre-print value, reverting the remaining weight to full. The form now only includes `weight_used` in the update request when the user explicitly changes the weight field.
- **K-Profile Auto-Select Fails for Non-BL Spools on Dual-Nozzle Printers** — When assigning a third-party spool to an AMS slot on dual-nozzle printers (H2D, H2D Pro), the MQTT auto-configure step crashed with `'SpoolKProfile' object has no attribute 'extruder_id'`. The K-profile model uses `extruder` (not `extruder_id`). Fixed the attribute name so K-profile matching correctly filters by nozzle on dual-extruder printers.
- **Loose Archive Name Matching Could Cause Wrong Archive Reuse** ([#374](https://github.com/maziggy/bambuddy/issues/374)) — The `on_print_start` callback used `ilike('%{name}%')` to find existing "printing" archives, which meant a print named "Clip" could incorrectly match "Cable Clip" or "Clip Stand". This could cause a new print to reuse the wrong archive or skip creating one. Tightened to exact `print_name` match or exact filename variants (`.3mf`, `.gcode.3mf`).
- **Phantom Prints on Power Cycle** ([#374](https://github.com/maziggy/bambuddy/issues/374)) — The print queue uploaded `.3mf` files to the printer's SD card root (`/`) but never deleted them after the print finished. Some printers (e.g. P1S) auto-start files found in the root directory on power cycle, causing ghost prints on every reboot. Now deletes the uploaded file from the SD card after print completion (best-effort, non-blocking). The cleanup also tries `.gcode` files and retries up to 3 times with a 2-second delay to handle printers that briefly lock the filesystem after a print ends. Runs before the archive lookup so it works even when auto-archiving is disabled.
- **Queue Items Stuck in "Printing" After Print Completes** — The queue item status update (from `printing` to `completed`/`failed`) was placed after an early return that exits when the archive record cannot be found. If the archive lookup failed (e.g. app restart mid-print, manual archive deletion), the function returned early and the queue item stayed in `printing` forever. Over multiple print cycles, stale items accumulated — causing the "Printing" count to show double the actual printers and completed prints to remain in the "Currently Printing" section. Moved the queue item status update (including MQTT relay notification, queue-completed notification, and auto-power-off) to before the archive lookup early return so it always runs.
- **Spool Form Scrollbar Flicker in Edge** ([#364](https://github.com/maziggy/bambuddy/issues/364)) — The Add/Edit Spool modal's scrollable area used `overflow-y: auto`, which on Windows Edge (where scrollbars take layout space) caused the scrollbar to appear and disappear on hover — making the color picker unusable at certain zoom levels. Added `scrollbar-gutter: stable` to reserve scrollbar space and prevent layout thrashing.
- **Archive Duplicate Badge Misses Name-Based Duplicates** ([#315](https://github.com/maziggy/bambuddy/issues/315)) — The duplicate badge on archive cards only matched by file content hash, so re-sliced prints of the same model (different GCODE, same print name) were not flagged as duplicates. Now also matches by print name (case-insensitive), consistent with the detail view's duplicate detection.
- **Schedule Print Allows No Plate Selected for Multi-Plate Files** ([#394](https://github.com/maziggy/bambuddy/issues/394)) — When scheduling a multi-plate file from the file manager, the modal showed a "Selection required" warning but still allowed submission without selecting a plate. The job defaulted to plate 1, but the queue item didn't indicate which plate, and editing showed no plate selected. Now auto-selects the first plate by default when plates load, and the submit button validation applies to both archive and library files.
- **3MF Usage Tracking Broken for Queue Prints from File Manager** ([#364](https://github.com/maziggy/bambuddy/issues/364)) — When a print was queued from the file manager (library file), the scheduler did not create an archive or register the expected print. The `on_print_start` callback had to re-download the 3MF from the printer via FTP, and if that failed, a fallback archive was created without the 3MF file — making 3MF-based filament usage tracking impossible. The queue item's `archive_id` also remained NULL, so the usage tracker could not find the queue's AMS slot mapping for correct spool resolution. The scheduler now creates an archive from the library file before uploading, links it to the queue item, and registers it as an expected print — matching the behavior of the direct library print route.
- **Printer Queue Widget Shows "Archive #null" for File Manager Prints** ([#364](https://github.com/maziggy/bambuddy/issues/364)) — The "Next in queue" widget on the printer card only checked `archive_name` and `archive_id` when displaying the queued item name. Queue items from the file manager have `library_file_name` and `library_file_id` instead, so the widget displayed "Archive #null". Now falls back to `library_file_name` and `library_file_id`, matching the Queue page display logic.
- **Inventory Usage Not Tracked for Remapped AMS Slots** ([#364](https://github.com/maziggy/bambuddy/issues/364)) — When reprinting an archive with a different AMS slot mapping (e.g. changing from slot A1 to C4 in the mapping modal), the usage tracker used the default 3MF slot-to-tray mapping instead of the actual mapping from the print command. The `ams_mapping` from reprint, library print, and queue print commands is now stored and used as the highest-priority mapping source for usage tracking.
- **Inventory Usage Not Tracked for Slicer-Initiated Prints on H2D** ([#364](https://github.com/maziggy/bambuddy/issues/364)) — On H2D printers, the AMS `tray_now` field is always 255 in MQTT data. The actual tray is resolved via the snow field ~44 seconds after print start, but reverts to "unloaded" when the AMS retracts filament at completion. The usage tracker now tracks `last_loaded_tray` — the last valid tray seen during printing — as a fallback when both `tray_now` at start and at completion are invalid. Also captures `tray_now` at print start for printers that report a valid value before the RUNNING state.
- **Inventory Usage Wrong Tray for Slicer-Initiated Prints** ([#364](https://github.com/maziggy/bambuddy/issues/364)) — When a print was started from an external slicer (BambuStudio, OrcaSlicer, Bambu Handy), Bambuddy never saw the `ams_mapping` the slicer sent, because it only subscribed to the printer's report topic. The usage tracker fell back to `tray_now` which could resolve to the wrong AMS tray (e.g., Black PLA at A2 instead of Green PLA at A4 on H2D Pro). Now subscribes to the MQTT request topic to intercept print commands from any source, capturing the `ams_mapping` universally — regardless of who starts the print. The request topic subscription is fail-safe: if the printer's MQTT broker rejects it (e.g., P1S), Bambuddy detects the rejection via SUBACK or disconnect timing and gracefully disables the subscription for that printer, falling back to the existing `tray_now`-based tracking without breaking the MQTT connection.
- **P1S Timelapse Not Detected — AVI Format Support** ([#405](https://github.com/maziggy/bambuddy/issues/405)) — P1-series printers save timelapse videos as `.avi` (MJPEG), but the timelapse scanner only looked for `.mp4` files — so P1S timelapses were never found or attached to archives. Now discovers both `.mp4` and `.avi` timelapse files across all FTP directories (`/timelapse`, `/timelapse/video`, `/record`, `/recording`). AVI files are saved immediately and converted to MP4 in a non-blocking background task using FFmpeg with `-threads 1` and `nice -n 19` to minimize CPU impact on Raspberry Pi. If FFmpeg is unavailable, the AVI is served as-is with the correct MIME type. The manual "Scan for Timelapse" route also searches the additional directories used by P1-series printers.
- **Timelapse Upload & Remove** ([#406](https://github.com/maziggy/bambuddy/issues/406)) — When the auto-scan attaches the wrong timelapse (e.g., from a different print), there was no way to remove it or attach the correct one. Added "Upload Timelapse" and "Remove Timelapse" context menu items. Upload accepts `.mp4`, `.avi`, and `.mkv` files (non-MP4 auto-converted in background). Remove deletes the file and clears the database reference. Both actions are permission-gated and available in grid and list views.
- **Spool Assignments Falsely Unlinked After Print Due to Color Variation** — The auto-unlink logic compared AMS tray colors against saved fingerprints using exact hex match. RFID sensors report slightly different color values across reads (e.g. `7CC4D5FF` vs `56B7E6FF` for the same spool, Euclidean distance ~43.6). Now uses a color similarity function with a tolerance threshold of 50, preventing false unlinks from minor RFID/firmware color variations while still detecting genuinely different spools.

### Improved
- **Virtual Printer: Dual Bind/Detect Ports 3000 + 3002** ([#445](https://github.com/maziggy/bambuddy/issues/445)) — BambuStudio/OrcaSlicer require a bind/detect handshake before connecting via MQTT/FTP. Different slicer versions use port 3000 or 3002, so the BindServer and proxy now listen on both ports for full compatibility. Docker users in bridge mode need to expose both (`-p 3000:3000 -p 3002:3002`).
- **Usage Tracking Diagnostic Logging** ([#364](https://github.com/maziggy/bambuddy/issues/364)) — Added INFO-level logging at print start and completion that dumps the printer's MQTT `mapping` field, `tray_now`, `last_loaded_tray`, all mapping-related raw data keys, and per-AMS-tray summaries (type, color, tray_now, tray_tar). Enables investigating the slot-to-tray mapping behavior across different printer models (X1E, H2D Pro, P1S, etc.) without requiring DEBUG mode.
- **Skip Objects: Click-to-Enlarge Lightbox** ([#396](https://github.com/maziggy/bambuddy/issues/396)) — The skip objects modal's small 208px image panel made it difficult to distinguish object markers when parts are small or close together. Clicking the image now opens a fullscreen lightbox overlay with the same image and markers at a much larger size (up to 600px). The 24px marker circles are proportionally smaller relative to the enlarged image, solving the overlap problem. Close via X button, Escape key, or clicking the backdrop. Escape cascades correctly — closes lightbox first, then the modal.
- **Phantom Print Investigation — Logging & Hardening** ([#374](https://github.com/maziggy/bambuddy/issues/374)) — Added targeted logging and hardening to help diagnose reports of prints starting automatically without user input. Debug log volume reduced ~90% by suppressing `sqlalchemy.engine` (changed from INFO to WARNING) and `aiosqlite` (new WARNING suppression) noise that previously filled 2.5MB in 16 minutes. Every `start_print()` call now logs a `PRINT COMMAND` trace with the caller's file, line, and function name. The print scheduler logs pending queue items when found. `on_print_complete` warns when multiple queue items are in "printing" status for the same printer, which signals a state inconsistency.
- **Reduce Log Noise from MQTT Diagnostics** ([#365](https://github.com/maziggy/bambuddy/issues/365)) — Downgraded 58 high-frequency MQTT diagnostic messages from INFO to DEBUG level. Payload dumps, detector state changes, field discovery logs, H2D disambiguation, and periodic status updates no longer flood the log at the default INFO level. Also suppresses paho-mqtt library INFO messages in production. User-initiated actions (print start/stop, AMS load/unload, calibration) remain at INFO. All diagnostic detail is still available when debug logging is enabled.
- **SQLite WAL Mode for Database Reliability** — Database now uses Write-Ahead Logging (WAL) mode with a 5-second busy timeout, reducing "database is locked" errors under concurrent access. WAL mode allows simultaneous reads during writes, improving responsiveness for multi-printer setups. Automatically enabled on startup.
- **External Camera Not Used for Snapshot + Stream Dropping** ([#325](https://github.com/maziggy/bambuddy/issues/325)) — The snapshot endpoint (`/camera/snapshot`) always used the internal printer camera even when an external camera was configured. Now checks for external camera first, matching the existing stream endpoint behavior. Also fixed external MJPEG and RTSP streams silently dropping every ~60 seconds due to missing reconnect logic — the underlying stream generators exit on read timeout, and the caller now retries up to 3 times with a 2-second delay instead of ending the stream.
- **H2C Nozzle Rack Text Unreadable on Light Filament Colors** ([#300](https://github.com/maziggy/bambuddy/issues/300)) — Nozzle rack slots use the loaded filament color as background, but white/light filaments made the white "0.4" text nearly invisible. Now uses a luminance check to switch to dark text on light backgrounds.
- **File Downloads Show Generic Filenames** ([#334](https://github.com/maziggy/bambuddy/issues/334)) — Downloaded files with special characters in their names (spaces, umlauts, parentheses) were saved as generic `file_1`, `file_2` instead of the original filename. The `Content-Disposition` header parser now handles RFC 5987 percent-encoded filenames (`filename*=utf-8''...`) used by FastAPI for non-ASCII characters. Fix applied to all download endpoints (library files, archives, source files, F3D files, project exports, support bundles, printer files).
- **Printer Card Cover Image Not Updating Between Prints** — The cover image on the printer card only refreshed on page reload. The `<img>` URL was always the same (`/printers/{id}/cover`) regardless of which print was active, so the browser served its cached image. Now appends the print name as a cache-busting query parameter so the browser fetches the new cover when a different print starts.
- **Telegram Bold Title Broken by Underscores in Message** ([#332](https://github.com/maziggy/bambuddy/issues/332)) — Telegram notifications showed literal `*Title*` asterisks instead of bold text when the message body contained underscores (e.g. job name `A1_plate_8`, error code `0300_0001`). The code was disabling Markdown parsing entirely when underscores were detected. Now escapes underscores in the body with `\_` so Markdown rendering stays enabled.
- **Queued Jobs Incorrectly Archived After Duplicate Execution Detection** ([#341](https://github.com/maziggy/bambuddy/issues/341)) — When the same file was added to the print queue multiple times, only the first job executed. All subsequent jobs were automatically skipped with "already printed X hours ago" because they shared the same archive reference, and a safety check incorrectly treated them as phantom reprints. The same issue also affected single queue items created from recently completed archives. Removed the overly broad 4-hour duplicate detection check — the crash recovery scenario it guarded against is already handled by the queue item status lifecycle.

### New Features
- **External Links: Open in New Tab** ([#338](https://github.com/maziggy/bambuddy/issues/338)) — External sidebar links can now optionally open in a new browser tab instead of an iframe. Sites behind reverse proxies (Traefik, nginx) that send `X-Frame-Options: SAMEORIGIN` or CSP `frame-ancestors` headers block iframe embedding, causing "refused to connect" errors. A new "Open in new tab" toggle in the add/edit link modal lets users choose per-link. Keyboard shortcuts (number keys) also respect the setting. Defaults to iframe (existing behavior) for backward compatibility.
- **Print Queue: Clear Plate Confirmation** — When a print finishes or fails and more items are queued, the printer card now shows a "Clear Plate & Start Next" button. The scheduler no longer auto-starts the next print while the printer is in FINISH or FAILED state — the user must confirm the build plate has been cleared first. This prevents prints from starting on a dirty plate. The button respects the `printers:control` permission and is available in all supported languages (en/de/ja).
- **Clear Plate State Persists Across Page Refresh** ([#410](https://github.com/maziggy/bambuddy/issues/410)) — After clicking "Clear Plate & Start Next", refreshing the page showed the Clear Plate button again because the frontend determined the state purely from the printer's FINISH/FAILED status. The `plate_cleared` flag is now included in the printer status API response, so the widget correctly shows the passive queue link instead of the Clear Plate button after acknowledgment — even after a page refresh.

### Improved
- **Skip Objects: Confirmation Dialog** ([#346](https://github.com/maziggy/bambuddy/issues/346)) — Added a warning confirmation modal before skipping an object during a print. Shows the object name and warns the action is irreversible. Prevents accidentally skipping the wrong object. Translated in all 4 locales (en, de, ja, it).
- **Additional Currency Options** ([#329](https://github.com/maziggy/bambuddy/issues/329), [#333](https://github.com/maziggy/bambuddy/issues/333)) — Added 17 additional currencies to the cost tracking dropdown: HKD, INR, KRW, SEK, NOK, DKK, PLN, BRL, TWD, SGD, NZD, MXN, CZK, THB, ZAR, RUB.
- **Move Email Settings Under Authentication Tab** — Renamed the settings "Users" tab to "Authentication" and moved the standalone "Global Email" tab into it as an "Email Authentication" sub-tab. Groups email/SMTP configuration with user management where it logically belongs. Legacy `?tab=email` URLs are handled automatically.
- **Inventory — Confirmation Modals for Delete & Archive** — The inventory page now uses the app's styled confirmation modal for both delete and archive actions. Previously, delete used the browser's native `confirm()` dialog and archive had no confirmation at all. Delete shows a danger-styled modal, archive shows a warning-styled modal. Translated in all 5 locales (en, de, fr, it, ja).
- **Default Color Catalog Expanded to 638 Colors Across 20 Brands** — The built-in filament color catalog has been expanded from 258 entries (6 brands) to 638 entries (20 brands). Added Overture, Sunlu, Creality, Elegoo, Jayo, Inland, Eryone, ColorFabb, Fillamentum, FormFutura, Fiberlogy, MatterHackers, Protopasta, 3DXTECH, and Sakata3D. eSUN expanded from 10 generic placeholder entries to 79 measured colors across 10 material lines (PLA+, Pro PLA+, PLA, PLA Silk, PLA Metal, PLA-ST, PETG, PETG-HS, ABS, ABS+). All hex codes sourced from FilamentColors.xyz measured swatches.
- **Settings — Built-in Inventory Feature Note** — Added a note in Settings > Filament > Built-in Inventory that third-party spools can be assigned to inventory spools for tracking.
- **Catalog Settings Cards Taller** — Spool Catalog and Color Catalog settings panels increased from 400px to 600px max height for better browsability with the expanded default catalogs.

## [0.1.9] - 2026-02-10

### New Features
- **Advanced Authentication via Email** ([#322](https://github.com/maziggy/bambuddy/pull/322)) — Optional SMTP-based email integration for streamlined user onboarding and self-service password management. Admins configure SMTP settings and create users with just a username and email — the system generates a secure random password and emails it directly to the new user. Admins can trigger one-click password resets from User Management. Users can reset their own forgotten password from the login screen without contacting an admin. Includes customizable email templates for welcome emails and password resets. Username and email login is case-insensitive. Can be enabled or disabled independently at any time without affecting existing accounts.
- **Configurable Slicer Preference** ([#313](https://github.com/maziggy/bambuddy/issues/313)) — New "Preferred Slicer" setting in General settings to choose between Bambu Studio and OrcaSlicer. Controls the protocol used by all "Open in Slicer" buttons across Archives, 3D Preview, and context menus. OrcaSlicer uses the `orcaslicer://open?file=` protocol. Default remains Bambu Studio for backward compatibility.
- **Local Profiles — OrcaSlicer Import** ([#310](https://github.com/maziggy/bambuddy/issues/310)) — Import slicer presets from OrcaSlicer without Bambu Cloud. Supports `.orca_filament`, `.bbscfg`, `.bbsflmt`, `.zip`, and `.json` exports. Resolves OrcaSlicer inheritance chains by fetching base Bambu profiles from GitHub (cached locally with 7-day TTL). Stores presets in the database with extracted core fields (material type, vendor, nozzle temps, pressure advance, compatible printers). New "Local Profiles" tab on the Profiles page with drag-and-drop import, 3-column layout (Filament/Process/Printer), search, and expandable preset details. Local filament presets appear in AMS slot configuration alongside cloud presets. Includes smart profile type detection (explicit type field, ZIP path hints, settings ID keys, content heuristics, and name-based patterns) and material/vendor extraction from preset names as fallback.
- **Hostname Support for Printers** ([#290](https://github.com/maziggy/bambuddy/issues/290)) — Printers can now be added using hostnames (e.g., `printer.local`, `my-printer.home.lan`) in addition to IPv4 addresses. Updated backend validation, frontend forms, and all locale labels.
- **Camera View Controls** ([#291](https://github.com/maziggy/bambuddy/issues/291)) — Added chamber light toggle and skip objects buttons to both embedded camera viewer and standalone camera page. Extracted skip objects modal into a reusable `SkipObjectsModal` component shared across PrintersPage and both camera views.
- **Per-Filament Spoolman Usage Tracking** ([#277](https://github.com/maziggy/bambuddy/pull/277)) — Accurate per-filament usage tracking for Spoolman integration with G-code parsing. Parses 3MF files at print start to build per-layer, per-filament extrusion maps. Reports accurate partial usage when prints fail or are cancelled based on actual layer progress. Tracking data stored in database to survive server restarts. Uses Spoolman's filament density for mm-to-grams conversion. Prefers `tray_uuid` over `tag_uid` for spool identification.
- **Disable AMS Weight Sync Setting** ([#277](https://github.com/maziggy/bambuddy/pull/277)) — New toggle to prevent AMS percentage-based weight estimates from overwriting Spoolman's granular usage-based calculations. Includes conditional "Report Partial Usage for Failed Prints" toggle.
- **Home Assistant Environment Variables** ([#283](https://github.com/maziggy/bambuddy/issues/283)) — Configure Home Assistant integration via `HA_URL` and `HA_TOKEN` environment variables for zero-configuration add-on deployments. Auto-enables when both variables are set. UI fields become read-only with lock icons when env-managed. Database values preserved as fallback.
- **Spoolman Fill Level for AMS Lite / External Spools** ([#293](https://github.com/maziggy/bambuddy/issues/293)) — AMS Lite (no weight sensor) always reported 0% fill level. Now uses Spoolman's remaining weight as a fallback when AMS reports 0%. External spools also show fill level from Spoolman data. Fill bars and hover cards indicate "(Spoolman)" when the data source is Spoolman rather than AMS.
- **Extended Support Bundle Diagnostics** — Support bundle now collects comprehensive diagnostic data for faster issue resolution: printer connectivity and firmware versions, integration status (Spoolman, MQTT, Home Assistant), network interfaces (subnets only), Python package versions, database health checks, Docker environment details, WebSocket connections, and log file info. All data properly anonymized — no IPs, names, or serials included. Privacy disclosure updated on System Info page.

### Improved
- **H2C Nozzle Rack — 6-Slot Display With Empty Placeholders** ([#300](https://github.com/maziggy/bambuddy/issues/300)) — The nozzle rack card now always shows 6 rack positions (IDs 16–21), with filled slots showing diameter and empty slots showing placeholder dashes. L/R hotend nozzles (IDs 0, 1) are excluded from the rack card and shown in the dedicated L/R indicator instead.
- **H2 Series — L/R Nozzle Hover Card** ([#300](https://github.com/maziggy/bambuddy/issues/300)) — New dual-nozzle hover card shows L and R nozzle details side by side (diameter, type, flow, status, wear, max temp, serial). Active nozzle highlighted in amber with Active/Idle status based on `active_extruder`, replacing the misleading "Docked" label.
- **H2 Series — Single-Nozzle Hover Card** ([#300](https://github.com/maziggy/bambuddy/issues/300)) — H2D/H2S printers with a single nozzle now show extended nozzle details (wear, serial, max temp) on hover over the temperature card. Backend changed from H2C-only (>2 nozzles) to all H2 series (any nozzle_info present).
- **H2C Nozzle Rack — Translate Type Codes & Add Flow Info** ([#300](https://github.com/maziggy/bambuddy/issues/300)) — Raw nozzle type codes (e.g. "HS", "HH01") are now translated to human-readable names: material (Hardened Steel, Stainless Steel, Tungsten Carbide) and flow type (High Flow, Standard). New "Flow" row in the hover card. Translations added in all 4 locales (en, de, ja, it).
- **H2C Nozzle Rack — Show Filament Material in Hover Card** ([#300](https://github.com/maziggy/bambuddy/issues/300)) — Nozzle hover card now shows the loaded filament material type (e.g. "PLA", "PETG") alongside the color swatch, captured from MQTT nozzle info data.
- **H2C Nozzle Rack — Resolve Filament Names From Cloud & Local Profiles** ([#300](https://github.com/maziggy/bambuddy/issues/300)) — Nozzle rack hover card previously showed raw filament IDs like "GFU99" instead of human-readable names. Now resolves filament names with a 4-tier fallback: Bambu Cloud preset lookup → local slicer profiles → built-in filament name table (86 known Bambu filament codes) → raw ID fallback. The built-in table resolves names like "Bambu ASA", "Generic TPU", "Generic PLA" when the cloud API returns 400 for certain filament IDs. Also benefits AMS tray tooltips.
- **H2C Nozzle Rack Compact Layout** ([#300](https://github.com/maziggy/bambuddy/issues/300)) — Redesigned nozzle rack from a 2×3 grid to a compact single-row layout with bottom accent bars (green = mounted, gray = docked). Temperature cards are thinner, rack card is wider (flex-[2]), and all cards vertically centered.
- **Firmware Version Badge on Printer Card** ([#311](https://github.com/maziggy/bambuddy/issues/311)) — Printer cards now show a firmware version badge (when firmware checking is enabled). Green with checkmark when up to date, orange with download icon when an update is available. Clicking the badge opens a firmware info modal showing release notes (auto-expanded when up to date) or the existing update workflow. Badge and modal respect `firmware:read` and `firmware:update` permissions. Translations added in all 4 locales.
- **Auto-Detect Subnet for Printer Discovery** — Docker users no longer need to manually enter a subnet in the Add Printer dialog. Bambuddy auto-detects available network subnets and pre-selects the first one. When multiple subnets are available (e.g., eth0 + wlan0), a dropdown lets users choose. Falls back to manual text input if no subnets are detected.
- **Japanese Locale Complete Overhaul** — Restructured `ja.ts` from a divergent format (different key structure, 12 structural conflicts, 1,366 missing translations) to match the English/German locale structure exactly. Translated all 2,083 keys into Japanese, achieving full parity with EN/DE. Zero structural divergences, zero missing keys.

### Fixed
- **Nozzle Rack Hides 0% Wear** ([#300](https://github.com/maziggy/bambuddy/issues/300)) — New nozzles with 0% wear showed no wear info in the hover card because the condition treated 0 the same as "not available." Now displays "Wear: 0%" correctly. The field is still hidden when the printer doesn't report wear data.
- **Nozzle Rack Shows L/R Hotend Nozzles in Rack** ([#300](https://github.com/maziggy/bambuddy/issues/300)) — The nozzle rack card incorrectly included L/R hotend nozzles (IDs 0, 1) alongside the 6 rack slots. Now filters to IDs >= 2 (rack only) and always pads to 6 positions with empty placeholders.
- **H2C Firmware Update Downloads Wrong Firmware** ([#311](https://github.com/maziggy/bambuddy/issues/311)) — H2C printers were mapped to the H2D firmware API key (`h2d`), causing firmware checks to offer H2D firmware instead of H2C firmware. H2C has its own firmware track (01.01.x.x vs H2D's 01.02.x.x). Added separate `h2c` API key mapping. Also added missing H2C/H2S entries to printer model ID and 3MF model maps.
- **Sidebar Links Custom Icons Have Inverted Colors** ([#308](https://github.com/maziggy/bambuddy/issues/308)) — Custom uploaded icons in sidebar links had their colors inverted in dark mode due to a CSS `invert()` filter. The filter was intended for monochrome preset icons but was incorrectly applied to user-uploaded images (e.g., full-color logos). Removed the invert filter from custom icon rendering in the sidebar and the add/edit link modal.
- **Virtual Printer FTP Transfer Fails With Connection Reset** ([#58](https://github.com/maziggy/bambuddy/issues/58)) — Large 3MF uploads to the virtual printer intermittently failed with `[Errno 104] Connection reset by peer` while the small verify_job always succeeded. The `_handle_data_connection` callback returned immediately, allowing the asyncio server-handler task to complete while the data connection was still in active use. The passive port listener also stayed open during transfers, risking duplicate data connections. Fixed by keeping the callback alive until the transfer completes (`_transfer_done` event), closing the passive listener after accepting the connection, and rejecting duplicate data connections. Also added a 5-second drain timeout to MQTT status pushes to prevent blocking when the slicer is busy uploading.
- **Virtual Printer IP Override for Server Mode** ([#52](https://github.com/maziggy/bambuddy/issues/52)) — The `remote_interface_ip` setting (network interface override) was only used in proxy mode, but users with multiple network interfaces (LAN + Tailscale, Docker bridges) also needed it in server modes (immediate/review/print_queue). Auto-detected IP from `_get_local_ip()` followed the OS default route, causing wrong IP in TLS certificate SAN (handshake failures) and SSDP broadcasts (slicer can't discover printer). Now the interface override applies to all modes: included in certificate SAN, passed to SSDP server as advertise IP, and triggers service restart on change. UI dropdown shown for all modes when enabled (not just proxy).
- **Wrong Thumbnail When Reprinting Same Project** ([#314](https://github.com/maziggy/bambuddy/issues/314)) — Reprinting a project with the same name but a different bed layout showed the old thumbnail during printing. The cover image cache was keyed by `subtask_name` and never invalidated between prints, so a cache hit returned the stale first-print thumbnail. Now the cover cache is cleared on every print start.
- **Wrong Timelapse Attached to Archive** ([#315](https://github.com/maziggy/bambuddy/issues/315)) — After a print, the archive could receive a timelapse from a previous print instead of the just-completed one. The auto-scan sorted MP4 files by mtime and grabbed the "most recent," but in LAN-only mode (no NTP) the printer's clock is wrong, making mtime unreliable. Replaced with a snapshot-diff approach: baseline existing files before waiting, then detect the new file that appears after encoding. Falls back to print-name matching if no new file is found after retries.
- **Timelapse Not Attached — Baseline Race Condition** ([#315](https://github.com/maziggy/bambuddy/issues/315)) — Follow-up to the snapshot-diff timelapse fix: the baseline of existing MP4 files was captured at print completion time inside a background task, but fast-encoding printers could finish writing the timelapse before the baseline was taken, causing the new file to appear in the baseline and never be detected as "new." Moved baseline capture to print start time, when the timelapse file cannot possibly exist yet. Falls back to completion-time baseline if the app was restarted mid-print.
- **Calibration Prints Archived** ([#315](https://github.com/maziggy/bambuddy/issues/315)) — Standalone calibration prints (flow, vibration, bed leveling) were being archived as regular prints. The calibration gcode (`/usr/etc/print/auto_cali_for_user.gcode`) and other internal printer files under `/usr/` are now detected and skipped during print start.
- **Camera Stop 401 When Auth Enabled** — Camera stop requests (`sendBeacon`) failed with 401 Unauthorized when authentication was enabled because `sendBeacon` cannot send auth headers. Replaced with `fetch` + `keepalive: true` which supports Authorization headers while remaining reliable during page unload.
- **Spoolman Creates Duplicate Spools on Startup** ([#295](https://github.com/maziggy/bambuddy/pull/295)) — Each AMS tray independently fetched all spools from Spoolman, causing redundant API calls and duplicate spool creation with large databases (300+ spools). Now fetches spools once and reuses cached data across all tray operations. Added retry logic (3 attempts, 500ms delay) with connection recreation for transient network errors.
- **Filament Usage Charts Inflated by Quantity Multiplier** ([#229](https://github.com/maziggy/bambuddy/issues/229)) — Daily, weekly, and filament-type charts were multiplying `filament_used_grams` by print quantity, even though the value already represents the total for the entire job. A 26-object print using 126g was counted as 3,276g. Removed the erroneous multiplier from three aggregations in `FilamentTrends.tsx`.
- **Energy Cost Shows 0.00 in "Total Consumption" Mode** ([#284](https://github.com/maziggy/bambuddy/issues/284)) — Statistics Quick Stats showed 0.00 energy cost when Energy Display Mode was set to "Total Consumption" with Home Assistant smart plugs. The `homeassistant_service` was not configured with HA URL/token before querying plug energy data, causing it to silently return nothing.
- **H2D Pro Prints Fail at ~75% With Extrusion Motor Overload** ([#245](https://github.com/maziggy/bambuddy/issues/245)) — H2D Pro firmware interprets `use_ams: 1` (integer) as a nozzle index, routing filament to the deputy nozzle instead of the main nozzle. Bambu Studio sends `use_ams: true` (boolean) while using integers for other fields. Fixed by keeping `use_ams` as boolean for all printers including H2D series.
- **GitHub Backup Description Misleading** — The "App Settings" backup card said "excludes sensitive data" but the complete database is pushed. Updated description to "complete database."
- **Support Bundle Shows 0 AMS Units** — The support info always reported `ams_unit_count: 0` because it expected `raw_data["ams"]` to be a nested dict (`{"ams": [...]}`) but the MQTT handler stores it as a flat list. Now handles both formats.
- **Firmware Badge Shown for Models Without API Data** ([#311](https://github.com/maziggy/bambuddy/issues/311)) — Printers whose model has no firmware data in Bambu Lab's API (e.g. H2C on public beta firmware) showed a misleading green "up to date" badge. The badge is now hidden when the API returns no `latest_version`, since there is nothing to compare against.
- **AMS-HT Mapping Fails for Left Nozzle on H2D Pro** ([#318](https://github.com/maziggy/bambuddy/issues/318)) — Printing with the left nozzle on dual-nozzle printers (H2D/H2D Pro) using AMS-HT failed with "Failed to get AMS mapping table." The global tray ID for AMS-HT units (ams_id >= 128) was calculated as `ams_id * 4 + tray_id` (= 512), but AMS-HT uses the raw `ams_id` (128) since it has a single tray. The backend then misidentified 512 as an external spool. Fixed in frontend tray ID calculation, backend `ams_mapping2` builder, print scheduler, and Spoolman tracking.
- **H2D Pro L/R Nozzle Hover Card Swapped** ([#300](https://github.com/maziggy/bambuddy/issues/300)) — The dual-nozzle hover card had left and right nozzles swapped: nozzle_rack id 0 (extruder 0 = right) was shown as left and vice versa. Serial number and max temp now correctly appear only on the right (removable) nozzle column.
- **H2C Printer Card Shows H2D Image** ([#300](https://github.com/maziggy/bambuddy/issues/300)) — The H2C printer card displayed the H2D printer image because no dedicated H2C image existed in the frontend. Added H2C image and updated `getPrinterImage()` to return it for H2C models.
- **H2C Nozzle Rack Shows Wrong Empty Slot and Missing Filament Colors** ([#300](https://github.com/maziggy/bambuddy/issues/300)) — Empty rack slots always appeared at position 6 instead of their actual position because nozzles were mapped by array index instead of by ID. Fixed by mapping each nozzle to its correct rack position (`id - 16`). Filament colors and materials were missing because the H2C uses different MQTT field names (`color_m`, `fila_id`, `sn`, `tm`) than the H2D (`filament_colour`, `filament_id`, `serial_number`, `max_temp`). Added fallback field name resolution. Also fixed nozzle rack layout breaking on medium card size by allowing the temperature row to wrap.

### Documentation
- **Advanced Auth via Email** — Updated README, website features page, and wiki authentication guide with SMTP setup, self-service password reset, admin password reset, email templates, and advanced auth overview.
- **Supported Printers Updated** — Updated README, website, and wiki to list all 12 supported Bambu Lab printer models: X1, X1C, X1E, P1P, P1S, P2S, A1, A1 Mini, H2D, H2D Pro, H2C, H2S. Removed outdated "Testers Needed" messaging and Tested/Needs Testing distinctions — all models are now uniformly listed as supported. Added H2C printer image to website. Added H2D Pro, H2C columns to wiki feature comparison tables and new P2 Series section.
- **CONTRIBUTING.md: i18n & Authentication Guides** — Added Internationalization (i18n) section with locale file conventions, code examples, and parity rules. Added Authentication & Permissions section covering the opt-in auth pattern, permission conventions, and default group structure.
- **Proxy Mode Security Warning** — Added FTP data channel security warning to wiki, README, and website. Bambu Studio does not encrypt the FTP data channel despite negotiating PROT P; MQTT and FTP control channels are fully TLS-encrypted. VPN (Tailscale/WireGuard) recommended for full data encryption.
- **Docker Proxy Mode Ports** — Documented FTP passive data ports 50000-50100 required for proxy mode in Docker bridge mode. Updated port mappings in wiki virtual-printer and docker guides.
- **SSDP Discovery Limitations** — Added table showing when SSDP discovery works (same LAN, dual-homed, Docker host mode) vs when manual IP entry is required (VPN, Docker bridge, port forwarding). Updated wiki, README, and website.
- **Firewall Rules Updated** — Added port 50000-50100/tcp to all UFW, firewalld, and iptables examples for proxy mode FTP passive data.

### Testing
- **Mock FTPS Server & Comprehensive FTP Test Suite** — Added 67 automated test cases against a real implicit FTPS mock server, covering every known FTP failure mode from 0.1.8+:
  - Mock server (`mock_ftp_server.py`) implements implicit TLS, custom AVBL command, and per-command failure injection
  - Connection tests: auth, SSL modes (prot_p/prot_c), timeout, cache, disconnect edge cases
  - Upload tests: chunked transfer via `transfercmd()`, progress callbacks, 553/550/552 error handling
  - Download tests: bytes, to-file, 0-byte regression, large files, missing file cleanup
  - Model-specific tests: X1C session reuse, A1/A1 Mini prot_c fallback, P1S, unknown model defaults
  - Async wrapper tests: upload/download/list/delete with A1 fallback and multi-path download
  - Failure injection tests: regressions for `error_perm` hierarchy, `diagnose_storage` CWD propagation, injection count decrement
  - Added `pyOpenSSL` to `requirements-dev.txt` for Docker test image compatibility
- **Nozzle Rack Tests** — Backend: 7 tests for MQTT nozzle_info parsing (H2C 8-entry, H2D 2-entry, H2S single, empty, sorting, field mapping, nozzle state updates). Frontend: 3 tests for rack card rendering (H2C shows 6 slots, empty placeholders, hidden when no rack IDs).

## [0.1.8.1] - 2026-02-07

### Fixed
- **FTP Upload Broken on All Printer Models** — Fixed critical bug where all FTP uploads failed with "550 Failed to change directory":
  - `diagnose_storage()` was running before every upload, and its CWD failures (`ftplib.error_perm`) were not caught because `error_perm` is not a subclass of `error_reply`
  - Removed `diagnose_storage()` from the upload hot path
  - Changed all FTP exception handlers from `except (OSError, ftplib.error_reply)` to `except (OSError, ftplib.Error)` to catch all FTP error types
- **HTTP 500 on Reprint and Print Endpoints** — Fixed 500 errors on `/api/v1/archives/{id}/reprint` and `/api/v1/library/files/{id}/print` caused by the FTP failure above
- **Exception Handling Reverted** — Reverted overly-narrow exception handling introduced in 0.1.8 that could cause uncaught errors in archive parsing, HTTP clients, 3MF/ZIP processing, Home Assistant, and firmware checks
- **HTTP 500 on Printer Cover Image** — Fixed 500 error on `/api/v1/printers/{id}/cover` when FTP download returned 0 bytes but reported success; now retries and falls back to 404
- **4-Segment Version Support** — Version parser now supports patch releases like `0.1.8.1` for hotfixes without incrementing the minor version

## [0.1.8] - 2026-02-06

### Security
- **XML External Entity (XXE) Prevention**:
  - Replaced `xml.etree.ElementTree` with `defusedxml` across all 3MF parsing code
  - Prevents XXE attacks through malicious 3MF files
  - Detected by Bandit B314 security scanner
- **Path Injection Vulnerabilities Fixed**:
  - Added path traversal validation to project attachment endpoints
  - Strengthened filename sanitization in timelapse processing
  - Prevents directory traversal attacks via `../` sequences
  - Detected by CodeQL security scanner
- **Security Scanning in CI/CD**:
  - Added Bandit (Python security analyzer) with SARIF upload to GitHub Security
  - Added Trivy (container/IaC scanner) for Docker image and Dockerfile analysis
  - Added pip-audit and npm-audit for dependency vulnerability scanning
  - Automatic GitHub issue creation for detected vulnerabilities
  - Security scan results visible in GitHub Security tab
- **CodeQL Zero-Finding Baseline**:
  - Reduced CodeQL findings from 591 to 0 across Python, JavaScript, and GitHub Actions
  - Created custom query suites (`.codeql/python-bambuddy.qls`, `.codeql/javascript-bambuddy.qls`) with documented accepted-risk exclusions
  - All exclusions reviewed and justified (log injection parameterized, cyclic imports from SQLAlchemy ORM, intentional 0.0.0.0 binds, etc.)
- **Log Injection Prevention**:
  - Converted ~700 f-string log calls to parameterized `%s` style across all backend files
  - Prevents log injection via newlines or fake log entries in user-controlled data
- **Exception Handling Hardened**:
  - Narrowed ~265 bare `except Exception` blocks to specific types (`OSError`, `KeyError`, `ValueError`, `zipfile.BadZipFile`, `sqlalchemy.exc.OperationalError`, etc.)
- **Stack Trace Exposure Fixed**:
  - Replaced `str(e)` with generic error messages in HTTP responses (`updates.py`)
  - Detailed errors still logged server-side for debugging
- **SSRF Mitigations Added**:
  - Home Assistant integration: URL scheme/hostname validation, metadata-service blocking (`homeassistant.py`)
  - Tasmota integration: IP validation blocking loopback and link-local addresses (`tasmota.py`)
- **Hashlib Security Annotations**:
  - Added `usedforsecurity=False` to non-security hash calls (MD5 for AMS fingerprinting, SHA1 for git blob format)
- **Unused Code Removal**:
  - Removed ~30 redundant function-level imports, unused variables, dead code, and trivial conditions flagged by CodeQL
- **Local Security Scanner Improvements**:
  - `test_security.sh` uses `--threads=0` for all CodeQL commands (auto-detects CPU cores)
  - Added `.trivyignore` to suppress accepted Dockerfile USER directive finding

### Enhancements
- **Per-Filament Spoolman Usage Tracking** (PR #277):
  - Reports exact filament consumption per spool to Spoolman after each print
  - Parses G-code from 3MF files for layer-by-layer extrusion data (multi-material support)
  - New setting: "Disable AMS Estimated Weight Sync" to prefer Spoolman usage tracking over AMS weight estimates
  - New setting: "Report Partial Usage for Failed Prints" estimates filament used up to the failure point based on layer progress
  - Persists tracking data in SQLite for reliability across restarts
  - Extracted Spoolman tracking into dedicated service module with DRY helpers
- **3D Model Viewer Improvements** (PR #262):
  - Added plate selector for multi-plate 3MF files with thumbnail previews
  - Object count display shows number of objects per plate and total
  - Fullscreen toggle for immersive model viewing
  - Resizable split view between plate selector and 3D viewer in fullscreen mode
  - Pagination support for files with many plates (e.g., 50+ plates)
  - Added i18n translations for all model viewer strings (English, German, Japanese)
- **Virtual Printer Proxy Mode Improvements**:
  - SSDP proxy for cross-network setups: select slicer network interface for automatic printer discovery via SSDP relay
  - FTP proxy now listens on privileged port 990 (matching Bambu Studio expectations) instead of 9990
  - For systemd: requires `AmbientCapabilities=CAP_NET_BIND_SERVICE` capability
  - Automatic directory permission checking at startup with clear error messages for Docker/bare metal
  - Updated translations for proxy mode steps in English, German, and Japanese

### Fixed
- **Authentication Required Error After Initial Setup** (Issue #257):
  - Fixed "Authentication required" error when using printer controls after fresh install with auth enabled
  - Token clearing on 401 responses is now more selective - only clears on invalid token messages
  - Generic "Authentication required" errors (which may be timing issues) no longer clear the token
  - Also fixed smart plug discovery scan endpoints missing auth headers
- **Filament Hover Card Overlapping Navigation Bar** (Issue #259):
  - Fixed filament info popup being partially covered by the navigation bar
  - Hover card positioning now accounts for the fixed 56px header
  - Cards near the top of the page now correctly flip to show below the slot
- **Filament Statistics Incorrectly Multiplied by Quantity** (Issue #229):
  - Fixed filament totals being inflated by incorrectly multiplying by quantity
  - The `filament_used_grams` field already contains the total for the entire print job
  - Removed incorrect `* quantity` multiplication from archive stats, Prometheus metrics, and FilamentTrends chart
  - Example: A print with 26 objects using 126g was incorrectly shown as 3,276g
- **Print Queue Status Does Not Match Printer Status** (Issue #249):
  - Queue now shows "Paused" when the printer is paused instead of "Printing"
  - Fetches real-time printer state for actively printing queue items
  - Added translations for paused status in English, German, and Japanese
- **Queue Scheduled Time Displayed in Wrong Timezone** (Issue #233):
  - Fixed scheduled time being displayed in UTC instead of local timezone when editing queue items
  - The datetime picker now correctly shows and saves times in the user's local timezone
- **Mobile Layout Issues on Archives and Statistics Pages** (Issue #255):
  - Fixed header buttons overflowing outside the screen on iPhone/mobile devices
  - Headers now stack vertically on small screens with proper wrapping
  - Applied consistent responsive pattern from PrintersPage
- **AMS Auto-Matching Selects Wrong Slot** (Issue #245):
  - Fixed AMS slot mapping when multiple trays have the same `tray_info_idx` (filament type identifier)
  - `tray_info_idx` (e.g., "GFA00" for generic PLA) identifies filament TYPE, not unique spools
  - When multiple trays match the same type, color is now used as a tiebreaker
  - Previously used `find()` which always returned the first match regardless of color
  - Fixed in both backend (print_scheduler.py) and frontend (useFilamentMapping.ts)
  - Resolves wrong tray selection (e.g., A4 instead of B1) when multiple AMS units have same filament type
- **A1/A1 Mini FTP Upload Failures** (Issue #271):
  - Fixed FTP uploads hanging/timing out on A1 and A1 Mini printers
  - Replaced `storbinary()` with manual chunked transfer using `transfercmd()`
  - A1's FTP server has issues with Python's `storbinary()` waiting for completion response
  - Uses 1MB chunks with explicit 120s socket timeout for reliable transfers
  - Works for all printer models (X1C, P1S, P1P, A1, A1 Mini)
- **P1S/P1P FTP Upload Failures**:
  - Fixed FTP uploads failing with EOFError on P1S and P1P printers
  - These printers use vsFTPd which requires SSL session reuse on data channel
  - Removed P1S/P1P from skip-session-reuse list (they were incorrectly added)
- **FTP Auto-Detection for A1 Printers**:
  - Automatically detects working FTP mode (prot_p vs prot_c) for A1/A1 Mini
  - Tries encrypted data channel first, falls back to clear if needed
  - Caches working mode per printer IP to avoid repeated detection
- **Safari Camera Stream Failing**:
  - Fixed camera streams not loading in Safari due to Service Worker error
  - Safari has stricter Service Worker scope requirements
- **Queue Print Time for Multi-Plate Files** (PR #274):
  - Fixed print time showing total for all plates instead of selected plate
  - Now extracts per-plate print time from 3MF slice_info.config
  - Contributed by MisterBeardy
- **Docker Permissions**:
  - Added user directive to docker-compose.yml using PUID/PGID environment variables
  - Allows container to run as host user, fixing permission issues with bind-mounted volumes
  - Usage: `PUID=$(id -u) PGID=$(id -g) docker compose up -d`

### Added
- **Windows Portable Launcher** (contributed by nmori):
  - New `start_bambuddy.bat` for Windows users - double-click to run, no installation required
  - Automatically downloads Python 3.13 and Node.js 22 on first run (portable, no system changes)
  - Everything stored in `.portable\` folder for easy cleanup
  - Commands: `start_bambuddy.bat` (launch), `start_bambuddy.bat update` (update deps), `start_bambuddy.bat reset` (clean start)
  - Custom port via `set PORT=9000 & start_bambuddy.bat`
  - Verifies all downloads with SHA256 checksums for security
  - Supports both x64 and ARM64 Windows systems

## [0.1.7] - 2026-02-03

### Security
- **Critical: Missing API Endpoint Authentication** (CVE-2026-25505, CVSS 9.8):
  - Added authentication to 200+ API endpoints that were previously unprotected
  - All route files now use `RequirePermissionIfAuthEnabled()` for permission checks
  - Protected endpoints: archives, projects, settings, API keys, groups, cloud, notifications, maintenance, filaments, external links, smart plugs, discovery, firmware, camera, k-profiles, AMS history, pending uploads, updates, spoolman, system, print queue, printers
  - Image-serving endpoints (thumbnails, timelapse, photos, camera streams) remain public as they require knowing the resource ID and are loaded via `<img>` tags which cannot send Authorization headers
  - Backend integration tests added to verify endpoint authentication enforcement

### Enhancements
- **TOTP Authenticator Support for Bambu Cloud** (Issue #182):
  - Added support for TOTP-based two-factor authentication when connecting to Bambu Cloud
  - Accounts with authenticator apps (Google Authenticator, Authy, etc.) now work correctly
  - Proper detection of verification type: email code vs TOTP code
  - Uses browser-like headers to bypass Cloudflare protection on TFA endpoint
  - Frontend shows appropriate message for each verification type
  - Added translations for TOTP UI in English, German, and Japanese
- **Spoolman: Open in Spoolman Button** (Issue #210):
  - FilamentHoverCard now shows "Open in Spoolman" button when spool is already linked in Spoolman
  - Button links directly to the spool's page in Spoolman for quick editing
  - "Link to Spoolman" button now only shows when spool is not yet linked
  - Link button correctly disabled when no unlinked spools are available in Spoolman
  - Toast notification shown on successful/failed spool linking
  - Added `/api/v1/spoolman/spools/linked` endpoint returning map of linked spool tags to IDs
- **Complete German Translations**:
  - All UI strings now fully translated to German (1800+ translation keys)
  - Pages translated: Settings, Archives, File Manager, Queue, Printers, Profiles, Projects, Stats, Maintenance, Camera, Groups, Users, Login, Setup, Stream Overlay
  - Components translated: ConfirmModal, LinkSpoolModal, FilamentHoverCard, Layout
  - Added locale parity test to ensure English and German stay in sync
- **Virtual Printer Proxy Mode**:
  - New "Proxy" mode allows remote printing over any network by relaying slicer traffic to a real printer
  - Configure a target printer and Bambuddy acts as a TLS proxy between your slicer and the printer
  - Supports both FTP (port 9990) and MQTT (port 8883) protocols with full TLS encryption
  - Slicer connects to Bambuddy using the real printer's access code
  - Real-time status display showing active FTP/MQTT connections
  - Target printer selector with validation (must be configured in Bambuddy)
  - Proxy mode bypasses the access code requirement (uses the real printer's credentials)
  - Full i18n support for all proxy mode UI strings (English, German, Japanese)

### Fixed
- **Cannot Link Multiple HA Entities to Same Printer** (Issue #214):
  - Fixed Home Assistant entities being limited to one per printer
  - Both frontend and backend were blocking printers that already had any smart plug linked
  - Now only Tasmota plugs are limited to one per printer (physical device constraint)
  - Multiple HA entities (switches, scripts, lights, etc.) can be linked to the same printer
  - Restored "Show on Printer Card" toggle for HA entities to control visibility on printer cards
  - Fixed printer card only showing `script.*` entities; now shows all HA entities with toggle enabled
  - HA entities now default to auto_on=False and auto_off=False (appropriate for automations)
  - Printer cards now update immediately when HA entities are added/modified/deleted
- **Monthly Comparison Calculation Off** (Issue #229):
  - Fixed filament statistics not accounting for quantity multiplier
  - Monthly comparison chart now correctly multiplies `filament_used_grams` by `quantity`
  - Daily and weekly charts also now account for quantity
  - Filament type breakdown includes quantity in calculations
  - Backend stats endpoint (`/archives/stats`) and Prometheus metrics also fixed
  - Prints count now shows total items (sum of quantities) instead of archive count
- **Authentication Required for Downloads** (Issue #231):
  - Fixed support bundle download returning 401 Unauthorized when auth is enabled
  - Fixed archive export (CSV/XLSX) failing with authentication enabled
  - Fixed statistics export failing with authentication enabled
  - Fixed printer file ZIP download failing with authentication enabled
  - Root cause: These endpoints used raw `fetch()` without Authorization header
- **Queue Schedule Date Picker Ignores User Format Settings** (Issue #233):
  - Replaced native datetime picker with custom date/time inputs respecting user settings
  - Date input shows in user's format (DD/MM/YYYY for EU, MM/DD/YYYY for US, YYYY-MM-DD for ISO)
  - Time input shows in user's format (24H or 12H with AM/PM)
  - Calendar button opens native picker for convenience; selection is formatted to user's preference
  - Placeholder text shows expected format (e.g., "DD/MM/YYYY" or "HH:MM AM/PM")
  - Added date utilities: `formatDateInput`, `parseDateInput`, `getDatePlaceholder`
  - Added time utilities: `formatTimeInput`, `parseTimeInput`, `getTimePlaceholder`
- **500 Error on Archive Detail Page**:
  - Fixed internal server error when viewing individual archive details
  - Root cause: `project` relationship not eagerly loaded in `get_archive()` service method
  - Async SQLAlchemy requires explicit eager loading; lazy loading is not supported

## [0.1.6.2] - 2026-02-02

> **Security Release**: This release addresses critical security vulnerabilities. Users running authentication-enabled instances should upgrade immediately.

### Security
- **Critical: Hardcoded JWT Secret Key** (GHSA-gc24-px2r-5qmf, CWE-321) - Fixed hardcoded JWT secret key that could allow attackers to forge authentication tokens:
  - JWT secret now loaded from `JWT_SECRET_KEY` environment variable (recommended for production)
  - Falls back to auto-generated `.jwt_secret` file in data directory with secure permissions (0600)
  - Generates cryptographically secure 64-byte random secret if neither exists
  - **Action Required**: Existing users will need to re-login after upgrading
- **Critical: Missing API Authentication** (GHSA-gc24-px2r-5qmf, CWE-306) - Fixed 77+ API endpoints that lacked authentication checks:
  - Added HTTP middleware enforcing authentication on ALL `/api/` routes when auth is enabled
  - Only essential public endpoints are exempt (login, auth status, version check, WebSocket)
  - All other API calls now require valid JWT token or API key

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
- **File Manager permissions not enforced** (Issue #224) - Fixed backend not checking `library:read` permission for File Manager endpoints:
  - Added `library:read` permission check to all list/view endpoints (files, folders, stats)
  - Added `library:upload` permission check to upload and folder creation endpoints
  - Added `queue:create` permission check to add-to-queue endpoint
  - Added `printers:control` permission check to direct print endpoint
  - Added ownership-based permission checks to file move operation
  - Users without `library:read` permission can no longer view files in the File Manager
  - Users can now only delete/update their own files unless they have `*_all` permissions
- **JWT secret key not persistent across restarts** - Fixed JWT secret key generation to properly use data directory, ensuring tokens remain valid across container restarts
- **Images/thumbnails returning 401 when auth enabled** - Fixed auth middleware to allow public access to image/media endpoints (thumbnails, photos, QR codes, timelapses, camera streams) since browser elements like `<img>` don't send Authorization headers
- **Library thumbnails missing after restore** - Fixed library files using absolute paths that break after restore on different systems:
  - Library now stores relative paths in database for portability
  - Automatic migration converts existing absolute paths to relative on startup
  - Thumbnails and files now display correctly after restoring backups
- **File uploads failing with authentication enabled** - Fixed all file upload functions (archives, photos, timelapses, library files, etc.) not sending authentication headers when auth is enabled
- **External spool AMS mapping causing "Failed to get AMS mapping table"** (Issue #213) - Fixed external spool `ams_mapping2` slot_id handling that caused AMS mapping failures
- **Filename matching for files with spaces** (Issue #218) - Fixed file detection when filenames contain spaces
- **P2S FTP upload failure** (Issue #218) - Fixed FTP uploads to P2S printers by passing `skip_session_reuse` to ImplicitFTP_TLS
- **Printer deletion freeze** (Issue #214) - Fixed UI freeze when deleting printers, and now allows multiple smart plugs per printer
- **Stack trace exposure in error responses** (CodeQL Alert #68) - Fixed stack traces being exposed in API error responses in archives.py
- **Printer serial numbers exposed in support bundle** (Issue #216) - Sanitized printer serial numbers in support bundle logs for privacy
- **Missing sliced_for_model migration** (Issue #211) - Fixed database migration for `sliced_for_model` column that was missing in some upgrade paths

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
