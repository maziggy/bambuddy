"""Regression test for the settings table dedupe + unique-index migration.

Legacy SQLite installs created the `settings` table without a UNIQUE constraint
on `key`. The seed loop's `INSERT OR IGNORE` silently degraded to a plain INSERT
on every restart, duplicating rows. After a handful of restarts, any code path
calling `scalar_one_or_none()` on a `SELECT settings WHERE key = :k` query
(e.g. `is_advanced_auth_enabled`) blew up with `MultipleResultsFound` and 500'd.

`run_migrations` now deletes dup rows (keeping MIN(id) per key) and creates the
missing unique index before the seed loop. This test verifies the fix and its
idempotency on both fresh and legacy schemas.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import run_migrations


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """Force the SQLite branch in run_migrations regardless of test env settings."""
    from backend.app.core import db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    from backend.app.core import database as database_module

    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


def _register_all_models():
    """Import every model so Base.metadata knows about them. run_migrations touches
    multiple tables, so the full schema has to exist before calling it — mirrors the
    pattern in test_ldap_migration.py."""
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
        spool_k_profile,
        spool_usage_history,
        spoolbuddy_device,
        user,
        user_email_pref,
        virtual_printer,
    )


@pytest.fixture
async def legacy_engine():
    """Simulate a pre-UNIQUE install: full schema via create_all, then drop the
    settings table and re-create it in the legacy shape (no UNIQUE on key).
    This matches real-world upgrades where everything else is modern and only
    the settings table carries the stale schema."""
    from backend.app.core.database import Base

    _register_all_models()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("DROP TABLE settings"))
        await conn.execute(
            text("""
            CREATE TABLE settings (
                id INTEGER PRIMARY KEY,
                key TEXT,
                value TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """)
        )
    yield engine
    await engine.dispose()


@pytest.fixture
async def fresh_engine():
    """Simulate a fresh install: every table created from SQLAlchemy models, which
    DOES emit the unique index on settings.key. Verifies the migration is a no-op."""
    from backend.app.core.database import Base

    _register_all_models()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


# -----------------------------------------------------------------------------
# Legacy schema tests
# -----------------------------------------------------------------------------


async def test_legacy_schema_allows_duplicate_keys_before_migration(legacy_engine):
    """Sanity check: the legacy schema really does permit duplicates — protects
    the migration test below from becoming a false-positive if the fixture drifts."""
    async with legacy_engine.begin() as conn:
        await conn.execute(text("INSERT INTO settings (key, value) VALUES ('advanced_auth_enabled', 'false')"))
        await conn.execute(text("INSERT INTO settings (key, value) VALUES ('advanced_auth_enabled', 'false')"))
        result = await conn.execute(text("SELECT COUNT(*) FROM settings WHERE key = 'advanced_auth_enabled'"))
        assert result.scalar_one() == 2


async def test_migration_dedupes_and_adds_unique_index(legacy_engine):
    """Given a legacy DB with duplicate rows for the same key, run_migrations
    should (a) delete duplicates keeping the lowest id, (b) add the unique index,
    (c) make future duplicate inserts fail with IntegrityError."""
    # Seed: two duplicate rows for the same key, with distinguishable values.
    async with legacy_engine.begin() as conn:
        await conn.execute(text("INSERT INTO settings (id, key, value) VALUES (1, 'advanced_auth_enabled', 'old')"))
        await conn.execute(text("INSERT INTO settings (id, key, value) VALUES (2, 'advanced_auth_enabled', 'new')"))
        # Also seed an unrelated key that should survive untouched.
        await conn.execute(text("INSERT INTO settings (id, key, value) VALUES (3, 'other_key', 'keep_me')"))

    async with legacy_engine.begin() as conn:
        await run_migrations(conn)

    async with legacy_engine.begin() as conn:
        # Only the MIN(id) row for the duplicated key remains.
        rows = (await conn.execute(text("SELECT id, value FROM settings WHERE key = 'advanced_auth_enabled'"))).all()
        assert len(rows) == 1
        assert rows[0].id == 1
        assert rows[0].value == "old"

        # Untouched key still present.
        other = (await conn.execute(text("SELECT value FROM settings WHERE key = 'other_key'"))).scalar_one()
        assert other == "keep_me"

        # Unique constraint is now enforced — inserting a duplicate fails.
        with pytest.raises(IntegrityError):
            await conn.execute(text("INSERT INTO settings (key, value) VALUES ('advanced_auth_enabled', 'x')"))


async def test_migration_is_idempotent_on_already_clean_legacy(legacy_engine):
    """Running the migration twice must not crash — the second run finds no
    duplicates and the CREATE UNIQUE INDEX IF NOT EXISTS is a no-op."""
    async with legacy_engine.begin() as conn:
        await conn.execute(text("INSERT INTO settings (key, value) VALUES ('k', 'v')"))

    async with legacy_engine.begin() as conn:
        await run_migrations(conn)
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)

    async with legacy_engine.begin() as conn:
        count = (await conn.execute(text("SELECT COUNT(*) FROM settings WHERE key = 'k'"))).scalar_one()
        assert count == 1


# -----------------------------------------------------------------------------
# Fresh-install test — migration must be a safe no-op
# -----------------------------------------------------------------------------


async def test_migration_is_noop_on_fresh_install(fresh_engine):
    """Fresh installs get the unique index from `create_all`. Running the
    migration must not crash and must not alter the schema."""
    async with fresh_engine.begin() as conn:
        await run_migrations(conn)

    async with fresh_engine.begin() as conn:
        # Unique constraint still present — duplicate insert fails.
        await conn.execute(text("INSERT INTO settings (key, value) VALUES ('k', 'v1')"))
        with pytest.raises(IntegrityError):
            await conn.execute(text("INSERT INTO settings (key, value) VALUES ('k', 'v2')"))
