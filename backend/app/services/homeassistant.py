"""Service for communicating with Home Assistant via REST API."""

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

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
        # Enclosure sensor cache: printer_id -> {temp, humidity, temp_unit, humidity_unit, fan_on}
        self._enclosure_cache: dict[int, dict] = {}
        # Fan state cache for transition detection: printer_id -> True/False/None
        self._fan_state_cache: dict[int, bool | None] = {}

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
            logger.warning("Failed to get HA entity state for %s: %s", plug.ha_entity_id, e)
            return {"state": None, "reachable": False, "device_name": None}

    async def turn_on(self, plug: "SmartPlug") -> bool:
        """Turn on HA entity. Returns True if successful."""
        success = await self._call_service(plug, "turn_on")
        if success:
            logger.info("Turned ON HA entity '%s' (%s)", plug.name, plug.ha_entity_id)
        return success

    async def turn_off(self, plug: "SmartPlug") -> bool:
        """Turn off HA entity. Returns True if successful."""
        success = await self._call_service(plug, "turn_off")
        if success:
            logger.info("Turned OFF HA entity '%s' (%s)", plug.name, plug.ha_entity_id)
        return success

    async def toggle(self, plug: "SmartPlug") -> bool:
        """Toggle HA entity. Returns True if successful."""
        success = await self._call_service(plug, "toggle")
        if success:
            logger.info("Toggled HA entity '%s' (%s)", plug.name, plug.ha_entity_id)
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
            logger.warning("Failed to %s HA entity %s: %s", action, plug.ha_entity_id, e)
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
        except Exception as e:
            logger.debug("Failed to get HA energy data: %s", e)
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
            pass  # Sensor read is best-effort; caller handles None
        return None

    @staticmethod
    def _validate_url(url: str) -> str | None:
        """Validate HA URL scheme and block dangerous destinations."""
        try:
            parsed = urlparse(url)
        except ValueError:
            return None
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        blocked = ("169.254.169.254", "metadata.google.internal", "0.0.0.0")  # nosec B104
        if parsed.hostname.lower() in blocked or (parsed.hostname or "").startswith("169.254."):
            return None
        return f"{parsed.scheme}://{parsed.hostname}" + (f":{parsed.port}" if parsed.port else "") + (parsed.path or "")

    async def test_connection(self, url: str, token: str) -> dict:
        """Test connection to Home Assistant.

        Returns dict with:
            - success: bool
            - message: str or None (HA message on success)
            - error: str or None (error message on failure)
        """
        safe_url = self._validate_url(url)
        if not safe_url:
            return {"success": False, "message": None, "error": "Invalid Home Assistant URL"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{safe_url.rstrip('/')}/api/",
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

        Always restricted to switch/light/input_boolean/script domains.
        When search is provided, further filters by entity_id or friendly_name match.

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

                    # Always restrict to allowed domains — prevents non-saveable
                    # entities (sensor.*, binary_sensor.*, media_player.*, …) from
                    # appearing in the dropdown even when a search query is active.
                    # Fixes: search was bypassing the domain filter so users could
                    # pick an entity whose entity_id the schema would later reject.
                    if domain not in default_domains:
                        continue

                    # When a search term is supplied, further narrow by entity_id
                    # or friendly_name (domain filter already applied above).
                    if search_lower:
                        if search_lower not in entity_id.lower() and search_lower not in friendly_name.lower():
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
            logger.warning("Failed to list HA entities: %s", e)
            return []

    async def poll_enclosure_for_printer(
        self, printer_id: int, temp_entity: str | None, humidity_entity: str | None
    ) -> None:
        """Fetch enclosure temp/humidity from HA and store in cache."""
        if not self.base_url or not self.token:
            return
        if not temp_entity and not humidity_entity:
            return

        result: dict = {"temp": None, "humidity": None, "temp_unit": "°C", "humidity_unit": "%"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout, verify=False) as client:
                if temp_entity:
                    response = await client.get(
                        f"{self.base_url}/api/states/{temp_entity}",
                        headers=self._headers(),
                    )
                    response.raise_for_status()
                    data = response.json()
                    state = data.get("state", "")
                    logger.info("Enclosure temp poll printer=%s entity=%s state=%r", printer_id, temp_entity, state)
                    if state not in ("unknown", "unavailable", ""):
                        try:
                            result["temp"] = float(state)
                            unit = data.get("attributes", {}).get("unit_of_measurement", "°C")
                            result["temp_unit"] = unit
                        except (ValueError, TypeError):
                            logger.warning(
                                "Enclosure temp not numeric for printer=%s entity=%s state=%r",
                                printer_id,
                                temp_entity,
                                state,
                            )

                if humidity_entity:
                    response = await client.get(
                        f"{self.base_url}/api/states/{humidity_entity}",
                        headers=self._headers(),
                    )
                    response.raise_for_status()
                    data = response.json()
                    state = data.get("state", "")
                    logger.info(
                        "Enclosure humidity poll printer=%s entity=%s state=%r", printer_id, humidity_entity, state
                    )
                    if state not in ("unknown", "unavailable", ""):
                        try:
                            result["humidity"] = float(state)
                        except (ValueError, TypeError):
                            logger.warning(
                                "Enclosure humidity not numeric for printer=%s entity=%s state=%r",
                                printer_id,
                                humidity_entity,
                                state,
                            )

            self._enclosure_cache[printer_id] = result
        except Exception as e:
            logger.warning("Enclosure sensor poll failed printer=%s: %s", printer_id, e)

    def get_cached_enclosure(self, printer_id: int) -> dict | None:
        """Return the last cached enclosure reading for a printer, or None."""
        return self._enclosure_cache.get(printer_id)

    async def get_sensor_value(self, entity_id: str) -> float | None:
        """Fetch a numeric sensor value from HA using pre-configured credentials.

        Called by the enclosure polling task in main.py. Returns None when HA
        is not configured, the entity is unavailable, or the value is non-numeric.
        """
        if not self.base_url or not self.token:
            return None
        try:
            async with httpx.AsyncClient(timeout=self.timeout, verify=False) as client:
                return await self._get_sensor_value(client, entity_id)
        except Exception as e:
            logger.debug("get_sensor_value failed entity=%s: %s", entity_id, e)
            return None

    async def get_entity_state(self, entity_id: str) -> str | None:
        """Fetch raw state string from HA using pre-configured credentials.

        Called by the enclosure fan polling task in main.py. Returns None when
        HA is not configured or the entity is unavailable.
        """
        if not self.base_url or not self.token:
            return None
        try:
            async with httpx.AsyncClient(timeout=self.timeout, verify=False) as client:
                response = await client.get(
                    f"{self.base_url}/api/states/{entity_id}",
                    headers=self._headers(),
                )
                response.raise_for_status()
                state = response.json().get("state", "")
                return state if state not in ("unknown", "unavailable", "") else None
        except Exception as e:
            logger.debug("get_entity_state failed entity=%s: %s", entity_id, e)
            return None

    # ── Storage unit polling ───────────────────────────────────────────────

    def __init_storage_cache(self):
        if not hasattr(self, "_storage_cache"):
            self._storage_cache: dict[int, dict] = {}

    async def poll_storage_unit(
        self, unit_id: int, temp_entity: str | None, humidity_entity: str | None
    ) -> dict | None:
        """Fetch temp/humidity from HA for a storage unit and store in cache.

        Returns the reading dict {temp, humidity, temp_unit, humidity_unit}
        or None if HA is not configured / both entities are absent.
        """
        self.__init_storage_cache()

        if not self.base_url or not self.token:
            return None
        if not temp_entity and not humidity_entity:
            return None

        result: dict = {"temp": None, "humidity": None, "temp_unit": "°C", "humidity_unit": "%"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout, verify=False) as client:
                if temp_entity:
                    response = await client.get(
                        f"{self.base_url}/api/states/{temp_entity}",
                        headers=self._headers(),
                    )
                    response.raise_for_status()
                    data = response.json()
                    state = data.get("state", "")
                    if state not in ("unknown", "unavailable", ""):
                        try:
                            result["temp"] = float(state)
                            result["temp_unit"] = data.get("attributes", {}).get("unit_of_measurement", "°C")
                        except (ValueError, TypeError):
                            logger.warning(
                                "Storage temp not numeric unit=%s entity=%s state=%r",
                                unit_id,
                                temp_entity,
                                state,
                            )

                if humidity_entity:
                    response = await client.get(
                        f"{self.base_url}/api/states/{humidity_entity}",
                        headers=self._headers(),
                    )
                    response.raise_for_status()
                    data = response.json()
                    state = data.get("state", "")
                    if state not in ("unknown", "unavailable", ""):
                        try:
                            result["humidity"] = float(state)
                        except (ValueError, TypeError):
                            logger.warning(
                                "Storage humidity not numeric unit=%s entity=%s state=%r",
                                unit_id,
                                humidity_entity,
                                state,
                            )

            self._storage_cache[unit_id] = result
            return result
        except Exception as e:
            logger.warning("Storage sensor poll failed unit=%s: %s", unit_id, e)
            return None

    def get_cached_storage(self, unit_id: int) -> dict | None:
        """Return the last cached reading for a storage unit, or None."""
        self.__init_storage_cache()
        return self._storage_cache.get(unit_id)

    def invalidate_storage_cache(self, unit_id: int | None = None) -> None:
        """Clear cached reading(s). Pass unit_id to clear one, None to clear all."""
        self.__init_storage_cache()
        if unit_id is None:
            self._storage_cache.clear()
        else:
            self._storage_cache.pop(unit_id, None)

    async def poll_fan_state(self, printer_id: int, fan_entity: str) -> bool | None:
        """Fetch fan on/off state from HA, update caches, return current state.

        Returns True (on), False (off), or None (unavailable/error).
        Stores previous state so callers can detect transitions via get_previous_fan_state().
        """
        if not self.base_url or not self.token or not fan_entity:
            return None

        try:
            async with httpx.AsyncClient(timeout=self.timeout, verify=False) as client:
                response = await client.get(
                    f"{self.base_url}/api/states/{fan_entity}",
                    headers=self._headers(),
                )
                response.raise_for_status()
                state = response.json().get("state", "").lower()
                is_on = state in ("on", "true", "1", "running", "active")
                fan_on = is_on if state not in ("unknown", "unavailable", "") else None
                logger.info(
                    "Enclosure fan poll printer=%s entity=%s state=%r is_on=%s", printer_id, fan_entity, state, fan_on
                )

            # Update enclosure cache with fan state
            if printer_id not in self._enclosure_cache:
                self._enclosure_cache[printer_id] = {}
            self._enclosure_cache[printer_id]["fan_on"] = fan_on

            return fan_on
        except Exception as e:
            logger.warning("Enclosure fan poll failed printer=%s: %s", printer_id, e)
            return None

    def get_previous_fan_state(self, printer_id: int) -> bool | None:
        """Return the fan state from the previous poll (before the latest poll)."""
        return self._fan_state_cache.get(printer_id)

    def set_fan_state_cache(self, printer_id: int, state: bool | None) -> None:
        """Persist the current fan state so next poll can detect transitions."""
        self._fan_state_cache[printer_id] = state

    async def list_environment_entities(self, url: str, token: str) -> list[dict]:
        """List HA sensor entities suitable for temperature or humidity monitoring.

        Returns sensors with units: °C, °F, K, %, g/m³ (common for temp/humidity sensors).
        """
        temp_units = {"°c", "°f", "k", "c", "f"}
        humidity_units = {"%", "% rh", "g/m³", "g/kg"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{url.rstrip('/')}/api/states",
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()

                entities = []
                for entity in response.json():
                    entity_id = entity.get("entity_id", "")
                    domain = entity_id.split(".")[0] if "." in entity_id else ""
                    if domain != "sensor":
                        continue

                    attrs = entity.get("attributes", {})
                    unit = (attrs.get("unit_of_measurement") or "").strip()
                    if unit.lower() not in (temp_units | humidity_units):
                        continue

                    device_class = attrs.get("device_class", "")
                    entities.append(
                        {
                            "entity_id": entity_id,
                            "friendly_name": attrs.get("friendly_name", entity_id),
                            "state": entity.get("state"),
                            "unit_of_measurement": unit,
                            "device_class": device_class,
                        }
                    )

                return sorted(entities, key=lambda x: x["friendly_name"].lower())
        except Exception as e:
            logger.warning("Failed to list HA environment entities: %s", e)
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
            logger.warning("Failed to list HA sensor entities: %s", e)
            return []


# Singleton instance
homeassistant_service = HomeAssistantService()
