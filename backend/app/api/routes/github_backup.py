"""API routes for GitHub profile backup."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import get_db
from backend.app.models.github_backup import GitHubBackupConfig, GitHubBackupLog
from backend.app.schemas.github_backup import (
    GitHubBackupConfigCreate,
    GitHubBackupConfigResponse,
    GitHubBackupConfigUpdate,
    GitHubBackupLogResponse,
    GitHubBackupStatus,
    GitHubBackupTriggerResponse,
    GitHubTestConnectionResponse,
)
from backend.app.services.github_backup import github_backup_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/github-backup", tags=["github-backup"])


def _config_to_response(config: GitHubBackupConfig) -> dict:
    """Convert config model to response dict."""
    return {
        "id": config.id,
        "repository_url": config.repository_url,
        "has_token": bool(config.access_token),
        "branch": config.branch,
        "schedule_enabled": config.schedule_enabled,
        "schedule_type": config.schedule_type,
        "backup_kprofiles": config.backup_kprofiles,
        "backup_cloud_profiles": config.backup_cloud_profiles,
        "backup_settings": config.backup_settings,
        "enabled": config.enabled,
        "last_backup_at": config.last_backup_at,
        "last_backup_status": config.last_backup_status,
        "last_backup_message": config.last_backup_message,
        "last_backup_commit_sha": config.last_backup_commit_sha,
        "next_scheduled_run": config.next_scheduled_run,
        "created_at": config.created_at,
        "updated_at": config.updated_at,
    }


@router.get("/config", response_model=GitHubBackupConfigResponse | None)
async def get_config(db: AsyncSession = Depends(get_db)):
    """Get the current GitHub backup configuration."""
    result = await db.execute(select(GitHubBackupConfig).limit(1))
    config = result.scalar_one_or_none()

    if not config:
        return None

    return _config_to_response(config)


@router.post("/config", response_model=GitHubBackupConfigResponse)
async def save_config(
    config_data: GitHubBackupConfigCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create or update GitHub backup configuration.

    Only one configuration is supported. If one exists, it will be updated.
    """
    # Check for existing config
    result = await db.execute(select(GitHubBackupConfig).limit(1))
    config = result.scalar_one_or_none()

    if config:
        # Update existing
        config.repository_url = config_data.repository_url
        config.access_token = config_data.access_token
        config.branch = config_data.branch
        config.schedule_enabled = config_data.schedule_enabled
        config.schedule_type = config_data.schedule_type.value
        config.backup_kprofiles = config_data.backup_kprofiles
        config.backup_cloud_profiles = config_data.backup_cloud_profiles
        config.backup_settings = config_data.backup_settings
        config.enabled = config_data.enabled

        # Calculate next scheduled run if enabled
        if config.schedule_enabled:
            config.next_scheduled_run = github_backup_service._calculate_next_run(config.schedule_type)
        else:
            config.next_scheduled_run = None

        logger.info(f"Updated GitHub backup config: {config.repository_url}")
    else:
        # Create new
        config = GitHubBackupConfig(
            repository_url=config_data.repository_url,
            access_token=config_data.access_token,
            branch=config_data.branch,
            schedule_enabled=config_data.schedule_enabled,
            schedule_type=config_data.schedule_type.value,
            backup_kprofiles=config_data.backup_kprofiles,
            backup_cloud_profiles=config_data.backup_cloud_profiles,
            backup_settings=config_data.backup_settings,
            enabled=config_data.enabled,
        )

        if config.schedule_enabled:
            config.next_scheduled_run = github_backup_service._calculate_next_run(config.schedule_type)

        db.add(config)
        logger.info(f"Created GitHub backup config: {config.repository_url}")

    await db.commit()
    await db.refresh(config)

    return _config_to_response(config)


@router.patch("/config", response_model=GitHubBackupConfigResponse)
async def update_config(
    update_data: GitHubBackupConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Partially update GitHub backup configuration."""
    result = await db.execute(select(GitHubBackupConfig).limit(1))
    config = result.scalar_one_or_none()

    if not config:
        raise HTTPException(status_code=404, detail="No configuration found")

    update_dict = update_data.model_dump(exclude_unset=True)

    for key, value in update_dict.items():
        if key == "schedule_type" and value is not None:
            setattr(config, key, value.value)
        else:
            setattr(config, key, value)

    # Recalculate next scheduled run if schedule settings changed
    if "schedule_enabled" in update_dict or "schedule_type" in update_dict:
        if config.schedule_enabled:
            config.next_scheduled_run = github_backup_service._calculate_next_run(config.schedule_type)
        else:
            config.next_scheduled_run = None

    await db.commit()
    await db.refresh(config)

    logger.info(f"Updated GitHub backup config: {config.repository_url}")

    return _config_to_response(config)


@router.delete("/config")
async def delete_config(db: AsyncSession = Depends(get_db)):
    """Delete the GitHub backup configuration and all logs."""
    result = await db.execute(select(GitHubBackupConfig).limit(1))
    config = result.scalar_one_or_none()

    if not config:
        raise HTTPException(status_code=404, detail="No configuration found")

    await db.delete(config)
    await db.commit()

    logger.info("Deleted GitHub backup config")

    return {"message": "Configuration deleted"}


@router.post("/test", response_model=GitHubTestConnectionResponse)
async def test_connection(
    repo_url: str = Query(..., description="GitHub repository URL"),
    token: str = Query(..., description="Personal Access Token"),
):
    """Test GitHub connection with provided credentials."""
    result = await github_backup_service.test_connection(repo_url, token)
    return GitHubTestConnectionResponse(**result)


@router.post("/test-stored", response_model=GitHubTestConnectionResponse)
async def test_stored_connection(db: AsyncSession = Depends(get_db)):
    """Test GitHub connection using stored configuration."""
    result = await db.execute(select(GitHubBackupConfig).limit(1))
    config = result.scalar_one_or_none()

    if not config:
        raise HTTPException(status_code=404, detail="No configuration found")

    if not config.access_token:
        raise HTTPException(status_code=400, detail="No access token configured")

    test_result = await github_backup_service.test_connection(config.repository_url, config.access_token)
    return GitHubTestConnectionResponse(**test_result)


@router.post("/run", response_model=GitHubBackupTriggerResponse)
async def trigger_backup(db: AsyncSession = Depends(get_db)):
    """Manually trigger a backup."""
    result = await db.execute(select(GitHubBackupConfig).limit(1))
    config = result.scalar_one_or_none()

    if not config:
        raise HTTPException(status_code=404, detail="No configuration found. Configure backup first.")

    if not config.enabled:
        raise HTTPException(status_code=400, detail="Backup is disabled")

    backup_result = await github_backup_service.run_backup(config.id, trigger="manual")

    return GitHubBackupTriggerResponse(**backup_result)


@router.get("/status", response_model=GitHubBackupStatus)
async def get_status(db: AsyncSession = Depends(get_db)):
    """Get current backup status."""
    result = await db.execute(select(GitHubBackupConfig).limit(1))
    config = result.scalar_one_or_none()

    if not config:
        return GitHubBackupStatus(
            configured=False,
            enabled=False,
            is_running=False,
            progress=None,
            last_backup_at=None,
            last_backup_status=None,
            next_scheduled_run=None,
        )

    return GitHubBackupStatus(
        configured=True,
        enabled=config.enabled,
        is_running=github_backup_service.is_running,
        progress=github_backup_service.progress,
        last_backup_at=config.last_backup_at,
        last_backup_status=config.last_backup_status,
        next_scheduled_run=config.next_scheduled_run,
    )


@router.get("/logs", response_model=list[GitHubBackupLogResponse])
async def get_logs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Get backup logs."""
    result = await db.execute(select(GitHubBackupConfig).limit(1))
    config = result.scalar_one_or_none()

    if not config:
        return []

    logs_result = await db.execute(
        select(GitHubBackupLog)
        .where(GitHubBackupLog.config_id == config.id)
        .order_by(desc(GitHubBackupLog.started_at))
        .offset(offset)
        .limit(limit)
    )
    logs = logs_result.scalars().all()

    return [
        GitHubBackupLogResponse(
            id=log.id,
            config_id=log.config_id,
            started_at=log.started_at,
            completed_at=log.completed_at,
            status=log.status,
            trigger=log.trigger,
            commit_sha=log.commit_sha,
            files_changed=log.files_changed,
            error_message=log.error_message,
        )
        for log in logs
    ]


@router.delete("/logs")
async def clear_logs(
    keep_last: int = Query(default=10, ge=0, le=100, description="Number of recent logs to keep"),
    db: AsyncSession = Depends(get_db),
):
    """Clear backup logs, optionally keeping the most recent entries."""
    result = await db.execute(select(GitHubBackupConfig).limit(1))
    config = result.scalar_one_or_none()

    if not config:
        return {"deleted": 0, "message": "No configuration found"}

    if keep_last > 0:
        # Get IDs to keep
        keep_result = await db.execute(
            select(GitHubBackupLog.id)
            .where(GitHubBackupLog.config_id == config.id)
            .order_by(desc(GitHubBackupLog.started_at))
            .limit(keep_last)
        )
        keep_ids = [row[0] for row in keep_result.fetchall()]

        if keep_ids:
            delete_result = await db.execute(
                delete(GitHubBackupLog).where(
                    GitHubBackupLog.config_id == config.id, GitHubBackupLog.id.not_in(keep_ids)
                )
            )
        else:
            delete_result = await db.execute(delete(GitHubBackupLog).where(GitHubBackupLog.config_id == config.id))
    else:
        delete_result = await db.execute(delete(GitHubBackupLog).where(GitHubBackupLog.config_id == config.id))

    await db.commit()

    deleted_count = delete_result.rowcount
    logger.info(f"Deleted {deleted_count} GitHub backup logs (kept {keep_last})")

    return {"deleted": deleted_count, "message": f"Deleted {deleted_count} logs"}
