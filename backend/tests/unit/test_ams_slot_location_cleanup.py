"""Cleanup of AMS slot markers imported into the storage-location catalogue.

Bambuddy used to write the slot a spool was loaded into -- "<printer> - AMS A1"
-- into Spoolman's ``location`` field, and the location sync then imported every
distinct one as a storage location. ``_migrate_drop_ams_slot_locations`` clears
the rows that already landed; the import side is covered in
``test_location_service.py``.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core.database import Base, _migrate_drop_ams_slot_locations
from backend.app.models.location import Location
from backend.app.models.spool import Spool
from backend.app.services.location_service import assign_location_name

FLAG = "_cleanup_ams_slot_locations_done"


@pytest.fixture
async def engine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        await eng.dispose()


def _location(name: str) -> Location:
    loc = Location()
    assign_location_name(loc, name)
    return loc


async def _names(db) -> set[str]:
    return {r[0] for r in (await db.execute(text("SELECT name FROM locations"))).fetchall()}


async def _run(engine):
    async with engine.begin() as conn:
        await _migrate_drop_ams_slot_locations(conn)


@pytest.mark.asyncio
async def test_removes_the_slot_markers_and_keeps_real_locations(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        db.add_all(
            [
                _location("H2D-1 - AMS A1"),
                _location("H2D-1 - AMS C3"),
                _location("X1C-2 - AMS-HT A1"),
                _location("P1S - External Spool"),
                _location("Drybox 1"),
                _location("Shelf A"),
            ]
        )
        await db.commit()

    await _run(engine)

    async with sm() as db:
        assert await _names(db) == {"Drybox 1", "Shelf A"}


@pytest.mark.asyncio
async def test_keeps_a_marker_a_spool_is_actually_filed_under(engine):
    """Deleting it would strand the spool's location, and someone who has
    deliberately filed spools under that name meant it."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        loc = _location("H2D-1 - AMS A1")
        db.add(loc)
        await db.flush()
        db.add(
            Spool(
                material="PLA",
                label_weight=1000,
                location_id=loc.id,
                storage_location="H2D-1 - AMS A1",
            )
        )
        await db.commit()

    await _run(engine)

    async with sm() as db:
        assert await _names(db) == {"H2D-1 - AMS A1"}


@pytest.mark.asyncio
async def test_keeps_a_marker_a_legacy_free_text_spool_still_names(engine):
    """Rows predating the location catalogue carry the name without the FK, and
    the rename cascade still matches them on it."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        db.add(_location("H2D-1 - AMS A1"))
        await db.flush()
        # Whitespace and case around the name are the legacy shape the rename
        # cascade already has to cope with, so the guard has to match it too.
        db.add(Spool(material="PLA", label_weight=1000, storage_location="  h2d-1 - ams a1 "))
        await db.commit()

    await _run(engine)

    async with sm() as db:
        assert await _names(db) == {"H2D-1 - AMS A1"}


@pytest.mark.asyncio
async def test_runs_exactly_once(engine):
    """A location the user creates afterwards is theirs, whatever it is named."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        db.add(_location("H2D-1 - AMS A1"))
        await db.commit()

    await _run(engine)

    async with sm() as db:
        db.add(_location("H2D-1 - AMS B2"))
        await db.commit()

    await _run(engine)

    async with sm() as db:
        assert await _names(db) == {"H2D-1 - AMS B2"}


@pytest.mark.asyncio
async def test_marks_itself_done_on_an_install_with_nothing_to_remove(engine):
    """Otherwise the whole catalogue is rescanned on every boot for ever."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        db.add(_location("Drybox 1"))
        await db.commit()

    await _run(engine)

    async with sm() as db:
        done = (await db.execute(text('SELECT value FROM settings WHERE "key" = :k'), {"k": FLAG})).scalar_one_or_none()
        assert done == "true"
        assert await _names(db) == {"Drybox 1"}
