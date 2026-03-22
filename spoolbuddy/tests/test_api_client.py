"""Tests for daemon.api_client — APIClient HTTP communication."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from daemon.api_client import MAX_BUFFER_SIZE, APIClient


@pytest.fixture
def api():
    return APIClient("http://localhost:5000", "test-key")


class TestAPIClientInit:
    def test_base_url_construction(self, api):
        assert api._base == "http://localhost:5000/api/v1/spoolbuddy"

    def test_base_url_strips_trailing_slash(self):
        client = APIClient("http://localhost:5000/", "key")
        assert client._base == "http://localhost:5000/api/v1/spoolbuddy"

    def test_api_key_in_headers(self):
        client = APIClient("http://localhost:5000", "my-key")
        assert client._headers == {"X-API-Key": "my-key"}

    def test_no_api_key_empty_headers(self):
        client = APIClient("http://localhost:5000", "")
        assert client._headers == {}


class TestPost:
    @pytest.mark.asyncio
    async def test_post_success(self, api):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}
        mock_resp.raise_for_status = MagicMock()

        api._client.post = AsyncMock(return_value=mock_resp)

        result = await api._post("/test", {"key": "value"})

        assert result == {"ok": True}
        assert api._connected is True
        assert api._backoff == 1.0
        api._client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_post_failure_buffers_request(self, api):
        api._client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        result = await api._post("/test", {"data": 1})

        assert result is None
        assert len(api._buffer) == 1
        assert api._buffer[0] == {"path": "/test", "data": {"data": 1}}

    @pytest.mark.asyncio
    async def test_post_failure_logs_connection_lost_once(self, api):
        api._connected = True
        api._client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        await api._post("/a", {})
        assert api._connected is False

        # Second failure should not log "connection lost" again
        await api._post("/b", {})
        assert len(api._buffer) == 2

    @pytest.mark.asyncio
    async def test_post_success_resets_backoff(self, api):
        api._backoff = 16.0
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status = MagicMock()
        api._client.post = AsyncMock(return_value=mock_resp)

        await api._post("/test", {})

        assert api._backoff == 1.0

    @pytest.mark.asyncio
    async def test_buffer_max_size(self, api):
        api._client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        for i in range(MAX_BUFFER_SIZE + 20):
            await api._post("/test", {"i": i})

        assert len(api._buffer) == MAX_BUFFER_SIZE
        # Oldest entries should have been dropped (deque maxlen behavior)
        assert api._buffer[0]["data"]["i"] == 20


class TestHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_posts_to_correct_path(self, api):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"pending_command": None}
        mock_resp.raise_for_status = MagicMock()
        api._client.post = AsyncMock(return_value=mock_resp)

        result = await api.heartbeat(
            device_id="dev-1",
            nfc_ok=True,
            scale_ok=False,
            uptime_s=120,
            ip_address="192.168.1.50",
            firmware_version="0.2.2b1",
        )

        assert result == {"pending_command": None}
        call_args = api._client.post.call_args
        assert "/devices/dev-1/heartbeat" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_heartbeat_flushes_buffer_on_success(self, api):
        # Pre-populate buffer
        api._buffer.append({"path": "/old", "data": {"x": 1}})

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_resp.raise_for_status = MagicMock()
        api._client.post = AsyncMock(return_value=mock_resp)

        await api.heartbeat(device_id="d", nfc_ok=True, scale_ok=True, uptime_s=0)

        # Buffer should be flushed (post called for heartbeat + 1 buffered item)
        assert len(api._buffer) == 0

    @pytest.mark.asyncio
    async def test_heartbeat_returns_none_on_failure(self, api):
        api._client.post = AsyncMock(side_effect=httpx.ConnectError("fail"))

        result = await api.heartbeat(device_id="d", nfc_ok=True, scale_ok=True, uptime_s=0)

        assert result is None


class TestRegisterDevice:
    @pytest.mark.asyncio
    async def test_register_retries_until_success(self, api):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"device_id": "dev-1"}
        mock_resp.raise_for_status = MagicMock()

        # Fail twice, then succeed
        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise httpx.ConnectError("refused")
            return mock_resp

        api._client.post = mock_post
        # Speed up retries
        api._backoff = 0.01
        api._max_backoff = 0.02

        result = await api.register_device(
            device_id="dev-1",
            hostname="test",
            ip_address="1.2.3.4",
        )

        assert result == {"device_id": "dev-1"}
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_register_sends_all_fields(self, api):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_resp.raise_for_status = MagicMock()
        api._client.post = AsyncMock(return_value=mock_resp)

        await api.register_device(
            device_id="dev-1",
            hostname="myhost",
            ip_address="10.0.0.1",
            firmware_version="0.2.2b1",
            has_nfc=True,
            has_scale=False,
            tare_offset=100,
            calibration_factor=1.05,
            nfc_reader_type="PN532",
            nfc_connection="SPI",
            has_backlight=True,
        )

        call_args = api._client.post.call_args
        payload = call_args[1]["json"]
        assert payload["device_id"] == "dev-1"
        assert payload["has_backlight"] is True
        assert payload["calibration_factor"] == 1.05


class TestReportUpdateStatus:
    @pytest.mark.asyncio
    async def test_report_update_status(self, api):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_resp.raise_for_status = MagicMock()
        api._client.post = AsyncMock(return_value=mock_resp)

        result = await api.report_update_status("dev-1", "updating", "Fetching...")

        assert result == {"ok": True}
        call_args = api._client.post.call_args
        assert "/devices/dev-1/update-status" in call_args[0][0]
        payload = call_args[1]["json"]
        assert payload["status"] == "updating"
        assert payload["message"] == "Fetching..."

    @pytest.mark.asyncio
    async def test_report_update_status_failure_returns_none(self, api):
        api._client.post = AsyncMock(side_effect=httpx.ConnectError("fail"))

        result = await api.report_update_status("dev-1", "error", "oops")

        assert result is None
