"""Tests for optional Telegram forum-topic delivery via message_thread_id (#1518).

Telegram forum groups route messages to a topic by ``message_thread_id``.  The
field is optional: when it is absent, Telegram posts to the group's General
topic, which is the behaviour every existing install already relies on.

The subtlety worth pinning is the type.  ``sendMessage`` is posted as JSON, and
Telegram rejects a *string* thread id there, while the multipart ``sendPhoto``
call would accept one.  A string passed straight through would therefore work
for notifications carrying a thumbnail and 400 for plain-text ones — so these
tests assert an ``int`` reaches both call sites.
"""

import httpx
import pytest

from backend.app.services.notification_service import NotificationService


class _CaptureClient:
    """Stand-in for httpx.AsyncClient recording the JSON body and form data."""

    def __init__(self):
        self.is_closed = False
        self.calls: list[dict] = []

    async def post(self, url, data=None, files=None, json=None):
        self.calls.append({"url": url, "data": data, "files": files, "json": json})
        return httpx.Response(200, json={"ok": True, "result": {}})


@pytest.fixture
def service_with_capture():
    service = NotificationService()
    client = _CaptureClient()
    service._http_client = client  # bypass real HTTP
    return service, client


BASE_CONFIG = {"bot_token": "123456:AAbbCC", "chat_id": "-1002520100736"}
PNG = b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_thread_id_omitted_when_unset(service_with_capture):
    """Default config must produce exactly the pre-#1518 payload."""
    service, client = service_with_capture
    ok, _ = await service._send_telegram(BASE_CONFIG, "*T*\nbody")
    assert ok
    assert "message_thread_id" not in client.calls[0]["json"]


@pytest.mark.asyncio
async def test_blank_thread_id_is_treated_as_unset(service_with_capture):
    """An emptied-out form field must not turn into a bogus topic."""
    service, client = service_with_capture
    ok, _ = await service._send_telegram({**BASE_CONFIG, "message_thread_id": "   "}, "*T*\nbody")
    assert ok
    assert "message_thread_id" not in client.calls[0]["json"]


@pytest.mark.asyncio
async def test_sendmessage_carries_thread_id_as_int(service_with_capture):
    service, client = service_with_capture
    ok, _ = await service._send_telegram({**BASE_CONFIG, "message_thread_id": "25"}, "*T*\nbody")
    assert ok
    body = client.calls[0]["json"]
    assert body["message_thread_id"] == 25
    assert isinstance(body["message_thread_id"], int), "Telegram 400s on a string thread id in JSON"


@pytest.mark.asyncio
async def test_sendphoto_carries_thread_id(service_with_capture):
    """Thumbnail notifications take the multipart path and must route too."""
    service, client = service_with_capture
    ok, _ = await service._send_telegram({**BASE_CONFIG, "message_thread_id": "25"}, "*T*\nbody", image_data=PNG)
    assert ok
    call = client.calls[0]
    assert call["url"].endswith("/sendPhoto")
    assert call["data"]["message_thread_id"] == 25


@pytest.mark.asyncio
async def test_thread_id_accepts_native_int(service_with_capture):
    """config is a JSON blob — the value may already deserialise as an int."""
    service, client = service_with_capture
    ok, _ = await service._send_telegram({**BASE_CONFIG, "message_thread_id": 25}, "*T*\nbody")
    assert ok
    assert client.calls[0]["json"]["message_thread_id"] == 25


@pytest.mark.asyncio
async def test_non_numeric_thread_id_fails_without_sending(service_with_capture):
    """Reject locally rather than let Telegram answer with an opaque 400."""
    service, client = service_with_capture
    ok, error = await service._send_telegram({**BASE_CONFIG, "message_thread_id": "General"}, "*T*\nbody")
    assert not ok
    assert "not a number" in error
    assert client.calls == []


@pytest.mark.asyncio
async def test_error_message_does_not_leak_bot_token(service_with_capture):
    service, _ = service_with_capture
    ok, error = await service._send_telegram({**BASE_CONFIG, "message_thread_id": "oops"}, "*T*\nbody")
    assert not ok
    assert "AAbbCC" not in error
