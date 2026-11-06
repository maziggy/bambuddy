"""Taking websocket:connect away ends the live connection it allowed.

Only minting a WebSocket token checked the permission: the handshake and the
scope refresh did not. A user whose group lost it kept their open dashboard
(their printers' live status and every event of no printer), and their token
reconnected for an hour.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocketDisconnect
from httpx import AsyncClient

from backend.app.core import database
from backend.app.core.printer_scope import ALL_PRINTERS
from backend.app.core.websocket import ws_manager
from backend.app.models.api_key import APIKey

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def admin(async_client: AsyncClient) -> dict:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "wsadmin", "admin_password": "AdminPass1!"},
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": "wsadmin", "password": "AdminPass1!"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _carol_in_group(client: AsyncClient, admin: dict) -> tuple[int, int]:
    group = await client.post(
        "/api/v1/groups/", headers=admin, json={"name": "Live", "permissions": ["websocket:connect", "printers:read"]}
    )
    assert group.status_code == 201, group.text
    user = await client.post(
        "/api/v1/users/",
        headers=admin,
        json={"username": "carol", "password": "Userpass1!", "role": "user", "group_ids": [group.json()["id"]]},
    )
    assert user.status_code == 201, user.text
    return group.json()["id"], user.json()["id"]


def _socket(username: str | None, user_id: int | None, key_id: int | None = None, prefix: str | None = None):
    socket = MagicMock()
    socket.state = SimpleNamespace(
        bambuddy_scope_principal=(username if username else "", key_id),
        bambuddy_principal_user_id=user_id,
        bambuddy_api_key_prefix=prefix,
        bambuddy_printer_scope=ALL_PRINTERS,
    )
    socket.close = AsyncMock()
    return socket


@pytest.mark.parametrize("keeps", [False, True])
async def test_a_group_losing_the_permission_closes_its_users_sockets(async_client: AsyncClient, admin, keeps):
    group, carol = await _carol_in_group(async_client, admin)
    socket = _socket("carol", carol)
    ws_manager.active_connections.append(socket)
    permissions = ["websocket:connect", "printers:read"] if keeps else ["printers:read"]
    try:
        with patch("backend.app.core.database.async_session", database.async_session):
            response = await async_client.patch(
                f"/api/v1/groups/{group}", headers=admin, json={"permissions": permissions}
            )

        assert response.status_code == 200, response.text
        if keeps:
            socket.close.assert_not_awaited()
        else:
            socket.close.assert_awaited_once_with(code=4401)
    finally:
        if socket in ws_manager.active_connections:
            ws_manager.active_connections.remove(socket)


async def test_a_user_removed_from_the_granting_group_loses_the_socket(async_client: AsyncClient, admin):
    group, carol = await _carol_in_group(async_client, admin)
    socket = _socket("carol", carol)
    ws_manager.active_connections.append(socket)
    try:
        response = await async_client.delete(f"/api/v1/groups/{group}/users/{carol}", headers=admin)

        assert response.status_code == 204, response.text
        socket.close.assert_awaited_once_with(code=4401)
    finally:
        if socket in ws_manager.active_connections:
            ws_manager.active_connections.remove(socket)


async def _connect(principal: tuple):
    from backend.app.api.routes import websocket as ws_route

    socket = MagicMock()
    socket.state = SimpleNamespace()
    socket.accept = AsyncMock()
    socket.close = AsyncMock()
    socket.send_json = AsyncMock()
    socket.receive_json = AsyncMock(side_effect=WebSocketDisconnect())
    with (
        patch.object(ws_route, "is_auth_enabled", AsyncMock(return_value=True)),
        patch.object(ws_route, "verify_websocket_token_principal", AsyncMock(return_value=principal)),
        patch.object(ws_route, "principal_printer_scope", AsyncMock(return_value=ALL_PRINTERS)),
        patch.object(ws_route, "async_session", database.async_session),
    ):
        await ws_route.websocket_endpoint(socket, token="t")
    return socket


async def test_a_token_whose_user_lost_the_permission_is_refused(async_client: AsyncClient, admin):
    """Minted while the user had it, still valid for an hour."""
    group, _ = await _carol_in_group(async_client, admin)
    await async_client.patch(f"/api/v1/groups/{group}", headers=admin, json={"permissions": ["printers:read"]})

    socket = await _connect(("carol", None))

    socket.close.assert_awaited_once_with(code=4401)
    socket.accept.assert_not_awaited()


async def test_a_token_of_a_key_without_status_access_is_refused(async_client: AsyncClient, admin, db_session):
    key = APIKey(name="k", key_hash="h", key_prefix="bb_nostat", can_read_status=False)
    db_session.add(key)
    await db_session.commit()

    socket = await _connect(("", key.id))

    socket.close.assert_awaited_once_with(code=4401)
    socket.accept.assert_not_awaited()


async def test_a_key_losing_status_access_closes_its_sockets(async_client: AsyncClient, admin, db_session):
    key = APIKey(name="k", key_hash="h", key_prefix="bb_stat")
    db_session.add(key)
    await db_session.commit()
    socket = _socket(None, None, key_id=key.id, prefix="bb_stat")
    ws_manager.active_connections.append(socket)
    try:
        with patch("backend.app.core.database.async_session", database.async_session):
            response = await async_client.patch(
                f"/api/v1/api-keys/{key.id}", headers=admin, json={"can_read_status": False}
            )

        assert response.status_code == 200, response.text
        socket.close.assert_awaited_once_with(code=4401)
    finally:
        if socket in ws_manager.active_connections:
            ws_manager.active_connections.remove(socket)


async def test_the_permission_taken_while_a_socket_joins_closes_it(async_client: AsyncClient, admin):
    """The group change's refresh ran before the socket was listed."""
    from backend.app.api.routes import websocket as ws_route

    await _carol_in_group(async_client, admin)
    calls = []
    real = ws_route.principal_may_connect

    async def held_at_connect_then_gone(db, username, key_id):
        calls.append(1)
        return await real(db, username, key_id) if len(calls) == 1 else False

    with patch.object(ws_route, "principal_may_connect", held_at_connect_then_gone):
        socket = await _connect(("carol", None))

    socket.close.assert_awaited_once_with(code=4401)
    assert socket not in ws_manager.active_connections
