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
        spool_rows = (await conn.execute(text("SELECT id, storage_location, location_id FROM spool ORDER BY id"))).all()

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
        linked = (await conn.execute(text("SELECT COUNT(*) FROM spool WHERE location_id IS NOT NULL"))).scalar_one()

    assert loc_count == 1
    assert linked == 2


@pytest.fixture
async def engine_with_null_storage_location():
    """A spool with NULL storage_location must NOT produce a phantom location row
    or get linked to anything — it stays NULL on both fields."""
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
                VALUES ('PLA', NULL, 1000, 250, 0, 0, 0),
                       ('PETG', '   ', 1000, 250, 0, 0, 0),
                       ('TPU', 'Real Shelf', 1000, 250, 0, 0, 0)
                """
            )
        )
    yield engine
    await engine.dispose()


async def test_backfill_skips_null_and_whitespace_storage_location(
    engine_with_null_storage_location,
):
    """NULL / whitespace-only `storage_location` rows must NOT create catalog
    rows; only the 'Real Shelf' value gets a location row + spool link."""
    async with engine_with_null_storage_location.begin() as conn:
        await run_migrations(conn)

    async with engine_with_null_storage_location.connect() as conn:
        loc_rows = (await conn.execute(text("SELECT name FROM locations"))).all()
        unlinked = (
            await conn.execute(text("SELECT material FROM spool WHERE location_id IS NULL ORDER BY material"))
        ).all()

    # Only the row with a real storage_location should be in the catalog.
    assert [r.name for r in loc_rows] == ["Real Shelf"]
    # The NULL and whitespace-only spools stay unlinked (no phantom row).
    assert [r.material for r in unlinked] == ["PETG", "PLA"]


@pytest.fixture
async def engine_with_legacy_null_name_key_location():
    """Simulate a legacy install where a `locations` row was manually inserted
    BEFORE the name_key column existed. The migration must backfill the
    legacy row's name_key BEFORE the dedup INSERT, so the spool-link UPDATE
    can join on the new key (#1505 review IMPORTANT 11)."""
    from backend.app.core.database import Base

    _register_all_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Drop the model-shaped locations table (which has NOT NULL on
        # name_key) and recreate it in its pre-migration shape: no name_key
        # column at all, mirroring a real upgrade from a Bambuddy version
        # that predates this feature. The migration's idempotent ALTER TABLE
        # is what adds the column without a NOT NULL constraint, so the
        # legacy row can legally have NULL until the new backfill UPDATE
        # runs.
        await conn.execute(text("DROP TABLE locations"))
        await conn.execute(
            text(
                """
                CREATE TABLE locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(255) NOT NULL UNIQUE,
                    identifier VARCHAR(100),
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        await conn.execute(text("INSERT INTO locations (name) VALUES ('Drybox 1')"))
        await conn.execute(
            text(
                """
                INSERT INTO spool (
                    material, storage_location, label_weight, core_weight,
                    weight_used, weight_used_baseline, weight_locked
                )
                VALUES ('PLA', 'Drybox 1', 1000, 250, 0, 0, 0)
                """
            )
        )
    yield engine
    await engine.dispose()


async def test_backfill_links_spool_to_legacy_null_name_key_location(
    engine_with_legacy_null_name_key_location,
):
    async with engine_with_legacy_null_name_key_location.begin() as conn:
        await run_migrations(conn)

    async with engine_with_legacy_null_name_key_location.connect() as conn:
        loc_rows = (await conn.execute(text("SELECT id, name, name_key FROM locations"))).all()
        spool_rows = (await conn.execute(text("SELECT location_id FROM spool"))).all()

    # Exactly one location row (the pre-existing legacy one); its name_key
    # got backfilled by the FIRST step of the migration.
    assert len(loc_rows) == 1
    assert loc_rows[0].name_key == "drybox 1"
    # The spool got linked to that legacy row — under the old ordering it
    # would have been left with `location_id IS NULL`.
    assert spool_rows[0].location_id == loc_rows[0].id
