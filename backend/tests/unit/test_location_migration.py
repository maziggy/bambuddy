"""Regression tests for storage-location migration backfill (#1004).

Legacy installs may have free-text storage_location values that differ only
by case. The backfill must collapse them to one catalog row and stay
idempotent across restarts.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import run_migrations


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    from backend.app.core import db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    from backend.app.core import database as database_module

    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


def _register_all_models():
    import backend.app.models  # noqa: F401

    from backend.app.models import (  # noqa: F401
        external_link,
        location,
        print_log,
        print_queue,
        project_bom,
        slot_preset,
        spoolman_k_profile,
        spoolman_slot_assignment,
        virtual_printer,
    )


@pytest.fixture
async def engine_with_case_variant_spools():
    from backend.app.core.database import Base

    _register_all_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("DELETE FROM locations"))
        await conn.execute(
            text(
                """
                INSERT INTO spool (
                    material, storage_location, label_weight, core_weight,
                    weight_used, weight_used_baseline, weight_locked
                )
                VALUES ('PLA', 'Drybox 1', 1000, 250, 0, 0, 0),
                       ('PETG', 'DRYBOX 1', 1000, 250, 0, 0, 0)
                """
            )
        )
    yield engine
    await engine.dispose()


async def test_backfill_collapses_case_variant_storage_locations(engine_with_case_variant_spools):
    async with engine_with_case_variant_spools.begin() as conn:
        await run_migrations(conn)

    async with engine_with_case_variant_spools.connect() as conn:
        loc_rows = (await conn.execute(text("SELECT id, name, name_key FROM locations ORDER BY id"))).all()
        spool_rows = (
            await conn.execute(text("SELECT id, storage_location, location_id FROM spool ORDER BY id"))
        ).all()

    assert len(loc_rows) == 1
    assert loc_rows[0].name_key == "drybox 1"
    location_id = loc_rows[0].id
    assert all(row.location_id == location_id for row in spool_rows)


async def test_backfill_is_idempotent_with_existing_locations(engine_with_case_variant_spools):
    async with engine_with_case_variant_spools.begin() as conn:
        await run_migrations(conn)
    async with engine_with_case_variant_spools.begin() as conn:
        await run_migrations(conn)

    async with engine_with_case_variant_spools.connect() as conn:
        loc_count = (await conn.execute(text("SELECT COUNT(*) FROM locations"))).scalar_one()
        linked = (
            await conn.execute(
                text("SELECT COUNT(*) FROM spool WHERE location_id IS NOT NULL")
            )
        ).scalar_one()

    assert loc_count == 1
    assert linked == 2
