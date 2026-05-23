"""Integration tests for /inventory/locations (#1004)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool


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
async def test_migration_backfill_storage_location(db_session: AsyncSession):
    spool = Spool(material="PLA", storage_location="Drybox 1")
    db_session.add(spool)
    await db_session.commit()
    await db_session.refresh(spool)

    # Simulate backfill: resolve location by storage string
    from backend.app.services.location_service import resolve_location_by_name

    loc = await resolve_location_by_name(db_session, spool.storage_location or "")
    spool.location_id = loc.id if loc else None
    await db_session.commit()

    assert loc is not None
    assert spool.location_id == loc.id
