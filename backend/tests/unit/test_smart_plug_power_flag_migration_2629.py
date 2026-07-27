"""Migration test for #2629 — smart_plugs.controls_printer_power.

Existing installs have plugs that were assumed to power their linked printer, so
the new column must be added *and backfilled to true*: a NULL or false backfill
would silently stop marking a real printer plug's power-off, which is the
behaviour users have today.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import run_migrations

LEGACY_SMART_PLUGS = """
CREATE TABLE smart_plugs (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    ip_address VARCHAR(45),
    plug_type VARCHAR(20) DEFAULT 'tasmota',
    ha_entity_id VARCHAR(100),
    printer_id INTEGER,
    enabled BOOLEAN DEFAULT 1,
    auto_on BOOLEAN DEFAULT 1,
    auto_off BOOLEAN DEFAULT 1,
    auto_off_persistent BOOLEAN DEFAULT 0,
    off_delay_mode VARCHAR(20) DEFAULT 'time',
    off_delay_minutes INTEGER DEFAULT 5,
    off_temp_threshold INTEGER DEFAULT 70,
    show_in_switchbar BOOLEAN DEFAULT 0,
    show_on_printer_card BOOLEAN DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """settings.database_url may point at Postgres in dev configs; the test engine
    is SQLite, so force the dialect both places run_migrations reads it from."""
    from backend.app.core import database as database_module, db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


@pytest.fixture
async def legacy_engine():
    """A modern schema with a pre-#2629 smart_plugs table holding one plug."""
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

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("DROP TABLE smart_plugs"))
        await conn.execute(text(LEGACY_SMART_PLUGS))
        await conn.execute(
            text("INSERT INTO smart_plugs (id, name, plug_type, printer_id) VALUES (1, 'P1S Power', 'tasmota', 1)")
        )
    yield engine
    await engine.dispose()


async def test_column_missing_before_migration(legacy_engine):
    """Sanity check so the assertion below can't pass by accident."""
    async with legacy_engine.begin() as conn:
        columns = {row[1] for row in await conn.execute(text("PRAGMA table_info(smart_plugs)"))}
    assert "controls_printer_power" not in columns


async def test_existing_plugs_backfill_to_true(legacy_engine):
    """An upgraded install must keep marking its printer offline on power-off."""
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)

    async with legacy_engine.begin() as conn:
        result = await conn.execute(text("SELECT controls_printer_power FROM smart_plugs WHERE id = 1"))
        assert bool(result.scalar_one()) is True


async def test_migration_is_idempotent(legacy_engine):
    """Second boot must not fail on the already-present column."""
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)

    async with legacy_engine.begin() as conn:
        result = await conn.execute(text("SELECT controls_printer_power FROM smart_plugs WHERE id = 1"))
        assert bool(result.scalar_one()) is True


class TestPostgresBranch:
    """CI runs on SQLite, so the Postgres branch of the dialect switch would be
    dead code without this. Captures the SQL ``run_migrations`` would emit,
    mirroring ``test_oidc_icon_migration_pg.py``.
    """

    @staticmethod
    async def _capture_sql(is_sqlite_value: bool) -> list[str]:
        from unittest.mock import AsyncMock, MagicMock, patch

        from backend.app.core import database as db_module

        class _AsyncCtxStub:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

        executed_sql: list[str] = []

        async def fake_safe_execute(_conn, sql: str) -> None:
            executed_sql.append(sql)

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

        return executed_sql

    @pytest.mark.asyncio
    async def test_pg_branch_uses_true_and_if_not_exists(self):
        executed = await self._capture_sql(is_sqlite_value=False)
        stmts = [s for s in executed if "controls_printer_power" in s]

        assert len(stmts) == 1, f"expected exactly one statement, got: {stmts!r}"
        assert "IF NOT EXISTS" in stmts[0]  # idempotent on PG, which has no _safe_execute retry semantics
        assert "DEFAULT true" in stmts[0]

    @pytest.mark.asyncio
    async def test_sqlite_branch_uses_numeric_default(self):
        """SQLite has no true/false literal — the switch must not be inverted."""
        executed = await self._capture_sql(is_sqlite_value=True)
        stmts = [s for s in executed if "controls_printer_power" in s]

        assert len(stmts) == 1
        assert "DEFAULT 1" in stmts[0]
        assert "true" not in stmts[0]
