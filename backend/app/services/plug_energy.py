"""Reading a smart plug's power and state, whatever kind of plug it is."""

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.smart_plug import SmartPlug
from backend.app.services.homeassistant import homeassistant_service
from backend.app.services.mqtt_relay import mqtt_relay
from backend.app.services.rest_smart_plug import rest_smart_plug_service
from backend.app.services.tasmota import tasmota_service


async def _configured_homeassistant(db: AsyncSession):
    from backend.app.api.routes.settings import get_homeassistant_settings

    ha_settings = await get_homeassistant_settings(db)
    homeassistant_service.configure(ha_settings["ha_url"], ha_settings["ha_token"])
    return homeassistant_service


async def get_plug_energy(plug: SmartPlug, db: AsyncSession) -> dict | None:
    """Get energy from plug regardless of type (Tasmota, Home Assistant, MQTT, or REST).

    For HA plugs, configures the service with current settings from DB.
    For MQTT plugs, returns data from the subscription service.
    For REST plugs, polls the status URL with JSON path extraction.
    """
    if plug.plug_type == "homeassistant":
        return await (await _configured_homeassistant(db)).get_energy(plug)
    elif plug.plug_type == "mqtt":
        # MQTT plugs report "today" energy, not lifetime total
        # For per-print tracking, we use "today" as the counter (resets at midnight)
        mqtt_data = mqtt_relay.smart_plug_service.get_plug_data(plug.id)
        if mqtt_data:
            return {
                "power": mqtt_data.power,
                "today": mqtt_data.energy,
                "total": mqtt_data.energy,  # Use today as total for per-print calculations
            }
        return None
    elif plug.plug_type == "rest":
        return await rest_smart_plug_service.get_energy(plug)
    else:
        return await tasmota_service.get_energy(plug)


async def read_plug(plug: SmartPlug, db: AsyncSession) -> tuple[str | None, float | None]:
    """The plug's switch state ("ON", "OFF" or None if unknown) and power in watts.

    An unreachable plug reads as (None, None). MQTT plugs answer from the last
    message received, without contacting the device.
    """
    if plug.plug_type == "mqtt":
        data = mqtt_relay.smart_plug_service.get_plug_data(plug.id)
        if not data or not mqtt_relay.smart_plug_service.is_reachable(plug.id):
            return None, None
        return data.state, data.power

    if plug.plug_type == "homeassistant":
        service = await _configured_homeassistant(db)
    elif plug.plug_type == "rest":
        service = rest_smart_plug_service
    else:
        service = tasmota_service
    status = await service.get_status(plug)
    if not status.get("reachable"):
        return None, None
    energy = await get_plug_energy(plug, db)
    return status.get("state"), (energy or {}).get("power")
