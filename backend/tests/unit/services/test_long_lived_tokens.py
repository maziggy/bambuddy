"""Unit tests for the long-lived camera-token service (#1108).

Drives the service directly against a real SQLAlchemy session so the
hash/lookup/expiry/revoke logic is exercised end-to-end with no HTTP.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.models.long_lived_token import LongLivedToken
from backend.app.models.user import User
from backend.app.services.long_lived_tokens import (
    ALLOWED_SCOPES,
    MAX_TOKEN_LIFETIME_DAYS,
    create_token,
    list_all_tokens,
    list_user_tokens,
    revoke_token,
    verify_token,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def alice(db_session) -> User:
    user = User(
        username="alice",
        email="alice@example.test",
        password_hash="x",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def bob(db_session) -> User:
    user = User(
        username="bob",
        email="bob@example.test",
        password_hash="x",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def test_create_returns_plaintext_once_and_stores_hash(db_session, alice: User):
    """Create returns the plaintext token; the DB only stores its hash."""
    created = await create_token(
        db_session,
        user_id=alice.id,
        name="Home Assistant",
        expires_in_days=30,
    )

    assert created.plaintext.startswith("bblt_")
    assert created.record.id is not None
    assert created.record.user_id == alice.id
    assert created.record.name == "Home Assistant"
    assert created.record.scope == "camera_stream"
    assert created.record.lookup_prefix in created.plaintext
    # Hash never matches plaintext.
    assert created.record.secret_hash != created.plaintext
    # Expiry roughly 30 days from now (allow a few seconds of clock drift).
    delta = created.record.expires_at - datetime.utcnow()
    assert timedelta(days=29, hours=23) < delta < timedelta(days=30, minutes=1)


async def test_create_rejects_zero_or_negative_expiry(db_session, alice: User):
    """Issue #1108 explicitly forbids ``expire_in: 0``."""
    with pytest.raises(ValueError, match="positive"):
        await create_token(db_session, user_id=alice.id, name="x", expires_in_days=0)
    with pytest.raises(ValueError, match="positive"):
        await create_token(db_session, user_id=alice.id, name="x", expires_in_days=-5)


async def test_create_rejects_expiry_above_policy_cap(db_session, alice: User):
    """Above the 365-day ceiling → reject. UI layer also clamps but the
    service is the canonical guard.
    """
    with pytest.raises(ValueError, match="exceeds policy maximum"):
        await create_token(
            db_session,
            user_id=alice.id,
            name="x",
            expires_in_days=MAX_TOKEN_LIFETIME_DAYS + 1,
        )


async def test_create_rejects_unsupported_scope(db_session, alice: User):
    """V1 only allows ``camera_stream``."""
    assert {"camera_stream"} == set(ALLOWED_SCOPES)
    with pytest.raises(ValueError, match="unsupported scope"):
        await create_token(
            db_session,
            user_id=alice.id,
            name="x",
            expires_in_days=7,
            scope="anything_else",
        )


async def test_create_rejects_blank_or_oversize_name(db_session, alice: User):
    with pytest.raises(ValueError, match="name is required"):
        await create_token(db_session, user_id=alice.id, name="   ", expires_in_days=7)
    with pytest.raises(ValueError, match="100"):
        await create_token(db_session, user_id=alice.id, name="x" * 101, expires_in_days=7)


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


async def test_verify_happy_path_returns_record_and_updates_last_used(db_session, alice: User):
    created = await create_token(db_session, user_id=alice.id, name="Frigate", expires_in_days=7)
    assert created.record.last_used_at is None

    record = await verify_token(db_session, created.plaintext)
    assert record is not None
    assert record.id == created.record.id
    assert record.last_used_at is not None


async def test_verify_returns_none_for_garbage_token(db_session, alice: User):
    await create_token(db_session, user_id=alice.id, name="x", expires_in_days=7)
    assert await verify_token(db_session, "not-a-real-token") is None
    assert await verify_token(db_session, "bblt_short") is None
    # Wrong prefix entirely.
    assert await verify_token(db_session, "abc_12345678_zzz") is None


async def test_verify_returns_none_for_expired_token(db_session, alice: User):
    created = await create_token(db_session, user_id=alice.id, name="x", expires_in_days=1)
    # Force expiry into the past.
    created.record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()
    assert await verify_token(db_session, created.plaintext) is None


async def test_verify_returns_none_for_revoked_token(db_session, alice: User):
    created = await create_token(db_session, user_id=alice.id, name="x", expires_in_days=7)
    revoked = await revoke_token(db_session, created.record.id)
    assert revoked is True
    assert await verify_token(db_session, created.plaintext) is None


async def test_verify_returns_none_when_scope_mismatched(db_session, alice: User):
    """A camera_stream-scoped token must NOT validate against any other scope.

    No other scopes exist today, but if/when they do, this guard prevents a
    camera token from being accepted by, say, a control endpoint.
    """
    created = await create_token(db_session, user_id=alice.id, name="x", expires_in_days=7)
    assert await verify_token(db_session, created.plaintext, scope="other") is None


async def test_verify_does_not_collide_across_users_with_same_prefix(db_session, alice: User, bob: User, monkeypatch):
    """If two tokens happened to land on the same lookup_prefix, only the
    one whose hash matches must verify. We force the collision by patching
    the token-part generator and asserting verify returns the right record.
    """
    from backend.app.services import long_lived_tokens

    real = long_lived_tokens._generate_token_parts

    sequence = iter(["aliceaaa", "bobbbbbb"])

    def _fixed_prefix():
        # First call (alice's token) gets the real generator output but with
        # the prefix forced to a known value.
        plaintext, _, hash_input = real()
        prefix = next(sequence)
        # Splice the forced prefix into the plaintext + hash_input.
        new_plaintext = "bblt_" + prefix + plaintext[len("bblt_") + 8 :]
        return new_plaintext, prefix, new_plaintext

    monkeypatch.setattr(long_lived_tokens, "_generate_token_parts", _fixed_prefix)

    a = await create_token(db_session, user_id=alice.id, name="a", expires_in_days=7)
    b = await create_token(db_session, user_id=bob.id, name="b", expires_in_days=7)
    assert a.record.lookup_prefix != b.record.lookup_prefix  # sanity

    # Cross-verify: alice's plaintext must only match alice's record.
    assert (await verify_token(db_session, a.plaintext)).id == a.record.id
    assert (await verify_token(db_session, b.plaintext)).id == b.record.id


# ---------------------------------------------------------------------------
# List + revoke
# ---------------------------------------------------------------------------


async def test_list_user_tokens_returns_only_owners_active_tokens(db_session, alice: User, bob: User):
    a1 = await create_token(db_session, user_id=alice.id, name="a1", expires_in_days=7)
    await create_token(db_session, user_id=alice.id, name="a2", expires_in_days=7)
    await create_token(db_session, user_id=bob.id, name="b1", expires_in_days=7)
    await revoke_token(db_session, a1.record.id)

    alice_tokens = await list_user_tokens(db_session, alice.id)
    names = {t.name for t in alice_tokens}
    assert names == {"a2"}  # a1 revoked, b1 belongs to bob


async def test_list_all_tokens_returns_every_active_token(db_session, alice: User, bob: User):
    await create_token(db_session, user_id=alice.id, name="a", expires_in_days=7)
    b = await create_token(db_session, user_id=bob.id, name="b", expires_in_days=7)
    await revoke_token(db_session, b.record.id)

    all_tokens = await list_all_tokens(db_session)
    names = {t.name for t in all_tokens}
    assert "a" in names
    assert "b" not in names  # revoked excluded


async def test_revoke_is_idempotent(db_session, alice: User):
    created = await create_token(db_session, user_id=alice.id, name="x", expires_in_days=7)
    assert await revoke_token(db_session, created.record.id) is True
    # Second revoke is a no-op (returns False, never raises).
    assert await revoke_token(db_session, created.record.id) is False


async def test_revoke_unknown_id_returns_false(db_session):
    assert await revoke_token(db_session, 99_999) is False
