"""The PostgreSQL test database is emptied between tests, not rebuilt.

Rebuilding it for every test (DROP SCHEMA + create_all) was 90% of a
PostgreSQL run. It is now built once per worker and emptied, so these pin what
emptying has to give each test: no rows, ids from 1, and the models' schema,
rebuilt when a test changed it. They run only on PostgreSQL.
"""

import pytest
from sqlalchemy import text

from backend.tests import conftest

pytestmark = pytest.mark.skipif(conftest.TEST_PG_URL is None, reason="the PostgreSQL test database only")


async def _reset(test_engine):
    async with test_engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
        await conftest._reset_pg_test_schema(conn)


def _printer(n: int, **extra):
    from backend.app.models.printer import Printer

    return Printer(name=f"p{n}", serial_number=f"S{n}", ip_address=f"10.0.0.{n}", access_code="x", model="X1C", **extra)


async def _add(test_engine, *rows):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    async with async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)() as session:
        for row in rows:
            session.add(row)
            await session.flush()
        await session.commit()


async def test_rows_go_and_ids_start_at_one_again(test_engine):
    await _add(test_engine, _printer(1), _printer(2))

    await _reset(test_engine)

    async with test_engine.begin() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM printers"))).scalar_one() == 0
    fresh = _printer(3)
    await _add(test_engine, fresh)
    assert fresh.id == 1


async def test_rows_that_reference_each_other_go_too(test_engine):
    """locations is emptied before spool, whose rows point at it with no
    ON DELETE action: only possible with the foreign-key triggers off."""
    from backend.app.models.location import Location
    from backend.app.models.spool import Spool

    location = Location(name="Shelf", name_key="shelf")
    await _add(test_engine, location)
    await _add(test_engine, Spool(material="PLA", location_id=location.id))

    await _reset(test_engine)

    async with test_engine.begin() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM spool"))).scalar_one() == 0
        assert (await conn.execute(text("SELECT count(*) FROM locations"))).scalar_one() == 0


@pytest.mark.parametrize(
    "change",
    [
        "ALTER TABLE printers ADD COLUMN leftover integer",
        "ALTER TABLE printers ALTER COLUMN location SET DEFAULT 'x'",
        "CREATE TABLE leftover_table (id integer)",
        "CREATE INDEX leftover_index ON printers (name)",
        "ALTER TABLE printers DROP CONSTRAINT printers_serial_number_key",
    ],
)
async def test_a_schema_a_test_changed_is_rebuilt(test_engine, change):
    async with test_engine.begin() as conn:
        before = (await conn.execute(text(conftest._PG_SCHEMA_FINGERPRINT))).scalar_one()
        await conn.execute(text(change))

    await _reset(test_engine)

    async with test_engine.begin() as conn:
        assert (await conn.execute(text(conftest._PG_SCHEMA_FINGERPRINT))).scalar_one() == before
