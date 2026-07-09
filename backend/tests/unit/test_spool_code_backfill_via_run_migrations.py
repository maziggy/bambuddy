"""Regression test for the spool_code backfill migration running through the
real run_migrations entrypoint, not in isolation.

test_spool_code_backfill_migration.py drives _migrate_backfill_spool_codes
directly against a two-table (spool, spool_code) engine, which doesn't cover
the ordering dependency on run_migrations having already added the barcode
column and created the spool_code table earlier in the same pass — this test
exercises the full migration sequence against the complete schema instead.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from backend.app.core.database import run_migrations


def _register_all_models():
    """run_migrations touches multiple tables; the full schema must exist."""
    from backend.app.models import (  # noqa: F401
        ams_history,
        ams_label,
        api_key,
        archive,
        color_catalog,
        external_link,
        filament,
        group,
        kprofile_note,
        maintenance,
        notification,
        notification_template,
        print_log,
        print_queue,
        printer,
        project,
        project_bom,
        settings,
        slot_preset,
        smart_plug,
        smart_plug_energy_snapshot,
        spool,
        spool_assignment,
        spool_catalog,
        spool_code,
        spool_k_profile,
        spool_usage_history,
        spoolbuddy_device,
        user,
        user_email_pref,
        virtual_printer,
    )


async def _engine():
    from backend.app.core.database import Base

    _register_all_models()

    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return eng


async def _insert_spool_via_orm(engine, *, spool_id: int, barcode: str | None) -> None:
    from backend.app.models.spool import Spool

    async with AsyncSession(engine) as session:
        session.add(Spool(id=spool_id, material="PLA", label_weight=1000, barcode=barcode))
        await session.commit()


async def _codes_for(engine, spool_id: int) -> list[tuple]:
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT code, kind, is_primary FROM spool_code WHERE spool_id = :id"), {"id": spool_id}
        )
        return result.all()


async def test_backfill_runs_through_full_migration_sequence():
    """Covers the ordering dependency: spool.barcode and spool_code both need
    to exist by the time the backfill step runs, within one run_migrations
    pass on a schema that predates both."""
    engine = await _engine()
    try:
        await _insert_spool_via_orm(engine, spool_id=1, barcode="6938936716785")
        await _insert_spool_via_orm(engine, spool_id=2, barcode="ALZMNTABS01")

        async with engine.begin() as conn:
            await run_migrations(conn)

        gtin_rows = await _codes_for(engine, 1)
        assert len(gtin_rows) == 1
        assert gtin_rows[0] == ("6938936716785", "gtin", True)

        sku_rows = await _codes_for(engine, 2)
        assert len(sku_rows) == 1
        assert sku_rows[0] == ("ALZMNTABS01", "sku", True)
    finally:
        await engine.dispose()


async def test_backfill_through_run_migrations_is_idempotent():
    engine = await _engine()
    try:
        await _insert_spool_via_orm(engine, spool_id=1, barcode="6938936716785")

        async with engine.begin() as conn:
            await run_migrations(conn)
        async with engine.begin() as conn:
            await run_migrations(conn)  # second pass should be a no-op

        rows = await _codes_for(engine, 1)
        assert len(rows) == 1
    finally:
        await engine.dispose()
