# Known Structural Issues (Not Yet Fixed)

_Last updated: 2026-07-01. These are deliberately documented rather than fixed — verify
each is still true against current behavior before assuming it's still open; if you fix
one, move it to [`fixes.md`](fixes.md) with the commit hash instead of deleting it here._

## Printer goes offline mid-print → queue item stuck forever

**Discovered 2026-05-18**, via queue item 1144 on Airtho 3DP 2 (printer 1). Timeline:

- 16:34:28 — printer 1 finishes a previous job, completion fires.
- 16:34:51 — scheduler dispatches item 1144 to printer 1 (plate_id=2).
- 16:35:07 — item 1144 status → `printing`, print command sent.
- 16:36:52 — printer 1's MQTT connection drops with a keep-alive timeout, ~1m45s into
  the print (likely during bed leveling, before the print body actually started).
- 16:42:46 — the BamBuddy service itself restarted (unrelated).
- From restart onward — printer 1 shows `connected=False, state=unknown`. **No MQTT
  reconnect ever fires.** No further log lines for that printer's serial after the
  disconnect.
- Root network cause that time: `10.1.10.220` was unreachable (ping: "Destination Host
  Unreachable") — the printer was physically off or had moved to a different IP.

**The structural gap:** there is no timeout mechanism. If a printer goes offline while
an item is `printing`, that item stays `printing` forever. The scheduler correctly
refuses to dispatch *new* jobs to a `connected=False` printer, but it never auto-fails
the stuck item, so every subsequent queued item behind it is blocked too (this blocked
items 1145–1148 for the rest of that day).

**Resolution path, not yet implemented:**
1. Manual workaround: update the stuck item to `cancelled` in the DB to unblock the
   queue.
2. Structural fix: scheduler logic that, if a printer has been `connected=False` for
   more than N minutes while it has an item in `printing`, auto-transitions that item to
   `failed`/`cancelled` so the queue can proceed (and someone gets notified).

**Related, separate gate:** if `plate_cleared=False` is also true (printer sitting in
FINISH state, plate not physically confirmed clear), the scheduler refuses to dispatch
for that reason too — don't conflate the two; check which gate is actually active before
diagnosing a "stuck queue" report.

## Double-dispatch via "Print Now" + "Add to Queue" racing

Documented as a contributing factor in Fix 7 ([`fixes.md`](fixes.md)). "Print Now"
(library route → `background_dispatch.py`) and "Add to Queue" (the queue/scheduler
route) are independent dispatch paths with **no coordination between them**. If a user
triggers both for the same printer close together, both will attempt to dispatch. This
has not been fixed — `background_dispatch.py` would need to either share the
scheduler's `busy_printers` bookkeeping or gate on the same idle-check the scheduler
uses.

## Farm-mode features that intentionally trade safety for uptime

Not a bug, but worth stating explicitly so it isn't "fixed" by someone unfamiliar with
why it's this way: `require_plate_clear=false` is a real, supported farm-mode
configuration (push-off is expected to clear the plate physically). This is what made
the fouled-bed issue in Fix 8 possible in the first place, and why that fix is
status-aware rather than just re-enabling the plate-clear requirement — turning the
requirement back on would break the lights-out flow this farm depends on.
