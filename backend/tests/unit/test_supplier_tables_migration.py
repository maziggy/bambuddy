"""Migration tests for the supplier tables (#2988).

A database that predates the feature must gain both tables on upgrade, and
re-running the migration must be a no-op (CREATE TABLE IF NOT EXISTS via
_safe_execute).
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
async def engine_without_supplier_tables():
    """create_all builds the current schema; dropping the tables reproduces a
    database from a Bambuddy version that predates #2988."""
    from backend.app.core.database import Base

    _register_all_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("DROP TABLE spoolman_spool_suppliers"))
        await conn.execute(text("DROP TABLE spool_suppliers"))
        await conn.execute(text("DROP TABLE suppliers"))
    yield engine
    await engine.dispose()


async def test_migration_creates_supplier_tables(engine_without_supplier_tables):
    async with engine_without_supplier_tables.begin() as conn:
        await run_migrations(conn)

    async with engine_without_supplier_tables.begin() as conn:
        await conn.execute(text("INSERT INTO suppliers (name) VALUES ('Supplier A')"))
        await conn.execute(
            text(
                """
                INSERT INTO spool (material, label_weight, core_weight, weight_used, weight_used_baseline, weight_locked)
                VALUES ('PLA', 1000, 250, 0, 0, 0)
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO spool_suppliers (spool_id, supplier_id, quoted_price_per_kg, is_purchase_source)
                SELECT s.id, sup.id, 19.99, 1 FROM spool s, suppliers sup
                """
            )
        )
        # Spoolman twin (#2988 parity): local row keyed by the remote spool id.
        await conn.execute(
            text(
                """
                INSERT INTO spoolman_spool_suppliers (spoolman_spool_id, supplier_id, is_purchase_source)
                SELECT 7, sup.id, 1 FROM suppliers sup
                """
            )
        )

    async with engine_without_supplier_tables.connect() as conn:
        links = (await conn.execute(text("SELECT supplier_id, is_purchase_source FROM spool_suppliers"))).all()
        twin_links = (
            await conn.execute(text("SELECT spoolman_spool_id, supplier_id FROM spoolman_spool_suppliers"))
        ).all()
    assert len(links) == 1
    assert len(twin_links) == 1


async def test_migration_is_idempotent(engine_without_supplier_tables):
    async with engine_without_supplier_tables.begin() as conn:
        await run_migrations(conn)
    async with engine_without_supplier_tables.begin() as conn:
        await conn.execute(text("INSERT INTO suppliers (name) VALUES ('Kept')"))
    async with engine_without_supplier_tables.begin() as conn:
        await run_migrations(conn)

    async with engine_without_supplier_tables.connect() as conn:
        names = (await conn.execute(text("SELECT name FROM suppliers"))).scalars().all()
    # Existing rows survive the re-run — the CREATE is swallowed, not applied.
    assert names == ["Kept"]
