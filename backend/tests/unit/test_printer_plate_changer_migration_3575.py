"""Migration test for #3575 - printers.plate_capacity / plates_remaining.

Both columns are nullable with no default: an existing printer reads NULL (plate
changer counting off) and behaves as before. The added columns must come out as a
fresh install builds them. notification_providers.on_plate_changer_empty defaults
to TRUE, so an existing provider keeps getting the new event.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import load_all_models, run_migrations

PRINTER_COLUMNS = ("plate_capacity", "plates_remaining")


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """settings.database_url may point at Postgres in dev configs; the test engine
    is SQLite, so force the dialect both places run_migrations reads it from."""
    from backend.app.core import database as database_module, db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


async def _columns(conn, table: str, names: tuple[str, ...]) -> dict[str, tuple]:
    """name -> (type, notnull, default) for the given columns, default unquoted."""
    rows = await conn.execute(text(f"PRAGMA table_info({table})"))
    return {row[1]: (row[2], row[3], row[4] and str(row[4]).strip("'")) for row in rows if row[1] in names}


@pytest.fixture
async def fresh_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(load_all_models().create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def legacy_engine():
    """A current schema whose printers/providers predate #3575, one row each."""
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
            metadata.tables["notification_providers"]
            .insert()
            .values(id=1, name="ntfy", provider_type="ntfy", config="{}")
        )
        await conn.execute(text("ALTER TABLE printers DROP COLUMN plate_capacity"))
        await conn.execute(text("ALTER TABLE printers DROP COLUMN plates_remaining"))
        await conn.execute(text("ALTER TABLE notification_providers DROP COLUMN on_plate_changer_empty"))
    yield engine
    await engine.dispose()


async def test_columns_missing_before_migration(legacy_engine):
    """Sanity check so the assertions below can't pass by accident."""
    async with legacy_engine.begin() as conn:
        assert await _columns(conn, "printers", PRINTER_COLUMNS) == {}
        assert await _columns(conn, "notification_providers", ("on_plate_changer_empty",)) == {}


async def test_existing_printer_has_counting_off(legacy_engine):
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)

    async with legacy_engine.begin() as conn:
        row = (await conn.execute(text("SELECT plate_capacity, plates_remaining FROM printers WHERE id = 1"))).one()
    assert tuple(row) == (None, None)


async def test_existing_provider_gets_the_event_on(legacy_engine):
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)

    async with legacy_engine.begin() as conn:
        value = (
            await conn.execute(text("SELECT on_plate_changer_empty FROM notification_providers WHERE id = 1"))
        ).scalar_one()
    assert bool(value) is True


async def test_upgraded_columns_match_a_fresh_install(legacy_engine, fresh_engine):
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)
        upgraded_printers = await _columns(conn, "printers", PRINTER_COLUMNS)
        upgraded_provider = await _columns(conn, "notification_providers", ("on_plate_changer_empty",))
    async with fresh_engine.begin() as conn:
        fresh_printers = await _columns(conn, "printers", PRINTER_COLUMNS)
        fresh_provider = await _columns(conn, "notification_providers", ("on_plate_changer_empty",))

    assert set(upgraded_printers) == set(PRINTER_COLUMNS)
    assert upgraded_printers == fresh_printers
    assert all(notnull == 0 for _type, notnull, _default in upgraded_printers.values())
    # SQLite spells the same TRUE default "TRUE" (ALTER) and "1" (create_all).
    up_type, up_notnull, up_default = upgraded_provider["on_plate_changer_empty"]
    fresh_type, fresh_notnull, fresh_default = fresh_provider["on_plate_changer_empty"]
    assert (up_type, up_notnull) == (fresh_type, fresh_notnull)
    assert {up_default, fresh_default} <= {"TRUE", "1"}
    assert upgraded_provider["on_plate_changer_empty"][1] == 0


async def test_migration_is_idempotent(legacy_engine):
    """A second boot must not fail on the columns already being there, nor reset data."""
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)
    async with legacy_engine.begin() as conn:
        await conn.execute(text("UPDATE printers SET plate_capacity = 5, plates_remaining = 2 WHERE id = 1"))
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)

    async with legacy_engine.begin() as conn:
        row = (await conn.execute(text("SELECT plate_capacity, plates_remaining FROM printers WHERE id = 1"))).one()
    assert tuple(row) == (5, 2)
