import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import (
    RequireCameraStreamTokenIfAuthEnabled,
    RequirePermissionIfAuthEnabled,
    require_ownership_permission,
)
from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.print_log import PrintLogEntry
from backend.app.models.user import User
from backend.app.schemas.print_log import PrintLogEntrySchema, PrintLogEntryUpdate, PrintLogResponse

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
                archive_id=e.archive_id,
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
                # failure_reason was silently dropped by the GET serialiser
                # before #1687 part 4 — without it the Print Log table couldn't
                # surface what the Failure Analysis widget already groups by.
                failure_reason=e.failure_reason,
                thumbnail_path=e.thumbnail_path,
                created_by_id=e.created_by_id,
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
    _: None = RequireCameraStreamTokenIfAuthEnabled,
):
    """Get the thumbnail for a print log entry.

    Requires a stream token query param (?token=xxx) when auth is enabled.

    Self-heals stale entries: when thumbnail_path points to a file that no
    longer exists on disk (archive was deleted, or print failed before the
    thumbnail was ever written), NULL the path on the entry so subsequent
    page renders skip the request entirely. The frontend's <img> tag is
    gated on entry.thumbnail_path being truthy, so the next fetch of the
    log list will simply not request this thumbnail again.
    """
    entry = await db.get(PrintLogEntry, entry_id)
    if not entry or not entry.thumbnail_path:
        raise HTTPException(404, "Thumbnail not found")

    thumb_path = settings.base_dir / entry.thumbnail_path
    if not thumb_path.exists():
        entry.thumbnail_path = None
        await db.commit()
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


@router.delete("/{entry_id}")
async def delete_print_log_entry(
    entry_id: int,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.ARCHIVES_DELETE_ALL,
            Permission.ARCHIVES_DELETE_OWN,
        )
    ),
):
    """Delete a single print-log entry (#1687).

    Removes the row entirely. Because /archives/stats aggregates over
    PrintLogEntry, the deleted row's filament / cost / duration / count
    contributions drop out of the totals in the same response cycle.
    The linked archive (if any) is untouched — the FK on the archive row
    is from PrintLogEntry, not the other way around.
    """
    user, can_modify_all = auth_result

    entry = await db.get(PrintLogEntry, entry_id)
    if not entry:
        raise HTTPException(404, "Print log entry not found")

    if not can_modify_all:
        if entry.created_by_id is None or (user is not None and entry.created_by_id != user.id):
            raise HTTPException(403, "You can only delete your own print log entries")

    await db.delete(entry)
    await db.commit()

    logger.info("Print log entry %d deleted", entry_id)
    return {"status": "deleted", "id": entry_id}


# Canonical failure-reason vocabulary. Mirrors the frontend dropdown in
# EditArchiveModal.tsx; the empty string is the "clear classification" value.
# The catch-all "other" is the escape hatch for failures that don't fit the
# enumerated list. Keep these two lists in sync if the EditArchiveModal options
# ever change.
_FAILURE_REASON_KEYS = frozenset(
    {
        "",
        "adhesionFailure",
        "spaghettiDetached",
        "layerShift",
        "cloggedNozzle",
        "filamentRunout",
        "warping",
        "stringing",
        "underExtrusion",
        "powerFailure",
        "userCancelled",
        "other",
    }
)

# Same status vocabulary the print-log column already filters by.
_STATUS_KEYS = frozenset({"completed", "failed", "stopped", "cancelled", "skipped"})


@router.patch("/{entry_id}", response_model=PrintLogEntrySchema)
async def update_print_log_entry(
    entry_id: int,
    update: PrintLogEntryUpdate,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.ARCHIVES_UPDATE_ALL,
            Permission.ARCHIVES_UPDATE_OWN,
        )
    ),
):
    """Edit a single Print Log row's classification (#1687 part 4, reporter
    IndividualGhost1905).

    Lets the user set ``failure_reason`` (and optionally re-classify ``status``)
    directly on a Print Log row — including orphan entries that have no
    archive to edit through. The Failure Analysis widget already groups by
    ``PrintLogEntry.failure_reason`` (see ``archives.py:1421`` for the
    archive-side mirror); this endpoint is the missing edit affordance for the
    log-side, mirror-less case.

    Ownership semantics mirror the per-row delete: archives:update_all sees
    everything; archives:update_own sees only rows it owns.
    """
    user, can_modify_all = auth_result

    entry = await db.get(PrintLogEntry, entry_id)
    if not entry:
        raise HTTPException(404, "Print log entry not found")

    if not can_modify_all:
        if entry.created_by_id is None or (user is not None and entry.created_by_id != user.id):
            raise HTTPException(403, "You can only update your own print log entries")

    payload = update.model_dump(exclude_unset=True)

    # Validate against the canonical vocabularies. Reject unknown values rather
    # than silently storing them — the Failure Analysis widget renders the
    # values back as i18n keys, and an unrecognised value would surface as a
    # raw string in the UI.
    if "failure_reason" in payload:
        new_reason = payload["failure_reason"] or ""
        if new_reason not in _FAILURE_REASON_KEYS:
            raise HTTPException(400, f"Unknown failure_reason: {new_reason!r}")
        # Store empty string back as NULL so the column's nullable=True intent
        # is preserved end-to-end.
        entry.failure_reason = new_reason or None

    if "status" in payload and payload["status"] is not None:
        new_status = payload["status"]
        if new_status not in _STATUS_KEYS:
            raise HTTPException(400, f"Unknown status: {new_status!r}")
        entry.status = new_status

    await db.commit()
    await db.refresh(entry)

    logger.info(
        "Print log entry %d updated (failure_reason=%r, status=%r)",
        entry_id,
        entry.failure_reason,
        entry.status,
    )

    return PrintLogEntrySchema(
        id=entry.id,
        archive_id=entry.archive_id,
        print_name=entry.print_name,
        printer_name=entry.printer_name,
        printer_id=entry.printer_id,
        status=entry.status,
        started_at=entry.started_at,
        completed_at=entry.completed_at,
        duration_seconds=entry.duration_seconds,
        filament_type=entry.filament_type,
        filament_color=entry.filament_color,
        filament_used_grams=entry.filament_used_grams,
        failure_reason=entry.failure_reason,
        thumbnail_path=entry.thumbnail_path,
        created_by_id=entry.created_by_id,
        created_by_username=entry.created_by_username,
        created_at=entry.created_at,
    )
