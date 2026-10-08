"""API routes for notification providers."""

import json
import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import delete, desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled, ScopedCaller, require_notification_send
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.notification import NotificationLog, NotificationProvider
from backend.app.models.notification_lock_screen_widget import NotificationLockScreenWidget
from backend.app.models.user import User
from backend.app.schemas.notification import (
    AppMessage,
    AppMessageChannel,
    AppMessageResult,
    NotificationLogResponse,
    NotificationLogStats,
    NotificationProviderCreate,
    NotificationProviderResponse,
    NotificationProviderUpdate,
    NotificationTestRequest,
    NotificationTestResponse,
)
from backend.app.services.notification_service import notification_service
from backend.app.services.notify_client import NotifyError, notify_credentials
from backend.app.services.notify_live_activities import notify_live_activities
from backend.app.services.notify_widgets import notify_widgets
from backend.app.services.telegram_reactions import telegram_reaction_poller
from backend.app.utils.notification_photos import find_notification_photo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _notify_providers_changed() -> None:
    """Wake optional workers after a provider edit, without waiting for their HTTP work."""
    for worker in (notify_live_activities, notify_widgets):
        try:
            worker.providers_changed()
        except Exception:
            logger.exception("Could not refresh Notify worker configuration")


async def _resync_reaction_poller():
    """Start/stop Telegram reaction polls after a provider changed (#3046).

    Never fails the request: the provider row is already saved, and the
    poller catches up on the next restart at worst.
    """
    try:
        await telegram_reaction_poller.sync()
    except Exception as e:
        logger.warning("Telegram reaction poller resync failed: %s", e)


def _provider_to_dict(provider: NotificationProvider) -> dict:
    """Convert a NotificationProvider model to a response dictionary."""
    return {
        "id": provider.id,
        "name": provider.name,
        "provider_type": provider.provider_type,
        "enabled": provider.enabled,
        "config": json.loads(provider.config) if isinstance(provider.config, str) else provider.config,
        "attach_photo": provider.attach_photo,
        # Print lifecycle events
        "on_print_start": provider.on_print_start,
        "on_print_complete": provider.on_print_complete,
        "on_print_failed": provider.on_print_failed,
        "on_print_stopped": provider.on_print_stopped,
        "on_print_progress": provider.on_print_progress,
        "on_print_missing_spool_assignment": provider.on_print_missing_spool_assignment,
        "on_billing_charge_failed": provider.on_billing_charge_failed,
        # Printer status events
        "on_printer_offline": provider.on_printer_offline,
        "on_printer_error": provider.on_printer_error,
        "on_ai_failure_detection": provider.on_ai_failure_detection,
        "on_filament_low": provider.on_filament_low,
        "on_maintenance_due": provider.on_maintenance_due,
        # Home Assistant sensor alerts (#1148, #2824). Both directions of this
        # file are hand-maintained field maps, so a column missing here reads
        # back as the schema default (False) no matter what the row holds.
        "on_ha_sensor_alert": provider.on_ha_sensor_alert,
        "on_location_ha_sensor_alert": provider.on_location_ha_sensor_alert,
        # AMS environmental alarms (regular AMS)
        "on_ams_humidity_high": provider.on_ams_humidity_high,
        "on_ams_temperature_high": provider.on_ams_temperature_high,
        "on_ams_drying_suspended": provider.on_ams_drying_suspended,
        # AMS-HT environmental alarms
        "on_ams_ht_humidity_high": provider.on_ams_ht_humidity_high,
        "on_ams_ht_temperature_high": provider.on_ams_ht_temperature_high,
        # Build plate detection
        "on_plate_not_empty": provider.on_plate_not_empty,
        "on_plate_clear_required": provider.on_plate_clear_required,
        # Post-print outcome confirmation (#1898)
        "on_print_confirm_request": provider.on_print_confirm_request,
        # Rows from before #3046 hold NULL here; "buttons" is what they did.
        "telegram_verdict_mode": provider.telegram_verdict_mode or "buttons",
        # Bed cooled
        "on_bed_cooled": provider.on_bed_cooled,
        # First layer complete
        "on_first_layer_complete": provider.on_first_layer_complete,
        # Messages from connected apps
        "on_app_message": bool(provider.on_app_message),
        # Inventory stock alerts. Absent here, the toggles above always read
        # back off no matter what the row holds — the same hand-maintained
        # field map the Home Assistant comment warns about.
        "on_stock_reorder_alert": provider.on_stock_reorder_alert,
        "on_stock_break_alert": provider.on_stock_break_alert,
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
async def list_notification_providers(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.NOTIFICATIONS_READ),
):
    """List all notification providers."""
    result = await db.execute(select(NotificationProvider).order_by(NotificationProvider.created_at.desc()))
    providers = result.scalars().all()

    return [_provider_to_dict(provider) for provider in providers]


@router.post("/", response_model=NotificationProviderResponse)
async def create_notification_provider(
    provider_data: NotificationProviderCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.NOTIFICATIONS_CREATE),
):
    """Create a new notification provider."""
    if provider_data.provider_type.value == "notify":
        try:
            notify_credentials(provider_data.config)
        except NotifyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
    provider = NotificationProvider(
        name=provider_data.name,
        provider_type=provider_data.provider_type.value,
        enabled=provider_data.enabled,
        config=json.dumps(provider_data.config),
        attach_photo=provider_data.attach_photo,
        # Print lifecycle events
        on_print_start=provider_data.on_print_start,
        on_print_complete=provider_data.on_print_complete,
        on_print_failed=provider_data.on_print_failed,
        on_print_stopped=provider_data.on_print_stopped,
        on_print_progress=provider_data.on_print_progress,
        on_print_missing_spool_assignment=provider_data.on_print_missing_spool_assignment,
        on_billing_charge_failed=provider_data.on_billing_charge_failed,
        # Printer status events
        on_printer_offline=provider_data.on_printer_offline,
        on_printer_error=provider_data.on_printer_error,
        on_ai_failure_detection=provider_data.on_ai_failure_detection,
        on_filament_low=provider_data.on_filament_low,
        on_maintenance_due=provider_data.on_maintenance_due,
        # Home Assistant sensor alerts (#1148, #2824)
        on_ha_sensor_alert=provider_data.on_ha_sensor_alert,
        on_location_ha_sensor_alert=provider_data.on_location_ha_sensor_alert,
        # AMS environmental alarms (regular AMS)
        on_ams_humidity_high=provider_data.on_ams_humidity_high,
        on_ams_temperature_high=provider_data.on_ams_temperature_high,
        on_ams_drying_suspended=provider_data.on_ams_drying_suspended,
        # AMS-HT environmental alarms
        on_ams_ht_humidity_high=provider_data.on_ams_ht_humidity_high,
        on_ams_ht_temperature_high=provider_data.on_ams_ht_temperature_high,
        # Build plate detection
        on_plate_not_empty=provider_data.on_plate_not_empty,
        on_plate_clear_required=provider_data.on_plate_clear_required,
        # Post-print outcome confirmation (#1898)
        on_print_confirm_request=provider_data.on_print_confirm_request,
        telegram_verdict_mode=provider_data.telegram_verdict_mode,
        # Bed cooled
        on_bed_cooled=provider_data.on_bed_cooled,
        # First layer complete
        on_first_layer_complete=provider_data.on_first_layer_complete,
        on_app_message=provider_data.on_app_message,
        # Inventory stock alerts
        on_stock_reorder_alert=provider_data.on_stock_reorder_alert,
        on_stock_break_alert=provider_data.on_stock_break_alert,
        # Print queue events
        on_queue_job_added=provider_data.on_queue_job_added,
        on_queue_job_assigned=provider_data.on_queue_job_assigned,
        on_queue_job_started=provider_data.on_queue_job_started,
        on_queue_job_waiting=provider_data.on_queue_job_waiting,
        on_queue_job_skipped=provider_data.on_queue_job_skipped,
        on_queue_job_failed=provider_data.on_queue_job_failed,
        on_queue_completed=provider_data.on_queue_completed,
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

    logger.info("Created notification provider: %s (%s)", provider.name, provider.provider_type)
    if provider.provider_type == "notify":
        _notify_providers_changed()
    await _resync_reaction_poller()

    return _provider_to_dict(provider)


# ============================================================================
# Static Path Routes (must come BEFORE parameterized routes)
# ============================================================================


@router.post("/test-config", response_model=NotificationTestResponse)
async def test_notification_config(
    test_request: NotificationTestRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.NOTIFICATIONS_CREATE),
):
    """Test notification configuration before saving."""
    success, message = await notification_service.send_test_notification(
        test_request.provider_type.value, test_request.config, db, attach_photo=test_request.attach_photo
    )

    return NotificationTestResponse(success=success, message=message)


@router.post("/test-all")
async def test_all_notification_providers(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.NOTIFICATIONS_UPDATE),
):
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
        success, message = await notification_service.send_test_notification(
            provider.provider_type, config, db, attach_photo=provider.attach_photo
        )

        # Update provider status
        if success:
            provider.last_success = datetime.now(timezone.utc)
            success_count += 1
        else:
            provider.last_error = message
            provider.last_error_at = datetime.now(timezone.utc)
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
    _: User | None = RequirePermissionIfAuthEnabled(Permission.NOTIFICATIONS_READ),
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
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
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
    _: User | None = RequirePermissionIfAuthEnabled(Permission.NOTIFICATIONS_READ),
):
    """Get notification log statistics."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

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
    _: User | None = RequirePermissionIfAuthEnabled(Permission.NOTIFICATIONS_DELETE),
):
    """Clear old notification logs."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)

    result = await db.execute(delete(NotificationLog).where(NotificationLog.created_at < cutoff))
    await db.commit()

    deleted_count = result.rowcount
    logger.info("Deleted %s notification logs older than %s days", deleted_count, older_than_days)

    return {"deleted": deleted_count, "message": f"Deleted {deleted_count} logs older than {older_than_days} days"}


@router.get("/photos/{filename}")
async def get_notification_photo(filename: str):
    """Serve an ad-hoc notification snapshot to HA, Bark or Slack.

    They fetch this URL themselves with no session, so the unguessable
    filename is the credential and opens this one photo only -- see
    backend/app/utils/notification_photos.py. Anything that isn't a live
    photo of exactly that shape is a 404.
    """
    photo_path = find_notification_photo(filename)
    if photo_path is None:
        raise HTTPException(404, "Photo not found")

    return FileResponse(path=photo_path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


# ============================================================================
# Provider Instance Routes (parameterized - must come LAST)
# ============================================================================


# Messages from other applications -------------------------------------------

# Per caller, in memory: enough for any real app (Bambuddy Orders sends a few a
# day), and a buggy or hostile one can't flood the channels.
APP_MESSAGE_LIMIT = 20
APP_MESSAGE_WINDOW_SECONDS = 60
_app_message_times: dict[str, deque[float]] = defaultdict(deque)


def _app_sender(caller: ScopedCaller) -> tuple[str, str]:
    """(rate-limit key, name shown in the log) for whoever sends the message."""
    if caller.api_key is not None:
        return f"key:{caller.api_key.id}", caller.api_key.name
    if caller.user is not None:
        return f"user:{caller.user.id}", caller.user.username
    return "anonymous", "app"


def _check_app_message_rate(key: str) -> None:
    times = _app_message_times[key]
    cutoff = time.monotonic() - APP_MESSAGE_WINDOW_SECONDS
    while times and times[0] < cutoff:
        times.popleft()
    if len(times) >= APP_MESSAGE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many messages; try again in a minute")
    times.append(time.monotonic())


@router.post("/app-message", response_model=AppMessageResult)
async def send_app_message(
    data: AppMessage,
    db: AsyncSession = Depends(get_db),
    caller: ScopedCaller = Depends(require_notification_send()),
):
    """Send a message through every enabled channel that has "Messages from
    connected apps" on. For other applications, e.g. Bambuddy Orders; an API
    key needs the "Send notifications" permission."""
    key, sender = _app_sender(caller)
    _check_app_message_rate(key)
    channels = await notification_service.on_app_message(
        db, sender=sender, title=data.title, message=data.message, url=data.url
    )
    return AppMessageResult(channels=channels)


@router.get("/app-message/channels", response_model=list[AppMessageChannel])
async def app_message_channels(
    db: AsyncSession = Depends(get_db),
    _: ScopedCaller = Depends(require_notification_send()),
):
    """The enabled channels that deliver app messages: names and types only,
    so an app can tell its user where its messages will arrive."""
    rows = await db.execute(
        select(NotificationProvider)
        .where(NotificationProvider.enabled.is_(True), NotificationProvider.on_app_message.is_(True))
        .order_by(NotificationProvider.name)
    )
    return [AppMessageChannel(name=p.name, provider_type=p.provider_type) for p in rows.scalars()]


@router.get("/{provider_id}", response_model=NotificationProviderResponse)
async def get_notification_provider(
    provider_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.NOTIFICATIONS_READ),
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
    _: User | None = RequirePermissionIfAuthEnabled(Permission.NOTIFICATIONS_UPDATE),
):
    """Update a notification provider."""
    result = await db.execute(select(NotificationProvider).where(NotificationProvider.id == provider_id))
    provider = result.scalar_one_or_none()

    if not provider:
        raise HTTPException(status_code=404, detail="Notification provider not found")

    old_config = json.loads(provider.config) if isinstance(provider.config, str) else provider.config
    old_type = provider.provider_type

    # Update only provided fields
    update_dict = update_data.model_dump(exclude_unset=True)

    effective_type = update_dict.get("provider_type") or provider.provider_type
    effective_type = getattr(effective_type, "value", effective_type)
    if effective_type == "notify":
        try:
            effective_config = update_dict.get("config", old_config)
            if not isinstance(effective_config, dict):
                raise NotifyError("Notify! configuration must be an object")
            notify_credentials(effective_config)
        except NotifyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    for key, value in update_dict.items():
        if key == "config" and value is not None:
            setattr(provider, key, json.dumps(value))
        elif key == "provider_type" and value is not None:
            setattr(provider, key, value.value)
        else:
            setattr(provider, key, value)

    if old_type == "notify":
        effective_config = json.loads(provider.config) if isinstance(provider.config, str) else provider.config
        remove_widgets = (
            provider.provider_type != "notify"
            or not provider.enabled
            or effective_config.get("lock_screen_widgets") is not True
        )
        if remove_widgets or provider.printer_id is not None:
            condition = NotificationLockScreenWidget.provider_id == provider_id
            if not remove_widgets:
                condition = condition & (NotificationLockScreenWidget.printer_id != provider.printer_id)
            # Persist opt-out before returning, even if the user immediately
            # opts back in. The worker finishes cleanup before creating anew.
            await db.execute(
                update(NotificationLockScreenWidget)
                .where(condition)
                .values(
                    state="deleting",
                    failures=0,
                    next_attempt_at=None,
                )
            )

    await db.commit()
    await db.refresh(provider)

    response = _provider_to_dict(provider)
    new_config = response["config"]
    changed = old_type != provider.provider_type or any(
        old_config.get(key) != new_config.get(key) for key in ("device_id", "token")
    )
    # Refresh opened a new read transaction; close it before remote cleanup.
    await db.commit()
    if old_type == "notify" and changed:
        await notify_live_activities.schedule_cleanup(provider_id, old_config)
        await notify_widgets.schedule_cleanup(provider_id, old_config)
    if old_type == "notify" or provider.provider_type == "notify":
        _notify_providers_changed()
    logger.info("Updated notification provider: %s", provider.name)
    await _resync_reaction_poller()

    return response


@router.delete("/{provider_id}")
async def delete_notification_provider(
    provider_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.NOTIFICATIONS_DELETE),
):
    """Delete a notification provider."""
    result = await db.execute(select(NotificationProvider).where(NotificationProvider.id == provider_id))
    provider = result.scalar_one_or_none()

    if not provider:
        raise HTTPException(status_code=404, detail="Notification provider not found")

    name = provider.name
    if provider.provider_type == "notify":
        config = json.loads(provider.config) if isinstance(provider.config, str) else provider.config
        # Retire this provider and capture owned IDs before FK cascade removes
        # them. Queue remote cleanup; HTTP must never delay this request.
        provider.enabled = False
        await db.commit()
        await notify_live_activities.schedule_cleanup(provider_id, config)
        await notify_widgets.schedule_cleanup(provider_id, config)
    await db.delete(provider)
    await db.commit()

    if provider.provider_type == "notify":
        _notify_providers_changed()

    logger.info("Deleted notification provider: %s", name)
    await _resync_reaction_poller()

    return {"message": f"Notification provider '{name}' deleted"}


@router.post("/{provider_id}/test", response_model=NotificationTestResponse)
async def test_notification_provider(
    provider_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.NOTIFICATIONS_UPDATE),
):
    """Send a test notification using an existing provider."""
    result = await db.execute(select(NotificationProvider).where(NotificationProvider.id == provider_id))
    provider = result.scalar_one_or_none()

    if not provider:
        raise HTTPException(status_code=404, detail="Notification provider not found")

    config = json.loads(provider.config) if isinstance(provider.config, str) else provider.config
    success, message = await notification_service.send_test_notification(
        provider.provider_type, config, db, attach_photo=provider.attach_photo
    )

    # Update provider status
    if success:
        provider.last_success = datetime.now(timezone.utc)
    else:
        provider.last_error = message
        provider.last_error_at = datetime.now(timezone.utc)

    await db.commit()

    return NotificationTestResponse(success=success, message=message)
