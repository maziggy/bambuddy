"""Migration test for #3581 -- print_log_entries.pending_charge.

An existing run has no charge waiting: it was charged when it ended. The
added column must come out as a fresh install builds it: nullable, no default.
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


async def _column(conn) -> tuple | None:
    """(notnull, default) of print_log_entries.pending_charge, None when missing."""
    rows = await conn.execute(text("PRAGMA table_info(print_log_entries)"))
    return next(((row[3], row[4]) for row in rows if row[1] == "pending_charge"), None)


@pytest.fixture
async def fresh_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(load_all_models().create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def legacy_engine():
    """A current schema whose print_log_entries predates #3581, holding one run."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        metadata = load_all_models()
        await conn.run_sync(metadata.create_all)
        await conn.execute(metadata.tables["print_log_entries"].insert().values(id=1, status="completed", cost=3.6))
        await conn.execute(text("ALTER TABLE print_log_entries DROP COLUMN pending_charge"))
    yield engine
    await engine.dispose()


async def test_column_missing_before_migration(legacy_engine):
    """Sanity check so the assertions below can't pass by accident."""
    async with legacy_engine.begin() as conn:
        assert await _column(conn) is None


async def test_upgraded_column_matches_a_fresh_install(legacy_engine, fresh_engine):
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)
        upgraded = await _column(conn)
    async with fresh_engine.begin() as conn:
        fresh = await _column(conn)

    assert upgraded is not None
    assert upgraded == fresh == (0, None)


async def test_an_existing_run_has_no_charge_waiting(legacy_engine):
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)
        # A second boot must not fail on the column already being there.
        await run_migrations(conn)
        row = (await conn.execute(text("SELECT cost, pending_charge FROM print_log_entries WHERE id = 1"))).one()
    assert tuple(row) == (3.6, None)
