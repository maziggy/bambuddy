# Custom Features Added in This Fork

_Last updated: 2026-07-01. Verify file paths and line references against current code —
the codebase moves faster than this doc._

## 1. Farm Post-Processor Script Hook

- `script_processing` bool column on `print_queue` — per-queue-item toggle to run a farm
  post-processor script before FTP upload.
- `post_process_script` setting (a path on the server) — configured in
  Settings → Workflow → Farm Post-Processor Script.
- Script interface: receives the temp `.3mf` path + `plate_id` as args, modifies the
  file in-place, exits 0 on success.
- Frontend checkbox "Run farm post-processor" in
  `frontend/src/components/modals/PrintModal.tsx`.
- The concrete script used is `scripts/farm_process.py` — see item 2 below.

## 1b. "Prefer Recently-Used Spool" Filament Preference

Commit `7a953506`, deployed 2026-06-12.

- **Why:** the farm runs non-BBL spools with no RFID, so AMS reports `remain=-1` and the
  existing upstream `prefer_lowest_filament` feature is a no-op for those slots. Goal:
  when multiple AMS slots hold a compatible filament, keep feeding the slot the printer
  last used, so one spool finishes before the next starts (instead of round-robin
  wasting partial spools).
- **Setting:** `prefer_recently_used_filament` bool (Settings → filament tracking, next
  to Prefer Lowest). A separate toggle; takes precedence over `prefer_lowest_filament`
  when both are on.
- **Signal used:** the printer's `PrinterState.last_loaded_tray` (falls back to
  `tray_now`) — this survives the unload→255 transition at print end and follows the AMS
  through a mid-print runout auto-fallback, so it reliably points at the spool
  physically in use. It resets on service restart, at which point behavior falls back to
  slot-position order until the first completion re-establishes it (no persistence
  across restarts by design — see the "reverted" note in 1c for why nothing more elaborate was added here either).
- **Backend:** `print_scheduler.py` — `_prefer_recent_sort_key`;
  `_match_filaments_to_slots` gained `prefer_recent`/`preferred_tray` params (applied at
  both sort call sites, including the `tray_info_idx` subset);
  `_compute_ams_mapping_for_printer` reads the setting + `last_loaded_tray`. **Key
  detail:** `check_queue` forces `needs_mapping=True` when the setting is on, at both
  dispatch sites, so a cached/baked mapping can't defeat it — non-RFID slots never
  validate as "empty," so the normal auto-remap path wouldn't otherwise fire.
- **Trade-off, explicit:** while this setting is on, a slot manually chosen in the Print
  modal and then queued gets overridden at dispatch time. This is intentional for the
  lights-out farm path; "Print Now" (immediate dispatch, not queued) is unaffected.
- **Frontend:** toggle in `SettingsPage.tsx` (i18n keys `preferRecentlyUsedFilament` /
  `...Desc` in `en.ts`), field in `api/client.ts`. Preview parity in
  `useFilamentMapping.ts` was deliberately **not** done — optional, since the farm/queue
  dispatch path is backend-authoritative and the preview is cosmetic.
- **Tests:** `TestPreferRecentlyUsedFilament` in `test_scheduler_ams_mapping.py` (7
  cases including auto-switch and precedence-over-`prefer_lowest`).
- Bundle at time of ship: `index-BDZcMu3C.js`.

## 1c. SD-Card HMS Auto-Clear

Added 2026-06-29. **Not yet committed/deployed as of last update** — check
`git log -- backend/app/main.py` / `git status` before assuming it's live; it may exist
as a local uncommitted diff.

- **Why:** transient SD/MicroSD read/write HMS errors (especially `0500_C010`) spam a
  notification on every MQTT push and, worse, block the dispatch gate (any HMS at
  severity ≤2 counts as "not idle" in `_is_printer_idle`) until a human clicks "Clear
  HMS." On unattended farm hardware that's a stuck queue for no real reason — the fault
  is transient and self-clearing.
- **Rejected first draft — read this before proposing anything similar:** an earlier
  version of this feature added a whole parallel system: a scheduler state machine
  (`_recover_sdcard_for_printer` / `recover_sdcard_now`), a per-printer cooldown/lockout
  dict, a `force_reconnect` escalation path, a new `auto_recover_sdcard_errors` settings
  toggle plus frontend control, and an `HMSError.short_code` property. **Brendan
  rejected this outright** — the ask was to implement it the way BamBuddy already works,
  not bolt on new machinery. The entire draft was reverted via `git checkout`.
- **What actually shipped: a single ~28-line additive diff in `backend/app/main.py`,
  nothing else.** It mirrors the existing `_HMS_FAILURE_REASONS` /
  `_HMS_NOTIFICATION_SUPPRESS` module-level-set pattern that was already in the file:
  - A module-level set `_HMS_SDCARD_AUTO_CLEAR` (next to `_HMS_FAILURE_REASONS`, ~line
    412): `0500_C010`, `0500_402F`, `0500_800E`, `0500_8013`, `0300_800E`. Codes that
    need physical intervention (no card / full / write-protected) are deliberately
    excluded so those still notify a human.
  - Inside the **existing** HMS handler `on_printer_status_change`'s per-error loop,
    right after `short_code` is computed and before the description/suppress check: if
    `short_code in _HMS_SDCARD_AUTO_CLEAR`, call the **existing**
    `printer_manager.get_client(printer_id).clear_hms_errors()` once — guarded by a
    `sdcard_cleared` flag, since `clean_print_error` acknowledges the whole error list at
    once — then `continue` to skip the notification for that error.
- **Explicitly not added:** no reconnect logic, no settings toggle, no scheduler
  changes, no frontend changes, no new tests (the analogous
  `_HMS_NOTIFICATION_SUPPRESS` behavior has none either — consistency with existing
  test coverage, not an oversight). The existing `_notified_hms_errors` debounce
  naturally limits clears to once per newly-seen occurrence; clearing the error unblocks
  the dispatch gate on its own via the existing severity check.
- **Lesson, stated plainly for future work in this area:** match BamBuddy's existing
  patterns and architecture. Don't invent a parallel subsystem, extra hooks, or a new
  toggle unless specifically asked for one. Grep for the nearest existing analogous
  feature first.
- **Known pre-existing, unrelated test failures** as of this feature's development:
  `TestPrePrintFailureCompletion::test_initial_failed_does_not_trigger_completion` and
  `test_idle_to_failed_does_not_trigger_completion` in `test_bambu_mqtt.py`. These encode
  the *pre-Fix-1* "FAILED shouldn't trigger completion" expectation that Fix 1 (commit
  `71facf20`, see [`fixes.md`](fixes.md)) deliberately removed. If you see these failing,
  it's not this feature's fault — but also verify they haven't been fixed/removed since,
  rather than assuming this note is still accurate.

## 2. `scripts/farm_process.py` — Farm Loop End Sequence

Cherry-picked from `airthos/print-farm`, adds the farm's end-of-print loop sequence.

- **What it does:** strips the stock `MACHINE_END_GCODE_START` block and injects: a bed
  cooldown loop (`M190` at **40°C** — raised from 35°C on 2026-06-12, commit `1d5d3c6c`;
  40 iterations) → `M140 S0` (clears the bed setpoint so the UI shows 0°C, not a
  leftover 25°C) → a bed-flex sequence (Z204↔Z224, three cycles) → a part push-off sweep
  across center/right/left lanes, all at **2000 mm/min** since 2026-06-12 (commit
  `509f5e02` — the center lane used to run at a "slow" 300 mm/min and now matches the
  other lanes; controlled by the `push_speed` param / `--push-speed` CLI flag).
  Note: the "40" in the `M190` loop is the **iteration count**, not the temperature —
  the temperature is the separate `cooldown_temp` param.
- **Plate-aware:** reads/writes `Metadata/plate_{N}.gcode` and
  `Metadata/plate_{N}.gcode.md5` for the correct plate — see Fix 4 in
  [`fixes.md`](fixes.md) for why this needed a dedicated fix.
- **Call signature:** `farm_process.py <path_to_3mf> [plate_id]` — `plate_id` defaults
  to `1`.
- **Archive behavior:** BamBuddy's archive stores the **original, unmodified** library
  file. The farm script only ever runs on a temp copy that gets FTP'd to the printer.
  So the archive viewer showing unprocessed gcode is expected, not a bug — don't
  "fix" this without understanding why it's intentional (archives are meant to preserve
  the source-of-truth file).
