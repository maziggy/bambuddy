"""Automatic filament consumption tracking.

Captures AMS tray remain% at print start, then computes consumption
deltas at print complete to update spool weight_used and last_used.
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

    ams_data = state.raw_data.get("ams", {}).get("ams", [])
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

    if not tray_remain_start:
        logger.debug("[UsageTracker] No valid remain%% data for printer %d", printer_id)
        return

    print_name = data.get("subtask_name", "") or data.get("filename", "unknown")

    session = PrintSession(
        printer_id=printer_id,
        print_name=print_name,
        started_at=datetime.now(timezone.utc),
        tray_remain_start=tray_remain_start,
    )
    _active_sessions[printer_id] = session
    logger.info(
        "[UsageTracker] Captured start remain%% for printer %d (%d trays): %s",
        printer_id,
        len(tray_remain_start),
        {f"{k[0]}-{k[1]}": v for k, v in tray_remain_start.items()},
    )


async def on_print_complete(
    printer_id: int,
    data: dict,
    printer_manager,
    db: AsyncSession,
) -> list[dict]:
    """Compute consumption deltas and update spool weight_used/last_used.

    Returns a list of dicts describing what was logged (for WebSocket broadcast).
    """
    session = _active_sessions.pop(printer_id, None)
    if not session:
        logger.debug("[UsageTracker] No active session for printer %d, skipping", printer_id)
        return []

    # Read current remain%
    state = printer_manager.get_status(printer_id)
    if not state or not state.raw_data:
        logger.warning("[UsageTracker] No state at print complete for printer %d", printer_id)
        return []

    ams_data = state.raw_data.get("ams", {}).get("ams", [])
    status = data.get("status", "completed")
    results = []

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

    if results:
        await db.commit()

    return results
