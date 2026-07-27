"""``is_jti_revoked`` must reuse the caller's session when given one (#2572).

The permission dependencies already hold a DB session when they check whether
a JWT's ``jti`` is revoked. The old ``is_jti_revoked`` always opened a *second*
``async_session``, so every authenticated request checked out two pooled
connections instead of one. On a large farm a burst of concurrent logins then
exhausted the pool (reporter @Jostxxl). Passing the existing session collapses
each request back to a single checkout.

These tests verify both that the revocation query is still correct and that a
provided session is genuinely reused (no second checkout), while the no-session
call still opens its own — the behaviour older callers rely on.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.core import auth as auth_mod
from backend.app.core.auth import is_jti_revoked
from backend.app.models.auth_ephemeral import AuthEphemeralToken


def _revoked_row(token: str) -> AuthEphemeralToken:
    return AuthEphemeralToken(
        token=token,
        token_type="revoked_jti",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


@pytest.mark.asyncio
async def test_revoked_jti_detected_via_provided_session(db_session):
    """A revoked jti is reported revoked when the caller passes its session."""
    db_session.add(_revoked_row("jti-revoked-1"))
    await db_session.commit()

    assert await is_jti_revoked("jti-revoked-1", db_session) is True


@pytest.mark.asyncio
async def test_unknown_jti_not_revoked_via_provided_session(db_session):
    assert await is_jti_revoked("jti-never-seen", db_session) is False


@pytest.mark.asyncio
async def test_provided_session_is_reused_not_a_second_checkout(db_session, monkeypatch):
    """PERF regression (#2572): passing ``db`` must NOT open a new session —
    that second checkout per request is the pool pressure we removed."""
    opened = {"n": 0}
    original = auth_mod.async_session

    def _counting(*args, **kwargs):
        opened["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(auth_mod, "async_session", _counting)

    assert await is_jti_revoked("jti-with-session", db_session) is False
    assert opened["n"] == 0, "is_jti_revoked opened its own session despite being given one"


@pytest.mark.asyncio
async def test_no_session_still_opens_its_own(test_engine, monkeypatch):
    """Callers that check the jti before they have a session (e.g. the token
    dependencies) must keep working — omitting ``db`` opens a short one."""
    test_async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async with test_async_session() as seed:
        seed.add(_revoked_row("jti-revoked-2"))
        await seed.commit()

    opened = {"n": 0}

    def _counting(*args, **kwargs):
        opened["n"] += 1
        return test_async_session(*args, **kwargs)

    monkeypatch.setattr(auth_mod, "async_session", _counting)

    assert await is_jti_revoked("jti-revoked-2") is True
    assert opened["n"] == 1, "is_jti_revoked should open exactly one session when none is provided"
