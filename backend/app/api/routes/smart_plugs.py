"""API routes for smart plug management."""

import logging

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.settings import get_setting
from backend.app.core.auth import (
    RequestPrinterScope,
    RequirePermissionIfAuthEnabled,
    RequirePrinterPermissionIfAuthEnabled,
    probe_permissions_if_auth_enabled,
)
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.core.printer_scope import PrinterScope
from backend.app.core.tasks import spawn_background_task
from backend.app.models.printer import Printer
from backend.app.models.smart_plug import SmartPlug
from backend.app.models.user import User
from backend.app.schemas.smart_plug import (
    HAEntity,
    HASensorEntity,
    HATestConnectionRequest,
    HATestConnectionResponse,
    RESTTestConnectionRequest,
    RESTTestConnectionResponse,
    SmartPlugBase,
    SmartPlugControl,
    SmartPlugCreate,
    SmartPlugEnergy,
    SmartPlugResponse,
    SmartPlugResponseWithSecret,
    SmartPlugStatus,
    SmartPlugTestConnection,
    SmartPlugUpdate,
)
from backend.app.services.discovery import tasmota_scanner
from backend.app.services.homeassistant import homeassistant_service
from backend.app.services.mqtt_relay import mqtt_relay
from backend.app.services.mqtt_smart_plug import subscribe_plug_to_mqtt
from backend.app.services.plug_energy_history import fill_derived_energy
from backend.app.services.printer_manager import printer_manager
from backend.app.services.rest_smart_plug import rest_smart_plug_service
from backend.app.services.secret_fields import (
    REST_PLUG_URLS,
    SECRET_PLACEHOLDER,
    SMART_PLUG_SECRETS,
    SecretConfigError,
    merge_partial,
    reject_placeholder,
    same_value,
)
from backend.app.services.smart_plug_manager import publish_plug_state, run_linked_scripts, smart_plug_manager
from backend.app.services.tasmota import tasmota_service
from backend.app.utils.local_time import utcnow_naive

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/smart-plugs", tags=["smart-plugs"])

# Whether the caller may edit plugs, and so gets the REST URLs and bodies.
CallerEditsPlugs = Depends(probe_permissions_if_auth_enabled(Permission.SMART_PLUGS_UPDATE))


def _serialize_plug(plug: SmartPlug, *, can_edit: bool) -> SmartPlugResponse:
    """The response shape that matches the caller's authority.

    Routes returning it declare no response_model: validating the reader shape
    again would run the REST URL checks on the fields it leaves out.
    """
    if can_edit:
        return SmartPlugResponseWithSecret.model_validate(plug)
    return SmartPlugResponse.model_validate(plug)


@router.get("/", response_model=None)
async def list_smart_plugs(
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_READ),
    printer_scope: PrinterScope = RequestPrinterScope,
    can_edit: bool = CallerEditsPlugs,
):
    """List all smart plugs, minus those powering printers the caller can't see (#1727)."""
    query = select(SmartPlug).order_by(SmartPlug.name)
    if (clause := printer_scope.where(SmartPlug.printer_id)) is not None:
        query = query.where(clause)
    result = await db.execute(query)
    return [_serialize_plug(plug, can_edit=can_edit) for plug in result.scalars().all()]


async def _printer_has_other_tasmota_plug(
    db: AsyncSession, printer_id: int, exclude_plug_id: int | None = None
) -> bool:
    """Whether the printer already has a Tasmota plug (other than this one).

    One per printer: a Tasmota plug is the printer's power device, while HA
    entities may be several (different automations). Tolerates printers that
    already hold more than one from before every path checked this.
    """
    query = select(SmartPlug.id).where(SmartPlug.printer_id == printer_id, SmartPlug.plug_type == "tasmota")
    if exclude_plug_id is not None:
        query = query.where(SmartPlug.id != exclude_plug_id)
    return (await db.execute(query.limit(1))).first() is not None


@router.post("/", response_model=SmartPlugResponseWithSecret)
async def create_smart_plug(
    data: SmartPlugCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_CREATE),
    printer_scope: PrinterScope = RequestPrinterScope,
):
    """Create a new smart plug."""
    try:
        reject_placeholder({field: getattr(data, field) for field in SMART_PLUG_SECRETS})
    except SecretConfigError as exc:
        raise HTTPException(400, str(exc)) from None
    # Validate printer_id if provided (0 included: it names no printer either)
    if data.printer_id is not None:
        printer_scope.ensure(data.printer_id)
        result = await db.execute(select(Printer).where(Printer.id == data.printer_id))
        if not result.scalar_one_or_none():
            raise HTTPException(400, "Printer not found")

        # Check if printer already has a plug assigned
        # Tasmota plugs: only one per printer (physical power device)
        # HA entities: allow multiple per printer (for different automations)
        if data.plug_type == "tasmota" and await _printer_has_other_tasmota_plug(db, data.printer_id):
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
        topics = subscribe_plug_to_mqtt(mqtt_relay.smart_plug_service, plug)
        if topics:
            logger.info("Created MQTT plug '%s' subscribed to %s", plug.name, ", ".join(topics))
    elif plug.plug_type == "homeassistant":
        logger.info("Created Home Assistant plug '%s' (%s)", plug.name, plug.ha_entity_id)
    else:
        logger.info("Created Tasmota plug '%s' at %s", plug.name, plug.ip_address)
    return plug


async def _plug_for_secret_test(
    db: AsyncSession, plug_id: int | None, can_update: bool, printer_scope: PrinterScope
) -> SmartPlug:
    """The saved plug whose stored secret a connection test may use."""
    if plug_id is None:
        raise HTTPException(400, "Enter the secret; nothing is saved for a new plug")
    if not can_update:
        raise HTTPException(403, "Testing a saved plug's secret requires smart_plugs:update")
    plug = await db.get(SmartPlug, plug_id)
    if plug is None or not printer_scope.allows(plug.printer_id):
        raise HTTPException(404, "Smart plug not found")
    return plug


def _is_script_plug(plug: SmartPlug) -> bool:
    """Whether the plug is a Home Assistant script rather than a switchable device."""
    return bool(plug.plug_type == "homeassistant" and plug.ha_entity_id and plug.ha_entity_id.startswith("script."))


def _can_be_switched(plug: SmartPlug) -> bool:
    """Whether ``control_smart_plug`` can actually turn this plug on and off.

    Two kinds cannot, and the card's on/off button is useless on both:

    - A Home Assistant script. It can be run, not switched.
    - An MQTT plug. Bambuddy subscribes to it and never publishes, so the
      control endpoint rejects it outright as monitor-only -- and an MQTT plug
      is exactly the kind that reports watts, so without this it would win the
      power tiebreak below and take the row off a plug that can be switched.
    """
    return not _is_script_plug(plug) and plug.plug_type != "mqtt"


def _reports_power(plug: SmartPlug) -> bool:
    """Whether the plug is configured with somewhere to read watts from (#2830).

    Read from the configuration rather than measured: this runs on every printer
    card render, and probing each plug would mean an HTTP round trip per plug.
    So it is approximate in both directions -- an HA plug with no dedicated power
    sensor may still report watts from the switch entity's own
    ``current_power_w`` attribute, and a Tasmota device without energy metering
    is counted here as if it had it. Only a live read could tell, and this is
    used solely to break a tie between plugs that are otherwise equally
    eligible, so neither miss can decide anything on its own.
    """
    if plug.plug_type == "homeassistant":
        return bool(plug.ha_power_entity)
    if plug.plug_type == "mqtt":
        return bool(plug.mqtt_power_topic or plug.mqtt_topic)
    if plug.plug_type == "rest":
        return bool(plug.rest_power_path)
    return True  # Tasmota, whose firmware reports power when the hardware has it


def _main_plug_rank(plug: SmartPlug) -> tuple:
    """Sort key for choosing the printer's main power plug, best first (#2830).

    A printer's plugs are not interchangeable. The card's Power row carries the
    power on/off and auto-off-after-print controls, so it has to land on the plug
    that actually feeds the printer -- pointing those at an exhaust fan is the
    same harm #2629 fixed for the scheduler's power-on. Ordered:

    1. It can be switched at all -- see ``_can_be_switched``. The row's buttons
       are the point of it.
    2. ``controls_printer_power`` -- the flag that says this plug feeds the
       printer, as opposed to an accessory that merely follows the print cycle.
    3. ``enabled`` -- a disabled plug ignores automation, so its auto-off toggle
       would sit there doing nothing.
    4. ``show_on_printer_card`` -- ranked, not filtered: excluding hidden plugs
       outright would strip the Power row, and with it the on/off button, from a
       printer whose only plug has the flag off. It sorts below the power flag
       because a display preference must not hand power control to an accessory.
    5. Reports power, so the row shows watts rather than "--" where there is a
       choice.
    6. Lowest id, so the answer never depends on row order. The query had no
       ORDER BY at all, which on Postgres means a plain UPDATE can move a row and
       silently swap which plug the card calls the printer's power.
    """
    return (
        not _can_be_switched(plug),
        not plug.controls_printer_power,
        not plug.enabled,
        not plug.show_on_printer_card,
        not _reports_power(plug),
        plug.id,
    )


def _pick_main_plug(plugs: list[SmartPlug]) -> SmartPlug | None:
    """The plug the printer card shows as its power, or None if there are none."""
    return min(plugs, key=_main_plug_rank, default=None)


async def _plugs_for_printer(db: AsyncSession, printer_id: int) -> list[SmartPlug]:
    result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id).order_by(SmartPlug.id))
    return list(result.scalars().all())


class PrinterCardPlugs(BaseModel):
    """A printer card's power plug and action buttons, in one answer."""

    # As GET /by-printer/{printer_id}: the editor's shape for a caller who may edit plugs
    plug: SmartPlugResponse | None
    # As GET /by-printer/{printer_id}/scripts
    scripts: list[SmartPlugResponse]


@router.get("/by-printer/{printer_id}", response_model=None)
async def get_smart_plug_by_printer(
    printer_id: int,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePrinterPermissionIfAuthEnabled(Permission.SMART_PLUGS_READ),
    can_edit: bool = CallerEditsPlugs,
):
    """Get the main smart plug assigned to a printer.

    When several plugs are assigned -- a printer outlet, an enclosure fan, a
    script -- returns the one that best fits the card's power controls. See
    ``_main_plug_rank`` for the order and why.
    """
    plug = _pick_main_plug(await _plugs_for_printer(db, printer_id))
    return _serialize_plug(plug, can_edit=can_edit) if plug else None


@router.get("/by-printer/{printer_id}/scripts", response_model=list[SmartPlugResponse])
async def get_script_plugs_by_printer(
    printer_id: int,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePrinterPermissionIfAuthEnabled(Permission.SMART_PLUGS_READ),
):
    """Get all HA entities assigned to a printer for display on printer card.

    Returns HA entities (switches, scripts, lights, etc.) for the printer that have
    show_on_printer_card enabled.
    Used to display action buttons alongside the main power plug.

    A switchable main plug is left out: it is rendered directly above this row
    with its own on/off button, so listing it here draws the same entity twice
    (#2830). A script is not, because a printer whose only entities are scripts
    falls back to showing one of them in the power row -- taking it out of this
    row too would cost the one-click run it has always had there.
    """
    return _card_script_plugs(await _plugs_for_printer(db, printer_id))


def _card_script_plugs(plugs: list[SmartPlug]) -> list[SmartPlug]:
    """The action buttons of one printer's card; see ``get_script_plugs_by_printer``."""
    main_plug = _pick_main_plug(plugs)
    duplicate_of_power_row = main_plug.id if main_plug and not _is_script_plug(main_plug) else None

    # Filter to HA entities with show_on_printer_card enabled
    ha_entities = [
        plug
        for plug in plugs
        if plug.plug_type == "homeassistant"
        and plug.ha_entity_id
        and plug.show_on_printer_card
        and plug.id != duplicate_of_power_row
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
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_CREATE),
):
    """Start an IP range scan for Tasmota devices.

    Auto-detects local network if no IP range provided.
    """

    # Auto-detect network
    from_ip, to_ip = get_local_network_range()
    timeout = request.timeout if request else 1.0

    # Start scan in background
    spawn_background_task(
        tasmota_scanner.scan_range(from_ip, to_ip, timeout),
        name="tasmota-scan",
    )

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
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_CREATE),
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
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_CONTROL),
    can_update_settings: bool = Depends(probe_permissions_if_auth_enabled(Permission.SETTINGS_UPDATE)),
):
    """Test connection to Home Assistant.

    The placeholder token tests the stored token, against the stored URL only.
    """
    token = request.token
    if token == SECRET_PLACEHOLDER:
        if not can_update_settings:
            raise HTTPException(403, "Testing the saved token requires settings:update")
        from backend.app.api.routes.settings import get_homeassistant_settings

        stored = await get_homeassistant_settings(db)
        if not stored.get("ha_token"):
            raise HTTPException(400, "Enter the token; none is saved")
        if not same_value(request.url, stored.get("ha_url")):
            raise HTTPException(400, "Enter the token again: the URL changed")
        token = stored["ha_token"]
    result = await homeassistant_service.test_connection(request.url, token)
    return HATestConnectionResponse(**result)


@router.post("/rest/test-connection", response_model=RESTTestConnectionResponse)
async def test_rest_connection(
    request: RESTTestConnectionRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_CONTROL),
    can_update: bool = Depends(probe_permissions_if_auth_enabled(Permission.SMART_PLUGS_UPDATE)),
    printer_scope: PrinterScope = RequestPrinterScope,
):
    """Test connection to a REST/HTTP endpoint.

    With plug_id, the placeholder headers are the plug's stored headers, for
    the plug's own URLs only.
    """
    headers = request.headers
    if headers == SECRET_PLACEHOLDER:
        plug = await _plug_for_secret_test(db, request.plug_id, can_update, printer_scope)
        if not plug.rest_headers:
            raise HTTPException(400, "Enter the headers; none are saved")
        if not any(same_value(request.url, getattr(plug, field)) for field in REST_PLUG_URLS):
            raise HTTPException(400, "Enter the headers again: the URL is not one of the plug's")
        headers = plug.rest_headers
    result = await rest_smart_plug_service.test_connection(request.url, request.method, headers)
    return RESTTestConnectionResponse(**result)


@router.get("/ha/entities", response_model=list[HAEntity])
async def list_ha_entities(
    db: AsyncSession = Depends(get_db, scope="function"),
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
    db: AsyncSession = Depends(get_db, scope="function"),
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


@router.get("/{plug_id}", response_model=None)
async def get_smart_plug(
    plug_id: int,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_READ),
    printer_scope: PrinterScope = RequestPrinterScope,
    can_edit: bool = CallerEditsPlugs,
):
    """Get a specific smart plug."""
    result = await db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
    plug = result.scalar_one_or_none()
    if not plug or not printer_scope.allows(plug.printer_id):
        raise HTTPException(404, "Smart plug not found")
    return _serialize_plug(plug, can_edit=can_edit)


@router.patch("/{plug_id}", response_model=SmartPlugResponseWithSecret)
async def update_smart_plug(
    plug_id: int,
    data: SmartPlugUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_UPDATE),
    printer_scope: PrinterScope = RequestPrinterScope,
):
    """Update a smart plug."""
    result = await db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
    plug = result.scalar_one_or_none()
    if not plug or not printer_scope.allows(plug.printer_id):
        raise HTTPException(404, "Smart plug not found")

    update_data = data.model_dump(exclude_unset=True)

    # Write-only secrets: the placeholder keeps the stored value, and a kept
    # secret does not follow a changed address or plug type.
    stored = {
        field: getattr(plug, field)
        for field in {*SMART_PLUG_SECRETS, *(f for fs in SMART_PLUG_SECRETS.values() for f in fs)}
    }
    try:
        update_data = merge_partial(stored, update_data, SMART_PLUG_SECRETS)
    except SecretConfigError as exc:
        raise HTTPException(400, str(exc)) from None

    # Validate new printer_id if being changed
    if "printer_id" in update_data and update_data["printer_id"] is not None:
        new_printer_id = update_data["printer_id"]
        printer_scope.ensure(new_printer_id)

        # Check printer exists
        result = await db.execute(select(Printer).where(Printer.id == new_printer_id))
        if not result.scalar_one_or_none():
            raise HTTPException(400, "Printer not found")

    # One Tasmota plug per printer (it is the printer's power device), checked
    # when this edit makes the plug a Tasmota plug on a printer, by its type or
    # its printer: a type change alone cannot add a second, and an edit that
    # changes neither is not refused for plugs stored earlier.
    merged_type = update_data.get("plug_type", plug.plug_type)
    merged_printer_id = update_data.get("printer_id", plug.printer_id)
    becomes_tasmota_here = merged_type != plug.plug_type or merged_printer_id != plug.printer_id
    if merged_type == "tasmota" and merged_printer_id and becomes_tasmota_here:
        if await _printer_has_other_tasmota_plug(db, merged_printer_id, exclude_plug_id=plug_id):
            raise HTTPException(400, "This printer already has a Tasmota plug assigned")

    # The stored plug with the change applied must pass what create checks:
    # a plug that fails it can no longer be read back (list, detail and this
    # response all fail). The error names the field to fix, sent or stored,
    # and carries no values: the stored plug holds write-only credentials.
    merged = {field: update_data.get(field, getattr(plug, field, None)) for field in SmartPlugBase.model_fields}
    try:
        SmartPlugBase.model_validate(merged)
    except ValidationError as exc:
        raise RequestValidationError(
            [
                {"type": error["type"], "loc": ("body", *error["loc"]), "msg": error["msg"]}
                for error in exc.errors(include_url=False, include_input=False, include_context=False)
            ]
        ) from None

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

            # Subscribe via the shared helper (matches startup restore and
            # create route) — keeps all three paths in lock-step.
            subscribe_plug_to_mqtt(mqtt_relay.smart_plug_service, plug)

    logger.info("Updated smart plug '%s'", plug.name)
    return plug


async def delete_plug(db: AsyncSession, plug: SmartPlug) -> None:
    """Delete a smart plug (the caller commits). An MQTT plug stops listening first."""
    smart_plug_manager.forget_plug(plug.id)
    if plug.plug_type == "mqtt":
        mqtt_relay.smart_plug_service.unsubscribe(plug.id)
    await db.delete(plug)


@router.delete("/{plug_id}")
async def delete_smart_plug(
    plug_id: int,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_DELETE),
    printer_scope: PrinterScope = RequestPrinterScope,
):
    """Delete a smart plug."""
    result = await db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
    plug = result.scalar_one_or_none()
    if not plug or not printer_scope.allows(plug.printer_id):
        raise HTTPException(404, "Smart plug not found")

    plug_name = plug.name
    await delete_plug(db, plug)
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
    if plug.plug_type == "rest":
        return rest_smart_plug_service
    return tasmota_service


@router.post("/{plug_id}/control")
async def control_smart_plug(
    plug_id: int,
    control: SmartPlugControl,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_CONTROL),
    printer_scope: PrinterScope = RequestPrinterScope,
):
    """Manual control: on/off/toggle."""
    result = await db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
    plug = result.scalar_one_or_none()
    if not plug or not printer_scope.allows(plug.printer_id):
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
        elif expected_state == "OFF" and plug.printer_id and plug.controls_printer_power:
            # Mark printer offline immediately for faster UI update. Skipped for
            # accessory plugs, which are linked to a printer but don't feed it (#2629).
            printer_manager.mark_printer_offline(plug.printer_id)
    plug.last_checked = utcnow_naive()
    await db.commit()

    # Trigger associated scripts if this is a main (non-script) plug
    is_main_plug = not (
        plug.plug_type == "homeassistant" and plug.ha_entity_id and plug.ha_entity_id.startswith("script.")
    )
    if is_main_plug and plug.printer_id and expected_state:
        await run_linked_scripts(plug.printer_id, expected_state, db)

    # MQTT relay - publish smart plug state change. A toggle doesn't say which
    # way it went, so read the state it ended in.
    published_state = expected_state
    if published_state is None:
        try:
            published_state = (await service.get_status(plug)).get("state")
        except Exception:
            published_state = None
    if published_state in ("ON", "OFF"):
        await publish_plug_state(
            plug.id, plug.name, plug.printer_id, on=published_state == "ON", ha_entity_id=plug.ha_entity_id
        )

    return {"success": True, "action": control.action}


@router.get("/{plug_id}/status", response_model=SmartPlugStatus)
async def get_plug_status(
    plug_id: int,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_READ),
    printer_scope: PrinterScope = RequestPrinterScope,
):
    """Get current plug status from device including energy data."""
    result = await db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
    plug = result.scalar_one_or_none()
    if not plug or not printer_scope.allows(plug.printer_id):
        raise HTTPException(404, "Smart plug not found")

    # Handle MQTT plugs - get data from subscription service
    if plug.plug_type == "mqtt":
        data = mqtt_relay.smart_plug_service.get_plug_data(plug_id)
        is_reachable = mqtt_relay.smart_plug_service.is_reachable(plug_id)

        if data:
            # Update last state in database
            if is_reachable and data.state:
                plug.last_state = data.state
                plug.last_checked = utcnow_naive()
                await db.commit()

            energy_data = None
            if data.power is not None or data.energy is not None:
                energy_data = SmartPlugEnergy(
                    power=data.power,
                    today=data.energy,
                )

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
        plug.last_checked = utcnow_naive()
        await db.commit()

    # Fetch energy data if device is reachable
    energy_data = None
    if status["reachable"]:
        energy = await service.get_energy(plug)
        if energy:
            # Most plugs report only a lifetime counter — a Shelly has no notion
            # of "today" at all, and Home Assistant never reports "yesterday".
            # Fill those in from the hourly snapshots (#2539). Tasmota, which
            # knows its own daily figures, is left alone.
            energy = await fill_derived_energy(db, plug.id, energy)
            energy_data = SmartPlugEnergy(**energy)
            # Power alerts are checked in the background (services/power_alerts.py),
            # not here: this is only read while someone has a printer page open.

    return SmartPlugStatus(
        state=status["state"],
        reachable=status["reachable"],
        device_name=status.get("device_name"),
        energy=energy_data,
    )


@router.post("/test-connection")
async def test_connection(
    data: SmartPlugTestConnection,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_CONTROL),
    can_update: bool = Depends(probe_permissions_if_auth_enabled(Permission.SMART_PLUGS_UPDATE)),
    printer_scope: PrinterScope = RequestPrinterScope,
):
    """Test connection to a Tasmota device.

    With plug_id, the placeholder password is the plug's stored password, for
    the plug's own address only.
    """
    password = data.password
    if password == SECRET_PLACEHOLDER:
        plug = await _plug_for_secret_test(db, data.plug_id, can_update, printer_scope)
        if not plug.password:
            raise HTTPException(400, "Enter the password; none is saved")
        if not same_value(data.ip_address, plug.ip_address):
            raise HTTPException(400, "Enter the password again: the address changed")
        password = plug.password
    result = await tasmota_service.test_connection(
        data.ip_address,
        data.username,
        password,
    )

    if not result["success"]:
        raise HTTPException(503, result.get("error", "Failed to connect to device"))

    return {
        "success": True,
        "state": result["state"],
        "device_name": result.get("device_name"),
    }
