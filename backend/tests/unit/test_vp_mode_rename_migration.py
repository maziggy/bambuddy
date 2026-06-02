"""Regression test for the VP mode wire-value rename migration (#1429 follow-up).

The UI buttons "Archive" and "Queue" had always saved the wire values
`immediate` and `print_queue` — confusing in every support bundle. The
rename migration in ``run_migrations`` rewrites existing rows to the
canonical names. This test verifies it on both fresh and legacy schemas
and confirms it's idempotent so reruns are safe (boot-on-boot).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import run_migrations


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """Force the SQLite branch regardless of test env settings."""
    from backend.app.core import db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    from backend.app.core import database as database_module

    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


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
        spool_k_profile,
        spool_usage_history,
        spoolbuddy_device,
        user,
        user_email_pref,
        virtual_printer,
    )


@pytest.fixture
async def engine():
    from backend.app.core.database import Base

    _register_all_models()

    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.mark.asyncio
async def test_legacy_mode_rows_get_canonical_names(engine):
    """Existing rows with `immediate` / `print_queue` get rewritten to
    `archive` / `queue` while canonical values and unrelated modes pass
    through untouched."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO virtual_printers (id, name, enabled, mode, serial_suffix, position) VALUES "
                "(1, 'A', 0, 'immediate', '391800001', 1),"
                "(2, 'B', 0, 'print_queue', '391800002', 2),"
                "(3, 'C', 0, 'review', '391800003', 3),"
                "(4, 'D', 0, 'proxy', '391800004', 4),"
                "(5, 'E', 0, 'archive', '391800005', 5),"
                "(6, 'F', 0, 'queue', '391800006', 6)"
            )
        )

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT id, mode FROM virtual_printers ORDER BY id"))
        rows = dict(result.fetchall())

    assert rows[1] == "archive"  # immediate → archive
    assert rows[2] == "queue"  # print_queue → queue
    assert rows[3] == "review"  # untouched
    assert rows[4] == "proxy"  # untouched
    assert rows[5] == "archive"  # already canonical
    assert rows[6] == "queue"  # already canonical


@pytest.mark.asyncio
async def test_legacy_settings_row_gets_canonical_name(engine):
    """The legacy single-VP `virtual_printer_mode` setting also gets renamed
    so the GET response (which feeds the support bundle and the settings
    page) reads the canonical name."""
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO settings (key, value) VALUES ('virtual_printer_mode', 'immediate')"))

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT value FROM settings WHERE key = 'virtual_printer_mode'"))
        value = result.scalar()

    assert value == "archive"


@pytest.mark.asyncio
async def test_migration_is_idempotent(engine):
    """Running the migration twice must be a no-op on canonical values —
    every boot re-runs the migration set."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO virtual_printers (id, name, enabled, mode, serial_suffix, position) "
                "VALUES (1, 'A', 0, 'immediate', '391800001', 1)"
            )
        )

    async with engine.begin() as conn:
        await run_migrations(conn)
    # Second run on already-canonical values.
    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT mode FROM virtual_printers WHERE id = 1"))
        assert result.scalar() == "archive"
