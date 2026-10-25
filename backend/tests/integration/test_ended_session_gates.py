"""An ended session's JWT is refused by every kind of route gate.

Each gate resolves the bearer token itself: the permission gate, the
ownership gate (``*_own`` / ``*_all``), the any-of gate and the admin gate. A
session ends at logout, at a password change (own or by an admin) and when
the user is deactivated; before that the same token passes every gate.

The API gateway (``auth_middleware``) refuses such a token before any route
runs, and most routes stack more than one gate, so a broken gate would not show
on a real route. Each gate is therefore also tried alone, on a route of its
own that the gateway lets through.

The session under test began 30 s ago: a token issued in the same second as a
password change passes on purpose (JWT ``iat`` is whole seconds).
"""

from datetime import timedelta

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import update

from backend.app.core import auth
from backend.app.models.user import User
from backend.app.utils.local_time import utcnow_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

PASSWORD = "Gatepass1!"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(client: AsyncClient, username: str, password: str) -> str:
    response = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _session_from_30_s_ago(username: str) -> str:
    issued = utcnow_naive() - timedelta(seconds=30)
    return jwt.encode(
        {"sub": username, "iat": issued, "exp": issued + timedelta(days=1), "jti": f"gates-{username}"},
        auth.SECRET_KEY,
        algorithm=auth.ALGORITHM,
    )


@pytest.fixture
async def users(async_client: AsyncClient, db_session) -> dict:
    """The first admin, and a second admin whose session the tests end."""
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "gateadmin", "admin_password": PASSWORD},
    )
    admin = await _login(async_client, "gateadmin", PASSWORD)
    created = await async_client.post(
        "/api/v1/users/",
        headers=_auth(admin),
        json={"username": "gateuser", "password": PASSWORD, "role": "admin"},
    )
    assert created.status_code == 201, created.text
    await db_session.execute(
        update(User)
        .where(User.username == "gateuser")
        .values(password_changed_at=utcnow_naive() - timedelta(minutes=1))
    )
    await db_session.commit()
    return {"admin": admin, "user_id": created.json()["id"], "jwt": _session_from_30_s_ago("gateuser")}


async def _gates(client: AsyncClient, token: str, name: str) -> dict[str, int]:
    headers = _auth(token)
    return {
        "permission": (await client.get("/api/v1/printers/", headers=headers)).status_code,
        "ownership": (await client.get("/api/v1/queue/", headers=headers)).status_code,
        "any-of": (await client.get("/api/v1/inventory/spools/by-tag?tag_uid=00", headers=headers)).status_code,
        "admin": (
            await client.post("/api/v1/groups/", headers=headers, json={"name": name, "permissions": []})
        ).status_code,
    }


async def _logout(client: AsyncClient, users: dict) -> None:
    assert (await client.post("/api/v1/auth/logout", headers=_auth(users["jwt"]))).status_code == 200


async def _own_password_change(client: AsyncClient, users: dict) -> None:
    response = await client.post(
        "/api/v1/users/me/change-password",
        headers=_auth(users["jwt"]),
        json={"current_password": PASSWORD, "new_password": "Changed1!pass"},
    )
    assert response.status_code == 200, response.text


async def _admin_password_change(client: AsyncClient, users: dict) -> None:
    response = await client.patch(
        f"/api/v1/users/{users['user_id']}", headers=_auth(users["admin"]), json={"password": "Changed1!pass"}
    )
    assert response.status_code == 200, response.text


async def _deactivation(client: AsyncClient, users: dict) -> None:
    response = await client.patch(
        f"/api/v1/users/{users['user_id']}", headers=_auth(users["admin"]), json={"is_active": False}
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("end_session", [_logout, _own_password_change, _admin_password_change, _deactivation])
async def test_every_gate_refuses_an_ended_session(async_client: AsyncClient, users, end_session):
    before = await _gates(async_client, users["jwt"], "before")
    assert all(code != 401 for code in before.values()), before

    await end_session(async_client, users)
    after = await _gates(async_client, users["jwt"], "after")

    assert after == dict.fromkeys(after, 401)


def _lone_gates():
    from fastapi import Depends

    from backend.app.core import auth as a
    from backend.app.core.permissions import Permission

    return {
        "gateway": None,
        "permission": a.RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
        "any-of": a.RequireAnyPermissionIfAuthEnabled(Permission.INVENTORY_READ, Permission.INVENTORY_UPDATE),
        "ownership": Depends(a.require_ownership_permission(Permission.QUEUE_READ_ALL, Permission.QUEUE_READ_OWN)),
        "admin": a.RequireAdminIfAuthEnabled(),
        "user": Depends(a.get_current_user),
        "optional user": Depends(a.get_current_user_optional),
        "auth if enabled": Depends(a.require_auth_if_enabled),
        "key scope": Depends(
            a.require_api_key_scope("can_update_energy_cost", "update_energy_cost", Permission.SETTINGS_UPDATE)
        ),
    }


LONE = "/api/v1/lone-gate-test/"


@pytest.fixture
def lone_gate_routes(monkeypatch):
    """One route per gate. All but "gateway" are let through by the gateway."""
    from backend.app import main

    routes = []
    for name, gate in _lone_gates().items():
        path = LONE + name.replace(" ", "-")
        main.app.add_api_route(path, lambda: {"ok": True}, dependencies=[gate] if gate else [])
        # Ahead of the SPA's catch-all route
        routes.append(main.app.router.routes.pop())
        main.app.router.routes.insert(0, routes[-1])
    monkeypatch.setattr(main, "PUBLIC_API_ROUTES", {*main.PUBLIC_API_ROUTES, *(r.path for r in routes[1:])})
    yield {name: route.path for name, route in zip(_lone_gates(), routes, strict=True)}
    for route in routes:
        main.app.router.routes.remove(route)


@pytest.mark.parametrize("end_session", [_logout, _own_password_change, _admin_password_change, _deactivation])
@pytest.mark.parametrize("gate", list(_lone_gates()))
async def test_each_gate_alone_refuses_an_ended_session(
    async_client: AsyncClient, users, lone_gate_routes, end_session, gate
):
    path = lone_gate_routes[gate]
    headers = _auth(users["jwt"])
    assert (await async_client.get(path, headers=headers)).status_code == 200

    await end_session(async_client, users)

    # get_current_user answers a deactivated account with 403 "disabled"
    assert (await async_client.get(path, headers=headers)).status_code in (401, 403)
