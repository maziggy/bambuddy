"""Auto-off never cuts power under an AMS drying cycle (#3660).

A print that finishes while an AMS dries, a queue job's auto-off, or one
unit's drying finishing while another still dries: the off waits until no
unit dries, then the plug's delay, and only then switches the plug off.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services import smart_plug_manager as spm
from backend.app.services.smart_plug_manager import SmartPlugManager

DRYING = {"dry_time": 600, "dry_status": 2}
COOLING = {"dry_time": 0, "dry_status": 3}
IDLE = {"dry_time": 0, "dry_status": 0}


class _Printer:
    """The printer's AMS reports, one per look at its status; the last repeats."""

    def __init__(self, *reports):
        self.reports = list(reports)
        self.offs: list[float] = []

    def status(self, _printer_id):
        report = self.reports.pop(0) if len(self.reports) > 1 else self.reports[0]
        if report is None:
            return None
        return MagicMock(connected=True, raw_data={"ams": report}, temperatures={"nozzle": 20})


@pytest.fixture
def run():
    """Run an off task with a printer and a clock that only counts."""

    async def _run(printer: _Printer, coro_factory):
        manager = SmartPlugManager()
        slept: list[float] = []
        service = MagicMock(turn_off=AsyncMock(side_effect=lambda _p: printer.offs.append(sum(slept)) or True))

        async def sleep(seconds):
            slept.append(seconds)

        with (
            patch.object(spm, "printer_manager") as pm,
            patch.object(spm.asyncio, "sleep", sleep),
            patch.object(manager, "get_service_for_plug", AsyncMock(return_value=service)),
            patch.object(manager, "_mark_auto_off_executed", AsyncMock()),
            patch.object(manager, "_mark_auto_off_pending", AsyncMock()),
            patch.object(spm, "publish_plug_state", AsyncMock()),
            patch.object(spm, "run_linked_scripts_after_automatic_switch", AsyncMock()),
        ):
            pm.get_status.side_effect = printer.status
            pm.is_print_active.return_value = False
            await coro_factory(manager)
        return slept

    return _run


def _delayed(after_drying_seconds=300):
    return lambda m: m._delayed_off(
        1, "tasmota", "1.2.3.4", None, None, None, 7, 300, after_drying_seconds=after_drying_seconds
    )


@pytest.mark.asyncio
async def test_without_drying_the_plug_goes_off_after_its_delay(run):
    printer = _Printer([IDLE])
    await run(printer, _delayed())
    assert printer.offs == [300]


@pytest.mark.asyncio
async def test_a_print_finished_during_drying_waits_for_it(run):
    # Drying for two more looks, then cooling (still a live cycle), then over.
    printer = _Printer([DRYING], [DRYING], [DRYING], [COOLING], [IDLE])
    await run(printer, _delayed(after_drying_seconds=300))
    # 300 s print delay, four 30 s looks while it dried and cooled, then the
    # 300 s after drying.
    assert printer.offs == [300 + 4 * 30 + 300]


@pytest.mark.asyncio
async def test_one_unit_still_drying_holds_the_off(run):
    # Unit 0 is done (its own drying-complete off), unit 1 still dries.
    printer = _Printer([IDLE, DRYING], [IDLE, DRYING], [IDLE, IDLE])
    await run(printer, _delayed(after_drying_seconds=0))
    assert printer.offs == [300 + 2 * 30]


@pytest.mark.asyncio
async def test_drying_that_starts_again_in_the_wait_after_it_is_waited_out(run):
    printer = _Printer([DRYING], [IDLE], [DRYING], [IDLE])
    await run(printer, _delayed(after_drying_seconds=60))
    # Each cycle that ends is followed by the delay.
    assert printer.offs == [300 + 30 + 60 + 30 + 60]


@pytest.mark.asyncio
async def test_a_parked_countdown_is_no_cycle(run):
    printer = _Printer([{"dry_time": 600, "dry_countdown_stalled": True}])
    await run(printer, _delayed())
    assert printer.offs == [300]


@pytest.mark.asyncio
async def test_a_printer_that_has_not_reported_is_given_a_while(run):
    # Just after a restart: no status yet, then a report with a cycle running.
    printer = _Printer(None, None, [DRYING], [IDLE])
    await run(printer, _delayed(after_drying_seconds=0))
    assert printer.offs == [300 + 2 * 30 + 30]


@pytest.mark.asyncio
async def test_a_printer_that_never_reports_is_switched_off_as_before(run):
    printer = _Printer(None)
    await run(printer, _delayed())
    assert printer.offs == [300 + spm._DRYING_UNKNOWN_WAIT_SECONDS]


@pytest.mark.asyncio
async def test_the_temperature_off_waits_for_drying_too(run):
    printer = _Printer([DRYING], [DRYING], [IDLE])
    slept = await run(
        printer,
        lambda m: m._temp_based_off(1, "tasmota", "1.2.3.4", None, None, None, 7, 50, after_drying_seconds=0),
    )
    assert len(printer.offs) == 1
    assert slept == [30]


@pytest.mark.asyncio
async def test_a_print_start_cancels_a_postponed_off():
    manager = SmartPlugManager()
    plug = MagicMock(
        id=1,
        plug_type="tasmota",
        off_delay_mode="time",
        off_delay_minutes=0,
        auto_off_after_drying=False,
        off_delay_after_drying_minutes=10,
    )
    service = MagicMock(turn_off=AsyncMock(return_value=True))
    with (
        patch.object(spm, "printer_manager") as pm,
        patch.object(spm, "_DRYING_POLL_SECONDS", 0.01),
        patch.object(manager, "get_service_for_plug", AsyncMock(return_value=service)),
        patch.object(manager, "_mark_auto_off_pending", AsyncMock()),
        patch.object(spm, "spawn_background_task", MagicMock()),
    ):
        pm.is_print_active.return_value = False
        pm.get_status.return_value = MagicMock(connected=True, raw_data={"ams": [DRYING]})
        manager._schedule_delayed_off(plug, 7, 0)
        task = manager._pending_off[1]
        await asyncio.sleep(0.05)
        assert not task.done()
        manager._cancel_pending_off(1)
        await asyncio.gather(task, return_exceptions=True)

    service.turn_off.assert_not_called()


@pytest.mark.parametrize(
    ("after_drying", "expected"),
    [
        # The drying auto-off takes over then, with its own delay.
        (True, 600),
        (False, 300),
    ],
)
def test_the_delay_after_drying(after_drying, expected):
    plug = MagicMock(auto_off_after_drying=after_drying, off_delay_after_drying_minutes=10, off_delay_minutes=5)
    assert spm._after_drying_seconds(plug) == expected


@pytest.mark.asyncio
async def test_an_off_resumed_after_a_restart_waits_for_drying():
    # Time mode: its delay ran out while Bambuddy was down, so it goes off at
    # once, but not under a drying cycle.
    manager = SmartPlugManager()
    plug = MagicMock(
        id=1,
        printer_id=7,
        plug_type="tasmota",
        off_delay_mode="time",
        off_delay_minutes=0,
        auto_off_after_drying=False,
        auto_off_pending_since=None,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [plug])))
    session = MagicMock()
    session.return_value.__aenter__ = AsyncMock(return_value=db)
    session.return_value.__aexit__ = AsyncMock(return_value=False)
    service = MagicMock(turn_off=AsyncMock(return_value=True))
    with (
        patch("backend.app.core.database.async_session", session),
        patch.object(spm, "printer_manager") as pm,
        patch.object(spm, "_DRYING_POLL_SECONDS", 0.01),
        patch.object(manager, "get_service_for_plug", AsyncMock(return_value=service)),
        patch.object(manager, "_mark_auto_off_pending", AsyncMock()),
        patch.object(manager, "_mark_auto_off_executed", AsyncMock()),
        patch.object(spm, "publish_plug_state", AsyncMock()),
        patch.object(spm, "run_linked_scripts_after_automatic_switch", AsyncMock()),
        patch.object(spm, "spawn_background_task", MagicMock()),
    ):
        pm.is_print_active.return_value = False
        pm.get_status.return_value = MagicMock(connected=True, raw_data={"ams": [DRYING]})
        await manager.resume_pending_auto_offs()
        task = manager._pending_off[1]
        await asyncio.sleep(0.05)
        service.turn_off.assert_not_called()

        pm.get_status.return_value = MagicMock(connected=True, raw_data={"ams": [IDLE]})
        await task

    service.turn_off.assert_awaited_once()
