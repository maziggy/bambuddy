"""The daily digest is not lost (FO-C4).

The queued events were deleted whatever the send's outcome, so a failed send
lost the day's digest. A digest time inside the provider's quiet hours was
"skipped", logged as sent, and its events deleted too. It now goes out at its
time even in quiet hours -- that time was chosen for it, as plate-not-empty
alerts also ignore them -- and a failed send keeps the events for the next.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.notification import NotificationDigestQueue, NotificationProvider
from backend.app.services.notification_service import notification_service

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _provider_with_queue(db_session) -> int:
    provider = NotificationProvider(
        name="phone",
        provider_type="ntfy",
        enabled=True,
        config=json.dumps({"server": "https://ntfy.example", "topic": "t"}),
        daily_digest_enabled=True,
        daily_digest_time="07:00",
    )
    db_session.add(provider)
    await db_session.flush()
    for title in ("Print Completed", "Print Failed"):
        db_session.add(
            NotificationDigestQueue(provider_id=provider.id, event_type="print_complete", title=title, message=title)
        )
    await db_session.commit()
    return provider.id


async def _send_digest(
    test_engine, provider_id: int, send_result: tuple[bool, str], *, quiet_now: bool = False
) -> AsyncMock:
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    send = AsyncMock(return_value=send_result)
    with (
        patch("backend.app.core.database.async_session", maker),
        patch.object(notification_service, "_send_ntfy", send),
        patch.object(notification_service, "_is_in_quiet_hours", return_value=quiet_now),
    ):
        await notification_service.send_digest(provider_id)
    return send


async def _queued(db_session, provider_id: int) -> int:
    db_session.expire_all()
    rows = await db_session.scalars(
        select(NotificationDigestQueue).where(NotificationDigestQueue.provider_id == provider_id)
    )
    return len(rows.all())


async def test_a_failed_send_keeps_the_events_for_the_next_digest(test_engine, db_session):
    provider_id = await _provider_with_queue(db_session)

    send = await _send_digest(test_engine, provider_id, (False, "connection refused"))

    send.assert_awaited_once()
    assert await _queued(db_session, provider_id) == 2


async def test_a_digest_inside_quiet_hours_is_still_sent(test_engine, db_session):
    provider_id = await _provider_with_queue(db_session)

    send = await _send_digest(test_engine, provider_id, (True, ""), quiet_now=True)

    send.assert_awaited_once()
    assert await _queued(db_session, provider_id) == 0


async def test_a_sent_digest_clears_its_events(test_engine, db_session):
    provider_id = await _provider_with_queue(db_session)

    await _send_digest(test_engine, provider_id, (True, ""))

    assert await _queued(db_session, provider_id) == 0


async def test_events_kept_after_a_failure_are_retried_once_not_forever(test_engine, db_session):
    """A digest that always fails -- revoked credentials, or a busy day too
    long for Pushover's 1024 characters -- must not grow its queue without end:
    kept events get the next digest, and are dropped after that."""
    from datetime import datetime, timedelta

    provider_id = await _provider_with_queue(db_session)
    for hours_ago, title in ((50, "Three days ago"), (26, "Yesterday")):
        db_session.add(
            NotificationDigestQueue(
                provider_id=provider_id,
                event_type="print_complete",
                title=title,
                message=title,
                created_at=datetime.utcnow() - timedelta(hours=hours_ago),
            )
        )
    await db_session.commit()

    send = await _send_digest(test_engine, provider_id, (False, "message too long"))

    body = send.await_args.args[2]
    assert "Yesterday" in body
    assert "Three days ago" not in body
    # Yesterday's line says which day it was from.
    yesterday = (datetime.utcnow() - timedelta(hours=26)).strftime("%m-%d")
    assert f"{yesterday} " in body
    assert await _queued(db_session, provider_id) == 3
