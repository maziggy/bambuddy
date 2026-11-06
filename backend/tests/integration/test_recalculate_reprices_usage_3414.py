"""Recalculate Costs prices recorded usage at the spools' current prices (#3414).

A usage row stores what its grams cost at completion, and Recalculate added
those up, so a spool price corrected afterwards changed nothing: "0 archives
were updated". The rows now take the spool's current price first, and the run
each row was charged in moves by the same difference, as statistics read it.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import PrintLogEntry
from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.models.spool_usage_history import SpoolUsageHistory

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

START = datetime(2026, 10, 28, 10, 0, 0)


async def _spool(db, cost_per_kg):
    spool = Spool(material="PLA", label_weight=1000, core_weight=250, weight_used=0, cost_per_kg=cost_per_kg)
    db.add(spool)
    await db.commit()
    await db.refresh(spool)
    return spool


async def _print(archive_factory, printer, *, grams, cost, started=START):
    return await archive_factory(
        printer.id,
        status="completed",
        cost=cost,
        filament_used_grams=grams,
        started_at=started,
        completed_at=started + timedelta(hours=1),
    )


def _usage(archive, printer, *, grams, cost, spool_id=None, spoolman_spool_id=None, at=START, status="completed"):
    return SpoolUsageHistory(
        spool_id=spool_id,
        spoolman_spool_id=spoolman_spool_id,
        printer_id=printer.id,
        archive_id=archive.id if archive is not None else None,
        weight_used=grams,
        percent_used=5,
        status=status,
        cost=cost,
        created_at=at + timedelta(hours=1, seconds=5),
    )


async def _recalculate(client: AsyncClient, **kwargs):
    response = await client.post("/api/v1/archives/recalculate-costs", **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


async def _fresh(db, model, row_id):
    return (
        await db.execute(select(model).where(model.id == row_id).execution_options(populate_existing=True))
    ).scalar_one()


async def _runs(db, archive_id):
    query = select(PrintLogEntry).where(PrintLogEntry.archive_id == archive_id).order_by(PrintLogEntry.id)
    return list((await db.execute(query.execution_options(populate_existing=True))).scalars().all())


async def test_a_spools_new_price_reaches_its_usage_the_archive_and_the_run(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    """The reported case: a print off a priced spool, the spool's price raised
    tenfold afterwards."""
    db_session.add(Settings(key="default_filament_cost", value="1400"))
    printer = await printer_factory()
    spool = await _spool(db_session, 1400.0)
    archive = await _print(archive_factory, printer, grams=45.6, cost=63.84)
    usage = _usage(archive, printer, grams=45.6, cost=63.84, spool_id=spool.id)
    db_session.add(usage)
    spool.cost_per_kg = 14000.0
    await db_session.commit()

    body = await _recalculate(async_client)

    assert body["updated"] == 1
    assert body["usage_repriced"] == 1
    assert (await _fresh(db_session, SpoolUsageHistory, usage.id)).cost == 638.4
    assert (await _fresh(db_session, PrintArchive, archive.id)).cost == 638.4
    assert [run.cost for run in await _runs(db_session, archive.id)] == [638.4]


async def test_an_unchanged_price_changes_nothing_and_a_second_run_neither(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    db_session.add(Settings(key="default_filament_cost", value="1400"))
    printer = await printer_factory()
    spool = await _spool(db_session, 1400.0)
    archive = await _print(archive_factory, printer, grams=45.6, cost=63.84)
    usage = _usage(archive, printer, grams=45.6, cost=63.84, spool_id=spool.id)
    db_session.add(usage)
    await db_session.commit()

    first = await _recalculate(async_client)
    second = await _recalculate(async_client)

    assert (first["usage_repriced"], first["updated"]) == (0, 0)
    assert (second["usage_repriced"], second["updated"]) == (0, 0)
    assert [run.cost for run in await _runs(db_session, archive.id)] == [63.84]


async def test_a_small_prints_price_change_of_a_cent_is_taken(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    db_session.add(Settings(key="default_filament_cost", value="20"))
    printer = await printer_factory()
    spool = await _spool(db_session, 20.0)
    archive = await _print(archive_factory, printer, grams=5.0, cost=0.1)
    usage = _usage(archive, printer, grams=5.0, cost=0.1, spool_id=spool.id)
    db_session.add(usage)
    spool.cost_per_kg = 22.0
    await db_session.commit()

    await _recalculate(async_client)

    assert (await _fresh(db_session, SpoolUsageHistory, usage.id)).cost == 0.11
    assert [run.cost for run in await _runs(db_session, archive.id)] == [0.11]


async def test_a_reprint_off_an_unchanged_spool_keeps_its_cost(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    """The archive's new cost averages both prints; the reprint's own spool
    priced it and did not change."""
    db_session.add(Settings(key="default_filament_cost", value="20"))
    printer = await printer_factory()
    spool_a = await _spool(db_session, 20.0)
    spool_b = await _spool(db_session, 20.0)
    archive = await _print(archive_factory, printer, grams=100.0, cost=2.0)
    later = START + timedelta(days=1)
    db_session.add(
        PrintLogEntry(
            archive_id=archive.id,
            printer_id=printer.id,
            status="completed",
            started_at=later,
            completed_at=later + timedelta(hours=1),
            filament_used_grams=100.0,
            cost=2.0,
        )
    )
    db_session.add(_usage(archive, printer, grams=100.0, cost=2.0, spool_id=spool_a.id))
    db_session.add(_usage(archive, printer, grams=100.0, cost=2.0, spool_id=spool_b.id, at=later))
    spool_a.cost_per_kg = 40.0
    await db_session.commit()

    await _recalculate(async_client)

    assert (await _fresh(db_session, PrintArchive, archive.id)).cost == 3.0
    assert [run.cost for run in await _runs(db_session, archive.id)] == [4.0, 2.0]


async def test_a_spoolman_print_takes_a_new_default_and_spool_price_together(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    """80 g off the spool, 20 g unpriced at the default: completion gave the
    run the archive's whole figure, and it follows the archive's new one."""
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="default_filament_cost", value="50"))
    printer = await printer_factory()
    archive = await _print(archive_factory, printer, grams=100.0, cost=2.1)
    usage = _usage(archive, printer, grams=80.0, cost=1.6, spoolman_spool_id=41)
    db_session.add(usage)
    await db_session.commit()

    with _spoolman([{"id": 41, "price": 30.0, "initial_weight": 1000, "filament": {"weight": 1000}}]):
        await _recalculate(async_client)

    assert (await _fresh(db_session, PrintArchive, archive.id)).cost == 3.4
    assert [run.cost for run in await _runs(db_session, archive.id)] == [3.4]


async def test_spoolman_spool_given_a_price_reaches_every_run(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    """Unpriced, completion covered each run's grams at the default rate."""
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="default_filament_cost", value="25"))
    printer = await printer_factory()
    archive = await _print(archive_factory, printer, grams=100.0, cost=2.5)
    later = START + timedelta(days=1)
    db_session.add(
        PrintLogEntry(
            archive_id=archive.id,
            printer_id=printer.id,
            status="failed",
            started_at=later,
            completed_at=later + timedelta(hours=1),
            cost=1.0,
        )
    )
    db_session.add(_usage(archive, printer, grams=100.0, cost=None, spoolman_spool_id=41))
    db_session.add(_usage(archive, printer, grams=40.0, cost=None, spoolman_spool_id=41, at=later, status="failed"))
    await db_session.commit()

    with _spoolman([{"id": 41, "price": 50.0, "initial_weight": 1000, "filament": {"weight": 1000}}]):
        await _recalculate(async_client)

    assert [run.cost for run in await _runs(db_session, archive.id)] == [5.0, 2.0]


async def test_usage_recorded_outside_any_run_moves_none(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    """A row stamped two hours late (local time on an older install) must not
    move the reprint that started after the print it belongs to."""
    db_session.add(Settings(key="default_filament_cost", value="20"))
    printer = await printer_factory()
    spool = await _spool(db_session, 20.0)
    archive = await _print(archive_factory, printer, grams=100.0, cost=2.0)
    later = START + timedelta(hours=1, minutes=10)
    db_session.add(
        PrintLogEntry(
            archive_id=archive.id,
            printer_id=printer.id,
            status="completed",
            started_at=later,
            completed_at=later + timedelta(minutes=30),
            cost=1.9,
        )
    )
    usage = _usage(archive, printer, grams=100.0, cost=2.0, spool_id=spool.id)
    usage.created_at = START + timedelta(hours=3)
    db_session.add(usage)
    spool.cost_per_kg = 40.0
    await db_session.commit()

    await _recalculate(async_client)

    assert [run.cost for run in await _runs(db_session, archive.id)] == [2.0, 1.9]


async def test_a_run_without_an_end_holds_usage_until_the_next_run(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    db_session.add(Settings(key="default_filament_cost", value="20"))
    printer = await printer_factory()
    spool = await _spool(db_session, 20.0)
    archive = await _print(archive_factory, printer, grams=100.0, cost=2.0)
    (first,) = await _runs(db_session, archive.id)
    first.completed_at = None
    first.cost = 1.0
    later = START + timedelta(hours=2)
    db_session.add(
        PrintLogEntry(
            archive_id=archive.id,
            printer_id=printer.id,
            status="completed",
            started_at=later,
            completed_at=later + timedelta(hours=1),
            cost=1.9,
        )
    )
    db_session.add(_usage(archive, printer, grams=50.0, cost=1.0, spool_id=spool.id))
    spool.cost_per_kg = 40.0
    await db_session.commit()

    await _recalculate(async_client)

    assert [run.cost for run in await _runs(db_session, archive.id)] == [2.0, 1.9]


async def test_the_run_of_a_deleted_archive_follows_its_usage(async_client: AsyncClient, db_session, printer_factory):
    """A printer deleted with its archives leaves both without an archive."""
    db_session.add(Settings(key="default_filament_cost", value="20"))
    printer = await printer_factory()
    spool = await _spool(db_session, 20.0)
    run = PrintLogEntry(
        printer_id=printer.id, status="completed", started_at=START, completed_at=START + timedelta(hours=1), cost=2.0
    )
    db_session.add(run)
    db_session.add(_usage(None, printer, grams=100.0, cost=2.0, spool_id=spool.id))
    spool.cost_per_kg = 40.0
    await db_session.commit()

    await _recalculate(async_client)

    assert (await _fresh(db_session, PrintLogEntry, run.id)).cost == 4.0


async def test_a_spool_without_a_price_follows_the_default_cost(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    db_session.add(Settings(key="default_filament_cost", value="50"))
    printer = await printer_factory()
    spool = await _spool(db_session, None)
    archive = await _print(archive_factory, printer, grams=100.0, cost=2.5)
    usage = _usage(archive, printer, grams=100.0, cost=2.5, spool_id=spool.id)
    db_session.add(usage)
    await db_session.commit()

    await _recalculate(async_client)

    assert (await _fresh(db_session, SpoolUsageHistory, usage.id)).cost == 5.0
    assert (await _fresh(db_session, PrintArchive, archive.id)).cost == 5.0
    assert [run.cost for run in await _runs(db_session, archive.id)] == [5.0]


async def test_a_deleted_spools_usage_keeps_what_it_cost(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    db_session.add(Settings(key="default_filament_cost", value="50"))
    printer = await printer_factory()
    archive = await _print(archive_factory, printer, grams=100.0, cost=3.0)
    usage = _usage(archive, printer, grams=100.0, cost=3.0)
    db_session.add(usage)
    await db_session.commit()

    await _recalculate(async_client)

    assert (await _fresh(db_session, SpoolUsageHistory, usage.id)).cost == 3.0
    assert (await _fresh(db_session, PrintArchive, archive.id)).cost == 3.0


async def test_a_deleted_spool_still_named_by_its_usage_keeps_what_it_cost(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    """Usage left pointing at a spool id that no longer exists, which only
    SQLite allows: PostgreSQL's foreign key clears it with the spool."""
    if db_session.bind.dialect.name != "sqlite":
        pytest.skip("PostgreSQL refuses a usage row of a missing spool")
    db_session.add(Settings(key="default_filament_cost", value="50"))
    printer = await printer_factory()
    archive = await _print(archive_factory, printer, grams=100.0, cost=3.0)
    usage = _usage(archive, printer, grams=100.0, cost=3.0, spool_id=987654)
    db_session.add(usage)
    await db_session.commit()

    await _recalculate(async_client)

    assert (await _fresh(db_session, SpoolUsageHistory, usage.id)).cost == 3.0


async def test_a_run_its_usage_moved_is_not_given_the_archives_cost(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    """The run had only its spool's 50 g (1.00), the archive 50 g more at the
    default (2.00). Repriced, the run is 2.00 -- the archive's old figure, which
    must not hand it the archive's new 3.00."""
    db_session.add(Settings(key="default_filament_cost", value="20"))
    printer = await printer_factory()
    spool = await _spool(db_session, 20.0)
    archive = await _print(archive_factory, printer, grams=100.0, cost=2.0)
    (run,) = await _runs(db_session, archive.id)
    run.cost = 1.0
    db_session.add(_usage(archive, printer, grams=50.0, cost=1.0, spool_id=spool.id))
    spool.cost_per_kg = 40.0
    await db_session.commit()

    await _recalculate(async_client)

    assert (await _fresh(db_session, PrintArchive, archive.id)).cost == 3.0
    assert [run.cost for run in await _runs(db_session, archive.id)] == [2.0]


async def test_each_run_moves_by_its_own_usage(async_client: AsyncClient, db_session, printer_factory, archive_factory):
    """A reprint that failed halfway: each run gets its own row's difference,
    the default-rate grams of the full run stay as they were."""
    db_session.add(Settings(key="default_filament_cost", value="20"))
    printer = await printer_factory()
    spool = await _spool(db_session, 20.0)
    # 100 g: 80 g off the spool at 20/kg (1.60) + 20 g at the default (0.40).
    archive = await _print(archive_factory, printer, grams=100.0, cost=2.0)
    later = START + timedelta(days=2)
    db_session.add(
        PrintLogEntry(
            archive_id=archive.id,
            printer_id=printer.id,
            status="failed",
            started_at=later,
            completed_at=later + timedelta(hours=1),
            cost=0.8,
        )
    )
    first = _usage(archive, printer, grams=80.0, cost=1.6, spool_id=spool.id)
    second = _usage(archive, printer, grams=40.0, cost=0.8, spool_id=spool.id, at=later, status="failed")
    db_session.add_all([first, second])
    spool.cost_per_kg = 40.0
    await db_session.commit()

    await _recalculate(async_client)

    assert [run.cost for run in await _runs(db_session, archive.id)] == [3.6, 1.6]
    assert (await _fresh(db_session, SpoolUsageHistory, first.id)).cost == 3.2
    assert (await _fresh(db_session, SpoolUsageHistory, second.id)).cost == 1.6


async def test_a_run_on_another_printer_is_not_charged_this_ones_usage(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    db_session.add(Settings(key="default_filament_cost", value="20"))
    here = await printer_factory(name="Here")
    there = await printer_factory(name="There")
    spool = await _spool(db_session, 20.0)
    archive = await _print(archive_factory, here, grams=100.0, cost=2.0)
    db_session.add(
        PrintLogEntry(
            archive_id=archive.id,
            printer_id=there.id,
            status="completed",
            # Started after this printer's run, before its usage was recorded.
            started_at=START + timedelta(minutes=1),
            cost=1.9,
        )
    )
    db_session.add(_usage(archive, here, grams=100.0, cost=2.0, spool_id=spool.id))
    spool.cost_per_kg = 30.0
    await db_session.commit()

    await _recalculate(async_client)

    assert sorted((run.printer_id, run.cost) for run in await _runs(db_session, archive.id)) == sorted(
        [(here.id, 3.0), (there.id, 1.9)]
    )


def _spoolman(spools):
    client = MagicMock()
    client.get_all_spools = AsyncMock(return_value=spools)
    return patch("backend.app.services.usage_repricing._spoolman_client", AsyncMock(return_value=client))


async def test_a_spoolman_spools_new_price_reaches_its_usage(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    """Feature parity: a Spoolman charge is repriced from the spool's price in
    Spoolman, as a built-in one from the inventory's."""
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="default_filament_cost", value="25"))
    printer = await printer_factory()
    archive = await _print(archive_factory, printer, grams=100.0, cost=2.0)
    usage = _usage(archive, printer, grams=100.0, cost=2.0, spoolman_spool_id=41)
    db_session.add(usage)
    await db_session.commit()

    with _spoolman([{"id": 41, "price": 30.0, "initial_weight": 1000, "filament": {"weight": 1000}}]):
        body = await _recalculate(async_client)

    assert body["usage_repriced"] == 1
    assert (await _fresh(db_session, SpoolUsageHistory, usage.id)).cost == 3.0
    assert (await _fresh(db_session, PrintArchive, archive.id)).cost == 3.0
    assert [run.cost for run in await _runs(db_session, archive.id)] == [3.0]


async def test_unreachable_spoolman_keeps_its_usage_and_says_so(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    printer = await printer_factory()
    archive = await _print(archive_factory, printer, grams=100.0, cost=2.0)
    usage = _usage(archive, printer, grams=100.0, cost=2.0, spoolman_spool_id=41)
    db_session.add(usage)
    await db_session.commit()

    with patch("backend.app.services.usage_repricing._spoolman_client", AsyncMock(return_value=None)):
        body = await _recalculate(async_client)

    assert body["spoolman_unreachable"] == 1
    assert "Spoolman not reachable" in body["message"]
    assert (await _fresh(db_session, SpoolUsageHistory, usage.id)).cost == 2.0
    assert (await _fresh(db_session, PrintArchive, archive.id)).cost == 2.0


async def test_a_spool_gone_from_spoolman_keeps_its_usage(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    printer = await printer_factory()
    archive = await _print(archive_factory, printer, grams=100.0, cost=2.0)
    usage = _usage(archive, printer, grams=100.0, cost=2.0, spoolman_spool_id=41)
    db_session.add(usage)
    await db_session.commit()

    with _spoolman([{"id": 7, "price": 30.0, "initial_weight": 1000}]):
        body = await _recalculate(async_client)

    assert body["spoolman_unreachable"] == 0
    assert (await _fresh(db_session, SpoolUsageHistory, usage.id)).cost == 2.0


async def test_a_caller_limited_to_some_printers_reprices_only_their_usage(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    from backend.tests.integration.test_bulk_archive_routes_scope import _member

    db_session.add(Settings(key="default_filament_cost", value="20"))
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    spool = await _spool(db_session, 20.0)
    on_a = await _print(archive_factory, a, grams=100.0, cost=2.0)
    on_b = await _print(archive_factory, b, grams=100.0, cost=2.0)
    usage_a = _usage(on_a, a, grams=100.0, cost=2.0, spool_id=spool.id)
    usage_b = _usage(on_b, b, grams=100.0, cost=2.0, spool_id=spool.id)
    unlinked = _usage(None, b, grams=100.0, cost=2.0, spool_id=spool.id)
    db_session.add_all([usage_a, usage_b, unlinked])
    spool.cost_per_kg = 30.0
    await db_session.commit()
    member = await _member(async_client, [a.id])

    await _recalculate(async_client, headers=member)

    assert (await _fresh(db_session, SpoolUsageHistory, usage_a.id)).cost == 3.0
    assert (await _fresh(db_session, SpoolUsageHistory, usage_b.id)).cost == 2.0
    assert (await _fresh(db_session, SpoolUsageHistory, unlinked.id)).cost == 2.0


async def test_usage_of_no_archive_is_repriced_for_an_unlimited_caller(
    async_client: AsyncClient, db_session, printer_factory
):
    """It shows on the spool's usage list."""
    db_session.add(Settings(key="default_filament_cost", value="20"))
    printer = await printer_factory()
    spool = await _spool(db_session, 20.0)
    unlinked = _usage(None, printer, grams=100.0, cost=2.0, spool_id=spool.id)
    db_session.add(unlinked)
    spool.cost_per_kg = 30.0
    await db_session.commit()

    await _recalculate(async_client)

    assert (await _fresh(db_session, SpoolUsageHistory, unlinked.id)).cost == 3.0


async def test_a_spoolman_spool_given_a_price_prices_its_print(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    """Unpriced at completion, the print took the archive's estimate; with the
    spool priced now, the run follows the archive's new cost."""
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="default_filament_cost", value="25"))
    printer = await printer_factory()
    archive = await _print(archive_factory, printer, grams=100.0, cost=3.0)
    usage = _usage(archive, printer, grams=100.0, cost=None, spoolman_spool_id=41)
    db_session.add(usage)
    await db_session.commit()

    with _spoolman([{"id": 41, "price": 40.0, "initial_weight": 1000, "filament": {"weight": 1000}}]):
        await _recalculate(async_client)

    assert (await _fresh(db_session, SpoolUsageHistory, usage.id)).cost == 4.0
    assert (await _fresh(db_session, PrintArchive, archive.id)).cost == 4.0
    assert [run.cost for run in await _runs(db_session, archive.id)] == [4.0]


async def test_a_spoolman_spool_losing_its_price_keeps_its_print_and_run_together(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    """The archive keeps a Spoolman-priced cost it can't rebuild (#2591); its
    run must not be moved away from it."""
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="default_filament_cost", value="25"))
    printer = await printer_factory()
    archive = await _print(archive_factory, printer, grams=100.0, cost=2.0)
    usage = _usage(archive, printer, grams=100.0, cost=2.0, spoolman_spool_id=41)
    db_session.add(usage)
    await db_session.commit()

    with _spoolman([{"id": 41, "initial_weight": 1000, "filament": {"weight": 1000}}]):
        await _recalculate(async_client)

    assert (await _fresh(db_session, SpoolUsageHistory, usage.id)).cost is None
    assert (await _fresh(db_session, PrintArchive, archive.id)).cost == 2.0
    assert [run.cost for run in await _runs(db_session, archive.id)] == [2.0]
