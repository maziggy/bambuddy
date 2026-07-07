"""Regression test for the spool_code backfill migration.

Spool.barcode stays the single denormalized "primary code" column, but the
new spool_code one-to-many table needs a starting-point row for every spool
that already had a barcode scanned before this feature shipped, so
cross-referencing (see _resolve_barcode in routes/inventory.py) has
something to build on immediately after upgrade.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import _migrate_backfill_spool_codes


@pytest.fixture
async def engine():
    """In-memory SQLite with just the spool + spool_code tables.

    The migration only touches these two tables, so the fixture avoids
    registering every model in the project just to satisfy run_migrations's
    broader DDL surface (same rationale as the user-print-template rename
    migration test).
    """
    from backend.app.models.spool import Spool
    from backend.app.models.spool_code import SpoolCode

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Spool.__table__.create)
        await conn.run_sync(SpoolCode.__table__.create)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _insert_spool(conn, spool_id: int, barcode: str | None) -> None:
    # Several NOT NULL columns only have a Python-side ORM default (not a DB
    # server_default), so a raw SQL insert must supply them explicitly.
    await conn.execute(
        text(
            "INSERT INTO spool (id, material, label_weight, core_weight, weight_used, "
            "weight_used_baseline, weight_locked, barcode) "
            "VALUES (:id, 'PLA', 1000, 250, 0, 0, 0, :barcode)"
        ),
        {"id": spool_id, "barcode": barcode},
    )


async def _codes_for(conn, spool_id: int) -> list[tuple]:
    result = await conn.execute(
        text("SELECT code, kind, is_refill, is_primary FROM spool_code WHERE spool_id = :id"),
        {"id": spool_id},
    )
    return result.all()


async def test_backfills_a_code_row_for_every_barcoded_spool(engine):
    async with engine.begin() as conn:
        await _insert_spool(conn, 1, "6938936716785")
        await _insert_spool(conn, 2, "12345678901234")

    async with engine.begin() as conn:
        await _migrate_backfill_spool_codes(conn)

    async with engine.begin() as conn:
        rows = await _codes_for(conn, 1)
        assert len(rows) == 1
        code, kind, is_refill, is_primary = rows[0]
        assert code == "6938936716785"
        assert kind == "gtin"
        assert not is_refill
        assert is_primary

        rows2 = await _codes_for(conn, 2)
        assert len(rows2) == 1
        assert rows2[0][0] == "12345678901234"


async def test_spool_without_barcode_gets_no_code_row(engine):
    async with engine.begin() as conn:
        await _insert_spool(conn, 1, None)

    async with engine.begin() as conn:
        await _migrate_backfill_spool_codes(conn)

    async with engine.begin() as conn:
        assert await _codes_for(conn, 1) == []


async def test_migration_is_idempotent(engine):
    """Running the backfill twice must not violate the (spool_id, code) unique constraint."""
    async with engine.begin() as conn:
        await _insert_spool(conn, 1, "6938936716785")

    async with engine.begin() as conn:
        await _migrate_backfill_spool_codes(conn)
    async with engine.begin() as conn:
        await _migrate_backfill_spool_codes(conn)

    async with engine.begin() as conn:
        rows = await _codes_for(conn, 1)
        assert len(rows) == 1


async def test_migration_does_not_touch_a_manually_added_code(engine):
    """A code already recorded some other way (e.g. cross-reference discovery)
    for the same spool must not be duplicated or disturbed by the backfill."""
    async with engine.begin() as conn:
        await _insert_spool(conn, 1, "6938936716785")
        await conn.execute(
            text(
                "INSERT INTO spool_code (spool_id, code, kind, is_refill, is_primary) "
                "VALUES (1, '6938936716785', 'gtin', 0, 1)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO spool_code (spool_id, code, kind, is_refill, is_primary) "
                "VALUES (1, 'ALZMNTABS01', 'sku', 0, 0)"
            )
        )

    async with engine.begin() as conn:
        await _migrate_backfill_spool_codes(conn)

    async with engine.begin() as conn:
        rows = await _codes_for(conn, 1)
        assert len(rows) == 2
        codes = {r[0] for r in rows}
        assert codes == {"6938936716785", "ALZMNTABS01"}


async def test_migration_handles_empty_table(engine):
    """Migration on an empty spool table must be a safe no-op (fresh install path)."""
    async with engine.begin() as conn:
        await _migrate_backfill_spool_codes(conn)

    async with engine.begin() as conn:
        count = (await conn.execute(text("SELECT COUNT(*) FROM spool_code"))).scalar_one()
        assert count == 0
