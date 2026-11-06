"""A refused header's value must not reach a notification's stored error.

A Home Assistant token with a stray space makes httpx refuse the header and
quote its value. The send's error text went into the provider's last_error and
the notification log, which notification readers (Viewers, by default) read
through the API, and back to whoever ran a test notification. The token is an
admin-only setting.
"""

from __future__ import annotations

import socket

import pytest
from sqlalchemy import select

from backend.app.models.notification import NotificationLog, NotificationProvider
from backend.app.services.notification_service import NotificationService

# Made up. Never paste a value from a real log here.
SECRET = "ha-notify-value-7"


@pytest.fixture
def home_assistant(monkeypatch):
    """HA settings from the environment, pointed at a socket that accepts."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    monkeypatch.setenv("HA_URL", f"http://127.0.0.1:{server.getsockname()[1]}")
    monkeypatch.setenv("HA_TOKEN", f"{SECRET} ")
    yield
    server.close()


@pytest.mark.asyncio
async def test_a_failed_send_stores_no_header_value(db_session, home_assistant):
    provider = NotificationProvider(
        name="HA", provider_type="homeassistant", enabled=True, config="{}", attach_photo=False
    )
    db_session.add(provider)
    await db_session.commit()

    await NotificationService()._send_to_providers([provider], "Title", "Body", db_session, event_type="test")

    await db_session.refresh(provider)
    log = (await db_session.execute(select(NotificationLog))).scalar_one()
    assert provider.last_error and "Illegal header value [REDACTED]" in provider.last_error
    assert SECRET not in provider.last_error
    assert log.error_message and SECRET not in log.error_message


@pytest.mark.asyncio
async def test_a_test_notification_returns_no_header_value(db_session, home_assistant):
    success, message = await NotificationService().send_test_notification(
        "homeassistant", {}, db=db_session, attach_photo=False
    )

    assert success is False
    assert "Illegal header value [REDACTED]" in message
    assert SECRET not in message


@pytest.mark.asyncio
async def test_an_error_raised_out_of_a_send_is_stored_masked(db_session):
    """The dispatcher's own except stores str(e) as well."""
    provider = NotificationProvider(name="X", provider_type="webhook", enabled=True, config="{}")
    db_session.add(provider)
    await db_session.commit()
    service = NotificationService()

    async def _raise(*_args, **_kwargs):
        raise RuntimeError(f"Illegal header value b'Bearer {SECRET} '")

    service._send_to_provider = _raise
    await service._send_to_providers([provider], "Title", "Body", db_session, event_type="test")

    await db_session.refresh(provider)
    log = (await db_session.execute(select(NotificationLog))).scalar_one()
    assert SECRET not in (provider.last_error or "") and "[REDACTED]" in provider.last_error
    assert SECRET not in (log.error_message or "")
