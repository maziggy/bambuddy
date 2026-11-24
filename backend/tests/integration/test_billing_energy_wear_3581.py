"""A print's end charges what the charging policy says (#3581).

Driven through real print starts and completions. Wear is known when the
print ends and is charged there, as the filament is. Energy is read from the
smart plug in the background a little later: only when the policy includes
it, and only for a run that has a start reading, the charge waits for that task,
on the run row, so nothing is lost when the task gives up or the backend
restarts in between.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.main import register_expected_print
from backend.app.models.archive import PrintArchive
from backend.app.models.finance import BudgetReservation
from backend.app.models.print_log import INCLUDE_OPEN_RUNS, RUN_OPEN, PrintLogEntry
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.services.finance_billing import recover_pending_print_charges
from backend.app.utils.local_time import utcnow_naive
from backend.tests.integration.test_external_print_confirmation_1898 import (  # noqa: F401
    _clear_print_state,
    _drive_print_start,
)
from backend.tests.integration.test_run_lifecycle import (  # noqa: F401
    FILE,
    SUBTASK,
    _all_runs,
    _charges,
    _clean,
    _complete,
    _run,
    farm,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

# What the background energy task writes a charge with: not called for a
# charge written at the print's end.
WRITES_A_WAITING_CHARGE = "backend.app.services.finance_billing.apply_pending_print_charge"
FILAMENT_ONLY = (-5.0, f"Print charge: {SUBTASK}")
# The smart plug that gives a print's start reading.
PLUG = 1


async def _switch_on(db_session, *keys: str, **values: str) -> None:
    db_session.add_all([Settings(key=key, value="true") for key in keys])
    db_session.add_all([Settings(key=key, value=value) for key, value in values.items()])
    await db_session.commit()


async def _start(
    test_engine,
    db_session,
    farm,
    *,
    start_kwh: float | None = None,
    hours: float = 0,
    queued_by_nobody: bool = False,
    run_id: str = "run-1",
    planned_cost: float | None = None,
    planned_grams: float | None = None,
) -> int:
    """A queued print of the archive, started, holding a budget reservation.
    ``start_kwh`` is the plug's reading at its start, ``hours`` how long ago
    it started. Queued by the farm's user, or by nobody (login off).
    ``planned_cost`` is what its start priced the run at, for
    ``planned_grams``, at the spools mapped for it (#3580); None when it could
    not. Returns the reservation's id."""
    item = PrintQueueItem(
        printer_id=farm.printer,
        archive_id=farm.archive,
        status="printing",
        position=1,
        plate_id=1,
        created_by_id=None if queued_by_nobody else farm.user,
        cost_center_id=farm.center,
        estimated_cost=5.0,
        billing_run_id=run_id,
    )
    db_session.add(item)
    await db_session.flush()
    hold = BudgetReservation(
        cost_center_id=farm.center,
        amount=5.0,
        status="active",
        source_type="print_queue",
        source_id=item.id,
        print_archive_id=farm.archive,
    )
    db_session.add(hold)
    await db_session.commit()
    hold_id = hold.id
    register_expected_print(farm.printer, FILE, farm.archive, plate_id=1, cost_center_id=farm.center)
    await _drive_print_start(
        test_engine,
        SimpleNamespace(id=farm.printer),
        download_ok=False,
        # A background task with nothing to read here; it would write between
        # the test's own writes on the one shared connection.
        extra_patches=[patch("backend.app.main._schedule_archive_cost_estimate")],
    )

    db_session.expire_all()
    archive = await db_session.get(PrintArchive, farm.archive)
    run = await db_session.scalar(
        select(PrintLogEntry)
        .where(PrintLogEntry.archive_id == farm.archive, PrintLogEntry.status == RUN_OPEN)
        .execution_options(**{INCLUDE_OPEN_RUNS: True})
    )
    assert run is not None, "the print's start opens its run"
    if hours:
        archive.started_at = run.started_at = utcnow_naive() - timedelta(hours=hours)
    if start_kwh is not None:
        archive.energy_start_kwh = start_kwh
        archive.energy_start_plug_id = PLUG
    # The start's own estimate is the background task patched out above; what
    # it writes on the run is written here in its place.
    run.planned_cost = planned_cost
    run.planned_grams = planned_grams
    await db_session.commit()
    return hold_id


def _plug(end_kwh: float = 10.5, energy_cost: float = 0.08, plug_id: int = PLUG):
    """The printer's smart plug, read at the print's end."""
    plug = MagicMock(id=plug_id)
    plug.name = "Plug"

    async def reading(candidates, read, db):
        return plug, {"total": end_kwh}

    return (
        patch("backend.app.main.energy_plug_candidates", AsyncMock(return_value=[plug])),
        patch("backend.app.main.select_energy_reading", side_effect=reading),
        patch("backend.app.services.energy_price.print_energy_cost", AsyncMock(return_value=energy_cost)),
        patch("backend.app.services.energy_price.current_price", AsyncMock(return_value=0.3)),
    )


async def _hold_status(db_session, hold_id: int) -> str:
    db_session.expire_all()
    return (await db_session.get(BudgetReservation, hold_id)).status


async def _waiting(db_session, farm) -> list[str | None]:
    return [run.pending_charge for run in await _all_runs(db_session, farm.archive)]


async def test_off_the_charge_is_the_filaments_written_at_the_end(test_engine, db_session, farm):
    """Wear rate set, plug read, nothing configured: nothing changes."""
    (await db_session.get(Printer, farm.printer)).wear_cost_per_hour = 0.25
    hold = await _start(test_engine, db_session, farm, start_kwh=10.0, hours=2)

    plug = _plug()
    with plug[0], plug[1], plug[2], plug[3], patch(WRITES_A_WAITING_CHARGE, new=AsyncMock()) as background:
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    background.assert_not_awaited()
    [charge] = await _charges(db_session)
    assert (charge.amount, charge.description) == FILAMENT_ONLY
    assert await _waiting(db_session, farm) == [None]
    assert await _hold_status(db_session, hold) == "consumed"


async def test_wear_is_charged_at_the_prints_end(test_engine, db_session, farm):
    """Two hours on a printer that wears 0.25 an hour: the figure its print
    log entry shows."""
    await _switch_on(db_session, "billing_completed_wear")
    (await db_session.get(Printer, farm.printer)).wear_cost_per_hour = 0.25
    hold = await _start(test_engine, db_session, farm, start_kwh=10.0, hours=2)

    plug = _plug()
    with plug[0], plug[1], plug[2], plug[3], patch(WRITES_A_WAITING_CHARGE, new=AsyncMock()) as background:
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    background.assert_not_awaited()
    [charge] = await _charges(db_session)
    assert (charge.amount, charge.description) == (-5.5, f"Print charge: {SUBTASK} (filament 5.00 + wear 0.50)")
    [run] = await _all_runs(db_session, farm.archive)
    assert round(run.wear_cost, 2) == 0.5, "the charge's wear is the run's"
    assert await _hold_status(db_session, hold) == "consumed"


async def test_wear_is_charged_to_the_runs_own_end_not_the_archives(test_engine, db_session, farm):
    """Another plate of the archive starting just before the charge is written
    clears the archive's end time: the wear is still the run's two hours."""
    await _switch_on(db_session, "billing_completed_wear")
    (await db_session.get(Printer, farm.printer)).wear_cost_per_hour = 0.25
    await _start(test_engine, db_session, farm, start_kwh=10.0, hours=2)

    async def another_plate_starts(*_args, **_kwargs):
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as other:
            (await other.get(PrintArchive, farm.archive)).completed_at = None
            await other.commit()

    plug = _plug()
    with (
        plug[0],
        plug[1],
        plug[2],
        plug[3],
        patch(WRITES_A_WAITING_CHARGE, new=AsyncMock()),
        patch("backend.app.main._price_unpriced_run", new=another_plate_starts),
    ):
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    [charge] = await _charges(db_session)
    assert (charge.amount, charge.description) == (-5.5, f"Print charge: {SUBTASK} (filament 5.00 + wear 0.50)")


async def test_wear_of_a_printer_without_a_rate_is_nothing(test_engine, db_session, farm):
    await _switch_on(db_session, "billing_completed_wear")
    await _start(test_engine, db_session, farm, hours=2)

    await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    [charge] = await _charges(db_session)
    assert (charge.amount, charge.description) == FILAMENT_ONLY


async def test_energy_is_charged_once_the_plug_is_read(test_engine, db_session, farm):
    await _switch_on(db_session, "billing_completed_energy", "billing_completed_wear")
    (await db_session.get(Printer, farm.printer)).wear_cost_per_hour = 0.25
    hold = await _start(test_engine, db_session, farm, start_kwh=10.0, hours=2)

    plug = _plug(energy_cost=0.08)
    with plug[0], plug[1], plug[2], plug[3]:
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    [charge] = await _charges(db_session)
    assert charge.transaction_type == "print_charge" and charge.print_run_id == "run-1"
    assert charge.amount == -5.58
    assert charge.description == f"Print charge: {SUBTASK} (filament 5.00 + energy 0.08 + wear 0.50)"
    [run] = await _all_runs(db_session, farm.archive)
    assert run.energy_cost == 0.08
    assert await _waiting(db_session, farm) == [None]
    assert await _hold_status(db_session, hold) == "consumed"


async def test_a_run_without_a_start_reading_is_charged_at_its_end(test_engine, db_session, farm):
    """Nothing to wait for: charged as before, and the description says the
    energy is missing."""
    await _switch_on(db_session, "billing_completed_energy")
    hold = await _start(test_engine, db_session, farm)

    with patch(WRITES_A_WAITING_CHARGE, new=AsyncMock()) as background:
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    background.assert_not_awaited()

    [charge] = await _charges(db_session)
    assert charge.amount == -5.0
    assert charge.description == f"Print charge: {SUBTASK} (filament 5.00; energy not available)"
    assert await _waiting(db_session, farm) == [None]
    assert await _hold_status(db_session, hold) == "consumed"


def _no_plug():
    return [patch("backend.app.main.energy_plug_candidates", AsyncMock(return_value=[]))]


def _no_counter():
    return [
        patch("backend.app.main.energy_plug_candidates", AsyncMock(return_value=[MagicMock(id=1)])),
        patch("backend.app.main.select_energy_reading", AsyncMock(return_value=None)),
    ]


def _counter_reset():
    return list(_plug(end_kwh=0.2))


def _plug_error():
    return [patch("backend.app.main.energy_plug_candidates", AsyncMock(side_effect=RuntimeError("plug offline")))]


@pytest.mark.parametrize("energy_task_gives_up", [_no_plug, _no_counter, _counter_reset, _plug_error])
async def test_energy_that_cannot_be_read_does_not_keep_the_charge(test_engine, db_session, farm, energy_task_gives_up):
    """Every way the energy task ends without a figure: the filament is still
    charged, the description says so, and the reservation is consumed."""
    await _switch_on(db_session, "billing_completed_energy")
    hold = await _start(test_engine, db_session, farm, start_kwh=10.0)

    patches = energy_task_gives_up()
    for p in patches:
        p.start()
    try:
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)
    finally:
        for p in patches:
            p.stop()

    [charge] = await _charges(db_session)
    assert charge.amount == -5.0
    assert charge.description == f"Print charge: {SUBTASK} (filament 5.00; energy not available)"
    assert await _waiting(db_session, farm) == [None]
    assert await _hold_status(db_session, hold) == "consumed"


async def test_a_failed_run_pays_its_share_of_filament_and_all_its_energy(test_engine, db_session, farm):
    await _switch_on(db_session, "billing_failed_energy")
    (await db_session.get(PrintArchive, farm.archive)).filament_used_grams = 100.0
    await _start(test_engine, db_session, farm, start_kwh=10.0)

    plug = _plug(energy_cost=0.08)
    with plug[0], plug[1], plug[2], plug[3]:
        await _complete(test_engine, farm.printer, "failed", name=SUBTASK, extra={"last_progress": 50})

    [charge] = await _charges(db_session)
    assert charge.amount == -2.58
    assert charge.description == (
        f"Print charge: {SUBTASK} (filament 2.50 + energy 0.08) [failed: 50.0% filament (50.0g/100.0g)]"
    )


async def test_a_charge_that_fails_after_the_energy_is_reported(test_engine, db_session, farm):
    """The alert follows the charge into the background, and the charge stays
    on the run for the next startup."""
    await _switch_on(db_session, "billing_completed_energy")
    hold = await _start(test_engine, db_session, farm, start_kwh=10.0)

    plug = _plug()
    with (
        plug[0],
        plug[1],
        plug[2],
        plug[3],
        patch(
            "backend.app.services.finance_billing.apply_print_charge_for_archive",
            AsyncMock(side_effect=RuntimeError("ledger unavailable")),
        ),
    ):
        notif = await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    notif.on_billing_charge_failed.assert_called_once()
    assert notif.on_billing_charge_failed.call_args.args[3:5] == (farm.archive, "ledger unavailable")
    assert await _charges(db_session) == []
    assert await _hold_status(db_session, hold) == "active"
    [waiting] = await _waiting(db_session, farm)
    assert waiting is not None

    sessions = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    assert await recover_pending_print_charges(sessions) == 1
    assert [c.amount for c in await _charges(db_session)] == [-5.08]
    assert await _hold_status(db_session, hold) == "consumed"


async def test_a_restart_before_the_plug_is_read_does_not_lose_the_charge(test_engine, db_session, farm):
    """The backend stops while the energy task runs: no charge yet, the
    reservation still held. The next startup writes it, without the energy."""
    await _switch_on(db_session, "billing_completed_energy")
    hold = await _start(test_engine, db_session, farm, start_kwh=10.0)

    with (
        patch("backend.app.main.energy_plug_candidates", AsyncMock(return_value=[MagicMock(id=1)])),
        patch("backend.app.main.select_energy_reading", AsyncMock(side_effect=asyncio.CancelledError())),
    ):
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    assert await _charges(db_session) == []
    assert await _hold_status(db_session, hold) == "active"
    [run] = await _all_runs(db_session, farm.archive)
    assert run.status == "completed" and run.pending_charge is not None

    sessions = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    assert await recover_pending_print_charges(sessions) == 1

    [charge] = await _charges(db_session)
    assert (charge.amount, charge.print_run_id) == (-5.0, "run-1")
    assert charge.description == f"Print charge: {SUBTASK} (filament 5.00; energy not available)"
    assert await _waiting(db_session, farm) == [None]
    assert await _hold_status(db_session, hold) == "consumed"
    # Once: a second startup finds nothing waiting.
    assert await recover_pending_print_charges(sessions) == 0
    assert len(await _charges(db_session)) == 1


async def _fail(test_engine, db_session, farm, *, hours: float = 0, start_kwh: float | None = None) -> int:
    """A print of 100 g that fails half way. Returns its reservation's id."""
    (await db_session.get(PrintArchive, farm.archive)).filament_used_grams = 100.0
    hold = await _start(test_engine, db_session, farm, start_kwh=start_kwh, hours=hours)
    plug = _plug(energy_cost=0.08)
    with plug[0], plug[1], plug[2], plug[3]:
        await _complete(test_engine, farm.printer, "failed", name=SUBTASK, extra={"last_progress": 50})
    return hold


async def test_each_outcome_has_its_own_switches(test_engine, db_session, farm):
    """Energy and wear switched on for completed prints only: a failed print
    is charged as before."""
    await _switch_on(db_session, "billing_completed_energy", "billing_completed_wear")
    (await db_session.get(Printer, farm.printer)).wear_cost_per_hour = 0.25

    await _fail(test_engine, db_session, farm, hours=2, start_kwh=10.0)

    [charge] = await _charges(db_session)
    assert charge.amount == -2.5
    assert charge.description == f"Print charge: {SUBTASK} [failed: 50.0% filament (50.0g/100.0g)]"


async def test_a_completed_print_is_not_charged_by_the_failed_switches(test_engine, db_session, farm):
    await _switch_on(db_session, "billing_failed_energy", "billing_failed_wear", billing_failed_filament="none")
    (await db_session.get(Printer, farm.printer)).wear_cost_per_hour = 0.25
    await _start(test_engine, db_session, farm, start_kwh=10.0, hours=2)

    plug = _plug()
    with plug[0], plug[1], plug[2], plug[3]:
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    [charge] = await _charges(db_session)
    assert (charge.amount, charge.description) == FILAMENT_ONLY


async def test_a_failed_print_charged_in_full(test_engine, db_session, farm):
    await _switch_on(db_session, billing_failed_filament="full")

    hold = await _fail(test_engine, db_session, farm)

    [charge] = await _charges(db_session)
    assert charge.amount == -5.0
    assert charge.description == (
        f"Print charge: {SUBTASK} (filament 5.00; filament charged in full) [failed: full filament]"
    )
    assert await _hold_status(db_session, hold) == "consumed"


async def test_a_failed_print_not_charged_releases_its_reservation(test_engine, db_session, farm):
    await _switch_on(db_session, billing_failed_filament="none")

    hold = await _fail(test_engine, db_session, farm)

    assert await _charges(db_session) == []
    assert await _hold_status(db_session, hold) == "released"


async def test_a_failed_print_not_charged_for_filament_pays_its_wear(test_engine, db_session, farm):
    await _switch_on(db_session, "billing_failed_wear", billing_failed_filament="none")
    (await db_session.get(Printer, farm.printer)).wear_cost_per_hour = 0.25

    hold = await _fail(test_engine, db_session, farm, hours=2)

    [charge] = await _charges(db_session)
    assert charge.amount == -0.5
    assert charge.description == (
        f"Print charge: {SUBTASK} (wear 0.50; filament not charged) [failed: filament not charged]"
    )
    assert await _hold_status(db_session, hold) == "consumed"


async def test_a_failed_print_charged_in_full_waits_for_its_energy(test_engine, db_session, farm):
    """The filament mode is kept with the waiting charge."""
    await _switch_on(db_session, "billing_failed_energy", billing_failed_filament="full")

    hold = await _fail(test_engine, db_session, farm, start_kwh=10.0)

    [charge] = await _charges(db_session)
    assert charge.amount == -5.08
    assert charge.description == (
        f"Print charge: {SUBTASK} (filament 5.00 + energy 0.08; filament charged in full) [failed: full filament]"
    )
    assert await _waiting(db_session, farm) == [None]
    assert await _hold_status(db_session, hold) == "consumed"


async def test_a_print_that_ended_offline_is_not_charged_its_energy(test_engine, db_session, farm):
    """Its end is noticed at the reconnect and the plug read then: that
    reading holds everything since the real end (idle draw, other prints).
    Charged at once, without energy, as its wear is nothing."""
    await _switch_on(db_session, "billing_completed_energy", "billing_completed_wear")
    (await db_session.get(Printer, farm.printer)).wear_cost_per_hour = 0.25
    hold = await _start(test_engine, db_session, farm, start_kwh=10.0, hours=2)

    plug = _plug(end_kwh=60.0, energy_cost=15.0)
    with plug[0], plug[1], plug[2], plug[3], patch(WRITES_A_WAITING_CHARGE, new=AsyncMock()) as background:
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK, extra={"_reconciled": True})

    background.assert_not_awaited()
    [charge] = await _charges(db_session)
    assert charge.amount == -5.0
    assert charge.description == f"Print charge: {SUBTASK} (filament 5.00; energy not available)"
    assert await _waiting(db_session, farm) == [None]
    assert await _hold_status(db_session, hold) == "consumed"


async def test_a_print_that_ended_offline_at_an_estimated_time_is_not_charged_its_energy(test_engine, db_session, farm):
    """An end time worked out for it gives its duration, and so its wear. The
    plug is still read at the reconnect: a print of an hour found two days
    later would be charged two days of the plug's counter."""
    await _switch_on(db_session, "billing_completed_energy", "billing_completed_wear")
    (await db_session.get(Printer, farm.printer)).wear_cost_per_hour = 0.25
    hold = await _start(test_engine, db_session, farm, start_kwh=10.0, hours=2)

    plug = _plug(end_kwh=60.0, energy_cost=15.0)
    with plug[0], plug[1], plug[2], plug[3], patch(WRITES_A_WAITING_CHARGE, new=AsyncMock()) as background:
        await _complete(
            test_engine,
            farm.printer,
            "completed",
            name=SUBTASK,
            extra={"_reconciled": True, "_reconciled_completed_at": utcnow_naive() - timedelta(hours=1)},
        )

    background.assert_not_awaited()
    [charge] = await _charges(db_session)
    assert charge.amount == -5.25
    assert charge.description == f"Print charge: {SUBTASK} (filament 5.00 + wear 0.25; energy not available)"
    assert await _waiting(db_session, farm) == [None]
    assert await _hold_status(db_session, hold) == "consumed"


async def test_energy_read_from_another_plug_is_not_charged(test_engine, db_session, farm):
    """The start reading's plug does not answer at the end and another plug
    of the printer does, with a lifetime counter of its own: the difference
    of the two is not what the print used. Saved on the run as before, and
    not charged."""
    await _switch_on(db_session, "billing_completed_energy")
    hold = await _start(test_engine, db_session, farm, start_kwh=12.0)

    plug = _plug(end_kwh=950.0, energy_cost=281.4, plug_id=PLUG + 1)
    with plug[0], plug[1], plug[2], plug[3]:
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    [charge] = await _charges(db_session)
    assert charge.amount == -5.0
    assert charge.description == f"Print charge: {SUBTASK} (filament 5.00; energy not available)"
    [run] = await _all_runs(db_session, farm.archive)
    assert run.energy_kwh == 938.0, "the print log's figure is left as it was"
    assert await _waiting(db_session, farm) == [None]
    assert await _hold_status(db_session, hold) == "consumed"


async def test_energy_read_from_another_plug_is_not_charged_by_a_restart_either(test_engine, db_session, farm):
    """The backend stops after the energy was saved on the run and before the
    charge: startup charges the figure saved, but not this one."""
    await _switch_on(db_session, "billing_completed_energy")
    await _start(test_engine, db_session, farm, start_kwh=12.0)

    plug = _plug(end_kwh=950.0, energy_cost=281.4, plug_id=PLUG + 1)
    with plug[0], plug[1], plug[2], plug[3], patch(WRITES_A_WAITING_CHARGE, new=AsyncMock()):
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)
    [run] = await _all_runs(db_session, farm.archive)
    assert run.energy_cost == 281.4 and run.pending_charge is not None

    sessions = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    assert await recover_pending_print_charges(sessions) == 1

    [charge] = await _charges(db_session)
    assert charge.amount == -5.0
    assert charge.description == f"Print charge: {SUBTASK} (filament 5.00; energy not available)"


async def test_a_completed_print_on_free_filament_pays_its_energy_and_wear(test_engine, db_session, farm):
    """No filament cost (free spools): the printer and the power were still
    used. Through the charge's ``base_cost_override``, as a print's end passes it."""
    await _switch_on(db_session, "billing_completed_energy", "billing_completed_wear")
    (await db_session.get(Printer, farm.printer)).wear_cost_per_hour = 0.25
    hold = await _start(test_engine, db_session, farm, start_kwh=10.0, hours=2)
    item = await db_session.scalar(select(PrintQueueItem))
    item.estimated_cost = 0.0
    (await db_session.get(PrintArchive, farm.archive)).cost = None
    await db_session.commit()

    plug = _plug(energy_cost=0.08)
    with plug[0], plug[1], plug[2], plug[3]:
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    [charge] = await _charges(db_session)
    assert charge.amount == -0.58
    assert charge.description == f"Print charge: {SUBTASK} (energy 0.08 + wear 0.50)"
    assert await _hold_status(db_session, hold) == "consumed"


@pytest.mark.sqlite_only  # run_with_retry tries again on SQLite's "database is locked" only
async def test_a_waiting_charge_is_written_again_when_the_database_is_locked(test_engine, db_session, farm):
    """SQLite has one writer: a charge that finds it busy is tried again, as
    the print log entry before it is, rather than left for the next startup
    with its reservation held."""
    from sqlalchemy.exc import OperationalError

    from backend.app.services import finance_billing

    await _switch_on(db_session, "billing_completed_energy")
    hold = await _start(test_engine, db_session, farm, start_kwh=10.0)
    charges_for_real = finance_billing.apply_print_charge_for_archive
    attempts = []

    async def locked_once(db, archive_id, **charge):
        attempts.append(archive_id)
        if len(attempts) == 1:
            raise OperationalError("INSERT", {}, Exception("database is locked"))
        return await charges_for_real(db, archive_id, **charge)

    plug = _plug()
    with (
        plug[0],
        plug[1],
        plug[2],
        plug[3],
        patch("backend.app.services.finance_billing.apply_print_charge_for_archive", side_effect=locked_once),
        patch("backend.app.core.database.asyncio.sleep", new=AsyncMock()),
    ):
        notif = await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    assert len(attempts) == 2
    notif.on_billing_charge_failed.assert_not_called()
    assert [c.amount for c in await _charges(db_session)] == [-5.08]
    assert await _waiting(db_session, farm) == [None]
    assert await _hold_status(db_session, hold) == "consumed"


async def test_a_second_end_of_the_run_leaves_its_waiting_charge_alone(test_engine, db_session, farm):
    """The run's real end keeps its charge for the energy, and a reconnect
    closes the same run again before it is written. The second end has no
    queue item and no user: charging by it, or keeping its charge over the
    first, would charge someone else."""
    await _switch_on(db_session, "billing_completed_energy")
    hold = await _start(test_engine, db_session, farm, start_kwh=10.0)
    with (
        patch("backend.app.main.energy_plug_candidates", AsyncMock(return_value=[MagicMock(id=PLUG)])),
        patch("backend.app.main.select_energy_reading", AsyncMock(side_effect=asyncio.CancelledError())),
    ):
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)
    [kept] = await _waiting(db_session, farm)
    assert kept is not None and '"print_queue_id": null' not in kept
    # As the reconnect finds it: still printing, its run still open.
    [run] = await _all_runs(db_session, farm.archive)
    run.status = RUN_OPEN
    (await db_session.get(PrintArchive, farm.archive)).status = "printing"
    await db_session.commit()

    with (
        patch("backend.app.main.energy_plug_candidates", AsyncMock(return_value=[MagicMock(id=PLUG)])),
        patch("backend.app.main.select_energy_reading", AsyncMock(side_effect=asyncio.CancelledError())),
    ):
        await _complete(
            test_engine,
            farm.printer,
            "completed",
            name=SUBTASK,
            extra={"_reconciled": True, "_reconciled_archive_id": farm.archive},
        )

    assert await _charges(db_session) == [], "not charged by the second end"
    assert await _waiting(db_session, farm) == [kept]
    assert await _hold_status(db_session, hold) == "active"

    sessions = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    assert await recover_pending_print_charges(sessions) == 1
    [charge] = await _charges(db_session)
    assert (charge.amount, charge.print_run_id, charge.user_id) == (-5.0, "run-1", farm.user)
    assert charge.print_queue_id is not None
    assert await _hold_status(db_session, hold) == "consumed"


async def test_a_charge_that_could_not_be_kept_is_not_looked_for_after_the_energy(test_engine, db_session, farm):
    """Keeping the waiting charge fails with the rest of the charge block:
    reported there, once, and the energy task has no charge to write."""
    from backend.app.services import finance_billing

    await _switch_on(db_session, "billing_completed_energy")
    await _start(test_engine, db_session, farm, start_kwh=10.0)
    keeps = finance_billing.defer_print_charge

    def keeps_what_cannot_be_saved(run, archive_id, charge):
        keeps(run, archive_id, charge)
        run.pending_charge = object()

    plug = _plug()
    with (
        plug[0],
        plug[1],
        plug[2],
        plug[3],
        patch("backend.app.services.finance_billing.defer_print_charge", side_effect=keeps_what_cannot_be_saved),
        patch(WRITES_A_WAITING_CHARGE, new=AsyncMock()) as background,
    ):
        notif = await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    notif.on_billing_charge_failed.assert_called_once()
    background.assert_not_awaited()
    assert await _charges(db_session) == []
    assert await _waiting(db_session, farm) == [None]


async def test_a_queue_item_deleted_while_the_charge_waits_does_not_stop_it(test_engine, db_session, farm):
    """The charge was kept with its queue item's id. PostgreSQL refused a
    wallet transaction pointing at a deleted item, at every startup."""
    from sqlalchemy import delete

    await _switch_on(db_session, "billing_completed_energy")
    hold = await _start(test_engine, db_session, farm, start_kwh=10.0)
    with (
        patch("backend.app.main.energy_plug_candidates", AsyncMock(return_value=[MagicMock(id=1)])),
        patch("backend.app.main.select_energy_reading", AsyncMock(side_effect=asyncio.CancelledError())),
    ):
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)
    [waiting] = await _waiting(db_session, farm)
    assert '"print_queue_id": ' in waiting and '"print_queue_id": null' not in waiting
    await db_session.execute(delete(PrintQueueItem))
    await db_session.commit()

    sessions = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    assert await recover_pending_print_charges(sessions) == 1

    [charge] = await _charges(db_session)
    assert (charge.amount, charge.print_queue_id) == (-5.0, None)
    assert await _waiting(db_session, farm) == [None]
    assert await _hold_status(db_session, hold) == "consumed"


@pytest.mark.parametrize("deleted", ["its queue item", "its archive"])
async def test_a_queued_print_of_nobody_is_not_charged_to_the_archives_creator_without_its_queue_item(
    test_engine, db_session, farm, async_client, deleted
):
    """Queued with login off: charged to its cost centre, without a user.
    Its queue item goes while the charge waits, deleted itself or with its
    archive (deleting an archive deletes its queue items, and keeps the
    archive's row for the statistics). The charge is still nobody's: the
    archive's creator did not print it."""
    await _switch_on(db_session, "billing_completed_energy")
    hold = await _start(test_engine, db_session, farm, start_kwh=10.0, queued_by_nobody=True)
    with (
        patch("backend.app.main.energy_plug_candidates", AsyncMock(return_value=[MagicMock(id=1)])),
        patch("backend.app.main.select_energy_reading", AsyncMock(side_effect=asyncio.CancelledError())),
    ):
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)
    [waiting] = await _waiting(db_session, farm)
    assert waiting is not None and '"charged_user_id": null' in waiting
    [item] = (await db_session.scalars(select(PrintQueueItem))).all()
    if deleted == "its queue item":
        assert (await async_client.delete(f"/api/v1/queue/{item.id}")).status_code == 200
    else:
        assert (await async_client.delete(f"/api/v1/archives/{farm.archive}")).status_code == 200
    db_session.expire_all()
    assert (await db_session.scalars(select(PrintQueueItem))).all() == []

    sessions = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    assert await recover_pending_print_charges(sessions) == 1

    [charge] = await _charges(db_session)
    assert (charge.amount, charge.user_id, charge.cost_center_id) == (-5.0, None, farm.center)
    assert charge.print_queue_id is None
    assert await _waiting(db_session, farm) == [None]
    # Deleting a queue item gives back what it reserved; the archive's delete
    # leaves it to the charge.
    assert await _hold_status(db_session, hold) == ("released" if deleted == "its queue item" else "consumed")


# --- A reprint: its own spools' price (#3580), and the policy on top ---------
#
# The archive keeps its first print's cost, 5.00. A later print of it is
# priced at the spools it prints with when it starts
# (``PrintLogEntry.planned_cost``), and that is the filament its charge is
# made from: with energy and wear added, waiting for the energy, written by a
# restart, and for a print that did not finish under each filament mode.


async def _reprint(test_engine, db_session, farm, **start) -> int:
    """The archive printed once at 5.00, then started again as ``_start``
    starts it. Returns the second print's reservation's id."""
    await _run(test_engine, db_session, farm, plate=1, status="completed", run_id="run-1")
    # Long over when the second starts, also one ``_start`` dates hours back.
    [first] = await _all_runs(db_session, farm.archive)
    first.started_at = utcnow_naive() - timedelta(hours=9)
    first.completed_at = utcnow_naive() - timedelta(hours=8)
    await db_session.commit()
    return await _start(test_engine, db_session, farm, run_id="run-2", **start)


async def test_a_reprint_that_waits_for_its_energy_is_charged_at_its_own_spools(test_engine, db_session, farm):
    """Printed again on a spool that prices it at 8.40: that, not the first
    print's 5.00, with its energy and wear."""
    await _switch_on(db_session, "billing_completed_energy", "billing_completed_wear")
    (await db_session.get(Printer, farm.printer)).wear_cost_per_hour = 0.25
    hold = await _reprint(test_engine, db_session, farm, start_kwh=10.0, hours=2, planned_cost=8.4)

    plug = _plug(energy_cost=0.08)
    with plug[0], plug[1], plug[2], plug[3]:
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    first, reprint = await _charges(db_session)
    assert first.amount == -5.0
    assert reprint.print_run_id == "run-2"
    assert reprint.amount == -8.98
    assert reprint.description == f"Print charge: {SUBTASK} (filament 8.40 + energy 0.08 + wear 0.50)"
    assert await _waiting(db_session, farm) == [None, None]
    assert await _hold_status(db_session, hold) == "consumed"
    assert (await db_session.get(PrintArchive, farm.archive)).cost == 5.0, "the card keeps its first print's cost"


async def test_a_reprint_is_logged_at_its_filament_with_energy_and_wear_beside_it(test_engine, db_session, farm):
    """The Print Log's cost is the run's filament, at its own spools; its
    energy and wear have their own columns. The charge is the three."""
    await _switch_on(db_session, "billing_completed_energy", "billing_completed_wear")
    (await db_session.get(Printer, farm.printer)).wear_cost_per_hour = 0.25
    await _reprint(test_engine, db_session, farm, start_kwh=10.0, hours=2, planned_cost=8.4)

    plug = _plug(energy_cost=0.08)
    with plug[0], plug[1], plug[2], plug[3]:
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    reprint = (await _all_runs(db_session, farm.archive))[-1]
    assert (reprint.cost, reprint.energy_cost, round(reprint.wear_cost, 2)) == (8.4, 0.08, 0.5)
    assert (await _charges(db_session))[-1].amount == -8.98


async def test_a_restart_writes_a_waiting_reprint_at_its_own_spools(test_engine, db_session, farm):
    """The price is kept with the waiting charge. Startup writes it from
    there: not from the archive's cost, and not from a price the run gets
    later (the archive printed again and seen as this run)."""
    await _switch_on(db_session, "billing_completed_energy")
    hold = await _reprint(test_engine, db_session, farm, start_kwh=10.0, planned_cost=8.4)

    with (
        patch("backend.app.main.energy_plug_candidates", AsyncMock(return_value=[MagicMock(id=1)])),
        patch("backend.app.main.select_energy_reading", AsyncMock(side_effect=asyncio.CancelledError())),
    ):
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    assert len(await _charges(db_session)) == 1, "the first print's only"
    assert await _hold_status(db_session, hold) == "active"
    reprint = (await _all_runs(db_session, farm.archive))[-1]
    assert reprint.pending_charge is not None
    reprint.planned_cost = 99.0
    (await db_session.get(PrintArchive, farm.archive)).cost = 77.0
    await db_session.commit()

    sessions = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    assert await recover_pending_print_charges(sessions) == 1

    charge = (await _charges(db_session))[-1]
    assert charge.amount == -8.4
    assert charge.description == f"Print charge: {SUBTASK} (filament 8.40; energy not available)"
    assert await _hold_status(db_session, hold) == "consumed"


async def test_a_waiting_reprint_whose_log_entry_is_deleted_is_charged_at_its_own_spools(test_engine, db_session, farm):
    """Deleting the run writes its waiting charge first, at the same price."""
    from backend.app.services.finance_billing import settle_pending_print_charges

    await _switch_on(db_session, "billing_completed_energy")
    hold = await _reprint(test_engine, db_session, farm, start_kwh=10.0, planned_cost=8.4)

    plug = _plug(energy_cost=0.08)
    with plug[0], plug[1], plug[2], plug[3], patch(WRITES_A_WAITING_CHARGE, new=AsyncMock()):
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)
    reprint = (await _all_runs(db_session, farm.archive))[-1]
    assert reprint.pending_charge is not None
    reprint_id = reprint.id

    assert await settle_pending_print_charges(db_session, PrintLogEntry.id == reprint_id) == 1
    await db_session.commit()

    charge = (await _charges(db_session))[-1]
    assert charge.amount == -8.48
    assert charge.description == f"Print charge: {SUBTASK} (filament 8.40 + energy 0.08)"
    assert await _hold_status(db_session, hold) == "consumed"
    # Once: the reservation is settled with the charge, not again after it.
    assert await settle_pending_print_charges(db_session, PrintLogEntry.id == reprint_id) == 0
    assert len(await _charges(db_session)) == 2


@pytest.mark.parametrize(
    ("mode", "amount", "text"),
    [
        (
            "proportional",
            -4.78,
            "(filament 4.20 + energy 0.08 + wear 0.50) [failed: 50.0% filament (20.0g/40.0g)]",
        ),
        (
            "full",
            -8.98,
            "(filament 8.40 + energy 0.08 + wear 0.50; filament charged in full) [failed: full filament]",
        ),
        ("none", -0.58, "(energy 0.08 + wear 0.50; filament not charged) [failed: filament not charged]"),
    ],
)
async def test_a_failed_reprint_is_charged_by_the_policy_from_its_own_price(
    test_engine, db_session, farm, mode, amount, text
):
    """Priced at 8.40 for 40 g and failed half way. The archive's figures
    (5.00, 100 g) are the first print's and price nothing of this one."""
    await _switch_on(db_session, "billing_failed_energy", "billing_failed_wear", billing_failed_filament=mode)
    (await db_session.get(Printer, farm.printer)).wear_cost_per_hour = 0.25
    (await db_session.get(PrintArchive, farm.archive)).filament_used_grams = 100.0
    hold = await _reprint(test_engine, db_session, farm, start_kwh=10.0, hours=2, planned_cost=8.4, planned_grams=40.0)

    plug = _plug(energy_cost=0.08)
    with plug[0], plug[1], plug[2], plug[3]:
        await _complete(test_engine, farm.printer, "failed", name=SUBTASK, extra={"last_progress": 50})

    reprint = (await _charges(db_session))[-1]
    assert reprint.print_run_id == "run-2"
    assert reprint.amount == amount
    assert reprint.description == f"Print charge: {SUBTASK} {text}"
    assert await _hold_status(db_session, hold) == "consumed"


async def test_a_reprint_on_free_spools_pays_its_wear_and_no_filament(test_engine, db_session, farm):
    """Priced at 0 (spools priced 0 in the built-in inventory): a price, so
    the archive's 5.00 is not charged in its place. The wear still is."""
    await _switch_on(db_session, "billing_completed_wear")
    (await db_session.get(Printer, farm.printer)).wear_cost_per_hour = 0.25
    hold = await _reprint(test_engine, db_session, farm, hours=2, planned_cost=0.0, planned_grams=40.0)

    await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    reprint = (await _charges(db_session))[-1]
    assert reprint.print_run_id == "run-2"
    assert reprint.amount == -0.5
    assert reprint.description == f"Print charge: {SUBTASK} (wear 0.50)"
    assert await _hold_status(db_session, hold) == "consumed"


async def test_a_reprint_on_free_spools_with_nothing_else_to_charge_releases_its_reservation(
    test_engine, db_session, farm
):
    """Energy on, but no start reading, and no wear rate: nothing to charge."""
    await _switch_on(db_session, "billing_completed_energy", "billing_completed_wear")
    hold = await _reprint(test_engine, db_session, farm, hours=2, planned_cost=0.0, planned_grams=40.0)

    await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    assert len(await _charges(db_session)) == 1, "the first print's only"
    assert await _hold_status(db_session, hold) == "released"


async def test_a_reprint_nothing_priced_is_charged_the_archives_cost_with_its_energy(test_engine, db_session, farm):
    """No price for the run (no mapping; an unpriced spool at a default rate
    of 0): the filament is the archive's cost, as before, and the energy is
    added to that."""
    await _switch_on(db_session, "billing_completed_energy")
    await _reprint(test_engine, db_session, farm, start_kwh=10.0, planned_cost=None)

    plug = _plug(energy_cost=0.08)
    with plug[0], plug[1], plug[2], plug[3]:
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    reprint = (await _charges(db_session))[-1]
    assert reprint.amount == -5.08
    assert reprint.description == f"Print charge: {SUBTASK} (filament 5.00 + energy 0.08)"


async def test_a_reprint_that_ended_offline_is_charged_at_its_own_spools_without_energy(test_engine, db_session, farm):
    await _switch_on(db_session, "billing_completed_energy")
    hold = await _reprint(test_engine, db_session, farm, start_kwh=10.0, planned_cost=8.4)

    plug = _plug(end_kwh=60.0, energy_cost=15.0)
    with plug[0], plug[1], plug[2], plug[3], patch(WRITES_A_WAITING_CHARGE, new=AsyncMock()) as background:
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK, extra={"_reconciled": True})

    background.assert_not_awaited()
    reprint = (await _charges(db_session))[-1]
    assert reprint.amount == -8.4
    assert reprint.description == f"Print charge: {SUBTASK} (filament 8.40; energy not available)"
    assert await _hold_status(db_session, hold) == "consumed"
