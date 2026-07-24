"""The is_env_managed column has to reach databases that already exist (#2593).

The model test covers a table freshly created from metadata, which is not how
an upgrade arrives: an installed instance has an oidc_providers table without
the column, and only run_migrations adds it there. Every boot re-runs the whole
migration set, so adding it twice must be a no-op rather than an error.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import Base, run_migrations


def _register_all_models():
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
        library,
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
        spool_k_profile,
        spool_usage_history,
        spoolbuddy_device,
        user,
        user_email_pref,
        virtual_printer,
    )


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """Force the SQLite branch regardless of test env settings."""
    from backend.app.core import database as database_module, db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


@pytest.fixture
async def engine():
    """A database as it stands before this change: every table created from the
    models, then the new column dropped again -- the model already declares it,
    so only removing it reproduces what an installed instance actually has."""
    _register_all_models()

    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("ALTER TABLE oidc_providers DROP COLUMN is_env_managed"))
    yield eng
    await eng.dispose()


async def _columns(conn) -> set[str]:
    rows = await conn.execute(text("PRAGMA table_info(oidc_providers)"))
    return {r[1] for r in rows}


@pytest.mark.asyncio
async def test_migration_adds_the_column_to_an_existing_table(engine):
    async with engine.connect() as conn:
        assert "is_env_managed" not in await _columns(conn)

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        assert "is_env_managed" in await _columns(conn)


@pytest.mark.asyncio
async def test_existing_rows_default_to_not_env_managed(engine):
    """A provider created through the UI before the upgrade must not come back
    locked -- is_env_managed decides whether the API refuses to edit it."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO oidc_providers"
                " (id, name, issuer_url, client_id, client_secret, scopes, is_enabled,"
                "  auto_create_users, auto_link_existing_accounts, email_claim,"
                "  require_email_verified)"
                " VALUES (1, 'UI provider', 'https://sso.example', 'app', 'enc',"
                "  'openid email profile', 1, 0, 0, 'email', 1)"
            )
        )
        await run_migrations(conn)

    async with engine.connect() as conn:
        row = await conn.execute(text("SELECT is_env_managed FROM oidc_providers WHERE id = 1"))
        assert not row.scalar()


@pytest.mark.asyncio
async def test_it_is_idempotent(engine):
    """Every boot re-runs the migration set."""
    for _ in range(2):
        async with engine.begin() as conn:
            await run_migrations(conn)

    async with engine.connect() as conn:
        assert "is_env_managed" in await _columns(conn)
