"""Tests for Pushover emergency-priority (2) retry/expire handling (#2586).

Pushover rejects a priority-2 (Emergency) message unless it also carries
``retry`` and ``expire``. These tests pin that we send those params at
priority 2 (clamped to Pushover's legal 30..10800 range) and omit them at
every other priority.
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
async def test_priority_2_includes_retry_and_expire(service_with_capture):
    service, client = service_with_capture
    ok, _ = await service._send_pushover({**BASE_CONFIG, "priority": 2, "retry": 90, "expire": 7200}, "T", "M")
    assert ok
    assert client.last_data["priority"] == 2
    assert client.last_data["retry"] == 90
    assert client.last_data["expire"] == 7200


@pytest.mark.asyncio
async def test_priority_2_uses_defaults_when_unset(service_with_capture):
    service, client = service_with_capture
    ok, _ = await service._send_pushover({**BASE_CONFIG, "priority": 2}, "T", "M")
    assert ok
    assert client.last_data["retry"] == 60
    assert client.last_data["expire"] == 3600


@pytest.mark.asyncio
async def test_priority_2_clamps_to_pushover_limits(service_with_capture):
    service, client = service_with_capture
    ok, _ = await service._send_pushover({**BASE_CONFIG, "priority": 2, "retry": 5, "expire": 999999}, "T", "M")
    assert ok
    assert client.last_data["retry"] == 30  # min 30
    assert client.last_data["expire"] == 10800  # max 10800


@pytest.mark.asyncio
async def test_priority_2_tolerates_string_values(service_with_capture):
    service, client = service_with_capture
    ok, _ = await service._send_pushover({**BASE_CONFIG, "priority": "2", "retry": "120", "expire": "1800"}, "T", "M")
    assert ok
    assert client.last_data["priority"] == 2
    assert client.last_data["retry"] == 120
    assert client.last_data["expire"] == 1800


@pytest.mark.asyncio
@pytest.mark.parametrize("priority", [-2, -1, 0, 1])
async def test_non_emergency_priority_omits_retry_and_expire(service_with_capture, priority):
    service, client = service_with_capture
    ok, _ = await service._send_pushover({**BASE_CONFIG, "priority": priority, "retry": 90, "expire": 7200}, "T", "M")
    assert ok
    assert "retry" not in client.last_data
    assert "expire" not in client.last_data
