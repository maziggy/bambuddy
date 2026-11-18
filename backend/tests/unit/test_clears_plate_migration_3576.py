"""Migration test for #3576 - clears_plate on library_files / print_queue / print_archives.

Each column is BOOLEAN NOT NULL DEFAULT FALSE: an existing row reads False and
keeps waiting for the plate confirmation as before. The added columns must come
out as a fresh install builds them, and a second boot must change nothing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import load_all_models, run_migrations

TABLES = ("library_files", "print_queue", "print_archives")


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """settings.database_url may point at Postgres in dev configs; the test engine
    is SQLite, so force the dialect both places run_migrations reads it from."""
    from backend.app.core import database as database_module, db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


async def _column(conn, table: str) -> tuple | None:
    """(type, notnull, default) of clears_plate, default unquoted; None if missing."""
    rows = await conn.execute(text(f"PRAGMA table_info({table})"))
    for row in rows:
        if row[1] == "clears_plate":
            return (row[2], row[3], row[4] and str(row[4]).strip("'"))
    return None


@pytest.fixture
async def fresh_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(load_all_models().create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def legacy_engine():
    """A current schema whose three tables predate #3576, one row each."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        metadata = load_all_models()
        await conn.run_sync(metadata.create_all)
        await conn.execute(
            metadata.tables["printers"]
            .insert()
            .values(id=1, name="A1 Mini", serial_number="SERIAL1", ip_address="192.168.1.5", access_code="12345678")
        )
        await conn.execute(
            metadata.tables["library_files"]
            .insert()
            .values(id=1, filename="a.3mf", file_path="library/a.3mf", file_type="3mf", file_size=1)
        )
        await conn.execute(
            metadata.tables["print_archives"]
            .insert()
            .values(id=1, filename="a.3mf", file_path="archives/a.3mf", file_size=1, status="completed")
        )
        await conn.execute(
            metadata.tables["print_queue"]
            .insert()
            .values(id=1, printer_id=1, archive_id=1, status="pending", position=1)
        )
        for table in TABLES:
            await conn.execute(text(f"ALTER TABLE {table} DROP COLUMN clears_plate"))
    yield engine
    await engine.dispose()


async def test_columns_missing_before_migration(legacy_engine):
    """Sanity check so the assertions below can't pass by accident."""
    async with legacy_engine.begin() as conn:
        for table in TABLES:
            assert await _column(conn, table) is None


@pytest.mark.parametrize("table", TABLES)
async def test_existing_rows_read_false(legacy_engine, table):
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)

    async with legacy_engine.begin() as conn:
        value = (await conn.execute(text(f"SELECT clears_plate FROM {table} WHERE id = 1"))).scalar_one()
    assert value is not None
    assert bool(value) is False


@pytest.mark.parametrize("table", TABLES)
async def test_column_is_not_null_boolean_default_false(legacy_engine, table):
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)
        column = await _column(conn, table)

    assert column is not None
    col_type, notnull, default = column
    assert col_type.upper() == "BOOLEAN"
    assert notnull == 1
    assert default.upper() in ("FALSE", "0")


async def test_upgraded_columns_match_a_fresh_install(legacy_engine, fresh_engine):
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)
        upgraded = {table: await _column(conn, table) for table in TABLES}
    async with fresh_engine.begin() as conn:
        fresh = {table: await _column(conn, table) for table in TABLES}

    for table in TABLES:
        assert upgraded[table] is not None, table
        assert fresh[table] is not None, table
        up_type, up_notnull, up_default = upgraded[table]
        fresh_type, fresh_notnull, fresh_default = fresh[table]
        assert (up_type, up_notnull) == (fresh_type, fresh_notnull), table
        # SQLite spells the same FALSE default "FALSE" (ALTER) and "false"/"0" (create_all).
        assert up_default.upper() in ("FALSE", "0"), table
        assert fresh_default.upper() in ("FALSE", "0"), table


async def test_migration_is_idempotent(legacy_engine):
    """A second boot must not fail on the columns already being there, nor reset data."""
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)
    async with legacy_engine.begin() as conn:
        for table in TABLES:
            await conn.execute(text(f"UPDATE {table} SET clears_plate = TRUE WHERE id = 1"))
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)

    async with legacy_engine.begin() as conn:
        for table in TABLES:
            value = (await conn.execute(text(f"SELECT clears_plate FROM {table} WHERE id = 1"))).scalar_one()
            assert bool(value) is True, table
