"""Integration tests for /inventory/locations (#1004)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.location import Location
from backend.app.services.location_service import assign_location_name


@pytest.mark.asyncio
@pytest.mark.integration
async def test_locations_crud_and_spool_link(async_client: AsyncClient, db_session: AsyncSession):
    create_resp = await async_client.post("/api/v1/inventory/locations", json={"name": "Shelf A"})
    assert create_resp.status_code == 201
    loc = create_resp.json()
    assert loc["name"] == "Shelf A"
    assert loc["spool_count"] == 0

    dup_resp = await async_client.post("/api/v1/inventory/locations", json={"name": "shelf a"})
    assert dup_resp.status_code == 409

    spool_resp = await async_client.post(
        "/api/v1/inventory/spools",
        json={"material": "PLA", "location_id": loc["id"]},
    )
    assert spool_resp.status_code == 200
    spool = spool_resp.json()
    assert spool["location_id"] == loc["id"]
    assert spool["storage_location"] == "Shelf A"

    list_resp = await async_client.get("/api/v1/inventory/locations")
    assert list_resp.status_code == 200
    listed = {item["id"]: item for item in list_resp.json()}
    assert listed[loc["id"]]["spool_count"] == 1

    delete_resp = await async_client.delete(f"/api/v1/inventory/locations/{loc['id']}")
    assert delete_resp.status_code == 409

    clear_resp = await async_client.patch(
        f"/api/v1/inventory/spools/{spool['id']}",
        json={"location_id": None},
    )
    assert clear_resp.status_code == 200

    delete_resp2 = await async_client.delete(f"/api/v1/inventory/locations/{loc['id']}")
    assert delete_resp2.status_code == 200


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rename_location_updates_spool_count(async_client: AsyncClient):
    create_resp = await async_client.post("/api/v1/inventory/locations", json={"name": "Old Name"})
    loc = create_resp.json()

    await async_client.post(
        "/api/v1/inventory/spools",
        json={"material": "PLA", "location_id": loc["id"]},
    )

    list_before = await async_client.get("/api/v1/inventory/locations")
    by_id = {item["id"]: item for item in list_before.json()}
    assert by_id[loc["id"]]["spool_count"] == 1

    rename_resp = await async_client.patch(
        f"/api/v1/inventory/locations/{loc['id']}",
        json={"name": "New Name"},
    )
    assert rename_resp.status_code == 200
    assert rename_resp.json()["name"] == "New Name"
    assert rename_resp.json()["spool_count"] == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rename_location_collision_returns_409(async_client: AsyncClient):
    first = await async_client.post("/api/v1/inventory/locations", json={"name": "Shelf A"})
    second = await async_client.post("/api/v1/inventory/locations", json={"name": "Shelf B"})
    assert first.status_code == 201
    assert second.status_code == 201

    collision = await async_client.patch(
        f"/api/v1/inventory/locations/{second.json()['id']}",
        json={"name": "Shelf A"},
    )
    assert collision.status_code == 409
    assert collision.json()["detail"] == "A location with this name already exists"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_location_duplicate_after_commit_returns_409(async_client: AsyncClient):
    """Second create with the same name_key must return 409, not 500."""
    first = await async_client.post("/api/v1/inventory/locations", json={"name": "Race Shelf"})
    second = await async_client.post("/api/v1/inventory/locations", json={"name": "race shelf"})
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"] == "A location with this name already exists"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_locations_is_read_only(async_client: AsyncClient, db_session: AsyncSession):
    """GET /locations is a pure read — no catalog rows appear without explicit writes."""
    from sqlalchemy import func, select

    loc = Location()
    assign_location_name(loc, "Local Only")
    db_session.add(loc)
    await db_session.commit()

    before = await db_session.scalar(select(func.count()).select_from(Location))
    resp = await async_client.get("/api/v1/inventory/locations")
    after = await db_session.scalar(select(func.count()).select_from(Location))

    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert before == after == 1
