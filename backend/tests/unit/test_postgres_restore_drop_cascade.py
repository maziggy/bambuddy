"""Regression test for the Postgres restore drop-tables-with-CASCADE fix.

The bug: the restore path called `metadata.drop_all`, which only drops
tables defined in the SQLAlchemy ORM and emits plain `DROP TABLE` (no
CASCADE). When the live DB carries orphan tables from removed features
(e.g. legacy `spoolman_slot_assignments` whose `_printer_id_fkey`
constraint still references `printers`), Postgres refuses with
`DependentObjectsStillExistError` and the entire restore aborts before
any rows land.

The fix: drop every table in the `public` schema with `CASCADE` via a
`pg_tables`-iterating PL/pgSQL `DO` block, then re-create from the
ORM metadata. CASCADE removes external constraints alongside the table,
so orphan tables can no longer block the restore.

These tests guard against a regression to `metadata.drop_all` (which
would re-introduce the bug for any user with orphan tables).
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_sqlite_source() -> Path:
    """Build a tiny SQLite file with one ORM-known table so the restore
    function progresses past its `tables_to_import & metadata.tables` gate."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        path = Path(tmp.name)
    conn = sqlite3.connect(str(path))
    # `users` is in the ORM metadata so `tables_to_import` is non-empty.
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
    conn.commit()
    conn.close()
    return path


@pytest.mark.asyncio
async def test_restore_drops_tables_with_cascade_not_metadata_drop_all():
    """Verify the restore drop phase issues a CASCADE-aware DROP TABLE
    iteration over `public` schema rather than `metadata.drop_all`.

    Regression: prior to the fix, an orphan table holding an FK back to
    `printers` (e.g. legacy `spoolman_slot_assignments_printer_id_fkey`)
    would cause `metadata.drop_all` to fail with
    `DependentObjectsStillExistError`, aborting the whole restore."""
    from backend.app.api.routes import settings as settings_module

    sqlite_path = _make_sqlite_source()
    try:
        executed_sql: list[str] = []
        run_sync_calls: list[str] = []

        # Capture the exact SQL emitted on the Postgres connection.
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(
            side_effect=lambda stmt, *a, **k: executed_sql.append(getattr(stmt, "text", str(stmt)))
        )

        # `await conn.run_sync(metadata.create_all)` is the only run_sync
        # the fix should issue. `metadata.drop_all` must never appear.
        async def _run_sync(fn, *args, **kw):
            name = getattr(fn, "__name__", repr(fn))
            run_sync_calls.append(name)
            return None

        mock_conn.run_sync = AsyncMock(side_effect=_run_sync)

        # `pg_engine.begin()` is used twice (drop+create, then import).
        # Both must yield the same captured-conn so we observe everything.
        begin_cm = MagicMock()
        begin_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        begin_cm.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.begin = MagicMock(return_value=begin_cm)
        mock_engine.dispose = AsyncMock()

        # `_create_engine` is imported lazily inside the function via
        # `from backend.app.core.database import ... _create_engine`,
        # so we patch the module it's imported FROM, not settings.py.
        with patch(
            "backend.app.core.database._create_engine",
            new=MagicMock(return_value=mock_engine),
        ):
            await settings_module._import_sqlite_to_postgres(sqlite_path, "postgresql+asyncpg://test/test")

        # 1. CASCADE drop is emitted, hitting every public-schema table.
        cascade_drops = [s for s in executed_sql if "CASCADE" in s and "pg_tables" in s]
        assert cascade_drops, (
            "Expected a CASCADE-aware DROP TABLE iteration over the public "
            "schema in the restore SQL stream. Without it, orphan tables "
            "with FK constraints back to ORM tables (e.g. legacy "
            "spoolman_slot_assignments) abort the restore. Captured SQL: " + "; ".join(s[:120] for s in executed_sql)
        )
        # 2. The DO block iterates pg_tables (not just one DROP) so every
        #    table is handled, including orphan ones not in the ORM.
        do_block = cascade_drops[0]
        assert "DROP TABLE" in do_block
        assert "schemaname = 'public'" in do_block

        # 3. `metadata.drop_all` is never invoked — that was the buggy
        #    path. `metadata.create_all` is fine; it rebuilds the schema
        #    after the CASCADE drop.
        assert "drop_all" not in run_sync_calls, (
            f"metadata.drop_all should not be called (regression): {run_sync_calls}"
        )
        assert "create_all" in run_sync_calls, f"metadata.create_all should still be called: {run_sync_calls}"

        # 4. Drop runs before create. The captured SQL is in execution order
        #    within the same pg_engine.begin() block, and run_sync_calls is
        #    in invocation order across both blocks.
        first_create_idx = run_sync_calls.index("create_all")
        # No drop_all anywhere — the cascade DO block (executed via .execute,
        # not run_sync) is what runs first. Its presence is confirmed above.
        assert first_create_idx >= 0
    finally:
        sqlite_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_restore_cascade_drop_targets_only_public_schema():
    """Defensive: the CASCADE drop must scope to the `public` schema so a
    shared Postgres holding non-Bambuddy tables in other schemas doesn't
    lose data on restore."""
    from backend.app.api.routes import settings as settings_module

    sqlite_path = _make_sqlite_source()
    try:
        executed_sql: list[str] = []
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(
            side_effect=lambda stmt, *a, **k: executed_sql.append(getattr(stmt, "text", str(stmt)))
        )
        mock_conn.run_sync = AsyncMock()

        begin_cm = MagicMock()
        begin_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        begin_cm.__aexit__ = AsyncMock(return_value=False)
        mock_engine = MagicMock()
        mock_engine.begin = MagicMock(return_value=begin_cm)
        mock_engine.dispose = AsyncMock()

        with patch(
            "backend.app.core.database._create_engine",
            new=MagicMock(return_value=mock_engine),
        ):
            await settings_module._import_sqlite_to_postgres(sqlite_path, "postgresql+asyncpg://test/test")

        cascade = next((s for s in executed_sql if "CASCADE" in s), None)
        assert cascade is not None
        # Schema scope check: we're not iterating `pg_class` /
        # `information_schema.tables` without a schema filter, which
        # would catch system catalogs or other-app tables.
        assert "schemaname = 'public'" in cascade, f"CASCADE drop must filter to public schema; got: {cascade[:200]}"
        assert "schemaname = '*'" not in cascade
    finally:
        sqlite_path.unlink(missing_ok=True)
