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
    print_name: str | None = None,
    printer_name: str | None = None,
    printer_id: int | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    filament_type: str | None = None,
    filament_color: str | None = None,
    filament_used_grams: float | None = None,
    thumbnail_path: str | None = None,
    created_by_username: str | None = None,
) -> PrintLogEntry:
    """Write a print log entry."""
    duration = None
    if started_at and completed_at:
        duration = int((completed_at - started_at).total_seconds())

    entry = PrintLogEntry(
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
        thumbnail_path=thumbnail_path,
        created_by_username=created_by_username,
    )
    db.add(entry)
    await db.flush()
    return entry
