"""Bambu PLA Silk+ rolls get their colour name.

The AMS reports a single-colour Silk+ roll as "PLA Silk+", and the RFID lookup
compares that with the catalogue's material. The Silk+ colours were seeded as
"PLA Silk" (what the AMS reports for the Dual/Tri Color silk), so every Silk+
roll arrived unnamed. The seed now files them under "PLA Silk+";
``_migrate_relabel_bambu_silk_plus_colors`` moves an existing install's rows
and ``_migrate_fill_rfid_color_names`` names the spools already added.
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core.catalog_defaults import DEFAULT_COLOR_CATALOG
from backend.app.core.database import (
    _migrate_fill_rfid_color_names,
    _migrate_relabel_bambu_silk_plus_colors,
    load_all_models,
)
from backend.app.models.color_catalog import ColorCatalogEntry
from backend.app.models.spool import Spool
from backend.app.services.color_catalog_lookup import resolve_bambu_color


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    # The engine below is SQLite also when the suite runs against PostgreSQL.
    from backend.app.core import database as database_module, db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


@pytest.fixture
async def engine(tmp_path):
    # Every table: run_migrations walks the whole schema.
    metadata = load_all_models()
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with eng.begin() as conn:
        await conn.run_sync(metadata.create_all)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _migrate(engine):
    async with engine.begin() as conn:
        await _migrate_relabel_bambu_silk_plus_colors(conn)
        await _migrate_fill_rfid_color_names(conn)


def _old_silk_rows():
    """The catalogue an install seeded before the fix."""
    return [
        ColorCatalogEntry(manufacturer=m, color_name=c, hex_color=h, material="PLA Silk", is_default=True)
        for m, c, h, mat in DEFAULT_COLOR_CATALOG
        if m == "Bambu Lab" and mat == "PLA Silk+"
    ]


def _rfid_spool(**kw):
    base = {
        "material": "PLA",
        "subtype": "Silk+",
        "brand": "Bambu Lab",
        "rgba": "C8C8C8FF",
        "label_weight": 1000,
        "core_weight": 250,
        "weight_used": 0.0,
        "data_origin": "rfid_auto",
    }
    base.update(kw)
    return Spool(**base)


def test_the_seed_files_the_silk_plus_colours_under_silk_plus():
    silk = {
        (c, h): mat
        for m, c, h, mat in DEFAULT_COLOR_CATALOG
        if m == "Bambu Lab" and (c, h) in {("Silver", "#C8C8C8"), ("Titan Gray", "#5F6367")}
    }
    assert silk == {("Silver", "#C8C8C8"): "PLA Silk+", ("Titan Gray", "#5F6367"): "PLA Silk+"}
    assert not any(m == "Bambu Lab" and mat == "PLA Silk" for m, _c, _h, mat in DEFAULT_COLOR_CATALOG)


@pytest.mark.asyncio
async def test_a_silk_plus_roll_is_named_from_the_seed(engine):
    session = async_sessionmaker(engine, expire_on_commit=False)
    async with session() as db:
        db.add_all(
            ColorCatalogEntry(manufacturer=m, color_name=c, hex_color=h, material=mat, is_default=True)
            for m, c, h, mat in DEFAULT_COLOR_CATALOG
        )
        await db.commit()
        assert (await resolve_bambu_color(db, "5F6367FF", "PLA Silk+")).name == "Titan Gray"
        assert (await resolve_bambu_color(db, "C8C8C8FF", "PLA Silk+")).name == "Silver"


@pytest.mark.asyncio
async def test_an_existing_catalogue_is_relabelled_and_its_spools_named(engine):
    session = async_sessionmaker(engine, expire_on_commit=False)
    async with session() as db:
        rows = _old_silk_rows()
        # One the user renamed, and one they added: theirs, left alone.
        next(r for r in rows if r.color_name == "Gold").color_name = "My Gold"
        rows.append(
            ColorCatalogEntry(
                manufacturer="Bambu Lab", color_name="Old Silk Red", hex_color="#AA0000", material="PLA Silk"
            )
        )
        db.add_all(rows)
        db.add_all(
            [
                _rfid_spool(rgba="5F6367FF"),
                _rfid_spool(),
                _rfid_spool(color_name="Mine"),  # named by the user
                _rfid_spool(data_origin=None),  # added by hand
                _rfid_spool(subtype="Dual Color"),  # a line the create path rewrote
                _rfid_spool(rgba="C8C8C800"),  # clear: named without the catalogue
            ]
        )
        await db.commit()

    await _migrate(engine)

    async with session() as db:
        materials = {(r.color_name, r.material) for r in (await db.execute(select(ColorCatalogEntry))).scalars().all()}
        assert ("Silver", "PLA Silk+") in materials
        assert ("Titan Gray", "PLA Silk+") in materials
        assert ("My Gold", "PLA Silk") in materials
        assert ("Old Silk Red", "PLA Silk") in materials
        names = [s.color_name for s in (await db.execute(select(Spool).order_by(Spool.id))).scalars().all()]
        assert names == ["Titan Gray", "Silver", "Mine", None, None, None]


@pytest.mark.asyncio
async def test_both_run_once(engine):
    await _migrate(engine)
    session = async_sessionmaker(engine, expire_on_commit=False)
    async with session() as db:
        db.add_all(_old_silk_rows())
        db.add(_rfid_spool())
        await db.commit()

    await _migrate(engine)

    async with session() as db:
        assert {r.material for r in (await db.execute(select(ColorCatalogEntry))).scalars().all()} == {"PLA Silk"}
        assert (await db.execute(select(Spool.color_name))).scalar_one() is None
        flags = (await db.execute(text('SELECT "key" FROM settings'))).scalars().all()
        assert {"_migrate_silk_plus_catalog_done", "_backfill_rfid_color_names_done"} <= set(flags)


@pytest.mark.asyncio
async def test_run_migrations_relabels_before_it_names(engine):
    """Wired into the startup migrations, relabel first: naming against the
    old "PLA Silk" rows would find nothing for a "Silk+" spool."""
    from backend.app.core.database import run_migrations

    session = async_sessionmaker(engine, expire_on_commit=False)
    async with session() as db:
        db.add_all(_old_silk_rows())
        db.add(_rfid_spool(rgba="5F6367FF"))
        await db.commit()

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with session() as db:
        assert (await db.execute(select(Spool.color_name))).scalar_one() == "Titan Gray"
