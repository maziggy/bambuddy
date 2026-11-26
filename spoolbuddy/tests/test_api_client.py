"""Tests for daemon.api_client — APIClient HTTP communication."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from daemon.api_client import MAX_BUFFER_SIZE, WRITE_RESULT_PATH, APIClient


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
    async def test_a_buffered_write_result_is_reported(self, api):
        # #3076: main must know a tag write's result is still unsent.
        api._client.post = AsyncMock(side_effect=httpx.ConnectError("down"))
        assert not api.has_buffered(WRITE_RESULT_PATH)

        await api.write_tag_result("dev-1", 7, "04A1", True, "ok")

        assert api.has_buffered(WRITE_RESULT_PATH)
        assert not api.has_buffered("/heartbeat")

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


def _status_error(code: int, detail: str | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://localhost:5000/x")
    body = {"json": {"detail": detail}} if detail is not None else {}
    return httpx.HTTPStatusError("rejected", request=request, response=httpx.Response(code, request=request, **body))


class TestTagScanned:
    @pytest.mark.asyncio
    async def test_sends_raw_blocks(self, api):
        api._post = AsyncMock(return_value={"status": "ok"})
        raw = {"1": "4130302D423038004746413030000000", "9": "36B6C12DB3DA4A39A50C275F510BD399"}

        await api.tag_scanned("sb-1", "865947E2", tray_uuid="36B6C12DB3DA4A39A50C275F510BD399", raw_blocks=raw)

        path, payload = api._post.await_args.args
        assert path == "/nfc/tag-scanned"
        assert payload["raw_blocks"] == raw
        assert payload["tray_uuid"] == "36B6C12DB3DA4A39A50C275F510BD399"

    @pytest.mark.asyncio
    async def test_raw_blocks_default_to_none(self, api):
        api._post = AsyncMock(return_value={"status": "ok"})

        await api.tag_scanned("sb-1", "04A1B2C3D4E5F6")

        _, payload = api._post.await_args.args
        assert payload["raw_blocks"] is None
        assert payload["ndef_hex"] is None

    @pytest.mark.asyncio
    async def test_sends_ndef_hex(self, api):
        """An NTAG's data area goes to the backend to decode (#3657)."""
        api._post = AsyncMock(return_value={"status": "ok"})

        await api.tag_scanned("sb-1", "04A1B2C3D4E5F6", sak=0, tag_type="ntag", ndef_hex="0300")

        _, payload = api._post.await_args.args
        assert payload["ndef_hex"] == "0300"


class TestRejectedRequests:
    """A request the backend refuses is not a connection problem: buffering it
    replays it forever, and the flush stops at the first item that fails."""

    @pytest.mark.asyncio
    async def test_a_heartbeat_for_an_unregistered_device_is_not_buffered(self, api):
        resp = MagicMock()
        resp.raise_for_status = MagicMock(side_effect=_status_error(404, "Device not registered"))
        api._client.post = AsyncMock(return_value=resp)

        result = await api.heartbeat(device_id="dev-1", nfc_ok=True, scale_ok=True, uptime_s=1)

        assert result is None
        assert len(api._buffer) == 0
        assert api.unregistered is True

    @pytest.mark.asyncio
    async def test_a_proxys_bare_404_is_not_taken_for_an_unregistered_device(self, api):
        """A reverse proxy answers 404 while Bambuddy restarts."""
        resp = MagicMock()
        resp.raise_for_status = MagicMock(side_effect=_status_error(404))
        api._client.post = AsyncMock(return_value=resp)

        await api.heartbeat(device_id="dev-1", nfc_ok=True, scale_ok=True, uptime_s=1)

        assert api.unregistered is False

    @pytest.mark.asyncio
    async def test_a_connection_error_is_still_buffered(self, api):
        api._client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        await api.heartbeat(device_id="dev-1", nfc_ok=True, scale_ok=True, uptime_s=1)

        assert len(api._buffer) == 1
        assert api.unregistered is False

    @pytest.mark.asyncio
    async def test_a_rejected_buffered_item_does_not_block_the_rest(self, api):
        api._buffer.append({"path": "/devices/dev-1/heartbeat", "data": {}})
        api._buffer.append({"path": "/scale/reading", "data": {"w": 1}})
        rejected = MagicMock(raise_for_status=MagicMock(side_effect=_status_error(404)))
        accepted = MagicMock(raise_for_status=MagicMock())
        api._client.post = AsyncMock(side_effect=[rejected, accepted])

        await api._flush_buffer()

        assert len(api._buffer) == 0
        assert api._client.post.await_count == 2


@pytest.fixture
def receiver(api):
    """Record only POSTs that reached the backend, in arrival order."""
    received = []
    online = False

    async def post(url, *, json):
        if not online:
            raise httpx.ConnectError("offline")
        received.append((url.removeprefix(api._base), json))
        return httpx.Response(200, json={"status": "ok"}, request=httpx.Request("POST", url))

    def reconnect():
        nonlocal online
        online = True

    api._client.post = post
    return received, reconnect


@pytest.mark.asyncio
@pytest.mark.parametrize("offline", [False, True], ids=["connected", "reconnect"])
async def test_tag_and_scale_after_two_heartbeats(api, receiver, offline):
    received, reconnect = receiver
    if not offline:
        reconnect()
    await api.tag_scanned("dev", "04A1")
    await api.scale_reading("dev", 250, True)
    reconnect()
    await api.tag_removed("dev", "04A1")
    await api.scale_reading("dev", 0, True)
    for uptime in (10, 20):
        await api.heartbeat("dev", True, True, uptime)

    tags = [(path, data["tag_uid"]) for path, data in received if path.startswith("/nfc/")]
    weights = [data["weight_grams"] for path, data in received if path == "/scale/reading"]
    scanned = [] if offline else [("/nfc/tag-scanned", "04A1")]
    assert tags == [*scanned, ("/nfc/tag-removed", "04A1")]
    assert weights == ([0] if offline else [250, 0])


@pytest.mark.asyncio
async def test_buffer_keeps_latest_state_per_device_and_group(api, receiver):
    received, reconnect = receiver
    for device in ("first", "second"):
        await api.tag_removed(device, "old")
        await api.tag_scanned(device, "new")
        await api.scale_reading(device, 250, False)
        await api.scale_reading(device, 300, True)
        await api.heartbeat(device, False, False, 1)
        await api.update_tare(device, 10)
        await api.update_tare(device, 20)

    reconnect()
    await api.update_tare("first", 30)
    await api.heartbeat("first", True, True, 2)
    assert [(path, data["uptime_s"]) for path, data in received if path.endswith("/heartbeat")] == [
        ("/devices/first/heartbeat", 2),
        ("/devices/second/heartbeat", 1),
    ]
    assert [(path, data["tare_offset"]) for path, data in received if path.endswith("/set-tare")] == [
        ("/devices/first/calibration/set-tare", 30),
        ("/devices/second/calibration/set-tare", 20),
    ]
    assert [(path, data["device_id"], data["tag_uid"]) for path, data in received if path.startswith("/nfc/")] == [
        ("/nfc/tag-scanned", "first", "new"),
        ("/nfc/tag-scanned", "second", "new"),
    ]
    assert [(data["device_id"], data["weight_grams"]) for path, data in received if path == "/scale/reading"] == [
        ("first", 300),
        ("second", 300),
    ]


@pytest.mark.asyncio
async def test_live_results_follow_buffered_results(api, receiver):
    received, reconnect = receiver
    for attempt in ("first", "second", "third"):
        if attempt == "third":
            reconnect()
        await api.write_tag_result("dev", 7, "04A1", False, attempt)
        await api.tag_scanned("dev", "04A1")
        await api.diagnostic_result("dev", "nfc", True, attempt, 0)
        await api.system_command_result("dev", "restart_browser", True, attempt)

    await api.heartbeat("dev", True, True, 3)
    assert [(path, data.get("message", data.get("output"))) for path, data in received if path.endswith("result")] == [
        (path, attempt)
        for attempt in ("first", "second", "third")
        for path in (WRITE_RESULT_PATH, "/diagnostics/dev/result", "/devices/dev/system/command-result")
    ]
    assert not api.has_buffered(WRITE_RESULT_PATH)


@pytest.mark.asyncio
async def test_live_removal_during_replay_keeps_next_write_result(api, receiver):
    received, reconnect = receiver
    await api.tag_scanned("dev", "04A1")
    await api.write_tag_result("dev", 7, "04A1", True)
    reconnect()
    post = api._client.post
    replay_started = asyncio.Event()
    resume = asyncio.Event()

    async def delay_scan(url, *, json):
        if url.endswith("/tag-scanned"):
            replay_started.set()
            await resume.wait()
        return await post(url, json=json)

    api._client.post = delay_scan
    replay = asyncio.create_task(api.heartbeat("dev", True, True, 1))
    await replay_started.wait()
    removal = asyncio.create_task(api.tag_removed("dev", "04A1"))
    await asyncio.sleep(0)  # Let the removal attempt its POST while the scan is in flight.
    resume.set()
    await asyncio.gather(replay, removal)
    assert [path for path, _ in received if path.startswith("/nfc/tag-")] == ["/nfc/tag-scanned", "/nfc/tag-removed"]
    assert [data["spool_id"] for path, data in received if path == WRITE_RESULT_PATH] == [7]


@pytest.mark.asyncio
async def test_command_result_can_be_sent_before_reboot_after_a_partial_flush(api, receiver):
    received, reconnect = receiver
    await api.write_tag_result("dev", 7, "04A1", True)
    await api.diagnostic_result("dev", "nfc", True, "ok", 0)
    reconnect()
    post = api._client.post

    async def fail_diagnostic(url, *, json):
        if url.endswith("/result"):
            return httpx.Response(503, request=httpx.Request("POST", url))
        return await post(url, json=json)

    api._client.post = fail_diagnostic
    await api.heartbeat("dev", True, True, 1)
    assert [path for path, _ in received if path.endswith("result")] == [WRITE_RESULT_PATH]

    api._client.post = post
    # The daemon can reboot as soon as this returns. There is no next heartbeat.
    assert await api.system_command_result("dev", "reboot", True, "executing") == {"status": "ok"}
    assert [path for path, _ in received if path.endswith("result")] == [
        WRITE_RESULT_PATH,
        "/diagnostics/dev/result",
        "/devices/dev/system/command-result",
    ]


@pytest.mark.asyncio
async def test_refused_live_reading_does_not_replay_older_weight(api, receiver):
    received, reconnect = receiver
    await api.scale_reading("dev", 250, True)
    reconnect()
    post = api._client.post
    api._client.post = AsyncMock(return_value=httpx.Response(422, request=httpx.Request("POST", api._base)))
    assert await api.scale_reading("dev", 0, True) is None
    api._client.post = post
    await api.heartbeat("dev", True, True, 1)
    assert [path for path, data in received] == ["/devices/dev/heartbeat"]
