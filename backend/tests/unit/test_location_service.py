"""Unit tests for storage location service (#1004)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.location import Location
from backend.app.models.spool import Spool
from backend.app.services.location_service import (
    assign_location_name,
    enrich_spool_dicts_with_location_id,
    get_location_by_name,
    is_ams_slot_location,
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


@pytest.mark.asyncio
async def test_rename_location_picks_up_legacy_row_with_trailing_whitespace(db_session: AsyncSession):
    """A legacy spool whose `storage_location` carries trailing whitespace
    must still get relinked by the rename cascade — the SQL `TRIM()` strips
    the column, so the Python comparison must also strip `old_name`."""
    loc = Location()
    assign_location_name(loc, "Old Shelf")
    # Simulate a legacy row whose name was stored with the same value but
    # the column entry has whitespace padding (this happens in old free-text
    # data + manual DB edits).
    legacy_spool = Spool(material="PLA", location_id=None, storage_location="  Old Shelf  ")
    db_session.add(loc)
    db_session.add(legacy_spool)
    await db_session.commit()
    await db_session.refresh(loc)
    await db_session.refresh(legacy_spool)

    # Force the in-memory name to carry trailing whitespace so the rename
    # path lifts a non-stripped `old_name`. This is the asymmetry the fix
    # addresses (#1505 review IMPORTANT 10).
    loc.name = "Old Shelf  "

    await rename_location(db_session, loc, "New Shelf")
    await db_session.commit()
    await db_session.refresh(legacy_spool)

    assert legacy_spool.storage_location == "New Shelf"
    assert legacy_spool.location_id == loc.id


@pytest.mark.asyncio
async def test_sync_locations_from_spoolman_logs_and_returns_false_on_unavailable(db_session: AsyncSession, caplog):
    """Bare `except Exception: return False` was the prior shape — verify the
    narrowed catch surfaces a warning so ops can see Spoolman outages."""
    from backend.app.services.spoolman import SpoolmanUnavailableError

    class FailingClient:
        async def get_distinct_locations(self):
            raise SpoolmanUnavailableError("Cannot reach Spoolman")

    with caplog.at_level("WARNING", logger="backend.app.services.location_service"):
        changed = await sync_locations_from_spoolman(db_session, FailingClient())

    assert changed is False
    assert any("location sync from Spoolman failed" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_sync_locations_from_spoolman_handles_dict_payload(db_session: AsyncSession):
    """Newer Spoolman returns `list[dict]` from `/location`; the SpoolmanClient
    normalises to `list[str]`, so sync_locations_from_spoolman should accept
    both shapes via the client contract."""

    class DictShapeClient:
        async def get_distinct_locations(self):
            # SpoolmanClient.get_distinct_locations is the one that normalises;
            # at this layer the contract is `list[str]`. Simulate post-normalisation.
            return ["Cabinet 3", "Cabinet 3"]  # dedup tested elsewhere — sanity here

    changed = await sync_locations_from_spoolman(db_session, DictShapeClient())
    assert changed is True
    await db_session.commit()

    cabinet = await get_location_by_name(db_session, "Cabinet 3")
    assert cabinet is not None


class TestIsAmsSlotLocation:
    """A printer slot is where a spool is loaded, not where it is stored."""

    @pytest.mark.parametrize(
        "name",
        [
            "H2D-1 - AMS A1",
            "X1C-2 - AMS C3",
            "P1S - AMS-HT A1",
            "H2D-1 - AMS HT B1",
            "AMS A1",
            "AMS-HT A1",
            "External Spool",
            "H2D-1 - External Spool",
            "h2d-1 - ams a1",
            "  H2D-1 - AMS A1  ",
        ],
    )
    def test_slot_markers_are_recognised(self, name):
        assert is_ams_slot_location(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "Drybox 1",
            "Shelf A",
            "AMS Drybox",
            "Spare AMS trays",
            "Locker - Top",
            "AMS A1 spares",
            "dadadad",
        ],
    )
    def test_real_storage_locations_are_kept(self, name):
        """The filter has to be narrow: anything it swallows is a place the user
        can no longer file a spool under."""
        assert is_ams_slot_location(name) is False


@pytest.mark.asyncio
async def test_sync_locations_from_spoolman_skips_ams_slot_markers(db_session: AsyncSession):
    """Bambuddy used to write the loaded slot into Spoolman's `location` field.
    Importing those back offered a printer slot as a storage location, and in
    Spoolman mode they could not even be deleted -- the delete route counts
    spools by that same string and answered 409."""

    class FakeClient:
        async def get_distinct_locations(self):
            return ["H2D-1 - AMS A1", "X1C-2 - AMS A1", "H2D-1 - External Spool", "Drybox 1"]

    changed = await sync_locations_from_spoolman(db_session, FakeClient())
    assert changed is True
    await db_session.commit()

    assert await get_location_by_name(db_session, "Drybox 1") is not None
    for marker in ("H2D-1 - AMS A1", "X1C-2 - AMS A1", "H2D-1 - External Spool"):
        assert await get_location_by_name(db_session, marker) is None


@pytest.mark.asyncio
async def test_sync_locations_from_spoolman_reports_no_change_when_only_markers(db_session: AsyncSession):
    """`changed` drives the caller's commit — claiming a change for rows that
    were all filtered out would open a write transaction on every poll."""

    class FakeClient:
        async def get_distinct_locations(self):
            return ["H2D-1 - AMS A1", "H2D-1 - AMS A2"]

    assert await sync_locations_from_spoolman(db_session, FakeClient()) is False
