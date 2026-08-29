import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, nullslast, select
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

# Sortable columns, keyed by the id the Print Log table uses for its columns
# (#2636). An explicit map rather than getattr on a caller-supplied string:
# the client picks the key, so anything else would let a request order by any
# attribute it can name.
#
# ``date`` coalesces because the column renders ``started_at or created_at`` —
# sorting on started_at alone would scatter the rows that have no start time
# (queue-skipped entries) instead of interleaving them where the user sees
# them.
_SORTABLE_COLUMNS = {
    "date": func.coalesce(PrintLogEntry.started_at, PrintLogEntry.created_at),
    "print_name": PrintLogEntry.print_name,
    "printer": PrintLogEntry.printer_name,
    "user": PrintLogEntry.created_by_username,
    "status": PrintLogEntry.status,
    "duration": PrintLogEntry.duration_seconds,
    "completed_at": PrintLogEntry.completed_at,
    "filament": PrintLogEntry.filament_type,
    "filament_used": PrintLogEntry.filament_used_grams,
    "cost": PrintLogEntry.cost,
    "energy": PrintLogEntry.energy_kwh,
    "energy_cost": PrintLogEntry.energy_cost,
}


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
    sort_by: str = Query(default="date"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.ARCHIVES_READ_ALL,
            Permission.ARCHIVES_READ_OWN,
        )
    ),
):
    """Get the print log."""
    user, can_read_all = auth_result
    query = select(PrintLogEntry)
    count_query = select(func.count(PrintLogEntry.id))
    if user is not None and not can_read_all:
        query = query.where(PrintLogEntry.created_by_id == user.id)
        count_query = count_query.where(PrintLogEntry.created_by_id == user.id)

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

    # Sorting happens here rather than in the browser because the table is
    # paginated server-side: ordering the 25 rows the client happens to hold
    # would answer "the most expensive print on this page", which is not what
    # clicking a column header means.
    sort_column = _SORTABLE_COLUMNS.get(sort_by)
    if sort_column is None:
        raise HTTPException(400, f"Cannot sort by {sort_by!r}")
    ordering = sort_column.asc() if sort_dir == "asc" else sort_column.desc()
    # NULLs last in both directions, so a column that is empty for half the
    # rows (cost before a spool is priced, energy without a smart plug) never
    # buries the rows that do have values. Left to the database this differs
    # per backend — Postgres sorts NULLs high, SQLite sorts them low — so the
    # same click would give two different first pages depending on deployment.
    query = query.order_by(nullslast(ordering), PrintLogEntry.id.desc())
    # id.desc() above is the tiebreaker: without it, rows sharing a value
    # (every "completed" when sorting by status) come back in whatever order
    # the planner picks, which can differ between pages and duplicate or drop
    # a row as the user pages through.
    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    entries = result.scalars().all()

    # Validate straight off the ORM rows rather than naming each field: the
    # hand-written version dropped whatever it forgot to mention, and a
    # forgotten field is indistinguishable from a NULL column on the wire.
    # It lost failure_reason that way (#1687 part 4), then cost / energy_kwh /
    # energy_cost, which were written to the table but never sent — so the
    # Print Log's cost and energy columns read empty for every run (#2636).
    return PrintLogResponse(
        items=[PrintLogEntrySchema.model_validate(e) for e in entries],
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
        # Written by the two stale-archive paths in main.py when no end-of-print
        # status ever arrived (issue #2974). It has to be in the vocabulary, not
        # just tolerated: the archive editor clears any stored value it does not
        # recognise, so leaving it out would delete the classification on the
        # next save of such an archive.
        "noStatusUpdate",
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

    # Same field-by-field trap as the list route: this one also omitted cost
    # and the energy pair, so the row the client merged back after an edit
    # blanked whichever columns it was showing for them.
    return PrintLogEntrySchema.model_validate(entry)
