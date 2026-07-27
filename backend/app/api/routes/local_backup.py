"""API routes for scheduled local backups."""

import logging

from fastapi import APIRouter, Path
from fastapi.responses import FileResponse, JSONResponse

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.permissions import Permission
from backend.app.models.user import User
from backend.app.services.local_backup import local_backup_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/local-backup", tags=["local-backup"])


@router.get("/status")
async def get_status(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_BACKUP),
):
    """Get local backup scheduler status and configuration."""
    from backend.app.services.local_backup import _local_zone

    settings = await local_backup_service._load_settings()
    status = local_backup_service.get_status()
    return {
        **status,
        "enabled": settings["enabled"],
        "schedule": settings["schedule"],
        "time": settings["time"],
        "retention": settings["retention"],
        "path": settings["path"],
        "default_path": str(local_backup_service._resolve_backup_dir("")),
        # IANA zone name the HH:MM picker is interpreted in (TZ env, UTC fallback).
        # Frontend renders this next to the time field so users see the same
        # zone the backend will use. #1602 follow-up.
        "timezone": str(_local_zone()),
    }


@router.get("/path-check")
async def check_path(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_BACKUP),
):
    """Check that the configured output directory can actually be written to.

    Writes and removes a probe file. A path the service cannot write to — a NAS
    share outside the systemd unit's ReadWritePaths, say — otherwise only shows
    up as a failed backup hours later (#2544).
    """
    settings = await local_backup_service._load_settings()
    return local_backup_service.check_path(settings["path"])


@router.post("/run")
async def trigger_backup(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_BACKUP),
):
    """Trigger a local backup immediately."""
    result = await local_backup_service.run_backup()
    return result


@router.get("/backups")
async def list_backups(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_BACKUP),
):
    """List existing backup files."""
    settings = await local_backup_service._load_settings()
    return local_backup_service.list_backups(settings["path"])


@router.get("/backups/{filename}/download")
async def download_backup(
    filename: str = Path(..., description="Backup filename to download"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_BACKUP),
):
    """Download a specific backup file."""
    settings = await local_backup_service._load_settings()
    file_path = local_backup_service.resolve_backup_file(settings["path"], filename)
    if file_path is None:
        return JSONResponse(status_code=404, content={"success": False, "message": "Backup not found"})
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/zip",
    )


@router.post("/backups/{filename}/restore")
async def restore_backup(
    filename: str = Path(..., description="Backup filename to restore"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_RESTORE),
):
    """Restore from a scheduled backup file on the server."""
    import io

    from fastapi import UploadFile
    from fastapi.responses import JSONResponse

    settings = await local_backup_service._load_settings()
    file_path = local_backup_service.resolve_backup_file(settings["path"], filename)
    if file_path is None:
        return JSONResponse(status_code=404, content={"success": False, "message": "Backup not found"})

    from backend.app.api.routes.settings import restore_backup as settings_restore_backup
    from backend.app.core.database import async_session

    content = file_path.read_bytes()
    upload = UploadFile(filename=filename, file=io.BytesIO(content))

    async with async_session() as db:
        return await settings_restore_backup(file=upload, db=db)


@router.delete("/backups/{filename}")
async def delete_backup(
    filename: str = Path(..., description="Backup filename to delete"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_BACKUP),
):
    """Delete a specific backup file."""
    settings = await local_backup_service._load_settings()
    return local_backup_service.delete_backup(settings["path"], filename)
