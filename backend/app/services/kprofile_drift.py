"""Put a slot's stored K-profile back when the printer has lost the selection.

A tray's ``cali_idx`` can fall back to -1 without anything else in the AMS
report changing: an X1 Carbon power-cycled mid-print came back with every slot
on the default K while spools, tags and remain% were exactly as before (#3219).
``on_ams_change`` is keyed on tray_type / tag_uid / remain, so it never saw
that, and two queued jobs went out on the default K.

This module re-selects the stored profile for such a slot. It acts only when
the selection is *lost* (``cali_idx`` -1 or missing), never when the slot
holds a different real profile: that one was picked on purpose, in Bambu
Studio for example. A profile picked in Bambuddy's Configure Slot dialog is
saved as the spool's stored profile anyway, but a "Default" pick there is not
saved anywhere, so it is recorded through ``note_slot_configured`` and left
alone too.

Two callers: the status handler, on every push while the printer is idle, and
the print scheduler, just before it sends a job.
"""

import logging
import time
from dataclasses import dataclass

from backend.app.core.database import async_session
from backend.app.services.printer_manager import printer_manager
from backend.app.services.slot_kprofile import find_slot_kprofile_for_extruder
from backend.app.services.slot_nozzle import resolve_slot_nozzle
from backend.app.services.spool_filament_preset import printer_safe_filament_id
from backend.app.utils.printer_models import is_dual_nozzle_model

logger = logging.getLogger(__name__)

# States in which re-selecting a profile cannot touch a print. PREPARE and
# SLICING are left out on purpose: the printer is starting a job there.
IDLE_STATES = frozenset({"IDLE", "FINISH", "FAILED"})

# Time between two re-selections of one slot: the push right after a send can
# still show the old value, so an immediate resend would only be noise.
RETRY_INTERVAL_S = 30.0
# Time before a slot with no stored profile is looked up again. Most slots on
# the default K are there on purpose, and on an install with many printers a
# lookup per slot every 30 seconds would add up for nothing. A reconnect (the
# #3219 case) clears this, so a power cycle is still caught at once.
NO_PROFILE_RECHECK_S = 600.0
# Re-selections of one profile before giving up on it. A stored cali_idx the
# printer no longer has will never stick; this keeps that from repeating forever.
MAX_ATTEMPTS = 3

SlotKey = tuple[int, int, int]


@dataclass
class _SlotWatch:
    next_check: float = 0.0
    attempts: int = 0
    gave_up_logged: bool = False


_watches: dict[SlotKey, _SlotWatch] = {}
_default_chosen: set[SlotKey] = set()
_running: set[int] = set()


def note_slot_configured(printer_id: int, ams_id: int, tray_id: int, cali_idx: int) -> None:
    """Record a K-profile picked for a slot in Bambuddy's Configure Slot dialog.

    A default pick (-1) is respected until the slot is emptied or configured
    again. It is not cleared by the slot reporting a real profile, because the
    push right after the pick can still carry the old one.
    """
    key = (printer_id, ams_id, tray_id)
    if cali_idx < 0:
        _default_chosen.add(key)
    else:
        _default_chosen.discard(key)


def forget_printer(printer_id: int, *, deleted: bool = False) -> None:
    """Drop the retry bookkeeping for a printer, e.g. when it disconnects.

    A reconnect is a fresh start: a profile given up on before may stick now.
    A deliberate default pick is kept -- it still describes the slot -- unless
    the printer was deleted: then it describes nothing, and SQLite gives the id
    to the next printer created.
    """
    for key in [k for k in _watches if k[0] == printer_id]:
        del _watches[key]
    if deleted:
        for key in [k for k in _default_chosen if k[0] == printer_id]:
            _default_chosen.discard(key)


def _selection_lost(tray: dict) -> bool:
    cali_idx = tray.get("cali_idx")
    if cali_idx is None:
        return True
    try:
        return int(cali_idx) < 0
    except (TypeError, ValueError):
        return False


def _loaded_trays(state) -> list[tuple[int, int, int, dict]]:
    """``(ams_id, tray_id, global_tray_id, tray)`` for every loaded slot.

    External spools use the assignment addressing (ams 255, tray 0/1) and the
    global id the print command carries for them (254/255).
    """
    raw = getattr(state, "raw_data", None) or {}
    ams_raw = raw.get("ams")
    units = ams_raw.get("ams", []) if isinstance(ams_raw, dict) else ams_raw if isinstance(ams_raw, list) else []
    trays: list[tuple[int, int, int, dict]] = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        try:
            ams_id = int(unit.get("id", 0))
        except (TypeError, ValueError):
            continue
        for tray in unit.get("tray") or []:
            if not isinstance(tray, dict):
                continue
            try:
                tray_id = int(tray.get("id", 0))
            except (TypeError, ValueError):
                continue
            global_id = ams_id if ams_id >= 128 else ams_id * 4 + tray_id
            trays.append((ams_id, tray_id, global_id, tray))

    vt_raw = raw.get("vt_tray")
    if isinstance(vt_raw, dict):
        vt_raw = [vt_raw]
    for vt in vt_raw if isinstance(vt_raw, list) else []:
        if not isinstance(vt, dict):
            continue
        try:
            vt_id = int(vt.get("id", 254))
        except (TypeError, ValueError):
            continue
        if vt_id in (254, 255):
            trays.append((255, vt_id - 254, vt_id, vt))
    return trays


def _settle(key: SlotKey, tray: dict) -> bool:
    """Clear bookkeeping a slot no longer needs; True if it is a candidate.

    A candidate is a loaded slot whose selection is lost and was not set to
    the default on purpose.
    """
    if not tray.get("tray_type"):
        _watches.pop(key, None)
        _default_chosen.discard(key)
        return False
    if not _selection_lost(tray):
        _watches.pop(key, None)
        return False
    return key not in _default_chosen


def _due(printer_id: int, key: SlotKey, now: float) -> bool:
    watch = _watches.get(key)
    if watch is None:
        return True
    if now < watch.next_check:
        return False
    if watch.attempts < MAX_ATTEMPTS:
        return True
    # The last re-selection had its interval to land and the slot is still
    # lost: say so once, then leave the slot alone until it changes.
    if not watch.gave_up_logged:
        watch.gave_up_logged = True
        logger.warning(
            "[Printer %s] AMS%d-T%d still has no K-profile after %d re-selections of the stored "
            "one; giving up until the slot changes. Check that the profile still exists on the printer.",
            printer_id,
            key[1],
            key[2],
            MAX_ATTEMPTS,
        )
    return False


def needs_check(printer_id: int, state) -> bool:
    """Cheap test the status handler runs on every push before spawning a check."""
    if printer_id in _running:
        return False
    if not getattr(state, "connected", False):
        return False
    if (getattr(state, "state", "") or "").upper() not in IDLE_STATES:
        return False
    now = time.monotonic()
    found = False
    for ams_id, tray_id, _global_id, tray in _loaded_trays(state):
        key = (printer_id, ams_id, tray_id)
        if _settle(key, tray) and _due(printer_id, key, now):
            found = True
    return found


async def reapply_lost_kprofiles(
    printer_id: int, global_trays: set[int] | None = None, *, throttle: bool = True
) -> int:
    """Re-select the stored K-profile on every slot whose selection was lost.

    ``global_trays`` limits the pass to the trays a print uses. ``throttle``
    off is for the dispatch guard: a job about to start must not wait out a
    retry interval, and that runs once per job anyway. Returns how many slots
    were re-selected.
    """
    # The dispatch guard runs regardless: a pass already in flight may have
    # skipped this job's trays on its retry interval.
    if throttle and printer_id in _running:
        return 0
    state = printer_manager.get_status(printer_id)
    client = printer_manager.get_client(printer_id)
    if state is None or client is None or not getattr(state, "connected", False):
        return 0

    model = printer_manager.get_model(printer_id)
    nozzles = getattr(state, "nozzles", None) or []
    dual_nozzle = is_dual_nozzle_model(model) or (len(nozzles) > 1 and bool(getattr(nozzles[1], "nozzle_diameter", "")))
    now = time.monotonic()
    candidates = []
    for ams_id, tray_id, global_id, tray in _loaded_trays(state):
        key = (printer_id, ams_id, tray_id)
        if not _settle(key, tray):
            continue
        if global_trays is not None and global_id not in global_trays:
            continue
        if throttle and not _due(printer_id, key, now):
            continue
        # Claimed before the first await, so a push arriving meanwhile does
        # not pick the same slot up a second time.
        _watches.setdefault(key, _SlotWatch()).next_check = now + RETRY_INTERVAL_S
        candidates.append((key, ams_id, tray_id, tray))
    if not candidates:
        return 0

    applied = 0
    if throttle:
        _running.add(printer_id)
    try:
        async with async_session() as db:
            for key, ams_id, tray_id, tray in candidates:
                slot_nozzle = resolve_slot_nozzle(state, ams_id, tray_id, model)
                if dual_nozzle:
                    if slot_nozzle.extruder is None:
                        # Unknown nozzle on a dual machine: picking extruder 0
                        # would be a guess, and a wrong guess binds the other
                        # hotend's calibration.
                        continue
                    extruders = [slot_nozzle.extruder]
                else:
                    # One nozzle, but not one extruder number: the external
                    # holder resolves to 1 (it is H2D's Ext-L), and Configure
                    # Slot stores the profile under that, while the spool form
                    # stores 0. Both mean the only nozzle there is.
                    extruders = list(dict.fromkeys([0, slot_nozzle.extruder_or_default]))
                profile = None
                for extruder in extruders:
                    profile = await find_slot_kprofile_for_extruder(
                        db,
                        printer_id,
                        ams_id,
                        tray_id,
                        extruder,
                        slot_nozzle.diameter,
                        model,
                        slot_nozzle.flow,
                    )
                    if profile is not None:
                        break
                if profile is None or profile.cali_idx is None or profile.cali_idx < 0:
                    _watches.setdefault(key, _SlotWatch()).next_check = now + NO_PROFILE_RECHECK_S
                    continue

                client.extrusion_cali_sel(
                    ams_id=ams_id,
                    tray_id=tray_id,
                    cali_idx=profile.cali_idx,
                    filament_id=printer_safe_filament_id(profile.filament_id, tray.get("tray_info_idx")),
                    nozzle_diameter=slot_nozzle.diameter,
                )
                applied += 1
                watch = _watches.setdefault(key, _SlotWatch())
                watch.attempts += 1
                logger.info(
                    "[Printer %s] AMS%d-T%d lost its K-profile (cali_idx=%s): re-selected stored "
                    "profile %r (cali_idx=%d), attempt %d",
                    printer_id,
                    ams_id,
                    tray_id,
                    tray.get("cali_idx"),
                    profile.name,
                    profile.cali_idx,
                    watch.attempts,
                )
    except Exception:
        logger.exception("[Printer %s] K-profile re-selection failed", printer_id)
    finally:
        if throttle:
            _running.discard(printer_id)
    return applied
