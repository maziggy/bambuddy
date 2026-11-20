"""{submitted_by} / {submitted_by_email} in print notifications.

The user who sent the job reaches the print start / complete / failed / stopped
templates, so a message can name them and a webhook bridge can turn the email
into a chat mention. The email only while an admin has turned it on.
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.app.core.auth import get_password_hash
from backend.app.models.user import User
from backend.app.services.notification_service import notification_service

pytestmark = pytest.mark.integration


async def _email_setting(db, enabled):
    from backend.app.models.settings import Settings

    db.add(Settings(key="notification_submitter_email_enabled", value="true" if enabled else "false"))
    await db.commit()


async def _user(db, username, email):
    user = User(username=username, email=email, password_hash=get_password_hash("Whatever1!"), is_active=True)
    db.add(user)
    await db.commit()
    return user.id


async def _variables(event, db, created_by_id):
    send = AsyncMock()
    archive_data = {"created_by_id": created_by_id}
    with patch.object(notification_service, "_send_to_providers", send):
        if event == "print_start":
            await notification_service.on_print_start(1, "X1C", {"filename": "part.3mf"}, db, archive_data=archive_data)
        else:
            await notification_service.on_print_complete(
                1, "X1C", "completed", {"filename": "part.3mf"}, db, archive_data=archive_data
            )
    send.assert_awaited_once()
    variables = send.await_args.kwargs["variables"]
    return {k: v for k, v in variables.items() if k.startswith("submitted_by")}


@pytest.fixture(autouse=True)
async def provider(notification_provider_factory):
    return await notification_provider_factory(name="Bridge", on_print_start=True, on_print_complete=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["print_start", "print_complete"])
async def test_the_submitter_and_their_email(event, db_session):
    await _email_setting(db_session, True)
    user_id = await _user(db_session, "alice", "alice@example.com")

    assert await _variables(event, db_session, user_id) == {
        "submitted_by": "alice",
        "submitted_by_email": "alice@example.com",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [None, False], ids=["unset", "off"])
async def test_the_email_stays_empty_unless_an_admin_turned_it_on(enabled, db_session):
    if enabled is not None:
        await _email_setting(db_session, enabled)
    user_id = await _user(db_session, "carol", "carol@example.com")

    assert await _variables("print_complete", db_session, user_id) == {"submitted_by": "carol"}


@pytest.mark.asyncio
async def test_a_submitter_without_an_email(db_session):
    await _email_setting(db_session, True)
    user_id = await _user(db_session, "bob", None)

    assert await _variables("print_complete", db_session, user_id) == {"submitted_by": "bob"}


@pytest.mark.asyncio
@pytest.mark.parametrize("created_by_id", [None, 9999], ids=["nobody", "user-gone"])
async def test_no_submitter_leaves_them_empty(created_by_id, db_session):
    assert await _variables("print_complete", db_session, created_by_id) == {}
