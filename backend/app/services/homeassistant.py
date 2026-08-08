"""Service for communicating with Home Assistant via REST API."""

import asyncio
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
        """Normalise a caller-supplied HA URL, or return None if it is unsafe.

        The stored ``ha_url`` setting is already validated at the schema layer
        (``LAN_SERVICE_URL_SETTINGS`` in schemas/settings.py), but
        ``test_connection`` takes its URL straight from the request body, so
        the same policy has to be applied here.

        Delegates to ``_url_safety.assert_safe_lan_service_url`` rather than
        the string blocklist this replaces. That blocklist only knew three
        literal hostnames plus a ``169.254.`` prefix and never parsed the
        hostname as an IP, so it let through the Alibaba (100.100.100.200)
        and AWS-IPv6 (fd00:ec2::254) metadata endpoints, numeric-encoded
        loopback, multicast, and IPv4-mapped IPv6 encodings of the IMDS
        address it did know about.

        Loopback and RFC-1918 remain permitted — Home Assistant is a
        LAN-resident service by design, and the shared guard is documented
        that way.
        """
        from backend.app.api.routes._url_safety import assert_safe_lan_service_url

        try:
            assert_safe_lan_service_url(url, label="Home Assistant URL")
        except ValueError:
            return None
        # Guard passed, so the scheme is http/https and a hostname is present;
        # re-parse only to drop query/fragment and normalise the authority.
        parsed = urlparse(url)
        if not parsed.hostname:
            return None
        # urlparse strips the brackets off an IPv6 literal, so they have to go
        # back on or the rebuilt URL is unparseable ("http://fd00::1:8123").
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        return f"{parsed.scheme.lower()}://{host}" + (f":{parsed.port}" if parsed.port else "") + (parsed.path or "")

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

        Always filters to switch/light/input_boolean/script — the only domains
        the SmartPlugBase.ha_entity_id pattern accepts. When a search query is
        provided it narrows the same domain-filtered list by entity_id or
        friendly_name substring (case-insensitive).

        Previously search bypassed the domain filter, which let users pick a
        sensor.* or binary_sensor.* entity from the dropdown that the backend
        schema would then reject with the cryptic Pydantic pattern error
        (#1388). Picking what you can't save isn't a useful UX.

        Returns list of entity dicts with:
            - entity_id: str
            - friendly_name: str
            - state: str
            - domain: str
        """
        # Allowed domains for smart plug control — must mirror the regex in
        # backend/app/schemas/smart_plug.py:17 (SmartPlugBase.ha_entity_id).
        allowed_domains = {"switch", "light", "input_boolean", "script"}

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

                    if domain not in allowed_domains:
                        continue

                    if search_lower and (
                        search_lower not in entity_id.lower() and search_lower not in friendly_name.lower()
                    ):
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

    async def list_display_entities(self, url: str, token: str, search: str | None = None) -> list[dict]:
        """List entities that can be bound to a printer for display (#1148, #448).

        Covers every ``binary_sensor.*`` plus the ``sensor.*`` entities that
        carry a reading. Distinct from ``list_sensor_entities``, which exists
        for a plug's energy monitoring and therefore only admits power/energy
        units — an enclosure thermometer is exactly what that one filters out.

        A ``sensor.*`` qualifies when it has a unit or its state parses as a
        number. That drops the text sensors (``sensor.washing_machine_status``)
        that the card has no way to render as a value.
        """
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
                    if domain not in ("binary_sensor", "sensor"):
                        continue

                    attrs = entity.get("attributes", {})
                    unit = attrs.get("unit_of_measurement")
                    state = entity.get("state")

                    if domain == "sensor" and not unit and as_float(state) is None:
                        continue

                    friendly_name = attrs.get("friendly_name") or entity_id
                    if search_lower and (
                        search_lower not in entity_id.lower() and search_lower not in friendly_name.lower()
                    ):
                        continue

                    entities.append(
                        {
                            "entity_id": entity_id,
                            "friendly_name": friendly_name,
                            "state": state,
                            "domain": domain,
                            "device_class": attrs.get("device_class"),
                            "unit_of_measurement": unit,
                        }
                    )

                return sorted(entities, key=lambda x: x["friendly_name"].lower())
        except Exception as e:
            logger.warning("Failed to list HA display entities: %s", e)
            return []

    async def fetch_states(self, entity_ids: list[str]) -> dict[str, dict | None]:
        """Read several entities in one pass, keyed by entity_id.

        One GET per entity over a shared client rather than a single
        ``/api/states`` sweep: the poller only ever wants a handful of bound
        entities, and pulling every state in the user's Home Assistant on a
        15-second cadence is a lot of payload to throw away.

        A ``None`` value means that entity could not be read — the callers
        treat that as "no opinion" rather than as a state, so an unreachable
        Home Assistant never trips an alert or holds a print.
        """
        if not entity_ids:
            return {}
        if not self.base_url or not self.token:
            return dict.fromkeys(entity_ids)

        async with httpx.AsyncClient(timeout=self.timeout) as client:

            async def _one(entity_id: str) -> tuple[str, dict | None]:
                try:
                    response = await client.get(
                        f"{self.base_url}/api/states/{entity_id}",
                        headers=self._headers(),
                    )
                    response.raise_for_status()
                    return entity_id, response.json()
                except Exception as e:
                    logger.debug("Failed to read HA entity %s: %s", entity_id, e)
                    return entity_id, None

            results = await asyncio.gather(*(_one(e) for e in entity_ids))

        return dict(results)


def as_float(value) -> float | None:
    """Parse a HA state to a number, or None for "unknown"/"unavailable"/text."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# Singleton instance
homeassistant_service = HomeAssistantService()
