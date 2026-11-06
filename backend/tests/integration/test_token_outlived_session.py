"""A one-time token minted before its user's session began is refused.

Deleting or renaming a user drops the tokens pending under the name in its
transaction; one minted in the milliseconds around it (a WebSocket token from
the old user's still-valid JWT, a forgot-password link) kept the name and
worked for whoever was given it next. Every token named after a user is now
refused if it was minted before that user's password_changed_at, which a new
user, a rename, a password change or reset, a 2FA removal and a restore set.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from backend.app.core import auth
from backend.app.models.auth_ephemeral import AuthEphemeralToken, TokenType

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def carol(async_client: AsyncClient, db_session) -> int:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "tokadmin", "admin_password": "AdminPass1!"},
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": "tokadmin", "password": "AdminPass1!"})
    admin = {"Authorization": f"Bearer {login.json()['access_token']}"}
    groups = (await async_client.get("/api/v1/groups/", headers=admin)).json()
    viewers = next(g["id"] for g in groups if g["name"] == "Viewers")
    user = await async_client.post(
        "/api/v1/users/",
        headers=admin,
        json={"username": "carol", "password": "Userpass1!", "role": "user", "group_ids": [viewers]},
    )
    carol_id = user.json()["id"]
    # Carol's current session began 30 minutes ago (a rename, a new account...).
    await db_session.execute(
        text("UPDATE users SET password_changed_at = :t WHERE id = :id"),
        {"t": _ago(minutes=30), "id": carol_id},
    )
    await db_session.commit()
    return carol_id


def _ago(**delta) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(**delta)


async def _token(db, kind: str, *, minted_ago_minutes: int, **fields) -> str:
    value = f"{kind}-{minted_ago_minutes}"
    db.add(
        AuthEphemeralToken(
            token=value,
            token_type=kind,
            username=fields.pop("username", "carol"),
            created_at=_ago(minutes=minted_ago_minutes),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            **fields,
        )
    )
    await db.commit()
    return value


@pytest.mark.parametrize(
    ("kind", "verify"),
    [
        ("websocket", lambda t: auth.verify_websocket_token_principal(t)),
        ("camera_stream", lambda t: auth.camera_stream_access(t)),
        ("media", lambda t: auth.verify_media_token(t)),
    ],
)
async def test_a_reusable_token_from_before_the_session_is_refused(carol, db_session, kind, verify):
    stale = await _token(db_session, kind, minted_ago_minutes=60)
    fresh = await _token(db_session, kind, minted_ago_minutes=10)

    assert await verify(stale) is None
    assert await verify(fresh) is not None


async def test_a_pre_auth_token_from_before_the_session_is_refused(carol, db_session):
    from backend.app.api.routes.mfa import consume_pre_auth_token, peek_pre_auth_token

    stale = await _token(db_session, TokenType.PRE_AUTH, minted_ago_minutes=60)
    fresh = await _token(db_session, TokenType.PRE_AUTH, minted_ago_minutes=10)

    assert await peek_pre_auth_token(db_session, stale) is None
    assert await consume_pre_auth_token(db_session, stale) is None
    assert await consume_pre_auth_token(db_session, fresh) == "carol"


async def test_a_reset_link_from_before_the_session_is_refused(async_client: AsyncClient, carol, db_session):
    stale = await _token(db_session, TokenType.PASSWORD_RESET, minted_ago_minutes=60)

    response = await async_client.post(
        "/api/v1/auth/forgot-password/confirm", json={"token": stale, "new_password": "Newpass12!"}
    )

    assert response.status_code == 400, response.text


async def test_an_oidc_exchange_token_from_before_the_session_is_refused(async_client: AsyncClient, carol, db_session):
    stale = await _token(db_session, TokenType.OIDC_EXCHANGE, minted_ago_minutes=60)

    response = await async_client.post("/api/v1/auth/oidc/exchange", json={"oidc_token": stale})

    assert response.status_code == 401, response.text


async def test_an_email_otp_setup_token_from_before_the_session_is_refused(
    async_client: AsyncClient, carol, db_session
):
    from backend.app.api.routes.mfa import pwd_context

    stale = await _token(db_session, TokenType.EMAIL_OTP_SETUP, minted_ago_minutes=60, nonce=pwd_context.hash("123456"))
    login = await async_client.post("/api/v1/auth/login", json={"username": "carol", "password": "Userpass1!"})

    response = await async_client.post(
        "/api/v1/auth/2fa/email/enable/confirm",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        json={"setup_token": stale, "code": "123456"},
    )

    assert response.status_code == 400, response.text


async def test_a_connect_code_from_before_the_session_is_refused(async_client: AsyncClient, carol, db_session):
    from backend.tests.integration.test_connected_apps import _exchange, _pkce, _register

    login = await async_client.post("/api/v1/auth/login", json={"username": "tokadmin", "password": "AdminPass1!"})
    app = await _register(async_client, login.json()["access_token"])
    verifier, challenge = _pkce()
    code = "connect-code-stale"
    from backend.app.api.routes.connected_apps import _hash_code

    db_session.add(
        AuthEphemeralToken.new_connect_code(
            code_hash=_hash_code(code),
            username="carol",
            app_id=app["id"],
            code_challenge=challenge,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
    )
    await db_session.commit()
    await db_session.execute(
        text("UPDATE auth_ephemeral_tokens SET created_at = :t WHERE token = :h"),
        {"t": _ago(minutes=60), "h": _hash_code(code)},
    )
    await db_session.commit()

    response = await _exchange(async_client, app, code, verifier)

    assert response.status_code == 400, response.text


async def test_every_token_kind_named_after_a_user_is_covered():
    """A new kind must get the check (and a test above)."""
    covered = {
        TokenType.WEBSOCKET.value,
        TokenType.CAMERA_STREAM.value,
        TokenType.MEDIA.value,
        TokenType.PRE_AUTH.value,
        TokenType.PASSWORD_RESET.value,
        TokenType.OIDC_EXCHANGE.value,
        TokenType.EMAIL_OTP_SETUP.value,
        TokenType.CONNECT_CODE.value,
    }
    named_by_nobody = {
        TokenType.OIDC_STATE.value,  # before anyone is signed in
        TokenType.SLICER_DOWNLOAD.value,  # bound to a resource, not a user
        TokenType.REVOKED_JTI.value,  # the logout list, not a credential
    }
    assert {kind.value for kind in TokenType} - named_by_nobody <= covered


async def test_every_token_kind_in_the_code_is_in_the_enum():
    """A kind written as a bare string would escape the check above."""
    import re
    from pathlib import Path

    import backend.app as app_pkg

    root = Path(app_pkg.__file__).parent
    kinds = set()
    for path in root.rglob("*.py"):
        kinds.update(re.findall(r'token_type\s*(?:==|=)\s*"([a-z_]+)"', path.read_text()))
    kinds.discard("bearer")  # the OAuth token_type of a login response, not a stored kind
    assert kinds <= {kind.value for kind in TokenType}, kinds - {kind.value for kind in TokenType}


async def test_a_token_of_a_user_that_does_not_exist_is_refused(carol, db_session):
    """Callers look the user up again on their own: a recreate in between would
    otherwise be served as the new user."""
    stale = await _token(db_session, "media", minted_ago_minutes=10, username="ghost")

    assert await auth.verify_media_token(stale) is None


async def test_a_token_from_the_same_second_but_before_the_session_is_refused(carol, db_session):
    """Both timestamps keep microseconds; nothing writes a user and its first
    token in one flush, so there is no reason to round."""
    session_start = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=600000)
    await db_session.execute(
        text("UPDATE users SET password_changed_at = :t WHERE id = :id"), {"t": session_start, "id": carol}
    )
    db_session.add(
        AuthEphemeralToken(
            token="same-second",
            token_type="media",
            username="carol",
            created_at=session_start.replace(microsecond=300000),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    await db_session.commit()

    assert await auth.verify_media_token("same-second") is None


async def test_a_token_minted_as_the_session_ends_is_dated_by_its_session(carol, db_session):
    """A stolen JWT keeps minting while the owner changes the password: a mint
    checked before the change committed wrote its row after. The token counts
    from when the session that minted it began, so it is refused."""
    import jwt as pyjwt

    from backend.app.models.user import User

    issued = datetime.now(timezone.utc) - timedelta(minutes=20)  # the session began 20 min ago
    session_jwt = pyjwt.encode(
        {"sub": "carol", "iat": issued, "exp": issued + timedelta(days=1), "jti": "stolen"},
        auth.SECRET_KEY,
        algorithm=auth.ALGORITHM,
    )
    user = await db_session.get(User, carol)
    marker = auth._authenticated_user.set((session_jwt, user))
    try:
        token = await auth.create_websocket_token("carol")
    finally:
        auth._authenticated_user.reset(marker)
    # The password change, which started before the row was written, commits now.
    await db_session.execute(
        text("UPDATE users SET password_changed_at = :t WHERE id = :id"), {"t": _ago(minutes=5), "id": carol}
    )
    await db_session.commit()

    assert await auth.verify_websocket_token_principal(token) is None


async def test_a_keys_token_does_not_pass_to_the_next_key_given_its_id(carol, db_session):
    """Minted while the key was deleted: SQLite gives the id to the next key."""
    from backend.app.models.api_key import APIKey

    key = APIKey(name="old", key_hash="h", key_prefix="bb_old1")
    db_session.add(key)
    await db_session.commit()
    key_id = key.id
    ws = await auth.create_websocket_token(None, key_id, api_key_prefix="bb_old1")
    cam = await auth.create_camera_stream_token(api_key_id=key_id, api_key_prefix="bb_old1")
    await db_session.execute(text("DELETE FROM api_keys WHERE id = :id"), {"id": key_id})
    await db_session.commit()
    db_session.add(APIKey(id=key_id, name="new", key_hash="h2", key_prefix="bb_new1"))
    await db_session.commit()

    assert await auth.verify_websocket_token_principal(ws) is None
    assert await auth.camera_stream_access(cam) is None


async def test_a_long_lived_token_does_not_pass_to_the_next_user_given_its_id(carol, db_session):
    """Minted while its user was deleted: SQLite gives the id to the next user,
    and the token would stream with their printers for up to a year."""
    from backend.app.services.long_lived_tokens import create_token

    created = await create_token(db_session, user_id=carol, name="wall", expires_in_days=30, scope="camwall")
    await db_session.execute(
        text("UPDATE long_lived_tokens SET created_at = :t WHERE id = :id"),
        {"t": _ago(minutes=60), "id": created.record.id},
    )
    await db_session.execute(
        text("UPDATE users SET created_at = :t WHERE id = :id"), {"t": _ago(minutes=1), "id": carol}
    )
    await db_session.commit()  # carol now stands for the user created after the token

    assert await auth.verify_camwall_token(created.plaintext) is None
    assert await auth.camera_stream_access(created.plaintext) is None


async def _session_then_password_change(client: AsyncClient, db, carol_id: int, mint) -> str:
    """Sign carol in, mint through the real route a second later, then commit a
    password change dated between the session's start and the token's row.
    Returns the token the route handed out."""
    import asyncio

    login = await client.post("/api/v1/auth/login", json={"username": "carol", "password": "Userpass1!"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    await asyncio.sleep(1.1)  # the row is written in a later second than the session began
    token = await mint(headers)
    # The change commits just before the request wrote its row.
    changed = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(milliseconds=50)
    await db.execute(text("UPDATE users SET password_changed_at = :t WHERE id = :id"), {"t": changed, "id": carol_id})
    await db.commit()
    return token


@pytest.mark.parametrize(
    ("route", "verify"),
    [
        ("/api/v1/auth/ws-token", lambda t: auth.verify_websocket_token_principal(t)),
        ("/api/v1/auth/media-token", lambda t: auth.verify_media_token(t)),
        ("/api/v1/printers/camera/stream-token", lambda t: auth.camera_stream_access(t)),
    ],
)
async def test_a_token_minted_through_the_route_counts_from_its_session(
    async_client: AsyncClient, carol, db_session, route, verify
):
    async def mint(headers):
        response = await async_client.post(route, headers=headers)
        assert response.status_code == 200, response.text
        return response.json()["token"]

    token = await _session_then_password_change(async_client, db_session, carol, mint)

    assert await verify(token) is None


async def test_a_connect_code_counts_from_its_session(async_client: AsyncClient, carol, db_session):
    from backend.tests.integration.test_connected_apps import _authorize, _exchange, _pkce, _register

    login = await async_client.post("/api/v1/auth/login", json={"username": "tokadmin", "password": "AdminPass1!"})
    app = await _register(async_client, login.json()["access_token"])
    verifier, challenge = _pkce()
    codes = []

    async def mint(headers):
        response = await _authorize(async_client, headers["Authorization"].split()[1], app, challenge)
        assert response.status_code == 200, response.text
        codes.append(response.json()["code"])
        return codes[-1]

    code = await _session_then_password_change(async_client, db_session, carol, mint)

    assert (await _exchange(async_client, app, code, verifier)).status_code == 400


async def test_an_email_otp_setup_token_counts_from_its_session(async_client: AsyncClient, carol, db_session):
    from unittest.mock import AsyncMock, MagicMock, patch

    await db_session.execute(text("UPDATE users SET email = 'carol@example.com' WHERE id = :id"), {"id": carol})
    await db_session.commit()

    async def mint(headers):
        with (
            patch("backend.app.api.routes.mfa.get_smtp_settings", AsyncMock(return_value=MagicMock())),
            patch("backend.app.api.routes.mfa.send_email", MagicMock()),
        ):
            response = await async_client.post("/api/v1/auth/2fa/email/enable", headers=headers)
        assert response.status_code == 200, response.text
        return response.json()["setup_token"]

    setup = await _session_then_password_change(async_client, db_session, carol, mint)
    written = await db_session.execute(
        text("SELECT created_at FROM auth_ephemeral_tokens WHERE token = :t"), {"t": setup}
    )
    changed = await db_session.execute(text("SELECT password_changed_at FROM users WHERE id = :id"), {"id": carol})
    assert written.scalar_one() < changed.scalar_one()  # dated before the change, so refused on use


async def test_a_connect_code_from_the_grant_race_retry_counts_from_its_session(
    async_client: AsyncClient, carol, db_session
):
    """Two consents at once: the loser rolls back and mints the code again."""
    from unittest.mock import patch

    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.tests.integration.test_connected_apps import _authorize, _exchange, _pkce, _register

    login = await async_client.post("/api/v1/auth/login", json={"username": "tokadmin", "password": "AdminPass1!"})
    app = await _register(async_client, login.json()["access_token"])
    verifier, challenge = _pkce()
    real_commit = AsyncSession.commit
    raised = []

    async def commit_losing_the_grant_race(self):
        if not raised and any(getattr(o, "token_type", None) == TokenType.CONNECT_CODE for o in self.new):
            raised.append(1)
            raise IntegrityError("INSERT", {}, Exception("grant exists"))
        return await real_commit(self)

    async def mint(headers):
        with patch.object(AsyncSession, "commit", commit_losing_the_grant_race):
            response = await _authorize(async_client, headers["Authorization"].split()[1], app, challenge)
        assert response.status_code == 200, response.text
        return response.json()["code"]

    code = await _session_then_password_change(async_client, db_session, carol, mint)

    assert raised, "the retry path was not taken"
    assert (await _exchange(async_client, app, code, verifier)).status_code == 400
