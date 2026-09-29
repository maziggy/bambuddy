"""Migration tests for the spool material_number column (#2870).

A legacy database whose spool table predates the column must gain it on
upgrade, existing rows must read back as NULL, and re-running the migration
must be a no-op (idempotent _safe_execute).
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
async def engine_with_legacy_spool_table():
    """create_all builds the current schema; dropping the column afterwards
    reproduces a database from a Bambuddy version that predates #2870."""
    from backend.app.core.database import Base

    _register_all_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("ALTER TABLE spool DROP COLUMN material_number"))
        await conn.execute(
            text(
                """
                INSERT INTO spool (
                    material, label_weight, core_weight,
                    weight_used, weight_used_baseline, weight_locked
                )
                VALUES ('PLA', 1000, 250, 0, 0, 0)
                """
            )
        )
    yield engine
    await engine.dispose()


async def test_migration_adds_material_number_column(engine_with_legacy_spool_table):
    async with engine_with_legacy_spool_table.begin() as conn:
        await run_migrations(conn)

    async with engine_with_legacy_spool_table.connect() as conn:
        rows = (await conn.execute(text("SELECT id, material, material_number FROM spool"))).all()

    assert len(rows) == 1
    # Pre-existing rows read back with NULL, not an error or a default.
    assert rows[0].material_number is None


async def test_migration_is_idempotent(engine_with_legacy_spool_table):
    async with engine_with_legacy_spool_table.begin() as conn:
        await run_migrations(conn)
    # A value written after the first run must survive the second run — the
    # duplicate ALTER TABLE is swallowed, not applied destructively.
    async with engine_with_legacy_spool_table.begin() as conn:
        await conn.execute(text("UPDATE spool SET material_number = '15'"))
    async with engine_with_legacy_spool_table.begin() as conn:
        await run_migrations(conn)

    async with engine_with_legacy_spool_table.connect() as conn:
        value = (await conn.execute(text("SELECT material_number FROM spool"))).scalar_one()

    assert value == "15"
