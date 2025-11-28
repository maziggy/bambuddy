"""Service for communicating with Tasmota devices via HTTP API."""

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from backend.app.models.smart_plug import SmartPlug

logger = logging.getLogger(__name__)


class TasmotaService:
    """Service for communicating with Tasmota devices via HTTP API."""

    def __init__(self, timeout: float = 5.0):
        self.timeout = timeout

    def _build_url(
        self,
        ip: str,
        command: str,
        username: str | None = None,
        password: str | None = None,
    ) -> str:
        """Build Tasmota command URL."""
        # URL encode the command
        cmd = command.replace(" ", "%20")

        if username and password:
            return f"http://{username}:{password}@{ip}/cm?cmnd={cmd}"
        return f"http://{ip}/cm?cmnd={cmd}"

    async def _send_command(
        self,
        ip: str,
        command: str,
        username: str | None = None,
        password: str | None = None,
    ) -> dict | None:
        """Send a command to a Tasmota device and return the response."""
        url = self._build_url(ip, command, username, password)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException:
            logger.warning(f"Tasmota device at {ip} timed out")
            return None
        except httpx.HTTPStatusError as e:
            logger.warning(f"Tasmota device at {ip} returned error: {e}")
            return None
        except httpx.RequestError as e:
            logger.warning(f"Failed to connect to Tasmota device at {ip}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error communicating with Tasmota at {ip}: {e}")
            return None

    async def get_status(self, plug: "SmartPlug") -> dict:
        """Get current power state and device info.

        Returns dict with:
            - state: "ON" or "OFF" or None if unreachable
            - reachable: bool
            - device_name: str or None
        """
        result = await self._send_command(
            plug.ip_address, "Power", plug.username, plug.password
        )

        if result is None:
            return {"state": None, "reachable": False, "device_name": None}

        # Response format: {"POWER":"ON"} or {"POWER":"OFF"}
        # Some devices use {"POWER1":"ON"} for multi-relay
        state = None
        for key in ["POWER", "POWER1"]:
            if key in result:
                state = result[key]
                break

        return {"state": state, "reachable": True, "device_name": None}

    async def turn_on(self, plug: "SmartPlug") -> bool:
        """Turn on the plug. Returns True if successful."""
        result = await self._send_command(
            plug.ip_address, "Power On", plug.username, plug.password
        )

        if result is None:
            return False

        # Check if the command was successful
        state = result.get("POWER") or result.get("POWER1")
        success = state == "ON"

        if success:
            logger.info(f"Turned ON smart plug '{plug.name}' at {plug.ip_address}")
        else:
            logger.warning(
                f"Failed to turn ON smart plug '{plug.name}' at {plug.ip_address}"
            )

        return success

    async def turn_off(self, plug: "SmartPlug") -> bool:
        """Turn off the plug. Returns True if successful."""
        result = await self._send_command(
            plug.ip_address, "Power Off", plug.username, plug.password
        )

        if result is None:
            return False

        # Check if the command was successful
        state = result.get("POWER") or result.get("POWER1")
        success = state == "OFF"

        if success:
            logger.info(f"Turned OFF smart plug '{plug.name}' at {plug.ip_address}")
        else:
            logger.warning(
                f"Failed to turn OFF smart plug '{plug.name}' at {plug.ip_address}"
            )

        return success

    async def toggle(self, plug: "SmartPlug") -> bool:
        """Toggle the plug state. Returns True if successful."""
        result = await self._send_command(
            plug.ip_address, "Power Toggle", plug.username, plug.password
        )

        if result is None:
            return False

        state = result.get("POWER") or result.get("POWER1")
        success = state in ["ON", "OFF"]

        if success:
            logger.info(
                f"Toggled smart plug '{plug.name}' at {plug.ip_address} to {state}"
            )

        return success

    async def test_connection(
        self,
        ip: str,
        username: str | None = None,
        password: str | None = None,
    ) -> dict:
        """Test connection to a Tasmota device.

        Returns dict with:
            - success: bool
            - state: current power state or None
            - device_name: device name or None
            - error: error message if failed
        """
        # Try to get power status
        result = await self._send_command(ip, "Power", username, password)

        if result is None:
            return {
                "success": False,
                "state": None,
                "device_name": None,
                "error": "Could not connect to device",
            }

        state = result.get("POWER") or result.get("POWER1")

        # Try to get device name
        status_result = await self._send_command(ip, "Status 0", username, password)
        device_name = None
        if status_result and "Status" in status_result:
            device_name = status_result["Status"].get("DeviceName")

        return {
            "success": True,
            "state": state,
            "device_name": device_name,
            "error": None,
        }


# Singleton instance
tasmota_service = TasmotaService()
