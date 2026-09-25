"""Model + migration tests for the typed spool code columns (#2648).

Covers the DB-layer guarantees the feature stands on:

- `_migrate_add_spool_code_columns` upgrades a pre-feature database (spool
  table with none of the code columns) and is idempotent, with the dialect
  pinned so the test's verdict doesn't depend on the developer's
  DATABASE_URL (the exact failure mode a Postgres-based dev environment hit
  reviewing PR #1895).
- A fresh create_all() install carries the columns + their indexes.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import _migrate_add_spool_code_columns

CODE_COLUMNS = ("gtin_code", "asin_code", "sku_code", "other_code", "bought_as_refill")


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """Force the SQLite branch regardless of test env settings."""
    from backend.app.core import db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    yield eng
    await eng.dispose()


async def _spool_columns(conn) -> set[str]:
    rows = (await conn.execute(text("PRAGMA table_info(spool)"))).fetchall()
    return {r[1] for r in rows}


async def _spool_indexes(conn) -> set[str]:
    return {r[1] for r in (await conn.execute(text("PRAGMA index_list(spool)"))).fetchall()}


class TestAddSpoolCodeColumnsMigration:
    async def test_adds_columns_and_indexes_to_pre_feature_table(self, engine):
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE spool (id INTEGER PRIMARY KEY, material VARCHAR(50))"))
            await conn.execute(text("INSERT INTO spool (id, material) VALUES (1, 'PLA')"))

            assert not (set(CODE_COLUMNS) & await _spool_columns(conn))
            await _migrate_add_spool_code_columns(conn)
            assert set(CODE_COLUMNS) <= await _spool_columns(conn)

            index_names = await _spool_indexes(conn)
            assert {"ix_spool_gtin_code", "ix_spool_asin_code", "ix_spool_sku_code"} <= index_names

    async def test_is_idempotent(self, engine):
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE spool (id INTEGER PRIMARY KEY, material VARCHAR(50))"))
            await _migrate_add_spool_code_columns(conn)
            await _migrate_add_spool_code_columns(conn)  # must not raise
            assert set(CODE_COLUMNS) <= await _spool_columns(conn)

    async def test_fresh_create_all_has_columns_and_indexes(self):
        from backend.app.core.database import Base

        eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        try:
            async with eng.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                assert set(CODE_COLUMNS) <= await _spool_columns(conn)
                index_names = await _spool_indexes(conn)
                assert {"ix_spool_gtin_code", "ix_spool_asin_code", "ix_spool_sku_code"} <= index_names
                # The interim table must NOT exist on a fresh install.
                tables = {
                    r[0]
                    for r in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).fetchall()
                }
                assert "spool_code" not in tables
        finally:
            await eng.dispose()
