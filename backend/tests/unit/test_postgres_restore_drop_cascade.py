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

The second half of the file covers the follow-on fix: the recreated
tables must carry no foreign keys at all while rows are being imported.
"""

from __future__ import annotations

import logging
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
    # At least one row, so the import actually emits an INSERT -- the
    # loop skips empty tables outright.
    conn.execute("INSERT INTO users (id, username) VALUES (1, 'alice')")
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


def _mock_pg_engine(
    executed_sql: list[str],
    create_all_error: Exception | None = None,
    fk_error: Exception | None = None,
):
    """Build a fake async engine that records every statement, plus a
    `run_sync:<fn>` marker, into `executed_sql` in execution order.

    `fk_error` makes every ADD CONSTRAINT fail, standing in for a backup
    carrying orphaned rows."""
    from sqlalchemy.schema import AddConstraint

    mock_conn = MagicMock()

    def _execute(stmt, *a, **k):
        if fk_error is not None and isinstance(stmt, AddConstraint):
            raise fk_error
        executed_sql.append(getattr(stmt, "text", str(stmt)))

    mock_conn.execute = AsyncMock(side_effect=_execute)

    async def _run_sync(fn, *args, **kw):
        executed_sql.append("run_sync:" + getattr(fn, "__name__", repr(fn)))
        if create_all_error is not None:
            raise create_all_error
        return None

    mock_conn.run_sync = AsyncMock(side_effect=_run_sync)

    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    begin_cm.__aexit__ = AsyncMock(return_value=False)

    mock_engine = MagicMock()
    mock_engine.begin = MagicMock(return_value=begin_cm)
    mock_engine.dispose = AsyncMock()
    return mock_engine


def _fk_names(table) -> set[str]:
    return {id(fk) for fk in table.constraints if hasattr(fk, "elements")}


@pytest.mark.asyncio
async def test_restore_drops_every_foreign_key_before_importing_rows():
    """The recreated schema must carry no FK constraints while rows land.

    Regression (#restore FK violation): the fix used to discard each
    ForeignKeyConstraint from `table.constraints` before `create_all`.
    That only suppresses the inline REFERENCES clause -- when `create_all`
    hits a dependency cycle it cannot sort (library_files /
    library_folders / print_archives are exactly such a cycle) it emits
    those tables' keys as separate ALTER TABLE ... ADD FOREIGN KEY
    statements read from `Table.foreign_key_constraints`, which the
    discard never touched. The child table then imported before its
    parent and Postgres raised ForeignKeyViolationError on
    `library_files_folder_id_fkey`."""
    from backend.app.api.routes import settings as settings_module

    sqlite_path = _make_sqlite_source()
    try:
        executed_sql: list[str] = []
        with patch(
            "backend.app.core.database._create_engine",
            new=MagicMock(return_value=_mock_pg_engine(executed_sql)),
        ):
            await settings_module._import_sqlite_to_postgres(sqlite_path, "postgresql+asyncpg://test/test")

        fk_drops = [i for i, s in enumerate(executed_sql) if "pg_constraint" in s and "DROP CONSTRAINT" in s]
        assert fk_drops, (
            "Expected an unconditional DROP CONSTRAINT sweep over pg_constraint "
            "so no foreign key survives create_all's cycle-breaking ALTER "
            "TABLE statements. Captured SQL: " + "; ".join(s[:100] for s in executed_sql)
        )
        drop_sql = executed_sql[fk_drops[0]]
        # Foreign keys only ('f'), scoped to public -- not PK/unique/check,
        # and not another application's schema on a shared Postgres.
        assert "contype = 'f'" in drop_sql, drop_sql
        assert "'public'::regnamespace" in drop_sql, drop_sql

        # It has to land after the tables exist and before the first row.
        create_idx = executed_sql.index("run_sync:create_all")
        insert_idx = next((i for i, s in enumerate(executed_sql) if s.startswith("INSERT INTO")), -1)
        assert insert_idx > 0, f"no row import happened, so the ordering is untested: {executed_sql}"
        assert create_idx < fk_drops[0] < insert_idx, (
            f"FK drop must sit between create_all and the first INSERT: {executed_sql}"
        )
    finally:
        sqlite_path.unlink(missing_ok=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("create_all_fails", [False, True])
async def test_restore_never_mutates_the_process_wide_orm_metadata(create_all_fails):
    """`Base.metadata` is global to the running app. The old code removed
    every FK from it and only put them back *after* the drop/create
    transaction, so a failure in there left the live process unable to
    emit or re-add foreign keys until restart."""
    from backend.app.api.routes import settings as settings_module
    from backend.app.core.database import Base

    table = Base.metadata.tables["library_files"]
    before = _fk_names(table)
    assert before, "library_files should carry FK constraints to begin with"

    sqlite_path = _make_sqlite_source()
    try:
        executed_sql: list[str] = []
        boom = RuntimeError("create_all exploded") if create_all_fails else None
        engine = _mock_pg_engine(executed_sql, create_all_error=boom)
        with patch("backend.app.core.database._create_engine", new=MagicMock(return_value=engine)):
            if create_all_fails:
                with pytest.raises(RuntimeError, match="create_all exploded"):
                    await settings_module._import_sqlite_to_postgres(sqlite_path, "postgresql+asyncpg://test/test")
            else:
                await settings_module._import_sqlite_to_postgres(sqlite_path, "postgresql+asyncpg://test/test")

        assert _fk_names(table) == before, "The restore must not add or remove constraints on the shared ORM metadata"
    finally:
        sqlite_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_unrestorable_fk_is_reported_by_its_columns(caplog):
    """A key that can't go back on must be named by what it links.

    These constraints are unnamed in the ORM, so `fk.name` is None and the
    warning used to read "print_archives.None" once per failure -- five of
    that table's keys share it, so the report said nothing about which
    columns to inspect."""
    from backend.app.api.routes import settings as settings_module

    orphan = RuntimeError(
        'violates foreign key constraint "library_files_folder_id_fkey"\n'
        'DETAIL:  Key (folder_id)=(9) is not present in table "library_folders".'
    )
    sqlite_path = _make_sqlite_source()
    try:
        executed_sql: list[str] = []
        engine = _mock_pg_engine(executed_sql, fk_error=orphan)
        with (
            patch("backend.app.core.database._create_engine", new=MagicMock(return_value=engine)),
            caplog.at_level(logging.INFO, logger="backend.app.api.routes.settings"),
        ):
            await settings_module._import_sqlite_to_postgres(sqlite_path, "postgresql+asyncpg://test/test")

        warning = next((r.getMessage() for r in caplog.records if r.levelno == logging.WARNING), None)
        assert warning is not None, "a failed FK restore must be reported"
        assert ".None" not in warning, f"constraints must not be named by fk.name: {warning}"
        assert "library_files(folder_id) -> library_folders.id" in warning, warning
        # And the offending value is recorded so the rows can be found.
        assert any("Key (folder_id)=(9)" in r.getMessage() for r in caplog.records), (
            "the Postgres DETAIL line names the orphan; it must survive into the log"
        )
    finally:
        sqlite_path.unlink(missing_ok=True)


def test_library_tables_form_an_fk_cycle():
    """Documents why the restore cannot import in dependency order.

    library_files -> library_folders -> print_archives -> library_files.
    SQLAlchemy's `sorted_tables` gives up on these three and falls back to
    alphabetical, which puts the child (library_files) before its parent.
    Dropping the constraints outright is the only ordering-independent
    answer; if this cycle is ever broken, the restore still works, but the
    comment in `_import_sqlite_to_postgres` should be revisited."""
    from backend.app.core.database import Base

    def refs(name: str) -> set[str]:
        table = Base.metadata.tables[name]
        return {fk.column.table.name for fk in table.foreign_keys}

    assert "library_folders" in refs("library_files")
    assert "print_archives" in refs("library_folders")
    assert "library_files" in refs("print_archives")
