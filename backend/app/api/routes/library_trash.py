"""Library trash bin + admin purge endpoints (#1008).

Permission model:

* **Admin purge** (``/library/purge/*``) and **retention settings**
  (``/library/trash/settings``) require :attr:`Permission.LIBRARY_PURGE` —
  admin-only.
* **Per-user trash** (list / restore / hard-delete / empty own trash) is
  gated by the existing :attr:`Permission.LIBRARY_DELETE_ALL` /
  :attr:`Permission.LIBRARY_DELETE_OWN` ownership pair, so a regular user
  sees their own trashed files and an admin sees everyone's.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import require_ownership_permission, require_permission_if_auth_enabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.library import LibraryFile, LibraryFolder
from backend.app.models.user import User
from backend.app.schemas.library_trash import (
    EmptyTrashResponse,
    PurgePreviewResponse,
    PurgeRequest,
    PurgeResponse,
    TrashFile,
    TrashListResponse,
    TrashSettings,
)
from backend.app.services.library_trash import (
    MAX_RETENTION_DAYS,
    MIN_RETENTION_DAYS,
    library_trash_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/library", tags=["library-trash"])


# ===================== Admin purge =====================


@router.get("/purge/preview", response_model=PurgePreviewResponse)
async def preview_purge(
    older_than_days: int = Query(ge=1, le=3650),
    include_never_printed: bool = True,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_PURGE)),
):
    """Preview how many files would move to trash for the given age threshold.

    Read-only — safe to call repeatedly as the admin adjusts the slider.
    """
    result = await library_trash_service.preview_purge(
        db,
        older_than_days=older_than_days,
        include_never_printed=include_never_printed,
    )
    return PurgePreviewResponse(**result)


@router.post("/purge", response_model=PurgeResponse)
async def execute_purge(
    body: PurgeRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_PURGE)),
):
    """Move matching files to trash. Idempotent — already-trashed rows skip."""
    moved = await library_trash_service.purge_older_than(
        db,
        older_than_days=body.older_than_days,
        include_never_printed=body.include_never_printed,
    )
    return PurgeResponse(moved_to_trash=moved)


# ===================== Trash list + per-item ops =====================


@router.get("/trash", response_model=TrashListResponse)
async def list_trash(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_DELETE_ALL,
            Permission.LIBRARY_DELETE_OWN,
        )
    ),
):
    """List trashed files.

    Admins (``LIBRARY_DELETE_ALL``) see every user's trash; regular users
    (``LIBRARY_DELETE_OWN``) see only rows they created.
    """
    user, can_modify_all = auth_result
    retention_days = await library_trash_service.get_retention_days(db)

    # Base query: trashed files + their folder name (for the UI) + creator.
    base_conditions = [LibraryFile.deleted_at.isnot(None)]
    if not can_modify_all:
        if user is None:
            # Defensive: ownership checker only returns user=None when auth is off,
            # in which case can_modify_all=True. If we somehow land here, err safe.
            raise HTTPException(status_code=403, detail="Authentication required")
        base_conditions.append(LibraryFile.created_by_id == user.id)

    total_result = await db.execute(select(func.count(LibraryFile.id)).where(*base_conditions))
    total = int(total_result.scalar() or 0)

    rows_result = await db.execute(
        select(LibraryFile, LibraryFolder.name, User.username)
        .outerjoin(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
        .outerjoin(User, LibraryFile.created_by_id == User.id)
        .where(*base_conditions)
        .order_by(LibraryFile.deleted_at.desc())
        .limit(limit)
        .offset(offset)
    )

    items: list[TrashFile] = []
    for file, folder_name, username in rows_result.all():
        # deleted_at is not-null by construction above; narrow for the typechecker.
        assert file.deleted_at is not None
        auto_purge_at = file.deleted_at + timedelta(days=retention_days)
        items.append(
            TrashFile(
                id=file.id,
                filename=file.filename,
                file_size=file.file_size,
                thumbnail_path=file.thumbnail_path,
                folder_id=file.folder_id,
                folder_name=folder_name,
                created_by_id=file.created_by_id,
                created_by_username=username,
                deleted_at=file.deleted_at,
                auto_purge_at=auto_purge_at,
            )
        )

    return TrashListResponse(items=items, total=total, retention_days=retention_days)


async def _load_trashed_file(
    db: AsyncSession,
    file_id: int,
    user: User | None,
    can_modify_all: bool,
) -> LibraryFile:
    """Fetch a trashed file, enforcing ownership for non-admins."""
    result = await db.execute(
        select(LibraryFile).where(
            LibraryFile.id == file_id,
            LibraryFile.deleted_at.isnot(None),
        )
    )
    file = result.scalar_one_or_none()
    if file is None:
        raise HTTPException(status_code=404, detail="Trashed file not found")
    if not can_modify_all:
        if user is None or file.created_by_id != user.id:
            raise HTTPException(status_code=403, detail="You can only manage your own trashed files")
    return file


@router.post("/trash/{file_id}/restore")
async def restore_from_trash(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_DELETE_ALL,
            Permission.LIBRARY_DELETE_OWN,
        )
    ),
):
    user, can_modify_all = auth_result
    file = await _load_trashed_file(db, file_id, user, can_modify_all)
    await library_trash_service.restore(db, file)
    return {"status": "success", "id": file.id}


@router.delete("/trash/{file_id}")
async def hard_delete_from_trash(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_DELETE_ALL,
            Permission.LIBRARY_DELETE_OWN,
        )
    ),
):
    """Permanently delete a single trashed file + its bytes. Irreversible."""
    user, can_modify_all = auth_result
    file = await _load_trashed_file(db, file_id, user, can_modify_all)
    await library_trash_service.hard_delete_now(db, file)
    return {"status": "success"}


@router.delete("/trash", response_model=EmptyTrashResponse)
async def empty_trash(
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_DELETE_ALL,
            Permission.LIBRARY_DELETE_OWN,
        )
    ),
):
    """Permanently delete all trashed files in the caller's scope.

    Regular users empty only their own trash; admins empty everyone's.
    """
    user, can_modify_all = auth_result
    conditions = [LibraryFile.deleted_at.isnot(None)]
    if not can_modify_all:
        if user is None:
            raise HTTPException(status_code=403, detail="Authentication required")
        conditions.append(LibraryFile.created_by_id == user.id)

    rows_result = await db.execute(select(LibraryFile).where(*conditions))
    rows = rows_result.scalars().all()
    deleted = 0
    for row in rows:
        await library_trash_service.hard_delete_now(db, row)
        deleted += 1
    return EmptyTrashResponse(deleted=deleted)


# ===================== Retention settings (admin only) =====================


@router.get("/trash/settings", response_model=TrashSettings)
async def get_trash_settings(
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_PURGE)),
):
    retention = await library_trash_service.get_retention_days(db)
    auto = await library_trash_service.get_auto_purge_settings(db)
    return TrashSettings(
        retention_days=retention,
        auto_purge_enabled=auto["enabled"],
        auto_purge_days=auto["days"],
        auto_purge_include_never_printed=auto["include_never_printed"],
    )


@router.put("/trash/settings", response_model=TrashSettings)
async def update_trash_settings(
    body: TrashSettings,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_PURGE)),
):
    if body.retention_days < MIN_RETENTION_DAYS or body.retention_days > MAX_RETENTION_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"retention_days must be between {MIN_RETENTION_DAYS} and {MAX_RETENTION_DAYS}",
        )
    saved_retention = await library_trash_service.set_retention_days(db, body.retention_days)
    saved_auto = await library_trash_service.set_auto_purge_settings(
        db,
        enabled=body.auto_purge_enabled,
        days=body.auto_purge_days,
        include_never_printed=body.auto_purge_include_never_printed,
    )
    return TrashSettings(
        retention_days=saved_retention,
        auto_purge_enabled=saved_auto["enabled"],
        auto_purge_days=saved_auto["days"],
        auto_purge_include_never_printed=saved_auto["include_never_printed"],
    )
