"""A deleted API key leaves nothing that the next key given its id would take.

SQLite gives a deleted key's id to the next key created. Open WebSockets opened
with the key, and the camera and WebSocket tokens it minted, are tied to
the key by id: they took the new key's printers.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocketDisconnect
from httpx import AsyncClient
from sqlalchemy import select, text

from backend.app.core.websocket import ws_manager
from backend.app.models.api_key import APIKey
from backend.app.models.auth_ephemeral import AuthEphemeralToken

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def admin(async_client: AsyncClient) -> dict:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "keyadmin", "admin_password": "AdminPass1!"},
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": "keyadmin", "password": "AdminPass1!"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _key(db, user_id: int | None = None) -> int:
    key = APIKey(name="k", key_hash="h", key_prefix="bb_k", printer_ids=[1], user_id=user_id)
    db.add(key)
    await db.commit()
    return key.id


async def _tokens(db, key_id: int) -> None:
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    db.add_all(
        [
            AuthEphemeralToken(
                token=f"{kind}-{key_id}", token_type=kind, username="", api_key_id=key_id, expires_at=expires
            )
            for kind in ("camera_stream", "websocket")  # media tokens never record the key
        ]
    )
    await db.commit()


async def _token_keys(db) -> list:
    rows = await db.execute(
        select(AuthEphemeralToken.api_key_id)
        .where(AuthEphemeralToken.api_key_id.is_not(None))
        .execution_options(populate_existing=True)
    )
    return sorted(rows.scalars().all())


def _socket(api_key_id: int | None) -> MagicMock:
    socket = MagicMock()
    socket.state = SimpleNamespace(
        bambuddy_scope_principal=("", api_key_id), bambuddy_principal_user_id=None, bambuddy_principal=""
    )
    socket.accept = AsyncMock()
    socket.close = AsyncMock()
    socket.send_json = AsyncMock()
    socket.receive_json = AsyncMock(side_effect=WebSocketDisconnect())
    return socket


async def test_deleting_a_key_closes_its_sockets_and_drops_its_tokens(async_client: AsyncClient, admin, db_session):
    gone, kept = await _key(db_session), await _key(db_session)
    await _tokens(db_session, gone)
    await _tokens(db_session, kept)
    theirs, other = _socket(gone), _socket(kept)
    ws_manager.active_connections.extend([theirs, other])
    try:
        response = await async_client.delete(f"/api/v1/api-keys/{gone}", headers=admin)

        assert response.status_code == 200, response.text
        assert await _token_keys(db_session) == [kept] * 2
        theirs.close.assert_awaited_once_with(code=4401)
        assert theirs not in ws_manager.active_connections
        other.close.assert_not_awaited()
    finally:
        for socket in (theirs, other):
            if socket in ws_manager.active_connections:
                ws_manager.active_connections.remove(socket)


async def test_deleting_a_user_does_the_same_for_their_keys(async_client: AsyncClient, admin, db_session):
    created = await async_client.post(
        "/api/v1/users/", headers=admin, json={"username": "keyowner", "password": "Userpass1!", "role": "user"}
    )
    owner = created.json()["id"]
    key = await _key(db_session, user_id=owner)
    await _tokens(db_session, key)
    theirs = _socket(key)
    ws_manager.active_connections.append(theirs)
    try:
        response = await async_client.delete(f"/api/v1/users/{owner}", headers=admin)

        assert response.status_code == 204, response.text
        assert await _token_keys(db_session) == []
        theirs.close.assert_awaited_once_with(code=4401)
    finally:
        if theirs in ws_manager.active_connections:
            ws_manager.active_connections.remove(theirs)


async def _connect(key_id: int, connect=None) -> MagicMock:
    from backend.app.api.routes import websocket as ws_route
    from backend.app.core import database
    from backend.app.core.printer_scope import ALL_PRINTERS

    socket = _socket(None)
    socket.state = SimpleNamespace()
    with (
        patch.object(ws_route, "is_auth_enabled", AsyncMock(return_value=True)),
        patch.object(ws_route, "verify_websocket_token_principal", AsyncMock(return_value=("", key_id))),
        patch.object(ws_route, "principal_printer_scope", AsyncMock(return_value=ALL_PRINTERS)),
        patch.object(ws_route, "async_session", database.async_session),
        patch.object(ws_manager, "connect", connect or ws_manager.connect),
    ):
        await ws_route.websocket_endpoint(socket, token="t")
    return socket


async def test_a_websocket_of_a_deleted_key_is_refused(async_client: AsyncClient):
    socket = await _connect(9999)

    socket.close.assert_awaited_once_with(code=4401)
    socket.accept.assert_not_awaited()


async def test_a_key_deleted_while_its_websocket_joins_closes_it(async_client: AsyncClient, db_session):
    key = await _key(db_session)
    joined = ws_manager.connect

    async def join_while_deleted(websocket):
        await db_session.execute(text("DELETE FROM api_keys WHERE id = :id"), {"id": key})
        await db_session.commit()
        await ws_manager.close_api_keys([key])  # what the delete does after its commit
        await joined(websocket)

    socket = await _connect(key, join_while_deleted)

    socket.close.assert_awaited_once_with(code=4401)
    assert socket not in ws_manager.active_connections


async def test_the_startup_cleanup_drops_tokens_of_keys_that_are_gone(db_session):
    from backend.app.core.database import _drop_ephemeral_tokens_of_missing_owners

    kept = await _key(db_session)
    await _tokens(db_session, kept)
    await _tokens(db_session, kept + 50)  # deleted before deletes took them along

    await _drop_ephemeral_tokens_of_missing_owners(await db_session.connection())
    await db_session.commit()

    assert await _token_keys(db_session) == [kept] * 2


async def test_a_deleted_keys_message_rate_goes_too(async_client: AsyncClient, admin, db_session):
    """The next key given the id would start out throttled."""
    from backend.app.api.routes import notifications

    key = await _key(db_session)
    notifications._app_message_times[f"key:{key}"].extend([1.0] * notifications.APP_MESSAGE_LIMIT)
    try:
        response = await async_client.delete(f"/api/v1/api-keys/{key}", headers=admin)

        assert response.status_code == 200, response.text
        assert f"key:{key}" not in notifications._app_message_times
    finally:
        notifications._app_message_times.pop(f"key:{key}", None)


@pytest.mark.parametrize("replaced", [False, True])
async def test_a_scope_refresh_closes_a_socket_whose_key_is_gone(async_client: AsyncClient, db_session, replaced):
    """Rotated from the command line, or an id that came free later: the socket
    was re-scoped by id, taking the printers of whichever key had it next."""
    from backend.app.core import database

    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "keyadmin2", "admin_password": "AdminPass1!"},
    )
    key = await _key(db_session)
    prefix = (await db_session.execute(select(APIKey.key_prefix).where(APIKey.id == key))).scalar_one()
    socket = _socket(key)
    socket.state.bambuddy_api_key_prefix = prefix
    ws_manager.active_connections.append(socket)
    await db_session.execute(text("DELETE FROM api_keys WHERE id = :id"), {"id": key})
    await db_session.commit()
    if replaced:  # a new key given the freed id
        db_session.add(APIKey(id=key, name="new", key_hash="h2", key_prefix="bb_new", printer_ids=None))
        await db_session.commit()
    try:
        with patch("backend.app.core.database.async_session", database.async_session):
            await ws_manager.refresh_printer_scopes()

        socket.close.assert_awaited_once_with(code=4401)
        assert socket not in ws_manager.active_connections
    finally:
        if socket in ws_manager.active_connections:
            ws_manager.active_connections.remove(socket)


async def test_a_deleted_users_message_rate_goes_too(async_client: AsyncClient, admin):
    """The next user given the id would start out throttled."""
    from backend.app.api.routes import notifications

    created = await async_client.post(
        "/api/v1/users/", headers=admin, json={"username": "sender", "password": "Userpass1!", "role": "user"}
    )
    user = created.json()["id"]
    notifications._app_message_times[f"user:{user}"].extend([1.0] * notifications.APP_MESSAGE_LIMIT)
    try:
        response = await async_client.delete(f"/api/v1/users/{user}", headers=admin)

        assert response.status_code == 204, response.text
        assert f"user:{user}" not in notifications._app_message_times
    finally:
        notifications._app_message_times.pop(f"user:{user}", None)
