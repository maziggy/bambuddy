"""Event-loop stall watchdog (#1486).

A frozen asyncio event loop is invisible: it produces no log line and no
traceback — the HTTP server just goes silent, ``/health`` hangs, and the
process can stop responding to SIGTERM. Several "container hangs after adding
a printer" reports had exactly this shape, with nothing in the logs to act on.

This watchdog makes such a freeze diagnosable. An async heartbeat re-arms
``faulthandler.dump_traceback_later()`` every ``HEARTBEAT_INTERVAL`` seconds,
always ``STALL_THRESHOLD`` seconds ahead. While the loop keeps ticking the
timer is cancelled and re-armed before it can fire. If the loop stalls, the
heartbeat can't re-arm — and faulthandler's timer runs in a dedicated C-level
thread that fires regardless of the frozen loop, dumping *every* thread's
stack to stderr. The blocked frame then shows up in ``docker compose logs``.
"""

import asyncio
import faulthandler
import logging

logger = logging.getLogger(__name__)

# How often the heartbeat cancels + re-arms the faulthandler timer. Must be
# comfortably below STALL_THRESHOLD so a healthy loop always re-arms in time.
HEARTBEAT_INTERVAL = 10.0

# The loop must be unresponsive for at least this long before thread stacks
# are dumped. Generous on purpose: no legitimate on-loop operation should
# block for 30s, so anything that does is itself a bug worth a stack dump.
STALL_THRESHOLD = 30.0

_watchdog_task: asyncio.Task | None = None


async def _heartbeat_loop() -> None:
    """Re-arm the faulthandler stall timer on every tick."""
    while True:
        try:
            faulthandler.cancel_dump_traceback_later()
            # repeat=False: one dump pinpoints a hard freeze. If the loop
            # recovers and stalls again, the next heartbeat re-arms anyway.
            faulthandler.dump_traceback_later(STALL_THRESHOLD, repeat=False)
        except Exception as e:  # never let the watchdog itself crash the app
            logger.warning("Loop watchdog re-arm failed: %s", e)
        try:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
        except asyncio.CancelledError:
            break


def start_loop_watchdog() -> None:
    """Start the event-loop stall watchdog. Idempotent."""
    global _watchdog_task
    if _watchdog_task is not None:
        return
    if not faulthandler.is_enabled():
        # Also installs handlers for fatal signals (SIGSEGV etc.) — harmless
        # and useful; the dump_traceback_later timer works either way.
        faulthandler.enable()
    _watchdog_task = asyncio.create_task(_heartbeat_loop())
    logger.info(
        "Event-loop stall watchdog started — dumps all thread stacks to stderr if the loop stalls for more than %.0fs",
        STALL_THRESHOLD,
    )


def stop_loop_watchdog() -> None:
    """Stop the watchdog and disarm the pending stall timer."""
    global _watchdog_task
    if _watchdog_task is not None:
        _watchdog_task.cancel()
        _watchdog_task = None
    try:
        faulthandler.cancel_dump_traceback_later()
    except Exception:
        pass
    logger.info("Event-loop stall watchdog stopped")
