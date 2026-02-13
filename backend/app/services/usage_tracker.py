"""Automatic filament consumption tracking.

Captures AMS tray remain% at print start, then computes consumption
deltas at print complete to update spool weight_used and last_used.

For non-BL spools (no RFID, AMS reports remain=-1), falls back to
per-filament usage estimates from the archived 3MF file.
"""

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

    Uses two tracking strategies:
    1. AMS remain% delta — for BL spools with valid RFID remain data
    2. 3MF per-filament estimates — for non-BL spools without remain data

    Returns a list of dicts describing what was logged (for WebSocket broadcast).
    """
    session = _active_sessions.pop(printer_id, None)
    status = data.get("status", "completed")
    results = []
    handled_trays: set[tuple[int, int]] = set()

    # --- Path 1: AMS remain% delta (for spools with valid RFID remain data) ---
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
                        }
                    )

                    logger.info(
                        "[UsageTracker] Spool %d consumed %.1fg (%d%%) on printer %d AMS%d-T%d (%s)",
                        spool.id,
                        weight_grams,
                        delta_pct,
                        printer_id,
                        ams_id,
                        tray_id,
                        status,
                    )

    # --- Path 2: 3MF per-filament estimates (for non-BL spools without remain data) ---
    if archive_id:
        print_name = (
            (session.print_name if session else None) or data.get("subtask_name", "") or data.get("filename", "unknown")
        )
        threemf_results = await _track_from_3mf(
            printer_id, archive_id, status, print_name, handled_trays, printer_manager, db
        )
        results.extend(threemf_results)

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
    """Track usage from 3MF per-filament data for non-BL spools.

    Falls back to slicer-estimated filament weight when AMS remain% is
    unavailable (non-RFID spools). For partial prints (failed/aborted),
    scales the estimate by print progress.
    """
    from backend.app.core.config import settings as app_settings
    from backend.app.models.archive import PrintArchive
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

    # Scale factor for partial prints (failed/aborted)
    if status == "completed":
        scale = 1.0
    else:
        state = printer_manager.get_status(printer_id)
        progress = state.progress if state else 0
        scale = max(0.0, min(progress / 100.0, 1.0))

    results = []

    for usage in filament_usage:
        slot_id = usage.get("slot_id", 0)
        used_g = usage.get("used_g", 0)
        if used_g <= 0:
            continue

        # Map 3MF slot_id (1-based) to (ams_id, tray_id)
        global_tray_id = slot_id - 1
        if global_tray_id >= 128:
            ams_id = global_tray_id
            tray_id = 0
        else:
            ams_id = global_tray_id // 4
            tray_id = global_tray_id % 4

        key = (ams_id, tray_id)
        if key in handled_trays:
            continue  # Already tracked via AMS remain% delta

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

        # Only use 3MF tracking for non-BL spools (BL spools use AMS remain%)
        if spool.tag_uid or spool.tray_uuid:
            continue

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

        results.append(
            {
                "spool_id": spool.id,
                "weight_used": round(weight_grams, 1),
                "percent_used": percent,
                "ams_id": ams_id,
                "tray_id": tray_id,
            }
        )

        logger.info(
            "[UsageTracker] Spool %d consumed %.1fg (3MF estimate%s) on printer %d AMS%d-T%d (%s)",
            spool.id,
            weight_grams,
            f" scaled to {scale:.0%}" if scale < 1 else "",
            printer_id,
            ams_id,
            tray_id,
            status,
        )

    return results
