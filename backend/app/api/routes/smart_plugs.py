"""API routes for smart plug management."""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.settings import get_setting
from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.printer import Printer
from backend.app.models.smart_plug import SmartPlug
from backend.app.models.user import User
from backend.app.schemas.smart_plug import (
    HAEntity,
    HASensorEntity,
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
from backend.app.services.mqtt_relay import mqtt_relay
from backend.app.services.notification_service import notification_service
from backend.app.services.printer_manager import printer_manager
from backend.app.services.tasmota import tasmota_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/smart-plugs", tags=["smart-plugs"])


@router.get("/", response_model=list[SmartPlugResponse])
async def list_smart_plugs(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_READ),
):
    """List all smart plugs."""
    result = await db.execute(select(SmartPlug).order_by(SmartPlug.name))
    return list(result.scalars().all())


@router.post("/", response_model=SmartPlugResponse)
async def create_smart_plug(
    data: SmartPlugCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_CREATE),
):
    """Create a new smart plug."""
    # Validate printer_id if provided
    if data.printer_id:
        result = await db.execute(select(Printer).where(Printer.id == data.printer_id))
        if not result.scalar_one_or_none():
            raise HTTPException(400, "Printer not found")

        # Check if printer already has a plug assigned
        # Tasmota plugs: only one per printer (physical power device)
        # HA entities: allow multiple per printer (for different automations)
        if data.plug_type == "tasmota":
            result = await db.execute(
                select(SmartPlug).where(
                    SmartPlug.printer_id == data.printer_id,
                    SmartPlug.plug_type == "tasmota",
                )
            )
            if result.scalar_one_or_none():
                raise HTTPException(400, "This printer already has a Tasmota plug assigned")

    # For MQTT plugs, ensure MQTT broker is configured and service is connected
    if data.plug_type == "mqtt":
        # Try to configure the smart plug service if not already configured
        if not mqtt_relay.smart_plug_service.is_configured():
            # Get MQTT broker settings from database
            mqtt_broker = await get_setting(db, "mqtt_broker") or ""
            if not mqtt_broker:
                raise HTTPException(
                    400,
                    "MQTT broker not configured. Please set MQTT broker address in Settings → Network → MQTT Publishing.",
                )

            # Configure the smart plug service with broker settings
            mqtt_settings = {
                "mqtt_enabled": True,  # Enable for smart plug subscription
                "mqtt_broker": mqtt_broker,
                "mqtt_port": int(await get_setting(db, "mqtt_port") or "1883"),
                "mqtt_username": await get_setting(db, "mqtt_username") or "",
                "mqtt_password": await get_setting(db, "mqtt_password") or "",
                "mqtt_use_tls": (await get_setting(db, "mqtt_use_tls") or "false") == "true",
            }
            await mqtt_relay.smart_plug_service.configure(mqtt_settings)

            # Check if connection succeeded
            if not mqtt_relay.smart_plug_service.is_configured():
                raise HTTPException(
                    400,
                    f"Failed to connect to MQTT broker at {mqtt_broker}. Please check your MQTT settings.",
                )

    plug_data = data.model_dump()

    # For HA entities, default auto_on and auto_off to False
    # (they're for automations, not power control like Tasmota plugs)
    if data.plug_type == "homeassistant":
        plug_data["auto_on"] = False
        plug_data["auto_off"] = False

    plug = SmartPlug(**plug_data)
    db.add(plug)
    await db.commit()
    await db.refresh(plug)

    # Subscribe MQTT plugs to their topics
    if plug.plug_type == "mqtt":
        # Determine effective topics (new fields take priority, fall back to legacy)
        power_topic = plug.mqtt_power_topic or plug.mqtt_topic
        energy_topic = plug.mqtt_energy_topic
        state_topic = plug.mqtt_state_topic

        # Only subscribe if at least one topic is configured
        if power_topic or energy_topic or state_topic:
            mqtt_relay.smart_plug_service.subscribe(
                plug_id=plug.id,
                # Power source (path is optional)
                power_topic=power_topic,
                power_path=plug.mqtt_power_path,
                power_multiplier=plug.mqtt_power_multiplier or plug.mqtt_multiplier or 1.0,
                # Energy source (path is optional)
                energy_topic=energy_topic,
                energy_path=plug.mqtt_energy_path,
                energy_multiplier=plug.mqtt_energy_multiplier or plug.mqtt_multiplier or 1.0,
                # State source (path is optional)
                state_topic=state_topic,
                state_path=plug.mqtt_state_path,
                state_on_value=plug.mqtt_state_on_value,
            )
            topics = [t for t in [power_topic, energy_topic, state_topic] if t]
            logger.info("Created MQTT plug '%s' subscribed to %s", plug.name, ", ".join(set(topics)))
    elif plug.plug_type == "homeassistant":
        logger.info("Created Home Assistant plug '%s' (%s)", plug.name, plug.ha_entity_id)
    else:
        logger.info("Created Tasmota plug '%s' at %s", plug.name, plug.ip_address)
    return plug


@router.get("/by-printer/{printer_id}", response_model=SmartPlugResponse | None)
async def get_smart_plug_by_printer(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_READ),
):
    """Get the main smart plug assigned to a printer.

    When multiple plugs are assigned (e.g., a regular plug + script),
    returns the main (non-script) plug for power control.
    """
    result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
    plugs = result.scalars().all()

    if not plugs:
        return None

    # If multiple plugs, prefer the non-script one (main power plug)
    for plug in plugs:
        is_script = plug.plug_type == "homeassistant" and plug.ha_entity_id and plug.ha_entity_id.startswith("script.")
        if not is_script:
            return plug

    # All are scripts, return the first one
    return plugs[0]


@router.get("/by-printer/{printer_id}/scripts", response_model=list[SmartPlugResponse])
async def get_script_plugs_by_printer(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_READ),
):
    """Get all HA entities assigned to a printer for display on printer card.

    Returns HA entities (switches, scripts, lights, etc.) for the printer that have
    show_on_printer_card enabled.
    Used to display action buttons alongside the main power plug.
    """
    result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
    plugs = result.scalars().all()

    # Filter to HA entities with show_on_printer_card enabled
    ha_entities = [
        plug for plug in plugs if plug.plug_type == "homeassistant" and plug.ha_entity_id and plug.show_on_printer_card
    ]
    return ha_entities


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

        logger.info("Auto-detected network: %s - %s (local IP: %s)", from_ip, to_ip, local_ip)
        return from_ip, to_ip

    except OSError as e:
        logger.error("Failed to detect local network: %s", e)
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
async def start_tasmota_scan(
    request: TasmotaScanRequest | None = Body(default=None),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_READ),
):
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
async def get_tasmota_scan_status(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_READ),
):
    """Get the current Tasmota scan status."""
    scanned, total = tasmota_scanner.progress
    return TasmotaScanStatus(
        running=tasmota_scanner.is_running,
        scanned=scanned,
        total=total,
    )


@router.post("/discover/stop", response_model=TasmotaScanStatus)
async def stop_tasmota_scan(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_READ),
):
    """Stop the current Tasmota scan."""
    tasmota_scanner.stop()
    scanned, total = tasmota_scanner.progress
    return TasmotaScanStatus(
        running=tasmota_scanner.is_running,
        scanned=scanned,
        total=total,
    )


@router.get("/discover/devices", response_model=list[DiscoveredTasmotaDevice])
async def get_discovered_tasmota_devices(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_READ),
):
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
async def test_ha_connection(
    request: HATestConnectionRequest,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_CONTROL),
):
    """Test connection to Home Assistant."""
    result = await homeassistant_service.test_connection(request.url, request.token)
    return HATestConnectionResponse(**result)


@router.get("/ha/entities", response_model=list[HAEntity])
async def list_ha_entities(
    db: AsyncSession = Depends(get_db),
    search: str | None = None,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_READ),
):
    """List available Home Assistant entities.

    By default, returns switch/light/input_boolean entities.
    When search is provided, searches ALL entities by entity_id or friendly_name.

    Requires HA connection settings to be configured in Settings.
    """
    from backend.app.api.routes.settings import get_homeassistant_settings

    ha_settings = await get_homeassistant_settings(db)
    ha_url = ha_settings["ha_url"]
    ha_token = ha_settings["ha_token"]

    if not ha_url or not ha_token:
        raise HTTPException(
            400, "Home Assistant not configured. Please set HA URL and token in Settings → Network → Home Assistant."
        )

    entities = await homeassistant_service.list_entities(ha_url, ha_token, search)
    return [HAEntity(**e) for e in entities]


@router.get("/ha/sensors", response_model=list[HASensorEntity])
async def list_ha_sensor_entities(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_READ),
):
    """List available Home Assistant sensor entities for energy monitoring.

    Returns sensors with power/energy units (W, kW, kWh, Wh).
    Requires HA connection settings to be configured in Settings.
    """
    from backend.app.api.routes.settings import get_homeassistant_settings

    ha_settings = await get_homeassistant_settings(db)
    ha_url = ha_settings["ha_url"]
    ha_token = ha_settings["ha_token"]

    if not ha_url or not ha_token:
        raise HTTPException(
            400, "Home Assistant not configured. Please set HA URL and token in Settings → Network → Home Assistant."
        )

    sensors = await homeassistant_service.list_sensor_entities(ha_url, ha_token)
    return [HASensorEntity(**s) for s in sensors]


@router.get("/{plug_id}", response_model=SmartPlugResponse)
async def get_smart_plug(
    plug_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_READ),
):
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
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_UPDATE),
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

        # Check if that printer already has a different Tasmota plug assigned
        # Tasmota plugs: only one per printer (physical power device)
        # HA entities: allow multiple per printer (for different automations)
        new_plug_type = update_data.get("plug_type", plug.plug_type)
        if new_plug_type == "tasmota":
            result = await db.execute(
                select(SmartPlug).where(
                    SmartPlug.printer_id == new_printer_id,
                    SmartPlug.id != plug_id,
                    SmartPlug.plug_type == "tasmota",
                )
            )
            if result.scalar_one_or_none():
                raise HTTPException(400, "This printer already has a Tasmota plug assigned")

    # Track old MQTT settings for comparison
    old_plug_type = plug.plug_type
    old_mqtt_config = {
        "power_topic": plug.mqtt_power_topic or plug.mqtt_topic,
        "power_path": plug.mqtt_power_path,
        "power_multiplier": plug.mqtt_power_multiplier,
        "energy_topic": plug.mqtt_energy_topic or plug.mqtt_topic,
        "energy_path": plug.mqtt_energy_path,
        "energy_multiplier": plug.mqtt_energy_multiplier,
        "state_topic": plug.mqtt_state_topic or plug.mqtt_topic,
        "state_path": plug.mqtt_state_path,
        "state_on_value": plug.mqtt_state_on_value,
    }

    for field, value in update_data.items():
        setattr(plug, field, value)

    await db.commit()
    await db.refresh(plug)

    # Handle MQTT subscription changes
    if old_plug_type == "mqtt" and plug.plug_type != "mqtt":
        # Changed away from MQTT - unsubscribe
        mqtt_relay.smart_plug_service.unsubscribe(plug.id)
    elif plug.plug_type == "mqtt":
        # Check if any MQTT config changed
        new_mqtt_config = {
            "power_topic": plug.mqtt_power_topic or plug.mqtt_topic,
            "power_path": plug.mqtt_power_path,
            "power_multiplier": plug.mqtt_power_multiplier,
            "energy_topic": plug.mqtt_energy_topic or plug.mqtt_topic,
            "energy_path": plug.mqtt_energy_path,
            "energy_multiplier": plug.mqtt_energy_multiplier,
            "state_topic": plug.mqtt_state_topic or plug.mqtt_topic,
            "state_path": plug.mqtt_state_path,
            "state_on_value": plug.mqtt_state_on_value,
        }

        mqtt_changed = old_plug_type != "mqtt" or old_mqtt_config != new_mqtt_config

        if mqtt_changed:
            # Unsubscribe from old topics first
            if old_plug_type == "mqtt":
                mqtt_relay.smart_plug_service.unsubscribe(plug.id)

            # Subscribe to new topics
            power_topic = plug.mqtt_power_topic or plug.mqtt_topic
            energy_topic = plug.mqtt_energy_topic
            state_topic = plug.mqtt_state_topic

            # Only subscribe if at least one topic is configured
            if power_topic or energy_topic or state_topic:
                mqtt_relay.smart_plug_service.subscribe(
                    plug_id=plug.id,
                    # Power source (path is optional)
                    power_topic=power_topic,
                    power_path=plug.mqtt_power_path,
                    power_multiplier=plug.mqtt_power_multiplier or plug.mqtt_multiplier or 1.0,
                    # Energy source (path is optional)
                    energy_topic=energy_topic,
                    energy_path=plug.mqtt_energy_path,
                    energy_multiplier=plug.mqtt_energy_multiplier or plug.mqtt_multiplier or 1.0,
                    # State source (path is optional)
                    state_topic=state_topic,
                    state_path=plug.mqtt_state_path,
                    state_on_value=plug.mqtt_state_on_value,
                )

    logger.info("Updated smart plug '%s'", plug.name)
    return plug


@router.delete("/{plug_id}")
async def delete_smart_plug(
    plug_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_DELETE),
):
    """Delete a smart plug."""
    result = await db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
    plug = result.scalar_one_or_none()
    if not plug:
        raise HTTPException(404, "Smart plug not found")

    plug_name = plug.name
    plug_type = plug.plug_type

    # Unsubscribe MQTT plug before deletion
    if plug_type == "mqtt":
        mqtt_relay.smart_plug_service.unsubscribe(plug_id)

    await db.delete(plug)
    await db.commit()

    logger.info("Deleted smart plug '%s'", plug_name)
    return {"message": "Smart plug deleted"}


async def _get_service_for_plug(plug: SmartPlug, db: AsyncSession):
    """Get the appropriate service for the plug type.

    For HA plugs, configures the service with current settings from DB.
    """
    if plug.plug_type == "homeassistant":
        # Configure HA service with current settings
        from backend.app.api.routes.settings import get_homeassistant_settings

        ha_settings = await get_homeassistant_settings(db)
        homeassistant_service.configure(ha_settings["ha_url"], ha_settings["ha_token"])
        return homeassistant_service
    return tasmota_service


@router.post("/{plug_id}/control")
async def control_smart_plug(
    plug_id: int,
    control: SmartPlugControl,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_CONTROL),
):
    """Manual control: on/off/toggle."""
    result = await db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
    plug = result.scalar_one_or_none()
    if not plug:
        raise HTTPException(404, "Smart plug not found")

    # MQTT plugs are monitor-only - cannot control them
    if plug.plug_type == "mqtt":
        raise HTTPException(
            400,
            "MQTT plugs are monitor-only. Use your MQTT broker or home automation system to control them.",
        )

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

    # Trigger associated scripts if this is a main (non-script) plug
    is_main_plug = not (
        plug.plug_type == "homeassistant" and plug.ha_entity_id and plug.ha_entity_id.startswith("script.")
    )
    if is_main_plug and plug.printer_id and expected_state:
        await trigger_associated_scripts(plug.printer_id, expected_state, db)

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


async def trigger_associated_scripts(printer_id: int, plug_state: str, db: AsyncSession):
    """Trigger scripts linked to a printer based on main plug state change.

    When the main plug turns ON, triggers scripts with auto_on=True.
    When the main plug turns OFF, triggers scripts with auto_off=True.
    """
    result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
    plugs = result.scalars().all()

    # Find scripts that should be triggered
    for plug in plugs:
        is_script = plug.plug_type == "homeassistant" and plug.ha_entity_id and plug.ha_entity_id.startswith("script.")
        if not is_script:
            continue

        should_trigger = False
        if plug_state == "ON" and plug.auto_on:
            should_trigger = True
            logger.info("Auto-triggering script '%s' on printer power-on", plug.name)
        elif plug_state == "OFF" and plug.auto_off:
            should_trigger = True
            logger.info("Auto-triggering script '%s' on printer power-off", plug.name)

        if should_trigger:
            try:
                service = await _get_service_for_plug(plug, db)
                await service.turn_on(plug)  # Scripts are triggered by calling turn_on
            except Exception as e:
                logger.error("Failed to trigger script '%s': %s", plug.name, e)


@router.get("/{plug_id}/status", response_model=SmartPlugStatus)
async def get_plug_status(
    plug_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_READ),
):
    """Get current plug status from device including energy data."""
    result = await db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
    plug = result.scalar_one_or_none()
    if not plug:
        raise HTTPException(404, "Smart plug not found")

    # Handle MQTT plugs - get data from subscription service
    if plug.plug_type == "mqtt":
        data = mqtt_relay.smart_plug_service.get_plug_data(plug_id)
        is_reachable = mqtt_relay.smart_plug_service.is_reachable(plug_id)

        if data:
            # Update last state in database
            if is_reachable and data.state:
                plug.last_state = data.state
                plug.last_checked = datetime.utcnow()
                await db.commit()

            energy_data = None
            if data.power is not None or data.energy is not None:
                energy_data = SmartPlugEnergy(
                    power=data.power,
                    today=data.energy,
                )
                # Check power alerts
                if data.power is not None:
                    await check_power_alerts(plug, data.power, db)

            return SmartPlugStatus(
                state=data.state,
                reachable=is_reachable,
                device_name=None,
                energy=energy_data,
            )

        # No data received yet
        return SmartPlugStatus(
            state=None,
            reachable=False,
            device_name=None,
            energy=None,
        )

    # Handle Tasmota/HomeAssistant plugs
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

        logger.info("Power alert triggered for %s: %s", plug.name, message)

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
async def test_connection(
    data: SmartPlugTestConnection,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_CONTROL),
):
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
