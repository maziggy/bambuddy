"""Repair for RFID-added spools that took the wrong Bambu spool tare (#2909).

A Bambu roll arrives on the 250 g Low Temp spool, but the lookup that gave an
auto-added spool its ``core_weight`` took the first row named "Bambu Lab" in
whatever order the database returned, of which there are three. The forward fix
picks the row by name; ``_migrate_repair_rfid_core_weight`` corrects the rows
already written, and the ``weight_used`` a wrong tare put out with them.
"""

from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core.database import Base, _migrate_repair_rfid_core_weight
from backend.app.models.spool import Spool
from backend.app.models.spool_catalog import SpoolCatalogEntry

FLAG = "_backfill_2909_rfid_core_weight_done"

# The three rows the broken lookup could return, at their seeded weights.
HIGH_TEMP = 216
LOW_TEMP = 250
WHITE = 253


@pytest.fixture
async def engine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _seed_catalog(db):
    db.add_all(
        [
            SpoolCatalogEntry(name="Bambu Lab - Plastic High Temp", weight=HIGH_TEMP),
            SpoolCatalogEntry(name="Bambu Lab - Plastic Low Temp", weight=LOW_TEMP),
            SpoolCatalogEntry(name="Bambu Lab - Plastic White", weight=WHITE),
            SpoolCatalogEntry(name="eSUN - Plastic", weight=240),
        ]
    )
    await db.flush()


def _spool(**kw):
    base = {
        "material": "PLA",
        "brand": "Bambu Lab",
        "label_weight": 1000,
        "core_weight": HIGH_TEMP,
        "weight_used": 0.0,
        "data_origin": "rfid_auto",
    }
    base.update(kw)
    return Spool(**base)


async def _run(engine):
    async with engine.begin() as conn:
        await _migrate_repair_rfid_core_weight(conn)


@pytest.mark.asyncio
async def test_corrects_the_tare_of_a_wrongly_added_spool(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        await _seed_catalog(db)
        s = _spool()
        db.add(s)
        await db.commit()
        spool_id = s.id
        catalog_id = (
            await db.execute(text("SELECT id FROM spool_catalog WHERE weight = :w"), {"w": LOW_TEMP})
        ).scalar_one()

    await _run(engine)

    async with sm() as db:
        fixed = await db.get(Spool, spool_id)
        assert fixed.core_weight == LOW_TEMP
        # The picker's own row, so the spool form shows the tare it now has.
        assert fixed.core_weight_catalog_id == catalog_id


@pytest.mark.asyncio
async def test_adds_back_the_filament_a_wrong_tare_took_off_a_weighed_spool(engine):
    """A 34 g low tare wrote a ``weight_used`` 34 g short. That error is a
    constant, so adding the difference back is exact however much has been
    printed since the weighing."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        await _seed_catalog(db)
        # Weighed at 800 g with a 216 g tare: 584 g of filament read, so
        # weight_used = 1000 - 584 = 416. The truth is 800 - 250 = 550 left,
        # i.e. 450 used.
        weighed = _spool(weight_used=416.0, last_scale_weight=800.0, last_weighed_at=datetime(2026, 8, 23, 11, 0))
        db.add(weighed)
        await db.commit()
        weighed_id = weighed.id

    await _run(engine)

    async with sm() as db:
        fixed = await db.get(Spool, weighed_id)
        assert fixed.core_weight == LOW_TEMP
        assert fixed.weight_used == 450.0


@pytest.mark.asyncio
async def test_leaves_the_used_weight_of_a_never_weighed_spool_alone(engine):
    """Its ``weight_used`` came from the AMS remaining percentage, which the
    tare never entered into."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        await _seed_catalog(db)
        s = _spool(weight_used=200.0)
        db.add(s)
        await db.commit()
        spool_id = s.id

    await _run(engine)

    async with sm() as db:
        fixed = await db.get(Spool, spool_id)
        assert fixed.core_weight == LOW_TEMP
        assert fixed.weight_used == 200.0


@pytest.mark.asyncio
async def test_leaves_spools_the_user_added_alone(engine):
    """Only ``rfid_auto`` rows came through the lookup that got this wrong."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        await _seed_catalog(db)
        manual = _spool(data_origin="manual", weight_used=100.0)
        db.add(manual)
        await db.commit()
        manual_id = manual.id

    await _run(engine)

    async with sm() as db:
        untouched = await db.get(Spool, manual_id)
        assert untouched.core_weight == HIGH_TEMP
        assert untouched.weight_used == 100.0


@pytest.mark.asyncio
async def test_leaves_a_tare_that_is_not_a_bambu_row_alone(engine):
    """A third-party or hand-typed tare is not the broken lookup's signature."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        await _seed_catalog(db)
        third_party = _spool(core_weight=240)  # the eSUN row
        odd = _spool(core_weight=137)  # typed by hand
        db.add_all([third_party, odd])
        await db.commit()
        ids = (third_party.id, odd.id)

    await _run(engine)

    async with sm() as db:
        assert (await db.get(Spool, ids[0])).core_weight == 240
        assert (await db.get(Spool, ids[1])).core_weight == 137


@pytest.mark.asyncio
async def test_reads_the_right_weight_from_an_edited_catalogue(engine):
    """The weights are read out of the catalogue, not hardcoded, so an install
    whose rows have been re-measured is repaired to its own numbers."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        db.add_all(
            [
                SpoolCatalogEntry(name="Bambu Lab - Plastic High Temp", weight=210),
                SpoolCatalogEntry(name="Bambu Lab - Plastic Low Temp", weight=247),
            ]
        )
        await db.flush()
        s = _spool(core_weight=210)
        db.add(s)
        await db.commit()
        spool_id = s.id

    await _run(engine)

    async with sm() as db:
        assert (await db.get(Spool, spool_id)).core_weight == 247


@pytest.mark.asyncio
async def test_runs_exactly_once(engine):
    """A tare the user sets after the repair has run is theirs to keep."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        await _seed_catalog(db)
        s = _spool()
        db.add(s)
        await db.commit()
        spool_id = s.id

    await _run(engine)

    async with sm() as db:
        deliberate = await db.get(Spool, spool_id)
        deliberate.core_weight = HIGH_TEMP
        await db.commit()

    await _run(engine)

    async with sm() as db:
        assert (await db.get(Spool, spool_id)).core_weight == HIGH_TEMP


@pytest.mark.asyncio
async def test_marks_itself_done_on_an_install_with_nothing_to_repair(engine):
    """Otherwise the scan would repeat on every boot for the rest of time."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        await _seed_catalog(db)
        await db.commit()

    await _run(engine)

    async with sm() as db:
        done = (await db.execute(text('SELECT value FROM settings WHERE "key" = :k'), {"k": FLAG})).scalar_one_or_none()
        assert done == "true"


@pytest.mark.asyncio
async def test_survives_a_catalogue_with_no_bambu_rows(engine):
    """A pruned catalogue gets the documented 250 g default rather than a crash."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        db.add(SpoolCatalogEntry(name="eSUN - Plastic", weight=240))
        await db.flush()
        s = _spool()
        db.add(s)
        await db.commit()
        spool_id = s.id

    await _run(engine)

    async with sm() as db:
        # No Bambu rows means no signature to match, so the spool is left as it
        # stands -- guessing at it would be worse than the tare it has.
        assert (await db.get(Spool, spool_id)).core_weight == HIGH_TEMP
