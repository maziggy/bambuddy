"""API routes for smart plug management."""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.settings import get_setting
from backend.app.core.database import get_db
from backend.app.models.printer import Printer
from backend.app.models.smart_plug import SmartPlug
from backend.app.schemas.smart_plug import (
    HAEntity,
    HATestConnectionRequest,
    HATestConnectionResponse,
    SmartPlugControl,
    SmartPlugCreate,
    SmartPlugEnergy,
    SmartPlugResponse,
    SmartPlugStatus,
    SmartPlugTestConnection,
    SmartPlugUpdate,
)
from backend.app.services.discovery import tasmota_scanner
from backend.app.services.homeassistant import homeassistant_service
from backend.app.services.notification_service import notification_service
from backend.app.services.printer_manager import printer_manager
from backend.app.services.tasmota import tasmota_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/smart-plugs", tags=["smart-plugs"])


@router.get("/", response_model=list[SmartPlugResponse])
async def list_smart_plugs(db: AsyncSession = Depends(get_db)):
    """List all smart plugs."""
    result = await db.execute(select(SmartPlug).order_by(SmartPlug.name))
    return list(result.scalars().all())


@router.post("/", response_model=SmartPlugResponse)
async def create_smart_plug(
    data: SmartPlugCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new smart plug."""
    # Validate printer_id if provided
    if data.printer_id:
        result = await db.execute(select(Printer).where(Printer.id == data.printer_id))
        if not result.scalar_one_or_none():
            raise HTTPException(400, "Printer not found")

        # Check if printer already has a plug assigned
        result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == data.printer_id))
        if result.scalar_one_or_none():
            raise HTTPException(400, "This printer already has a smart plug assigned")

    plug = SmartPlug(**data.model_dump())
    db.add(plug)
    await db.commit()
    await db.refresh(plug)

    if plug.plug_type == "homeassistant":
        logger.info(f"Created Home Assistant plug '{plug.name}' ({plug.ha_entity_id})")
    else:
        logger.info(f"Created Tasmota plug '{plug.name}' at {plug.ip_address}")
    return plug


@router.get("/by-printer/{printer_id}", response_model=SmartPlugResponse | None)
async def get_smart_plug_by_printer(printer_id: int, db: AsyncSession = Depends(get_db)):
    """Get the smart plug assigned to a printer."""
    result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
    plug = result.scalar_one_or_none()
    if not plug:
        return None
    return plug


# Tasmota Discovery Endpoints
# NOTE: These must be defined BEFORE /{plug_id} routes to avoid path conflicts


class TasmotaScanRequest(BaseModel):
    """Request to scan for Tasmota devices."""

    from_ip: str | None = None  # Starting IP (auto-detected if not provided)
    to_ip: str | None = None  # Ending IP (auto-detected if not provided)
    timeout: float = 1.0  # Connection timeout per host


def get_local_network_range() -> tuple[str, str]:
    """Auto-detect local network and return IP range to scan."""
    import socket

    try:
        # Get local IP by connecting to a public DNS (doesn't actually send data)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()

        # Parse IP and create range (assume /24 subnet)
        parts = local_ip.split(".")
        base = ".".join(parts[:3])
        from_ip = f"{base}.1"
        to_ip = f"{base}.254"

        logger.info(f"Auto-detected network: {from_ip} - {to_ip} (local IP: {local_ip})")
        return from_ip, to_ip

    except Exception as e:
        logger.error(f"Failed to detect local network: {e}")
        # Fallback to common home network
        return "192.168.1.1", "192.168.1.254"


class TasmotaScanStatus(BaseModel):
    """Tasmota scan status response."""

    running: bool
    scanned: int
    total: int


class DiscoveredTasmotaDevice(BaseModel):
    """Discovered Tasmota device."""

    ip_address: str
    name: str
    module: int | None = None
    state: str | None = None
    discovered_at: str | None = None


@router.post("/discover/scan", response_model=TasmotaScanStatus)
async def start_tasmota_scan(request: TasmotaScanRequest | None = Body(default=None)):
    """Start an IP range scan for Tasmota devices.

    Auto-detects local network if no IP range provided.
    """
    import asyncio

    # Auto-detect network
    from_ip, to_ip = get_local_network_range()
    timeout = request.timeout if request else 1.0

    # Start scan in background
    asyncio.create_task(tasmota_scanner.scan_range(from_ip, to_ip, timeout))

    # Return immediate status
    scanned, total = tasmota_scanner.progress
    return TasmotaScanStatus(
        running=tasmota_scanner.is_running,
        scanned=scanned,
        total=total,
    )


@router.get("/discover/status", response_model=TasmotaScanStatus)
async def get_tasmota_scan_status():
    """Get the current Tasmota scan status."""
    scanned, total = tasmota_scanner.progress
    return TasmotaScanStatus(
        running=tasmota_scanner.is_running,
        scanned=scanned,
        total=total,
    )


@router.post("/discover/stop", response_model=TasmotaScanStatus)
async def stop_tasmota_scan():
    """Stop the current Tasmota scan."""
    tasmota_scanner.stop()
    scanned, total = tasmota_scanner.progress
    return TasmotaScanStatus(
        running=tasmota_scanner.is_running,
        scanned=scanned,
        total=total,
    )


@router.get("/discover/devices", response_model=list[DiscoveredTasmotaDevice])
async def get_discovered_tasmota_devices():
    """Get list of discovered Tasmota devices."""
    return [
        DiscoveredTasmotaDevice(
            ip_address=d["ip_address"],
            name=d["name"],
            module=d.get("module"),
            state=d.get("state"),
            discovered_at=d.get("discovered_at"),
        )
        for d in tasmota_scanner.discovered_devices
    ]


# Home Assistant Discovery Endpoints


@router.post("/ha/test-connection", response_model=HATestConnectionResponse)
async def test_ha_connection(request: HATestConnectionRequest):
    """Test connection to Home Assistant."""
    result = await homeassistant_service.test_connection(request.url, request.token)
    return HATestConnectionResponse(**result)


@router.get("/ha/entities", response_model=list[HAEntity])
async def list_ha_entities(db: AsyncSession = Depends(get_db)):
    """List available Home Assistant entities.

    Requires HA connection settings to be configured in Settings.
    """
    ha_url = await get_setting(db, "ha_url") or ""
    ha_token = await get_setting(db, "ha_token") or ""

    if not ha_url or not ha_token:
        raise HTTPException(
            400, "Home Assistant not configured. Please set HA URL and token in Settings → Network → Home Assistant."
        )

    entities = await homeassistant_service.list_entities(ha_url, ha_token)
    return [HAEntity(**e) for e in entities]


@router.get("/{plug_id}", response_model=SmartPlugResponse)
async def get_smart_plug(plug_id: int, db: AsyncSession = Depends(get_db)):
    """Get a specific smart plug."""
    result = await db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
    plug = result.scalar_one_or_none()
    if not plug:
        raise HTTPException(404, "Smart plug not found")
    return plug


@router.patch("/{plug_id}", response_model=SmartPlugResponse)
async def update_smart_plug(
    plug_id: int,
    data: SmartPlugUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a smart plug."""
    result = await db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
    plug = result.scalar_one_or_none()
    if not plug:
        raise HTTPException(404, "Smart plug not found")

    update_data = data.model_dump(exclude_unset=True)

    # Validate new printer_id if being changed
    if "printer_id" in update_data and update_data["printer_id"]:
        new_printer_id = update_data["printer_id"]

        # Check printer exists
        result = await db.execute(select(Printer).where(Printer.id == new_printer_id))
        if not result.scalar_one_or_none():
            raise HTTPException(400, "Printer not found")

        # Check if that printer already has a different plug assigned
        result = await db.execute(
            select(SmartPlug).where(
                SmartPlug.printer_id == new_printer_id,
                SmartPlug.id != plug_id,
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(400, "This printer already has a smart plug assigned")

    for field, value in update_data.items():
        setattr(plug, field, value)

    await db.commit()
    await db.refresh(plug)

    logger.info(f"Updated smart plug '{plug.name}'")
    return plug


@router.delete("/{plug_id}")
async def delete_smart_plug(plug_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a smart plug."""
    result = await db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
    plug = result.scalar_one_or_none()
    if not plug:
        raise HTTPException(404, "Smart plug not found")

    plug_name = plug.name
    await db.delete(plug)
    await db.commit()

    logger.info(f"Deleted smart plug '{plug_name}'")
    return {"message": "Smart plug deleted"}


async def _get_service_for_plug(plug: SmartPlug, db: AsyncSession):
    """Get the appropriate service for the plug type.

    For HA plugs, configures the service with current settings from DB.
    """
    if plug.plug_type == "homeassistant":
        # Configure HA service with current settings
        ha_url = await get_setting(db, "ha_url") or ""
        ha_token = await get_setting(db, "ha_token") or ""
        homeassistant_service.configure(ha_url, ha_token)
        return homeassistant_service
    return tasmota_service


@router.post("/{plug_id}/control")
async def control_smart_plug(
    plug_id: int,
    control: SmartPlugControl,
    db: AsyncSession = Depends(get_db),
):
    """Manual control: on/off/toggle."""
    result = await db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
    plug = result.scalar_one_or_none()
    if not plug:
        raise HTTPException(404, "Smart plug not found")

    service = await _get_service_for_plug(plug, db)

    if control.action == "on":
        success = await service.turn_on(plug)
        expected_state = "ON"
    elif control.action == "off":
        success = await service.turn_off(plug)
        expected_state = "OFF"
    elif control.action == "toggle":
        success = await service.toggle(plug)
        expected_state = None  # Unknown after toggle
    else:
        raise HTTPException(400, f"Invalid action: {control.action}")

    if not success:
        raise HTTPException(503, "Failed to communicate with device")

    # Update last state and reset auto_off_executed when turning on
    if expected_state:
        plug.last_state = expected_state
        if expected_state == "ON":
            plug.auto_off_executed = False  # Reset flag when manually turning on
        elif expected_state == "OFF" and plug.printer_id:
            # Mark printer offline immediately for faster UI update
            printer_manager.mark_printer_offline(plug.printer_id)
    plug.last_checked = datetime.utcnow()
    await db.commit()

    # MQTT relay - publish smart plug state change
    if expected_state:
        try:
            from backend.app.services.mqtt_relay import mqtt_relay

            # Get printer name if linked
            printer_name = None
            if plug.printer_id:
                result = await db.execute(select(Printer).where(Printer.id == plug.printer_id))
                printer = result.scalar_one_or_none()
                printer_name = printer.name if printer else None

            await mqtt_relay.on_smart_plug_state(
                plug_id=plug.id,
                plug_name=plug.name,
                state="on" if expected_state == "ON" else "off",
                printer_id=plug.printer_id,
                printer_name=printer_name,
            )
        except Exception:
            pass  # Don't fail if MQTT fails

    return {"success": True, "action": control.action}


@router.get("/{plug_id}/status", response_model=SmartPlugStatus)
async def get_plug_status(plug_id: int, db: AsyncSession = Depends(get_db)):
    """Get current plug status from device including energy data."""
    result = await db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
    plug = result.scalar_one_or_none()
    if not plug:
        raise HTTPException(404, "Smart plug not found")

    service = await _get_service_for_plug(plug, db)
    status = await service.get_status(plug)

    # Update last state in database
    if status["reachable"]:
        plug.last_state = status["state"]
        plug.last_checked = datetime.utcnow()
        await db.commit()

    # Fetch energy data if device is reachable
    energy_data = None
    if status["reachable"]:
        energy = await service.get_energy(plug)
        if energy:
            energy_data = SmartPlugEnergy(**energy)

            # Check power alerts
            await check_power_alerts(plug, energy.get("power"), db)

    return SmartPlugStatus(
        state=status["state"],
        reachable=status["reachable"],
        device_name=status.get("device_name"),
        energy=energy_data,
    )


async def check_power_alerts(plug: SmartPlug, current_power: float | None, db: AsyncSession):
    """Check if power crosses alert thresholds and send notifications."""
    if not plug.power_alert_enabled or current_power is None:
        return

    # Cooldown: don't alert more than once per 5 minutes
    cooldown_minutes = 5
    if plug.power_alert_last_triggered:
        time_since_last = datetime.utcnow() - plug.power_alert_last_triggered
        if time_since_last < timedelta(minutes=cooldown_minutes):
            return

    alert_triggered = False
    alert_type = None
    threshold = None

    # Check high threshold
    if plug.power_alert_high is not None and current_power > plug.power_alert_high:
        alert_triggered = True
        alert_type = "high"
        threshold = plug.power_alert_high

    # Check low threshold
    if plug.power_alert_low is not None and current_power < plug.power_alert_low:
        alert_triggered = True
        alert_type = "low"
        threshold = plug.power_alert_low

    if alert_triggered:
        plug.power_alert_last_triggered = datetime.utcnow()
        await db.commit()

        # Send notification
        title = f"Power Alert: {plug.name}"
        if alert_type == "high":
            message = f"Power consumption is {current_power:.1f}W, above threshold of {threshold:.1f}W"
        else:
            message = f"Power consumption is {current_power:.1f}W, below threshold of {threshold:.1f}W"

        logger.info(f"Power alert triggered for {plug.name}: {message}")

        # Use printer_error event type for power alerts (closest match)
        await notification_service.send_notification(
            event_type="printer_error",
            title=title,
            message=message,
            printer_id=plug.printer_id,
            printer_name=plug.name,
            context={
                "error_type": f"Power {alert_type.title()}",
                "error_detail": message,
            },
        )


@router.post("/test-connection")
async def test_connection(data: SmartPlugTestConnection):
    """Test connection to a Tasmota device."""
    result = await tasmota_service.test_connection(
        data.ip_address,
        data.username,
        data.password,
    )

    if not result["success"]:
        raise HTTPException(503, result.get("error", "Failed to connect to device"))

    return {
        "success": True,
        "state": result["state"],
        "device_name": result.get("device_name"),
    }
