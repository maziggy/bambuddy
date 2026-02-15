import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.print_log import PrintLogEntry
from backend.app.models.user import User
from backend.app.schemas.print_log import PrintLogEntrySchema, PrintLogResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/print-log", tags=["print-log"])


@router.get("/", response_model=PrintLogResponse)
async def get_print_log(
    search: str | None = None,
    printer_id: int | None = None,
    created_by_username: str | None = None,
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.ARCHIVES_READ),
):
    """Get the print log."""
    query = select(PrintLogEntry)
    count_query = select(func.count(PrintLogEntry.id))

    if printer_id is not None:
        query = query.where(PrintLogEntry.printer_id == printer_id)
        count_query = count_query.where(PrintLogEntry.printer_id == printer_id)
    if created_by_username:
        query = query.where(PrintLogEntry.created_by_username == created_by_username)
        count_query = count_query.where(PrintLogEntry.created_by_username == created_by_username)
    if status:
        query = query.where(PrintLogEntry.status == status)
        count_query = count_query.where(PrintLogEntry.status == status)
    if search:
        query = query.where(PrintLogEntry.print_name.ilike(f"%{search}%"))
        count_query = count_query.where(PrintLogEntry.print_name.ilike(f"%{search}%"))
    if date_from:
        query = query.where(PrintLogEntry.created_at >= date_from)
        count_query = count_query.where(PrintLogEntry.created_at >= date_from)
    if date_to:
        query = query.where(PrintLogEntry.created_at <= date_to)
        count_query = count_query.where(PrintLogEntry.created_at <= date_to)

    # Get total count
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Get paginated results
    query = query.order_by(PrintLogEntry.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    entries = result.scalars().all()

    return PrintLogResponse(
        items=[
            PrintLogEntrySchema(
                id=e.id,
                print_name=e.print_name,
                printer_name=e.printer_name,
                printer_id=e.printer_id,
                status=e.status,
                started_at=e.started_at,
                completed_at=e.completed_at,
                duration_seconds=e.duration_seconds,
                filament_type=e.filament_type,
                filament_color=e.filament_color,
                filament_used_grams=e.filament_used_grams,
                thumbnail_path=e.thumbnail_path,
                created_by_username=e.created_by_username,
                created_at=e.created_at,
            )
            for e in entries
        ],
        total=total,
    )


@router.get("/{entry_id}/thumbnail")
async def get_print_log_thumbnail(
    entry_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get the thumbnail for a print log entry.

    Note: Unauthenticated - loaded via <img> tags which can't send auth headers.
    """
    entry = await db.get(PrintLogEntry, entry_id)
    if not entry or not entry.thumbnail_path:
        raise HTTPException(404, "Thumbnail not found")

    thumb_path = settings.base_dir / entry.thumbnail_path
    if not thumb_path.exists():
        raise HTTPException(404, "Thumbnail file not found")

    return FileResponse(
        path=thumb_path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.delete("/")
async def clear_print_log(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.ARCHIVES_DELETE_ALL),
):
    """Clear the print log.

    Only deletes log entries. Archives and queue items are never touched.
    """
    result = await db.execute(delete(PrintLogEntry))
    deleted = result.rowcount
    await db.commit()

    logger.info("Print log cleared: %d entries deleted", deleted)
    return {"deleted": deleted}
