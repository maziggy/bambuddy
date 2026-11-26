"""One Bambu Lab plastic spool row in the catalog (#3727)."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core.catalog_defaults import DEFAULT_BRAND_SPOOLS, DEFAULT_SPOOL_CATALOG
from backend.app.core.database import Base, _migrate_one_bambu_plastic_spool_row
from backend.app.models.spool import Spool
from backend.app.models.spool_catalog import SpoolCatalogEntry

PLASTIC = "Bambu Lab - Plastic"
LOW, HIGH, WHITE = (f"Bambu Lab - Plastic {kind}" for kind in ("Low Temp", "High Temp", "White"))


@pytest.fixture
async def engine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
def sm(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


async def _old_catalogue(sm, low_weight: int = 250) -> dict[str, int]:
    """The three seeded rows from before #3727; returns their ids."""
    async with sm() as db:
        rows = {
            n: SpoolCatalogEntry(name=n, weight=w, is_default=True)
            for n, w in ((HIGH, 216), (LOW, low_weight), (WHITE, 253))
        }
        db.add_all(rows.values())
        await db.commit()
        return {n: r.id for n, r in rows.items()}


async def _migrate(engine):
    async with engine.begin() as conn:
        await _migrate_one_bambu_plastic_spool_row(conn)


async def _rows(sm) -> dict[str, SpoolCatalogEntry]:
    async with sm() as db:
        return {r.name: r for r in (await db.execute(select(SpoolCatalogEntry))).scalars().all()}


def test_the_seed_has_one_bambu_plastic_row_and_it_is_the_default():
    plastic = {n: w for n, w in DEFAULT_SPOOL_CATALOG if n.startswith("Bambu Lab - Plastic")}
    assert plastic == {PLASTIC: 250}
    assert DEFAULT_BRAND_SPOOLS["Bambu Lab"] == PLASTIC


@pytest.mark.asyncio
async def test_renames_low_temp_in_place_and_drops_the_other_two(engine, sm):
    ids = await _old_catalogue(sm, low_weight=244)
    await _migrate(engine)
    rows = await _rows(sm)
    assert set(rows) == {PLASTIC}
    # Same row: spools that point at it and the user's corrected weight carry over
    assert (rows[PLASTIC].id, rows[PLASTIC].weight) == (ids[LOW], 244)


@pytest.mark.asyncio
async def test_keeps_a_row_a_spool_uses_or_the_user_edited(engine, sm):
    ids = await _old_catalogue(sm)
    async with sm() as db:
        db.add(Spool(material="ABS", label_weight=1000, core_weight=216, core_weight_catalog_id=ids[HIGH]))
        await db.execute(text("UPDATE spool_catalog SET weight = 255 WHERE name = :n"), {"n": WHITE})
        await db.commit()
    await _migrate(engine)
    assert set(await _rows(sm)) == {PLASTIC, HIGH, WHITE}


@pytest.mark.asyncio
async def test_does_not_rename_onto_a_row_the_user_has(engine, sm):
    await _old_catalogue(sm)
    async with sm() as db:
        db.add(SpoolCatalogEntry(name=PLASTIC, weight=240))
        await db.commit()
    await _migrate(engine)
    rows = await _rows(sm)
    assert (rows[PLASTIC].weight, rows[LOW].weight) == (240, 250)


@pytest.mark.asyncio
async def test_leaves_an_empty_catalogue_and_runs_once(engine, sm):
    await _migrate(engine)
    assert await _rows(sm) == {}
    # Seeded rows that appear later (say, an old backup's) are left alone
    await _old_catalogue(sm)
    await _migrate(engine)
    assert set(await _rows(sm)) == {LOW, HIGH, WHITE}
