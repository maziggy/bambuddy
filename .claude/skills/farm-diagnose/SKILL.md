---
name: farm-diagnose
description: Diagnose a print-farm issue on Airtho's BamBuddy deployment (stuck queue item, printer offline, HMS error, underextrusion/print-quality report, unattended dispatch not happening). Use whenever investigating why the farm isn't behaving as expected on airtho-server.
---

# Diagnosing a print-farm issue

This is a Claude-Code-specific formalization of the investigation steps used in past
incidents on this fork. If you're on a different harness, the same steps are described
in prose in `docs/airtho/` — read that instead; this file just sequences the same
knowledge into a checklist.

## 0. Check the knowledge base first

Before running anything, grep `docs/airtho/` (`fixes.md`, `known-issues.md`,
`incidents/`) for the symptom. Several failure modes on this farm are already
root-caused: stuck-in-printing items, stale P1S state fields, HMS-blocked dispatch,
fouled-bed re-dispatch, front-cover-driven fan faults, underextrusion via damp PLA +
flow-cali. Re-deriving one of these from scratch is the single biggest token/time waste
on this project — don't do it if the doc already has the answer.

## 1. Identify which printer/queue item/timeframe

Get the printer id (see `docs/airtho/printers.md` for the id↔name↔serial table — verify
against Settings → Printers if it matters for the diagnosis) and the approximate time
window.

## 2. Pull the relevant evidence from airtho-server (read-only — do not send print/MQTT commands during diagnosis)

SSH to `airtho-server` (see `docs/airtho/infrastructure.md`; credentials are not in this
repo, ask the user or the team's credential store if you don't have them).

- **App log:** `/opt/bambuddy/logs/bambuddy.log*` — search for the printer's serial or
  the queue item id. `[ARCHIVE] HMS errors at failure` lines capture the HMS state at
  the moment a print failed.
- **Database (SQLite, no `sqlite3` CLI installed — use `python3 -c "import sqlite3; ..."`):**
  at `/opt/bambuddy/data/bambuddy.db`.
  - `notification_logs` — every notification sent, including `printer_error`/HMS text,
    with `printer_id` and `created_at`. History starts 2026-06-12.
  - `ams_sensor_history` — AMS humidity readings per printer over time. Useful for
    filament-related print-quality issues.
  - `print_archives` / `print_queue` — job history, statuses, timestamps. Use this to
    check job-adjacency between two events (did event A happen in the same job as
    event B, or days apart with jobs in between).
- **MQTT/HMS codes:** BamBuddy's own `_hms_short_code()` (in `backend/app/main.py`) can
  drop information — cross-check against the canonical 4-group `MMMM_CCCC` HMS code
  form when in doubt, not just what the UI/notification text shows.

## 3. Classify the failure against known bug classes before inventing a new theory

- **Item stuck in `printing` forever** → check: (a) is the printer `connected=False`
  (see "printer offline mid-print" in `known-issues.md`)? (b) was there a
  double-dispatch race (`known-issues.md`)? (c) is this the Fix-7 watchdog/HMS-gate
  scenario (`fixes.md`)?
- **A displayed field looks wrong/stale on an idle printer** → suspect the P1S
  delta-MQTT staleness class (`printers.md`, Fix 2 in `fixes.md`) — check whether the
  field is ever explicitly reset on a state transition, not just updated on new data.
- **Partial/incomplete parts in the output bin** → check `known-issues.md` /
  Fix 8 (fouled-bed re-dispatch) — is `require_plate_clear` off, and did the failure
  happen close in time to the next dispatch?
- **Underextrusion / poor surface quality** → check HMS history for fan-fault or
  front-cover codes first (see `incidents/2026-06-underextrusion.md`), then AMS
  humidity, then consider the MQTT-calibration-flags theory in
  `print-quality-mqtt-calibration.md` (bed leveling/flow-cali skipped due to a
  workflow-default misconfiguration) if nothing hardware-side explains it.
- **Purge line off the bed / bed-leveling seems skipped** → go straight to
  `print-quality-mqtt-calibration.md`.

## 4. Write up what you find

Whatever the outcome — confirmed a known cause, found a new root cause, or ruled
something out — update the relevant file in `docs/airtho/` in the same piece of work.
See "Keeping this knowledge base honest" in the repo's top-level `AGENTS.md`.
