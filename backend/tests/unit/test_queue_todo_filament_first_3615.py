"""The "To do" records next to "loaded filament first" (#3615 with #3587).

With the mode on, a model job that waits for filament gets a priority suffix on
its waiting text and, past the maximum wait, keeps a printer free. The filament
holds of the matcher are recorded all the same, and the same ones as with the
mode off. Only recording: the waiting texts are covered by the tests of #3587.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.print_scheduler import PrintScheduler, _is_unmatched_hold_reason
from backend.app.services.queue_todo import FilamentHold, TodoFilament, todo_filament_from_requirement
from backend.tests.unit.test_scheduler_loaded_filament_first_3587 import (
    _BROWN,
    _BROWN_TEXT,
    _DEFICIT,
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
            "_missing_force_color_pairs",
            MagicMock(side_effect=lambda pid, overrides, raw=None: [] if pid == 3 else [("PLA (Red)", FORCED_RED[0])]),
        ):
            (printer_id, reason), holds = await self._find(ctx, scheduler, {3}, filament_overrides=FORCED_RED)
        assert printer_id is None
        assert reason == (
            "No matching material/color. Waiting on PLA (Red) | Waiting for filament: X1C-3 (needs more filament)"
        )
        assert holds == {1: [RED], 2: [RED], 3: []}  # no reader for the short printer: nothing to name

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
        assert holds == {1: [PLA], 2: [PLA], 3: []}


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
        assert staged.waiting_reason == f"X1C-1 needs {_BROWN_TEXT}"
        recorded = scheduler.filament_holds_snapshot()[item_id]
        assert [(h.printer_id, [f.type for f in h.filaments]) for h in recorded] == [(1, ["PLA"])]

        await _mode(ctx, on=True)
        await _run(ctx, scheduler, unmatched_items={item_id})

        still = await _get(ctx, item_id)
        assert still.manual_start is True
        assert still.waiting_reason == f"X1C-1 needs {_BROWN_TEXT}"
        assert scheduler.filament_holds_snapshot()[item_id] == recorded


class TestSkippedJobsAreRecorded:
    """With the mode on a job is skipped where it used to be staged. The hold
    the staging gate recorded is recorded by the skip as well."""

    @pytest.mark.asyncio
    async def test_a_pinned_job_skipped_for_unmatched_filament(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx, printer_id=1)
        scheduler = PrintScheduler()

        await _run(ctx, scheduler, unmatched_items={item_id})

        item = await _get(ctx, item_id)
        assert item.manual_start is not True
        assert item.waiting_reason == f"Waiting for filament: {_BROWN_TEXT} · priority in 38 h"
        assert scheduler.filament_holds_snapshot()[item_id] == [
            FilamentHold(item_id, 1, None, (todo_filament_from_requirement(_BROWN[0]),))
        ]

    @pytest.mark.asyncio
    async def test_a_pinned_job_skipped_for_a_deficit(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx, printer_id=1, age_hours=49)
        scheduler = PrintScheduler()

        await _run(ctx, scheduler, deficit_items={item_id})

        assert (await _get(ctx, item_id)).waiting_reason.endswith(" · has priority")
        assert scheduler.filament_holds_snapshot()[item_id] == [FilamentHold(item_id, 1, None, (PLA,))]

    @pytest.mark.asyncio
    async def test_the_record_goes_when_the_job_fits_again(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx, printer_id=1)
        scheduler = PrintScheduler()
        await _run(ctx, scheduler, unmatched_items={item_id})
        assert item_id in scheduler.filament_holds_snapshot()

        result = await _run(ctx, scheduler)

        assert _dispatched(result) == [item_id]
        assert scheduler.filament_holds_snapshot() == {}

    @pytest.mark.asyncio
    async def test_a_model_job_that_loses_its_printer_after_assignment(self, ctx):
        """The race fallback: the matcher let printer 1 through, the check after
        the assignment did not. The job goes back to its model and the hold
        names the printer it was on."""
        await _mode(ctx)
        item_id = await _add_item(ctx)
        scheduler = PrintScheduler()

        await _run(ctx, scheduler, unmatched_items={item_id})

        item = await _get(ctx, item_id)
        assert item.printer_id is None
        assert item.waiting_reason.startswith(f"Waiting for filament: {_BROWN_TEXT}")
        assert scheduler.filament_holds_snapshot()[item_id] == [
            FilamentHold(item_id, 1, "X1C", (todo_filament_from_requirement(_BROWN[0]),))
        ]

    @pytest.mark.asyncio
    async def test_the_declined_wake_of_a_switched_off_printer(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx, printer_id=1)
        scheduler = PrintScheduler()
        plug = SimpleNamespace(
            id=1, name="plug", auto_on=True, enabled=True, printer_id=1, ha_entity_id=None, controls_printer_power=True
        )
        power_on = AsyncMock(return_value=False)
        patches = [
            patch.object(scheduler, "_get_smart_plugs", AsyncMock(return_value=[plug])),
            patch.object(scheduler, "_power_on_and_wait", power_on),
            patch.object(scheduler, "_cached_filament_shortfall", MagicMock(return_value=["PLA (Red)"])),
            patch.object(scheduler, "_cached_filament_shortfall_entries", MagicMock(return_value=[("PLA (Red)", RED)])),
        ]

        await _run(ctx, scheduler, connected=lambda pid: pid != 1, extra_patches=patches)

        power_on.assert_not_awaited()
        assert (await _get(ctx, item_id)).waiting_reason == "Waiting for filament: PLA (Red) · priority in 38 h"
        assert scheduler.filament_holds_snapshot()[item_id] == [FilamentHold(item_id, 1, None, (RED,))]


class TestShortPrintersAreRecorded:
    """A model job whose printers are all only short used to be staged on the
    first of them. With the mode on it waits for a refill on any of them."""

    @pytest.mark.asyncio
    async def test_every_short_printer_is_a_hold_with_the_short_filaments(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx)
        scheduler = PrintScheduler()
        mapping = [patch.object(scheduler, "_compute_ams_mapping_for_printer", AsyncMock(return_value=[0]))]

        await _run(ctx, scheduler, short={1, 2, 3}, deficit_items={item_id}, extra_patches=mapping)

        reason = (await _get(ctx, item_id)).waiting_reason
        assert reason == (
            "Waiting for filament: X1C-1 (needs more filament); X1C-2 (needs more filament); "
            "X1C-3 (needs more filament) · priority in 38 h"
        )
        holds = scheduler.filament_holds_snapshot()[item_id]
        assert sorted(holds, key=lambda h: h.printer_id) == _holds_on_all_x1c(item_id)

    @pytest.mark.asyncio
    async def test_a_full_printer_takes_the_job_and_nothing_is_recorded(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx)
        scheduler = PrintScheduler()

        with patch.object(scheduler, "_short_filaments_on", AsyncMock(return_value=[PLA])):
            result = await _run(ctx, scheduler, short={1, 2})

        assert _dispatched(result) == [item_id]
        assert scheduler.filament_holds_snapshot() == {}

    @pytest.mark.asyncio
    async def test_with_the_mode_off_the_short_filaments_are_not_read(self, ctx):
        await _mode(ctx, on=False)
        await _add_item(ctx)
        scheduler = PrintScheduler()
        short_filaments = AsyncMock(return_value=[PLA])

        with patch.object(scheduler, "_short_filaments_on", short_filaments):
            await _run(ctx, scheduler, short={1, 2, 3})

        short_filaments.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_short_printers_next_to_a_forced_colour_wait(self, ctx):
        scheduler = PrintScheduler()

        async def is_short(pid):
            return pid == 3

        async def short_filaments(pid):
            return [PLA]

        def misses(pid, overrides, raw=None):
            return [] if pid == 3 else [("PLA (Red)", FORCED_RED[0])]

        holds: dict[int, list[TodoFilament]] = {}
        with (
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
            patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=None)),
            patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
            patch.object(scheduler, "_missing_force_color_pairs", MagicMock(side_effect=misses)),
        ):
            async with ctx.session_maker() as db:
                printer_id, reason = await scheduler._find_idle_printer_for_model(
                    db,
                    "X1C",
                    set(),
                    filament_overrides=FORCED_RED,
                    is_short=is_short,
                    short_is_missing=True,
                    short_filaments=short_filaments,
                    holds_out=holds,
                )
        assert printer_id is None
        assert reason == (
            "No matching material/color. Waiting on PLA (Red) | Waiting for filament: X1C-3 (needs more filament)"
        )
        assert holds == {1: [RED], 2: [RED], 3: [PLA]}

    @pytest.mark.asyncio
    async def test_the_short_filaments_are_the_deficit_the_gate_reads(self, ctx):
        scheduler = PrintScheduler()
        item = MagicMock(skip_filament_check=False, id=5)
        deficit = AsyncMock(return_value=_DEFICIT)
        with (
            patch.object(scheduler, "_compute_ams_mapping_for_printer", AsyncMock(return_value=[0])),
            patch("backend.app.services.print_scheduler.compute_deficit_for_queue_item", deficit),
            patch.object(scheduler, "_get_filament_requirements", AsyncMock(return_value=None)),
        ):
            assert await scheduler._short_filaments_on(AsyncMock(), item, 1) == [PLA]

    @pytest.mark.asyncio
    async def test_nothing_short_or_unreadable_is_empty(self, ctx):
        scheduler = PrintScheduler()
        item = MagicMock(skip_filament_check=False, id=5)
        with patch.object(scheduler, "_compute_ams_mapping_for_printer", AsyncMock(return_value=[])):
            assert await scheduler._short_filaments_on(AsyncMock(), item, 1) == []
        with patch.object(scheduler, "_compute_ams_mapping_for_printer", AsyncMock(side_effect=RuntimeError)):
            assert await scheduler._short_filaments_on(AsyncMock(), item, 1) == []
        assert await scheduler._short_filaments_on(AsyncMock(), MagicMock(skip_filament_check=True), 1) == []
