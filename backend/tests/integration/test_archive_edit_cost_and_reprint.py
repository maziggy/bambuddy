"""Editing an archive: its cost reaches the run, a reprint's run is not touched.

* A hand-set cost was saved on the archive only. Statistics, project totals
  and batch sums read the run's log entry, so they kept the old figure (COST-5).
  Like the grams, it reaches the run that inherited it, never a run that
  measured its own.
* While the archive is being reprinted, its newest log entry is the previous
  run's; an edit then rewrote that run's outcome (FG/I9).
* A negative cost was accepted and subtracted from every total (COST-6).
"""

import pytest
from sqlalchemy import select

from backend.app.models.print_log import PrintLogEntry

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _entry(db_session, archive_id):
    query = select(PrintLogEntry).where(PrintLogEntry.archive_id == archive_id)
    return (await db_session.scalars(query.execution_options(populate_existing=True))).one()


class TestAHandSetCost:
    async def test_reaches_the_run_that_inherited_it(self, async_client, archive_factory, printer_factory, db_session):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, cost=1.50)

        response = await async_client.patch(f"/api/v1/archives/{archive.id}", json={"cost": 5.00})

        assert response.status_code == 200, response.text
        assert (await _entry(db_session, archive.id)).cost == 5.00

    async def test_leaves_a_run_that_measured_its_own(self, async_client, archive_factory, printer_factory, db_session):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, cost=1.50)
        entry = await _entry(db_session, archive.id)
        entry.cost = 1.23  # the tracked spools' price for this run
        await db_session.commit()

        await async_client.patch(f"/api/v1/archives/{archive.id}", json={"cost": 5.00})

        assert (await _entry(db_session, archive.id)).cost == 1.23

    async def test_a_negative_cost_is_refused(self, async_client, archive_factory, printer_factory, db_session):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, cost=1.50)

        response = await async_client.patch(f"/api/v1/archives/{archive.id}", json={"cost": -100})

        assert response.status_code == 422
        assert (await _entry(db_session, archive.id)).cost == 1.50


class TestAnEditDuringAReprint:
    async def test_leaves_the_previous_run_alone(self, async_client, archive_factory, printer_factory, db_session):
        from backend.app.models.archive import PrintArchive

        printer = await printer_factory()
        archive = await archive_factory(printer.id, status="completed", filament_used_grams=10.0, cost=1.50)
        # The reprint is running: the archive row is its, the entry the last run's
        row = await db_session.get(PrintArchive, archive.id)
        row.status = "printing"
        await db_session.commit()

        response = await async_client.patch(
            f"/api/v1/archives/{archive.id}",
            json={"failure_reason": "spaghetti", "user_verdict": "reject", "filament_used_grams": 12.0, "cost": 9.0},
        )

        assert response.status_code == 200, response.text
        entry = await _entry(db_session, archive.id)
        assert (entry.status, entry.failure_reason, entry.user_verdict, entry.filament_used_grams, entry.cost) == (
            "completed",
            None,
            None,
            10.0,
            1.50,
        )


class TestWhichRunsTakeIt:
    async def test_not_a_failed_run_that_printed_only_part(
        self, async_client, archive_factory, printer_factory, db_session
    ):
        """A failed run without tracked spools has no cost on purpose."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, cost=1.50, status="failed")
        entry = await _entry(db_session, archive.id)
        entry.cost = None
        await db_session.commit()

        await async_client.patch(f"/api/v1/archives/{archive.id}", json={"cost": 5.00})

        assert (await _entry(db_session, archive.id)).cost is None

    async def test_after_recalculate_costs_too(self, async_client, archive_factory, printer_factory, db_session):
        """Recalculate gives the run the new figure as well, so a later hand
        edit still finds the run carrying the archive's cost."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, cost=1.50, filament_used_grams=100.0, filament_type="PLA")

        recalculated = await async_client.post("/api/v1/archives/recalculate-costs")
        assert recalculated.json()["updated"] == 1
        recalculated_cost = (await _entry(db_session, archive.id)).cost
        assert recalculated_cost not in (None, 1.50)

        await async_client.patch(f"/api/v1/archives/{archive.id}", json={"cost": 2.00})

        assert (await _entry(db_session, archive.id)).cost == 2.00

    async def test_an_infinite_cost_is_refused(self, async_client, archive_factory, printer_factory):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, cost=1.50)

        response = await async_client.patch(
            f"/api/v1/archives/{archive.id}",
            content=b'{"cost": Infinity}',
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 422
