"""The scheduler records what each held job waits for in filament (#3615).

The records sit next to the existing waiting texts (which are unchanged and
covered by their own tests) and feed the queue's "To do" list.
"""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.print_scheduler import _UNMATCHED_HOLD_PREFIX, PrintScheduler, _todo_for_types
from backend.app.services.queue_todo import (
    FilamentHold,
    TodoFilament,
    TodoJob,
    build_todo,
    todo_filament_from_requirement,
)
from backend.tests.unit.test_ams_mapping_per_printer_2799 import (
    P2S_5_TRAYS,
    _item,
    _scheduler,
)
from backend.tests.unit.test_scheduler_model_filament_short_3137 import (
    _add_item,
    _run,
    queue_db,  # noqa: F401 - fixture
)

RED = TodoFilament("PLA", "#FF0000", "Red")


class TestPinnedUnmatchedHold:
    @pytest.mark.asyncio
    async def test_records_the_unmatched_requirements(self):
        scheduler = _scheduler(P2S_5_TRAYS)
        scheduler._get_job_name = AsyncMock(return_value="body1")
        scheduler._get_printer = AsyncMock(return_value=MagicMock(model="P2S"))
        scheduler._pass_holds = {}
        item = _item(ams_mapping=json.dumps([0, -1, -1]), target_model=None)

        assert await scheduler._block_on_unmatched_filament(AsyncMock(), item) is True

        assert scheduler._pass_holds[item.id] == [
            FilamentHold(item.id, 8, None, (TodoFilament("PETG", "#2850E0", ""),))
        ]

    @pytest.mark.asyncio
    async def test_a_released_model_job_is_no_printers(self):
        scheduler = _scheduler(P2S_5_TRAYS)
        scheduler._get_job_name = AsyncMock(return_value="body1")
        scheduler._get_printer = AsyncMock(return_value=MagicMock(model="P2S"))
        scheduler._pass_holds = {}
        item = _item(ams_mapping=json.dumps([0, -1, -1]), target_model="P2S")

        await scheduler._block_on_unmatched_filament(AsyncMock(), item, release_assignment=True)

        (hold,) = scheduler._pass_holds[item.id]
        assert (hold.printer_id, hold.target_model) == (None, "P2S")

    @pytest.mark.asyncio
    async def test_nothing_is_recorded_outside_a_pass(self):
        scheduler = _scheduler(P2S_5_TRAYS)
        scheduler._get_job_name = AsyncMock(return_value="body1")
        scheduler._get_printer = AsyncMock(return_value=MagicMock(model="P2S"))
        item = _item(ams_mapping=json.dumps([0, -1, -1]))

        await scheduler._block_on_unmatched_filament(AsyncMock(), item)

        assert scheduler.filament_holds_snapshot() == {}


class TestNozzle:
    """A dual-nozzle file names the nozzle each filament goes on; the record keeps it (#3615)."""

    def test_the_requirement_s_nozzle_is_carried(self):
        req = {"type": "petg", "color": "#000000", "nozzle_id": 1}
        assert todo_filament_from_requirement(req) == TodoFilament("PETG", "#000000", "", nozzle=1)
        assert todo_filament_from_requirement({**req, "nozzle_id": 0}).nozzle == 0
        # Single-nozzle files name none, and anything else is no nozzle.
        for other in ({}, {"nozzle_id": None}, {"nozzle_id": 2}, {"nozzle_id": "1"}, {"nozzle_id": True}):
            assert todo_filament_from_requirement({"type": "PETG", **other}).nozzle is None

    @pytest.mark.asyncio
    async def test_the_same_filament_on_both_nozzles_is_two_distinct_filaments(self):
        requirements = [
            {"slot_id": 1, "type": "PETG", "color": "#000000", "nozzle_id": 0},
            {"slot_id": 2, "type": "PETG", "color": "#000000", "nozzle_id": 1},
        ]
        scheduler = _scheduler(P2S_5_TRAYS, requirements)
        scheduler._get_job_name = AsyncMock(return_value="body1")
        scheduler._get_printer = AsyncMock(return_value=MagicMock(model="H2D"))
        scheduler._pass_holds = {}
        item = _item(ams_mapping=json.dumps([-1, -1]), target_model=None)

        assert await scheduler._block_on_unmatched_filament(AsyncMock(), item) is True

        (hold,) = scheduler._pass_holds[item.id]
        assert hold.filaments == (
            TodoFilament("PETG", "#000000", "", nozzle=0),
            TodoFilament("PETG", "#000000", "", nozzle=1),
        )
        assert "PETG #000000 (right nozzle), PETG #000000 (left nozzle)" in item.waiting_reason

    def test_the_list_keeps_nozzles_apart(self):
        left = TodoFilament("PETG", "#000000", "", nozzle=1)
        right = TodoFilament("PETG", "#000000", "", nozzle=0)
        jobs = [TodoJob(i, f"J{i}", frozenset({8}), 8, None, None, False) for i in (1, 2, 3)]
        holds = {
            1: [FilamentHold(1, 8, None, (left, right))],
            2: [FilamentHold(2, 8, None, (left,))],
            3: [FilamentHold(3, 8, None, (left,))],
        }

        (group,) = build_todo(jobs, {8: "H2D"}, holds, [], {}, [])["groups"]

        # Jobs 2 and 3 want the same nozzle and share an entry; job 1 wants both.
        assert [
            ([j["id"] for j in e["jobs"]], [(f["type"], f["nozzle"]) for f in e["filaments"]]) for e in group["entries"]
        ] == [([1], [("PETG", 0), ("PETG", 1)]), ([2, 3], [("PETG", 1)])]

    def test_a_filament_on_the_other_nozzle_is_another_entry(self):
        jobs = [TodoJob(i, f"J{i}", frozenset({8}), 8, None, None, False) for i in (1, 2)]
        holds = {
            1: [FilamentHold(1, 8, None, (TodoFilament("PETG", "#000000", "", nozzle=0),))],
            2: [FilamentHold(2, 8, None, (TodoFilament("PETG", "#000000", "", nozzle=1),))],
        }
        assert build_todo(jobs, {8: "H2D"}, holds, [], {}, [])["count"] == 2

    def test_a_printer_with_nothing_to_do_has_no_group(self):
        jobs = [
            TodoJob(1, "Idle", frozenset({7}), 7, None, None, False),
            TodoJob(2, "Waits", frozenset({8}), 8, None, None, False),
        ]
        holds = {2: [FilamentHold(2, 8, None, (TodoFilament("PLA", "#FF0000", ""),))]}

        todo = build_todo(jobs, {7: "Empty", 8: "Busy"}, holds, [], {}, [])

        assert [g["printer_name"] for g in todo["groups"]] == ["Busy"]
        assert todo["count"] == 1


class TestMatcherHolds:
    async def _find(self, ctx, scheduler, *, status=None, **kwargs):
        holds: dict[int, list[TodoFilament]] = {}
        with (
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
            patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=status)),
            patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
        ):
            async with ctx.session_maker() as db:
                result = await scheduler._find_idle_printer_for_model(db, "X1C", set(), holds_out=holds, **kwargs)
        return result, holds

    @pytest.mark.asyncio
    async def test_missing_types_are_carried_per_printer(self, queue_db):
        scheduler = PrintScheduler()
        overrides = [{"slot_id": 1, "type": "PLA", "color": "#ff0000", "color_name": "Red"}]
        with patch.object(scheduler, "_get_missing_filament_types", MagicMock(return_value=["PLA"])):
            (printer_id, reason), holds = await self._find(
                queue_db, scheduler, required_filament_types=["PLA"], filament_overrides=overrides
            )
        assert printer_id is None
        assert reason.startswith("Waiting for filament: X1C-1 (needs PLA)")
        # No force-colour override: the text names no colour, so neither does the record.
        assert holds == {pid: [TodoFilament("PLA", "")] for pid in (1, 2, 3)}

    @pytest.mark.asyncio
    async def test_missing_types_take_their_colour_from_force_overrides(self, queue_db):
        scheduler = PrintScheduler()
        overrides = [{"slot_id": 1, "type": "PLA", "color": "#ff0000", "color_name": "Red", "force_color_match": True}]
        with patch.object(scheduler, "_get_missing_filament_types", MagicMock(return_value=["PLA"])):
            (_, reason), holds = await self._find(
                queue_db, scheduler, required_filament_types=["PLA"], filament_overrides=overrides
            )
        assert reason == "No matching material/color. Waiting on PLA (Red)"
        assert holds == {1: [RED], 2: [RED], 3: [RED]}

    @pytest.mark.asyncio
    async def test_force_colour_wait_names_the_colour(self, queue_db):
        scheduler = PrintScheduler()
        overrides = [{"slot_id": 1, "type": "PLA", "color": "#FF0000", "color_name": "Red", "force_color_match": True}]
        status = MagicMock(raw_data={"ams": [], "vt_tray": []})
        (printer_id, reason), holds = await self._find(queue_db, scheduler, status=status, filament_overrides=overrides)
        assert printer_id is None
        assert reason == "No matching material/color. Waiting on PLA (Red)"
        assert holds == {1: [RED], 2: [RED], 3: [RED]}

    @pytest.mark.asyncio
    async def test_unmatched_filaments_use_the_structured_form(self, queue_db):
        scheduler = PrintScheduler()
        wanted = [TodoFilament("PLA", "#FFFFFF", "")]

        async def unmatched_on(pid):
            return ["PLA #FFFFFF (left nozzle)"] if pid in (1, 2) else []

        (printer_id, _), holds = await self._find(
            queue_db, scheduler, unmatched_on=unmatched_on, unmatched_todo=lambda pid: wanted
        )
        assert printer_id == 3  # a printer that can run it is found: no wait, nothing recorded for it
        assert 3 not in holds

    @pytest.mark.asyncio
    async def test_a_match_leaves_no_holds(self, queue_db):
        (printer_id, reason), holds = await self._find(queue_db, PrintScheduler())
        assert (printer_id, reason) == (1, None)
        assert holds == {}


class TestPassRecording:
    @pytest.mark.asyncio
    async def test_model_job_wait_is_recorded_and_then_swept(self, queue_db):
        scheduler = PrintScheduler()
        item_id = await _add_item(queue_db, required_filament_types=json.dumps(["PLA"]))
        with patch.object(scheduler, "_get_missing_filament_types", MagicMock(return_value=["PLA"])):
            await _run(queue_db, scheduler, short=set(), launched=MagicMock())

        snapshot = scheduler.filament_holds_snapshot()
        assert sorted(h.printer_id for h in snapshot[item_id]) == [1, 2, 3]
        assert {h.target_model for h in snapshot[item_id]} == {"X1C"}
        assert all(h.filaments == (TodoFilament("PLA", ""),) for h in snapshot[item_id])

        # The snapshot is a copy.
        snapshot.clear()
        assert item_id in scheduler.filament_holds_snapshot()

        # The job leaves the queue: the next pass drops its holds.
        async with queue_db.session_maker() as db:
            row = await db.get(PrintQueueItem, item_id)
            row.status = "cancelled"
            await db.commit()
        with patch.object(scheduler, "_get_missing_filament_types", MagicMock(return_value=["PLA"])):
            await _run(queue_db, scheduler, short=set(), launched=MagicMock())
        assert scheduler.filament_holds_snapshot() == {}

    @pytest.mark.asyncio
    async def test_deficit_hold_is_recorded_and_kept_while_staged(self, queue_db):
        scheduler = PrintScheduler()
        item_id = await _add_item(queue_db, printer_id=1)
        await _run(queue_db, scheduler, short={1, 2, 3}, launched=MagicMock())

        (hold,) = scheduler.filament_holds_snapshot()[item_id]
        assert (hold.printer_id, hold.target_model) == (1, "X1C")
        assert hold.filaments == (TodoFilament("PLA", ""),)

        # Staged now: the item never reaches the writers again, but its hold stays.
        await _run(queue_db, scheduler, short={1, 2, 3}, launched=MagicMock())
        assert item_id in scheduler.filament_holds_snapshot()

    @pytest.mark.asyncio
    async def test_a_failed_pass_keeps_the_previous_snapshot_merged_with_what_it_recorded(self, queue_db):
        scheduler = PrintScheduler()
        scheduler._filament_holds = {
            7: [FilamentHold(7, 1, None, (RED,))],
            8: [FilamentHold(8, 1, None, (RED,))],
        }
        fresh = FilamentHold(8, 2, None, (TodoFilament("PETG", "#0000FF", "Blue"),))

        async def stops_early():
            scheduler._record_hold(8, 2, None, list(fresh.filaments))
            raise RuntimeError("boom")

        with (
            patch.object(scheduler, "_check_queue_pass", AsyncMock(side_effect=stops_early)),
            pytest.raises(RuntimeError),
        ):
            await scheduler.check_queue()

        snapshot = scheduler.filament_holds_snapshot()
        assert snapshot == {7: [FilamentHold(7, 1, None, (RED,))], 8: [fresh]}
        assert scheduler._pass_holds is None


class TestRebuildAfterRestart:
    """A fresh scheduler has no records; a staged job's are rebuilt, its text untouched (#3615)."""

    @pytest.mark.asyncio
    async def test_pinned_needs_hold_is_rebuilt_without_touching_the_text(self, queue_db):
        scheduler = PrintScheduler()
        text = f"{_UNMATCHED_HOLD_PREFIX}PLA #FF0000"
        item_id = await _add_item(queue_db, printer_id=1, manual_start=True, waiting_reason=text)
        with (
            patch.object(scheduler, "_build_loaded_filaments", MagicMock(return_value=list(P2S_5_TRAYS))),
            patch.object(scheduler, "_compute_ams_mapping_for_printer", AsyncMock(return_value=[-1])),
            patch.object(
                scheduler,
                "_unmatched_requirements",
                AsyncMock(return_value=[{"type": "pla", "color": "#ff0000", "color_name": "Red"}]),
            ),
        ):
            await _run(queue_db, scheduler, short=set(), launched=MagicMock(), status=MagicMock())

        assert scheduler.filament_holds_snapshot()[item_id] == [FilamentHold(item_id, 1, "X1C", (RED,))]
        async with queue_db.session_maker() as db:
            assert (await db.get(PrintQueueItem, item_id)).waiting_reason == text

    @pytest.mark.asyncio
    async def test_filament_short_hold_is_rebuilt(self, queue_db):
        scheduler = PrintScheduler()
        item_id = await _add_item(queue_db, printer_id=1, target_model=None, manual_start=True, filament_short=True)
        await _run(queue_db, scheduler, short={1}, launched=MagicMock())

        (hold,) = scheduler.filament_holds_snapshot()[item_id]
        assert (hold.printer_id, hold.filaments) == (1, (TodoFilament("PLA", ""),))

    @pytest.mark.asyncio
    async def test_a_released_model_job_is_rebuilt_under_no_printer(self, queue_db):
        scheduler = PrintScheduler()
        text = f"{_UNMATCHED_HOLD_PREFIX}PLA #00FF00"
        item_id = await _add_item(queue_db, printer_id=None, manual_start=True, waiting_reason=text)
        with patch.object(
            scheduler,
            "_get_filament_requirements",
            AsyncMock(return_value=[{"slot_id": 1, "type": "PLA", "color": "#00FF00"}]),
        ):
            await _run(queue_db, scheduler, short=set(), launched=MagicMock())

        (hold,) = scheduler.filament_holds_snapshot()[item_id]
        assert (hold.printer_id, hold.target_model) == (None, "X1C")
        assert hold.filaments == (TodoFilament("PLA", "#00FF00", ""),)

    @pytest.mark.asyncio
    async def test_a_carried_hold_is_not_rebuilt(self, queue_db):
        scheduler = PrintScheduler()
        text = f"{_UNMATCHED_HOLD_PREFIX}PLA #FF0000"
        item_id = await _add_item(queue_db, printer_id=1, manual_start=True, waiting_reason=text)
        scheduler._filament_holds = {item_id: [FilamentHold(item_id, 1, "X1C", (RED,))]}
        with patch.object(scheduler, "_unmatched_requirements", AsyncMock(side_effect=AssertionError)):
            await _run(queue_db, scheduler, short=set(), launched=MagicMock())
        assert scheduler.filament_holds_snapshot()[item_id] == [FilamentHold(item_id, 1, "X1C", (RED,))]


class TestCarryRekey:
    @pytest.mark.asyncio
    async def test_a_moved_job_carries_its_hold_to_its_current_printer(self, queue_db):
        scheduler = PrintScheduler()
        item_id = await _add_item(queue_db, printer_id=2, target_model=None, manual_start=True, filament_short=True)
        scheduler._filament_holds = {item_id: [FilamentHold(item_id, 1, "X1C", (RED,))]}
        await _run(queue_db, scheduler, short=set(), launched=MagicMock())
        assert scheduler.filament_holds_snapshot()[item_id] == [FilamentHold(item_id, 2, None, (RED,))]

    @pytest.mark.asyncio
    async def test_a_job_moved_to_its_model_carries_it_to_the_model_group(self, queue_db):
        scheduler = PrintScheduler()
        item_id = await _add_item(queue_db, printer_id=None, manual_start=True, filament_short=True)
        scheduler._filament_holds = {item_id: [FilamentHold(item_id, 1, "X1C", (RED,))]}
        with patch.object(scheduler, "_release_short_model_hold", AsyncMock(return_value=False)):
            await _run(queue_db, scheduler, short=set(), launched=MagicMock())
        assert scheduler.filament_holds_snapshot()[item_id] == [FilamentHold(item_id, None, "X1C", (RED,))]


class TestTodoForTypes:
    def test_colour_comes_from_force_overrides_only_last_one_winning(self):
        force = [
            {"type": "pla", "color": "#111111", "color_name": "Old", "force_color_match": True},
            {"type": "PLA", "color": "#222222", "color_name": "New", "force_color_match": True},
        ]
        assert _todo_for_types(["PLA"], force) == [TodoFilament("PLA", "#222222", "New")]
        # Exact (upper-case) type, as the text matches it: "Pla" is looked up as "PLA".
        assert _todo_for_types(["Pla"], force) == [TodoFilament("PLA", "#222222", "New")]
        assert _todo_for_types(["PETG"], force) == [TodoFilament("PETG", "")]
        assert _todo_for_types(["PLA"], None) == [TodoFilament("PLA", "")]


class TestPowerOnFailed:
    def test_only_printers_still_cooling_off(self):
        scheduler = PrintScheduler()
        scheduler._wake_failures = {1: time.monotonic() + 100, 2: time.monotonic() - 1}
        assert scheduler.power_on_failed_printers() == {1}

    def test_a_printer_that_has_connected_is_dropped_and_its_cool_off_kept(self):
        scheduler = PrintScheduler()
        scheduler._wake_failures = {1: time.monotonic() + 100, 2: time.monotonic() + 100}
        scheduler._power_failed_pinned = {3, 4}
        with patch(
            "backend.app.services.print_scheduler.printer_manager.is_connected",
            MagicMock(side_effect=lambda pid: pid in (2, 4)),
        ):
            assert scheduler.power_on_failed_printers() == {1, 3}
        # The cool-off itself (#2786) is untouched.
        assert set(scheduler._wake_failures) == {1, 2}


class TestRebuildAttemptsAreRemembered:
    """A rebuild that finds nothing is not repeated every pass (#3615)."""

    @staticmethod
    def _staged(printer_id=8):
        return MagicMock(
            id=41,
            printer_id=printer_id,
            target_model=None,
            filament_short=None,
            filament_overrides=None,
            waiting_reason=_UNMATCHED_HOLD_PREFIX + " PETG",
        )

    @pytest.mark.asyncio
    async def test_three_passes_make_one_attempt(self):
        scheduler = _scheduler(P2S_5_TRAYS)
        scheduler._pass_holds = {}
        scheduler._compute_ams_mapping_for_printer = AsyncMock(return_value=None)
        scheduler._unmatched_requirements = AsyncMock(return_value=[])
        item = self._staged()
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            for _ in range(3):
                await scheduler._rebuild_filament_hold(AsyncMock(), item)
        assert scheduler._compute_ams_mapping_for_printer.await_count == 1

        # The inputs changing is a new question.
        item.waiting_reason = _UNMATCHED_HOLD_PREFIX + " ABS"
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            await scheduler._rebuild_filament_hold(AsyncMock(), item)
        assert scheduler._compute_ams_mapping_for_printer.await_count == 2

    @pytest.mark.asyncio
    async def test_keys_of_jobs_a_pass_did_not_ask_about_are_dropped(self):
        scheduler = PrintScheduler()
        scheduler._rebuild_tried = {(41, "x", None, None, 8), (42, "y", None, None, 8)}

        async def one_pass():
            scheduler._rebuild_seen.add((42, "y", None, None, 8))
            return False

        scheduler._check_queue_pass = one_pass
        await scheduler.check_queue()
        assert scheduler._rebuild_tried == {(42, "y", None, None, 8)}

    @pytest.mark.asyncio
    async def test_an_offline_printer_is_not_mapped_and_asked_again_later(self, caplog):
        scheduler = _scheduler(P2S_5_TRAYS)
        scheduler._pass_holds = {}
        scheduler._compute_ams_mapping_for_printer = AsyncMock(return_value=None)
        scheduler._unmatched_requirements = AsyncMock(return_value=[])
        item = self._staged()
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = None
            with caplog.at_level("WARNING"):
                await scheduler._rebuild_filament_hold(AsyncMock(), item)
            assert scheduler._compute_ams_mapping_for_printer.await_count == 0
            assert not [r for r in caplog.records if r.levelname == "WARNING"]
            pm.get_status.return_value = MagicMock()
            await scheduler._rebuild_filament_hold(AsyncMock(), item)
        assert scheduler._compute_ams_mapping_for_printer.await_count == 1

    @pytest.mark.asyncio
    async def test_a_printer_that_has_not_reported_its_trays_is_asked_again(self):
        """A client and its status exist from connect on, also for a printer that is off."""
        scheduler = PrintScheduler()
        scheduler._pass_holds = {}
        scheduler._get_filament_requirements = AsyncMock(
            return_value=[{"slot_id": 1, "type": "PETG", "color": "#2850E0"}]
        )
        scheduler._get_bool_setting = AsyncMock(return_value=False)
        item = self._staged()
        item.created_by_id = None
        state = PrinterState()  # as connect_printer stores it: no AMS report yet
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = state
            for _ in range(2):
                await scheduler._rebuild_filament_hold(AsyncMock(), item)
            assert scheduler._pass_holds == {}
            assert scheduler._rebuild_tried == set()

            # The trays arrive: the next pass records what is missing, once.
            state.raw_data = {"ams": [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA", "tray_color": "FF0000FF"}]}]}
            await scheduler._rebuild_filament_hold(AsyncMock(), item)
        assert scheduler._pass_holds == {41: [FilamentHold(41, 8, None, (TodoFilament("PETG", "#2850E0", ""),))]}
        assert len(scheduler._rebuild_tried) == 1
