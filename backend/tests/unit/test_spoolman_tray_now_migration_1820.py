"""Migration for active_print_spoolman.tray_now_at_start (#1820).

The remain%-delta fallback needs to know which slot a print actually drew from,
or a spool swapped into an idle slot mid-print is charged for consumption it
never had. For a print with no 3MF -- the case this fallback exists for -- the
tray in use at the start is often the only evidence, so it is captured at print
start and has to survive on an upgraded database.

Nullable, not backfilled: a row written before the column existed has no answer
to give, and inventing 0 would name a real slot.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import run_migrations

LEGACY_TABLE = """
CREATE TABLE active_print_spoolman (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    printer_id INTEGER NOT NULL,
    archive_id INTEGER NOT NULL,
    filament_usage TEXT,
    ams_trays TEXT NOT NULL,
    slot_to_tray TEXT,
    layer_usage TEXT,
    filament_properties TEXT,
    tray_remain_start TEXT,
    UNIQUE(printer_id, archive_id)
)
"""


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """settings.database_url may point at Postgres in dev configs; the test
    engine is SQLite, so force the dialect where run_migrations reads it."""
    from backend.app.core import database as database_module, db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


def _register_every_model() -> None:
    """Put every table on ``Base.metadata``.

    ``backend.app.models``'s ``__init__`` re-exports only some of them, and
    ``run_migrations`` walks the whole schema -- a table that was never
    imported is missing, and its ALTER fails the run before reaching ours.
    Walking the package keeps this from rotting as models are added.
    """
    import importlib
    import pkgutil

    import backend.app.models as models_pkg

    for module in pkgutil.iter_modules(models_pkg.__path__):
        importlib.import_module(f"backend.app.models.{module.name}")


@pytest.fixture
async def legacy_engine():
    """A modern schema whose tracking table predates the column, mid-print."""
    from backend.app.core.database import Base

    _register_every_model()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("DROP TABLE active_print_spoolman"))
        await conn.execute(text(LEGACY_TABLE))
        await conn.execute(
            text(
                "INSERT INTO active_print_spoolman (id, printer_id, archive_id, ams_trays, tray_remain_start) "
                'VALUES (1, 1, 42, \'{}\', \'{"0-0": {"remain": 80, "tray_uuid": "AAAA"}}\')'
            )
        )
    yield engine
    await engine.dispose()


async def test_column_missing_before_migration(legacy_engine):
    """Sanity check, so the assertion below cannot pass by accident."""
    async with legacy_engine.begin() as conn:
        columns = {row[1] for row in await conn.execute(text("PRAGMA table_info(active_print_spoolman)"))}

    assert "tray_now_at_start" not in columns


async def test_the_column_is_added_and_the_row_survives(legacy_engine):
    """A print running across the upgrade keeps its remain snapshot; it simply
    has no tray evidence, which the fallback reads as "consider every slot"."""
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)

    async with legacy_engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT tray_now_at_start, tray_remain_start FROM active_print_spoolman WHERE id = 1")
            )
        ).one()

    assert row[0] is None
    assert "AAAA" in row[1]


async def test_migration_is_idempotent(legacy_engine):
    """Second boot must not fail on the already-present column."""
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)
    async with legacy_engine.begin() as conn:
        await run_migrations(conn)

    async with legacy_engine.begin() as conn:
        columns = {row[1] for row in await conn.execute(text("PRAGMA table_info(active_print_spoolman)"))}

    assert "tray_now_at_start" in columns


async def test_a_fresh_database_has_the_column(legacy_engine):
    """The CREATE TABLE carries it too, so a new install never runs the ALTER."""
    from backend.app.core.database import Base

    _register_every_model()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            columns = {row[1] for row in await conn.execute(text("PRAGMA table_info(active_print_spoolman)"))}
    finally:
        await engine.dispose()

    assert "tray_now_at_start" in columns


class TestPostgresBranch:
    """CI runs on SQLite, so the Postgres side of the dialect switch would be
    dead code without this. Captures the SQL run_migrations would emit,
    mirroring test_smart_plug_power_flag_migration_2629.
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

    @staticmethod
    def _alter_statements(executed: list[str]) -> list[str]:
        return [s for s in executed if "tray_now_at_start" in s and "ALTER" in s.upper()]

    @pytest.mark.asyncio
    async def test_pg_branch_is_idempotent_on_its_own(self):
        """Postgres has no _safe_execute retry semantics to lean on."""
        stmts = self._alter_statements(await self._capture_sql(is_sqlite_value=False))

        assert len(stmts) == 1, f"expected exactly one ALTER, got: {stmts!r}"
        assert "IF NOT EXISTS" in stmts[0]
        assert "INTEGER" in stmts[0]

    @pytest.mark.asyncio
    async def test_sqlite_branch_omits_if_not_exists(self):
        """SQLite's ALTER TABLE has no IF NOT EXISTS; _safe_execute swallows the
        duplicate-column error instead."""
        stmts = self._alter_statements(await self._capture_sql(is_sqlite_value=True))

        assert len(stmts) == 1
        assert "IF NOT EXISTS" not in stmts[0]
        assert "INTEGER" in stmts[0]
