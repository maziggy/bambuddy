"""SpoolBuddy device management API routes."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.core.websocket import ws_manager
from backend.app.models.spoolbuddy_device import SpoolBuddyDevice
from backend.app.models.user import User
from backend.app.schemas.spoolbuddy import (
    CalibrationResponse,
    DeviceRegisterRequest,
    DeviceResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    ScaleReadingRequest,
    SetCalibrationFactorRequest,
    TagRemovedRequest,
    TagScannedRequest,
    UpdateSpoolWeightRequest,
)
from backend.app.services.spool_tag_matcher import get_spool_by_tag

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/spoolbuddy", tags=["spoolbuddy"])

OFFLINE_THRESHOLD_SECONDS = 30


def _is_online(device: SpoolBuddyDevice) -> bool:
    if not device.last_seen:
        return False
    return (
        datetime.now(timezone.utc) - device.last_seen.replace(tzinfo=timezone.utc)
    ).total_seconds() < OFFLINE_THRESHOLD_SECONDS


def _device_to_response(device: SpoolBuddyDevice) -> DeviceResponse:
    return DeviceResponse(
        id=device.id,
        device_id=device.device_id,
        hostname=device.hostname,
        ip_address=device.ip_address,
        firmware_version=device.firmware_version,
        has_nfc=device.has_nfc,
        has_scale=device.has_scale,
        tare_offset=device.tare_offset,
        calibration_factor=device.calibration_factor,
        last_seen=device.last_seen,
        pending_command=device.pending_command,
        nfc_ok=device.nfc_ok,
        scale_ok=device.scale_ok,
        uptime_s=device.uptime_s,
        online=_is_online(device),
        created_at=device.created_at,
        updated_at=device.updated_at,
    )


# --- Device endpoints ---


@router.post("/devices/register", response_model=DeviceResponse)
async def register_device(
    req: DeviceRegisterRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Register or re-register a SpoolBuddy device."""
    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == req.device_id))
    device = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if device:
        device.hostname = req.hostname
        device.ip_address = req.ip_address
        device.firmware_version = req.firmware_version
        device.has_nfc = req.has_nfc
        device.has_scale = req.has_scale
        device.last_seen = now
        logger.info("SpoolBuddy device re-registered: %s (%s)", req.device_id, req.hostname)
    else:
        device = SpoolBuddyDevice(
            device_id=req.device_id,
            hostname=req.hostname,
            ip_address=req.ip_address,
            firmware_version=req.firmware_version,
            has_nfc=req.has_nfc,
            has_scale=req.has_scale,
            tare_offset=req.tare_offset,
            calibration_factor=req.calibration_factor,
            last_seen=now,
        )
        db.add(device)
        logger.info("SpoolBuddy device registered: %s (%s)", req.device_id, req.hostname)

    await db.commit()
    await db.refresh(device)

    await ws_manager.broadcast(
        {
            "type": "spoolbuddy_online",
            "device_id": device.device_id,
            "hostname": device.hostname,
        }
    )

    return _device_to_response(device)


@router.get("/devices", response_model=list[DeviceResponse])
async def list_devices(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """List all registered SpoolBuddy devices."""
    result = await db.execute(select(SpoolBuddyDevice).order_by(SpoolBuddyDevice.hostname))
    devices = list(result.scalars().all())
    return [_device_to_response(d) for d in devices]


@router.post("/devices/{device_id}/heartbeat", response_model=HeartbeatResponse)
async def device_heartbeat(
    device_id: str,
    req: HeartbeatRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Daemon heartbeat — updates status and returns pending commands."""
    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    was_offline = not _is_online(device)
    now = datetime.now(timezone.utc)

    device.last_seen = now
    device.nfc_ok = req.nfc_ok
    device.scale_ok = req.scale_ok
    device.uptime_s = req.uptime_s
    if req.firmware_version:
        device.firmware_version = req.firmware_version
    if req.ip_address:
        device.ip_address = req.ip_address

    # Return and clear pending command
    pending = device.pending_command
    device.pending_command = None

    await db.commit()

    if was_offline:
        await ws_manager.broadcast(
            {
                "type": "spoolbuddy_online",
                "device_id": device.device_id,
                "hostname": device.hostname,
            }
        )

    return HeartbeatResponse(
        pending_command=pending,
        tare_offset=device.tare_offset,
        calibration_factor=device.calibration_factor,
    )


# --- NFC endpoints ---


@router.post("/nfc/tag-scanned")
async def nfc_tag_scanned(
    req: TagScannedRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """RPi reports NFC tag detected — lookup spool and broadcast."""
    spool = await get_spool_by_tag(db, req.tag_uid, req.tray_uuid or "")

    if spool:
        await ws_manager.broadcast(
            {
                "type": "spoolbuddy_tag_matched",
                "device_id": req.device_id,
                "tag_uid": req.tag_uid,
                "spool": {
                    "id": spool.id,
                    "material": spool.material,
                    "subtype": spool.subtype,
                    "color_name": spool.color_name,
                    "rgba": spool.rgba,
                    "brand": spool.brand,
                    "label_weight": spool.label_weight,
                    "core_weight": spool.core_weight,
                    "weight_used": spool.weight_used,
                },
            }
        )
        logger.info("SpoolBuddy tag matched: %s -> spool %d", req.tag_uid, spool.id)
    else:
        await ws_manager.broadcast(
            {
                "type": "spoolbuddy_unknown_tag",
                "device_id": req.device_id,
                "tag_uid": req.tag_uid,
                "sak": req.sak,
                "tag_type": req.tag_type,
            }
        )
        logger.info("SpoolBuddy unknown tag: %s", req.tag_uid)

    return {"status": "ok", "matched": spool is not None, "spool_id": spool.id if spool else None}


@router.post("/nfc/tag-removed")
async def nfc_tag_removed(
    req: TagRemovedRequest,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """RPi reports NFC tag removed — broadcast event."""
    await ws_manager.broadcast(
        {
            "type": "spoolbuddy_tag_removed",
            "device_id": req.device_id,
            "tag_uid": req.tag_uid,
        }
    )
    return {"status": "ok"}


# --- Scale endpoints ---


@router.post("/scale/reading")
async def scale_reading(
    req: ScaleReadingRequest,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """RPi reports scale weight — broadcast to all clients."""
    await ws_manager.broadcast(
        {
            "type": "spoolbuddy_weight",
            "device_id": req.device_id,
            "weight_grams": req.weight_grams,
            "stable": req.stable,
            "raw_adc": req.raw_adc,
        }
    )
    return {"status": "ok"}


@router.post("/scale/update-spool-weight")
async def update_spool_weight(
    req: UpdateSpoolWeightRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Update spool's used weight from scale reading."""
    from backend.app.models.spool import Spool

    result = await db.execute(select(Spool).where(Spool.id == req.spool_id))
    spool = result.scalar_one_or_none()
    if not spool:
        raise HTTPException(status_code=404, detail="Spool not found")

    # net weight = total on scale minus empty spool core
    net_filament = max(0, req.weight_grams - spool.core_weight)
    spool.weight_used = max(0, spool.label_weight - net_filament)
    await db.commit()

    logger.info(
        "SpoolBuddy updated spool %d weight: %.1fg on scale, %.1fg used",
        spool.id,
        req.weight_grams,
        spool.weight_used,
    )
    return {"status": "ok", "weight_used": spool.weight_used}


# --- Calibration endpoints ---


@router.post("/devices/{device_id}/calibration/tare")
async def tare_scale(
    device_id: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Set pending tare command for the device to pick up."""
    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    device.pending_command = "tare"
    await db.commit()
    return {"status": "ok", "message": "Tare command queued"}


@router.post("/devices/{device_id}/calibration/set-factor")
async def set_calibration_factor(
    device_id: str,
    req: SetCalibrationFactorRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Calculate and store calibration factor from a known weight."""
    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    raw_delta = req.raw_adc - device.tare_offset
    if raw_delta == 0:
        raise HTTPException(status_code=400, detail="Raw ADC value equals tare offset — place weight on scale")

    device.calibration_factor = req.known_weight_grams / raw_delta
    await db.commit()

    logger.info(
        "SpoolBuddy %s calibration factor set to %.6f (known=%.1fg, raw=%d, tare=%d)",
        device_id,
        device.calibration_factor,
        req.known_weight_grams,
        req.raw_adc,
        device.tare_offset,
    )
    return CalibrationResponse(
        tare_offset=device.tare_offset,
        calibration_factor=device.calibration_factor,
    )


@router.get("/devices/{device_id}/calibration", response_model=CalibrationResponse)
async def get_calibration(
    device_id: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """Get current calibration values for a device."""
    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    return CalibrationResponse(
        tare_offset=device.tare_offset,
        calibration_factor=device.calibration_factor,
    )


# --- Background watchdog ---


async def spoolbuddy_watchdog():
    """Check for devices that have gone offline (no heartbeat for 30s).

    Called periodically from the main app's background task loop.
    """
    from backend.app.core.database import async_session

    async with async_session() as db:
        result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.last_seen.isnot(None)))
        devices = list(result.scalars().all())

        threshold = datetime.now(timezone.utc) - timedelta(seconds=OFFLINE_THRESHOLD_SECONDS)
        for device in devices:
            last_seen = device.last_seen.replace(tzinfo=timezone.utc) if device.last_seen else None
            if last_seen and last_seen < threshold:
                # Only broadcast once — clear last_seen after marking offline
                await ws_manager.broadcast(
                    {
                        "type": "spoolbuddy_offline",
                        "device_id": device.device_id,
                    }
                )
                device.last_seen = None
                logger.info("SpoolBuddy device offline: %s", device.device_id)

        await db.commit()
