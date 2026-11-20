"""Migration test for #3582 — smart_plugs.mqtt_energy_total_path / _multiplier.

An existing MQTT plug has no lifetime path, so it keeps reading its one energy
value as before. The added columns must come out as a fresh install builds
them: the path nullable, the multiplier NOT NULL with the model's default.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import load_all_models, run_migrations

NEW_COLUMNS = ("mqtt_energy_total_path", "mqtt_energy_total_multiplier")


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """settings.database_url may point at Postgres in dev configs; the test engine
    is SQLite, so force the dialect both places run_migrations reads it from."""
    from backend.app.core import database as database_module, db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


async def _columns(conn) -> dict[str, tuple]:
    """name -> (type, notnull, default) for smart_plugs, the default unquoted."""
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
    """A current schema whose smart_plugs predates #3582, holding one MQTT plug."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        metadata = load_all_models()
        await conn.run_sync(metadata.create_all)
        await conn.execute(
            metadata.tables["smart_plugs"]
            .insert()
            .values(
                id=1,
                name="Tasmota MQTT",
                plug_type="mqtt",
                mqtt_energy_topic="tele/plug/SENSOR",
                mqtt_energy_path="ENERGY.Today",
            )
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


async def test_existing_plug_has_no_lifetime_path(legacy_engine):
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)

    async with legacy_engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT mqtt_energy_total_path, mqtt_energy_total_multiplier FROM smart_plugs WHERE id = 1")
            )
        ).one()
    assert tuple(row) == (None, 1.0)


async def test_upgraded_columns_match_a_fresh_install(legacy_engine, fresh_engine):
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)
        upgraded = await _columns(conn)
    async with fresh_engine.begin() as conn:
        fresh = await _columns(conn)

    # NOT NULL and default as a fresh install; the type name differs (REAL from
    # the migration, FLOAT from create_all), the REAL affinity does not.
    for column in NEW_COLUMNS:
        assert upgraded[column][1:] == fresh[column][1:], column
    assert upgraded["mqtt_energy_total_multiplier"][1] == 1, "the multiplier must be NOT NULL"


async def test_migration_is_idempotent(legacy_engine):
    """A second boot must not fail on the columns already being there."""
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)

    async with legacy_engine.begin() as conn:
        multiplier = (
            await conn.execute(text("SELECT mqtt_energy_total_multiplier FROM smart_plugs WHERE id = 1"))
        ).scalar_one()
    assert multiplier == 1.0
