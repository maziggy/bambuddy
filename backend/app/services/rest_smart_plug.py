"""Service for controlling smart plugs via generic REST/HTTP API."""

import ipaddress
import json
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

if TYPE_CHECKING:
    from backend.app.models.smart_plug import SmartPlug

logger = logging.getLogger(__name__)


class RESTSmartPlugService:
    """Service for controlling smart plugs via generic REST/HTTP API.

    Supports any home automation platform with an HTTP API (openHAB, ioBroker, FHEM, Node-RED, etc.).
    """

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    @staticmethod
    def _validate_url(url: str) -> bool:
        """Block cloud metadata and link-local IPs."""
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname
            if not hostname:
                return False
            addr = ipaddress.ip_address(hostname)
            return not addr.is_loopback and not addr.is_link_local
        except ValueError:
            # Hostname is not an IP (e.g., "openhab.local") — allow it
            return True

    def _parse_headers(self, headers_json: str | None) -> dict[str, str]:
        """Parse JSON string to dict of headers."""
        if not headers_json:
            return {}
        try:
            headers = json.loads(headers_json)
            if isinstance(headers, dict):
                return {str(k): str(v) for k, v in headers.items()}
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to parse REST headers JSON: %s", headers_json)
        return {}

    @staticmethod
    def _extract_json_path(data: Any, path: str) -> Any:
        """Extract value using dot notation (e.g., 'state' or 'data.power.status')."""
        if not path:
            return None

        parts = path.split(".")
        current = data

        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None

        return current

    async def _send_request(
        self,
        url: str,
        method: str = "POST",
        headers: dict[str, str] | None = None,
        body: str | None = None,
    ) -> httpx.Response | None:
        """Send an HTTP request and return the response."""
        if not self._validate_url(url):
            logger.warning("Blocked REST request to invalid URL: %s", url)
            return None

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                kwargs: dict[str, Any] = {"headers": headers or {}}
                if body is not None:
                    # Try to detect if body is JSON
                    try:
                        json.loads(body)
                        kwargs["content"] = body
                        if "Content-Type" not in (headers or {}):
                            kwargs["headers"]["Content-Type"] = "application/json"
                    except (json.JSONDecodeError, TypeError):
                        kwargs["content"] = body

                response = await client.request(method.upper(), url, **kwargs)
                response.raise_for_status()
                return response
        except httpx.TimeoutException:
            logger.warning("REST smart plug at %s timed out", url)
            return None
        except httpx.HTTPStatusError as e:
            logger.warning("REST smart plug at %s returned error: %s", url, e)
            return None
        except httpx.RequestError as e:
            logger.warning("Failed to connect to REST smart plug at %s: %s", url, e)
            return None
        except Exception as e:
            logger.error("Unexpected error communicating with REST smart plug at %s: %s", url, e)
            return None

    async def turn_on(self, plug: "SmartPlug") -> bool:
        """Turn on the plug. Returns True if successful."""
        if not plug.rest_on_url:
            logger.warning("No ON URL configured for REST plug '%s'", plug.name)
            return False

        headers = self._parse_headers(plug.rest_headers)
        method = plug.rest_method or "POST"
        response = await self._send_request(plug.rest_on_url, method, headers, plug.rest_on_body)

        if response is not None:
            logger.info("Turned ON REST smart plug '%s' via %s %s", plug.name, method, plug.rest_on_url)
            return True

        logger.warning("Failed to turn ON REST smart plug '%s'", plug.name)
        return False

    async def turn_off(self, plug: "SmartPlug") -> bool:
        """Turn off the plug. Returns True if successful."""
        if not plug.rest_off_url:
            logger.warning("No OFF URL configured for REST plug '%s'", plug.name)
            return False

        headers = self._parse_headers(plug.rest_headers)
        method = plug.rest_method or "POST"
        response = await self._send_request(plug.rest_off_url, method, headers, plug.rest_off_body)

        if response is not None:
            logger.info("Turned OFF REST smart plug '%s' via %s %s", plug.name, method, plug.rest_off_url)
            return True

        logger.warning("Failed to turn OFF REST smart plug '%s'", plug.name)
        return False

    async def toggle(self, plug: "SmartPlug") -> bool:
        """Toggle the plug state by checking status first."""
        status = await self.get_status(plug)
        if status["state"] == "ON":
            return await self.turn_off(plug)
        else:
            return await self.turn_on(plug)

    async def get_status(self, plug: "SmartPlug") -> dict:
        """Get current power state.

        Returns dict with:
            - state: "ON" or "OFF" or None if unreachable
            - reachable: bool
            - device_name: None (REST plugs don't report device names)
        """
        if not plug.rest_status_url:
            return {"state": None, "reachable": True, "device_name": None}

        headers = self._parse_headers(plug.rest_headers)
        response = await self._send_request(plug.rest_status_url, "GET", headers)

        if response is None:
            return {"state": None, "reachable": False, "device_name": None}

        # Try to extract state from response
        state = None
        try:
            data = response.json()
            if plug.rest_status_path:
                raw_value = self._extract_json_path(data, plug.rest_status_path)
                if raw_value is not None:
                    on_value = (plug.rest_status_on_value or "ON").upper()
                    state = "ON" if str(raw_value).upper() == on_value else "OFF"
            else:
                # No path configured — try common patterns
                raw_value = str(data).upper() if not isinstance(data, dict) else None
                if raw_value in ("ON", "TRUE", "1"):
                    state = "ON"
                elif raw_value in ("OFF", "FALSE", "0"):
                    state = "OFF"
        except Exception:
            # Response is not JSON — try raw text
            text = response.text.strip().upper()
            on_value = (plug.rest_status_on_value or "ON").upper()
            state = "ON" if text == on_value else "OFF"

        return {"state": state, "reachable": True, "device_name": None}

    async def get_energy(self, plug: "SmartPlug") -> dict | None:
        """Get energy monitoring data.

        Each value can come from its own URL or fall back to the shared status URL.
        Multipliers convert units (e.g. Wh → kWh with multiplier 0.001).

        Two distinct energy counters, because devices differ in which they have
        (#2539):

        - ``rest_energy_path`` — energy used **today**, resetting at midnight.
        - ``rest_energy_total_path`` — a **lifetime** counter that never resets.
          A Shelly exposes only this one (``aenergy.total``, in Wh). Reading it as
          "today" is wrong all day long, and leaves Total and the hourly snapshots
          — which the Statistics page's date filters run on — permanently empty.

        Yesterday is not read from the device: no REST device we know of reports
        it. It is derived from the lifetime counter's snapshots instead, in
        ``services.plug_energy_history``.

        Returns dict with energy data or None if not available.
        """
        if not plug.rest_power_path and not plug.rest_energy_path and not plug.rest_energy_total_path:
            return None

        headers = self._parse_headers(plug.rest_headers)
        energy: dict[str, float | None] = {}

        power_url = plug.rest_power_url or plug.rest_status_url if plug.rest_power_path else None
        energy_url = plug.rest_energy_url or plug.rest_status_url if plug.rest_energy_path else None
        # The lifetime counter almost always rides on the same response as the
        # today counter (one Shelly RPC call returns both `apower` and
        # `aenergy.total`), so it shares the energy URL and the dedupe below
        # collapses them into a single fetch.
        total_url = plug.rest_energy_url or plug.rest_status_url if plug.rest_energy_total_path else None

        # Fetch data — deduplicate when several resolve to the same URL
        fetched: dict[str, Any] = {}

        for url in {power_url, energy_url, total_url} - {None}:
            fetched[url] = await self._fetch_json(url, headers)

        def _read(path: str | None, url: str | None, multiplier: float | None) -> float | None:
            if not path or not url or fetched.get(url) is None:
                return None
            raw = self._extract_json_path(fetched[url], path)
            if raw is None:
                return None
            try:
                return float(raw) * (multiplier or 1.0)
            except (ValueError, TypeError):
                return None

        power = _read(plug.rest_power_path, power_url, plug.rest_power_multiplier)
        if power is not None:
            energy["power"] = power

        today = _read(plug.rest_energy_path, energy_url, plug.rest_energy_multiplier)
        if today is not None:
            energy["today"] = today

        total = _read(plug.rest_energy_total_path, total_url, plug.rest_energy_total_multiplier)
        if total is not None:
            energy["total"] = total

        return energy if energy else None

    async def _fetch_json(self, url: str, headers: dict[str, str]) -> Any:
        """Fetch a URL and parse JSON response. Returns parsed data or None."""
        response = await self._send_request(url, "GET", headers)
        if response is None:
            return None
        try:
            return response.json()
        except Exception:
            return None

    async def test_connection(self, url: str, method: str = "GET", headers: str | None = None) -> dict:
        """Test connection to a REST endpoint.

        Returns dict with:
            - success: bool
            - error: error message if failed
        """
        if not self._validate_url(url):
            return {"success": False, "error": "Invalid URL (loopback/link-local addresses are blocked)"}

        parsed_headers = self._parse_headers(headers)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(method.upper(), url, headers=parsed_headers)
                response.raise_for_status()
                return {"success": True, "error": None}
        except httpx.TimeoutException:
            return {"success": False, "error": "Connection timed out"}
        except httpx.HTTPStatusError as e:
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.reason_phrase}"}
        except httpx.RequestError as e:
            return {"success": False, "error": f"Connection failed: {e}"}
        except Exception as e:
            return {"success": False, "error": str(e)}


# Singleton instance
rest_smart_plug_service = RESTSmartPlugService()
