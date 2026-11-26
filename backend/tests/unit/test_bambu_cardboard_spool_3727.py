"""Bambu's cardboard-spool lines and the catalog row they take (#3727)."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core.database import Base, _migrate_add_bambu_cardboard_spool_row
from backend.app.models.spool_catalog import SpoolCatalogEntry
from backend.app.services.spool_tag_matcher import (
    BAMBU_CARDBOARD_SPOOL_CATALOG_NAME,
    bambu_spool_core_weight,
    default_spool_core_weight,
    ships_on_bambu_cardboard,
)

CARDBOARD = BAMBU_CARDBOARD_SPOOL_CATALOG_NAME


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


async def _catalog(sm, cardboard: bool = True) -> int | None:
    """A Bambu Lab default at 250 g, and the cardboard row; returns the cardboard row's id."""
    async with sm() as db:
        db.add(
            SpoolCatalogEntry(
                name="Bambu Lab - Plastic Low Temp", weight=250, manufacturer="Bambu Lab", default_for_brand=True
            )
        )
        row = SpoolCatalogEntry(name=CARDBOARD, weight=196, manufacturer="Bambu Lab") if cardboard else None
        if row:
            db.add(row)
        await db.commit()
        return row.id if row else None


class TestWhichLines:
    def test_ppa_cf_and_pps_cf_by_id_or_exact_material(self):
        assert ships_on_bambu_cardboard(None, "GFN06")
        assert ships_on_bambu_cardboard(None, "GFT02")
        assert ships_on_bambu_cardboard(" ppa-cf ", None)
        # A slot set to a local preset: no GF id, but the material says so
        assert ships_on_bambu_cardboard("PPS-CF", "P4d1c2e")

    def test_other_nylons_stay_on_the_plastic_spool(self):
        # PA6-CF and PAHT-CF ship on the high-temp reusable spool; PA-CF
        # (discontinued) is taken to come on its successor PAHT-CF's.
        for material, filament_id in (("PA6-CF", "GFN05"), ("PAHT-CF", "GFN04"), ("PA-CF", "GFN03")):
            assert not ships_on_bambu_cardboard(material, filament_id)
        assert not ships_on_bambu_cardboard(None, None)


class TestLookup:
    @pytest.mark.asyncio
    async def test_a_bambu_cardboard_line_takes_the_cardboard_row(self, sm):
        cardboard_id = await _catalog(sm)
        async with sm() as db:
            assert await bambu_spool_core_weight(db, "PPA-CF", "GFN06") == (196, cardboard_id)
            assert await default_spool_core_weight(db, " bambu lab ", "PPS-CF") == (196, cardboard_id)
            assert (await bambu_spool_core_weight(db, "PA6-CF", "GFN05"))[0] == 250

    @pytest.mark.asyncio
    async def test_only_for_bambu_rolls(self, sm):
        await _catalog(sm)
        async with sm() as db:
            assert await default_spool_core_weight(db, "Polymaker", "PPA-CF") is None

    @pytest.mark.asyncio
    async def test_without_the_row_it_takes_the_brand_default(self, sm):
        await _catalog(sm, cardboard=False)
        async with sm() as db:
            assert (await bambu_spool_core_weight(db, "PPA-CF", "GFN06"))[0] == 250


class TestMigration:
    async def _migrate(self, engine):
        async with engine.begin() as conn:
            await _migrate_add_bambu_cardboard_spool_row(conn)

    async def _names(self, sm) -> set[str]:
        async with sm() as db:
            return set((await db.execute(select(SpoolCatalogEntry.name))).scalars().all())

    @pytest.mark.asyncio
    async def test_adds_the_row_once(self, engine, sm):
        await _catalog(sm, cardboard=False)
        await self._migrate(engine)
        async with sm() as db:
            row = (await db.execute(select(SpoolCatalogEntry).where(SpoolCatalogEntry.name == CARDBOARD))).scalar_one()
            assert (row.weight, row.manufacturer, row.is_default, row.default_for_brand) == (
                196,
                "Bambu Lab",
                True,
                False,
            )
            await db.execute(text("DELETE FROM spool_catalog WHERE name = :n"), {"n": CARDBOARD})
            await db.commit()
        # A user who deletes it keeps it deleted
        await self._migrate(engine)
        assert CARDBOARD not in await self._names(sm)

    @pytest.mark.asyncio
    async def test_leaves_an_empty_catalogue_for_the_seeding(self, engine, sm):
        await self._migrate(engine)
        assert await self._names(sm) == set()

    @pytest.mark.asyncio
    async def test_does_not_duplicate_a_row_of_that_name(self, engine, sm):
        async with sm() as db:
            db.add(SpoolCatalogEntry(name=CARDBOARD.upper(), weight=199))
            await db.commit()
        await self._migrate(engine)
        assert await self._names(sm) == {CARDBOARD.upper()}
