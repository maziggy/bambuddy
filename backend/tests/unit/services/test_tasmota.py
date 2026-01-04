"""Unit tests for TasmotaService.

Tests smart plug HTTP communication and error handling.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.app.services.tasmota import TasmotaService


class TestTasmotaService:
    """Tests for TasmotaService class."""

    @pytest.fixture
    def service(self):
        """Create a TasmotaService instance."""
        return TasmotaService(timeout=5.0)

    @pytest.fixture
    def mock_plug(self):
        """Create a mock SmartPlug object."""
        plug = MagicMock()
        plug.ip_address = "192.168.1.100"
        plug.username = None
        plug.password = None
        plug.name = "Test Plug"
        return plug

    # ========================================================================
    # Tests for URL building
    # ========================================================================

    def test_build_url_without_auth(self, service):
        """Verify URL is built correctly without auth."""
        url = service._build_url("192.168.1.100", "Power On")
        assert url == "http://192.168.1.100/cm?cmnd=Power%20On"

    def test_build_url_with_auth(self, service):
        """Verify URL includes credentials when provided."""
        url = service._build_url("192.168.1.100", "Power On", username="admin", password="secret")
        assert url == "http://admin:secret@192.168.1.100/cm?cmnd=Power%20On"

    def test_build_url_encodes_special_characters(self, service):
        """Verify special characters in commands are encoded."""
        url = service._build_url("192.168.1.100", "Backlog Power On; Delay 100")
        assert "Backlog%20Power%20On" in url

    # ========================================================================
    # Tests for turn_on
    # ========================================================================

    @pytest.mark.asyncio
    async def test_turn_on_success(self, service, mock_plug):
        """Verify turn_on returns True on success."""
        with patch.object(service, "_send_command", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"POWER": "ON"}

            result = await service.turn_on(mock_plug)

            assert result is True
            mock_send.assert_called_once_with("192.168.1.100", "Power On", None, None)

    @pytest.mark.asyncio
    async def test_turn_on_failure(self, service, mock_plug):
        """Verify turn_on returns False on failure."""
        with patch.object(service, "_send_command", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = None

            result = await service.turn_on(mock_plug)

            assert result is False

    @pytest.mark.asyncio
    async def test_turn_on_with_auth(self, service, mock_plug):
        """Verify turn_on passes credentials when provided."""
        mock_plug.username = "admin"
        mock_plug.password = "secret"

        with patch.object(service, "_send_command", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"POWER": "ON"}

            await service.turn_on(mock_plug)

            mock_send.assert_called_once_with("192.168.1.100", "Power On", "admin", "secret")

    # ========================================================================
    # Tests for turn_off
    # ========================================================================

    @pytest.mark.asyncio
    async def test_turn_off_success(self, service, mock_plug):
        """Verify turn_off returns True on success."""
        with patch.object(service, "_send_command", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"POWER": "OFF"}

            result = await service.turn_off(mock_plug)

            assert result is True

    @pytest.mark.asyncio
    async def test_turn_off_failure(self, service, mock_plug):
        """Verify turn_off returns False on failure."""
        with patch.object(service, "_send_command", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = None

            result = await service.turn_off(mock_plug)

            assert result is False

    # ========================================================================
    # Tests for toggle
    # ========================================================================

    @pytest.mark.asyncio
    async def test_toggle_success(self, service, mock_plug):
        """Verify toggle returns True on success."""
        with patch.object(service, "_send_command", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"POWER": "ON"}

            result = await service.toggle(mock_plug)

            assert result is True
            mock_send.assert_called_once_with("192.168.1.100", "Power Toggle", None, None)

    # ========================================================================
    # Tests for get_status
    # ========================================================================

    @pytest.mark.asyncio
    async def test_get_status_returns_on(self, service, mock_plug):
        """Verify get_status returns correct state when ON."""
        with patch.object(service, "_send_command", new_callable=AsyncMock) as mock_send:
            # Tasmota returns {"POWER": "ON"} for Power command
            mock_send.return_value = {"POWER": "ON"}

            result = await service.get_status(mock_plug)

            assert result is not None
            assert result["state"] == "ON"
            assert result["reachable"] is True

    @pytest.mark.asyncio
    async def test_get_status_returns_off(self, service, mock_plug):
        """Verify get_status returns correct state when OFF."""
        with patch.object(service, "_send_command", new_callable=AsyncMock) as mock_send:
            # Tasmota returns {"POWER": "OFF"} for Power command
            mock_send.return_value = {"POWER": "OFF"}

            result = await service.get_status(mock_plug)

            assert result is not None
            assert result["state"] == "OFF"

    @pytest.mark.asyncio
    async def test_get_status_unreachable(self, service, mock_plug):
        """Verify get_status handles unreachable device."""
        with patch.object(service, "_send_command", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = None

            result = await service.get_status(mock_plug)

            assert result is not None
            assert result["reachable"] is False

    # ========================================================================
    # Tests for get_energy
    # ========================================================================

    @pytest.mark.asyncio
    async def test_get_energy_returns_data(self, service, mock_plug):
        """Verify get_energy parses energy data correctly."""
        with patch.object(service, "_send_command", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {
                "StatusSNS": {
                    "ENERGY": {
                        "Power": 150.5,
                        "Voltage": 120.0,
                        "Current": 1.25,
                        "Today": 2.5,
                        "Total": 100.0,
                        "Factor": 0.95,
                    }
                }
            }

            result = await service.get_energy(mock_plug)

            assert result is not None
            assert result["power"] == 150.5
            assert result["voltage"] == 120.0
            assert result["current"] == 1.25
            assert result["today"] == 2.5
            assert result["total"] == 100.0
            assert result["factor"] == 0.95

    @pytest.mark.asyncio
    async def test_get_energy_handles_missing_data(self, service, mock_plug):
        """Verify get_energy handles devices without energy monitoring."""
        with patch.object(service, "_send_command", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"StatusSNS": {}}

            result = await service.get_energy(mock_plug)

            assert result is None

    @pytest.mark.asyncio
    async def test_get_energy_handles_unreachable(self, service, mock_plug):
        """Verify get_energy handles unreachable device."""
        with patch.object(service, "_send_command", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = None

            result = await service.get_energy(mock_plug)

            assert result is None

    @pytest.mark.asyncio
    async def test_get_energy_handles_partial_data(self, service, mock_plug):
        """Verify get_energy handles partial energy data."""
        with patch.object(service, "_send_command", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {
                "StatusSNS": {
                    "ENERGY": {
                        "Power": 150.5,
                        # Missing other fields
                    }
                }
            }

            result = await service.get_energy(mock_plug)

            assert result is not None
            assert result["power"] == 150.5
            # Missing fields should be None or 0
            assert result.get("voltage") is None or result.get("voltage") == 0

    # ========================================================================
    # Tests for test_connection
    # ========================================================================

    @pytest.mark.asyncio
    async def test_test_connection_success(self, service):
        """Verify test_connection returns success on reachable device."""
        with patch.object(service, "_send_command", new_callable=AsyncMock) as mock_send:
            # First call (Power) returns state, second call (Status 0) returns device info
            mock_send.side_effect = [
                {"POWER": "ON"},  # Power command response
                {"Status": {"DeviceName": "Test Plug"}},  # Status 0 response
            ]

            result = await service.test_connection("192.168.1.100")

            assert result["success"] is True
            assert result["state"] == "ON"
            assert result["device_name"] == "Test Plug"

    @pytest.mark.asyncio
    async def test_test_connection_failure(self, service):
        """Verify test_connection returns failure on unreachable device."""
        with patch.object(service, "_send_command", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = None

            result = await service.test_connection("192.168.1.100")

            assert result["success"] is False

    # ========================================================================
    # Tests for _send_command
    # ========================================================================

    @pytest.mark.asyncio
    async def test_send_command_handles_timeout(self, service):
        """Verify timeout is handled gracefully."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.TimeoutException("Timeout")
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_class.return_value.__aexit__ = AsyncMock()

            result = await service._send_command("192.168.1.100", "Power")

            assert result is None

    @pytest.mark.asyncio
    async def test_send_command_handles_connection_error(self, service):
        """Verify connection error is handled gracefully."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ConnectError("Connection refused")
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_class.return_value.__aexit__ = AsyncMock()

            result = await service._send_command("192.168.1.100", "Power")

            assert result is None

    @pytest.mark.asyncio
    async def test_send_command_handles_invalid_json(self, service):
        """Verify invalid JSON response is handled gracefully."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.json.side_effect = ValueError("Invalid JSON")
            mock_client.get.return_value = mock_response
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_class.return_value.__aexit__ = AsyncMock()

            result = await service._send_command("192.168.1.100", "Power")

            assert result is None

    @pytest.mark.asyncio
    async def test_send_command_success(self, service):
        """Verify successful command returns parsed JSON."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.json.return_value = {"POWER": "ON"}
            mock_client.get.return_value = mock_response
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_class.return_value.__aexit__ = AsyncMock()

            result = await service._send_command("192.168.1.100", "Power")

            assert result == {"POWER": "ON"}


class TestTasmotaServiceSingleton:
    """Tests for the global tasmota_service singleton."""

    def test_singleton_exists(self):
        """Verify global tasmota_service instance exists."""
        from backend.app.services.tasmota import tasmota_service

        assert tasmota_service is not None
        assert isinstance(tasmota_service, TasmotaService)
