"""Integration tests for the /api/v1/groups/* endpoints.

Issue #1083: updates to a group's permission list must persist across GET,
regardless of whether the frontend invalidates its React Query cache.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.core.database import seed_default_groups
from backend.app.models.group import Group


async def _setup_admin(async_client: AsyncClient) -> dict[str, str]:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "gadmin", "admin_password": "AdminPass1!"},
    )
    resp = await async_client.post(
        "/api/v1/auth/login",
        json={"username": "gadmin", "password": "AdminPass1!"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_update_group_permissions_persists(async_client: AsyncClient, db_session):
    """PATCH /groups/{id} with a new permissions list must persist to DB (#1083)."""
    headers = await _setup_admin(async_client)

    create = await async_client.post(
        "/api/v1/groups/",
        headers=headers,
        json={
            "name": "test_perms",
            "permissions": ["printers:read", "archives:read", "queue:read", "inventory:read"],
        },
    )
    assert create.status_code == 201
    gid = create.json()["id"]

    # Update to a wholly different set
    update = await async_client.patch(
        f"/api/v1/groups/{gid}",
        headers=headers,
        json={"permissions": ["users:read", "groups:read"]},
    )
    assert update.status_code == 200
    assert sorted(update.json()["permissions"]) == ["groups:read", "users:read"]

    # Re-read via API — must reflect the update, not the creation
    got = await async_client.get(f"/api/v1/groups/{gid}", headers=headers)
    assert got.status_code == 200
    assert sorted(got.json()["permissions"]) == ["groups:read", "users:read"]

    # Direct DB read — same expectation
    result = await db_session.execute(select(Group).where(Group.id == gid))
    assert sorted(result.scalar_one().permissions or []) == ["groups:read", "users:read"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_update_group_to_empty_permissions(async_client: AsyncClient, db_session):
    """Clearing all permissions via PATCH must result in an empty list, not a no-op."""
    headers = await _setup_admin(async_client)

    create = await async_client.post(
        "/api/v1/groups/",
        headers=headers,
        json={"name": "test_clear", "permissions": ["printers:read", "archives:read"]},
    )
    gid = create.json()["id"]

    update = await async_client.patch(
        f"/api/v1/groups/{gid}",
        headers=headers,
        json={"permissions": []},
    )
    assert update.status_code == 200
    assert update.json()["permissions"] == []

    got = await async_client.get(f"/api/v1/groups/{gid}", headers=headers)
    assert got.json()["permissions"] == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_update_group_without_permissions_field_preserves_existing(async_client: AsyncClient, db_session):
    """PATCH without a permissions field (None) must leave the existing list untouched."""
    headers = await _setup_admin(async_client)

    create = await async_client.post(
        "/api/v1/groups/",
        headers=headers,
        json={"name": "test_preserve", "permissions": ["printers:read", "archives:read"]},
    )
    gid = create.json()["id"]

    # Only update description
    update = await async_client.patch(
        f"/api/v1/groups/{gid}",
        headers=headers,
        json={"description": "updated"},
    )
    assert update.status_code == 200
    assert sorted(update.json()["permissions"]) == ["archives:read", "printers:read"]
    assert update.json()["description"] == "updated"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_update_group_invalid_permission_rejected(async_client: AsyncClient):
    """Invalid permission strings yield 400 and do not persist."""
    headers = await _setup_admin(async_client)

    create = await async_client.post(
        "/api/v1/groups/",
        headers=headers,
        json={"name": "test_bad", "permissions": ["printers:read"]},
    )
    gid = create.json()["id"]

    update = await async_client.patch(
        f"/api/v1/groups/{gid}",
        headers=headers,
        json={"permissions": ["printers:read", "bogus:permission"]},
    )
    assert update.status_code == 400
    assert "Invalid permissions" in update.json()["detail"]

    # Existing value unchanged
    got = await async_client.get(f"/api/v1/groups/{gid}", headers=headers)
    assert got.json()["permissions"] == ["printers:read"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_default_group_system_flags(async_client: AsyncClient):
    """Only Administrators should be locked as a system group."""
    headers = await _setup_admin(async_client)

    response = await async_client.get("/api/v1/groups/", headers=headers)

    assert response.status_code == 200
    groups = {group["name"]: group for group in response.json()}
    assert groups["Administrators"]["is_system"] is True
    assert groups["Operators"]["is_system"] is False
    assert groups["Viewers"]["is_system"] is False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_default_operator_group_permissions_can_be_updated(async_client: AsyncClient):
    """Operators are seeded automatically, but must remain user-editable."""
    headers = await _setup_admin(async_client)
    groups_response = await async_client.get("/api/v1/groups/", headers=headers)
    operators = next(group for group in groups_response.json() if group["name"] == "Operators")

    response = await async_client.patch(
        f"/api/v1/groups/{operators['id']}",
        headers=headers,
        json={"permissions": ["printers:read"]},
    )

    assert response.status_code == 200
    assert response.json()["is_system"] is False
    assert response.json()["permissions"] == ["printers:read"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_default_viewer_group_can_be_deleted(async_client: AsyncClient):
    """Viewers are seeded automatically, but must be removable by admins."""
    headers = await _setup_admin(async_client)
    groups_response = await async_client.get("/api/v1/groups/", headers=headers)
    viewers = next(group for group in groups_response.json() if group["name"] == "Viewers")

    response = await async_client.delete(f"/api/v1/groups/{viewers['id']}", headers=headers)

    assert response.status_code == 204

    await seed_default_groups()
    groups_response = await async_client.get("/api/v1/groups/", headers=headers)
    group_names = {group["name"] for group in groups_response.json()}
    assert "Viewers" not in group_names


@pytest.mark.asyncio
@pytest.mark.integration
async def test_seed_default_groups_downgrades_legacy_operator_and_viewer_system_flags(
    async_client: AsyncClient,
    db_session,
):
    """Existing installs with locked Operators/Viewers should be unlocked on startup."""
    headers = await _setup_admin(async_client)

    for group_name in ("Operators", "Viewers"):
        result = await db_session.execute(select(Group).where(Group.name == group_name))
        group = result.scalar_one()
        group.is_system = True
    await db_session.commit()

    await seed_default_groups()

    response = await async_client.get("/api/v1/groups/", headers=headers)
    groups = {group["name"]: group for group in response.json()}
    assert groups["Administrators"]["is_system"] is True
    assert groups["Operators"]["is_system"] is False
    assert groups["Viewers"]["is_system"] is False
