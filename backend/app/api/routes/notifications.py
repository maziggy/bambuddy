"""API routes for notification providers."""

import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import get_db
from backend.app.models.notification import NotificationLog, NotificationProvider
from backend.app.schemas.notification import (
    NotificationLogResponse,
    NotificationLogStats,
    NotificationProviderCreate,
    NotificationProviderResponse,
    NotificationProviderUpdate,
    NotificationTestRequest,
    NotificationTestResponse,
)
from backend.app.services.notification_service import notification_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _provider_to_dict(provider: NotificationProvider) -> dict:
    """Convert a NotificationProvider model to a response dictionary."""
    return {
        "id": provider.id,
        "name": provider.name,
        "provider_type": provider.provider_type,
        "enabled": provider.enabled,
        "config": json.loads(provider.config) if isinstance(provider.config, str) else provider.config,
        # Print lifecycle events
        "on_print_start": provider.on_print_start,
        "on_print_complete": provider.on_print_complete,
        "on_print_failed": provider.on_print_failed,
        "on_print_stopped": provider.on_print_stopped,
        "on_print_progress": provider.on_print_progress,
        # Printer status events
        "on_printer_offline": provider.on_printer_offline,
        "on_printer_error": provider.on_printer_error,
        "on_filament_low": provider.on_filament_low,
        "on_maintenance_due": provider.on_maintenance_due,
        # AMS environmental alarms (regular AMS)
        "on_ams_humidity_high": provider.on_ams_humidity_high,
        "on_ams_temperature_high": provider.on_ams_temperature_high,
        # AMS-HT environmental alarms
        "on_ams_ht_humidity_high": provider.on_ams_ht_humidity_high,
        "on_ams_ht_temperature_high": provider.on_ams_ht_temperature_high,
        # Build plate detection
        "on_plate_not_empty": provider.on_plate_not_empty,
        # Print queue events
        "on_queue_job_added": provider.on_queue_job_added,
        "on_queue_job_assigned": provider.on_queue_job_assigned,
        "on_queue_job_started": provider.on_queue_job_started,
        "on_queue_job_waiting": provider.on_queue_job_waiting,
        "on_queue_job_skipped": provider.on_queue_job_skipped,
        "on_queue_job_failed": provider.on_queue_job_failed,
        "on_queue_completed": provider.on_queue_completed,
        # Quiet hours
        "quiet_hours_enabled": provider.quiet_hours_enabled,
        "quiet_hours_start": provider.quiet_hours_start,
        "quiet_hours_end": provider.quiet_hours_end,
        # Daily digest
        "daily_digest_enabled": provider.daily_digest_enabled,
        "daily_digest_time": provider.daily_digest_time,
        # Printer filter
        "printer_id": provider.printer_id,
        # Status tracking
        "last_success": provider.last_success,
        "last_error": provider.last_error,
        "last_error_at": provider.last_error_at,
        # Timestamps
        "created_at": provider.created_at,
        "updated_at": provider.updated_at,
    }


# ============================================================================
# Provider List/Create Routes (no path parameters)
# ============================================================================


@router.get("/", response_model=list[NotificationProviderResponse])
async def list_notification_providers(db: AsyncSession = Depends(get_db)):
    """List all notification providers."""
    result = await db.execute(select(NotificationProvider).order_by(NotificationProvider.created_at.desc()))
    providers = result.scalars().all()

    return [_provider_to_dict(provider) for provider in providers]


@router.post("/", response_model=NotificationProviderResponse)
async def create_notification_provider(
    provider_data: NotificationProviderCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new notification provider."""
    provider = NotificationProvider(
        name=provider_data.name,
        provider_type=provider_data.provider_type.value,
        enabled=provider_data.enabled,
        config=json.dumps(provider_data.config),
        # Print lifecycle events
        on_print_start=provider_data.on_print_start,
        on_print_complete=provider_data.on_print_complete,
        on_print_failed=provider_data.on_print_failed,
        on_print_stopped=provider_data.on_print_stopped,
        on_print_progress=provider_data.on_print_progress,
        # Printer status events
        on_printer_offline=provider_data.on_printer_offline,
        on_printer_error=provider_data.on_printer_error,
        on_filament_low=provider_data.on_filament_low,
        on_maintenance_due=provider_data.on_maintenance_due,
        # AMS environmental alarms (regular AMS)
        on_ams_humidity_high=provider_data.on_ams_humidity_high,
        on_ams_temperature_high=provider_data.on_ams_temperature_high,
        # AMS-HT environmental alarms
        on_ams_ht_humidity_high=provider_data.on_ams_ht_humidity_high,
        on_ams_ht_temperature_high=provider_data.on_ams_ht_temperature_high,
        # Build plate detection
        on_plate_not_empty=provider_data.on_plate_not_empty,
        # Quiet hours
        quiet_hours_enabled=provider_data.quiet_hours_enabled,
        quiet_hours_start=provider_data.quiet_hours_start,
        quiet_hours_end=provider_data.quiet_hours_end,
        # Daily digest
        daily_digest_enabled=provider_data.daily_digest_enabled,
        daily_digest_time=provider_data.daily_digest_time,
        # Printer filter
        printer_id=provider_data.printer_id,
    )

    db.add(provider)
    await db.commit()
    await db.refresh(provider)

    logger.info(f"Created notification provider: {provider.name} ({provider.provider_type})")

    return _provider_to_dict(provider)


# ============================================================================
# Static Path Routes (must come BEFORE parameterized routes)
# ============================================================================


@router.post("/test-config", response_model=NotificationTestResponse)
async def test_notification_config(
    test_request: NotificationTestRequest,
    db: AsyncSession = Depends(get_db),
):
    """Test notification configuration before saving."""
    success, message = await notification_service.send_test_notification(
        test_request.provider_type.value, test_request.config, db
    )

    return NotificationTestResponse(success=success, message=message)


@router.post("/test-all")
async def test_all_notification_providers(db: AsyncSession = Depends(get_db)):
    """Send a test notification to all enabled providers."""
    result = await db.execute(select(NotificationProvider).where(NotificationProvider.enabled.is_(True)))
    providers = result.scalars().all()

    if not providers:
        return {"tested": 0, "success": 0, "failed": 0, "results": []}

    results = []
    success_count = 0
    failed_count = 0

    for provider in providers:
        config = json.loads(provider.config) if isinstance(provider.config, str) else provider.config
        success, message = await notification_service.send_test_notification(provider.provider_type, config, db)

        # Update provider status
        if success:
            provider.last_success = datetime.utcnow()
            success_count += 1
        else:
            provider.last_error = message
            provider.last_error_at = datetime.utcnow()
            failed_count += 1

        results.append(
            {
                "provider_id": provider.id,
                "provider_name": provider.name,
                "provider_type": provider.provider_type,
                "success": success,
                "message": message,
            }
        )

    await db.commit()

    return {
        "tested": len(providers),
        "success": success_count,
        "failed": failed_count,
        "results": results,
    }


# ============================================================================
# Notification Log Routes (must come BEFORE /{provider_id} routes)
# ============================================================================


@router.get("/logs", response_model=list[NotificationLogResponse])
async def get_notification_logs(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    provider_id: int | None = Query(default=None),
    event_type: str | None = Query(default=None),
    success: bool | None = Query(default=None),
    days: int | None = Query(default=7, ge=1, le=90, description="Filter logs from the last N days"),
    db: AsyncSession = Depends(get_db),
):
    """Get notification logs with optional filters."""
    query = select(NotificationLog).order_by(desc(NotificationLog.created_at))

    # Apply filters
    if provider_id is not None:
        query = query.where(NotificationLog.provider_id == provider_id)
    if event_type is not None:
        query = query.where(NotificationLog.event_type == event_type)
    if success is not None:
        query = query.where(NotificationLog.success == success)
    if days is not None:
        cutoff = datetime.utcnow() - timedelta(days=days)
        query = query.where(NotificationLog.created_at >= cutoff)

    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    logs = result.scalars().all()

    # Get provider info for each log
    response = []
    providers_cache: dict[int, NotificationProvider | None] = {}

    for log in logs:
        if log.provider_id not in providers_cache:
            provider_result = await db.execute(
                select(NotificationProvider).where(NotificationProvider.id == log.provider_id)
            )
            providers_cache[log.provider_id] = provider_result.scalar_one_or_none()

        provider = providers_cache[log.provider_id]
        response.append(
            NotificationLogResponse(
                id=log.id,
                provider_id=log.provider_id,
                provider_name=provider.name if provider else None,
                provider_type=provider.provider_type if provider else None,
                event_type=log.event_type,
                title=log.title,
                message=log.message,
                success=log.success,
                error_message=log.error_message,
                printer_id=log.printer_id,
                printer_name=log.printer_name,
                created_at=log.created_at,
            )
        )

    return response


@router.get("/logs/stats", response_model=NotificationLogStats)
async def get_notification_log_stats(
    days: int = Query(default=7, ge=1, le=90, description="Statistics for the last N days"),
    db: AsyncSession = Depends(get_db),
):
    """Get notification log statistics."""
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Total counts
    total_result = await db.execute(select(func.count(NotificationLog.id)).where(NotificationLog.created_at >= cutoff))
    total = total_result.scalar() or 0

    success_result = await db.execute(
        select(func.count(NotificationLog.id)).where(
            NotificationLog.created_at >= cutoff, NotificationLog.success.is_(True)
        )
    )
    success_count = success_result.scalar() or 0

    # By event type
    event_result = await db.execute(
        select(NotificationLog.event_type, func.count(NotificationLog.id))
        .where(NotificationLog.created_at >= cutoff)
        .group_by(NotificationLog.event_type)
    )
    by_event_type = {row[0]: row[1] for row in event_result.fetchall()}

    # By provider (need to join to get name)
    provider_result = await db.execute(
        select(NotificationProvider.name, func.count(NotificationLog.id))
        .join(NotificationProvider, NotificationLog.provider_id == NotificationProvider.id)
        .where(NotificationLog.created_at >= cutoff)
        .group_by(NotificationProvider.name)
    )
    by_provider = {row[0]: row[1] for row in provider_result.fetchall()}

    return NotificationLogStats(
        total=total,
        success_count=success_count,
        failure_count=total - success_count,
        by_event_type=by_event_type,
        by_provider=by_provider,
    )


@router.delete("/logs")
async def clear_notification_logs(
    older_than_days: int = Query(default=30, ge=1, description="Delete logs older than N days"),
    db: AsyncSession = Depends(get_db),
):
    """Clear old notification logs."""
    cutoff = datetime.utcnow() - timedelta(days=older_than_days)

    result = await db.execute(delete(NotificationLog).where(NotificationLog.created_at < cutoff))
    await db.commit()

    deleted_count = result.rowcount
    logger.info(f"Deleted {deleted_count} notification logs older than {older_than_days} days")

    return {"deleted": deleted_count, "message": f"Deleted {deleted_count} logs older than {older_than_days} days"}


# ============================================================================
# Provider Instance Routes (parameterized - must come LAST)
# ============================================================================


@router.get("/{provider_id}", response_model=NotificationProviderResponse)
async def get_notification_provider(
    provider_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get a specific notification provider."""
    result = await db.execute(select(NotificationProvider).where(NotificationProvider.id == provider_id))
    provider = result.scalar_one_or_none()

    if not provider:
        raise HTTPException(status_code=404, detail="Notification provider not found")

    return _provider_to_dict(provider)


@router.patch("/{provider_id}", response_model=NotificationProviderResponse)
async def update_notification_provider(
    provider_id: int,
    update_data: NotificationProviderUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a notification provider."""
    result = await db.execute(select(NotificationProvider).where(NotificationProvider.id == provider_id))
    provider = result.scalar_one_or_none()

    if not provider:
        raise HTTPException(status_code=404, detail="Notification provider not found")

    # Update only provided fields
    update_dict = update_data.model_dump(exclude_unset=True)

    for key, value in update_dict.items():
        if key == "config" and value is not None:
            setattr(provider, key, json.dumps(value))
        elif key == "provider_type" and value is not None:
            setattr(provider, key, value.value)
        else:
            setattr(provider, key, value)

    await db.commit()
    await db.refresh(provider)

    logger.info(f"Updated notification provider: {provider.name}")

    return _provider_to_dict(provider)


@router.delete("/{provider_id}")
async def delete_notification_provider(
    provider_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a notification provider."""
    result = await db.execute(select(NotificationProvider).where(NotificationProvider.id == provider_id))
    provider = result.scalar_one_or_none()

    if not provider:
        raise HTTPException(status_code=404, detail="Notification provider not found")

    name = provider.name
    await db.delete(provider)
    await db.commit()

    logger.info(f"Deleted notification provider: {name}")

    return {"message": f"Notification provider '{name}' deleted"}


@router.post("/{provider_id}/test", response_model=NotificationTestResponse)
async def test_notification_provider(
    provider_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Send a test notification using an existing provider."""
    result = await db.execute(select(NotificationProvider).where(NotificationProvider.id == provider_id))
    provider = result.scalar_one_or_none()

    if not provider:
        raise HTTPException(status_code=404, detail="Notification provider not found")

    config = json.loads(provider.config) if isinstance(provider.config, str) else provider.config
    success, message = await notification_service.send_test_notification(provider.provider_type, config, db)

    # Update provider status
    if success:
        provider.last_success = datetime.utcnow()
    else:
        provider.last_error = message
        provider.last_error_at = datetime.utcnow()

    await db.commit()

    return NotificationTestResponse(success=success, message=message)
