"""An archive's cost is the cost of one print of it.

Completion stores the first run's cost on the archive and every run's own cost
on its Print History entry (#1378). "Recalculate costs" and "Rescan" used to
sum the spool usage of every run, so an archive printed three times was priced
three times over; editing its grams repriced nothing.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.print_log import PrintLogEntry
from backend.app.models.spool_usage_history import SpoolUsageHistory


async def _archive(archive_factory, printer_factory, db_session, *, grams, cost=None, with_run=True):
    printer = await printer_factory()
    archive = await archive_factory(
        printer.id,
        print_name="Bracket",
        status="completed",
        cost=cost,
        filament_used_grams=grams,
        filament_type="PLA",
        with_run=with_run,
    )
    return archive.id


async def _usage(db_session, archive_id, *rows):
    for grams, cost in rows:
        db_session.add(SpoolUsageHistory(archive_id=archive_id, weight_used=grams, cost=cost, print_name="Bracket"))
    await db_session.commit()


async def _cost(async_client, archive_id):
    response = await async_client.get(f"/api/v1/archives/{archive_id}")
    assert response.status_code == 200
    return response.json()["cost"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_recalculate_prices_one_print_of_an_archive_printed_twice(
    async_client: AsyncClient, archive_factory, printer_factory, db_session
):
    archive_id = await _archive(archive_factory, printer_factory, db_session, grams=100.0)
    await _usage(db_session, archive_id, (100.0, 3.0), (100.0, 3.0))

    assert (await async_client.post("/api/v1/archives/recalculate-costs")).status_code == 200

    assert await _cost(async_client, archive_id) == 3.0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_recalculate_still_prices_untracked_grams_at_the_default(
    async_client: AsyncClient, archive_factory, printer_factory, db_session
):
    """One run, half the grams on an inventory spool (#1344): 50 g at the
    spool's 2.00 plus 50 g at the 25/kg default."""
    archive_id = await _archive(archive_factory, printer_factory, db_session, grams=100.0)
    await _usage(db_session, archive_id, (50.0, 2.0))

    assert (await async_client.post("/api/v1/archives/recalculate-costs")).status_code == 200

    assert await _cost(async_client, archive_id) == 3.25


@pytest.mark.asyncio
@pytest.mark.integration
async def test_editing_grams_prices_the_archive_and_its_run(
    async_client: AsyncClient, archive_factory, printer_factory, db_session
):
    """A print archived without its 3MF has neither grams nor cost (#1820)."""
    archive_id = await _archive(archive_factory, printer_factory, db_session, grams=None)

    response = await async_client.patch(f"/api/v1/archives/{archive_id}", json={"filament_used_grams": 120})
    assert response.status_code == 200
    assert response.json()["cost"] == 3.0

    run = await db_session.scalar(select(PrintLogEntry).where(PrintLogEntry.archive_id == archive_id))
    await db_session.refresh(run)
    assert (run.filament_used_grams, run.cost) == (120, 3.0)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_cost_given_with_the_grams_is_kept(
    async_client: AsyncClient, archive_factory, printer_factory, db_session
):
    archive_id = await _archive(archive_factory, printer_factory, db_session, grams=100.0, cost=2.5)

    response = await async_client.patch(
        f"/api/v1/archives/{archive_id}", json={"filament_used_grams": 200, "cost": 9.99}
    )

    assert response.json()["cost"] == 9.99


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_run_that_measured_its_own_grams_keeps_its_cost(
    async_client: AsyncClient, archive_factory, printer_factory, db_session
):
    archive_id = await _archive(archive_factory, printer_factory, db_session, grams=100.0, cost=2.5)
    run = await db_session.scalar(select(PrintLogEntry).where(PrintLogEntry.archive_id == archive_id))
    run.filament_used_grams, run.cost = 37.0, 1.11
    await db_session.commit()

    response = await async_client.patch(f"/api/v1/archives/{archive_id}", json={"filament_used_grams": 200})
    assert response.json()["cost"] == 5.0

    await db_session.refresh(run)
    assert (run.filament_used_grams, run.cost) == (37.0, 1.11)


async def _runs(db_session, archive_id, *grams):
    for g in grams:
        db_session.add(PrintLogEntry(archive_id=archive_id, status="completed", filament_used_grams=g))
    await db_session.commit()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_recalculate_keeps_the_untracked_share_of_every_run(
    async_client: AsyncClient, archive_factory, printer_factory, db_session
):
    """Printed three times, half of each 100 g run on a 60/kg spool: one print
    is 50 g at 0.06 plus 50 g at the 25/kg default, as completion priced it."""
    archive_id = await _archive(archive_factory, printer_factory, db_session, grams=100.0)
    await _runs(db_session, archive_id, 100.0, 100.0)
    await _usage(db_session, archive_id, (50.0, 3.0), (50.0, 3.0), (50.0, 3.0))

    assert (await async_client.post("/api/v1/archives/recalculate-costs")).status_code == 200

    assert await _cost(async_client, archive_id) == 4.25


@pytest.mark.asyncio
@pytest.mark.integration
async def test_recalculate_prices_unpriced_usage_at_the_default(
    async_client: AsyncClient, archive_factory, printer_factory, db_session
):
    """A usage row recorded without a price is not free filament."""
    archive_id = await _archive(archive_factory, printer_factory, db_session, grams=100.0)
    await _usage(db_session, archive_id, (50.0, 2.0), (50.0, None))

    assert (await async_client.post("/api/v1/archives/recalculate-costs")).status_code == 200

    assert await _cost(async_client, archive_id) == 3.25


@pytest.mark.asyncio
@pytest.mark.integration
async def test_recalculate_without_grams_prices_an_average_run(
    async_client: AsyncClient, archive_factory, printer_factory, db_session
):
    """Archived without its 3MF, so no grams: the spool usage of its two runs
    still prices one of them."""
    archive_id = await _archive(archive_factory, printer_factory, db_session, grams=None)
    await _runs(db_session, archive_id, None)
    await _usage(db_session, archive_id, (40.0, 2.0), (40.0, 2.0))

    assert (await async_client.post("/api/v1/archives/recalculate-costs")).status_code == 200

    assert await _cost(async_client, archive_id) == 2.0
