"""Automatic filament consumption tracking.

Captures AMS tray remain% at print start, then computes consumption
deltas at print complete to update spool weight_used and last_used.

Primary tracking uses 3MF slicer estimates (precise per-filament data).
AMS remain% delta is the fallback for trays not covered by 3MF data.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spool_usage_history import SpoolUsageHistory

logger = logging.getLogger(__name__)


@dataclass
class PrintSession:
    printer_id: int
    print_name: str
    started_at: datetime
    tray_remain_start: dict[tuple[int, int], int] = field(default_factory=dict)


# Module-level storage, keyed by printer_id
_active_sessions: dict[int, PrintSession] = {}


async def on_print_start(printer_id: int, data: dict, printer_manager) -> None:
    """Capture AMS tray remain% at print start."""
    state = printer_manager.get_status(printer_id)
    if not state or not state.raw_data:
        logger.debug("[UsageTracker] No state for printer %d, skipping", printer_id)
        return

    ams_raw = state.raw_data.get("ams", [])
    ams_data = ams_raw.get("ams", []) if isinstance(ams_raw, dict) else ams_raw if isinstance(ams_raw, list) else []
    if not ams_data:
        logger.debug("[UsageTracker] No AMS data for printer %d, skipping", printer_id)
        return

    tray_remain_start: dict[tuple[int, int], int] = {}
    for ams_unit in ams_data:
        ams_id = int(ams_unit.get("id", 0))
        for tray in ams_unit.get("tray", []):
            tray_id = int(tray.get("id", 0))
            remain = tray.get("remain", -1)
            if isinstance(remain, int) and 0 <= remain <= 100:
                tray_remain_start[(ams_id, tray_id)] = remain

    print_name = data.get("subtask_name", "") or data.get("filename", "unknown")

    # Always create session (even without valid remain data) so print_name
    # is available at completion for 3MF-based tracking
    session = PrintSession(
        printer_id=printer_id,
        print_name=print_name,
        started_at=datetime.now(timezone.utc),
        tray_remain_start=tray_remain_start,
    )
    _active_sessions[printer_id] = session

    if tray_remain_start:
        logger.info(
            "[UsageTracker] Captured start remain%% for printer %d (%d trays): %s",
            printer_id,
            len(tray_remain_start),
            {f"{k[0]}-{k[1]}": v for k, v in tray_remain_start.items()},
        )
    else:
        logger.debug("[UsageTracker] No valid remain%% for printer %d, 3MF fallback available", printer_id)


async def on_print_complete(
    printer_id: int,
    data: dict,
    printer_manager,
    db: AsyncSession,
    archive_id: int | None = None,
) -> list[dict]:
    """Compute consumption deltas and update spool weight_used/last_used.

    Uses two tracking strategies in priority order:
    1. 3MF per-filament estimates (primary) — precise slicer data for all spools
    2. AMS remain% delta (fallback) — only for trays not already handled by 3MF

    Returns a list of dicts describing what was logged (for WebSocket broadcast).
    """
    session = _active_sessions.pop(printer_id, None)
    status = data.get("status", "completed")
    results = []
    handled_trays: set[tuple[int, int]] = set()

    # --- Path 1 (PRIMARY): 3MF per-filament estimates ---
    if archive_id:
        print_name = (
            (session.print_name if session else None) or data.get("subtask_name", "") or data.get("filename", "unknown")
        )
        threemf_results = await _track_from_3mf(
            printer_id, archive_id, status, print_name, handled_trays, printer_manager, db
        )
        results.extend(threemf_results)

    # --- Path 2 (FALLBACK): AMS remain% delta (only for trays not handled by 3MF) ---
    if session and session.tray_remain_start:
        state = printer_manager.get_status(printer_id)
        if state and state.raw_data:
            ams_raw = state.raw_data.get("ams", [])
            ams_data = (
                ams_raw.get("ams", []) if isinstance(ams_raw, dict) else ams_raw if isinstance(ams_raw, list) else []
            )

            for ams_unit in ams_data:
                ams_id = int(ams_unit.get("id", 0))
                for tray in ams_unit.get("tray", []):
                    tray_id = int(tray.get("id", 0))
                    key = (ams_id, tray_id)

                    if key in handled_trays:
                        continue  # Already tracked via 3MF

                    if key not in session.tray_remain_start:
                        continue

                    current_remain = tray.get("remain", -1)
                    if not isinstance(current_remain, int) or current_remain < 0 or current_remain > 100:
                        continue

                    start_remain = session.tray_remain_start[key]
                    delta_pct = start_remain - current_remain

                    if delta_pct <= 0:
                        continue  # No consumption or tray was refilled

                    # Look up SpoolAssignment for this slot
                    result = await db.execute(
                        select(SpoolAssignment).where(
                            SpoolAssignment.printer_id == printer_id,
                            SpoolAssignment.ams_id == ams_id,
                            SpoolAssignment.tray_id == tray_id,
                        )
                    )
                    assignment = result.scalar_one_or_none()
                    if not assignment:
                        continue

                    # Load spool
                    spool_result = await db.execute(select(Spool).where(Spool.id == assignment.spool_id))
                    spool = spool_result.scalar_one_or_none()
                    if not spool:
                        continue

                    # Compute weight consumed
                    weight_grams = (delta_pct / 100.0) * spool.label_weight

                    # Update spool
                    spool.weight_used = (spool.weight_used or 0) + weight_grams
                    spool.last_used = datetime.now(timezone.utc)

                    # Insert usage history record
                    history = SpoolUsageHistory(
                        spool_id=spool.id,
                        printer_id=printer_id,
                        print_name=session.print_name,
                        weight_used=round(weight_grams, 1),
                        percent_used=delta_pct,
                        status=status,
                    )
                    db.add(history)

                    handled_trays.add(key)
                    results.append(
                        {
                            "spool_id": spool.id,
                            "weight_used": round(weight_grams, 1),
                            "percent_used": delta_pct,
                            "ams_id": ams_id,
                            "tray_id": tray_id,
                            "material": spool.material,
                        }
                    )

                    logger.info(
                        "[UsageTracker] Spool %d consumed %.1fg (%d%%) on printer %d AMS%d-T%d (AMS fallback, %s)",
                        spool.id,
                        weight_grams,
                        delta_pct,
                        printer_id,
                        ams_id,
                        tray_id,
                        status,
                    )

    if results:
        await db.commit()

    return results


async def _track_from_3mf(
    printer_id: int,
    archive_id: int,
    status: str,
    print_name: str,
    handled_trays: set[tuple[int, int]],
    printer_manager,
    db: AsyncSession,
) -> list[dict]:
    """Track usage from 3MF per-filament slicer data (primary path).

    Uses slicer-estimated filament weight for all spools (BL and non-BL).
    For partial prints (failed/aborted), tries per-layer gcode data first,
    then falls back to linear scaling by progress.

    Slot-to-tray mapping priority:
    1. Queue item ams_mapping (for queue-initiated prints)
    2. tray_now from printer state (for single-filament non-queue prints)
    3. Default mapping: slot_id - 1 = global_tray_id (last resort)
    """
    from backend.app.core.config import settings as app_settings
    from backend.app.models.archive import PrintArchive
    from backend.app.models.print_queue import PrintQueueItem
    from backend.app.utils.threemf_tools import extract_filament_usage_from_3mf

    result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
    archive = result.scalar_one_or_none()
    if not archive or not archive.file_path:
        return []

    file_path = app_settings.base_dir / archive.file_path
    if not file_path.exists():
        return []

    filament_usage = extract_filament_usage_from_3mf(file_path)
    if not filament_usage:
        return []

    # --- Resolve slot-to-tray mapping ---
    # 1. Try queue item ams_mapping (queue-initiated prints store the exact mapping)
    slot_to_tray = None
    queue_result = await db.execute(
        select(PrintQueueItem)
        .where(PrintQueueItem.archive_id == archive_id)
        .where(PrintQueueItem.status.in_(["printing", "completed", "failed"]))
    )
    queue_item = queue_result.scalar_one_or_none()
    if queue_item and queue_item.ams_mapping:
        try:
            slot_to_tray = json.loads(queue_item.ams_mapping)
        except (json.JSONDecodeError, TypeError):
            pass

    # 2. For single-filament non-queue prints, use tray_now from printer state
    nonzero_slots = [u for u in filament_usage if u.get("used_g", 0) > 0]
    tray_now_override: int | None = None
    if not slot_to_tray and len(nonzero_slots) == 1:
        state = printer_manager.get_status(printer_id)
        if state and 0 <= state.tray_now <= 254:
            tray_now_override = state.tray_now
        elif state and state.tray_now == 255:
            # 255 = "no filament" on legacy printers, but valid 2nd external spool on H2-series
            vt_tray = state.raw_data.get("vt_tray") or []
            if any(int(vt.get("id", 0)) == 255 for vt in vt_tray if isinstance(vt, dict)):
                tray_now_override = state.tray_now

    # Scale factor for partial prints (failed/aborted)
    if status == "completed":
        scale = 1.0
    else:
        state = printer_manager.get_status(printer_id)
        progress = state.progress if state else 0
        scale = max(0.0, min(progress / 100.0, 1.0))

    # Per-layer gcode accuracy for partial prints
    layer_grams: dict[int, float] | None = None
    if status != "completed":
        state = printer_manager.get_status(printer_id)
        current_layer = state.layer_num if state else 0
        if current_layer > 0:
            try:
                from backend.app.utils.threemf_tools import (
                    extract_filament_properties_from_3mf,
                    extract_layer_filament_usage_from_3mf,
                    get_cumulative_usage_at_layer,
                    mm_to_grams,
                )

                layer_usage = extract_layer_filament_usage_from_3mf(file_path)
                if layer_usage:
                    cumulative_mm = get_cumulative_usage_at_layer(layer_usage, current_layer)
                    filament_props = extract_filament_properties_from_3mf(file_path)
                    layer_grams = {}
                    for filament_id, mm_used in cumulative_mm.items():
                        slot_id = filament_id + 1  # 0-based to 1-based
                        props = filament_props.get(slot_id, {})
                        density = props.get("density", 1.24)
                        diameter = props.get("diameter", 1.75)
                        layer_grams[slot_id] = mm_to_grams(mm_used, diameter, density)
            except Exception:
                pass  # Fall back to linear scaling

    results = []

    for usage in filament_usage:
        slot_id = usage.get("slot_id", 0)
        used_g = usage.get("used_g", 0)
        if used_g <= 0:
            continue

        # Map 3MF slot_id to physical (ams_id, tray_id) using resolved mapping
        if tray_now_override is not None:
            # Single-filament non-queue print: use actual tray from printer state
            global_tray_id = tray_now_override
        else:
            # Queue mapping or default: slot_id - 1, overridden by ams_mapping
            global_tray_id = slot_id - 1
            if slot_to_tray and slot_id <= len(slot_to_tray):
                mapped = slot_to_tray[slot_id - 1]
                if isinstance(mapped, int) and mapped >= 0:
                    global_tray_id = mapped

        if global_tray_id >= 254:
            # External spool: ams_id=255 (sentinel), tray_id=slot index (0 or 1)
            ams_id = 255
            tray_id = global_tray_id - 254
        elif global_tray_id >= 128:
            ams_id = global_tray_id
            tray_id = 0
        else:
            ams_id = global_tray_id // 4
            tray_id = global_tray_id % 4

        key = (ams_id, tray_id)
        if key in handled_trays:
            continue

        # Find spool assignment for this tray
        assign_result = await db.execute(
            select(SpoolAssignment).where(
                SpoolAssignment.printer_id == printer_id,
                SpoolAssignment.ams_id == ams_id,
                SpoolAssignment.tray_id == tray_id,
            )
        )
        assignment = assign_result.scalar_one_or_none()
        if not assignment:
            continue

        # Load spool
        spool_result = await db.execute(select(Spool).where(Spool.id == assignment.spool_id))
        spool = spool_result.scalar_one_or_none()
        if not spool:
            continue

        # Use per-layer grams if available, otherwise linear scale
        if layer_grams and slot_id in layer_grams:
            weight_grams = layer_grams[slot_id]
        else:
            weight_grams = used_g * scale

        if weight_grams <= 0:
            continue

        # Update spool
        spool.weight_used = (spool.weight_used or 0) + weight_grams
        spool.last_used = datetime.now(timezone.utc)

        percent = round(weight_grams / (spool.label_weight or 1000) * 100)

        # Insert usage history record
        history = SpoolUsageHistory(
            spool_id=spool.id,
            printer_id=printer_id,
            print_name=print_name,
            weight_used=round(weight_grams, 1),
            percent_used=percent,
            status=status,
        )
        db.add(history)

        handled_trays.add(key)
        results.append(
            {
                "spool_id": spool.id,
                "weight_used": round(weight_grams, 1),
                "percent_used": percent,
                "ams_id": ams_id,
                "tray_id": tray_id,
                "material": spool.material,
            }
        )

        # Determine mapping source for debug logging
        if tray_now_override is not None:
            map_src = ", tray_now"
        elif slot_to_tray:
            map_src = ", queue_map"
        else:
            map_src = ""
        logger.info(
            "[UsageTracker] Spool %d consumed %.1fg (3MF%s%s) on printer %d AMS%d-T%d (%s)",
            spool.id,
            weight_grams,
            " per-layer" if (layer_grams and slot_id in layer_grams) else (f" scaled {scale:.0%}" if scale < 1 else ""),
            map_src,
            printer_id,
            ams_id,
            tray_id,
            status,
        )

    return results
