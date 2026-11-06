"""A username given to a new account carries nothing of its earlier owner.

Sessions and one-time tokens name the user by username. A user created with a
deleted user's name, or renamed to it, accepted the deleted user's still-valid
JWTs (a new user had no password_changed_at, which accepts every token), and
the reset links, 2FA, camera and WebSocket tokens still pending under the name.
"""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from backend.app.core import auth
from backend.app.models.auth_ephemeral import AuthEphemeralToken
from backend.app.models.user import User

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

PENDING = (
    "pre_auth",
    "oidc_exchange",
    "password_reset",
    "email_otp_setup",
    "slicer_download",
    "connect_code",
    "camera_stream",
    "websocket",
)


@pytest.fixture
async def admin(async_client: AsyncClient) -> dict:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "nameadmin", "admin_password": "AdminPass1!"},
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": "nameadmin", "password": "AdminPass1!"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _old_session(username: str, issued: datetime) -> dict:
    """A JWT this server issued earlier for ``username``, still within its lifetime."""
    token = jwt.encode(
        {"sub": username, "exp": issued + timedelta(days=7), "jti": "old-" + username, "iat": issued},
        auth.SECRET_KEY,
        algorithm=auth.ALGORITHM,
    )
    return {"Authorization": f"Bearer {token}"}


async def _create(client: AsyncClient, admin: dict, username: str) -> int:
    response = await client.post(
        "/api/v1/users/", headers=admin, json={"username": username, "password": "Userpass1!", "role": "user"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _pending(db, username: str) -> None:
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    db.add_all(
        [
            AuthEphemeralToken(token=f"{kind}-{username}", token_type=kind, username=username, expires_at=expires)
            for kind in (*PENDING, "revoked_jti")
        ]
    )
    await db.commit()


async def _kinds(db, username: str) -> list[str]:
    rows = await db.execute(
        select(AuthEphemeralToken.token_type)
        .where(AuthEphemeralToken.username == username)
        .execution_options(populate_existing=True)
    )
    return sorted(rows.scalars().all())


async def test_a_recreated_user_rejects_the_deleted_namesakes_sessions(async_client: AsyncClient, admin):
    alice = await _create(async_client, admin, "alice")
    old = _old_session("alice", datetime.now(timezone.utc) - timedelta(minutes=5))
    assert (await async_client.delete(f"/api/v1/users/{alice}", headers=admin)).status_code == 204

    await _create(async_client, admin, "alice")

    assert (await async_client.get("/api/v1/auth/me", headers=old)).status_code == 401


async def test_a_new_user_records_when_it_started(db_session):
    """Every way of creating a user (admin, setup, LDAP, OIDC) goes through the model."""
    user = User(username="ldapuser", password_hash=None, role="user", auth_source="ldap")
    db_session.add(user)
    await db_session.commit()

    assert user.password_changed_at is not None


@pytest.mark.parametrize("name", ["alice", "Ärger"])
async def test_a_deleted_users_pending_tokens_go_but_logouts_stay(async_client: AsyncClient, admin, db_session, name):
    """A reset link, 2FA step, camera or WebSocket token of the deleted user would
    work for whoever is given the name next. The logout list keeps its entries,
    or a token logged out would work again."""
    # SQLite's lower() changes only ASCII letters: a name with an umlaut matched nothing.
    user = await _create(async_client, admin, name)
    await _pending(db_session, name)

    assert (await async_client.delete(f"/api/v1/users/{user}", headers=admin)).status_code == 204

    assert await _kinds(db_session, name) == ["revoked_jti"]


async def test_a_user_renamed_to_a_deleted_name_rejects_its_sessions(async_client: AsyncClient, admin, db_session):
    carol_session = _old_session("carol", datetime.now(timezone.utc) - timedelta(minutes=5))
    bob = await _create(async_client, admin, "bob")
    # bob has had his password for a while; carol's session is younger.
    await db_session.execute(
        text("UPDATE users SET password_changed_at = :t WHERE id = :id"),
        {"t": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30), "id": bob},
    )
    await db_session.commit()

    response = await async_client.patch(f"/api/v1/users/{bob}", headers=admin, json={"username": "carol"})

    assert response.status_code == 200, response.text
    assert (await async_client.get("/api/v1/auth/me", headers=carol_session)).status_code == 401


async def test_a_renamed_users_old_name_leaves_nothing_pending(async_client: AsyncClient, admin, db_session):
    """The next user given the old name would take over its reset link."""
    bob = await _create(async_client, admin, "bob")
    await _pending(db_session, "bob")

    response = await async_client.patch(f"/api/v1/users/{bob}", headers=admin, json={"username": "bobby"})

    assert response.status_code == 200, response.text
    assert await _kinds(db_session, "bob") == ["revoked_jti"]


async def test_the_startup_cleanup_drops_tokens_of_users_that_are_gone(db_session):
    """Deleted before deletes took them along."""
    from backend.app.core.database import _drop_ephemeral_tokens_of_missing_owners

    db_session.add(User(username="Dave", password_hash="x", role="user"))
    await db_session.commit()
    await _pending(db_session, "ghost")
    await _pending(db_session, "dave")  # names match without case
    db_session.add(
        AuthEphemeralToken(
            token="camera-key", token_type="camera_stream", username="", expires_at=datetime.now(timezone.utc)
        )
    )  # an API key's: no user
    await db_session.commit()

    await _drop_ephemeral_tokens_of_missing_owners(await db_session.connection())
    await db_session.commit()

    assert await _kinds(db_session, "ghost") == ["revoked_jti"]
    assert await _kinds(db_session, "dave") == sorted((*PENDING, "revoked_jti"))
    assert await _kinds(db_session, "") == ["camera_stream"]


async def test_a_renamed_users_open_connections_close(async_client: AsyncClient, admin):
    """An open WebSocket keeps the name it connected with and looks its printers
    up by it again on every scope refresh: the next user given the old name."""
    from unittest.mock import AsyncMock, patch

    bob = await _create(async_client, admin, "bob")

    with patch("backend.app.api.routes.users.ws_manager.close_user", AsyncMock()) as close:
        response = await async_client.patch(f"/api/v1/users/{bob}", headers=admin, json={"username": "bobby"})

    assert response.status_code == 200, response.text
    close.assert_awaited_once_with(bob)


async def test_a_rename_that_only_changes_case_counts_too(async_client: AsyncClient, admin, db_session):
    """SQLite compares only ASCII letters without case: 'Ärger' and 'ärger' are
    two names to it, so the old one can be given to someone else."""
    user = await _create(async_client, admin, "Ärger")
    await _pending(db_session, "Ärger")

    response = await async_client.patch(f"/api/v1/users/{user}", headers=admin, json={"username": "ärger"})

    assert response.status_code == 200, response.text
    assert await _kinds(db_session, "Ärger") == ["revoked_jti"]


@pytest.mark.parametrize("lookup_fails", [False, True])
async def test_a_websocket_whose_user_is_gone_by_connect_is_closed(async_client: AsyncClient, lookup_fails):
    """Renamed or deleted between the token check and the socket joining: the
    close by user id missed it, and it kept the old name for scope refreshes."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from fastapi import WebSocketDisconnect

    from backend.app.api.routes import websocket as ws_route
    from backend.app.core.printer_scope import ALL_PRINTERS
    from backend.app.core.websocket import ws_manager

    socket = MagicMock()
    socket.state = SimpleNamespace()
    socket.accept = AsyncMock()
    socket.close = AsyncMock()
    socket.send_json = AsyncMock()
    socket.receive_json = AsyncMock(side_effect=WebSocketDisconnect())
    from backend.app.core import database

    session = database.async_session  # the test database (the fixture patches it)
    if lookup_fails:
        calls = {"n": 0}

        def session_then_failure():
            calls["n"] += 1
            if calls["n"] >= 3:  # auth probe, scope, then the user lookup
                raise OSError("database gone")
            return session()

    with (
        patch.object(ws_route, "is_auth_enabled", AsyncMock(return_value=True)),
        patch.object(ws_route, "verify_websocket_token_principal", AsyncMock(return_value=("bob", None))),
        patch.object(ws_route, "principal_printer_scope", AsyncMock(return_value=ALL_PRINTERS)),
        patch.object(ws_route, "async_session", session_then_failure if lookup_fails else session),
    ):
        await ws_route.websocket_endpoint(socket, token="t")

    socket.close.assert_awaited_once_with(code=4401)
    assert socket not in ws_manager.active_connections


async def test_a_password_set_by_an_admin_ends_the_old_sessions(async_client: AsyncClient, admin, db_session):
    """An admin setting a password is often taking an account back: the
    attacker's JWT and a pending reset link must stop working."""
    bob = await _create(async_client, admin, "bob")
    session = _old_session("bob", datetime.now(timezone.utc) - timedelta(minutes=5))
    await db_session.execute(
        text("UPDATE users SET password_changed_at = :t WHERE id = :id"),
        {"t": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30), "id": bob},
    )
    await db_session.commit()
    await _pending(db_session, "bob")
    assert (await async_client.get("/api/v1/auth/me", headers=session)).status_code == 200

    response = await async_client.patch(f"/api/v1/users/{bob}", headers=admin, json={"password": "Newpass12!"})

    assert response.status_code == 200, response.text
    assert (await async_client.get("/api/v1/auth/me", headers=session)).status_code == 401
    assert await _kinds(db_session, "bob") == ["revoked_jti"]


async def test_a_rename_landing_while_a_websocket_joins_closes_it(async_client: AsyncClient, admin, db_session):
    """The id is read, then the socket joins the list. A rename committing in
    between closes the user's sockets by id before this one is listed."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from fastapi import WebSocketDisconnect

    from backend.app.api.routes import websocket as ws_route
    from backend.app.core import database
    from backend.app.core.printer_scope import ALL_PRINTERS
    from backend.app.core.websocket import ws_manager

    bob = await _create(async_client, admin, "bob")
    socket = MagicMock()
    socket.state = SimpleNamespace()
    socket.accept = AsyncMock()
    socket.close = AsyncMock()
    socket.send_json = AsyncMock()
    socket.receive_json = AsyncMock(side_effect=WebSocketDisconnect())
    joined = ws_manager.connect

    async def join_while_renamed(websocket):
        await db_session.execute(text("UPDATE users SET username = 'bobby' WHERE id = :id"), {"id": bob})
        await db_session.commit()
        await ws_manager.close_user(bob)  # what the rename does after its commit
        await joined(websocket)

    with (
        patch.object(ws_route, "is_auth_enabled", AsyncMock(return_value=True)),
        patch.object(ws_route, "verify_websocket_token_principal", AsyncMock(return_value=("bob", None))),
        patch.object(ws_route, "principal_printer_scope", AsyncMock(return_value=ALL_PRINTERS)),
        patch.object(ws_route, "async_session", database.async_session),
        patch.object(ws_manager, "connect", join_while_renamed),
    ):
        await ws_route.websocket_endpoint(socket, token="t")

    socket.close.assert_awaited_once_with(code=4401)
    assert socket not in ws_manager.active_connections


async def test_a_password_set_by_an_admin_closes_open_connections(async_client: AsyncClient, admin):
    """Open dashboards are not signed in again: they would be whoever held the account."""
    from unittest.mock import AsyncMock, patch

    bob = await _create(async_client, admin, "bob")

    with patch("backend.app.api.routes.users.ws_manager.close_user", AsyncMock()) as close:
        response = await async_client.patch(f"/api/v1/users/{bob}", headers=admin, json={"password": "Newpass12!"})

    assert response.status_code == 200, response.text
    close.assert_awaited_once_with(bob)
