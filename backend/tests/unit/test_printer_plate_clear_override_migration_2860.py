"""Migration test for #2860 — printers.plate_clear_override.

A printer that existed before followed the require_plate_clear setting. The
upgrade must keep it doing so ("inherit"), and the added column must come out
as a fresh install builds it: NOT NULL, with the model's default.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import load_all_models, run_migrations


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """settings.database_url may point at Postgres in dev configs; the test engine
    is SQLite, so force the dialect both places run_migrations reads it from."""
    from backend.app.core import database as database_module, db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


async def _column(conn) -> tuple:
    """(type, notnull, default) of printers.plate_clear_override, default unquoted."""
    rows = await conn.execute(text("PRAGMA table_info(printers)"))
    for row in rows:
        if row[1] == "plate_clear_override":
            return (row[2], row[3], row[4] and row[4].strip("'"))
    return ()


@pytest.fixture
async def fresh_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(load_all_models().create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def legacy_engine():
    """A current schema whose printers predates #2860, holding one printer."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        metadata = load_all_models()
        await conn.run_sync(metadata.create_all)
        # Through the table, which fills in the model's Python-side defaults.
        await conn.execute(
            metadata.tables["printers"]
            .insert()
            .values(id=1, name="A1 Mini", serial_number="SERIAL1", ip_address="192.168.1.5", access_code="12345678")
        )
        await conn.execute(text("ALTER TABLE printers DROP COLUMN plate_clear_override"))
    yield engine
    await engine.dispose()


async def test_column_missing_before_migration(legacy_engine):
    """Sanity check so the assertions below can't pass by accident."""
    async with legacy_engine.begin() as conn:
        assert await _column(conn) == ()


async def test_existing_printer_keeps_following_the_setting(legacy_engine):
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)

    async with legacy_engine.begin() as conn:
        value = (await conn.execute(text("SELECT plate_clear_override FROM printers WHERE id = 1"))).scalar_one()
    assert value == "inherit"


async def test_upgraded_column_matches_a_fresh_install(legacy_engine, fresh_engine):
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)
        upgraded = await _column(conn)
    async with fresh_engine.begin() as conn:
        fresh = await _column(conn)

    assert upgraded == fresh
    assert upgraded[1] == 1, "plate_clear_override must be NOT NULL"


async def test_migration_is_idempotent(legacy_engine):
    """A second boot must not fail on the column already being there."""
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)

    async with legacy_engine.begin() as conn:
        value = (await conn.execute(text("SELECT plate_clear_override FROM printers WHERE id = 1"))).scalar_one()
    assert value == "inherit"
