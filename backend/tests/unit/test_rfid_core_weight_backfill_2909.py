"""Backfill for RFID-created spools carrying the wrong catalogue tare (#2909).

`create_spool_from_tray` looked the core weight up with a `Bambu Lab%` prefix
query and took the first row back, which on SQLite is `Bambu Lab - Plastic High
Temp` — 216 g against the 250 g the spool actually weighs. The forward fix
selects the row by name; `_migrate_repair_rfid_core_weight` repairs the spools
created before it.

`core_weight` is the tare in SpoolBuddy's weigh flow, so these are not display
errors: every scale weighing of an affected spool charged 34 g of filament that
was not on it.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core.database import Base, _migrate_repair_rfid_core_weight
from backend.app.models.spool import Spool
from backend.app.models.spool_catalog import SpoolCatalogEntry


@pytest.fixture
async def engine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _seed_catalog(db, low_temp_weight: int | None = 250) -> int | None:
    """Seed the Bambu rows in DEFAULT_SPOOL_CATALOG order. Low Temp omitted when
    its weight is None, which is the renamed/deleted case."""
    db.add(SpoolCatalogEntry(name="Bambu Lab - Plastic High Temp", weight=216, is_default=True))
    await db.flush()
    low_id = None
    if low_temp_weight is not None:
        low = SpoolCatalogEntry(name="Bambu Lab - Plastic Low Temp", weight=low_temp_weight, is_default=True)
        db.add(low)
        await db.flush()
        low_id = low.id
    db.add(SpoolCatalogEntry(name="Bambu Lab - Plastic White", weight=253, is_default=True))
    await db.flush()
    return low_id


def _spool(**kw) -> Spool:
    base = {
        "material": "PLA",
        "label_weight": 1000,
        "core_weight": 216,
        "data_origin": "rfid_auto",
    }
    return Spool(**{**base, **kw})


@pytest.mark.asyncio
async def test_repairs_the_wrong_tare_and_records_the_row_used(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        low_id = await _seed_catalog(db)
        s = _spool()
        db.add(s)
        await db.commit()
        spool_id = s.id

    async with engine.begin() as conn:
        await _migrate_repair_rfid_core_weight(conn)

    async with sm() as db:
        fixed = await db.get(Spool, spool_id)
        assert fixed.core_weight == 250
        assert fixed.core_weight_catalog_id == low_id


@pytest.mark.asyncio
async def test_repairs_a_spool_whose_form_laundered_the_wrong_row(engine):
    """The reason this cannot key on `core_weight_catalog_id IS NULL`.

    The spool form's catalogue picker auto-selects when exactly one row matches
    the weight, and there is exactly one 216 g row — so opening a spool and
    saving any unrelated field writes the High Temp id, which reads as a
    deliberate user selection. Those spools are the ones most likely to have been
    weighed, so they are exactly the ones a NULL guard must not skip.
    """
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        low_id = await _seed_catalog(db)
        high_id = (
            await db.execute(
                text("SELECT id FROM spool_catalog WHERE name = 'Bambu Lab - Plastic High Temp'")
            )
        ).scalar_one()
        s = _spool(core_weight_catalog_id=high_id)
        db.add(s)
        await db.commit()
        spool_id = s.id

    async with engine.begin() as conn:
        await _migrate_repair_rfid_core_weight(conn)

    async with sm() as db:
        fixed = await db.get(Spool, spool_id)
        assert fixed.core_weight == 250
        assert fixed.core_weight_catalog_id == low_id


@pytest.mark.asyncio
async def test_leaves_a_hand_corrected_weight_alone(engine):
    """The case the NULL guard was written to protect and would have broken.

    A user who types a weight matching no catalogue row has their catalogue id
    cleared to NULL by the same picker effect. Keying on the value leaves them
    untouched.
    """
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        await _seed_catalog(db)
        s = _spool(core_weight=207, core_weight_catalog_id=None)
        db.add(s)
        await db.commit()
        spool_id = s.id

    async with engine.begin() as conn:
        await _migrate_repair_rfid_core_weight(conn)

    async with sm() as db:
        assert (await db.get(Spool, spool_id)).core_weight == 207


@pytest.mark.asyncio
async def test_leaves_manually_created_spools_alone(engine):
    """216 g on a hand-added spool is a value the user chose. Only the RFID path
    produced it without being asked."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        await _seed_catalog(db)
        s = _spool(data_origin="manual")
        db.add(s)
        await db.commit()
        spool_id = s.id

    async with engine.begin() as conn:
        await _migrate_repair_rfid_core_weight(conn)

    async with sm() as db:
        assert (await db.get(Spool, spool_id)).core_weight == 216


@pytest.mark.asyncio
async def test_uses_a_user_edited_catalogue_weight(engine):
    """Same rule as the forward fix: someone who has weighed their own empty
    spool and corrected the row gets their number, not the shipped 250."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        low_id = await _seed_catalog(db, low_temp_weight=244)
        s = _spool()
        db.add(s)
        await db.commit()
        spool_id = s.id

    async with engine.begin() as conn:
        await _migrate_repair_rfid_core_weight(conn)

    async with sm() as db:
        fixed = await db.get(Spool, spool_id)
        assert fixed.core_weight == 244
        assert fixed.core_weight_catalog_id == low_id


@pytest.mark.asyncio
async def test_does_nothing_when_the_catalogue_row_is_missing(engine):
    """With no row to take a weight from there is nothing to say what the right
    value is, and 216 is at least a number the user can see and edit."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        await _seed_catalog(db, low_temp_weight=None)
        s = _spool()
        db.add(s)
        await db.commit()
        spool_id = s.id

    async with engine.begin() as conn:
        await _migrate_repair_rfid_core_weight(conn)

    async with sm() as db:
        assert (await db.get(Spool, spool_id)).core_weight == 216


@pytest.mark.asyncio
async def test_does_not_revert_a_216_set_after_the_repair(engine):
    """One-shot. Without the settings flag, a user who deliberately puts an
    rfid_auto spool on a High Temp core would have it silently reverted on the
    next restart."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        await _seed_catalog(db)
        s = _spool()
        db.add(s)
        await db.commit()
        spool_id = s.id

    async with engine.begin() as conn:
        await _migrate_repair_rfid_core_weight(conn)

    async with sm() as db:
        deliberate = await db.get(Spool, spool_id)
        deliberate.core_weight = 216
        await db.commit()

    async with engine.begin() as conn:
        await _migrate_repair_rfid_core_weight(conn)

    async with sm() as db:
        assert (await db.get(Spool, spool_id)).core_weight == 216
