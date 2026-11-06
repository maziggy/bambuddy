"""Duplicate maintenance rows are merged, and a unique index keeps them out (FO-C14).

A printer's maintenance rows are created on first read, with no constraint:
two dashboards, or a dashboard and the background check, could each create
one. "Performed" could then update the copy that is not shown, and assigning
the type again answered 500. The migration merges existing duplicates --
keeping the most recently performed row and moving the others' history onto
it -- and adds the index, which CREATE UNIQUE INDEX would refuse to build over
duplicates.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import _migrate_printer_maintenance_unique

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def engine():
    """In-memory SQLite with the two tables as they were before the index."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE printer_maintenance ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, printer_id INTEGER NOT NULL, "
                "maintenance_type_id INTEGER NOT NULL, last_performed_at DATETIME, "
                "last_performed_hours FLOAT DEFAULT 0)"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE maintenance_history ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, printer_maintenance_id INTEGER NOT NULL)"
            )
        )
    try:
        yield engine
    finally:
        await engine.dispose()


async def _item(conn, printer_id: int, type_id: int, performed_at: str | None = None) -> int:
    result = await conn.execute(
        text(
            "INSERT INTO printer_maintenance (printer_id, maintenance_type_id, last_performed_at) "
            "VALUES (:p, :t, :at) RETURNING id"
        ),
        {"p": printer_id, "t": type_id, "at": performed_at},
    )
    return result.scalar_one()


async def _history(conn, item_id: int) -> None:
    await conn.execute(text("INSERT INTO maintenance_history (printer_maintenance_id) VALUES (:i)"), {"i": item_id})


async def test_duplicates_merge_into_the_most_recently_performed_row(engine):
    async with engine.begin() as conn:
        never = await _item(conn, 1, 5)
        performed = await _item(conn, 1, 5, "2026-10-01 10:00:00")
        older = await _item(conn, 1, 5, "2026-09-01 10:00:00")
        other_type = await _item(conn, 1, 6)
        for item_id in (never, performed, older):
            await _history(conn, item_id)

    async with engine.begin() as conn:
        await _migrate_printer_maintenance_unique(conn)

    async with engine.begin() as conn:
        ids = (await conn.execute(text("SELECT id FROM printer_maintenance ORDER BY id"))).scalars().all()
        history = (await conn.execute(text("SELECT printer_maintenance_id FROM maintenance_history"))).scalars().all()
    assert ids == [performed, other_type]
    assert history == [performed, performed, performed]


async def test_without_a_performance_the_row_the_page_showed_is_kept(engine):
    """The page took the last row read -- the highest id -- so a user's
    settings went there; keeping another would bring the defaults back."""
    async with engine.begin() as conn:
        await _item(conn, 1, 5)
        shown = await _item(conn, 1, 5)

    async with engine.begin() as conn:
        await _migrate_printer_maintenance_unique(conn)

    async with engine.begin() as conn:
        ids = (await conn.execute(text("SELECT id FROM printer_maintenance"))).scalars().all()
    assert ids == [shown]


async def test_the_index_then_refuses_a_second_row(engine):
    async with engine.begin() as conn:
        await _migrate_printer_maintenance_unique(conn)
        await _item(conn, 1, 5)

    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await _item(conn, 1, 5)


async def test_is_idempotent(engine):
    async with engine.begin() as conn:
        await _item(conn, 1, 5)
        await _item(conn, 2, 5)
    for _ in range(2):
        async with engine.begin() as conn:
            await _migrate_printer_maintenance_unique(conn)

    async with engine.begin() as conn:
        assert (await conn.execute(text("SELECT COUNT(*) FROM printer_maintenance"))).scalar_one() == 2
