"""Unit tests for storage location service (#1004)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.location import Location
from backend.app.models.spool import Spool
from backend.app.services.location_service import (
    assign_location_name,
    enrich_spool_dicts_with_location_id,
    get_location_by_name,
    location_name_key,
    prepare_internal_spool_payload,
    rename_location,
    resolve_location_by_name,
    resolve_spool_location_fields,
    sync_locations_from_spoolman,
)


@pytest.mark.asyncio
async def test_resolve_location_by_name_creates(db_session: AsyncSession):
    loc = await resolve_location_by_name(db_session, "Shelf A")
    await db_session.commit()
    assert loc is not None
    assert loc.name == "Shelf A"
    assert loc.name_key == location_name_key("Shelf A")

    again = await get_location_by_name(db_session, "shelf a")
    assert again is not None
    assert again.id == loc.id


@pytest.mark.asyncio
async def test_prepare_internal_spool_payload_from_location_id(db_session: AsyncSession):
    loc = Location()
    assign_location_name(loc, "Drawer 2")
    db_session.add(loc)
    await db_session.commit()
    await db_session.refresh(loc)

    payload = await prepare_internal_spool_payload(
        db_session,
        {"material": "PLA", "location_id": loc.id},
        {"material", "location_id"},
    )
    assert payload["location_id"] == loc.id
    assert payload["storage_location"] == "Drawer 2"


@pytest.mark.asyncio
async def test_resolve_spool_location_fields_prefers_location_id(db_session: AsyncSession):
    loc = Location()
    assign_location_name(loc, "Catalog A")
    db_session.add(loc)
    await db_session.commit()
    await db_session.refresh(loc)

    resolved = await resolve_spool_location_fields(
        db_session,
        location_id=loc.id,
        storage_location="Other",
        fields_set={"location_id", "storage_location"},
    )
    assert resolved is not None
    assert resolved.location_id == loc.id
    assert resolved.storage_location == "Catalog A"


@pytest.mark.asyncio
async def test_rename_location_updates_spool_storage(db_session: AsyncSession):
    loc = Location()
    assign_location_name(loc, "Old Shelf")
    spool = Spool(material="PLA", location_id=None, storage_location="Old Shelf")
    db_session.add(loc)
    db_session.add(spool)
    await db_session.commit()
    await db_session.refresh(loc)

    await rename_location(db_session, loc, "New Shelf")
    await db_session.commit()
    await db_session.refresh(spool)

    assert loc.name == "New Shelf"
    assert loc.name_key == location_name_key("New Shelf")
    assert spool.storage_location == "New Shelf"
    assert spool.location_id == loc.id


@pytest.mark.asyncio
async def test_enrich_spool_dicts_with_location_id(db_session: AsyncSession):
    loc = Location()
    assign_location_name(loc, "Garage")
    db_session.add(loc)
    await db_session.commit()

    spools = [{"id": 1, "storage_location": "Garage"}, {"id": 2, "storage_location": None}]
    await enrich_spool_dicts_with_location_id(db_session, spools)
    assert spools[0]["location_id"] == loc.id
    assert spools[1]["location_id"] is None


@pytest.mark.asyncio
async def test_sync_locations_from_spoolman_stages_without_commit(db_session: AsyncSession):
    class FakeClient:
        async def get_distinct_locations(self):
            return ["Spoolman Shelf"]

    changed = await sync_locations_from_spoolman(db_session, FakeClient())
    assert changed is True
    loc = await get_location_by_name(db_session, "Spoolman Shelf")
    assert loc is not None
    # Caller owns the transaction — no commit() was called in sync itself.
    assert loc.id is not None


@pytest.mark.asyncio
async def test_sync_locations_from_spoolman_dedupes_case_variants(db_session: AsyncSession):
    class FakeClient:
        async def get_distinct_locations(self):
            return ["Drybox 1", "DRYBOX 1", "Locker"]

    changed = await sync_locations_from_spoolman(db_session, FakeClient())
    assert changed is True
    await db_session.commit()

    drybox = await get_location_by_name(db_session, "Drybox 1")
    locker = await get_location_by_name(db_session, "Locker")
    assert drybox is not None
    assert locker is not None

    from sqlalchemy import func, select

    from backend.app.models.location import Location

    count = await db_session.scalar(select(func.count()).select_from(Location))
    assert count == 2


@pytest.mark.asyncio
async def test_rename_location_duplicate_name_raises(db_session: AsyncSession):
    first = Location()
    assign_location_name(first, "Shelf A")
    second = Location()
    assign_location_name(second, "Shelf B")
    db_session.add_all([first, second])
    await db_session.commit()
    await db_session.refresh(first)
    await db_session.refresh(second)

    with pytest.raises(ValueError, match="already exists"):
        await rename_location(db_session, second, "Shelf A")
