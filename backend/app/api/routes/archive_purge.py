"""Archive auto-purge endpoints (#1008 follow-up).

Admin-only (``ARCHIVES_PURGE``). Provides:

* ``GET /archives/purge/preview`` — live count for the admin slider
* ``POST /archives/purge`` — one-shot manual bulk delete
* ``GET/PUT /archives/purge/settings`` — auto-purge toggle + threshold
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import require_permission_if_auth_enabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.user import User
from backend.app.schemas.archive_purge import (
    ArchivePurgePreviewResponse,
    ArchivePurgeRequest,
    ArchivePurgeResponse,
    ArchivePurgeSettings,
)
from backend.app.services.archive_purge import (
    MAX_AUTO_PURGE_DAYS,
    MIN_AUTO_PURGE_DAYS,
    archive_purge_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/archives", tags=["archives-purge"])


@router.get("/purge/preview", response_model=ArchivePurgePreviewResponse)
async def preview_archive_purge(
    older_than_days: int = Query(ge=1, le=3650),
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.ARCHIVES_PURGE)),
):
    """Count + size of archives eligible for purge. Read-only."""
    result = await archive_purge_service.preview_purge(db, older_than_days=older_than_days)
    return ArchivePurgePreviewResponse(**result)


@router.post("/purge", response_model=ArchivePurgeResponse)
async def execute_archive_purge(
    body: ArchivePurgeRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.ARCHIVES_PURGE)),
):
    """Hard-delete archives older than the threshold. Irreversible."""
    deleted = await archive_purge_service.purge_older_than(db, older_than_days=body.older_than_days)
    return ArchivePurgeResponse(deleted=deleted)


@router.get("/purge/settings", response_model=ArchivePurgeSettings)
async def get_archive_purge_settings(
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.ARCHIVES_PURGE)),
):
    cfg = await archive_purge_service.get_settings(db)
    return ArchivePurgeSettings(enabled=cfg["enabled"], days=cfg["days"])


@router.put("/purge/settings", response_model=ArchivePurgeSettings)
async def update_archive_purge_settings(
    body: ArchivePurgeSettings,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.ARCHIVES_PURGE)),
):
    if body.days < MIN_AUTO_PURGE_DAYS or body.days > MAX_AUTO_PURGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"days must be between {MIN_AUTO_PURGE_DAYS} and {MAX_AUTO_PURGE_DAYS}",
        )
    saved = await archive_purge_service.set_settings(db, enabled=body.enabled, days=body.days)
    return ArchivePurgeSettings(enabled=saved["enabled"], days=saved["days"])
