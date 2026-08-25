"""Migration idempotency must not depend on the server's message language (#2949).

``_safe_execute`` used to decide whether a failed DDL statement had simply
already been applied by looking for ``"already exists"`` in the error text.
PostgreSQL renders its messages in the server's ``lc_messages`` locale, so a
Russian-locale server answered a duplicate ``ADD COLUMN`` with
``столбец … уже существует`` — no English substring, so the statement was
re-raised and startup aborted.

That was not a corner case: ``create_all()`` runs before ``run_migrations()``,
so on a fresh database essentially every ``ADD COLUMN`` in the migration list is
*expected* to come back as a duplicate. The reporter's install died on the very
first one, and no PostgreSQL server outside an English locale could start at all.

The classifier now reads the SQLSTATE, which PostgreSQL never translates. These
tests pin both halves: the codes it accepts, the codes it must still let through,
and the SQLite fallback for a DBAPI that has no SQLSTATE to offer.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError

from backend.app.core.database import _is_already_applied, _safe_execute, _sqlstate

# Verbatim from a PostgreSQL 15 server running lc_messages=ru_RU.utf8 — the exact
# text the reporter pasted into the issue. Nothing in the classifier may read it.
RU_DUPLICATE_COLUMN = 'столбец "parent_run_id" отношения "pipeline_runs" уже существует'
RU_DUPLICATE_TABLE = 'отношение "t_dup" уже существует'
RU_DUPLICATE_OBJECT = 'ограничение-проверка "ck_a" для отношения "t_ck" уже существует'
RU_UNDEFINED_COLUMN = 'столбец "nope" не существует'
RU_UNDEFINED_TABLE = 'отношение "no_such_table" не существует'

ADD_COLUMN = (
    "ALTER TABLE pipeline_runs ADD COLUMN parent_run_id INTEGER REFERENCES pipeline_runs(id) ON DELETE SET NULL"
)
RENAME_COLUMN = "ALTER TABLE t_rn RENAME COLUMN nope TO other"
CREATE_INDEX = "CREATE INDEX ix_t ON t (nope)"


class _FakeOrig(Exception):
    """Stand-in for asyncpg's DBAPI wrapper, which exposes ``sqlstate``."""

    def __init__(self, sqlstate: str, message: str):
        super().__init__(message)
        self.sqlstate = sqlstate


def _pg_error(sqlstate: str, message: str, sql: str) -> ProgrammingError:
    return ProgrammingError(sql, {}, _FakeOrig(sqlstate, message))


class TestSqlstateIsPreferredOverTheMessage:
    @pytest.mark.parametrize(
        "sqlstate,message,sql",
        [
            ("42701", RU_DUPLICATE_COLUMN, ADD_COLUMN),  # duplicate_column — the reported failure
            ("42P07", RU_DUPLICATE_TABLE, "CREATE TABLE t_dup (id INTEGER)"),  # duplicate_table
            ("42P07", RU_DUPLICATE_TABLE, "CREATE INDEX ix_t_idx ON t_idx (a)"),  # duplicate index
            ("42710", RU_DUPLICATE_OBJECT, "ALTER TABLE t_ck ADD CONSTRAINT ck_a CHECK (a > 0)"),  # duplicate_object
        ],
    )
    def test_a_russian_language_duplicate_is_recognised(self, sqlstate, message, sql):
        assert _is_already_applied(_pg_error(sqlstate, message, sql), sql) is True

    def test_undefined_column_is_idempotency_only_for_rename(self):
        """The rename already ran. On any other statement a missing column means
        the schema is broken, and swallowing it would hide the corruption."""
        rename = _pg_error("42703", RU_UNDEFINED_COLUMN, RENAME_COLUMN)
        assert _is_already_applied(rename, RENAME_COLUMN) is True

        index = _pg_error("42703", RU_UNDEFINED_COLUMN, CREATE_INDEX)
        assert _is_already_applied(index, CREATE_INDEX) is False

    @pytest.mark.parametrize(
        "sqlstate,message",
        [
            ("42P01", RU_UNDEFINED_TABLE),  # undefined_table — migrating a table that isn't there
            ("42704", 'тип "notatype" не существует'),  # undefined_object — a typo'd column type
            ("42601", "ошибка синтаксиса"),  # syntax_error
        ],
    )
    def test_real_failures_still_abort_startup(self, sqlstate, message):
        exc = _pg_error(sqlstate, message, ADD_COLUMN)
        assert _is_already_applied(exc, ADD_COLUMN) is False

    def test_an_english_message_saying_already_exists_cannot_rescue_a_fatal_sqlstate(self):
        """Once a SQLSTATE is present it is the whole answer. A server whose text
        happens to contain the old keyword must not talk us out of a real error.
        """
        exc = _pg_error("42P01", 'relation "printers" does not exist, table already exists', ADD_COLUMN)
        assert _is_already_applied(exc, ADD_COLUMN) is False


class TestSqlstateExtraction:
    def test_reads_sqlstate_from_the_dbapi_error(self):
        assert _sqlstate(_pg_error("42701", RU_DUPLICATE_COLUMN, ADD_COLUMN)) == "42701"

    def test_falls_back_to_pgcode(self):
        """psycopg spells the same value ``pgcode``."""

        class _Psycopg(Exception):
            pgcode = "42P07"

        exc = ProgrammingError("CREATE TABLE t (a INTEGER)", {}, _Psycopg())
        assert _sqlstate(exc) == "42P07"

    def test_none_when_the_driver_offers_no_code(self):
        """SQLite. Callers fall back to matching the message, which SQLite —
        unlike PostgreSQL — never translates."""
        exc = OperationalError("ALTER TABLE t ADD COLUMN a INTEGER", {}, Exception("duplicate column name: a"))
        assert _sqlstate(exc) is None


class TestSqliteFallback:
    """No SQLSTATE, so the message keywords still decide — unchanged behaviour."""

    @pytest.mark.parametrize(
        "message,sql,expected",
        [
            ("duplicate column name: a", ADD_COLUMN, True),
            ("table t already exists", "CREATE TABLE t (a INTEGER)", True),
            ("no such column: nope", RENAME_COLUMN, True),
            ("no such table: printers", ADD_COLUMN, False),
            ('near "GARBAGE": syntax error', ADD_COLUMN, False),
        ],
    )
    def test_message_keywords(self, message, sql, expected):
        exc = OperationalError(sql, {}, Exception(message))
        assert _is_already_applied(exc, sql) is expected


@pytest.mark.asyncio
class TestSafeExecuteAgainstRealSqlite:
    async def test_a_duplicate_add_column_is_swallowed(self, db_session):
        conn = await db_session.connection()
        await conn.execute(text("CREATE TABLE t_2949 (id INTEGER PRIMARY KEY)"))
        await _safe_execute(conn, "ALTER TABLE t_2949 ADD COLUMN extra INTEGER")
        await _safe_execute(conn, "ALTER TABLE t_2949 ADD COLUMN extra INTEGER")

        cols = {row[1] for row in await conn.execute(text("PRAGMA table_info(t_2949)"))}
        assert cols == {"id", "extra"}

    async def test_a_genuine_failure_is_re_raised(self, db_session):
        conn = await db_session.connection()
        with pytest.raises(OperationalError):
            await _safe_execute(conn, "ALTER TABLE table_that_does_not_exist ADD COLUMN x INTEGER")
