"""Unit tests for REST smart plug service."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.app.services.rest_smart_plug import RESTSmartPlugService


@pytest.fixture
def service():
    return RESTSmartPlugService(timeout=5.0)


@pytest.fixture
def mock_plug():
    plug = MagicMock()
    plug.name = "Test REST Plug"
    plug.plug_type = "rest"
    plug.rest_on_url = "http://192.168.1.50:8080/api/plug/on"
    plug.rest_on_body = '{"state": "on"}'
    plug.rest_off_url = "http://192.168.1.50:8080/api/plug/off"
    plug.rest_off_body = '{"state": "off"}'
    plug.rest_method = "POST"
    plug.rest_headers = '{"Authorization": "Bearer test-token"}'
    plug.rest_status_url = "http://192.168.1.50:8080/api/plug/status"
    plug.rest_status_path = "state"
    plug.rest_status_on_value = "ON"
    plug.rest_power_url = None
    plug.rest_power_path = "power"
    plug.rest_power_multiplier = 1.0
    plug.rest_energy_url = None
    plug.rest_energy_path = "energy.today"
    plug.rest_energy_multiplier = 1.0
    return plug


class TestURLValidation:
    def test_valid_ip_url(self, service):
        assert service._validate_url("http://192.168.1.50:8080/api") is True

    def test_hostname_url(self, service):
        assert service._validate_url("http://openhab.local:8080/api") is True

    def test_loopback_blocked(self, service):
        assert service._validate_url("http://127.0.0.1/api") is False

    def test_link_local_blocked(self, service):
        assert service._validate_url("http://169.254.1.1/api") is False

    def test_empty_hostname(self, service):
        assert service._validate_url("http:///api") is False


class TestParseHeaders:
    def test_valid_json(self, service):
        headers = service._parse_headers('{"Authorization": "Bearer abc", "X-Custom": "val"}')
        assert headers == {"Authorization": "Bearer abc", "X-Custom": "val"}

    def test_none_headers(self, service):
        assert service._parse_headers(None) == {}

    def test_empty_string(self, service):
        assert service._parse_headers("") == {}

    def test_invalid_json(self, service):
        assert service._parse_headers("not json") == {}


class TestExtractJsonPath:
    def test_simple_path(self, service):
        data = {"state": "ON"}
        assert service._extract_json_path(data, "state") == "ON"

    def test_nested_path(self, service):
        data = {"data": {"power": {"current": 42.5}}}
        assert service._extract_json_path(data, "data.power.current") == 42.5

    def test_missing_path(self, service):
        data = {"state": "ON"}
        assert service._extract_json_path(data, "missing") is None

    def test_empty_path(self, service):
        assert service._extract_json_path({"a": 1}, "") is None

    def test_none_path(self, service):
        assert service._extract_json_path({"a": 1}, None) is None


class TestTurnOn:
    @pytest.mark.asyncio
    async def test_turn_on_success(self, service, mock_plug):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(service, "_send_request", new_callable=AsyncMock, return_value=mock_response):
            result = await service.turn_on(mock_plug)

        assert result is True

    @pytest.mark.asyncio
    async def test_turn_on_failure(self, service, mock_plug):
        with patch.object(service, "_send_request", new_callable=AsyncMock, return_value=None):
            result = await service.turn_on(mock_plug)

        assert result is False

    @pytest.mark.asyncio
    async def test_turn_on_no_url(self, service, mock_plug):
        mock_plug.rest_on_url = None
        result = await service.turn_on(mock_plug)
        assert result is False


class TestTurnOff:
    @pytest.mark.asyncio
    async def test_turn_off_success(self, service, mock_plug):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(service, "_send_request", new_callable=AsyncMock, return_value=mock_response):
            result = await service.turn_off(mock_plug)

        assert result is True

    @pytest.mark.asyncio
    async def test_turn_off_no_url(self, service, mock_plug):
        mock_plug.rest_off_url = None
        result = await service.turn_off(mock_plug)
        assert result is False


class TestGetStatus:
    @pytest.mark.asyncio
    async def test_status_on(self, service, mock_plug):
        mock_response = MagicMock()
        mock_response.json.return_value = {"state": "ON"}

        with patch.object(service, "_send_request", new_callable=AsyncMock, return_value=mock_response):
            result = await service.get_status(mock_plug)

        assert result["state"] == "ON"
        assert result["reachable"] is True

    @pytest.mark.asyncio
    async def test_status_off(self, service, mock_plug):
        mock_response = MagicMock()
        mock_response.json.return_value = {"state": "OFF"}

        with patch.object(service, "_send_request", new_callable=AsyncMock, return_value=mock_response):
            result = await service.get_status(mock_plug)

        assert result["state"] == "OFF"
        assert result["reachable"] is True

    @pytest.mark.asyncio
    async def test_status_unreachable(self, service, mock_plug):
        with patch.object(service, "_send_request", new_callable=AsyncMock, return_value=None):
            result = await service.get_status(mock_plug)

        assert result["state"] is None
        assert result["reachable"] is False

    @pytest.mark.asyncio
    async def test_status_no_url(self, service, mock_plug):
        mock_plug.rest_status_url = None
        result = await service.get_status(mock_plug)

        assert result["state"] is None
        assert result["reachable"] is True  # No URL = assume reachable


class TestGetEnergy:
    @pytest.mark.asyncio
    async def test_energy_with_paths(self, service, mock_plug):
        mock_response = MagicMock()
        mock_response.json.return_value = {"power": 42.5, "energy": {"today": 1.23}}

        with patch.object(service, "_send_request", new_callable=AsyncMock, return_value=mock_response):
            result = await service.get_energy(mock_plug)

        assert result["power"] == 42.5
        assert result["today"] == 1.23

    @pytest.mark.asyncio
    async def test_energy_no_status_url_no_separate_urls(self, service, mock_plug):
        """No URLs at all (status=None, power_url=None, energy_url=None) → None."""
        mock_plug.rest_status_url = None
        mock_plug.rest_power_url = None
        mock_plug.rest_energy_url = None
        result = await service.get_energy(mock_plug)
        assert result is None

    @pytest.mark.asyncio
    async def test_energy_no_paths(self, service, mock_plug):
        mock_plug.rest_power_path = None
        mock_plug.rest_energy_path = None
        result = await service.get_energy(mock_plug)
        assert result is None

    @pytest.mark.asyncio
    async def test_energy_with_separate_urls(self, service, mock_plug):
        """Power and energy fetched from different URLs."""
        mock_plug.rest_power_url = "http://192.168.1.50:8087/power"
        mock_plug.rest_energy_url = "http://192.168.1.50:8087/energy"

        power_response = MagicMock()
        power_response.json.return_value = {"power": 9.5}
        energy_response = MagicMock()
        energy_response.json.return_value = {"energy": {"today": 30947.07}}

        call_count = 0

        async def mock_send(url, method="GET", headers=None, body=None):
            nonlocal call_count
            call_count += 1
            if "power" in url:
                return power_response
            return energy_response

        with patch.object(service, "_send_request", side_effect=mock_send):
            result = await service.get_energy(mock_plug)

        assert call_count == 2
        assert result["power"] == 9.5
        assert result["today"] == 30947.07

    @pytest.mark.asyncio
    async def test_energy_with_multipliers(self, service, mock_plug):
        """Multipliers convert units (e.g., Wh → kWh)."""
        mock_plug.rest_energy_multiplier = 0.001  # Wh → kWh

        mock_response = MagicMock()
        mock_response.json.return_value = {"power": 9.5, "energy": {"today": 30947.07}}

        with patch.object(service, "_send_request", new_callable=AsyncMock, return_value=mock_response):
            result = await service.get_energy(mock_plug)

        assert result["power"] == 9.5  # No multiplier (default 1.0)
        assert result["today"] == pytest.approx(30.94707)  # 30947.07 * 0.001

    @pytest.mark.asyncio
    async def test_energy_separate_url_falls_back_to_status(self, service, mock_plug):
        """When no separate URL is set, falls back to status URL."""
        mock_plug.rest_power_url = None
        mock_plug.rest_energy_url = None

        mock_response = MagicMock()
        mock_response.json.return_value = {"power": 42.5, "energy": {"today": 1.23}}

        with patch.object(service, "_send_request", new_callable=AsyncMock, return_value=mock_response):
            result = await service.get_energy(mock_plug)

        assert result["power"] == 42.5
        assert result["today"] == 1.23

    @pytest.mark.asyncio
    async def test_energy_no_urls_at_all(self, service, mock_plug):
        """No status URL and no separate URLs → None."""
        mock_plug.rest_status_url = None
        mock_plug.rest_power_url = None
        mock_plug.rest_energy_url = None

        result = await service.get_energy(mock_plug)
        assert result is None

    @pytest.mark.asyncio
    async def test_energy_deduplicates_same_url(self, service, mock_plug):
        """When power and energy both fall back to status URL, only one HTTP request is made."""
        mock_plug.rest_power_url = None
        mock_plug.rest_energy_url = None

        mock_response = MagicMock()
        mock_response.json.return_value = {"power": 42.5, "energy": {"today": 1.23}}

        with patch.object(service, "_send_request", new_callable=AsyncMock, return_value=mock_response) as mock_send:
            result = await service.get_energy(mock_plug)

        assert mock_send.call_count == 1
        assert result["power"] == 42.5
        assert result["today"] == 1.23


class TestTestConnection:
    @pytest.mark.asyncio
    async def test_connection_success(self, service):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await service.test_connection("http://192.168.1.50:8080/api")

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_connection_timeout(self, service):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await service.test_connection("http://192.168.1.50:8080/api")

        assert result["success"] is False
        assert "timed out" in result["error"]

    @pytest.mark.asyncio
    async def test_connection_invalid_url(self, service):
        result = await service.test_connection("http://127.0.0.1/api")
        assert result["success"] is False
        assert "blocked" in result["error"].lower()
