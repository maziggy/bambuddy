"""Hold an automatic slot unlink until "the spool is gone" has lasted.

Both inventory backends unlink a slot's spool when the AMS says the slot is
empty: the built-in inventory deletes the ``spool_assignment`` row, Spoolman
mode deletes the ``spoolman_slot_assignments`` row. Until #3186 one MQTT push
was enough. An idle X1 Carbon on X1Plus firmware sent a push that cleared an
entire AMS unit -- presence bits off, colour and type blank -- and four saved
assignments were deleted in the same instant. The spools never moved. When
the next push reported them again, nothing brought the rows back, and the
identity of a non-RFID spool exists nowhere but in that row.

A spool that really was taken out stays out, so the evidence is cheap to
confirm: note when a slot first looks empty, keep the row, and unlink only if
it still looks empty ``GRACE_SECONDS`` later. A slot that reads normally again
in between is forgotten. Evidence of a *different* spool -- another colour or
type, another Bambu tag -- is not held here; callers unlink that immediately.

The unlink passes run only when the AMS hash changes, so a slot that goes
empty and stays empty might never be looked at again. Holding a removal
therefore also schedules one re-check per printer, which re-runs just the
cleanup passes against the printer's current AMS state.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

GRACE_SECONDS = 120.0

# A hold that nobody re-observed for this long is out of date -- the pass that
# would have seen it did not run (Spoolman unreachable, printer offline) -- so
# the next sighting starts the clock again instead of confirming at once.
_STALE_AFTER = 2 * GRACE_SECONDS

# (printer_id, scope, ams_id, tray_id, spool_id) -> (first_seen, last_seen)
_held: dict[tuple, tuple[float, float]] = {}
_recheck_tasks: dict[int, asyncio.Task] = {}
_recheck: Callable[[int], Awaitable[None]] | None = None

# Indirection so tests can move the clock without touching time.monotonic
# itself, which the event loop reads too.
_now = time.monotonic


def set_recheck(callback: Callable[[int], Awaitable[None]] | None) -> None:
    """Register the coroutine that re-runs the cleanup passes for a printer."""
    global _recheck
    _recheck = callback


def removal_confirmed(printer_id: int, key: tuple) -> bool:
    """Has this slot looked empty for the whole grace period?

    ``key`` is ``(scope, ams_id, tray_id, spool_id)``, the spool id being the
    inventory spool or the Spoolman spool linked to the slot. Returns False while the
    removal is being held, and schedules a re-check for when it falls due.
    """
    full_key = (printer_id, *key)
    now = _now()
    first_seen, last_seen = _held.get(full_key, (now, now))
    if now - last_seen > _STALE_AFTER:
        first_seen = now
    _held[full_key] = (first_seen, now)
    if now - first_seen >= GRACE_SECONDS:
        return True
    _schedule_recheck(printer_id, GRACE_SECONDS - (now - first_seen))
    return False


def is_held(printer_id: int, key: tuple) -> bool:
    """Is a removal already being held for this slot?

    Lets a caller tell a spool swap from a spool the AMS merely cannot read.
    A slot that reports occupied-but-blank is normally kept (#3100), but if it
    was reported *empty* first, a spool came out and another went in -- that
    keeps the hold running instead of cancelling it.
    """
    entry = _held.get((printer_id, *key))
    return entry is not None and _now() - entry[1] <= _STALE_AFTER


def forget_slot(printer_id: int, ams_id: int, tray_id: int) -> None:
    """Drop any hold on a slot a spool has just been assigned or linked to.

    A new assignment is fresher evidence than any empty report before it. It
    can carry the same key as the hold -- the same spool put back and assigned
    again -- so without this the user's own assignment could run out the old
    clock and be deleted.
    """
    for full_key in [k for k in _held if k[0] == printer_id and k[2] == ams_id and k[3] == tray_id]:
        del _held[full_key]


def settle(printer_id: int, scope: str, held_keys: set[tuple]) -> None:
    """Forget holds in ``scope`` that this pass did not hold again.

    Called at the end of each pass with the keys it held. Anything else --
    a slot that reads normally again, a removal just confirmed and unlinked,
    an assignment that went away by other means -- no longer needs its clock.
    """
    for full_key in [k for k in _held if k[0] == printer_id and k[1] == scope and k[1:] not in held_keys]:
        del _held[full_key]


def _schedule_recheck(printer_id: int, delay: float) -> None:
    if _recheck is None:
        return
    task = _recheck_tasks.get(printer_id)
    if task is not None and not task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _recheck_tasks[printer_id] = loop.create_task(_run_recheck(printer_id, delay))


async def _run_recheck(printer_id: int, delay: float) -> None:
    await asyncio.sleep(max(delay, 0) + 1)
    # Drop the handle first, so a hold the re-check itself renews can schedule
    # the next one.
    _recheck_tasks.pop(printer_id, None)
    callback = _recheck
    if callback is None:
        return
    try:
        await callback(printer_id)
    except Exception:
        logger.exception("Held slot unlink re-check failed for printer %s", printer_id)


def reset() -> None:
    """Drop every hold and cancel pending re-checks (shutdown, tests)."""
    for task in _recheck_tasks.values():
        try:
            task.cancel()
        except RuntimeError:
            # Its event loop is already closed; the task can never run.
            pass
    _recheck_tasks.clear()
    _held.clear()
