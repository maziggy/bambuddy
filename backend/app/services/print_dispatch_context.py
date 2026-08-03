"""Whether Bambuddy injected End G-code into the print now running (#2547).

The finish-photo path has to know one thing at print completion that no MQTT
field reports: did this print end with user End G-code? If it did, a SwapMod
snippet may already have ejected the plate, so the scene in front of the camera
at ``gcode_state=FINISH`` is not the finished print and the photo must come from
the in-print frame bank instead (#1867).

Only the dispatcher ever sees this, so it is recorded here in two steps:

1. ``mark_pending`` when the scheduler injects an End G-code snippet.
2. ``adopt`` when the printer reports a print starting, which moves the pending
   flag onto the running print and consumes it.

The two steps exist so the flag can never outlive its print. A print Bambuddy
did not dispatch — started from the slicer, the SD card, or the printer's own
screen — finds no pending flag and correctly adopts ``False``, instead of
inheriting the answer from whatever ran before it.

In-memory and best-effort: a restart mid-print loses the flag, and ``False`` is
the safe way to be wrong (a live grab that might show a swapped plate, rather
than silently substituting a mid-print frame).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Printers the scheduler has injected End G-code for, awaiting a print start.
_pending: set[int] = set()
# Printers whose *currently running* print has injected End G-code.
_active: set[int] = set()


def mark_pending(printer_id: int) -> None:
    """Record that the job now being sent to ``printer_id`` has End G-code."""
    _pending.add(printer_id)
    logger.debug("[DISPATCH-CTX] printer %s: End G-code injected, awaiting print start", printer_id)


def adopt(printer_id: int) -> bool:
    """Bind any pending flag to the print that just started, and return it.

    Called once per print start. Always writes ``_active`` — including the
    ``False`` case — so a print Bambuddy didn't dispatch clears its
    predecessor's flag rather than inheriting it.
    """
    injected = printer_id in _pending
    _pending.discard(printer_id)
    if injected:
        _active.add(printer_id)
        logger.debug("[DISPATCH-CTX] printer %s: running print has injected End G-code", printer_id)
    else:
        _active.discard(printer_id)
    return injected


def end_gcode_injected(printer_id: int) -> bool:
    """True if the print currently running on ``printer_id`` has End G-code."""
    return printer_id in _active


def clear(printer_id: int) -> None:
    """Forget everything about this printer (disconnect, removal, tests)."""
    _pending.discard(printer_id)
    _active.discard(printer_id)
