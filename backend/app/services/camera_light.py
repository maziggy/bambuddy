"""Chamber light for the camera (#1655).

A printer kept dark between uses gives black live views and black snapshots.
The app setting ``camera_light_mode`` picks the printers this applies to:
"off", "all", or "selected" (those with ``Printer.camera_light_auto``). For
those, every camera use holds the light: the first
holder turns it on, and once the last one lets go it is turned off again after
a short grace, so a reconnecting viewer or the camera wall's periodic
snapshots don't make it flash.

Bambuddy only ever turns off a light it turned on itself. A light that was
already on stays on, and switching the light by hand hands it back to the user.

Layer timelapse, the Obico check and the in-print frame bank don't hold the
light: they capture all through a print, and the light would flash with every
frame.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

# Long enough to cover a viewer reconnecting and the camera wall polling each
# tile every 8 seconds.
OFF_GRACE_SECONDS = 15.0

# Upper bound for camera_light_delay. The finish photo's producer budget
# (_FINISH_PHOTO_PRODUCER_WAIT_SECONDS in main.py) has about 8 seconds to spare
# beyond its plate settle and worst-case grab; a longer wait would push the
# producer past it and into the #1790 race with the fallback grab.
MAX_DELAY_SECONDS = 5.0

# The two app settings are read at most this often: the camera wall asks for a
# snapshot of every tile every 8 seconds, which on a large farm is many reads a
# second. A saved change applies at once through invalidate_settings().
_SETTINGS_TTL_SECONDS = 5.0
_MODES = frozenset({"off", "all", "selected"})

# A light we switched on can still read as off until the printer reports back.
# Within this window we trust our own command instead of sending another.
_REPORT_LAG_SECONDS = 5.0

_holders: dict[int, int] = {}
_lit_at: dict[int, float] = {}  # printers whose light we turned on, and when
_off_tasks: dict[int, asyncio.Task] = {}
_settings_cache: tuple[float, str, float] | None = None  # (read at, mode, delay)


async def _read_settings() -> tuple[str, float]:
    from backend.app.api.routes.settings import get_setting
    from backend.app.core import database

    async with database.async_session() as db:
        mode = await get_setting(db, "camera_light_mode")
        delay = await get_setting(db, "camera_light_delay")
    mode = mode if mode in _MODES else "off"
    try:
        seconds = float(delay) if delay is not None else 2.0
    except ValueError:
        seconds = 2.0
    return mode, min(max(0.0, seconds), MAX_DELAY_SECONDS)


async def _light_settings() -> tuple[str, float]:
    """The light mode and snapshot delay, cached briefly. Off when unreadable."""
    global _settings_cache
    now = time.monotonic()
    if _settings_cache is not None and now - _settings_cache[0] < _SETTINGS_TTL_SECONDS:
        return _settings_cache[1], _settings_cache[2]
    try:
        mode, delay = await _read_settings()
    except Exception as e:
        # The light must never cost the picture, and an unreadable setting
        # must not switch lights it was never asked to switch.
        logger.debug("[CAMERA-LIGHT] Could not read the light settings: %s", e)
        return "off", 2.0
    _settings_cache = (now, mode, delay)
    return mode, delay


def invalidate_settings() -> None:
    """Make the next camera use read the light settings again."""
    global _settings_cache
    _settings_cache = None


def _get_client(printer_id: int):
    from backend.app.services.printer_manager import printer_manager

    return printer_manager.get_client(printer_id)


def _cancel_off(printer_id: int) -> None:
    task = _off_tasks.pop(printer_id, None)
    if task is not None and not task.done():
        task.cancel()


def _acquire(printer_id: int) -> float | None:
    """Hold the light. Returns when it was turned on, or None if it was already on.

    The hold is counted before anything can fail, so the caller always releases.
    """
    _holders[printer_id] = _holders.get(printer_id, 0) + 1
    _cancel_off(printer_id)

    client = _get_client(printer_id)
    if client is None or client.state is None or client.state.chamber_light:
        return None

    lit_at = _lit_at.get(printer_id)
    if lit_at is not None and time.monotonic() - lit_at < _REPORT_LAG_SECONDS:
        return lit_at  # our "on" hasn't been reported yet

    if not client.set_chamber_light(True):
        return None
    logger.info("[CAMERA-LIGHT] Turned on chamber light for printer %s", printer_id)
    now = time.monotonic()
    _lit_at[printer_id] = now
    return now


def _release(printer_id: int) -> None:
    count = _holders.get(printer_id, 0) - 1
    if count > 0:
        _holders[printer_id] = count
        return
    _holders.pop(printer_id, None)
    if printer_id in _lit_at:
        _cancel_off(printer_id)
        try:
            _off_tasks[printer_id] = asyncio.create_task(_turn_off_later(printer_id))
        except RuntimeError:
            # No running loop: a stream finalised during shutdown. Nothing can
            # switch the light any more.
            pass


async def _turn_off_later(printer_id: int) -> None:
    try:
        await asyncio.sleep(OFF_GRACE_SECONDS)
    except asyncio.CancelledError:
        return
    _off_tasks.pop(printer_id, None)
    if _holders.get(printer_id) or _lit_at.pop(printer_id, None) is None:
        return
    client = _get_client(printer_id)
    if client is None:
        return
    try:
        if client.set_chamber_light(False):
            logger.info("[CAMERA-LIGHT] Turned chamber light back off for printer %s", printer_id)
    except Exception as e:
        logger.warning("[CAMERA-LIGHT] Failed to turn chamber light off for printer %s: %s", printer_id, e)


def hand_back(printer_id: int) -> None:
    """The user switched the light: it is theirs now, so never turn it off."""
    _lit_at.pop(printer_id, None)
    _cancel_off(printer_id)


@asynccontextmanager
async def camera_light(
    printer, *, wait: bool = True, any_mode: bool = False, settle: float | None = None
) -> AsyncIterator[None]:
    """Keep the printer's chamber light on while the camera is in use.

    ``wait`` makes a snapshot wait the ``camera_light_delay`` setting after
    the light came on, so the light (and any light synced to it) has time to
    reach the picture. A live view doesn't wait.

    ``any_mode`` holds the light whatever the light mode says, and ``settle``
    replaces the delay setting: the plate check needs a lit plate on every
    printer, and holding the light here rather than switching it itself keeps
    it from switching the light off under a viewer who came meanwhile (SP-C11).
    """
    if printer is None:
        yield
        return
    mode, delay = await _light_settings()
    if settle is not None:
        delay = settle
    if not any_mode and (
        mode == "off" or (mode == "selected" and getattr(printer, "camera_light_auto", False) is not True)
    ):
        yield
        return

    printer_id = printer.id
    try:
        try:
            lit_at = _acquire(printer_id)
        except Exception as e:
            # A light we can't switch must never cost the picture.
            logger.warning("[CAMERA-LIGHT] Failed to turn chamber light on for printer %s: %s", printer_id, e)
            lit_at = None
        if wait and lit_at is not None:
            remaining = delay - (time.monotonic() - lit_at)
            if remaining > 0:
                await asyncio.sleep(remaining)
        yield
    finally:
        _release(printer_id)
