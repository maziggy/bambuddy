# Incident: Underextrusion on 3DP 2 & 3DP 3 (Jun 23–24, 2026)

_Recorded 2026-06-29 (from investigation dated 2026-06-23/24). This is a postmortem —
treat the "recommended" section as an open action item unless someone confirms it was
done and updates this file._

## Summary

Underextrusion observed on **Airtho 3DP 2** (printer 1) and **Airtho 3DP 3** (printer 2)
around June 23–24, 2026. Confirmed **not** a software/gcode/pipeline issue: the
BamBuddy service had run continuously since June 12 (no restart/crash) with no
exceptions, the farm post-processor applied cleanly to every queued item, and the exact
same file (`PEC-V-PPA-CNC_Rev_B.1.3mf`) printed fine both before and after the affected
window.

## Findings

**HMS codes (decoded via the canonical 4-group `MMMM_CCCC` form** — BamBuddy's own
`_hms_short_code()` drops the attr's low word and misses some lookups, so cross-check
against the canonical form when decoding, not just what the UI surfaces):

- **3DP 2:** `0300_8010` ("hotend cooling fan speed abnormal") +
  `0300_0300_0001_0001` ("hotend cooling fan too slow/stopped"), Jun 24 → consistent
  with a heat-creep clog from restricted hotend airflow → underextrusion. Also offline
  for most of the window (373 MQTT disconnects, zero AMS telemetry during that time).
- **3DP 3:** `0300_3100_0001_0001` ("part cooling fan too slow/stopped"), FATAL, Jun 23
  11:41. Part-cooling fan affects layer cooling more directly than extrusion, but see
  the front-cover theory below for how it's still connected.

**Filament/humidity:** both units run generic (non-Bambu) white PLA in an AMS slot with
no spool assignment (`remain=-1`). 3DP 3's AMS humidity ran chronically ~23–27% RH
during this window vs. ~14% on 3DP 4 — consistent with spent desiccant or a damp spool.
Every affected print ran with `flow_cali: true`; Bambu's auto flow-dynamics calibration
on damp/inconsistent PLA can lock in an artificially low flow ratio, producing systemic
underextrusion independent of any mechanical fault.

**Likely root cause — recurring "Toolhead front cover fell off" (HMS `0300_8005`).**
From `notification_logs` (`event_type = printer_error`): 3DP 2 got this Jun 12 22:14,
Jun 19 11:38, and Jun 20 01:25 (×3) — all *before* its fan-fault codes appeared. 3DP 3
got it Jun 20 07:35. (3DP 4 also saw it twice on Jun 24, but showed no fan-fault
symptoms in this window.) On the P1S, the front cover shrouds the part-cooling-fan duct
and the hotend airflow path — a dislodged cover plausibly disrupts fan airflow and/or
fan-speed feedback, producing the "fan too slow/abnormal" HMS codes, which for the
hotend fan specifically leads to heat creep and underextrusion. Proposed causal chain:
**cover dislodges → cooling/airflow disrupted → fan-fault HMS → (hotend side)
underextrusion.** 3DP 2's cover wasn't staying latched — three occurrences in 8 days.

**Job-adjacency check** (via `print_archives`): on **3DP 3, the cover-off event and the
part-fan fatal are the same print job** (internal id `127`) — the cover fell off ~80s
after the job started (Jun 20 03:34 EDT start, 03:35 cover notice), and that same job
later failed with the part-cooling-fan fatal (Jun 23 11:42 EDT). Zero jobs apart —
near-direct causation for 3DP 3. On **3DP 2 there were zero tracked archives in the
relevant window** (it was offline/in calibration only), so its cover notices (last Jun
19 21:25 EDT) to its first hotend-fan-abnormal notice (Jun 24 12:02 EDT, ~4d15h later)
have no intervening jobs to correlate — the link there is circumstantial, not
job-adjacency-confirmed.

**Best read:** a front-cover/fan-airflow problem on both units, manifesting differently
— 3DP 2 as a hotend-fan heat-creep clog, 3DP 3 as a part-fan fault compounded by damp
generic PLA. Shared secondary contributor: third-party white PLA plus flow-cali
sensitivity to humidity.

## Recommended actions (status: not confirmed done — verify before assuming closed)

1. Reseat/secure the toolhead front cover on 3DP 2 and 3DP 3, and verify the fan duct
   clips are fully seated. Do this **first** — it may clear the fan codes with no parts
   swap needed.
2. If fan codes persist after (1), replace the hotend fan on 3DP 2 and do a cold pull.
3. Dry the PLA / replace the AMS desiccant on 3DP 3.

## Useful sources for this class of investigation

- HMS-at-failure snapshot: app log line `[ARCHIVE] HMS errors at failure`.
- AMS humidity history: `ams_sensor_history` table (`printer_id`, humidity `%RH`,
  `recorded_at` in UTC).
- All sent notifications, including printer_error/HMS text: `notification_logs` table
  (`title`/`message`/`printer_id`/`created_at`). History in this table only starts
  2026-06-12 — don't expect earlier data.

Related: [`printers.md`](../printers.md) (P1S delta-MQTT / stale-state class of bug;
front-cover hardware quirk), [`fixes.md`](../fixes.md) (Fix 2, the same delta-MQTT
staleness class applied to a different field).
