"""Service for communicating with Home Assistant via REST API."""

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from backend.app.models.smart_plug import SmartPlug

logger = logging.getLogger(__name__)


class HomeAssistantService:
    """Service for controlling Home Assistant entities via REST API."""

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self.base_url: str = ""
        self.token: str = ""

    def configure(self, url: str, token: str):
        """Configure HA connection settings."""
        self.base_url = url.rstrip("/") if url else ""
        self.token = token or ""

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def get_status(self, plug: "SmartPlug") -> dict:
        """Get current state of HA entity.

        Returns dict with:
            - state: "ON" or "OFF" or None if unreachable
            - reachable: bool
            - device_name: str or None
        """
        if not self.base_url or not self.token:
            return {"state": None, "reachable": False, "device_name": None}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}/api/states/{plug.ha_entity_id}",
                    headers=self._headers(),
                )
                response.raise_for_status()
                data = response.json()

                state_value = data.get("state", "").lower()
                # Normalize to ON/OFF
                if state_value == "on":
                    state = "ON"
                elif state_value == "off":
                    state = "OFF"
                else:
                    state = None

                return {
                    "state": state,
                    "reachable": True,
                    "device_name": data.get("attributes", {}).get("friendly_name"),
                }
        except Exception as e:
            logger.warning(f"Failed to get HA entity state for {plug.ha_entity_id}: {e}")
            return {"state": None, "reachable": False, "device_name": None}

    async def turn_on(self, plug: "SmartPlug") -> bool:
        """Turn on HA entity. Returns True if successful."""
        success = await self._call_service(plug, "turn_on")
        if success:
            logger.info(f"Turned ON HA entity '{plug.name}' ({plug.ha_entity_id})")
        return success

    async def turn_off(self, plug: "SmartPlug") -> bool:
        """Turn off HA entity. Returns True if successful."""
        success = await self._call_service(plug, "turn_off")
        if success:
            logger.info(f"Turned OFF HA entity '{plug.name}' ({plug.ha_entity_id})")
        return success

    async def toggle(self, plug: "SmartPlug") -> bool:
        """Toggle HA entity. Returns True if successful."""
        success = await self._call_service(plug, "toggle")
        if success:
            logger.info(f"Toggled HA entity '{plug.name}' ({plug.ha_entity_id})")
        return success

    async def _call_service(self, plug: "SmartPlug", action: str) -> bool:
        """Call HA service on entity."""
        if not self.base_url or not self.token or not plug.ha_entity_id:
            return False

        domain = plug.ha_entity_id.split(".")[0]  # "switch", "light", etc.

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/services/{domain}/{action}",
                    headers=self._headers(),
                    json={"entity_id": plug.ha_entity_id},
                )
                response.raise_for_status()
                return True
        except Exception as e:
            logger.warning(f"Failed to {action} HA entity {plug.ha_entity_id}: {e}")
            return False

    async def get_energy(self, plug: "SmartPlug") -> dict | None:
        """Get energy data from HA sensor entities or switch attributes.

        First tries dedicated sensor entities if configured, then falls back
        to checking the switch entity's attributes.
        Returns dict with energy data or None if not available.
        """
        if not self.base_url or not self.token:
            return None

        power = None
        today = None
        total = None

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # Fetch power from dedicated sensor entity if configured
                if plug.ha_power_entity:
                    power = await self._get_sensor_value(client, plug.ha_power_entity)

                # Fetch today's energy from dedicated sensor entity if configured
                if plug.ha_energy_today_entity:
                    today = await self._get_sensor_value(client, plug.ha_energy_today_entity)

                # Fetch total energy from dedicated sensor entity if configured
                if plug.ha_energy_total_entity:
                    total = await self._get_sensor_value(client, plug.ha_energy_total_entity)

                # Fallback: try switch entity attributes (original behavior)
                if power is None:
                    response = await client.get(
                        f"{self.base_url}/api/states/{plug.ha_entity_id}",
                        headers=self._headers(),
                    )
                    response.raise_for_status()
                    attrs = response.json().get("attributes", {})
                    power = attrs.get("current_power_w") or attrs.get("power")
                    if today is None:
                        today = attrs.get("today_energy_kwh")
                    if total is None:
                        total = attrs.get("total_energy_kwh")

                if power is None:
                    return None

                return {
                    "power": power,
                    "voltage": None,
                    "current": None,
                    "today": today,
                    "total": total,
                    "yesterday": None,
                    "factor": None,
                    "apparent_power": None,
                    "reactive_power": None,
                }
        except Exception:
            return None

    async def _get_sensor_value(self, client: httpx.AsyncClient, entity_id: str) -> float | None:
        """Fetch numeric value from a HA sensor entity."""
        try:
            response = await client.get(
                f"{self.base_url}/api/states/{entity_id}",
                headers=self._headers(),
            )
            response.raise_for_status()
            state = response.json().get("state")
            if state and state not in ("unknown", "unavailable"):
                return float(state)
        except Exception:
            pass
        return None

    async def test_connection(self, url: str, token: str) -> dict:
        """Test connection to Home Assistant.

        Returns dict with:
            - success: bool
            - message: str or None (HA message on success)
            - error: str or None (error message on failure)
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{url.rstrip('/')}/api/",
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                data = response.json()
                return {
                    "success": True,
                    "message": data.get("message", "Connected"),
                    "error": None,
                }
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                return {"success": False, "message": None, "error": "Invalid access token"}
            return {"success": False, "message": None, "error": f"HTTP {e.response.status_code}"}
        except httpx.TimeoutException:
            return {"success": False, "message": None, "error": "Connection timeout"}
        except httpx.ConnectError:
            return {"success": False, "message": None, "error": "Could not connect to Home Assistant"}
        except Exception as e:
            return {"success": False, "message": None, "error": str(e)}

    async def list_entities(self, url: str, token: str, search: str | None = None) -> list[dict]:
        """List available entities from HA.

        By default, returns switch/light/input_boolean domains.
        When search is provided, searches ALL entities by entity_id or friendly_name.

        Returns list of entity dicts with:
            - entity_id: str
            - friendly_name: str
            - state: str
            - domain: str
        """
        # Default domains for smart plug control
        default_domains = {"switch", "light", "input_boolean", "script"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{url.rstrip('/')}/api/states",
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()

                entities = []
                search_lower = search.lower().strip() if search else None

                for entity in response.json():
                    entity_id = entity.get("entity_id", "")
                    domain = entity_id.split(".")[0] if "." in entity_id else ""
                    friendly_name = entity.get("attributes", {}).get("friendly_name", entity_id)

                    # If searching, match against entity_id or friendly_name
                    if search_lower:
                        if search_lower not in entity_id.lower() and search_lower not in friendly_name.lower():
                            continue
                    else:
                        # No search: filter to default domains only
                        if domain not in default_domains:
                            continue

                    entities.append(
                        {
                            "entity_id": entity_id,
                            "friendly_name": friendly_name,
                            "state": entity.get("state"),
                            "domain": domain,
                        }
                    )

                return sorted(entities, key=lambda x: x["friendly_name"].lower())
        except Exception as e:
            logger.warning(f"Failed to list HA entities: {e}")
            return []

    async def list_sensor_entities(self, url: str, token: str) -> list[dict]:
        """List available sensor entities for energy monitoring.

        Returns list of sensor entities with power/energy units.
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{url.rstrip('/')}/api/states",
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()

                # Valid units for energy monitoring sensors (lowercase for case-insensitive matching)
                power_units = {"w", "kw", "mw"}
                energy_units = {"kwh", "wh", "mwh"}
                valid_units = power_units | energy_units

                entities = []
                for entity in response.json():
                    entity_id = entity.get("entity_id", "")
                    domain = entity_id.split(".")[0] if "." in entity_id else ""

                    # Filter to sensor domain only
                    if domain != "sensor":
                        continue

                    attrs = entity.get("attributes", {})
                    unit = attrs.get("unit_of_measurement", "")

                    # Only include sensors with power/energy units (case-insensitive)
                    if unit.lower() in valid_units:
                        entities.append(
                            {
                                "entity_id": entity_id,
                                "friendly_name": attrs.get("friendly_name", entity_id),
                                "state": entity.get("state"),
                                "unit_of_measurement": unit,
                            }
                        )

                return sorted(entities, key=lambda x: x["friendly_name"].lower())
        except Exception as e:
            logger.warning(f"Failed to list HA sensor entities: {e}")
            return []


# Singleton instance
homeassistant_service = HomeAssistantService()
