"""Service for communicating with generic REST API smart plugs."""
import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx

if TYPE_CHECKING:
    from backend.app.models.smart_plug import SmartPlug

logger = logging.getLogger(__name__)


class RestPlugService:
    """Service for controlling smart plugs via user-defined REST URLs.

    The user configures custom URLs for each action (on/off/toggle/status).
    Bambuddy sends an HTTP GET or POST request to the configured URL.
    The method (GET/POST) is configurable per plug.

    For the status URL, the response body is parsed to determine ON/OFF state:
    - If rest_state_on_value is set, the response body is checked to contain that string.
    - Otherwise the plug is assumed reachable if the request succeeds (2xx).
    """

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    @staticmethod
    def _validate_url(url: str | None) -> str | None:
        """Validate URL and block dangerous/internal destinations."""
        if not url:
            return None
        try:
            parsed = urlparse(url)
        except ValueError:
            return None
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        hostname = (parsed.hostname or "").lower()
        blocked = (
            "169.254.169.254",
            "metadata.google.internal",
            "127.0.0.1",
            "localhost",
            "::1",
        )
        if hostname in blocked or hostname.startswith("169.254."):
            return None
        return url

    async def _send_request(
        self,
        url: str | None,
        method: str = "GET",
    ) -> httpx.Response | None:
        """Send an HTTP request and return the response, or None on failure."""
        safe_url = self._validate_url(url)
        if not safe_url:
            logger.warning("Blocked REST plug request to invalid/unsafe URL: %s", url)
            return None
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                if method.upper() == "POST":
                    response = await client.post(safe_url)
                else:
                    response = await client.get(safe_url)
                response.raise_for_status()
                return response
        except httpx.TimeoutException:
            logger.warning("REST plug request to %s timed out", url)
        except httpx.HTTPStatusError as e:
            logger.warning("REST plug request to %s returned HTTP %s", url, e.response.status_code)
        except httpx.RequestError as e:
            logger.warning("REST plug request to %s failed: %s", url, e)
        except Exception as e:
            logger.error("Unexpected error in REST plug request to %s: %s", url, e)
        return None

    # ------------------------------------------------------------------
    # Public interface (mirrors TasmotaService / HomeAssistantService)
    # ------------------------------------------------------------------

    async def get_status(self, plug: "SmartPlug") -> dict:
        """Query the plug status URL and return state dict.

        Returns:
            dict with keys:
                state (str | None): "ON", "OFF", or None if unreachable
                reachable (bool)
                device_name (str | None)
        """
        if not plug.rest_status_url:
            # No status URL configured – plug may still be working, state is unknown
            return {"state": None, "reachable": True, "device_name": None}

        response = await self._send_request(plug.rest_status_url, plug.rest_method or "GET")
        if response is None:
            return {"state": None, "reachable": False, "device_name": None}

        # Determine ON/OFF from response body
        state: str | None = None
        if plug.rest_state_on_value:
            body = response.text
            state = "ON" if plug.rest_state_on_value in body else "OFF"

        return {"state": state, "reachable": True, "device_name": None}

    async def turn_on(self, plug: "SmartPlug") -> bool:
        """Call the configured ON URL. Returns True if successful."""
        if not plug.rest_on_url:
            logger.warning("REST plug '%s' has no on_url configured", plug.name)
            return False
        response = await self._send_request(plug.rest_on_url, plug.rest_method or "GET")
        success = response is not None
        if success:
            logger.info("Turned ON REST plug '%s' via %s", plug.name, plug.rest_on_url)
        else:
            logger.warning("Failed to turn ON REST plug '%s'", plug.name)
        return success

    async def turn_off(self, plug: "SmartPlug") -> bool:
        """Call the configured OFF URL. Returns True if successful."""
        if not plug.rest_off_url:
            logger.warning("REST plug '%s' has no off_url configured", plug.name)
            return False
        response = await self._send_request(plug.rest_off_url, plug.rest_method or "GET")
        success = response is not None
        if success:
            logger.info("Turned OFF REST plug '%s' via %s", plug.name, plug.rest_off_url)
        else:
            logger.warning("Failed to turn OFF REST plug '%s'", plug.name)
        return success

    async def toggle(self, plug: "SmartPlug") -> bool:
        """Call the configured toggle URL, or fall back to on/off based on current state."""
        if plug.rest_toggle_url:
            response = await self._send_request(plug.rest_toggle_url, plug.rest_method or "GET")
            success = response is not None
            if success:
                logger.info("Toggled REST plug '%s' via %s", plug.name, plug.rest_toggle_url)
            return success

        # No toggle URL: determine current state and flip
        status = await self.get_status(plug)
        if status["state"] == "ON":
            return await self.turn_off(plug)
        return await self.turn_on(plug)

    async def get_energy(self, plug: "SmartPlug") -> dict | None:
        """REST plugs do not support energy monitoring."""
        return None

    async def test_connection(
        self,
        on_url: str | None,
        off_url: str | None,
        status_url: str | None,
        method: str = "GET",
        state_on_value: str | None = None,
    ) -> dict:
        """Test the configured URLs by sending a request to the status URL (preferred)
        or the ON URL as fallback.

        Returns:
            dict with keys:
                success (bool)
                state (str | None)
                error (str | None)
        """
        test_url = status_url or on_url
        if not test_url:
            return {"success": False, "state": None, "error": "No URL configured to test"}

        safe_url = self._validate_url(test_url)
        if not safe_url:
            return {"success": False, "state": None, "error": "Invalid or unsafe URL"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                if method.upper() == "POST":
                    response = await client.post(safe_url)
                else:
                    response = await client.get(safe_url)
                response.raise_for_status()

            state: str | None = None
            if status_url and state_on_value:
                state = "ON" if state_on_value in response.text else "OFF"

            return {"success": True, "state": state, "error": None}
        except httpx.HTTPStatusError as e:
            return {"success": False, "state": None, "error": f"HTTP {e.response.status_code}"}
        except httpx.TimeoutException:
            return {"success": False, "state": None, "error": "Connection timeout"}
        except httpx.ConnectError:
            return {"success": False, "state": None, "error": "Could not connect to device"}
        except Exception as e:
            return {"success": False, "state": None, "error": str(e)}


# Singleton instance
rest_plug_service = RestPlugService()
