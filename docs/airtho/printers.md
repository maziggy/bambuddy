# Printers

_Last updated: 2026-07-01. IPs/serials verify against Settings → Printers before relying on them —
DHCP or hardware swaps can change these without a code change._

| DB id | Name | IP | Serial | Model |
|---|---|---|---|---|
| 1 | Airtho 3DP 2 | 10.1.10.220 | 01P00C530300230 | P1S |
| 2 | Airtho 3DP 3 | (unknown — check Settings → Printers) | 01P00C5C0202000 | P1S |
| 3 | Airtho 3DP 4 | 10.1.10.17 | 01P00C5C0201596 | P1S |

All three run on the farm's push-off/lights-out loop (see
[`features.md`](features.md) — farm post-processor).

## P1S delta MQTT

The P1 series (unlike X1C) only sends **changed fields** in its MQTT status reports, not
a full state snapshot. Practically:

- BamBuddy's in-memory `PrinterState` can hold a **stale value** for any field the
  printer hasn't re-reported recently, including across a state transition where you'd
  expect it to reset (e.g. a stage name from mid-print persisting after the print ends —
  see Fix 2 in [`fixes.md`](fixes.md)).
- If you're adding or touching any printer-state field, ask explicitly: *what happens to
  this field's stale value when the printer stops reporting it, or transitions to a
  state where it should logically reset?* Don't only handle the "new value arrives" path.
- This class of bug has already caused two of the seven documented fork fixes. Treat any
  new P1S state-handling code as needing this check before it ships.

## Hardware quirks observed in the field

- **Toolhead front cover dislodging** (HMS `0300_8005`, "Toolhead front cover fell off")
  has recurred on multiple units and is the suspected root cause of a fan-airflow-driven
  underextrusion incident — see
  [`incidents/2026-06-underextrusion.md`](incidents/2026-06-underextrusion.md). If a
  printer starts throwing hotend/part-cooling-fan HMS codes, check the front cover
  latching before assuming a fan hardware failure.
- **Generic/third-party PLA humidity** — AMS slots running non-Bambu spools have no RFID
  (`remain=-1`), so `prefer_lowest_filament` is a no-op for them (this is what motivated
  the "Prefer Recently-Used Spool" feature, see [`features.md`](features.md)). Chronic
  AMS humidity above ~20% RH on a unit running generic PLA is a leading indicator of the
  auto-flow-calibration-driven underextrusion failure mode documented in the incident
  file above — check desiccant before assuming a software cause.
