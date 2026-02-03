"""API routes for pending uploads (virtual printer queue mode)."""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.pending_upload import PendingUpload
from backend.app.models.user import User
from backend.app.services.archive import ArchiveService

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
    file_size: int
    source_ip: str | None
    status: str
    tags: str | None
    notes: str | None
    project_id: int | None
    uploaded_at: datetime

    class Config:
        from_attributes = True


@router.get("/", response_model=list[PendingUploadResponse])
async def list_pending_uploads(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.QUEUE_READ),
):
    """List all pending uploads."""
    result = await db.execute(
        select(PendingUpload).where(PendingUpload.status == "pending").order_by(PendingUpload.uploaded_at.desc())
    )

    return result.scalars().all()


@router.get("/count")
async def get_pending_count(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.QUEUE_READ),
):
    """Get count of pending uploads."""
    result = await db.execute(select(PendingUpload).where(PendingUpload.status == "pending"))
    count = len(result.scalars().all())

    return {"count": count}


# Note: Bulk operations must be defined BEFORE parameterized routes
# to prevent FastAPI from matching /archive-all as /{upload_id}


@router.post("/archive-all")
async def archive_all_pending(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.QUEUE_CREATE),
):
    """Archive all pending uploads."""
    result = await db.execute(select(PendingUpload).where(PendingUpload.status == "pending"))
    pending_uploads = result.scalars().all()

    archived = 0
    failed = 0

    service = ArchiveService(db)

    for pending in pending_uploads:
        file_path = Path(pending.file_path)
        if not file_path.exists():
            pending.status = "discarded"
            failed += 1
            continue

        try:
            archive = await service.archive_print(
                printer_id=None,
                source_file=file_path,
                print_data={
                    "status": "archived",
                    "source": "virtual_printer",
                    "source_ip": pending.source_ip,
                },
            )

            if archive:
                pending.status = "archived"
                pending.archived_id = archive.id
                pending.archived_at = datetime.now(UTC)
                archived += 1

                # Clean up temp file
                try:
                    file_path.unlink()
                except Exception:
                    pass
            else:
                failed += 1
        except Exception:
            failed += 1

    await db.commit()

    return {
        "archived": archived,
        "failed": failed,
    }


@router.delete("/discard-all")
async def discard_all_pending(
    db: AsyncSession = Depends(get_db),
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
        except Exception:
            pass

        pending.status = "discarded"
        discarded += 1

    await db.commit()

    return {"discarded": discarded}


@router.get("/{upload_id}", response_model=PendingUploadResponse)
async def get_pending_upload(
    upload_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.QUEUE_READ),
):
    """Get a specific pending upload."""
    result = await db.execute(select(PendingUpload).where(PendingUpload.id == upload_id))
    pending = result.scalar_one_or_none()

    if not pending:
        raise HTTPException(status_code=404, detail="Upload not found")

    return pending


@router.post("/{upload_id}/archive")
async def archive_pending_upload(
    upload_id: int,
    request: ArchiveRequest = None,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.QUEUE_CREATE),
):
    """Archive a pending upload."""
    result = await db.execute(select(PendingUpload).where(PendingUpload.id == upload_id))
    pending = result.scalar_one_or_none()

    if not pending:
        raise HTTPException(status_code=404, detail="Upload not found")
    if pending.status != "pending":
        raise HTTPException(status_code=400, detail="Upload already processed")

    # Check file exists
    file_path = Path(pending.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Upload file not found on disk")

    # Archive the file
    service = ArchiveService(db)
    archive = await service.archive_print(
        printer_id=None,
        source_file=file_path,
        print_data={
            "status": "archived",
            "source": "virtual_printer",
            "source_ip": pending.source_ip,
        },
    )

    if not archive:
        raise HTTPException(status_code=500, detail="Failed to archive file")

    # Apply tags/notes/project from request
    if request:
        if request.tags:
            archive.tags = request.tags
        if request.notes:
            archive.notes = request.notes
        if request.project_id:
            archive.project_id = request.project_id

    # Update pending record
    pending.status = "archived"
    pending.archived_id = archive.id
    pending.archived_at = datetime.now(UTC)
    if request:
        pending.tags = request.tags
        pending.notes = request.notes
        pending.project_id = request.project_id

    await db.commit()

    # Clean up temp file
    try:
        file_path.unlink()
    except Exception:
        pass

    return {
        "id": archive.id,
        "print_name": archive.print_name,
        "filename": archive.filename,
    }


@router.delete("/{upload_id}")
async def discard_pending_upload(
    upload_id: int,
    db: AsyncSession = Depends(get_db),
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
    except Exception:
        pass

    # Update status
    pending.status = "discarded"
    await db.commit()

    return {"success": True}
