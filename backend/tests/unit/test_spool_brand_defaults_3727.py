"""The spool a brand's rolls come on (#3727).

Spool catalog rows carry the manufacturer they belong to, and at most one row per
manufacturer is that brand's default: the empty-spool weight a new roll of the
brand starts with. The migration fills both in on an existing catalogue, once.
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core.catalog_defaults import (
    DEFAULT_BRAND_SPOOLS,
    DEFAULT_SPOOL_CATALOG,
    seed_manufacturer,
    seed_spool_row,
)
from backend.app.core.database import Base, _migrate_spool_catalog_brand_defaults, load_all_models
from backend.app.models.spool_catalog import SpoolCatalogEntry
from backend.app.services.spool_tag_matcher import (
    BAMBU_PLASTIC_SPOOL_CORE_WEIGHT,
    bambu_spool_core_weight,
    default_spool_core_weight,
)

load_all_models()  # every table, for the run_migrations test

FLAG = "_migrate_3727_spool_brand_defaults_done"
SUNLU_CORE = "Sunlu - Plastic V3 + Cardboard Core"


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


async def _migrate(engine):
    async with engine.begin() as conn:
        await _migrate_spool_catalog_brand_defaults(conn)


async def _rows(sm) -> dict[str, SpoolCatalogEntry]:
    async with sm() as db:
        return {r.name: r for r in (await db.execute(select(SpoolCatalogEntry))).scalars().all()}


def _seeded(name: str, weight: int) -> SpoolCatalogEntry:
    """A row as an install from before #3727 seeded it: no manufacturer, no default."""
    return SpoolCatalogEntry(name=name, weight=weight, is_default=True)


# -- Seed data ---------------------------------------------------------------


class TestSeedData:
    def test_manufacturer_is_the_name_before_the_dash(self):
        assert seed_manufacturer("Sunlu - Plastic V3") == "Sunlu"
        assert seed_manufacturer("Bambu Lab - Plastic Low Temp") == "Bambu Lab"
        # A roll size or a product line before the dash isn't the maker
        assert seed_manufacturer("Colorfabb 750g - Cardboard") == "Colorfabb"
        assert seed_manufacturer("eSUN 2.5kg - Plastic") == "eSUN"
        assert seed_manufacturer("PolyTerra PLA - Cardboard") == "PolyMaker"
        assert seed_manufacturer("3DJake ecoPLA - Plastic") == "3DJake"

    def test_a_name_without_a_dash_has_no_manufacturer(self):
        assert seed_manufacturer("My spool") is None
        assert seed_manufacturer(" - Plastic") is None

    def test_no_seeded_manufacturer_carries_a_roll_size(self):
        import re

        makers = {seed_manufacturer(name) for name, _ in DEFAULT_SPOOL_CATALOG}
        assert not [m for m in makers if m and re.search(r"\d\s*k?g$", m, re.IGNORECASE)]

    def test_every_brand_default_is_a_seeded_row_of_that_brand(self):
        names = {name for name, _ in DEFAULT_SPOOL_CATALOG}
        for manufacturer, name in DEFAULT_BRAND_SPOOLS.items():
            assert name in names
            assert seed_manufacturer(name) == manufacturer

    def test_seeding_marks_one_default_per_brand(self):
        rows = [seed_spool_row(name, weight) for name, weight in DEFAULT_SPOOL_CATALOG]
        defaults = [r for r in rows if r["default_for_brand"]]
        assert {r["manufacturer"]: r["name"] for r in defaults} == DEFAULT_BRAND_SPOOLS
        assert all(r["is_default"] for r in rows)


# -- Lookup ------------------------------------------------------------------


class TestBrandLookup:
    @pytest.mark.asyncio
    async def test_finds_the_brand_default_ignoring_case_and_spaces(self, sm):
        async with sm() as db:
            db.add(SpoolCatalogEntry(name="Sunlu - V3", weight=179, manufacturer="Sunlu"))
            db.add(SpoolCatalogEntry(name=SUNLU_CORE, weight=217, manufacturer="Sunlu", default_for_brand=True))
            await db.commit()
            weight, row_id = await default_spool_core_weight(db, "  SUNLU ")
            assert weight == 217
            assert (
                row_id
                == (await db.execute(select(SpoolCatalogEntry.id).where(SpoolCatalogEntry.weight == 217))).scalar_one()
            )

    @pytest.mark.asyncio
    async def test_a_brand_with_a_non_ascii_letter_finds_its_default(self, sm):
        """SQLite's UPPER() folds ASCII only, so brands are compared in Python."""
        async with sm() as db:
            db.add(
                SpoolCatalogEntry(
                    name="Süd - Plastic", weight=230, manufacturer="Filamentwerk Süd", default_for_brand=True
                )
            )
            await db.commit()
            assert (await default_spool_core_weight(db, "Filamentwerk Süd"))[0] == 230
            assert (await default_spool_core_weight(db, " FILAMENTWERK SÜD "))[0] == 230

    @pytest.mark.asyncio
    async def test_a_brand_without_a_default_has_none(self, sm):
        async with sm() as db:
            db.add(SpoolCatalogEntry(name="eSUN - Plastic", weight=240, manufacturer="eSUN"))
            await db.commit()
            assert await default_spool_core_weight(db, "eSUN") is None
            assert await default_spool_core_weight(db, "") is None
            assert await default_spool_core_weight(db, None) is None

    @pytest.mark.asyncio
    async def test_two_defaults_for_one_brand_give_the_same_row_every_time(self, sm):
        """The API keeps one, but a database edited by hand may hold two."""
        async with sm() as db:
            first = SpoolCatalogEntry(name="A", weight=200, manufacturer="X", default_for_brand=True)
            db.add(first)
            await db.flush()
            db.add(SpoolCatalogEntry(name="B", weight=300, manufacturer="X", default_for_brand=True))
            await db.commit()
            assert await default_spool_core_weight(db, "X") == (200, first.id)

    @pytest.mark.asyncio
    async def test_bambu_takes_its_brand_default(self, sm):
        async with sm() as db:
            db.add(SpoolCatalogEntry(name="Bambu Lab - Plastic Low Temp", weight=250, manufacturer="Bambu Lab"))
            db.add(SpoolCatalogEntry(name="Mine", weight=244, manufacturer="Bambu Lab", default_for_brand=True))
            await db.commit()
            assert (await bambu_spool_core_weight(db))[0] == 244

    @pytest.mark.asyncio
    async def test_bambu_without_a_default_gets_the_constant_not_a_row_by_name(self, sm):
        """A user who takes the star off the Bambu row gets 250 g, not that row anyway."""
        async with sm() as db:
            db.add(SpoolCatalogEntry(name="Bambu Lab - Plastic Low Temp", weight=247, manufacturer="Bambu Lab"))
            await db.commit()
            assert await bambu_spool_core_weight(db) == (BAMBU_PLASTIC_SPOOL_CORE_WEIGHT, None)

    @pytest.mark.asyncio
    async def test_bambu_with_neither_gets_the_constant(self, sm):
        async with sm() as db:
            assert await bambu_spool_core_weight(db) == (BAMBU_PLASTIC_SPOOL_CORE_WEIGHT, None)


# -- Migration ---------------------------------------------------------------


class TestMigration:
    @pytest.mark.asyncio
    async def test_fills_the_manufacturer_of_seeded_rows_only(self, engine, sm):
        async with sm() as db:
            db.add(_seeded("eSUN - Plastic", 240))
            db.add(SpoolCatalogEntry(name="Shelf - Spare", weight=200, is_default=False))
            await db.commit()

        await _migrate(engine)

        rows = await _rows(sm)
        assert rows["eSUN - Plastic"].manufacturer == "eSUN"
        # A row the user added: its name follows no rule
        assert rows["Shelf - Spare"].manufacturer is None

    @pytest.mark.asyncio
    async def test_adds_the_new_sunlu_row_and_the_brand_defaults(self, engine, sm):
        async with sm() as db:
            db.add(_seeded("Bambu Lab - Plastic", 250))
            db.add(_seeded("Sunlu - Plastic V3", 179))
            await db.commit()

        await _migrate(engine)

        rows = await _rows(sm)
        assert rows[SUNLU_CORE].weight == 217
        assert rows[SUNLU_CORE].is_default
        assert rows[SUNLU_CORE].manufacturer == "Sunlu"
        defaults = {r.name for r in rows.values() if r.default_for_brand}
        assert defaults == {"Bambu Lab - Plastic", SUNLU_CORE}

    @pytest.mark.asyncio
    async def test_keeps_a_brand_default_the_user_already_set(self, engine, sm):
        async with sm() as db:
            db.add(_seeded("Bambu Lab - Plastic Low Temp", 250))
            db.add(
                SpoolCatalogEntry(name="My Bambu spool", weight=244, manufacturer="bambu lab", default_for_brand=True)
            )
            await db.commit()

        await _migrate(engine)

        rows = await _rows(sm)
        assert rows["My Bambu spool"].default_for_brand
        assert not rows["Bambu Lab - Plastic Low Temp"].default_for_brand

    @pytest.mark.asyncio
    async def test_a_brand_default_set_with_spaces_counts(self, engine, sm):
        async with sm() as db:
            db.add(_seeded("Sunlu - Plastic V3", 179))
            db.add(SpoolCatalogEntry(name="Mine", weight=210, manufacturer=" sunlu ", default_for_brand=True))
            await db.commit()

        await _migrate(engine)

        rows = await _rows(sm)
        assert rows["Mine"].default_for_brand
        assert not rows[SUNLU_CORE].default_for_brand

    @pytest.mark.asyncio
    async def test_does_not_duplicate_a_row_the_user_already_added(self, engine, sm):
        async with sm() as db:
            db.add(_seeded("Sunlu - Plastic V3", 179))
            db.add(SpoolCatalogEntry(name=SUNLU_CORE.upper(), weight=215))
            await db.commit()

        await _migrate(engine)

        rows = await _rows(sm)
        assert SUNLU_CORE not in rows
        # The user's row of that name becomes the default, with its own weight
        assert rows[SUNLU_CORE.upper()].default_for_brand
        assert rows[SUNLU_CORE.upper()].weight == 215
        assert rows[SUNLU_CORE.upper()].manufacturer == "Sunlu"

    @pytest.mark.asyncio
    async def test_a_pruned_catalogue_gets_no_default_it_lacks_the_row_for(self, engine, sm):
        async with sm() as db:
            db.add(_seeded("eSUN - Plastic", 240))
            await db.commit()

        await _migrate(engine)

        rows = await _rows(sm)
        # Sunlu's new row is added and marked; Bambu has no row to mark
        assert [r.name for r in rows.values() if r.default_for_brand] == [SUNLU_CORE]

    @pytest.mark.asyncio
    async def test_leaves_an_empty_catalogue_for_the_seeding(self, engine, sm):
        """seed_spool_catalog() runs after the migrations and only seeds an empty table."""
        await _migrate(engine)

        assert await _rows(sm) == {}
        async with sm() as db:
            done = (await db.execute(text('SELECT value FROM settings WHERE "key" = :k'), {"k": FLAG})).scalar_one()
            assert done == "true"

    @pytest.mark.asyncio
    async def test_runs_exactly_once(self, engine, sm):
        """A row the user deletes or unmarks afterwards stays that way."""
        async with sm() as db:
            db.add(_seeded("Sunlu - Plastic V3", 179))
            await db.commit()

        await _migrate(engine)

        async with sm() as db:
            await db.execute(text("DELETE FROM spool_catalog WHERE name = :n"), {"n": SUNLU_CORE})
            await db.commit()

        await _migrate(engine)

        assert SUNLU_CORE not in await _rows(sm)


# -- The whole upgrade -------------------------------------------------------


@pytest.fixture
def force_sqlite_dialect(monkeypatch):
    """run_migrations' SQLite branch, whatever DATABASE_URL the test env sets."""
    from backend.app.core import database as database_module, db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


class TestUpgrade:
    @pytest.mark.asyncio
    async def test_from_before_2909_through_run_migrations(self, engine, sm, force_sqlite_dialect):
        """Old schema, the #2909 repair, then #3727, then a second boot that changes nothing."""
        from datetime import datetime

        from backend.app.core.database import run_migrations
        from backend.app.models.spool import Spool

        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE spool_catalog DROP COLUMN manufacturer"))
            await conn.execute(text("ALTER TABLE spool_catalog DROP COLUMN default_for_brand"))
            for name, weight in (
                ("Bambu Lab - Plastic High Temp", 216),
                ("Bambu Lab - Plastic Low Temp", 250),
                ("Bambu Lab - Plastic White", 253),
                ("Sunlu - Plastic V3", 179),
            ):
                await conn.execute(
                    text("INSERT INTO spool_catalog (name, weight, is_default) VALUES (:n, :w, 1)"),
                    {"n": name, "w": weight},
                )
        async with sm() as db:
            # An RFID roll the pre-#2909 lookup gave the High Temp tare, then weighed
            db.add(
                Spool(
                    material="PLA",
                    label_weight=1000,
                    core_weight=216,
                    weight_used=100,
                    data_origin="rfid_auto",
                    last_weighed_at=datetime.now(),
                )
            )
            await db.commit()

        async with engine.begin() as conn:
            await run_migrations(conn)

        rows = await _rows(sm)
        # Low Temp renamed in place and the default; High Temp and White, which
        # no spool uses once #2909 has repaired the tare, are gone
        low_temp = rows["Bambu Lab - Plastic"]
        assert low_temp.default_for_brand
        assert not {"Bambu Lab - Plastic Low Temp", "Bambu Lab - Plastic High Temp", "Bambu Lab - Plastic White"} & set(
            rows
        )
        async with sm() as db:
            spool = (await db.execute(select(Spool))).scalar_one()
            assert (spool.core_weight, spool.core_weight_catalog_id, spool.weight_used) == (250, low_temp.id, 134)
            assert await bambu_spool_core_weight(db) == (250, low_temp.id)
            assert (await default_spool_core_weight(db, "SUNLU"))[0] == 217

        async with engine.begin() as conn:
            await run_migrations(conn)
        again = await _rows(sm)
        assert {n: (r.weight, r.manufacturer, r.default_for_brand) for n, r in again.items()} == {
            n: (r.weight, r.manufacturer, r.default_for_brand) for n, r in rows.items()
        }
