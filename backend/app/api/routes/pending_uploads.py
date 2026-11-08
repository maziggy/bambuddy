"""API routes for pending uploads (virtual printer review mode)."""

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import ApiKeyActor, RequirePermissionIfAuthEnabled, require_ownership_permission
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.core.websocket import ws_manager
from backend.app.models.pending_upload import PendingUpload
from backend.app.models.project import Project
from backend.app.models.user import User
from backend.app.services.archive import resolve_display_stem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pending-uploads", tags=["pending-uploads"])


class ArchiveRequest(BaseModel):
    """Request to archive a pending upload."""

    tags: str | None = None
    notes: str | None = None
    project_id: int | None = None


class PendingUploadResponse(BaseModel):
    """Response model for pending upload."""

    id: int
    filename: str
    display_name: str  # Resolved name that mirrors the eventual archive's print_name (#1152 follow-up)
    file_size: int
    source_ip: str | None
    status: str
    tags: str | None
    notes: str | None
    project_id: int | None
    uploaded_at: datetime

    class Config:
        from_attributes = True


def _resolve_display_name(pending: PendingUpload, prefer_filename: bool) -> str:
    """Compute the name the review card should show, matching what archive_print
    will eventually write to ``PrintArchive.print_name`` so the user sees the
    same name in both places (#1152 follow-up).

    Mirrors ``ArchiveService.archive_print``:
      - ``prefer_filename=True`` → stripped filename stem.
      - ``prefer_filename=False`` → ``metadata_print_name`` if set, else stem.
    """
    stem = resolve_display_stem(pending.filename)
    if prefer_filename:
        return stem
    return (pending.metadata_print_name or "").strip() or stem


async def _augment_with_display_name(
    db: AsyncSession,
    pendings: list[PendingUpload],
) -> list[PendingUploadResponse]:
    """Build response objects with display_name resolved against the toggle.

    Reads the ``virtual_printer_archive_name_source`` setting once per request
    rather than per row.
    """
    from backend.app.api.routes.settings import get_setting

    prefer_filename = (await get_setting(db, "virtual_printer_archive_name_source")) == "filename"
    return [
        PendingUploadResponse(
            id=p.id,
            filename=p.filename,
            display_name=_resolve_display_name(p, prefer_filename),
            file_size=p.file_size,
            source_ip=p.source_ip,
            status=p.status,
            tags=p.tags,
            notes=p.notes,
            project_id=p.project_id,
            uploaded_at=p.uploaded_at,
        )
        for p in pendings
    ]


@router.get("/", response_model=list[PendingUploadResponse])
async def list_pending_uploads(
    db: AsyncSession = Depends(get_db, scope="function"),
    _: tuple[User | ApiKeyActor | None, bool] = Depends(
        require_ownership_permission(
            Permission.QUEUE_READ_ALL,
            Permission.QUEUE_READ_OWN,
        )
    ),
):
    """List all pending uploads."""
    result = await db.execute(
        select(PendingUpload).where(PendingUpload.status == "pending").order_by(PendingUpload.uploaded_at.desc())
    )

    return await _augment_with_display_name(db, list(result.scalars().all()))


@router.get("/count")
async def get_pending_count(
    db: AsyncSession = Depends(get_db, scope="function"),
    _: tuple[User | ApiKeyActor | None, bool] = Depends(
        require_ownership_permission(
            Permission.QUEUE_READ_ALL,
            Permission.QUEUE_READ_OWN,
        )
    ),
):
    """Get count of pending uploads."""
    result = await db.execute(select(PendingUpload).where(PendingUpload.status == "pending"))
    count = len(result.scalars().all())

    return {"count": count}


# Note: Bulk operations must be defined BEFORE parameterized routes
# to prevent FastAPI from matching /archive-all as /{upload_id}


async def _store_pending(db: AsyncSession, pending: PendingUpload, request: "ArchiveRequest | None"):
    """Store an approved upload in its virtual printer's File Manager folder.

    The file, its notes, project and tags go to the File Manager; the archive
    gets it once it prints (an archive is a print that happened). An upload
    from before the virtual printer was recorded, or whose virtual printer is
    gone, goes to the shared "Virtual Printer" folder. Commits.
    """
    from backend.app.services.virtual_printer.library_store import store_upload, upload_settings

    folder_id = None
    if pending.virtual_printer_id is not None:
        folder_id, _ = await upload_settings(db, pending.virtual_printer_id)
    library_file, _ = await store_upload(
        db,
        source_path=Path(pending.file_path),
        filename=pending.filename,
        folder_id=folder_id,
        notes=request.notes if request else None,
        project_id=(request.project_id or None) if request else None,
        tags=request.tags if request else None,
    )
    pending.status = "approved"
    pending.archived_at = datetime.now(timezone.utc)
    if request:
        pending.tags = request.tags
        pending.notes = request.notes
        pending.project_id = request.project_id or None
    await db.commit()
    try:
        Path(pending.file_path).unlink()
    except OSError:
        pass  # Best-effort temp file cleanup; the File Manager has its copy
    return library_file


@router.post("/archive-all")
async def archive_all_pending(
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.QUEUE_CREATE, Permission.LIBRARY_UPLOAD),
):
    """Approve every pending upload: each goes to the File Manager.

    Needs library:upload as well as queue:create: approving adds files.
    """
    # By id, each loaded fresh: a failed one is rolled back, and a rollback
    # expires every row the session holds.
    pending_ids = (await db.execute(select(PendingUpload.id).where(PendingUpload.status == "pending"))).scalars().all()

    stored = 0
    failed = 0
    for pending_id in pending_ids:
        pending = await db.get(PendingUpload, pending_id)
        if pending is None or pending.status != "pending":
            continue
        if not Path(pending.file_path).exists():
            pending.status = "discarded"
            await db.commit()
            failed += 1
            continue
        try:
            await _store_pending(db, pending, None)
            stored += 1
        except Exception:  # noqa: BLE001 -- one bad file must not stop the rest
            logger.exception("Could not store pending upload %s in the File Manager", pending_id)
            await db.rollback()
            failed += 1

    if stored:
        await ws_manager.send_library_files_changed()
    return {"stored": stored, "failed": failed}


@router.delete("/discard-all")
async def discard_all_pending(
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.QUEUE_DELETE_ALL),
):
    """Discard all pending uploads."""
    result = await db.execute(select(PendingUpload).where(PendingUpload.status == "pending"))
    pending_uploads = result.scalars().all()

    discarded = 0

    for pending in pending_uploads:
        # Delete file from disk
        try:
            file_path = Path(pending.file_path)
            file_path.unlink(missing_ok=True)
        except OSError:
            pass  # Best-effort file deletion; record is still marked discarded

        pending.status = "discarded"
        discarded += 1

    await db.commit()

    return {"discarded": discarded}


@router.get("/{upload_id}", response_model=PendingUploadResponse)
async def get_pending_upload(
    upload_id: int,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: tuple[User | ApiKeyActor | None, bool] = Depends(
        require_ownership_permission(
            Permission.QUEUE_READ_ALL,
            Permission.QUEUE_READ_OWN,
        )
    ),
):
    """Get a specific pending upload."""
    result = await db.execute(select(PendingUpload).where(PendingUpload.id == upload_id))
    pending = result.scalar_one_or_none()

    if not pending:
        raise HTTPException(status_code=404, detail="Upload not found")

    return (await _augment_with_display_name(db, [pending]))[0]


@router.post("/{upload_id}/archive")
async def archive_pending_upload(
    upload_id: int,
    request: ArchiveRequest = None,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.QUEUE_CREATE, Permission.LIBRARY_UPLOAD),
):
    """Approve a pending upload: it goes to the File Manager.

    Needs library:upload as well as queue:create: approving adds a file.
    """
    result = await db.execute(select(PendingUpload).where(PendingUpload.id == upload_id))
    pending = result.scalar_one_or_none()

    if not pending:
        raise HTTPException(status_code=404, detail="Upload not found")
    if pending.status != "pending":
        raise HTTPException(status_code=400, detail="Upload already processed")

    if not Path(pending.file_path).exists():
        raise HTTPException(status_code=404, detail="Upload file not found on disk")

    # The project must exist before anything is stored; 0 means none.
    project_id = (request.project_id or None) if request else None
    if project_id is not None and await db.get(Project, project_id) is None:
        raise HTTPException(status_code=400, detail="Project not found")

    try:
        library_file = await _store_pending(db, pending, request)
    except Exception as e:
        logger.exception("Could not store pending upload %s in the File Manager", upload_id)
        raise HTTPException(status_code=500, detail="Failed to store the file in the File Manager") from e
    await ws_manager.send_library_files_changed()

    return {
        "id": library_file.id,
        "filename": library_file.filename,
        "folder_id": library_file.folder_id,
    }


@router.delete("/{upload_id}")
async def discard_pending_upload(
    upload_id: int,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.QUEUE_DELETE_ALL),
):
    """Discard a pending upload without archiving."""
    result = await db.execute(select(PendingUpload).where(PendingUpload.id == upload_id))
    pending = result.scalar_one_or_none()

    if not pending:
        raise HTTPException(status_code=404, detail="Upload not found")

    # Delete file from disk
    file_path = Path(pending.file_path)
    try:
        file_path.unlink(missing_ok=True)
    except OSError:
        pass  # Best-effort file deletion on discard

    # Update status
    pending.status = "discarded"
    await db.commit()

    return {"success": True}
