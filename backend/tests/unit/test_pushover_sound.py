"""Tests for the optional Pushover notification sound (#1637).

An empty or missing ``sound`` must leave the param out so the device plays
its own default sound; any other value is sent as Pushover's ``sound`` param.
"""

import httpx
import pytest

from backend.app.services.notification_service import NotificationService


class _CaptureClient:
    """Minimal stand-in for httpx.AsyncClient that records the posted data."""

    def __init__(self):
        self.is_closed = False
        self.last_data: dict | None = None

    async def post(self, url, data=None, files=None):
        self.last_data = data
        return httpx.Response(200, json={"status": 1})


@pytest.fixture
def service_with_capture():
    service = NotificationService()
    client = _CaptureClient()
    service._http_client = client  # bypass real HTTP
    return service, client


BASE_CONFIG = {"user_key": "u" * 30, "app_token": "a" * 30}


@pytest.mark.asyncio
async def test_sound_is_sent_when_set(service_with_capture):
    service, client = service_with_capture
    ok, _ = await service._send_pushover({**BASE_CONFIG, "sound": "cashregister"}, "T", "M")
    assert ok
    assert client.last_data["sound"] == "cashregister"


@pytest.mark.asyncio
async def test_sound_is_stripped(service_with_capture):
    service, client = service_with_capture
    ok, _ = await service._send_pushover({**BASE_CONFIG, "sound": "  siren "}, "T", "M")
    assert ok
    assert client.last_data["sound"] == "siren"


@pytest.mark.asyncio
@pytest.mark.parametrize("sound", [None, "", "   "])
async def test_device_default_omits_sound(service_with_capture, sound):
    service, client = service_with_capture
    config = dict(BASE_CONFIG)
    if sound is not None:
        config["sound"] = sound
    ok, _ = await service._send_pushover(config, "T", "M")
    assert ok
    assert "sound" not in client.last_data
