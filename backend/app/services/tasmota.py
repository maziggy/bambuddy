"""Service for communicating with Tasmota devices via HTTP API."""

import ipaddress
import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from backend.app.models.smart_plug import SmartPlug

logger = logging.getLogger(__name__)


class TasmotaService:
    """Service for communicating with Tasmota devices via HTTP API."""

    def __init__(self, timeout: float = 5.0):
        self.timeout = timeout

    def _build_url(self, ip: str, command: str) -> str:
        """Build Tasmota command URL."""
        # URL encode the command
        cmd = command.replace(" ", "%20")
        return f"http://{ip}/cm?cmnd={cmd}"

    @staticmethod
    def _validate_ip(ip: str) -> bool:
        """Block cloud metadata and link-local IPs."""
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False  # Not a valid IP
        return not addr.is_loopback and not addr.is_link_local

    async def _send_command(
        self,
        ip: str,
        command: str,
        username: str | None = None,
        password: str | None = None,
    ) -> dict | None:
        """Send a command to a Tasmota device and return the response."""
        if not self._validate_ip(ip):
            logger.warning("Blocked Tasmota request to invalid IP: %s", ip)
            return None
        url = self._build_url(ip, command)
        auth = (username, password) if username and password else None

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, auth=auth)
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException:
            logger.warning("Tasmota device at %s timed out", ip)
            return None
        except httpx.HTTPStatusError as e:
            logger.warning("Tasmota device at %s returned error: %s", ip, e)
            return None
        except httpx.RequestError as e:
            logger.warning("Failed to connect to Tasmota device at %s: %s", ip, e)
            return None
        except Exception as e:
            logger.error("Unexpected error communicating with Tasmota at %s: %s", ip, e)
            return None

    async def get_status(self, plug: "SmartPlug") -> dict:
        """Get current power state and device info.

        Returns dict with:
            - state: "ON" or "OFF" or None if unreachable
            - reachable: bool
            - device_name: str or None
        """
        result = await self._send_command(plug.ip_address, "Power", plug.username, plug.password)

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
        result = await self._send_command(plug.ip_address, "Power On", plug.username, plug.password)

        if result is None:
            return False

        # Check if the command was successful
        state = result.get("POWER") or result.get("POWER1")
        success = state == "ON"

        if success:
            logger.info("Turned ON smart plug '%s' at %s", plug.name, plug.ip_address)
        else:
            logger.warning("Failed to turn ON smart plug '%s' at %s", plug.name, plug.ip_address)

        return success

    async def turn_off(self, plug: "SmartPlug") -> bool:
        """Turn off the plug. Returns True if successful."""
        result = await self._send_command(plug.ip_address, "Power Off", plug.username, plug.password)

        if result is None:
            return False

        # Check if the command was successful
        state = result.get("POWER") or result.get("POWER1")
        success = state == "OFF"

        if success:
            logger.info("Turned OFF smart plug '%s' at %s", plug.name, plug.ip_address)
        else:
            logger.warning("Failed to turn OFF smart plug '%s' at %s", plug.name, plug.ip_address)

        return success

    async def toggle(self, plug: "SmartPlug") -> bool:
        """Toggle the plug state. Returns True if successful."""
        result = await self._send_command(plug.ip_address, "Power Toggle", plug.username, plug.password)

        if result is None:
            return False

        state = result.get("POWER") or result.get("POWER1")
        success = state in ["ON", "OFF"]

        if success:
            logger.info("Toggled smart plug '%s' at %s to %s", plug.name, plug.ip_address, state)

        return success

    async def get_energy(self, plug: "SmartPlug") -> dict | None:
        """Get energy monitoring data from the plug.

        Returns dict with energy data or None if not available:
            - power: Current power in watts
            - voltage: Voltage in V
            - current: Current in A
            - today: Energy used today in kWh
            - total: Total energy in kWh
            - factor: Power factor (0-1)
        """
        result = await self._send_command(plug.ip_address, "Status 8", plug.username, plug.password)

        if result is None:
            return None

        # Response format: {"StatusSNS":{"ENERGY":{...}}}
        status_sns = result.get("StatusSNS", {})
        energy = status_sns.get("ENERGY")

        if not energy:
            # Device doesn't have energy monitoring
            return None

        return {
            "power": energy.get("Power"),  # Current watts
            "voltage": energy.get("Voltage"),  # Volts
            "current": energy.get("Current"),  # Amps
            "today": energy.get("Today"),  # kWh today
            "yesterday": energy.get("Yesterday"),  # kWh yesterday
            "total": energy.get("Total"),  # Total kWh
            "factor": energy.get("Factor"),  # Power factor
            "apparent_power": energy.get("ApparentPower"),  # VA
            "reactive_power": energy.get("ReactivePower"),  # VAr
        }

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
