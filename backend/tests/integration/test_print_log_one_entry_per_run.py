"""One print log entry per run, however often the run is reported.

A run is its archive, printer and start time. A reprint starts at a new time
and a plate printing on a second printer is a run of its own, so both get
their own entries; the same run reported again replaces its entry.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.app.models.print_log import PrintLogEntry
from backend.app.services.print_log import write_log_entry

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

START = datetime(2026, 10, 19, 8, 0)


@pytest.fixture
async def rows(db_session, printer_factory) -> dict:
    """Two printers and an archive, so the foreign keys hold (PostgreSQL)."""
    from backend.app.models.archive import PrintArchive

    first, second = await printer_factory(name="A"), await printer_factory(name="B")
    archive = PrintArchive(
        printer_id=first.id, filename="a.3mf", file_path="a.3mf", file_size=1, status="printing", started_at=START
    )
    db_session.add(archive)
    await db_session.commit()
    return {"printer": first.id, "other_printer": second.id, "archive": archive}


async def _write(db, rows, *, printer_id=None, started_at=START, status="completed", queue_item_id=None, cost=None):
    await write_log_entry(
        db,
        status=status,
        archive_id=rows["archive"].id,
        printer_id=printer_id or rows["printer"],
        started_at=started_at,
        completed_at=started_at + timedelta(hours=1),
        queue_item_id=queue_item_id,
        cost=cost,
    )
    await db.commit()


async def _entries(db) -> list[tuple]:
    rows = (await db.scalars(select(PrintLogEntry).order_by(PrintLogEntry.id))).all()
    return [(r.printer_id, r.started_at, r.status, r.queue_item_id) for r in rows]


async def test_the_same_run_reported_twice_is_one_entry(db_session, rows):
    await _write(db_session, rows, status="cancelled")
    await _write(db_session, rows, status="completed")

    assert await _entries(db_session) == [(rows["printer"], START, "completed", None)]


async def test_a_reprint_is_an_entry_of_its_own(db_session, rows):
    await _write(db_session, rows)
    await _write(db_session, rows, started_at=START + timedelta(days=1))

    assert len(await _entries(db_session)) == 2


async def test_a_plate_on_another_printer_is_an_entry_of_its_own(db_session, rows):
    await _write(db_session, rows)
    await _write(db_session, rows, printer_id=rows["other_printer"])

    assert len(await _entries(db_session)) == 2


async def test_an_aware_start_time_is_the_same_run(db_session, rows):
    """The stop route holds aware times, the columns store naive UTC."""
    await _write(db_session, rows, status="cancelled")
    await _write(db_session, rows, started_at=START.replace(tzinfo=timezone.utc))

    assert [status for _p, _s, status, _q in await _entries(db_session)] == ["completed"]


async def test_the_same_outcome_reported_again_changes_nothing(db_session, rows):
    """A repeat lacks the run's tracked usage; the entry may have been edited
    or given its energy since."""
    await _write(db_session, rows, cost=4.20)
    entry = await db_session.scalar(select(PrintLogEntry))
    entry.energy_kwh = 0.5
    entry.failure_reason = "edited"
    await db_session.commit()

    await _write(db_session, rows, cost=5.00)

    db_session.expire_all()
    entry = await db_session.scalar(select(PrintLogEntry))
    assert (entry.cost, entry.energy_kwh, entry.failure_reason) == (4.20, 0.5, "edited")


class TestOtherRunsOf:
    """Whether an archive's card still shows its first run (#1378)."""

    async def _entry(self, db, rows, started_at, printer_id=None):
        db.add(
            PrintLogEntry(
                archive_id=rows["archive"].id,
                printer_id=printer_id or rows["printer"],
                status="cancelled",
                started_at=started_at,
            )
        )
        await db.commit()

    async def test_the_current_run_s_own_entry_is_not_another_run(self, db_session, rows):
        from backend.app.services.print_log import other_runs_of

        await self._entry(db_session, rows, START)

        assert await other_runs_of(db_session, rows["archive"]) == 0

    async def test_an_earlier_run_or_another_printer_is(self, db_session, rows):
        from backend.app.services.print_log import other_runs_of

        await self._entry(db_session, rows, START - timedelta(days=1))
        await self._entry(db_session, rows, START, printer_id=rows["other_printer"])

        assert await other_runs_of(db_session, rows["archive"]) == 2

    async def test_without_a_start_time_every_entry_is(self, db_session, rows):
        from backend.app.services.print_log import other_runs_of

        await self._entry(db_session, rows, START)
        rows["archive"].started_at = None
        await db_session.commit()

        assert await other_runs_of(db_session, rows["archive"]) == 1
