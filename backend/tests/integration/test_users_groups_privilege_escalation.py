"""Privilege-escalation regression suite for the users/groups admin boundary.

The intent declared in ``permissions.py`` is that USERS_* / GROUPS_* are
admin-level capabilities — the comments literally say "(admin-level)".
The original implementation enforced ONLY the permission, not admin role.
Any user holding USERS_UPDATE (or USERS_CREATE / GROUPS_UPDATE /
GROUPS_CREATE) could grant themselves admin via the management routes.

This suite reproduces every attack vector from the disclosure and pins
the fail-closed behaviour. Each negative test grants the operator the
minimum permission needed to *reach* the route gate, then asserts the
admin gate blocks them. A companion positive test verifies the same
operation succeeds with an admin token (so the admin gate doesn't
over-block real flows).

Default-install operators do NOT have USERS_* / GROUPS_* (see
``DEFAULT_GROUPS``), so default deployments were never vulnerable
unless an admin had explicitly granted the permission to a custom
group — but anyone in that position would expect the boundary the
comments described.
"""

import secrets

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.group import Group


def _make_fixture_password() -> str:
    """Build a per-run test credential at import time.

    Tests in this module exercise the admin authorization gate, not
    password handling — the value is irrelevant as long as the same
    string is used at setup/create and at login. Generating the random
    body with :mod:`secrets` keeps any literal out of the source so
    secret scanners don't flag the file. The four-char prefix satisfies
    the password-complexity validator in :mod:`backend.app.schemas.auth`
    (upper + lower + digit + symbol).
    """
    return "Aa1!" + secrets.token_urlsafe(12)


_FIXTURE_PW = _make_fixture_password()  # pragma: allowlist secret


async def _setup_admin(async_client: AsyncClient, username: str = "secadmin") -> str:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": username, "admin_password": _FIXTURE_PW},
    )
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": _FIXTURE_PW},
    )
    return login.json()["access_token"]


async def _create_operator_with_perms(
    async_client: AsyncClient,
    admin_token: str,
    db_session,
    *,
    username: str,
    permissions: list[str],
) -> tuple[str, int]:
    """Create a non-admin user, drop them in a custom group with exactly
    the requested permissions, return (token, user_id).

    The operator is intentionally NOT an admin and NOT in the Administrators
    group — they hold ONLY the listed permission strings. Mirrors the exact
    deployment shape the security engineer described: an operator gifted
    one admin-level permission via a custom group ends up able to escalate
    to full admin without the gate.
    """
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Create a custom group carrying just the requested permissions.
    grp_resp = await async_client.post(
        "/api/v1/groups/",
        headers=headers,
        json={"name": f"escalation_test_{username}", "permissions": permissions},
    )
    assert grp_resp.status_code == 201, grp_resp.text
    gid = grp_resp.json()["id"]

    # Create a regular (role="user") user.
    user_resp = await async_client.post(
        "/api/v1/users/",
        headers=headers,
        json={"username": username, "password": _FIXTURE_PW, "role": "user", "group_ids": [gid]},
    )
    assert user_resp.status_code == 201, user_resp.text
    uid = user_resp.json()["id"]

    # Confirm the operator is NOT admin in the response shape.
    assert user_resp.json()["is_admin"] is False

    login = await async_client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": _FIXTURE_PW},
    )
    assert login.status_code == 200
    return login.json()["access_token"], uid


async def _admin_group_id(db_session) -> int:
    result = await db_session.execute(select(Group).where(Group.name == "Administrators"))
    return result.scalar_one().id


# ---------------------------------------------------------------------------
# 1. PATCH /users/{id} {role: "admin"} — USERS_UPDATE holder cannot
# self-promote
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_users_update_holder_cannot_set_role_to_admin(async_client: AsyncClient, db_session):
    admin_token = await _setup_admin(async_client)
    op_token, op_id = await _create_operator_with_perms(
        async_client, admin_token, db_session, username="op1", permissions=["users:update"]
    )

    resp = await async_client.patch(
        f"/api/v1/users/{op_id}",
        headers={"Authorization": f"Bearer {op_token}"},
        json={"role": "admin"},
    )
    assert resp.status_code == 403

    # And the operator is not admin in the DB after the attempted patch.
    from backend.app.models.user import User

    result = await db_session.execute(select(User).where(User.id == op_id))
    user = result.scalar_one()
    assert user.role == "user"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_users_update_holder_cannot_target_other_user(async_client: AsyncClient, db_session):
    admin_token = await _setup_admin(async_client)
    op_token, _ = await _create_operator_with_perms(
        async_client, admin_token, db_session, username="op2", permissions=["users:update"]
    )
    # Create a separate target user.
    headers = {"Authorization": f"Bearer {admin_token}"}
    target = await async_client.post(
        "/api/v1/users/",
        headers=headers,
        json={"username": "target", "password": _FIXTURE_PW, "role": "user"},
    )
    target_id = target.json()["id"]

    # Operator attempts to elevate target to admin.
    resp = await async_client.patch(
        f"/api/v1/users/{target_id}",
        headers={"Authorization": f"Bearer {op_token}"},
        json={"role": "admin"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 2. POST /users/ {role: "admin"} — USERS_CREATE holder cannot create admin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_users_create_holder_cannot_create_admin(async_client: AsyncClient, db_session):
    admin_token = await _setup_admin(async_client)
    op_token, _ = await _create_operator_with_perms(
        async_client, admin_token, db_session, username="op3", permissions=["users:create"]
    )

    resp = await async_client.post(
        "/api/v1/users/",
        headers={"Authorization": f"Bearer {op_token}"},
        json={"username": "newadmin", "password": _FIXTURE_PW, "role": "admin"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 3. PATCH /groups/{id} {permissions: [...]} — GROUPS_UPDATE holder cannot
# rewrite a group to admin-equivalent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_groups_update_holder_cannot_rewrite_permissions(async_client: AsyncClient, db_session):
    admin_token = await _setup_admin(async_client)
    op_token, _ = await _create_operator_with_perms(
        async_client, admin_token, db_session, username="op4", permissions=["groups:update"]
    )

    # Admin creates a target group; operator tries to grant it everything.
    headers = {"Authorization": f"Bearer {admin_token}"}
    create = await async_client.post(
        "/api/v1/groups/",
        headers=headers,
        json={"name": "innocent", "permissions": ["printers:read"]},
    )
    gid = create.json()["id"]

    from backend.app.core.permissions import ALL_PERMISSIONS

    resp = await async_client.patch(
        f"/api/v1/groups/{gid}",
        headers={"Authorization": f"Bearer {op_token}"},
        json={"permissions": ALL_PERMISSIONS},
    )
    assert resp.status_code == 403

    # And the group still has its original (narrow) permissions.
    result = await db_session.execute(select(Group).where(Group.id == gid))
    assert result.scalar_one().permissions == ["printers:read"]


# ---------------------------------------------------------------------------
# 4. POST /groups/ {permissions: [...]} — GROUPS_CREATE holder cannot create
# an admin-equivalent group
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_groups_create_holder_cannot_create_admin_equivalent(async_client: AsyncClient, db_session):
    admin_token = await _setup_admin(async_client)
    op_token, _ = await _create_operator_with_perms(
        async_client, admin_token, db_session, username="op5", permissions=["groups:create"]
    )
    from backend.app.core.permissions import ALL_PERMISSIONS

    resp = await async_client.post(
        "/api/v1/groups/",
        headers={"Authorization": f"Bearer {op_token}"},
        json={"name": "shadowadmins", "permissions": ALL_PERMISSIONS},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 5. POST /groups/{admin_gid}/users/{my_id} — GROUPS_UPDATE holder cannot
# self-add to Administrators
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_groups_update_holder_cannot_self_add_to_administrators(async_client: AsyncClient, db_session):
    admin_token = await _setup_admin(async_client)
    op_token, op_id = await _create_operator_with_perms(
        async_client, admin_token, db_session, username="op6", permissions=["groups:update"]
    )
    admin_gid = await _admin_group_id(db_session)

    resp = await async_client.post(
        f"/api/v1/groups/{admin_gid}/users/{op_id}",
        headers={"Authorization": f"Bearer {op_token}"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 6. PATCH /groups/{system_gid} — even an admin must not be able to strip
# the Administrators group's permissions (DoS guard).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_cannot_strip_administrators_group_permissions(async_client: AsyncClient, db_session):
    admin_token = await _setup_admin(async_client)
    headers = {"Authorization": f"Bearer {admin_token}"}
    admin_gid = await _admin_group_id(db_session)

    resp = await async_client.patch(
        f"/api/v1/groups/{admin_gid}",
        headers=headers,
        json={"permissions": []},
    )
    assert resp.status_code == 400
    assert "system groups" in resp.json()["detail"].lower()

    # Untouched in DB.
    result = await db_session.execute(select(Group).where(Group.id == admin_gid))
    grp = result.scalar_one()
    assert len(grp.permissions or []) > 0


# ---------------------------------------------------------------------------
# Positive companions — admin should succeed on each route (the admin gate
# must not over-block normal admin flows).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_can_still_perform_user_role_change(async_client: AsyncClient, db_session):
    admin_token = await _setup_admin(async_client)
    headers = {"Authorization": f"Bearer {admin_token}"}
    target = await async_client.post(
        "/api/v1/users/",
        headers=headers,
        json={"username": "promoteme", "password": _FIXTURE_PW, "role": "user"},
    )
    tid = target.json()["id"]

    resp = await async_client.patch(
        f"/api/v1/users/{tid}",
        headers=headers,
        json={"role": "admin"},
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_administrators_group_member_passes_admin_gate(async_client: AsyncClient, db_session):
    """A user whose admin status comes from Administrators-group membership
    rather than the legacy ``role`` column must pass the admin gate. The
    canonical signal is ``User.is_admin``, not ``role == 'admin'``.

    Uses a write endpoint (PATCH /users/{id} {role}) since the admin gate
    lives on writes only — reads stay at ``USERS_READ`` so operator UIs
    (Stats filter-by-user, Archives Print Log, File Manager username
    autocomplete) keep working for non-admin operators who hold the
    read permission via a custom group."""
    admin_token = await _setup_admin(async_client)
    headers = {"Authorization": f"Bearer {admin_token}"}
    admin_gid = await _admin_group_id(db_session)

    # Create a regular user, then add them to Administrators.
    user_resp = await async_client.post(
        "/api/v1/users/",
        headers=headers,
        json={"username": "groupadmin", "password": _FIXTURE_PW, "role": "user"},
    )
    uid = user_resp.json()["id"]
    add = await async_client.post(f"/api/v1/groups/{admin_gid}/users/{uid}", headers=headers)
    assert add.status_code == 204

    # Also create a separate target user to mutate (cleaner than self-modify).
    target_resp = await async_client.post(
        "/api/v1/users/",
        headers=headers,
        json={"username": "target_member", "password": _FIXTURE_PW, "role": "user"},
    )
    target_id = target_resp.json()["id"]

    login = await async_client.post("/api/v1/auth/login", json={"username": "groupadmin", "password": _FIXTURE_PW})
    group_admin_token = login.json()["access_token"]

    # Through an admin-gated write route — must succeed.
    resp = await async_client.patch(
        f"/api/v1/users/{target_id}",
        headers={"Authorization": f"Bearer {group_admin_token}"},
        json={"is_active": False},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.integration
async def test_users_read_remains_delegable_to_non_admin(async_client: AsyncClient, db_session):
    """Operator-visible UIs (Stats filter-by-user, Archives Print Log
    username column, File Manager username autocomplete) reach
    ``GET /users/`` for non-admin operators when a deployment granted
    them ``users:read`` via a custom group. The admin gate must NOT
    apply to read endpoints — only to writes."""
    admin_token = await _setup_admin(async_client)
    op_token, _ = await _create_operator_with_perms(
        async_client, admin_token, db_session, username="reader", permissions=["users:read"]
    )

    resp = await async_client.get("/api/v1/users/", headers={"Authorization": f"Bearer {op_token}"})
    assert resp.status_code == 200
    # Operator is in the list with is_admin=False — confirms the read is
    # working AND the operator hasn't escalated.
    me = next(u for u in resp.json() if u["username"] == "reader")
    assert me["is_admin"] is False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_groups_read_remains_delegable_to_non_admin(async_client: AsyncClient, db_session):
    """Companion to ``users:read``. ``GET /groups/`` + ``GET /groups/
    permissions`` stay reachable to non-admin operators with the read
    permission. Used by setup wizards / informational lookups."""
    admin_token = await _setup_admin(async_client)
    op_token, _ = await _create_operator_with_perms(
        async_client, admin_token, db_session, username="greader", permissions=["groups:read"]
    )

    headers = {"Authorization": f"Bearer {op_token}"}
    list_resp = await async_client.get("/api/v1/groups/", headers=headers)
    assert list_resp.status_code == 200
    perms_resp = await async_client.get("/api/v1/groups/permissions", headers=headers)
    assert perms_resp.status_code == 200
