"""Migration test for #3464 — smart_plugs.off_temp_source / off_bed_temp_threshold.

An existing plug in temperature mode waited for the nozzle only. The upgrade
must keep it doing so (source "nozzle"), and the added columns must come out
as a fresh install builds them: NOT NULL, with the model's defaults.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import load_all_models, run_migrations

NEW_COLUMNS = ("off_temp_source", "off_bed_temp_threshold")


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """settings.database_url may point at Postgres in dev configs; the test engine
    is SQLite, so force the dialect both places run_migrations reads it from."""
    from backend.app.core import database as database_module, db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


async def _columns(conn) -> dict[str, tuple]:
    """name -> (type, notnull, default) for smart_plugs. The default is unquoted:
    create_all writes an integer server default as '35', the migration as 35."""
    rows = await conn.execute(text("PRAGMA table_info(smart_plugs)"))
    return {row[1]: (row[2], row[3], row[4] and row[4].strip("'")) for row in rows}


@pytest.fixture
async def fresh_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(load_all_models().create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def legacy_engine():
    """A current schema whose smart_plugs predates #3464, holding one temp-mode plug."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        metadata = load_all_models()
        await conn.run_sync(metadata.create_all)
        # Through the table, which fills in the model's Python-side defaults.
        await conn.execute(
            metadata.tables["smart_plugs"]
            .insert()
            .values(id=1, name="P1S Power", plug_type="tasmota", off_delay_mode="temperature", off_temp_threshold=60)
        )
        for column in NEW_COLUMNS:
            await conn.execute(text(f"ALTER TABLE smart_plugs DROP COLUMN {column}"))
    yield engine
    await engine.dispose()


async def test_columns_missing_before_migration(legacy_engine):
    """Sanity check so the assertions below can't pass by accident."""
    async with legacy_engine.begin() as conn:
        columns = await _columns(conn)
    assert not set(NEW_COLUMNS) & set(columns)


async def test_existing_plug_keeps_waiting_for_the_nozzle(legacy_engine):
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)

    async with legacy_engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT off_temp_source, off_bed_temp_threshold, off_temp_threshold FROM smart_plugs WHERE id = 1")
            )
        ).one()
    assert tuple(row) == ("nozzle", 35, 60)


async def test_upgraded_columns_match_a_fresh_install(legacy_engine, fresh_engine):
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)
        upgraded = await _columns(conn)
    async with fresh_engine.begin() as conn:
        fresh = await _columns(conn)

    for column in NEW_COLUMNS:
        assert upgraded[column] == fresh[column], column
        assert upgraded[column][1] == 1, f"{column} must be NOT NULL"


async def test_migration_is_idempotent(legacy_engine):
    """A second boot must not fail on the columns already being there."""
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)

    async with legacy_engine.begin() as conn:
        source = (await conn.execute(text("SELECT off_temp_source FROM smart_plugs WHERE id = 1"))).scalar_one()
    assert source == "nozzle"
