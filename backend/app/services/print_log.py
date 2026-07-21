"""Service for writing independent print log entries.

Log entries are written to a separate table and never touch archives or queue items.
"""

import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.print_log import PrintLogEntry

logger = logging.getLogger(__name__)


async def write_log_entry(
    db: AsyncSession,
    *,
    status: str,
    archive_id: int | None = None,
    print_name: str | None = None,
    printer_name: str | None = None,
    printer_id: int | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    filament_type: str | None = None,
    filament_color: str | None = None,
    filament_used_grams: float | None = None,
    cost: float | None = None,
    energy_kwh: float | None = None,
    energy_cost: float | None = None,
    failure_reason: str | None = None,
    thumbnail_path: str | None = None,
    created_by_id: int | None = None,
    created_by_username: str | None = None,
    reconciled: bool = False,
) -> PrintLogEntry:
    """Write a print log entry.

    ``reconciled`` marks a synthetic completion written when a stale
    ``status="printing"`` archive is closed out at reconnect. Its real end time
    is unknown — the print stopped somewhere during the disconnect and
    ``completed_at`` is only the reconnect moment — so ``completed_at -
    started_at`` would bank the entire disconnect gap as print time, adding
    hundreds of fictitious hours across a farm of stale rows (#2592). For those
    entries we store an explicit ``0`` ("no measured runtime") rather than a
    fabricated duration; the stats total trusts a stored 0 instead of
    recomputing from the stale timestamps.
    """
    if reconciled:
        duration: int | None = 0
    elif started_at and completed_at:
        duration = int((completed_at - started_at).total_seconds())
    else:
        duration = None

    entry = PrintLogEntry(
        archive_id=archive_id,
        print_name=print_name,
        printer_name=printer_name,
        printer_id=printer_id,
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        duration_seconds=duration,
        filament_type=filament_type,
        filament_color=filament_color,
        filament_used_grams=filament_used_grams,
        cost=cost,
        energy_kwh=energy_kwh,
        energy_cost=energy_cost,
        failure_reason=failure_reason,
        thumbnail_path=thumbnail_path,
        created_by_id=created_by_id,
        created_by_username=created_by_username,
    )
    db.add(entry)
    await db.flush()
    return entry
