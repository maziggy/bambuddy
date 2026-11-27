"""Three waits of the queue as the "To do" list shows them (#3615).

A filament no spool in the inventory has left (#3590) is marked on its "Load
filament" entry. A spool that holds too little (#3137) is marked short, with
its tray and the amounts, so it does not read as a filament that is missing.
A plate changer with no plate left (#3575) is an entry of its own on its
printer, next to "Confirm the plate is clear".
"""

import json
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services import plate_changer
from backend.app.services.print_scheduler import (
    _NO_STOCK_SUFFIX,
    PrintScheduler,
    plate_changer_reads_empty,
)
from backend.app.services.queue_todo import (
    KIND_LOAD_FILAMENT,
    KIND_PLATE_CHANGER_EMPTY,
    FilamentHold,
    ShortTray,
    TodoFilament,
    TodoJob,
    build_todo,
)
from backend.tests.unit import (
    test_plate_changer_capacity_3575 as changer,
    test_scheduler_loaded_filament_first_3587 as filament_first,
    test_scheduler_out_of_stock_3590 as stock,
)
from backend.tests.unit.test_plate_changer_capacity_3575 import _clean_marks  # noqa: F401 - fixture
from backend.tests.unit.test_scheduler_model_filament_short_3137 import (
    _add_item,
    _get_item,
    _run,
    queue_db,  # noqa: F401 - fixture
)

# Two harnesses with a fixture of the same name: each under its own here.
filament_ctx = filament_first.ctx
changer_ctx = changer.ctx

BROWN = TodoFilament("PLA", "#8B5A2B", "Earth Brown")
RED = TodoFilament("PLA", "#FF0000", "Red")
SHORT_PLA = TodoFilament("PLA", "", short=True, short_trays=(ShortTray(0, 0, 40.0),), required_grams=120.0)


def _short_facts(filament: TodoFilament) -> tuple:
    return (filament.short, filament.short_trays, filament.required_grams)


def _job(job_id: int, printer_ids: set[int], pinned: int | None, staged: bool = False) -> TodoJob:
    model = None if pinned is not None else "X1C"
    return TodoJob(job_id, f"Job {job_id}", frozenset(printer_ids), pinned, model and model.lower(), model, staged)


def _entries(todo: dict) -> list[dict]:
    return [entry for group in todo["groups"] for entry in group["entries"]]


class TestOutOfStockIsRecorded:
    """The mark the waiting text carries is on the recorded filament too (#3590)."""

    async def _find(self, ctx, entries, overrides=stock.FORCED, **kwargs):
        scheduler = stock._sched()
        holds: dict[int, list[TodoFilament]] = {}
        with (
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
            patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
            patch.object(scheduler, "_missing_force_color_pairs", stock._pairs()),
            stock._entries(entries),
        ):
            async with ctx.session_maker() as db:
                _, reason = await scheduler._find_idle_printer_for_model(
                    db, "X1C", set(), filament_overrides=overrides, holds_out=holds, **kwargs
                )
        return reason, holds

    @pytest.mark.asyncio
    async def test_a_model_job_s_forced_colour_with_no_stock(self, queue_db):
        reason, holds = await self._find(queue_db, (0, 0.0))

        assert reason.endswith(_NO_STOCK_SUFFIX)
        assert holds == {pid: [replace(BROWN, no_stock=True)] for pid in (1, 2, 3)}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("entries", [(2, 400.0), None])
    async def test_stock_left_or_unknown_is_not_marked(self, queue_db, entries):
        reason, holds = await self._find(queue_db, entries)

        assert _NO_STOCK_SUFFIX not in reason
        assert holds == {pid: [BROWN] for pid in (1, 2, 3)}

    @pytest.mark.asyncio
    async def test_a_forced_colour_next_to_a_preferred_one(self, queue_db):
        """The other wording of the matcher ("Waiting for filament: <printer> (needs ...)")."""
        preferred = {"slot_id": 2, "type": "PETG", "color": "#0000FF", "color_name": "Blue"}
        reason, holds = await self._find(queue_db, (0, 0.0), overrides=[*stock.FORCED, preferred])

        assert reason.startswith(f"Waiting for filament: X1C-1 (needs PLA (Earth Brown){_NO_STOCK_SUFFIX})")
        assert holds == {pid: [replace(BROWN, no_stock=True)] for pid in (1, 2, 3)}

    @pytest.mark.asyncio
    async def test_the_pass_records_it_for_a_model_job(self, queue_db):
        item_id = await _add_item(queue_db, filament_overrides=json.dumps(stock.FORCED))
        await stock._set_on(queue_db)
        scheduler = PrintScheduler()
        with (
            patch.object(PrintScheduler, "_missing_force_color_pairs", stock._pairs()),
            stock._entries((0, 0.0)),
            patch(stock.NOTIFY, AsyncMock()),
        ):
            await _run(queue_db, scheduler, short=set(), launched=MagicMock())

        assert _NO_STOCK_SUFFIX in (await _get_item(queue_db, item_id)).waiting_reason
        holds = scheduler.filament_holds_snapshot()[item_id]
        assert sorted(h.printer_id for h in holds) == [1, 2, 3]
        assert all(h.filaments == (replace(BROWN, no_stock=True),) for h in holds)

    @pytest.mark.asyncio
    async def test_a_job_staged_on_its_printer(self):
        scheduler = stock._pinned_scheduler()
        scheduler._pass_holds = {}
        item = stock._pinned_item(stock.FORCED)
        with (
            stock._entries((0, 0.0)),
            patch(stock.NOTIFY, AsyncMock()),
            patch("backend.app.services.print_scheduler.notification_service.on_queue_job_waiting", AsyncMock()),
        ):
            assert await scheduler._block_on_unmatched_filament(AsyncMock(), item) is True

        assert item.waiting_reason.endswith(_NO_STOCK_SUFFIX)
        (hold,) = scheduler._pass_holds[item.id]
        assert hold.filaments == (replace(BROWN, no_stock=True),)

    @pytest.mark.asyncio
    async def test_a_staged_job_with_stock_left_is_not_marked(self):
        scheduler = stock._pinned_scheduler()
        scheduler._pass_holds = {}
        item = stock._pinned_item(stock.FORCED)
        with (
            stock._entries((2, 300.0)),
            patch(stock.NOTIFY, AsyncMock()),
            patch("backend.app.services.print_scheduler.notification_service.on_queue_job_waiting", AsyncMock()),
        ):
            await scheduler._block_on_unmatched_filament(AsyncMock(), item)

        (hold,) = scheduler._pass_holds[item.id]
        assert hold.filaments == (BROWN,)

    @pytest.mark.asyncio
    async def test_the_mark_comes_back_after_a_restart(self, queue_db):
        """A staged job never reaches the writers again: its hold is rebuilt, mark included."""
        scheduler = stock._sched()
        scheduler._pass_holds = {}
        scheduler._unmatched_requirements = AsyncMock(return_value=list(stock.FORCED))
        scheduler._compute_ams_mapping_for_printer = AsyncMock(return_value=[-1])
        scheduler._build_loaded_filaments = MagicMock(return_value=[{"x": 1}])
        item = stock._pinned_item(stock.FORCED)
        item.filament_short = False
        item.target_model = None
        item.waiting_reason = f"Needs PLA Earth Brown{_NO_STOCK_SUFFIX}"
        with (
            stock._entries((0, 0.0)),
            patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock()),
        ):
            await scheduler._rebuild_filament_hold(AsyncMock(), item)

        (hold,) = scheduler._pass_holds[item.id]
        assert hold.filaments == (replace(BROWN, no_stock=True),)


class TestOutOfStockInTheList:
    def test_the_filament_of_the_entry_is_marked(self):
        holds = {1: [FilamentHold(1, 7, None, (replace(BROWN, no_stock=True), RED))]}
        todo = build_todo([_job(1, {7}, 7)], {7: "A"}, holds, [], {}, [])

        (entry,) = _entries(todo)
        assert entry["kind"] == KIND_LOAD_FILAMENT
        assert [(f["color_name"], f["no_stock"]) for f in entry["filaments"]] == [
            ("Earth Brown", True),
            ("Red", False),
        ]
        assert todo["count"] == 1


class TestShortSpoolIsRecorded:
    """A deficit is recorded as a short spool, with its tray and the amounts (#3137)."""

    @pytest.mark.asyncio
    async def test_a_model_job_staged_where_every_printer_is_short(self, queue_db):
        scheduler = PrintScheduler()
        item_id = await _add_item(queue_db)

        await _run(queue_db, scheduler, short={1, 2, 3}, launched=MagicMock())

        item = await _get_item(queue_db, item_id)
        assert (item.manual_start, item.filament_short, item.printer_id) == (True, True, 1)
        (hold,) = scheduler.filament_holds_snapshot()[item_id]
        assert (hold.printer_id, hold.filaments) == (1, (TodoFilament("PLA", ""),))
        assert _short_facts(hold.filaments[0]) == _short_facts(SHORT_PLA)

        # Staged now: the hold is carried, and still says short.
        await _run(queue_db, scheduler, short={1, 2, 3}, launched=MagicMock())
        (hold,) = scheduler.filament_holds_snapshot()[item_id]
        assert _short_facts(hold.filaments[0]) == _short_facts(SHORT_PLA)

    @pytest.mark.asyncio
    async def test_a_staged_job_s_hold_says_short_after_a_restart(self, queue_db):
        item_id = await _add_item(queue_db, printer_id=1, manual_start=True, filament_short=True)
        scheduler = PrintScheduler()

        await _run(queue_db, scheduler, short={1, 2, 3}, launched=MagicMock())

        (hold,) = scheduler.filament_holds_snapshot()[item_id]
        assert _short_facts(hold.filaments[0]) == _short_facts(SHORT_PLA)

    @pytest.mark.asyncio
    async def test_a_tray_that_is_not_known_is_left_out(self):
        scheduler = PrintScheduler()
        scheduler._get_filament_requirements = AsyncMock(return_value=None)
        row = MagicMock(slot_id=1, ams_id=None, tray_id=None, filament_type="PLA")
        row.required_grams, row.remaining_grams = 120.0, None

        (filament,) = await scheduler._deficit_todo_filaments(AsyncMock(), MagicMock(), [row])

        assert _short_facts(filament) == (True, (), 120.0)


class TestShortSpoolWithLoadedFilamentFirst:
    """With "Loaded filament first" (#3587) the job waits instead of being staged."""

    @pytest.mark.asyncio
    async def test_a_pinned_job_that_waits_for_a_refill(self, filament_ctx):
        await filament_first._mode(filament_ctx)
        item_id = await filament_first._add_item(filament_ctx, printer_id=1)
        scheduler = PrintScheduler()

        await filament_first._run(filament_ctx, scheduler, deficit_items={item_id})

        item = await filament_first._get(filament_ctx, item_id)
        assert item.manual_start is not True
        assert item.waiting_reason.startswith("Waiting for filament: PLA (40 g left, needs 120 g)")
        (hold,) = scheduler.filament_holds_snapshot()[item_id]
        assert (hold.printer_id, hold.filaments) == (1, (TodoFilament("PLA", ""),))
        assert _short_facts(hold.filaments[0]) == _short_facts(SHORT_PLA)

    @pytest.mark.asyncio
    async def test_a_model_job_whose_printers_are_all_short(self, filament_ctx):
        await filament_first._mode(filament_ctx)
        item_id = await filament_first._add_item(filament_ctx)
        scheduler = PrintScheduler()
        mapping = [patch.object(scheduler, "_compute_ams_mapping_for_printer", AsyncMock(return_value=[0]))]

        await filament_first._run(
            filament_ctx, scheduler, short={1, 2, 3}, deficit_items={item_id}, extra_patches=mapping
        )

        assert "needs more filament" in (await filament_first._get(filament_ctx, item_id)).waiting_reason
        holds = scheduler.filament_holds_snapshot()[item_id]
        assert sorted(h.printer_id for h in holds) == [1, 2, 3]
        assert all(_short_facts(h.filaments[0]) == _short_facts(SHORT_PLA) for h in holds)


class TestShortSpoolInTheList:
    def test_the_entry_says_short_with_the_tray_and_the_amounts(self):
        holds = {1: [FilamentHold(1, 7, None, (SHORT_PLA,))]}
        todo = build_todo([_job(1, {7}, 7)], {7: "A"}, holds, [], {}, [])

        (entry,) = _entries(todo)
        assert entry["kind"] == KIND_LOAD_FILAMENT
        assert entry["filaments"] == [
            {
                "type": "PLA",
                "color": "",
                "color_name": "",
                "no_stock": False,
                "nozzle": None,
                "short": True,
                "short_trays": [{"ams_id": 0, "tray_id": 0, "remaining_grams": 40.0}],
                "required_grams": 120.0,
            }
        ]
        assert todo["count"] == 1

    def test_a_missing_filament_is_not_short(self):
        todo = build_todo([_job(1, {7}, 7)], {7: "A"}, {1: [FilamentHold(1, 7, None, (RED,))]}, [], {}, [])

        (filament,) = _entries(todo)[0]["filaments"]
        assert (filament["short"], filament["short_trays"], filament["required_grams"]) == (False, [], None)

    def test_a_short_spool_and_the_same_filament_missing_are_two_entries(self):
        missing = TodoFilament("PLA", "")
        holds = {1: [FilamentHold(1, 7, None, (SHORT_PLA,))], 2: [FilamentHold(2, 7, None, (missing,))]}
        todo = build_todo([_job(1, {7}, 7), _job(2, {7}, 7)], {7: "A"}, holds, [], {}, [])

        assert [(e["jobs"][0]["id"], e["filaments"][0]["short"]) for e in _entries(todo)] == [(1, True), (2, False)]
        assert todo["count"] == 2

    def test_two_jobs_on_one_short_spool_share_an_entry_that_names_the_larger_need(self):
        more = replace(SHORT_PLA, required_grams=300.0)
        holds = {1: [FilamentHold(1, 7, None, (SHORT_PLA,))], 2: [FilamentHold(2, 7, None, (more,))]}
        todo = build_todo([_job(1, {7}, 7), _job(2, {7}, 7)], {7: "A"}, holds, [], {}, [])

        (entry,) = _entries(todo)
        assert [j["id"] for j in entry["jobs"]] == [1, 2]
        assert entry["filaments"][0]["required_grams"] == 300.0
        assert todo["count"] == 1

    def test_a_model_entry_names_the_tray_for_a_single_printer_only(self):
        printers = {7: "A", 8: "B"}
        other = replace(SHORT_PLA, short_trays=(ShortTray(1, 2, 10.0),))
        both = {1: [FilamentHold(1, 7, "X1C", (SHORT_PLA,)), FilamentHold(1, 8, "X1C", (other,))]}
        one = {1: [FilamentHold(1, 7, "X1C", (SHORT_PLA,))]}

        (entry,) = _entries(build_todo([_job(1, {7, 8}, None)], printers, both, [], {}, []))
        assert [p["id"] for p in entry["printers"]] == [7, 8]
        assert (entry["filaments"][0]["short"], entry["filaments"][0]["short_trays"]) == (True, [])
        assert entry["filaments"][0]["required_grams"] == 120.0

        (entry,) = _entries(build_todo([_job(1, {7, 8}, None)], printers, one, [], {}, []))
        assert entry["filaments"][0]["short_trays"] == [{"ams_id": 0, "tray_id": 0, "remaining_grams": 40.0}]

    def test_a_staged_job_keeps_its_start_next_to_the_short_spool(self):
        job = _job(1, {7}, 7, staged=True)
        job.can_start = True
        todo = build_todo([job], {7: "A"}, {1: [FilamentHold(1, 7, None, (SHORT_PLA,))]}, [], {}, [])

        load, start = _entries(todo)
        assert (load["kind"], load["jobs"][0]["can_start"]) == ("load_filament", False)
        assert (start["kind"], start["jobs"][0]["can_start"]) == ("start_staged", True)
        assert todo["count"] == 2


class TestShortSpoolsKeepTheirTrays:
    """Equality of a ``TodoFilament`` ignores the short fields; the list must not."""

    A2 = replace(SHORT_PLA, short_trays=(ShortTray(0, 1, 12.0),), required_grams=30.0)
    A3 = replace(SHORT_PLA, short_trays=(ShortTray(0, 2, 20.0),), required_grams=50.0)

    def test_two_jobs_short_in_different_trays_are_two_entries(self):
        holds = {1: [FilamentHold(1, 7, None, (self.A2,))], 2: [FilamentHold(2, 7, None, (self.A3,))]}
        todo = build_todo([_job(1, {7}, 7), _job(2, {7}, 7)], {7: "A"}, holds, [], {}, [])

        named = [
            (e["jobs"][0]["id"], e["filaments"][0]["short_trays"][0]["tray_id"], e["filaments"][0]["required_grams"])
            for e in _entries(todo)
        ]
        assert named == [(1, 1, 30.0), (2, 2, 50.0)]

    def test_two_model_jobs_short_in_different_trays_of_one_printer_are_two_entries(self):
        holds = {1: [FilamentHold(1, 7, "X1C", (self.A2,))], 2: [FilamentHold(2, 7, "X1C", (self.A3,))]}
        todo = build_todo([_job(1, {7}, None), _job(2, {7}, None)], {7: "A"}, holds, [], {}, [])

        assert [e["filaments"][0]["short_trays"][0]["tray_id"] for e in _entries(todo)] == [1, 2]

    def test_a_carried_hold_keeps_both_short_trays_and_a_missing_filament(self):
        scheduler = PrintScheduler()
        missing = TodoFilament("PLA", "")
        scheduler._filament_holds = {1: [FilamentHold(1, 7, None, (self.A2, self.A3, missing))]}
        scheduler._pass_holds = {}

        assert scheduler._carry_filament_hold(MagicMock(id=1, printer_id=7, target_model=None))
        (hold,) = scheduler._pass_holds[1]
        assert [(f.short, f.short_trays) for f in hold.filaments] == [
            (True, self.A2.short_trays),
            (True, self.A3.short_trays),
            (False, ()),
        ]


class TestOutOfStockHasItsOwnEntry:
    def test_a_filament_to_buy_and_the_same_one_to_load_are_two_entries(self):
        none_left = replace(RED, no_stock=True)
        for first, second in ((RED, none_left), (none_left, RED)):
            holds = {1: [FilamentHold(1, 7, None, (first,))], 2: [FilamentHold(2, 7, None, (second,))]}
            todo = build_todo([_job(1, {7}, 7), _job(2, {7}, 7)], {7: "A"}, holds, [], {}, [])

            marks = [(e["jobs"][0]["id"], e["filaments"][0]["no_stock"]) for e in _entries(todo)]
            assert marks == [(1, first.no_stock), (2, second.no_stock)]


class TestThePrinterModelNamesTheSlots:
    def test_a_printer_group_carries_its_model_and_a_model_group_its_target(self):
        holds = {1: [FilamentHold(1, 7, None, (SHORT_PLA,))], 2: [FilamentHold(2, 7, "X1C", (SHORT_PLA,))]}
        todo = build_todo([_job(1, {7}, 7), _job(2, {7}, None)], {7: "A"}, holds, [], {}, [], None, {7: "H2C"})

        assert [(g["printer_id"], g["printer_model"]) for g in todo["groups"]] == [(7, "H2C"), (None, "X1C")]


class TestPlateChangerEmptyInTheScheduler:
    """An empty plate changer makes the job wait without a filament hold (#3575)."""

    @pytest.mark.asyncio
    async def test_a_pinned_job_waits_and_records_no_filament(self, changer_ctx):
        await changer._add_printer(changer_ctx, 1, capacity=5, remaining=0)
        item_id = await changer._add_item(changer_ctx, printer_id=1)
        scheduler = PrintScheduler()

        launched = await changer._run(changer_ctx, scheduler)

        launched.assert_not_called()
        assert (await changer._item(changer_ctx, item_id)).waiting_reason == "Plate changer empty: X1C-01"
        assert scheduler.filament_holds_snapshot() == {}
        assert set(scheduler._plate_changer_empty) == {1}

    @pytest.mark.asyncio
    async def test_a_model_job_whose_printers_are_all_empty(self, changer_ctx):
        await changer._add_printer(changer_ctx, 1, capacity=5, remaining=0)
        await changer._add_printer(changer_ctx, 2, capacity=3, remaining=None)
        item_id = await changer._add_item(changer_ctx, target_model="X1C")
        scheduler = PrintScheduler()

        launched = await changer._run(changer_ctx, scheduler)

        launched.assert_not_called()
        row = await changer._item(changer_ctx, item_id)
        assert row.waiting_reason == "Plate changer empty: X1C-01, X1C-02"
        assert row.printer_id is None
        assert scheduler.filament_holds_snapshot() == {}

    @pytest.mark.asyncio
    async def test_the_filament_another_printer_lacks_is_still_recorded(self, changer_ctx):
        await changer._add_printer(changer_ctx, 1, capacity=5, remaining=0)
        await changer._add_printer(changer_ctx, 2)
        item_id = await changer._add_item(changer_ctx, target_model="X1C")
        async with changer_ctx.session_maker() as db:
            item = await db.get(changer.PrintQueueItem, item_id)
            item.required_filament_types = json.dumps(["PLA"])
            await db.commit()
        scheduler = PrintScheduler()

        with patch.object(scheduler, "_get_missing_filament_types", MagicMock(return_value=["PLA"])):
            await changer._run(changer_ctx, scheduler)

        reason = (await changer._item(changer_ctx, item_id)).waiting_reason
        assert reason == "Waiting for filament: X1C-02 (needs PLA) | Plate changer empty: X1C-01"
        assert scheduler.filament_holds_snapshot()[item_id] == [
            FilamentHold(item_id, 2, "X1C", (TodoFilament("PLA", ""),))
        ]

    def test_the_list_reads_empty_as_the_queue_pass_does(self):
        assert plate_changer_reads_empty(1, 0) is True
        assert plate_changer_reads_empty(1, None) is True
        assert plate_changer_reads_empty(1, 2) is False
        plate_changer.mark_count_unknown(1)
        assert plate_changer_reads_empty(1, 2) is True


class TestPlateChangerEmptyInTheList:
    def test_one_entry_on_the_printer_with_every_job_that_waits_for_it(self):
        jobs = [_job(1, {7}, 7), _job(2, {7, 8}, None), _job(3, {8}, 8)]
        todo = build_todo(jobs, {7: "A", 8: "B"}, {}, [], {}, [], {7: 5})

        (group,) = todo["groups"]
        assert (group["printer_id"], group["printer_name"]) == (7, "A")
        (entry,) = group["entries"]
        assert entry["kind"] == KIND_PLATE_CHANGER_EMPTY
        assert [j["id"] for j in entry["jobs"]] == [1, 2]
        assert entry["plate_capacity"] == 5
        assert todo["count"] == 1

    def test_a_model_job_is_listed_on_every_empty_printer_of_its_model(self):
        todo = build_todo([_job(1, {7, 8}, None)], {7: "A", 8: "B"}, {}, [], {}, [], {7: 5, 8: 3})

        assert [
            (g["printer_id"], g["entries"][0]["kind"], g["entries"][0]["plate_capacity"]) for g in todo["groups"]
        ] == [
            (7, KIND_PLATE_CHANGER_EMPTY, 5),
            (8, KIND_PLATE_CHANGER_EMPTY, 3),
        ]
        assert all([j["id"] for j in g["entries"][0]["jobs"]] == [1] for g in todo["groups"])
        assert todo["count"] == 2

    def test_no_entry_without_a_waiting_job_or_for_a_hidden_printer(self):
        assert build_todo([], {7: "A"}, {}, [], {}, [], {7: 5}) == {"count": 0, "groups": []}
        assert build_todo([_job(1, {7}, 7)], {}, {}, [], {}, [], {7: 5}) == {"count": 0, "groups": []}

    def test_it_comes_after_the_plate_to_clear_and_before_the_filament(self):
        job = _job(1, {7}, 7, staged=True)
        holds = {1: [FilamentHold(1, 7, None, (RED,))]}
        todo = build_todo([job], {7: "A"}, holds, [7], {7: "Door"}, [7], {7: 5})

        assert [e["kind"] for e in _entries(todo)] == [
            "plate_clear",
            "plate_changer_empty",
            "sensor_interlock",
            "power_on_failed",
            "load_filament",
            "start_staged",
        ]
        assert todo["count"] == 6

    def test_other_entries_carry_no_capacity(self):
        todo = build_todo([_job(1, {7}, 7)], {7: "A"}, {}, [7], {}, [])

        assert _entries(todo)[0]["plate_capacity"] is None
