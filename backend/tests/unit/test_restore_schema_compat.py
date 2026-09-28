"""Restoring a backup made by a different version of Bambuddy.

A backup carries the schema of the install that made it, and the Postgres
restore path does NOT use that schema: it drops every table, recreates them
from the running process's ORM, and inserts the backup's columns into the
result. So any NOT NULL column the running version has and the backup does not
arrives with nothing to put in it.

That is how a 2026-09-23 backup failed to restore on the published image:
``user_wallets.currency`` was dropped from the model in #3123, the backup
therefore had no such column, the older image still declared it NOT NULL, and
the import died on::

    null value in column "currency" of relation "user_wallets"

Its ``default="EUR"`` could not help -- SQLAlchemy applies a Python-side default
to ORM and Core inserts, never to the raw ``text()`` SQL this import builds, and
``create_all`` emits no DDL default for one.

Two things have to hold, and the second is the serious one:

1. A column with a default is filled rather than refused.
2. A column that cannot be filled is refused BEFORE the restore drops anything.
   The drop is the first thing the import does, in its own transaction, so a
   refusal at the INSERT means the install's data is already gone -- and the
   restore has by then also overwritten the MFA key file, leaving whatever
   survives encrypted under a key that no longer matches.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import Column, DateTime, Integer, MetaData, Numeric, String, Table, func

from backend.app.api.routes.settings import (
    BackupSchemaIncompatible,
    _missing_required_columns,
    _read_backup_manifest,
    check_backup_schema_compatible,
)


def _wallets_table(metadata: MetaData) -> Table:
    """`user_wallets` as the published image still declares it."""
    return Table(
        "user_wallets",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("user_id", Integer, nullable=False),
        Column("balance", Numeric(14, 2), nullable=False, default=0.0),
        Column("currency", String(3), nullable=False, default="EUR"),
        Column("updated_at", DateTime, nullable=False, server_default=func.now()),
    )


# ---------------------------------------------------------------------------
# Which missing columns are a problem
# ---------------------------------------------------------------------------


def test_a_column_with_a_model_default_is_filled_not_refused():
    """The exact case from the incident, with the values it would have used."""
    table = _wallets_table(MetaData())

    injectable, db_filled, unfillable = _missing_required_columns(table, {"id", "user_id", "balance", "updated_at"})

    assert injectable == {"currency": "EUR"}
    assert unfillable == []
    assert db_filled == []


def test_a_column_with_a_server_default_is_left_to_the_database():
    """Omitting it from the INSERT is right: Postgres fills it. Sending the
    ORM's idea of the default instead would overwrite a timestamp the database
    is better placed to produce."""
    table = _wallets_table(MetaData())

    injectable, db_filled, unfillable = _missing_required_columns(table, {"id", "user_id", "balance", "currency"})

    assert db_filled == ["updated_at"]
    assert injectable == {}
    assert unfillable == []


def test_a_callable_default_is_evaluated():
    table = Table(
        "cost_centers",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("code", String(32), nullable=False, default=lambda: "generated"),
    )

    injectable, _, unfillable = _missing_required_columns(table, {"id"})

    assert injectable == {"code": "generated"}
    assert unfillable == []


def test_a_required_column_with_no_default_is_unfillable():
    table = Table(
        "cost_centers",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("name", String(150), nullable=False),
    )

    injectable, _, unfillable = _missing_required_columns(table, {"id"})

    assert unfillable == ["name"]
    assert injectable == {}


def test_a_nullable_column_the_backup_lacks_is_not_a_problem():
    """Most schema drift is this, and it has always worked: the column is
    simply omitted and the row gets NULL."""
    table = Table(
        "printers",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("nickname", String(50), nullable=True),
    )

    injectable, db_filled, unfillable = _missing_required_columns(table, {"id"})

    assert (injectable, db_filled, unfillable) == ({}, [], [])


# ---------------------------------------------------------------------------
# The preflight, against a real backup file
# ---------------------------------------------------------------------------


def _source(tmp_path: Path, ddl: str, rows: list[str]) -> Path:
    path = tmp_path / "bambuddy.db"
    conn = sqlite3.connect(path)
    conn.execute(ddl)
    for row in rows:
        conn.execute(row)
    conn.commit()
    conn.close()
    return path


def test_a_backup_this_version_can_import_is_accepted(tmp_path):
    """`users` as the ORM has it, minus columns that are nullable or defaulted."""
    path = _source(
        tmp_path,
        "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)",
        ["INSERT INTO users (id, username) VALUES (1, 'alice')"],
    )

    check_backup_schema_compatible(path)  # does not raise


def test_a_backup_missing_a_required_column_is_refused(tmp_path):
    """`cost_centers.name` is NOT NULL with no default anywhere."""
    path = _source(
        tmp_path,
        "CREATE TABLE cost_centers (id INTEGER PRIMARY KEY, code TEXT)",
        ["INSERT INTO cost_centers (id, code) VALUES (1, 'abc')"],
    )

    with pytest.raises(BackupSchemaIncompatible) as exc:
        check_backup_schema_compatible(path)

    assert "cost_centers.name" in str(exc.value)
    assert "Nothing has been changed" in str(exc.value)


def test_the_refusal_names_the_version_that_made_the_backup(tmp_path):
    """A column name tells an operator nothing about what to do. Two version
    numbers tell them which install to restore on."""
    path = _source(
        tmp_path,
        "CREATE TABLE cost_centers (id INTEGER PRIMARY KEY, code TEXT)",
        ["INSERT INTO cost_centers (id, code) VALUES (1, 'abc')"],
    )

    with pytest.raises(BackupSchemaIncompatible) as exc:
        check_backup_schema_compatible(path, backup_version="1.2.7")

    assert "1.2.7" in str(exc.value)


def test_an_empty_table_is_not_a_reason_to_refuse(tmp_path):
    """No rows, no INSERT, no violation. Refusing here would block a restore
    over a feature the backup's install never used."""
    path = _source(tmp_path, "CREATE TABLE cost_centers (id INTEGER PRIMARY KEY, code TEXT)", [])

    check_backup_schema_compatible(path)  # does not raise


def test_a_table_the_orm_does_not_know_is_ignored(tmp_path):
    """Backups carry tables from features this version has removed. The import
    skips them (it only imports source ∩ ORM), so the check must not judge them
    either -- a column requirement that no longer exists cannot fail an INSERT
    that will never be made."""
    from backend.app.core.database import Base

    assert "legacy_removed_feature" not in Base.metadata.tables
    path = _source(
        tmp_path,
        "CREATE TABLE legacy_removed_feature (id INTEGER PRIMARY KEY, whatever TEXT)",
        ["INSERT INTO legacy_removed_feature (id, whatever) VALUES (1, 'x')"],
    )

    check_backup_schema_compatible(path)  # does not raise


# ---------------------------------------------------------------------------
# The import itself
# ---------------------------------------------------------------------------


def _mock_engine():
    """An engine that records every statement and parameter set."""
    executed: list[tuple[str, object]] = []
    conn = MagicMock()
    conn.execute = AsyncMock(
        side_effect=lambda stmt, *a, **k: executed.append((getattr(stmt, "text", str(stmt)), a[0] if a else None))
    )
    conn.run_sync = AsyncMock()
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=conn)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin = MagicMock(return_value=begin_cm)
    engine.dispose = AsyncMock()
    return engine, executed


@pytest.mark.asyncio
async def test_the_import_refuses_before_it_drops_anything(tmp_path):
    """The whole point. The drop is the import's first act and it is not
    reversible: by the time an INSERT fails, the install is empty."""
    path = _source(
        tmp_path,
        "CREATE TABLE cost_centers (id INTEGER PRIMARY KEY, code TEXT)",
        ["INSERT INTO cost_centers (id, code) VALUES (1, 'abc')"],
    )
    from backend.app.api.routes import settings as settings_module

    engine, executed = _mock_engine()
    create_engine = MagicMock(return_value=engine)
    with (
        patch("backend.app.core.database._create_engine", new=create_engine),
        pytest.raises(BackupSchemaIncompatible),
    ):
        await settings_module._import_sqlite_to_postgres(path, "postgresql+asyncpg://test/test")

    assert executed == [], f"SQL ran against the destination before the refusal: {executed}"
    create_engine.assert_not_called()


@pytest.mark.asyncio
async def test_a_missing_defaulted_column_is_inserted_with_its_default(tmp_path):
    """What would have rescued the failed restore: the column absent from the
    backup joins the INSERT carrying the model's default."""
    path = _source(
        tmp_path,
        "CREATE TABLE cost_centers (id INTEGER PRIMARY KEY, code TEXT, name TEXT, created_at TEXT, updated_at TEXT)",
        [
            "INSERT INTO cost_centers (id, code, name, created_at, updated_at) "
            "VALUES (1, 'abc', 'Lab', '2026-09-07 10:44:42', '2026-09-07 10:44:42')"
        ],
    )
    from backend.app.api.routes import settings as settings_module

    engine, executed = _mock_engine()
    with patch("backend.app.core.database._create_engine", new=MagicMock(return_value=engine)):
        await settings_module._import_sqlite_to_postgres(path, "postgresql+asyncpg://test/test")

    inserts = [(sql, params) for sql, params in executed if sql.startswith("INSERT INTO cost_centers")]
    assert inserts, f"no INSERT was emitted: {[sql[:60] for sql, _ in executed]}"
    sql, params = inserts[0]
    # is_active is NOT NULL with default=True in the model and absent above.
    assert "is_active" in sql
    assert params[0]["is_active"] is True
    assert params[0]["code"] == "abc", "the backup's own values must survive the injection"


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------


def test_a_backup_without_a_manifest_reads_as_unknown(tmp_path):
    """Every backup taken before the manifest existed. It must restore exactly
    as it did, with the version simply unknown."""
    assert _read_backup_manifest(tmp_path) == {}


def test_an_unreadable_manifest_does_not_break_the_restore(tmp_path):
    (tmp_path / "manifest.json").write_text("{ this is not json")

    assert _read_backup_manifest(tmp_path) == {}


def test_the_manifest_is_read(tmp_path):
    (tmp_path / "manifest.json").write_text('{"format": 1, "app_version": "1.2.7"}')

    assert _read_backup_manifest(tmp_path)["app_version"] == "1.2.7"
