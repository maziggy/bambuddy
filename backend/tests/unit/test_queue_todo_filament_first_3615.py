"""The "To do" records next to "loaded filament first" (#3615 with #3587).

With the mode on, a model job that waits for filament gets a priority suffix on
its waiting text and, past the maximum wait, keeps a printer free. The filament
holds of the matcher are recorded all the same, and the same ones as with the
mode off. Only recording: the waiting texts are covered by the tests of #3587.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.print_scheduler import PrintScheduler, _is_unmatched_hold_reason
from backend.app.services.queue_todo import FilamentHold, TodoFilament
from backend.tests.unit.test_scheduler_loaded_filament_first_3587 import (
    _BROWN_TEXT,
    _add_item,
    _dispatched,
    _get,
    _mode,
    _run,
    ctx,  # noqa: F401 - fixture
)

PLA = TodoFilament("PLA", "")
RED = TodoFilament("PLA", "#FF0000", "Red")
FORCED_RED = [{"slot_id": 1, "type": "PLA", "color": "#FF0000", "color_name": "Red", "force_color_match": True}]


def _missing_pla(scheduler):
    """Every printer lacks the PLA the job asks for."""
    return [patch.object(scheduler, "_get_missing_filament_types", MagicMock(return_value=["PLA"]))]


async def _add_pla_item(ctx, **fields):
    return await _add_item(ctx, required_filament_types=json.dumps(["PLA"]), **fields)


def _holds_on_all_x1c(item_id):
    return [FilamentHold(item_id, pid, "X1C", (PLA,)) for pid in (1, 2, 3)]


class TestModelJobHolds:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("on", [True, False])
    async def test_the_same_holds_with_the_mode_on_and_off(self, ctx, on):
        await _mode(ctx, on=on)
        item_id = await _add_pla_item(ctx)
        scheduler = PrintScheduler()

        await _run(ctx, scheduler, extra_patches=_missing_pla(scheduler))

        reason = (await _get(ctx, item_id)).waiting_reason
        assert reason.startswith("Waiting for filament: X1C-1 (needs PLA)")
        assert reason.endswith(" · priority in 38 h") is on
        assert sorted(scheduler.filament_holds_snapshot()[item_id], key=lambda h: h.printer_id) == _holds_on_all_x1c(
            item_id
        )

    @pytest.mark.asyncio
    async def test_without_a_limit_the_holds_are_recorded_too(self, ctx):
        await _mode(ctx, hours="None")
        item_id = await _add_pla_item(ctx)
        scheduler = PrintScheduler()

        await _run(ctx, scheduler, extra_patches=_missing_pla(scheduler))

        assert "priority" not in (await _get(ctx, item_id)).waiting_reason
        assert len(scheduler.filament_holds_snapshot()[item_id]) == 3

    @pytest.mark.asyncio
    async def test_a_job_with_priority_keeps_its_holds_on_the_printer_it_reserves(self, ctx):
        """Past the maximum wait printer 1 is kept free for the job. It still
        lacks the filament there, so the hold on printer 1 stays in the list."""
        await _mode(ctx)
        waiting_item = await _add_pla_item(ctx, position=1, age_hours=49)
        second = await _add_item(ctx, position=2, age_hours=1)
        scheduler = PrintScheduler()

        result = await _run(ctx, scheduler, extra_patches=_missing_pla(scheduler))

        first = await _get(ctx, waiting_item)
        assert first.printer_id is None
        assert first.waiting_reason.endswith(" · has priority")
        # Printer 1 is reserved: the job behind goes to printer 2.
        assert _dispatched(result) == [second]
        assert (await _get(ctx, second)).printer_id == 2
        holds = scheduler.filament_holds_snapshot()
        assert sorted(holds[waiting_item], key=lambda h: h.printer_id) == _holds_on_all_x1c(waiting_item)
        assert second not in holds

    @pytest.mark.asyncio
    async def test_the_holds_go_when_the_filament_is_loaded(self, ctx):
        await _mode(ctx)
        item_id = await _add_pla_item(ctx, age_hours=49)
        scheduler = PrintScheduler()
        await _run(ctx, scheduler, extra_patches=_missing_pla(scheduler))
        assert item_id in scheduler.filament_holds_snapshot()

        loaded = [patch.object(scheduler, "_get_missing_filament_types", MagicMock(return_value=[]))]
        result = await _run(ctx, scheduler, extra_patches=loaded)

        assert _dispatched(result) == [item_id]
        assert scheduler.filament_holds_snapshot() == {}

    @pytest.mark.asyncio
    async def test_a_busy_only_wait_records_nothing(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx, age_hours=49)
        scheduler = PrintScheduler()

        await _run(ctx, scheduler, idle=lambda pid: False)

        assert (await _get(ctx, item_id)).waiting_reason == "Busy: X1C-1, X1C-2, X1C-3"
        assert scheduler.filament_holds_snapshot() == {}


class TestMatcherShortIsMissing:
    """The matcher's reason was reworded for short printers; the holds follow the colours."""

    async def _find(self, ctx, scheduler, short, **kwargs):
        async def is_short(pid):
            return pid in short

        holds: dict[int, list[TodoFilament]] = {}
        with (
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
            patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=None)),
            patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
        ):
            async with ctx.session_maker() as db:
                result = await scheduler._find_idle_printer_for_model(
                    db, "X1C", set(), is_short=is_short, short_is_missing=True, holds_out=holds, **kwargs
                )
        return result, holds

    @pytest.mark.asyncio
    async def test_a_forced_colour_wait_is_recorded(self, ctx):
        (printer_id, reason), holds = await self._find(ctx, PrintScheduler(), set(), filament_overrides=FORCED_RED)
        assert printer_id is None
        assert reason == "No matching material/color. Waiting on PLA (Red)"
        assert holds == {1: [RED], 2: [RED], 3: [RED]}

    @pytest.mark.asyncio
    async def test_a_forced_colour_wait_next_to_a_short_printer_is_recorded(self, ctx):
        scheduler = PrintScheduler()
        with patch.object(
            scheduler,
            "_force_color_misses",
            MagicMock(side_effect=lambda pid, overrides, raw=None: [] if pid == 3 else [(FORCED_RED[0], "PLA (Red)")]),
        ):
            (printer_id, reason), holds = await self._find(ctx, scheduler, {3}, filament_overrides=FORCED_RED)
        assert printer_id is None
        assert reason == (
            "No matching material/color. Waiting on PLA (Red) | Waiting for filament: X1C-3 (needs more filament)"
        )
        assert holds == {1: [RED], 2: [RED]}

    @pytest.mark.asyncio
    async def test_missing_types_next_to_a_short_printer_are_recorded(self, ctx):
        scheduler = PrintScheduler()
        with patch.object(
            scheduler,
            "_get_missing_filament_types",
            MagicMock(side_effect=lambda pid, types, raw=None: [] if pid == 3 else ["PLA"]),
        ):
            (printer_id, reason), holds = await self._find(ctx, scheduler, {3}, required_filament_types=["PLA"])
        assert printer_id is None
        assert reason == ("Waiting for filament: X1C-1 (needs PLA); X1C-2 (needs PLA); X1C-3 (needs more filament)")
        assert holds == {1: [PLA], 2: [PLA]}


class TestStagedHold:
    def test_the_unmatched_hold_reason_never_carries_a_priority_suffix(self):
        """The suffix goes on "Waiting for filament" reasons of jobs that are
        not staged; the staged "Needs ..." reason is recognised as before."""
        assert _is_unmatched_hold_reason(f"Needs {_BROWN_TEXT}")
        assert not _is_unmatched_hold_reason(f"Waiting for filament: {_BROWN_TEXT} · priority in 38 h")
        assert not _is_unmatched_hold_reason(f"Waiting for filament: {_BROWN_TEXT} · has priority")

    @pytest.mark.asyncio
    async def test_a_job_staged_before_the_mode_keeps_its_hold(self, ctx):
        await _mode(ctx, on=False)
        item_id = await _add_item(ctx, printer_id=1)
        scheduler = PrintScheduler()
        await _run(ctx, scheduler, unmatched_items={item_id})
        staged = await _get(ctx, item_id)
        assert staged.manual_start is True
        assert staged.waiting_reason == f"Needs {_BROWN_TEXT}"
        recorded = scheduler.filament_holds_snapshot()[item_id]
        assert [(h.printer_id, [f.type for f in h.filaments]) for h in recorded] == [(1, ["PLA"])]

        await _mode(ctx, on=True)
        await _run(ctx, scheduler, unmatched_items={item_id})

        still = await _get(ctx, item_id)
        assert still.manual_start is True
        assert still.waiting_reason == f"Needs {_BROWN_TEXT}"
        assert scheduler.filament_holds_snapshot()[item_id] == recorded
