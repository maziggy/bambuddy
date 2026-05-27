"""Regression test for #794 — LDAP auto-provisioning on legacy SQLite schemas.

Pre-LDAP databases created the `users` table with `password_hash VARCHAR(255) NOT NULL`.
The LDAP provisioning path inserts users with `password_hash=None`, which crashes on
upgrade until the migration strips the NOT NULL constraint.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.core.database import run_migrations


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """The test engine is SQLite but settings.database_url may point to Postgres in dev
    configs — that would make run_migrations take the Postgres branch and skip the
    SQLite-specific writable_schema patch we're verifying. Force the sqlite dialect."""
    from backend.app.core import db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    # database.py imported is_sqlite at module load time — patch there too.
    from backend.app.core import database as database_module

    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


@pytest.fixture
async def legacy_engine():
    """Simulate an older install by creating all current tables via create_all, then
    dropping the `users` table and re-creating it with the legacy NOT NULL schema.
    This matches the real upgrade path — everything else in the DB looks modern, only
    the users table carries a stale constraint."""
    # Import every model so Base.metadata knows about them (same set as conftest).
    from backend.app.core.database import Base
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

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Drop the users table created from the current (nullable) model and replace it
        # with the pre-LDAP schema that real upgrading installations have on disk.
        await conn.execute(text("DROP TABLE IF EXISTS user_groups"))
        await conn.execute(text("DROP TABLE users"))
        await conn.execute(
            text("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                username VARCHAR(100) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(20) NOT NULL DEFAULT 'user',
                is_active BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """)
        )
    yield engine
    await engine.dispose()


async def test_legacy_schema_rejects_null_password_before_migration(legacy_engine):
    """Sanity check: without the migration, inserting a NULL password_hash fails.

    Guards against a false-positive where a future schema change silently allows NULL
    and the real migration test below becomes meaningless.
    """
    async with legacy_engine.begin() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(
                    "INSERT INTO users (username, password_hash, role, is_active) "
                    "VALUES ('ldap_alice', NULL, 'user', 1)"
                )
            )


async def test_migration_allows_null_password_hash_for_ldap_users(legacy_engine):
    """After running migrations on a legacy DB, LDAP users (password_hash=NULL) insert
    successfully — reproduces and verifies the #794 bug reported by DylanBrass."""
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)

    session_maker = async_sessionmaker(legacy_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        await session.execute(
            text(
                "INSERT INTO users (username, email, password_hash, role, auth_source, is_active) "
                "VALUES (:u, :e, NULL, 'user', 'ldap', 1)"
            ),
            {"u": "ldap_bob", "e": "bob@example.com"},
        )
        await session.commit()

        result = await session.execute(
            text("SELECT username, password_hash, auth_source FROM users WHERE username = 'ldap_bob'")
        )
        row = result.one()
        assert row.username == "ldap_bob"
        assert row.password_hash is None
        assert row.auth_source == "ldap"


async def test_migration_is_idempotent(legacy_engine):
    """Running migrations twice must not break the writable_schema patch."""
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)

    session_maker = async_sessionmaker(legacy_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        await session.execute(
            text(
                "INSERT INTO users (username, password_hash, role, auth_source, is_active) "
                "VALUES ('ldap_carol', NULL, 'user', 'ldap', 1)"
            )
        )
        await session.commit()
