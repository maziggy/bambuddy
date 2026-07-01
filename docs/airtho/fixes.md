# Bug Fixes Applied (committed to `airthos/bambuddy`)

_Last updated: 2026-07-01. Commit hashes are the source of truth — `git show <hash>` to
verify a fix's current form; this file records intent and root cause, which don't rot as
fast as line numbers do._

## Fix 1: Queue items stuck in "printing" on FINISH (`71facf20`)

**File:** `backend/app/services/bambu_mqtt.py`
**Problem:** `should_trigger_completion` required `_was_running=True`, which meant a
completion was missed if the service restarted during an active print.
**Fix:** Removed the `_was_running` gate for FINISH/FAILED states — completion now
always fires on FINISH/FAILED. IDLE still requires
`_previous_gcode_state == "RUNNING"` (so an explicit abort/cancel is the only IDLE case
that still triggers completion).

## Fix 2: Stale `stg_cur_name` showing "Auto bed leveling" on idle printers (`b30cea7e`)

**Files:** `backend/app/services/bambu_mqtt.py`,
`frontend/src/pages/PrintersPage.tsx`, `frontend/src/pages/StreamOverlayPage.tsx`
**Problem:** a P1S delta-MQTT instance of the class of bug described in
[`printers.md`](printers.md) — `stg_cur` (e.g. stage 1 = "Auto bed leveling") never gets
cleared when a print ends, because the printer doesn't send a reset delta for it.
**Fix (backend):** reset `stg_cur → -1` and `stg = []` in `_update_state()` whenever
`gcode_state` transitions to IDLE/FINISH/FAILED.
**Fix (frontend):** guard the `stg_cur_name` display behind an `isActive` check — only
show the stage name when `state` is `RUNNING`, `PREPARE`, or `PAUSE`.

## Fix 3: Farm script bed setpoint and cooldown threshold (`f142775e`)

**File:** `scripts/farm_process.py`
**Fix:** added `M140 S0` after the M190 cooldown loop, so the bed setpoint reads 0°C
after a farm job instead of a leftover 25°C. Also changed the cooldown release
threshold (argparse default + the hardcoded call in `process_inplace()`) — was 35°C,
**later raised to 40°C on 2026-06-12** in commit `1d5d3c6c` (see
[`features.md`](features.md) item 2).

## Fix 4: Farm script plate_id threading (`481cfa2a`)

**Files:** `scripts/farm_process.py`, `backend/app/services/print_scheduler.py`
**Problem:** the script always wrote `Metadata/plate_1.gcode`, even for queue items on
plate 2 or 3 — the gcode appeared unmodified for anything but plate 1.
**Fix (script):** `write_3mf`, `read_input_3mf`, and `process_inplace` all now accept a
`plate_id` parameter; the entry point accepts an optional second CLI arg.
**Fix (scheduler):** pass `str(item.plate_id or 1)` as the script's second argument.

## Fix 5: Frontend bundle rebuild (`d7cbebc4`, `b30cea7e`)

**Problem:** merging upstream releases kept overwriting `static/assets/index-*.js` with
a bundle missing the fork's farm post-processor checkbox — upstream's committed bundle
silently clobbered the fork's UI feature.
**Fix:** rebuild the frontend locally (`npm run build` in `frontend/`) and commit the
new bundle as part of the merge. **Always diff `static/` after merging upstream** —
this is a recurring failure mode, not a one-time fix.

## Fix 6: `farm_process.py` executable bit (`95009664`)

**Problem:** git doesn't track the executable bit across all clone/copy paths by
default.
**Fix:** `git update-index --chmod=+x scripts/farm_process.py`. If a stash-based deploy
pattern is ever used (normally it isn't — see [`infrastructure.md`](infrastructure.md)),
re-`chmod +x` on the server after.

## Fix 7: Watchdog false-positive exits + HMS dispatch gate (`6f069f8a`)

Root cause discovered 2026-06-09 — queue items 1269/1276/1283/1284 stuck in `printing`
on Airtho 3DP 4 (printer 3). Three distinct bugs combined to cause this:

1. **Watchdog subtask_id false positive.** The P1S echoes back the *new* subtask_id
   from a `project_file` command even when it *rejects* the command (e.g. an active HMS
   error blocks it). The watchdog saw the subtask_id change and treated it as
   confirmation, exiting without reverting the queue item. **Fixed** by adding a
   `status.state != "IDLE"` guard — a subtask_id change only counts as confirmation when
   the printer is not IDLE.
2. **Dispatch-hold had the same false positive.** `_is_dispatch_hold_active()` used the
   same subtask_id logic without the IDLE guard. Fixed identically.
3. **HMS dispatch gate added.** `_is_printer_idle()` now returns `False` when the
   printer has an active fatal/serious HMS error (severity ≤ 2) — Bambu firmware
   silently ignores `project_file` commands while an HMS error is unacknowledged, so
   dispatching into that state was guaranteed to produce another false start. The gate
   auto-unblocks once the error is cleared (manually, or via the auto-clear in
   [`features.md`](features.md) item 1c for the SD-card subset).

**Contributing factor, not fixed:** double-dispatch. If a user hits both "Print Now"
(the library route → `background_dispatch.py`) and "Add to Queue" (the queue route)
for the same printer at roughly the same time, both systems dispatch independently —
`background_dispatch.py` has no awareness of the print scheduler's dispatch state and no
coordination between the two paths. See [`known-issues.md`](known-issues.md).

**Also added:** an `INFO`-level watchdog startup log, so it's observable in logs when a
watchdog spawns and whether it exits early vs. via timeout — this was previously
invisible and made the above three bugs much harder to diagnose.

## Fix 8: Re-dispatch onto a fouled bed (`e878ec77`)

Pushed 2026-06-10, deployed 2026-06-12 (rode along with the 1b deploy). See the
discovery narrative in [`known-issues.md`](known-issues.md) — this used to be listed
there as unfixed; it has since been resolved and is kept here for the fix record.

**Fix:** status-aware `awaiting_plate_clear` gate — `main.py` now raises
`awaiting_plate_clear` only for failed/aborted/cancelled terminal states when
`require_plate_clear=false` (a naturally-completed print already got its plate cleared
by the push-off sequence, so it doesn't need the flag). `_is_printer_idle()` honors this
flag regardless of the `require_plate_clear` setting value. `PrintersPage.tsx` shows the
"Clear Plate" button whenever the flag is set — previously it was gated behind the
`require_plate_clear` setting itself, which would have left no UI path to unblock the
queue when the setting is off. Bundle: `index-2xBfCIGy.js`.

**Net effect:** after any failure/abort/cancel, the queue on that printer now holds
until a human physically clears the bed and clicks Clear Plate — the farm no longer
auto-dispatches the next job onto a bed with a partial part still on it.
