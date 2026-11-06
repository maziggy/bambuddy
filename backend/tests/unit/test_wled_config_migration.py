"""Migration coverage for per-printer WLED configuration (#1528)."""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.core.database import run_migrations
from backend.app.models.printer import Printer


@pytest.mark.asyncio
async def test_wled_config_migration_is_additive_and_idempotent(test_engine):
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db:
        db.add(Printer(name="Legacy", serial_number="WLED-LEGACY", ip_address="10.0.0.1", access_code="code"))
        await db.commit()

    async with test_engine.begin() as conn:
        await conn.execute(text("ALTER TABLE printers DROP COLUMN wled_config"))
        await run_migrations(conn)
        await run_migrations(conn)
        columns = await conn.run_sync(
            lambda sync_conn: {col["name"] for col in inspect(sync_conn).get_columns("printers")}
        )
        stored = (
            await conn.execute(text("SELECT wled_config FROM printers WHERE serial_number = 'WLED-LEGACY'"))
        ).scalar_one()

    assert "wled_config" in columns
    assert stored is None
