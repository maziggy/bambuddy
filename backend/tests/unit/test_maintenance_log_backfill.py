"""Resets from before the maintenance log are copied into it once, on upgrade.

They are only in maintenance_history, which goes with its printer task. The
copy takes the printer's and the task's names as they are now; who reset them
was never recorded. A second start copies nothing again, not even entries
deleted from the log since.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import _backfill_maintenance_log
from backend.app.models.maintenance import MaintenanceLogEntry

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def engine():
    """In-memory SQLite with the tables the copy reads, as an upgrade finds them,
    and the log as create_all builds it from the model."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        for ddl in (
            'CREATE TABLE settings (id INTEGER PRIMARY KEY, "key" VARCHAR(100) NOT NULL UNIQUE, value TEXT)',
            "CREATE TABLE printers (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL)",
            "CREATE TABLE maintenance_types (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL)",
            "CREATE TABLE printer_maintenance (id INTEGER PRIMARY KEY, printer_id INTEGER NOT NULL, "
            "maintenance_type_id INTEGER NOT NULL)",
            "CREATE TABLE maintenance_history (id INTEGER PRIMARY KEY, printer_maintenance_id INTEGER NOT NULL, "
            "performed_at DATETIME, hours_at_maintenance FLOAT, notes TEXT)",
        ):
            await conn.execute(text(ddl))
        await conn.run_sync(lambda sync: MaintenanceLogEntry.__table__.create(sync))
        await conn.execute(text("INSERT INTO printers (id, name) VALUES (1, 'X1C')"))
        await conn.execute(text("INSERT INTO maintenance_types (id, name) VALUES (7, 'Clean Nozzle')"))
        await conn.execute(
            text("INSERT INTO printer_maintenance (id, printer_id, maintenance_type_id) VALUES (3, 1, 7)")
        )
        for history_id, at, hours, notes in (
            (10, datetime(2026, 1, 2, 3, 4), 12.5, "old note"),
            (11, datetime(2026, 2, 1), 40.0, None),
        ):
            await conn.execute(
                text(
                    "INSERT INTO maintenance_history (id, printer_maintenance_id, performed_at, hours_at_maintenance, notes) "
                    "VALUES (:id, 3, :at, :hours, :notes)"
                ),
                {"id": history_id, "at": at, "hours": hours, "notes": notes},
            )
    try:
        yield engine
    finally:
        await engine.dispose()


async def _entries(conn) -> list[tuple]:
    result = await conn.execute(
        text(
            "SELECT history_id, printer_id, printer_name, maintenance_type_id, maintenance_type_name, "
            "hours_at_maintenance, notes, performed_by_id, performed_by_username "
            "FROM maintenance_log_entries ORDER BY history_id"
        )
    )
    return [tuple(row) for row in result]


async def test_old_resets_are_copied_with_their_printer_and_task(engine):
    async with engine.begin() as conn:
        await _backfill_maintenance_log(conn)
        entries = await _entries(conn)

    assert entries == [
        (10, 1, "X1C", 7, "Clean Nozzle", 12.5, "old note", None, None),
        (11, 1, "X1C", 7, "Clean Nozzle", 40.0, None, None, None),
    ]


async def test_the_copy_keeps_when_they_were_done(engine):
    async with engine.begin() as conn:
        await _backfill_maintenance_log(conn)
        performed = (
            await conn.execute(text("SELECT performed_at FROM maintenance_log_entries WHERE history_id = 10"))
        ).scalar_one()

    assert str(performed).startswith("2026-01-02 03:04")


async def test_a_second_start_copies_nothing_again(engine):
    async with engine.begin() as conn:
        await _backfill_maintenance_log(conn)
        await _backfill_maintenance_log(conn)
        entries = await _entries(conn)

    assert [e[0] for e in entries] == [10, 11]


async def test_resets_already_logged_are_not_copied(engine):
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO maintenance_log_entries (printer_id, printer_name, maintenance_type_id, "
                "maintenance_type_name, hours_at_maintenance, performed_by_username, history_id) "
                "VALUES (1, 'X1C', 7, 'Clean Nozzle', 12.5, 'mechanic', 10)"
            )
        )
        await _backfill_maintenance_log(conn)
        entries = await _entries(conn)

    assert [(e[0], e[-1]) for e in entries] == [(10, "mechanic"), (11, None)]


async def test_entries_deleted_from_the_log_stay_deleted(engine):
    async with engine.begin() as conn:
        await _backfill_maintenance_log(conn)
        await conn.execute(text("DELETE FROM maintenance_log_entries WHERE history_id = 10"))
        await _backfill_maintenance_log(conn)
        entries = await _entries(conn)

    assert [e[0] for e in entries] == [11]
