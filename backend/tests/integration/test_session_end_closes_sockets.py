"""Whatever ends a user's sessions also closes their open WebSockets.

A socket is authenticated once, at connect. A password change or reset, an
admin removing 2FA, or a backup restore makes every earlier JWT invalid, but a
dashboard someone else had open on the account kept receiving its printers'
status and events until the socket dropped.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from backend.app.models.auth_ephemeral import AuthEphemeralToken, TokenType

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

CLOSE_USER = "backend.app.core.websocket.ws_manager.close_user"


@pytest.fixture
async def admin(async_client: AsyncClient) -> dict:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "sockadmin", "admin_password": "AdminPass1!"},
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": "sockadmin", "password": "AdminPass1!"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _user(client: AsyncClient, admin: dict) -> int:
    response = await client.post(
        "/api/v1/users/", headers=admin, json={"username": "carol", "password": "Userpass1!", "role": "user"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_changing_ones_password_closes_ones_sockets(async_client: AsyncClient, admin):
    carol = await _user(async_client, admin)
    login = await async_client.post("/api/v1/auth/login", json={"username": "carol", "password": "Userpass1!"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    with patch(CLOSE_USER, AsyncMock()) as close:
        response = await async_client.post(
            "/api/v1/users/me/change-password",
            headers=headers,
            json={"current_password": "Userpass1!", "new_password": "Newpass12!"},
        )

    assert response.status_code == 200, response.text
    close.assert_awaited_once_with(carol)


async def test_a_reset_link_closes_the_sockets(async_client: AsyncClient, admin, db_session):
    carol = await _user(async_client, admin)
    db_session.add(
        AuthEphemeralToken(
            token="reset-carol",
            token_type=TokenType.PASSWORD_RESET,
            username="carol",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    await db_session.commit()

    with patch(CLOSE_USER, AsyncMock()) as close:
        response = await async_client.post(
            "/api/v1/auth/forgot-password/confirm", json={"token": "reset-carol", "new_password": "Newpass12!"}
        )

    assert response.status_code == 200, response.text
    close.assert_awaited_once_with(carol)


async def test_an_admin_removing_2fa_closes_the_sockets(async_client: AsyncClient, admin):
    carol = await _user(async_client, admin)

    with patch(CLOSE_USER, AsyncMock()) as close:
        response = await async_client.request(
            "DELETE", f"/api/v1/auth/2fa/admin/{carol}", headers=admin, json={"admin_password": "AdminPass1!"}
        )

    assert response.status_code == 200, response.text
    close.assert_awaited_once_with(carol)


def _socket(user_id=None, key_id=None):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    socket = MagicMock()
    socket.state = SimpleNamespace(
        bambuddy_principal_user_id=user_id,
        bambuddy_scope_principal=("someone" if user_id else ("" if key_id else None), key_id),
    )
    socket.close = AsyncMock()
    return socket


@pytest.mark.sqlite_only  # switches the app to its own SQLite database file
@pytest.mark.parametrize("auth_on", [True, False])
async def test_a_backup_restore_closes_the_signed_in_sockets(async_client: AsyncClient, monkeypatch, tmp_path, auth_on):
    """With auth on in the restored data, sessions end, and what the restored
    data no longer allows goes. With auth off nothing is closed: no sign-in is
    needed any more (the restore still invalidates every session), and everyone
    may see every printer."""
    from backend.app.api.routes.settings import create_backup_zip
    from backend.app.core.config import settings as app_settings
    from backend.app.core.websocket import ws_manager

    db_path = tmp_path / "bambuddy.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE backup_marker (id INTEGER PRIMARY KEY)")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_settings, "database_url", f"sqlite+aiosqlite:///{db_path}")
    zip_path, _ = await create_backup_zip(output_path=tmp_path)
    user, key, anonymous = _socket(user_id=5), _socket(key_id=3), _socket()
    ws_manager.active_connections.extend([user, key, anonymous])

    try:
        with (
            patch("backend.app.core.database.close_all_connections", new_callable=AsyncMock),
            patch("backend.app.core.database.reinitialize_database", new_callable=AsyncMock),
            patch("backend.app.core.database.init_db", new_callable=AsyncMock),
            patch("backend.app.core.database.invalidate_all_sessions", new_callable=AsyncMock),
            patch("backend.app.services.print_scheduler.scheduler.stop"),
            patch("backend.app.services.smart_plug_manager.smart_plug_manager.stop_scheduler"),
            patch("backend.app.services.notification_service.notification_service.stop_digest_scheduler"),
            patch("backend.app.api.routes.settings.is_auth_enabled", AsyncMock(return_value=auth_on)),
        ):
            response = await async_client.post(
                "/api/v1/settings/restore", files={"file": ("backup.zip", zip_path.read_bytes(), "application/zip")}
            )

        assert response.status_code == 200, response.text
        if auth_on:
            # The user's session ended, and the anonymous one has every printer.
            # (A key missing from the restored data goes at the scope refresh,
            # see test_api_key_id_reuse.)
            for socket in (user, anonymous):
                socket.close.assert_awaited_once_with(code=4401)
        else:  # auth off: everyone may see every printer, and no sign-in would reopen them
            for socket in (user, key, anonymous):
                socket.close.assert_not_awaited()
    finally:
        for socket in (user, key, anonymous):
            if socket in ws_manager.active_connections:
                ws_manager.active_connections.remove(socket)


@pytest.mark.sqlite_only  # switches the app to its own SQLite database file
async def test_a_restore_that_turns_authentication_on_closes_the_anonymous_sockets(
    async_client: AsyncClient, monkeypatch, tmp_path
):
    """The restore replaces the auth setting too: dashboards opened while it was
    off would keep every printer on an install that now needs a sign-in."""
    from backend.app.api.routes.settings import create_backup_zip
    from backend.app.core.config import settings as app_settings
    from backend.app.core.websocket import ws_manager

    db_path = tmp_path / "bambuddy.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE backup_marker (id INTEGER PRIMARY KEY)")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_settings, "database_url", f"sqlite+aiosqlite:///{db_path}")
    zip_path, _ = await create_backup_zip(output_path=tmp_path)
    anonymous = _socket()
    ws_manager.active_connections.append(anonymous)

    try:
        with (
            patch("backend.app.core.database.close_all_connections", new_callable=AsyncMock),
            patch("backend.app.core.database.reinitialize_database", new_callable=AsyncMock),
            patch("backend.app.core.database.init_db", new_callable=AsyncMock),
            patch("backend.app.core.database.invalidate_all_sessions", new_callable=AsyncMock),
            patch("backend.app.services.print_scheduler.scheduler.stop"),
            patch("backend.app.services.smart_plug_manager.smart_plug_manager.stop_scheduler"),
            patch("backend.app.services.notification_service.notification_service.stop_digest_scheduler"),
            patch("backend.app.api.routes.settings.is_auth_enabled", AsyncMock(return_value=True), create=True),
        ):
            response = await async_client.post(
                "/api/v1/settings/restore", files={"file": ("backup.zip", zip_path.read_bytes(), "application/zip")}
            )

        assert response.status_code == 200, response.text
        anonymous.close.assert_awaited_once_with(code=4401)
    finally:
        if anonymous in ws_manager.active_connections:
            ws_manager.active_connections.remove(anonymous)


async def _ws_tokens(db, username: str) -> list[str]:
    from sqlalchemy import select

    rows = await db.execute(
        select(AuthEphemeralToken.token_type)
        .where(AuthEphemeralToken.username == username, AuthEphemeralToken.token_type != "revoked_jti")
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars().all())


async def _give_tokens(db, username: str) -> None:
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    db.add_all(
        [
            AuthEphemeralToken(token=f"{kind}-{username}", token_type=kind, username=username, expires_at=expires)
            for kind in ("websocket", "camera_stream")
        ]
    )
    await db.commit()


async def test_change_password_drops_the_tokens_a_closed_dashboard_would_reconnect_with(
    async_client: AsyncClient, admin, db_session
):
    """A WebSocket token is reusable for an hour: closing the socket alone let
    whoever held it connect again straight away."""
    await _user(async_client, admin)
    login = await async_client.post("/api/v1/auth/login", json={"username": "carol", "password": "Userpass1!"})
    await _give_tokens(db_session, "carol")

    response = await async_client.post(
        "/api/v1/users/me/change-password",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        json={"current_password": "Userpass1!", "new_password": "Newpass12!"},
    )

    assert response.status_code == 200, response.text
    assert await _ws_tokens(db_session, "carol") == []


async def test_a_reset_link_drops_them_too(async_client: AsyncClient, admin, db_session):
    await _user(async_client, admin)
    await _give_tokens(db_session, "carol")
    db_session.add(
        AuthEphemeralToken(
            token="reset-carol2",
            token_type=TokenType.PASSWORD_RESET,
            username="carol",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    await db_session.commit()

    response = await async_client.post(
        "/api/v1/auth/forgot-password/confirm", json={"token": "reset-carol2", "new_password": "Newpass12!"}
    )

    assert response.status_code == 200, response.text
    assert await _ws_tokens(db_session, "carol") == []


async def test_an_admin_removing_2fa_drops_them_too(async_client: AsyncClient, admin, db_session):
    carol = await _user(async_client, admin)
    await _give_tokens(db_session, "carol")

    response = await async_client.request(
        "DELETE", f"/api/v1/auth/2fa/admin/{carol}", headers=admin, json={"admin_password": "AdminPass1!"}
    )

    assert response.status_code == 200, response.text
    assert await _ws_tokens(db_session, "carol") == []


async def test_deactivating_a_user_closes_their_sockets_and_drops_their_tokens(
    async_client: AsyncClient, admin, db_session
):
    """Their printers go at the next scope refresh, but events of no printer
    (inventory, SpoolBuddy tags, announcements) still reached them."""
    carol = await _user(async_client, admin)
    await _give_tokens(db_session, "carol")

    with patch(CLOSE_USER, AsyncMock()) as close:
        response = await async_client.patch(f"/api/v1/users/{carol}", headers=admin, json={"is_active": False})

    assert response.status_code == 200, response.text
    close.assert_awaited_once_with(carol)
    assert await _ws_tokens(db_session, "carol") == []


async def test_turning_authentication_on_closes_the_anonymous_sockets(async_client: AsyncClient):
    """Opened while auth was off, they had every printer and kept it."""
    from backend.app.core.websocket import ws_manager

    anonymous = _socket()
    ws_manager.active_connections.append(anonymous)
    try:
        response = await async_client.post(
            "/api/v1/auth/setup",
            json={"auth_enabled": True, "admin_username": "sockadmin3", "admin_password": "AdminPass1!"},
        )

        assert response.status_code == 200, response.text
        anonymous.close.assert_awaited_once_with(code=4401)
    finally:
        if anonymous in ws_manager.active_connections:
            ws_manager.active_connections.remove(anonymous)


async def test_deactivating_a_user_also_ends_their_keys_connections(async_client: AsyncClient, admin, db_session):
    """REST already refuses a deactivated owner's key; its open dashboards kept
    receiving the events of no printer, and its tokens opened new ones."""
    from backend.app.core.websocket import ws_manager
    from backend.app.models.api_key import APIKey

    carol = await _user(async_client, admin)
    key = APIKey(name="kiosk", key_hash="h", key_prefix="bb_kiosk", user_id=carol)
    db_session.add(key)
    await db_session.commit()
    db_session.add(
        AuthEphemeralToken(
            token="ws-kiosk",
            token_type="websocket",
            username="",
            api_key_id=key.id,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    await db_session.commit()
    socket = _socket(key_id=key.id)
    ws_manager.active_connections.append(socket)
    try:
        response = await async_client.patch(f"/api/v1/users/{carol}", headers=admin, json={"is_active": False})

        assert response.status_code == 200, response.text
        socket.close.assert_awaited_once_with(code=4401)
        from sqlalchemy import select

        left = await db_session.execute(
            select(AuthEphemeralToken.token)
            .where(AuthEphemeralToken.api_key_id == key.id)
            .execution_options(populate_existing=True)
        )
        assert left.scalars().all() == []
    finally:
        if socket in ws_manager.active_connections:
            ws_manager.active_connections.remove(socket)


async def _key_with_socket(db, **fields):
    from backend.app.core.websocket import ws_manager
    from backend.app.models.api_key import APIKey

    key = APIKey(name="kiosk", key_hash="h", key_prefix="bb_kiosk2", **fields)
    db.add(key)
    await db.commit()
    db.add(
        AuthEphemeralToken(
            token=f"ws-key-{key.id}",
            token_type="websocket",
            username="",
            api_key_id=key.id,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    await db.commit()
    socket = _socket(key_id=key.id)
    socket.state.bambuddy_api_key_prefix = "bb_kiosk2"
    ws_manager.active_connections.append(socket)
    return key.id, socket


async def test_disabling_a_key_ends_its_connections(async_client: AsyncClient, admin, db_session):
    """REST refuses it from then on; its dashboards kept the events of no printer
    and its token reconnected for an hour."""
    from sqlalchemy import select

    from backend.app.core.websocket import ws_manager

    key, socket = await _key_with_socket(db_session)
    try:
        response = await async_client.patch(f"/api/v1/api-keys/{key}", headers=admin, json={"enabled": False})

        assert response.status_code == 200, response.text
        socket.close.assert_awaited_once_with(code=4401)
        left = await db_session.execute(
            select(AuthEphemeralToken.token)
            .where(AuthEphemeralToken.api_key_id == key)
            .execution_options(populate_existing=True)
        )
        assert left.scalars().all() == []
    finally:
        if socket in ws_manager.active_connections:
            ws_manager.active_connections.remove(socket)


async def test_a_scope_refresh_closes_the_connections_of_an_expired_key(async_client: AsyncClient, admin, db_session):
    from backend.app.core import database
    from backend.app.core.websocket import ws_manager

    key, socket = await _key_with_socket(db_session, expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    try:
        with patch("backend.app.core.database.async_session", database.async_session):
            await ws_manager.refresh_printer_scopes()

        socket.close.assert_awaited_once_with(code=4401)
    finally:
        if socket in ws_manager.active_connections:
            ws_manager.active_connections.remove(socket)


async def _connect(principal: tuple, verify_results: list):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from fastapi import WebSocketDisconnect

    from backend.app.api.routes import websocket as ws_route
    from backend.app.core import database
    from backend.app.core.printer_scope import ALL_PRINTERS

    socket = MagicMock()
    socket.state = SimpleNamespace()
    socket.accept = AsyncMock()
    socket.close = AsyncMock()
    socket.send_json = AsyncMock()
    socket.receive_json = AsyncMock(side_effect=WebSocketDisconnect())
    with (
        patch.object(ws_route, "is_auth_enabled", AsyncMock(return_value=True)),
        patch.object(ws_route, "verify_websocket_token_principal", AsyncMock(side_effect=verify_results)),
        patch.object(ws_route, "principal_printer_scope", AsyncMock(return_value=ALL_PRINTERS)),
        patch.object(ws_route, "async_session", database.async_session),
    ):
        await ws_route.websocket_endpoint(socket, token="t")
    return socket


async def test_a_websocket_of_a_disabled_key_is_refused(async_client: AsyncClient, db_session):
    from backend.app.core.websocket import ws_manager
    from backend.app.models.api_key import APIKey

    key = APIKey(name="off", key_hash="h", key_prefix="bb_off", enabled=False)
    db_session.add(key)
    await db_session.commit()

    socket = await _connect(("", key.id), [("", key.id)] * 2)

    socket.close.assert_awaited_once_with(code=4401)
    socket.accept.assert_not_awaited()
    assert socket not in ws_manager.active_connections


async def test_a_session_ending_while_a_websocket_joins_closes_it(async_client: AsyncClient, admin):
    """The password change drops the tokens, then closes the user's sockets: one
    that checked its token before and joins after is missed by the close."""
    from backend.app.core.websocket import ws_manager

    await _user(async_client, admin)

    socket = await _connect(("carol", None), [("carol", None), None])  # gone by the second check

    socket.close.assert_awaited_once_with(code=4401)
    assert socket not in ws_manager.active_connections


async def test_turning_authentication_on_drops_the_tokens_minted_while_it_was_off(
    async_client: AsyncClient, db_session
):
    """Every tab mints one with auth off, naming nobody: it would reconnect the
    closed dashboard straight away (to the events of no printer)."""
    from sqlalchemy import select

    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    db_session.add_all(
        [
            AuthEphemeralToken(token="anon-ws", token_type="websocket", username="", expires_at=expires),
            AuthEphemeralToken(token="anon-cam", token_type="camera_stream", username="", expires_at=expires),
            AuthEphemeralToken(token="logged-out", token_type="revoked_jti", username="", expires_at=expires),
        ]
    )
    await db_session.commit()

    response = await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "sockadmin4", "admin_password": "AdminPass1!"},
    )

    assert response.status_code == 200, response.text
    left = await db_session.execute(select(AuthEphemeralToken.token).execution_options(populate_existing=True))
    assert sorted(left.scalars().all()) == ["logged-out"]


@pytest.mark.sqlite_only  # switches the app to its own SQLite database file
async def test_a_restore_reads_the_restored_auth_setting_not_the_cached_one(
    async_client: AsyncClient, monkeypatch, tmp_path
):
    """The restore request itself has just cached 'auth on' for 30 s."""
    from backend.app.api.routes.settings import create_backup_zip
    from backend.app.core import auth
    from backend.app.core.config import settings as app_settings

    db_path = tmp_path / "bambuddy.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE backup_marker (id INTEGER PRIMARY KEY)")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_settings, "database_url", f"sqlite+aiosqlite:///{db_path}")
    zip_path, _ = await create_backup_zip(output_path=tmp_path)
    calls = []
    real = auth.invalidate_auth_enabled_cache
    monkeypatch.setattr(auth, "invalidate_auth_enabled_cache", lambda: (calls.append(1), real())[1])
    with (
        patch("backend.app.core.database.close_all_connections", new_callable=AsyncMock),
        patch("backend.app.core.database.reinitialize_database", new_callable=AsyncMock),
        patch("backend.app.core.database.init_db", new_callable=AsyncMock),
        patch("backend.app.core.database.invalidate_all_sessions", new_callable=AsyncMock),
        patch("backend.app.services.print_scheduler.scheduler.stop"),
        patch("backend.app.services.smart_plug_manager.smart_plug_manager.stop_scheduler"),
        patch("backend.app.services.notification_service.notification_service.stop_digest_scheduler"),
    ):
        response = await async_client.post(
            "/api/v1/settings/restore", files={"file": ("backup.zip", zip_path.read_bytes(), "application/zip")}
        )

    assert response.status_code == 200, response.text
    assert calls, "the auth setting cache was not cleared after the restore"


async def test_authentication_turned_on_while_a_websocket_joins_closes_it(async_client: AsyncClient):
    """Admitted with auth off, it joined after the anonymous sockets were closed:
    nothing checked it again, and it kept every printer."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from fastapi import WebSocketDisconnect

    from backend.app.api.routes import websocket as ws_route
    from backend.app.core import database
    from backend.app.core.websocket import ws_manager

    socket = MagicMock()
    socket.state = SimpleNamespace()
    socket.accept = AsyncMock()
    socket.close = AsyncMock()
    socket.send_json = AsyncMock()
    socket.receive_json = AsyncMock(side_effect=WebSocketDisconnect())
    with (
        patch.object(ws_route, "is_auth_enabled", AsyncMock(side_effect=[False, True])),  # on by the second read
        patch.object(ws_route, "async_session", database.async_session),
    ):
        await ws_route.websocket_endpoint(socket, token=None)

    socket.close.assert_awaited_once_with(code=4401)
    assert socket not in ws_manager.active_connections
