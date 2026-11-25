"""Time to the next pause the slicer put into a print, and a heads-up before it (#2984).

The pauses come from the plate's G-code (threemf_tools.parse_gcode_pause_points)
with the slicer's time left at each. The time to one is the printer's time left
minus that: both count down to the end of the same print.

The printer manager serializes ``next_pause`` into every status, so nothing here
imports it at module level.
"""

import logging
import math
import time

from backend.app.core.tasks import spawn_background_task

logger = logging.getLogger(__name__)

DEFAULT_WARNING_MINUTES = 5
# The largest heads-up the setting allows. A pause further away than this is
# not looked at again until it comes closer, so the setting is read at most
# once per check interval, and only while a pause is this close.
MAX_WARNING_MINUTES = 120
# Time left is reported in whole minutes; checking more often finds nothing new.
_CHECK_INTERVAL_SECONDS = 30.0

_last_check: dict[int, float] = {}


def next_pause(state) -> dict | None:
    """The next pause of the running print: ``{"layer", "minutes"}``, or None.

    The next is the first on a layer above the one printing: the printer is
    on a pause's layer while it waits there and after it resumes, so that
    pause is behind it. ``minutes`` is None while the printer reports no time
    left (the start of a print); the layer is known all the same. Only while
    RUNNING: a paused print is waiting at its pause, or was paused by hand.
    """
    if getattr(state, "state", None) != "RUNNING":
        return None
    layer_now = getattr(state, "layer_num", 0) or 0
    remaining_now = getattr(state, "remaining_time", 0) or 0
    # The slicer's times are for standard speed. In another mode the printer
    # rescales its own time left, by a factor not measured yet, so a time to
    # the pause worked out from both would be wrong; the layer is still right.
    standard_speed = getattr(state, "speed_level", 2) == 2
    for point in getattr(state, "pause_points", None) or ():
        if point.layer > layer_now:
            minutes = None
            if standard_speed and remaining_now > 0 and point.remaining_minutes is not None:
                minutes = max(0, remaining_now - point.remaining_minutes)
            return {"layer": point.layer, "minutes": minutes}
    return None


def observe(printer_id: int, state) -> None:
    """Check on a status push whether a pause is close enough to warn about.

    Cheap on every push: the setting is read in a task, at most once per
    check interval, and only while an unwarned pause is within the largest
    heads-up the setting allows.
    """
    upcoming = next_pause(state)
    if (
        upcoming is None
        or upcoming["minutes"] is None
        or upcoming["minutes"] > MAX_WARNING_MINUTES
        or upcoming["layer"] in state.pause_warned_layers
    ):
        return
    now = time.monotonic()
    if now - _last_check.get(printer_id, -math.inf) < _CHECK_INTERVAL_SECONDS:
        return
    _last_check[printer_id] = now
    spawn_background_task(_warn_if_due(printer_id, upcoming["layer"]), name=f"pause-warning-{printer_id}")


async def warning_minutes(db) -> int:
    """The heads-up setting, in minutes; the default for a missing or bad value."""
    from backend.app.api.routes.settings import get_setting

    value = await get_setting(db, "pause_warning_minutes")
    try:
        minutes = int(value) if value else DEFAULT_WARNING_MINUTES
    except (TypeError, ValueError):
        return DEFAULT_WARNING_MINUTES
    return min(max(minutes, 1), MAX_WARNING_MINUTES)


# Kept on the print's archive under this key, so a restart does not warn again.
_WARNED_KEY = "pause_warned_layers"


def warned_layers_of(archive, state) -> set[int]:
    """The pauses this print was warned about before a restart.

    Only for the same run: a reprint reuses its archive row, and its
    subtask_id is the one thing that tells the runs apart.
    """
    saved = (getattr(archive, "extra_data", None) or {}).get(_WARNED_KEY) or {}
    subtask_id = str(getattr(state, "subtask_id", "") or "")
    if not isinstance(saved, dict) or subtask_id in ("", "0") or saved.get("subtask_id") != subtask_id:
        return set()
    return {layer for layer in saved.get("layers") or [] if isinstance(layer, int)}


async def _remember_warned(db, printer_id: int, state) -> None:
    from backend.app.main import running_print_archive

    archive = await running_print_archive(db, printer_id, state, printing_only=True, by_name=False)
    if archive is None:
        return
    subtask_id = str(getattr(state, "subtask_id", "") or "")
    archive.extra_data = {
        **(archive.extra_data or {}),
        _WARNED_KEY: {"subtask_id": subtask_id, "layers": sorted(state.pause_warned_layers)},
    }
    await db.commit()


async def _warn_if_due(printer_id: int, layer: int) -> None:
    from backend.app.core.database import async_session
    from backend.app.models.printer import Printer
    from backend.app.services.notification_service import notification_service
    from backend.app.services.printer_manager import printer_manager

    try:
        async with async_session() as db:
            lead = await warning_minutes(db)
            client = printer_manager.get_client(printer_id)
            if client is None:
                return
            state = client.state
            # Read again after the await: the print may have moved on.
            upcoming = next_pause(state)
            if (
                upcoming is None
                or upcoming["layer"] != layer
                or upcoming["minutes"] is None
                or upcoming["minutes"] > lead
                or layer in state.pause_warned_layers
            ):
                return
            state.pause_warned_layers.add(layer)
            printer = await db.get(Printer, printer_id)
            if printer is None:
                return
            await notification_service.on_print_pause_upcoming(
                printer_id,
                printer.name,
                state.subtask_name or state.gcode_file or "Unknown",
                minutes=upcoming["minutes"],
                layer=layer,
                total_layers=state.total_layers,
                db=db,
            )
            try:
                await _remember_warned(db, printer_id, state)
            except Exception:
                logger.warning(
                    "[Printer %s] Could not keep the pause heads-up on the archive", printer_id, exc_info=True
                )
    except Exception:
        logger.warning("[Printer %s] Pause heads-up failed", printer_id, exc_info=True)
