"""Tests for ``services/bambu_cloud_credentials`` — the credential seam.

The read paths are covered indirectly by the cloud-token expiry and
migration suites; these pin the write path that review blocker 5 hinged on:
``mark_cloud_token_invalid`` must record a rejection for *both* identity
shapes, because auth-disabled single-user installs (the default) hold their
token in global ``Settings`` — ``user_id=None`` is a real, expected input,
not a degenerate one.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select

from backend.app.core.auth import get_password_hash
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.services import bambu_cloud_credentials as creds
from backend.app.services.bambu_cloud_credentials import (
    CLOUD_TOKEN_INVALID_KEY,
    mark_cloud_token_invalid,
)

pytestmark = pytest.mark.asyncio


class _SharedSessionCtx:
    """Route ``mark`` through the fixture's in-memory session: the function
    normally opens its own session against the configured database, which in
    tests is a different SQLite than ``db_session``'s in-memory one."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def shared_session(db_session, monkeypatch):
    monkeypatch.setattr(creds, "async_session", lambda: _SharedSessionCtx(db_session))


async def _make_user(db, username: str = "cred-user") -> User:
    user = User(
        username=username,
        password_hash=get_password_hash("AdminPass1!"),
        role="admin",
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def test_mark_sets_the_per_user_flag(db_session):
    """user_id set → the rejection lands on that user's column."""
    user = await _make_user(db_session)

    await mark_cloud_token_invalid(user.id)
    await db_session.refresh(user)

    assert user.cloud_token_invalid_at is not None


async def test_mark_none_writes_the_global_settings_flag(db_session):
    """user_id=None (auth-disabled install) → the global ``Settings`` row.
    A second call updates the existing row rather than adding another."""
    await mark_cloud_token_invalid(None)

    result = await db_session.execute(select(Settings).where(Settings.key == CLOUD_TOKEN_INVALID_KEY))
    rows = result.scalars().all()
    assert len(rows) == 1
    # Stored value parses as ISO — the status endpoints compare it as a date.
    datetime.fromisoformat(rows[0].value)

    first_value = rows[0].value
    await mark_cloud_token_invalid(None)
    result = await db_session.execute(select(Settings).where(Settings.key == CLOUD_TOKEN_INVALID_KEY))
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].value >= first_value


async def test_mark_is_best_effort(db_session, monkeypatch):
    """A bookkeeping failure must never replace the 401 the caller needs to
    see — the function swallows everything."""

    class _Boom:
        async def __aenter__(self):
            raise RuntimeError("db gone")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(creds, "async_session", lambda: _Boom())

    # Must not raise.
    await mark_cloud_token_invalid(None)
