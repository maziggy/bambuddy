"""Verify the dialect-conditional branch of the icon-column migration (#1333).

``run_migrations`` issues ``ALTER TABLE … ADD COLUMN icon_data {BLOB|BYTEA}``
based on ``is_sqlite()``. The full migration only runs against a live
engine, so we monkey-patch ``is_sqlite()`` and capture the SQL passed to
``_safe_execute``. Mirrors the test pattern at
``backend/tests/unit/test_db_dialect.py`` (lines around 539-606) which is
already used to verify other dialect-conditional migrations.

Without this test the PostgreSQL branch would be dead code in CI (the
project's tests run on SQLite) and a typo in the BYTEA emission would
slip silently to production, where ``_safe_execute`` would swallow the
column-creation failure and PG users would never cache icon bytes.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.core import database as db_module


class _AsyncCtxStub:
    """Async context manager that does nothing — for ``begin_nested()``."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


async def _capture_sql(is_sqlite_value: bool) -> list[str]:
    """Patch ``is_sqlite()`` + ``_safe_execute`` and return every SQL string
    that would have been executed during ``run_migrations``.

    Sub-migration callables that don't emit ALTER TABLE icon_data (the auto-
    link constraint update and the AMS-id widening) are no-op'd to keep the
    test focused on the icon migration.

    ``run_migrations`` uses ``async with conn.begin_nested()`` for the few
    DML backfills, so the fake conn returns a real async context manager.
    Inline ``conn.execute()`` calls (in the SQLite-recreation branch only,
    which we exclude) are also wired up to record SQL — but the bulk of
    the DDL goes through ``_safe_execute`` which is what we capture.
    """
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
async def test_pg_branch_uses_bytea_for_icon_data():
    """is_sqlite()=False must emit ``ADD COLUMN icon_data BYTEA``."""
    executed = await _capture_sql(is_sqlite_value=False)
    icon_data_stmts = [s for s in executed if "ADD COLUMN icon_data" in s]
    assert len(icon_data_stmts) == 1, f"expected exactly one icon_data ADD COLUMN statement, got: {icon_data_stmts!r}"
    assert "BYTEA" in icon_data_stmts[0]
    assert "BLOB" not in icon_data_stmts[0]


@pytest.mark.asyncio
async def test_sqlite_branch_uses_blob_for_icon_data():
    """is_sqlite()=True must emit ``ADD COLUMN icon_data BLOB``.

    Companion to the PG test — together they guarantee the
    ``is_sqlite()`` switch wasn't accidentally inverted.
    """
    executed = await _capture_sql(is_sqlite_value=True)
    icon_data_stmts = [s for s in executed if "ADD COLUMN icon_data" in s]
    assert len(icon_data_stmts) == 1
    assert "BLOB" in icon_data_stmts[0]
    assert "BYTEA" not in icon_data_stmts[0]


@pytest.mark.asyncio
async def test_icon_content_type_and_etag_columns_both_dialects():
    """The two String columns are dialect-independent (VARCHAR works on
    both SQLite and PostgreSQL). Verify both branches emit them."""
    for is_sqlite_value in (True, False):
        executed = await _capture_sql(is_sqlite_value=is_sqlite_value)
        content_type_stmts = [s for s in executed if "ADD COLUMN icon_content_type" in s]
        etag_stmts = [s for s in executed if "ADD COLUMN icon_etag" in s]
        assert len(content_type_stmts) == 1, f"is_sqlite={is_sqlite_value}: {content_type_stmts!r}"
        assert len(etag_stmts) == 1, f"is_sqlite={is_sqlite_value}: {etag_stmts!r}"
        assert "VARCHAR" in content_type_stmts[0].upper()
        assert "VARCHAR" in etag_stmts[0].upper()
