"""Startup migrations leave no ERRORs in the PostgreSQL server log.

Nearly every ADD COLUMN in run_migrations() is a re-run on every start, because
create_all() has already made the column. _safe_execute swallowed the duplicate,
but PostgreSQL had written it to its own log first: ~320 ERROR + STATEMENT pairs
per start on every PostgreSQL install, measured on postgres:16. On PostgreSQL the
DDL is now made a no-op when already applied (IF NOT EXISTS, or a catalog check
where no IF NOT EXISTS exists), so the server skips it with a NOTICE instead.

Measured against a real postgres:16 after the change: 0 ERRORs on a fresh
database, 0 on a re-run, 0 on an older schema whose columns, rename and
constraint were then applied, and pg_dump -s identical to the schema before.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.core import database
from backend.app.core.database import _pg_already_applied, _pg_if_not_exists


class TestTheRewrite:
    @pytest.mark.parametrize(
        "sql,expected",
        [
            (
                "ALTER TABLE spool ADD COLUMN tag_type VARCHAR(20)",
                "ALTER TABLE spool ADD COLUMN IF NOT EXISTS tag_type VARCHAR(20)",
            ),
            (
                "ALTER TABLE print_queue ADD COLUMN batch_id INTEGER REFERENCES print_batches(id) ON DELETE SET NULL",
                "ALTER TABLE print_queue ADD COLUMN IF NOT EXISTS batch_id INTEGER REFERENCES print_batches(id) "
                "ON DELETE SET NULL",
            ),
            ("alter table t add column c int", "alter table t add column IF NOT EXISTS c int"),
            ("CREATE INDEX ix_a ON t (a)", "CREATE INDEX IF NOT EXISTS ix_a ON t (a)"),
            ("CREATE UNIQUE INDEX ux_a ON t (a)", "CREATE UNIQUE INDEX IF NOT EXISTS ux_a ON t (a)"),
            ("CREATE TABLE t (id INTEGER)", "CREATE TABLE IF NOT EXISTS t (id INTEGER)"),
        ],
    )
    def test_already_applied_ddl_becomes_a_no_op(self, sql, expected):
        assert _pg_if_not_exists(sql) == expected

    @pytest.mark.parametrize(
        "sql",
        [
            "ALTER TABLE t ADD COLUMN IF NOT EXISTS c INT",
            "CREATE INDEX IF NOT EXISTS ix_a ON t (a)",
            "CREATE TABLE IF NOT EXISTS t (id INTEGER)",
            # CONCURRENTLY has its own syntax position; leave it alone.
            "CREATE INDEX CONCURRENTLY ix_a ON t (a)",
            "ALTER TABLE t ALTER COLUMN c SET DEFAULT 0",
            "ALTER TABLE t DROP COLUMN c",
            "ALTER TABLE t ADD CONSTRAINT ck CHECK (a > 0)",
            "UPDATE t SET c = 1 WHERE c IS NULL",
            "SELECT 1",
        ],
    )
    def test_everything_else_is_untouched(self, sql):
        assert _pg_if_not_exists(sql) == sql


class _CatalogConn:
    """Answers the catalog query with a canned result and records it."""

    def __init__(self, result):
        self.result = result
        self.asked = []

    async def scalar(self, stmt, params):
        self.asked.append((str(stmt), params))
        return self.result


class TestTheCatalogCheck:
    ADD_CONSTRAINT = "ALTER TABLE oidc_providers ADD CONSTRAINT ck_x CHECK (a = FALSE OR b = TRUE)"
    RENAME = "ALTER TABLE project_bom_items RENAME COLUMN notes TO remarks"

    @pytest.mark.asyncio
    async def test_an_existing_constraint_is_skipped(self):
        conn = _CatalogConn(1)
        assert await _pg_already_applied(conn, self.ADD_CONSTRAINT) is True
        assert conn.asked[0][1] == {"name": "ck_x", "table": "oidc_providers"}

    @pytest.mark.asyncio
    async def test_a_missing_constraint_is_added(self):
        assert await _pg_already_applied(_CatalogConn(None), self.ADD_CONSTRAINT) is False

    @pytest.mark.asyncio
    async def test_a_rename_whose_old_column_is_gone_already_ran(self):
        conn = _CatalogConn(None)
        assert await _pg_already_applied(conn, self.RENAME) is True
        assert conn.asked[0][1] == {"table": "project_bom_items", "column": "notes"}

    @pytest.mark.asyncio
    async def test_a_rename_whose_old_column_is_there_still_runs(self):
        assert await _pg_already_applied(_CatalogConn(1), self.RENAME) is False

    @pytest.mark.asyncio
    async def test_other_ddl_is_not_asked_about(self):
        conn = _CatalogConn(1)
        assert await _pg_already_applied(conn, "ALTER TABLE t ADD COLUMN c INT") is False
        assert conn.asked == []


class TestTheHookIsScoped:
    @pytest.mark.asyncio
    async def test_sqlite_gets_no_hook(self, monkeypatch):
        listened = []
        monkeypatch.setattr(database.event, "listen", lambda *a, **k: listened.append(a))

        async def body(conn):
            return None

        monkeypatch.setattr(database, "_run_migrations", body)
        await database.run_migrations(SimpleNamespace(dialect=SimpleNamespace(name="sqlite")))
        assert listened == []

    @pytest.mark.asyncio
    async def test_postgres_hook_is_removed_even_when_a_migration_fails(self, monkeypatch):
        """Only the migration run is rewritten, never a runtime statement."""
        calls = []
        monkeypatch.setattr(database.event, "listen", lambda target, name, fn, **k: calls.append(("listen", fn)))
        monkeypatch.setattr(database.event, "remove", lambda target, name, fn: calls.append(("remove", fn)))

        async def boom(conn):
            raise RuntimeError("migration failed")

        monkeypatch.setattr(database, "_run_migrations", boom)
        conn = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"), sync_connection=object())
        with pytest.raises(RuntimeError):
            await database.run_migrations(conn)
        assert calls == [
            ("listen", database._pg_ddl_if_not_exists),
            ("remove", database._pg_ddl_if_not_exists),
        ]

    def test_the_hook_rewrites_and_keeps_the_parameters(self):
        params = {"a": 1}
        stmt, out = database._pg_ddl_if_not_exists(None, None, "ALTER TABLE t ADD COLUMN c INT", params, None, False)
        assert stmt == "ALTER TABLE t ADD COLUMN IF NOT EXISTS c INT"
        assert out is params
