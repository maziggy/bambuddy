"""Migration test for spool.batch_id / owner / purchase_date (#2828, #1301, #3408).

A database whose spool table predates the columns must gain them on upgrade,
existing spools must read back as NULL, and a second run must be a no-op.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import load_all_models, run_migrations

NEW_COLUMNS = ("batch_id", "owner", "purchase_date")


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """settings.database_url may point at Postgres in dev configs; the test engine
    is SQLite, so force the dialect both places run_migrations reads it from."""
    from backend.app.core import database as database_module, db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


async def _columns(conn) -> dict[str, tuple]:
    rows = await conn.execute(text("PRAGMA table_info(spool)"))
    return {row[1]: (row[2], row[3]) for row in rows}


@pytest.fixture
async def fresh_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(load_all_models().create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def legacy_engine():
    """A current schema whose spool table predates the columns, holding one spool."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        metadata = load_all_models()
        await conn.run_sync(metadata.create_all)
        # Through the table, which fills in the model's Python-side defaults.
        await conn.execute(metadata.tables["spool"].insert().values(id=1, material="PLA"))
        for column in NEW_COLUMNS:
            await conn.execute(text(f"ALTER TABLE spool DROP COLUMN {column}"))
    yield engine
    await engine.dispose()


async def test_columns_missing_before_migration(legacy_engine):
    """Sanity check so the assertions below can't pass by accident."""
    async with legacy_engine.begin() as conn:
        assert not set(NEW_COLUMNS) & set(await _columns(conn))


async def test_existing_spool_gets_none(legacy_engine):
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)

    async with legacy_engine.begin() as conn:
        row = (await conn.execute(text("SELECT batch_id, owner, purchase_date FROM spool WHERE id = 1"))).one()
    assert tuple(row) == (None, None, None)


async def test_upgraded_columns_match_a_fresh_install(legacy_engine, fresh_engine):
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)
        upgraded = await _columns(conn)
    async with fresh_engine.begin() as conn:
        fresh = await _columns(conn)

    for column in NEW_COLUMNS:
        assert upgraded[column] == fresh[column], column


async def test_a_purchase_date_survives_a_round_trip_as_a_day(legacy_engine):
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)
        await conn.execute(text("UPDATE spool SET purchase_date = '2026-03-14', owner = 'Alice' WHERE id = 1"))

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.app.models.spool import Spool

    async with async_sessionmaker(legacy_engine, class_=AsyncSession)() as session:
        spool = await session.get(Spool, 1)
        assert (spool.owner, spool.purchase_date) == ("Alice", date(2026, 3, 14))


async def test_migration_is_idempotent(legacy_engine):
    """A second boot must not fail on the columns already being there."""
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)

    async with legacy_engine.begin() as conn:
        assert set(NEW_COLUMNS) <= set(await _columns(conn))
