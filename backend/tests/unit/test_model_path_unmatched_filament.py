"""An "any model" job is only given a printer the slot matcher can map (#3280).

Choosing the printer asked cheaper questions than dispatch does: is each
filament type loaded somewhere, is each forced colour loaded somewhere. On a
dual-nozzle printer the matcher only uses a spool on the slot's own nozzle, so
a printer with the filament on the other side passed the choice, the matcher
left the slot unmatched, and the unmatched-filament hold (#2799) staged the job
and gave the printer back. Start then found no printer to check, the next pass
chose the same printer, and the job was held again, with no Print Anyway
offered in between.

Now the choice asks the matcher itself and passes over a printer it cannot
map. When no idle printer can, the job stays pending with the filament it
needs in its reason and is picked up once one is loaded.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.print_scheduler import PrintScheduler
from backend.tests.unit.test_scheduler_model_filament_short_3137 import (
    _add_item,
    _get_item,
    _run,
    queue_db,  # noqa: F401 - fixture
)

NEEDS = ["PLA #FFFFFF (left nozzle)"]


def _unmatched_on(unmatched: set[int]) -> AsyncMock:
    """``_unmatched_on`` stand-in: the printers in ``unmatched`` cannot map the job."""

    async def check(db, item, printer_id):
        return list(NEEDS) if printer_id in unmatched else []

    return AsyncMock(side_effect=check)


class TestChoosingAPrinter:
    async def _find(self, ctx, unmatched: set[int], **kwargs):
        scheduler = PrintScheduler()

        async def unmatched_on(pid):
            return list(NEEDS) if pid in unmatched else []

        with (
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
            patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
        ):
            async with ctx.session_maker() as db:
                return await scheduler._find_idle_printer_for_model(
                    db, "X1C", set(), unmatched_on=unmatched_on, **kwargs
                )

    @pytest.mark.asyncio
    async def test_passes_over_a_printer_the_matcher_cannot_map(self, queue_db):
        assert await self._find(queue_db, {1}) == (2, None)

    @pytest.mark.asyncio
    async def test_no_printer_when_none_can_map_it(self, queue_db):
        printer_id, reason = await self._find(queue_db, {1, 2, 3})
        assert printer_id is None
        assert "PLA #FFFFFF (left nozzle)" in reason

    @pytest.mark.asyncio
    async def test_colour_ranking_skips_a_printer_the_matcher_cannot_map(self, queue_db):
        """Preference overrides rank printers by colour; the best one still has
        to be mappable."""
        overrides = [{"slot_id": 1, "type": "PLA", "color": "#FFFFFF"}]
        scheduler = PrintScheduler()

        async def unmatched_on(pid):
            return list(NEEDS) if pid == 2 else []

        with (
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
            patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
            patch.object(
                scheduler, "_count_override_color_matches", MagicMock(side_effect=lambda pid, o: 2 if pid == 2 else 1)
            ),
        ):
            async with queue_db.session_maker() as db:
                found = await scheduler._find_idle_printer_for_model(
                    db, "X1C", set(), filament_overrides=overrides, unmatched_on=unmatched_on
                )

        assert found == (1, None)


class TestQueuePass:
    @pytest.mark.asyncio
    async def test_goes_to_a_printer_that_can_map_it(self, queue_db):
        item_id = await _add_item(queue_db)
        launched = MagicMock()
        scheduler = PrintScheduler()

        with patch.object(scheduler, "_unmatched_on", _unmatched_on({1})):
            await _run(queue_db, scheduler, short=set(), launched=launched)

        launched.assert_called_once()
        item = await _get_item(queue_db, item_id)
        assert item.printer_id == 2
        assert item.manual_start is False

    @pytest.mark.asyncio
    async def test_waits_unassigned_while_no_printer_can_map_it(self, queue_db):
        item_id = await _add_item(queue_db)
        launched = MagicMock()
        scheduler = PrintScheduler()

        with patch.object(scheduler, "_unmatched_on", _unmatched_on({1, 2, 3})):
            await _run(queue_db, scheduler, short=set(), launched=launched)

        launched.assert_not_called()
        item = await _get_item(queue_db, item_id)
        assert item.printer_id is None
        assert item.manual_start is False
        assert "PLA #FFFFFF (left nozzle)" in item.waiting_reason

    @pytest.mark.asyncio
    async def test_a_job_held_for_low_filament_is_not_moved_to_a_printer_that_cannot_map_it(self, queue_db):
        item_id = await _add_item(queue_db, printer_id=1, manual_start=True, filament_short=True)
        launched = MagicMock()
        scheduler = PrintScheduler()

        with patch.object(scheduler, "_unmatched_on", _unmatched_on({2})):
            await _run(queue_db, scheduler, short={1}, launched=launched)

        assert (await _get_item(queue_db, item_id)).printer_id == 3


class TestUnmatchedOn:
    """The check itself answers what the unmatched-filament hold would."""

    REQ = [{"slot_id": 1, "type": "PLA", "color": "#FFFFFF", "nozzle_id": 1}]

    @staticmethod
    def _item(**fields):
        values = {
            "id": 7,
            "skip_filament_check": False,
            "filament_overrides": None,
            "archive_id": None,
            "library_file_id": 3,
            "plate_id": 1,
        }
        values.update(fields)
        return SimpleNamespace(**values)

    def _scheduler(self, mapping, requirements=REQ, loaded=True):
        scheduler = PrintScheduler()
        if isinstance(mapping, Exception):
            scheduler._compute_ams_mapping_for_printer = AsyncMock(side_effect=mapping)
        else:
            scheduler._compute_ams_mapping_for_printer = AsyncMock(return_value=mapping)
        scheduler._get_filament_requirements = AsyncMock(
            return_value=[dict(r) for r in requirements] if requirements else None
        )
        scheduler._build_loaded_filaments = MagicMock(
            return_value=[{"global_tray_id": 0, "type": "PLA", "color": "#FFFFFF"}] if loaded else []
        )
        return scheduler

    @pytest.mark.asyncio
    async def test_an_unmatched_slot_is_named(self):
        scheduler = self._scheduler([-1])
        with patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock()):
            assert await scheduler._unmatched_on(AsyncMock(), self._item(), 4) == NEEDS

    @pytest.mark.asyncio
    async def test_a_mapped_slot_is_fine(self):
        scheduler = self._scheduler([0])
        with patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock()):
            assert await scheduler._unmatched_on(AsyncMock(), self._item(), 4) == []

    @pytest.mark.asyncio
    async def test_print_anyway_is_never_unmatched(self):
        scheduler = self._scheduler([-1])
        assert await scheduler._unmatched_on(AsyncMock(), self._item(skip_filament_check=True), 4) == []

    @pytest.mark.asyncio
    async def test_no_mapping_counts_only_when_the_printer_reported_trays(self):
        """The hold's own rule (#2589): an empty tray list is not knowing yet."""
        with patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock()):
            assert await self._scheduler(None)._unmatched_on(AsyncMock(), self._item(), 4) == NEEDS
            assert await self._scheduler(None, loaded=False)._unmatched_on(AsyncMock(), self._item(), 4) == []

    @pytest.mark.asyncio
    async def test_a_failing_match_does_not_pass_the_printer_over(self):
        scheduler = self._scheduler(RuntimeError("boom"))
        with patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock()):
            assert await scheduler._unmatched_on(AsyncMock(), self._item(), 4) == []

    @pytest.mark.asyncio
    async def test_an_unreadable_file_is_judged_on_its_forced_colours(self):
        forced = '[{"slot_id": 1, "type": "PLA", "color": "#A4AAAC", "force_color_match": true}]'
        scheduler = self._scheduler([-1], requirements=None)
        with patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock()):
            assert await scheduler._unmatched_on(AsyncMock(), self._item(filament_overrides=forced), 4) == [
                "PLA #A4AAAC"
            ]


class TestDualNozzleEndToEnd:
    """The reported shape with the real matcher: white PLA on the right nozzle,
    a slot that prints on the left. Type checks pass; the matcher refuses."""

    REQ = [{"slot_id": 1, "type": "PLA", "color": "#FFFFFF", "nozzle_id": 1, "tray_info_idx": ""}]

    @staticmethod
    def _trays(extruder_id):
        return [
            {"global_tray_id": 0, "type": "PLA", "color": "#FFFFFF", "tray_info_idx": "", "extruder_id": extruder_id},
        ]

    def _scheduler(self, extruder_id):
        scheduler = PrintScheduler()
        scheduler._build_loaded_filaments = MagicMock(return_value=self._trays(extruder_id))
        scheduler._get_filament_requirements = AsyncMock(side_effect=lambda db, item: [dict(r) for r in self.REQ])
        scheduler._get_bool_setting = AsyncMock(return_value=False)
        scheduler._get_job_name = AsyncMock(return_value="bracket")
        scheduler._get_printer = AsyncMock(return_value=MagicMock(model="H2D"))
        return scheduler

    @staticmethod
    def _item():
        item = MagicMock()
        item.id = 9
        item.printer_id = 4
        item.archive_id = None
        item.library_file_id = 3
        item.plate_id = 1
        item.ams_mapping = None
        item.skip_filament_check = False
        item.filament_overrides = None
        item.manual_start = False
        item.waiting_reason = None
        return item

    @pytest.mark.parametrize(("extruder_id", "held"), [(0, True), (1, False)])
    @pytest.mark.asyncio
    async def test_the_choice_and_the_hold_agree(self, extruder_id, held):
        scheduler = self._scheduler(extruder_id)
        item = self._item()
        db = AsyncMock()

        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock(fila_switch=None, ams_filament_backup=None)
            unmatched = await scheduler._unmatched_on(db, item, 4)
            await scheduler._ensure_ams_mapping(db, 4, item)
            blocked = await scheduler._block_on_unmatched_filament(db, item)

        assert bool(unmatched) is held
        assert blocked is held
        if held:
            assert unmatched == ["PLA #FFFFFF (left nozzle)"]

    @pytest.mark.asyncio
    async def test_asked_once_per_pass_for_the_same_job_and_printer(self):
        scheduler = self._scheduler(0)
        item = self._item()
        scheduler._compute_ams_mapping_for_printer = AsyncMock(return_value=[-1])

        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock(fila_switch=None, ams_filament_backup=None)
            first = await scheduler._unmatched_on(AsyncMock(), item, 4)
            second = await scheduler._unmatched_on(AsyncMock(), item, 4)
            scheduler._unmatched_memo.clear()
            await scheduler._unmatched_on(AsyncMock(), item, 4)

        assert first == second == ["PLA #FFFFFF (left nozzle)"]
        assert scheduler._compute_ams_mapping_for_printer.await_count == 2


class TestTheMemoLastsOnePass:
    @pytest.mark.asyncio
    async def test_a_new_pass_asks_again(self, queue_db):
        """Spools change between passes; an answer from the last one is stale."""
        scheduler = PrintScheduler()
        scheduler._unmatched_memo[("stale",)] = list(NEEDS)

        await _run(queue_db, scheduler, short=set(), launched=MagicMock())

        assert ("stale",) not in scheduler._unmatched_memo
