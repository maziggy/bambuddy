"""Migration coverage for removing user_wallets.currency (#3123).

An install has one currency, held in the ``currency`` app setting. The column
recorded whatever was configured when a wallet row happened to be created, and
three of its four writers hardcoded "EUR", so it could only ever disagree with
the setting. Dropping it from the model alone would leave every existing
database carrying a column nothing reads.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import Base, run_migrations


def _register_all_models():
    """run_migrations touches many tables; the whole schema has to exist.

    Same list as test_vp_mode_rename_migration.py -- importing only the finance
    models leaves run_migrations ALTERing tables create_all never built.
    """
    from backend.app.models import (  # noqa: F401
        ams_history,
        ams_label,
        api_key,
        archive,
        color_catalog,
        external_link,
        filament,
        finance,
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
        spool_k_profile,
        spool_usage_history,
        spoolbuddy_device,
        user,
        user_email_pref,
        virtual_printer,
    )


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """run_migrations branches on the global dialect, not on the connection.

    settings.database_url may point at Postgres in a dev config, which would
    run the Postgres branch against the SQLite engine below. Same fixture as
    test_billing_run_id_migration.py.
    """
    from backend.app.core import db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    # database.py imported is_sqlite at module load time — patch there too.
    from backend.app.core import database as database_module

    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


async def _wallet_columns(conn) -> set[str]:
    return {row[1] for row in (await conn.execute(text("PRAGMA table_info(user_wallets)"))).all()}


@pytest.mark.asyncio
async def test_an_existing_currency_column_is_dropped(tmp_path):
    """The upgrade path: a database that predates the fix."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'wallet-currency.db'}")
    try:
        async with engine.begin() as conn:
            _register_all_models()
            await conn.run_sync(Base.metadata.create_all)
            # Recreate the pre-#3123 shape, balance and all, then prove the
            # migration takes the column without taking the row with it.
            await conn.execute(text("ALTER TABLE user_wallets ADD COLUMN currency VARCHAR(3) NOT NULL DEFAULT 'EUR'"))
            await conn.execute(text("INSERT INTO user_wallets (user_id, balance, currency) VALUES (7, 12.34, 'EUR')"))
            assert "currency" in await _wallet_columns(conn)

            await run_migrations(conn)

            assert "currency" not in await _wallet_columns(conn)
            row = (await conn.execute(text("SELECT user_id, balance FROM user_wallets"))).all()
            assert row == [(7, 12.34)]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_legacy_create_table_does_not_declare_it(tmp_path):
    """_migrate_create_finance_tables carries its own raw CREATE TABLE.

    It exists for installs whose finance tables predate the ORM models, and it
    declared the column independently of the model. Exercised on its own here,
    without the drop migration that would otherwise mask it.
    """
    from backend.app.core.database import _migrate_create_finance_tables

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'wallet-legacy.db'}")
    try:
        async with engine.begin() as conn:
            _register_all_models()
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("DROP TABLE user_wallets"))

            await _migrate_create_finance_tables(conn)

            assert await _wallet_columns(conn), "the legacy path must still create the table"
            assert "currency" not in await _wallet_columns(conn)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_migration_is_idempotent(tmp_path):
    """Startup runs it every time; the second pass must not error."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'wallet-twice.db'}")
    try:
        async with engine.begin() as conn:
            _register_all_models()
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("ALTER TABLE user_wallets ADD COLUMN currency VARCHAR(3) NOT NULL DEFAULT 'EUR'"))
            await run_migrations(conn)
            await run_migrations(conn)

            assert "currency" not in await _wallet_columns(conn)
    finally:
        await engine.dispose()


class _AsyncCtxStub:
    """Async context manager that does nothing — for ``begin_nested()``."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


async def _capture_drop_sql(is_sqlite_value: bool) -> list[str]:
    """Every DROP COLUMN statement run_migrations would issue on this dialect.

    The project's suite runs on SQLite, so the PostgreSQL branch is otherwise
    dead code in CI. Same capture pattern as test_oidc_icon_migration_pg.py.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from backend.app.core import database as db_module

    executed: list[str] = []

    async def fake_safe_execute(_conn, sql: str) -> None:
        executed.append(sql)

    fake_conn = MagicMock()
    fake_conn.begin_nested = lambda: _AsyncCtxStub()
    fake_conn.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=None)))

    with (
        patch("backend.app.core.database.is_sqlite", return_value=is_sqlite_value),
        patch("backend.app.core.database._safe_execute", side_effect=fake_safe_execute),
        patch("backend.app.core.database._migrate_update_auto_link_constraint", AsyncMock()),
        patch("backend.app.core.database._migrate_widen_spoolman_slot_ams_id_range", AsyncMock()),
    ):
        await db_module.run_migrations(fake_conn)

    return [sql for sql in executed if "user_wallets" in sql and "DROP COLUMN" in sql]


@pytest.mark.asyncio
async def test_postgres_drops_it_conditionally():
    """PostgreSQL takes IF EXISTS, which SQLite's DROP COLUMN does not accept."""
    statements = await _capture_drop_sql(is_sqlite_value=False)
    assert statements == ["ALTER TABLE user_wallets DROP COLUMN IF EXISTS currency"]


@pytest.mark.asyncio
async def test_sqlite_drops_it_plainly():
    """Companion to the PostgreSQL case, so the dialect switch cannot invert."""
    statements = await _capture_drop_sql(is_sqlite_value=True)
    assert statements == ["ALTER TABLE user_wallets DROP COLUMN currency"]
