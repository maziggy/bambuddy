"""A job waiting for a forced colour says when the inventory has none left (#3590).

The waiting reason gains " \u2013 none left in inventory" on each forced filament whose
stock is known and zero, and a notification goes out once, when the reason first
gains it. Unknown or non-zero stock, and jobs with no forced colour, read as before.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select, text

from backend.app.core.filament_scope import (
    file_hold_message,
    print_anyway_hold_message,
    spool_hold_message,
    unlisted_file_hold_message,
)
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.services.inventory_stock import StockEntry
from backend.app.services.print_scheduler import (
    _NO_STOCK_SUFFIX,
    PrintScheduler,
    _forced_override_requirements,
    _is_unmatched_hold_reason,
)
from backend.tests.unit.test_scheduler_model_filament_short_3137 import (
    _add_item,
    _get_item,
    _run,
    queue_db,  # noqa: F401 - fixture
)

FORCED = [{"slot_id": 1, "type": "PLA", "color": "#8B5A2B", "color_name": "Earth Brown", "force_color_match": True}]
MISSING = ["PLA (Earth Brown)"]
ENTRIES = "backend.app.services.print_scheduler.PrintScheduler._stock_entries"
INVENTORY = "backend.app.services.print_scheduler.inventory_stock"
NOTIFY = "backend.app.services.notification_service.notification_service.on_filament_out_of_stock"


def _sched(on: bool = True) -> PrintScheduler:
    """A scheduler with the inventory hints switched on (as a pass would set them) or off (#3590)."""
    scheduler = PrintScheduler()
    scheduler._inventory_hints = on
    return scheduler


async def _setting(ctx, on: bool) -> None:
    """The install-wide setting a queue pass reads."""
    async with ctx.session_maker() as db:
        value = "true" if on else "false"
        row = (
            await db.execute(select(Settings).where(Settings.key == "filament_override_inventory"))
        ).scalar_one_or_none()
        if row is None:
            db.add(Settings(key="filament_override_inventory", value=value))
        else:
            row.value = value
        await db.commit()


async def _set_on(ctx) -> None:
    await _setting(ctx, True)


def _stock(stock):
    """Inventory entries for FORCED giving ``stock`` ((count, grams) or None = unknown)."""
    if stock is None or isinstance(stock, list):
        return stock
    count, grams = stock
    return [StockEntry("PLA", "#8B5A2B", "", "", "", None, None, None, count, grams, 0)]


def _entries(stock):
    return patch(ENTRIES, AsyncMock(return_value=_stock(stock)))


def _pairs():
    return MagicMock(return_value=[(MISSING[0], FORCED[0])])


class TestMatcherReason:
    async def _find(self, ctx, stock, overrides=FORCED, out=None):
        scheduler = _sched()
        with (
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
            patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
            patch.object(scheduler, "_missing_force_color_pairs", _pairs()),
            _entries(stock),
        ):
            async with ctx.session_maker() as db:
                return await scheduler._find_idle_printer_for_model(
                    db, "X1C", set(), filament_overrides=overrides, out_of_stock=out
                )

    @pytest.mark.asyncio
    async def test_zero_stock_adds_the_suffix(self, queue_db):
        out: list[str] = []
        pid, reason = await self._find(queue_db, (0, 0.0), out=out)
        assert pid is None
        assert reason == f"No matching material/color. Waiting on PLA (Earth Brown){_NO_STOCK_SUFFIX}"
        assert out == ["PLA (Earth Brown)"]

    @pytest.mark.asyncio
    async def test_stock_left_keeps_the_reason(self, queue_db):
        out: list[str] = []
        _, reason = await self._find(queue_db, (2, 400.0), out=out)
        assert reason == "No matching material/color. Waiting on PLA (Earth Brown)"
        assert out == []

    @pytest.mark.asyncio
    async def test_unknown_stock_keeps_the_reason(self, queue_db):
        _, reason = await self._find(queue_db, None)
        assert reason == "No matching material/color. Waiting on PLA (Earth Brown)"

    @pytest.mark.asyncio
    async def test_no_overrides_never_asks_the_inventory(self, queue_db):
        scheduler = _sched()
        stock = AsyncMock(return_value=_stock((0, 0.0)))
        with (
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=False)),
            patch.object(scheduler, "_cached_filament_shortfall", MagicMock(return_value=None)),
            patch(ENTRIES, stock),
        ):
            async with queue_db.session_maker() as db:
                _, reason = await scheduler._find_idle_printer_for_model(db, "X1C", set())
        assert _NO_STOCK_SUFFIX not in reason
        stock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_inventory_with_no_spools_is_unknown(self, queue_db):
        _, reason = await self._find(queue_db, None)
        assert reason == "No matching material/color. Waiting on PLA (Earth Brown)"
        _, reason = await self._find(queue_db, [])  # inventory read fine, nothing ever stocked
        assert reason == "No matching material/color. Waiting on PLA (Earth Brown)"

    @pytest.mark.asyncio
    async def test_a_failing_lookup_is_unknown_and_read_once(self, queue_db):
        scheduler = _sched()
        boom = AsyncMock(side_effect=RuntimeError("boom"))
        with patch(INVENTORY, boom):
            async with queue_db.session_maker() as db:
                overrides = {MISSING[0]: [FORCED[0]]}
                assert await scheduler._mark_out_of_stock(db, list(MISSING), overrides) == MISSING
                assert await scheduler._mark_out_of_stock(db, list(MISSING), overrides) == MISSING
                await db.execute(text("SELECT 1"))  # the session is still usable
        boom.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_suffix_goes_to_the_exact_entry_despite_case_and_duplicates(self, queue_db):
        scheduler = _sched()
        other = {**FORCED[0], "color": "#00FF00", "color_name": "Green"}
        entries = [
            StockEntry("PLA", "#8B5A2B", "", "", "", None, None, None, 0, 0.0, 1),
            StockEntry("PLA", "#00FF00", "", "", "", None, None, None, 3, 900.0, 0),
        ]
        overrides = {"PLA (Earth Brown)": [FORCED[0]], "PLA (Green)": [other]}
        with patch(ENTRIES, AsyncMock(return_value=entries)):
            async with queue_db.session_maker() as db:
                out: list[str] = []
                marked = await scheduler._mark_out_of_stock(
                    db, ["pla (earth brown)", "PLA (Green)", "PETG (Blue)"], overrides, out
                )
        assert marked == [f"pla (earth brown){_NO_STOCK_SUFFIX}", "PLA (Green)", "PETG (Blue)"]
        assert out == ["pla (earth brown)"]


class TestQueuePass:
    async def _pass(self, ctx, scheduler, stock):
        await _set_on(ctx)
        with (
            patch.object(scheduler, "_missing_force_color_pairs", _pairs()),
            _entries(stock),
            patch(NOTIFY, AsyncMock()) as notify,
        ):
            await _run(ctx, scheduler, short=set(), launched=MagicMock())
        return notify

    @pytest.mark.asyncio
    async def test_fires_once_on_the_transition_and_not_again(self, queue_db):
        item_id = await _add_item(queue_db, filament_overrides=json.dumps(FORCED))
        scheduler = _sched()

        notify = await self._pass(queue_db, scheduler, (0, 0.0))
        item = await _get_item(queue_db, item_id)
        assert item.waiting_reason.endswith(f"PLA (Earth Brown){_NO_STOCK_SUFFIX}")
        notify.assert_awaited_once()
        kwargs = notify.await_args.kwargs
        assert kwargs["filament"] == "PLA (Earth Brown)"
        assert kwargs["target_model"] == "X1C"

        # Same reason next pass, and after a restart (a new scheduler): the reason is persisted.
        assert (await self._pass(queue_db, scheduler, (0, 0.0))).await_count == 0
        assert (await self._pass(queue_db, _sched(), (0, 0.0))).await_count == 0

    @pytest.mark.asyncio
    async def test_no_event_with_stock_or_unknown(self, queue_db):
        item_id = await _add_item(queue_db, filament_overrides=json.dumps(FORCED))
        for stock in ((3, 500.0), None):
            notify = await self._pass(queue_db, _sched(), stock)
            assert notify.await_count == 0
        item = await _get_item(queue_db, item_id)
        assert _NO_STOCK_SUFFIX not in item.waiting_reason

    @pytest.mark.asyncio
    async def test_fires_when_stock_runs_out_while_waiting(self, queue_db):
        item_id = await _add_item(queue_db, filament_overrides=json.dumps(FORCED))
        scheduler = _sched()
        assert (await self._pass(queue_db, scheduler, (1, 100.0))).await_count == 0
        assert (await self._pass(queue_db, scheduler, (0, 0.0))).await_count == 1
        item = await _get_item(queue_db, item_id)
        assert _NO_STOCK_SUFFIX in item.waiting_reason

    @pytest.mark.asyncio
    async def test_a_notification_failure_does_not_stop_the_queue(self, queue_db):
        item_id = await _add_item(queue_db, filament_overrides=json.dumps(FORCED))
        await _set_on(queue_db)
        with (
            patch.object(PrintScheduler, "_missing_force_color_pairs", _pairs()),
            _entries((0, 0.0)),
            patch(NOTIFY, AsyncMock(side_effect=RuntimeError("down"))),
        ):
            await _run(queue_db, _sched(), short=set(), launched=MagicMock())
        assert _NO_STOCK_SUFFIX in (await _get_item(queue_db, item_id)).waiting_reason


def _pinned_item(overrides):
    item = MagicMock()
    item.id = 7
    item.printer_id = 8
    item.ams_mapping = json.dumps([-1])
    item.skip_filament_check = False
    item.filament_overrides = json.dumps(overrides)
    item.waiting_reason = None
    item.manual_start = False
    return item


def _pinned_scheduler(on: bool = True):
    scheduler = _sched(on)
    scheduler._get_filament_requirements = AsyncMock(
        return_value=[{"slot_id": 1, "type": "PLA", "color": "#FFFFFF", "tray_info_idx": ""}]
    )
    scheduler._get_job_name = AsyncMock(return_value="body1")
    scheduler._get_printer = AsyncMock(return_value=MagicMock(model="P2S"))
    return scheduler


class TestPinnedHold:
    @pytest.mark.asyncio
    async def test_forced_colour_with_no_stock_is_suffixed_and_notified(self):
        scheduler = _pinned_scheduler()
        item = _pinned_item(FORCED)
        with (
            _entries((0, 0.0)),
            patch(NOTIFY, AsyncMock()) as notify,
            patch("backend.app.services.print_scheduler.notification_service.on_queue_job_waiting", AsyncMock()),
        ):
            assert await scheduler._block_on_unmatched_filament(AsyncMock(), item) is True
        assert item.waiting_reason == f"Needs PLA Earth Brown{_NO_STOCK_SUFFIX}"
        assert _is_unmatched_hold_reason(item.waiting_reason)
        notify.assert_awaited_once()
        assert notify.await_args.kwargs["filament"] == "PLA Earth Brown"
        assert notify.await_args.kwargs["printer_id"] == 8

    @pytest.mark.asyncio
    async def test_the_reason_names_the_printer_that_holds_the_job(self):
        scheduler = _pinned_scheduler()
        scheduler._get_printer.return_value.name = "A1 Left"
        item = _pinned_item(FORCED)
        with (
            _entries((0, 0.0)),
            patch(NOTIFY, AsyncMock()),
            patch("backend.app.services.print_scheduler.notification_service.on_queue_job_waiting", AsyncMock()),
        ):
            await scheduler._block_on_unmatched_filament(AsyncMock(), item)
        assert item.waiting_reason == f"A1 Left needs PLA Earth Brown{_NO_STOCK_SUFFIX}"
        # Still the reason a staged item keeps, unlike the short-spool text.
        assert _is_unmatched_hold_reason(item.waiting_reason, "A1 Left")
        assert _is_unmatched_hold_reason("A1 Left needs PLA Earth Brown", "A1 Left")
        assert not _is_unmatched_hold_reason("PLA (20 g left, needs 300 g)", "A1 Left")
        assert not _is_unmatched_hold_reason("PLA (needs 300 g)", "A1 Left")

    @pytest.mark.asyncio
    async def test_a_job_released_to_the_pool_names_no_printer(self):
        scheduler = _pinned_scheduler()
        scheduler._get_printer.return_value.name = "A1 Left"
        item = _pinned_item(FORCED)
        with (
            _entries((2, 300.0)),
            patch(NOTIFY, AsyncMock()),
            patch("backend.app.services.print_scheduler.notification_service.on_queue_job_waiting", AsyncMock()),
            patch("backend.app.services.print_scheduler.drop_printer_resolved"),
        ):
            await scheduler._block_on_unmatched_filament(AsyncMock(), item, release_assignment=True)
        assert item.waiting_reason == "Needs PLA Earth Brown"

    @pytest.mark.asyncio
    async def test_colour_without_a_name_is_shown_as_its_code(self):
        """An override that names no colour (queued over the API) keeps the hex."""
        scheduler = _pinned_scheduler()
        item = _pinned_item([{k: v for k, v in FORCED[0].items() if k != "color_name"}])
        with (
            _entries((2, 300.0)),
            patch(NOTIFY, AsyncMock()),
            patch("backend.app.services.print_scheduler.notification_service.on_queue_job_waiting", AsyncMock()),
        ):
            await scheduler._block_on_unmatched_filament(AsyncMock(), item)
        assert item.waiting_reason == "Needs PLA #8B5A2B"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("requirements", [None, []])
    async def test_a_file_without_requirements_still_names_the_colour(self, requirements):
        """Then the forced overrides are the requirements, and they keep their colour name."""
        scheduler = _pinned_scheduler()
        scheduler._get_filament_requirements = AsyncMock(return_value=requirements)
        scheduler._get_printer.return_value.name = "A1 Left"
        item = _pinned_item(FORCED)
        with (
            _entries((0, 0.0)),
            patch(NOTIFY, AsyncMock()) as notify,
            patch("backend.app.services.print_scheduler.notification_service.on_queue_job_waiting", AsyncMock()),
        ):
            assert await scheduler._block_on_unmatched_filament(AsyncMock(), item) is True
        assert item.waiting_reason == f"A1 Left needs PLA Earth Brown{_NO_STOCK_SUFFIX}"
        assert notify.await_args.kwargs["filament"] == "PLA Earth Brown"

    def test_forced_overrides_as_requirements_keep_the_colour_name(self):
        named, unnamed = FORCED[0], {**FORCED[0], "slot_id": 2, "color_name": None}
        item = _pinned_item([named, unnamed, {**named, "slot_id": 3, "force_color_match": False}])
        assert [(r["slot_id"], r["color_name"]) for r in _forced_override_requirements(item)] == [
            (1, "Earth Brown"),
            (2, ""),
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stock", [(2, 300.0), None])
    async def test_stock_left_or_unknown_is_unchanged(self, stock):
        scheduler = _pinned_scheduler()
        item = _pinned_item(FORCED)
        with (
            _entries(stock),
            patch(NOTIFY, AsyncMock()) as notify,
            patch("backend.app.services.print_scheduler.notification_service.on_queue_job_waiting", AsyncMock()),
        ):
            await scheduler._block_on_unmatched_filament(AsyncMock(), item)
        assert item.waiting_reason == "Needs PLA Earth Brown"
        notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_forced_override_is_never_looked_up(self):
        scheduler = _pinned_scheduler()
        item = _pinned_item([{**FORCED[0], "force_color_match": False}])
        stock = AsyncMock(return_value=_stock((0, 0.0)))
        with (
            patch(ENTRIES, stock),
            patch(NOTIFY, AsyncMock()) as notify,
            patch("backend.app.services.print_scheduler.notification_service.on_queue_job_waiting", AsyncMock()),
        ):
            await scheduler._block_on_unmatched_filament(AsyncMock(), item)
        assert _NO_STOCK_SUFFIX not in item.waiting_reason
        stock.assert_not_awaited()
        notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_model_path_does_not_refire_when_the_reason_was_already_no_stock(self):
        # The model path clears the reason on assignment, so it hands the one the
        # item had before the pass over.
        scheduler = _pinned_scheduler()
        before = f"Needs PLA Earth Brown{_NO_STOCK_SUFFIX}"
        for previous, fires in ((before, 0), (None, 1)):
            item = _pinned_item(FORCED)
            with (
                _entries((0, 0.0)),
                patch(NOTIFY, AsyncMock()) as notify,
                patch("backend.app.services.print_scheduler.notification_service.on_queue_job_waiting", AsyncMock()),
            ):
                held = await scheduler._block_on_unmatched_filament(
                    AsyncMock(), item, release_assignment=True, previous_reason=previous
                )
            assert held is True
            assert item.waiting_reason == before
            assert notify.await_count == fires


PETG = [{"type": "PETG", "tray_info_idx": "GFG03"}]
SCOPE_HOLDS = [
    file_hold_message(PETG),
    spool_hold_message(PETG),
    unlisted_file_hold_message(PETG),
    print_anyway_hold_message([{"type": "PLA"}], PETG),
]


class TestStagedHoldReason:
    """Only the unmatched-filament hold's own reason outlives staging (#2799, #3074).

    A printer-named reason is recognised by the name of the printer the item
    is on, not by the word "needs": the filament scope hold "This file needs a
    filament ..." (#3522) holds it too, and has to be cleared like any other
    reason once the job waits for a start press.
    """

    @pytest.mark.parametrize("reason", SCOPE_HOLDS)
    def test_a_filament_scope_hold_is_not_the_unmatched_hold(self, reason):
        assert not _is_unmatched_hold_reason(reason, "X1C-1")
        assert not _is_unmatched_hold_reason(reason)

    def test_the_printer_named_form_needs_the_items_own_printer(self):
        reason = "X1C-1 needs PLA Earth Brown"
        assert _is_unmatched_hold_reason(reason, "X1C-1")
        assert _is_unmatched_hold_reason(reason, " X1C-1 ")
        assert not _is_unmatched_hold_reason(reason, "X1C-2")
        assert not _is_unmatched_hold_reason(reason)

    def test_a_printer_name_holding_the_word_still_works(self):
        name = "Bay 2 needs service"
        assert _is_unmatched_hold_reason(f"{name} needs PLA Earth Brown", name)
        assert not _is_unmatched_hold_reason(f"{name} is offline", name)

    def test_the_form_without_a_printer_still_works(self):
        assert _is_unmatched_hold_reason("Needs PLA #8B5A2B")
        assert _is_unmatched_hold_reason("Needs PLA Earth Brown", "X1C-1")
        assert not _is_unmatched_hold_reason(None, "X1C-1")
        assert not _is_unmatched_hold_reason("", "X1C-1")

    async def _reason_after_a_pass(self, ctx, reason, *, printer_name=None, **fields):
        if printer_name:
            async with ctx.session_maker() as db:
                (await db.get(Printer, 1)).name = printer_name
                await db.commit()
        values = {"printer_id": 1, "target_model": None, "manual_start": True, "waiting_reason": reason}
        values.update(fields)
        item_id = await _add_item(ctx, **values)
        await _run(ctx, _sched(), short=set(), launched=MagicMock())
        return (await _get_item(ctx, item_id)).waiting_reason

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reason", SCOPE_HOLDS)
    async def test_a_scope_held_job_set_to_manual_start_loses_the_reason(self, queue_db, reason):
        assert await self._reason_after_a_pass(queue_db, reason) is None

    @pytest.mark.asyncio
    async def test_the_reason_naming_its_printer_stays(self, queue_db):
        reason = f"X1C-1 needs PLA Earth Brown{_NO_STOCK_SUFFIX}"
        assert await self._reason_after_a_pass(queue_db, reason) == reason

    @pytest.mark.asyncio
    async def test_the_reason_stays_on_a_printer_named_with_the_word(self, queue_db):
        reason = "Bay 2 needs service needs PLA Earth Brown"
        assert await self._reason_after_a_pass(queue_db, reason, printer_name="Bay 2 needs service") == reason

    @pytest.mark.asyncio
    async def test_a_reason_naming_another_printer_is_cleared(self, queue_db):
        assert await self._reason_after_a_pass(queue_db, "X1C-2 needs PLA Earth Brown") is None

    @pytest.mark.asyncio
    async def test_the_form_without_a_printer_stays(self, queue_db):
        reason = "Needs PLA #8B5A2B"
        assert await self._reason_after_a_pass(queue_db, reason) == reason
        # Released to the pool: no printer holds it.
        pooled = {"printer_id": None, "target_model": "X1C", "position": 2}
        assert await self._reason_after_a_pass(queue_db, reason, **pooled) == reason


class TestPoweredOffPrinters:
    """A switched-off printer's cached trays behave like a live one's (#3590)."""

    RAW = {"ams": [{"tray": [{"tray_type": "PLA", "tray_color": "FFFFFFFF", "tray_info_idx": ""}]}]}

    async def _find(self, ctx, scheduler, out=None):
        with (
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=False)),
            patch.object(scheduler, "_tray_reading", MagicMock(return_value=self.RAW)),
            _entries((0, 0.0)),
        ):
            async with ctx.session_maker() as db:
                return await scheduler._find_idle_printer_for_model(
                    db, "X1C", set(), filament_overrides=FORCED, out_of_stock=out
                )

    @pytest.mark.asyncio
    async def test_all_candidates_off_still_get_the_suffix(self, queue_db):
        out: list[str] = []
        pid, reason = await self._find(queue_db, _sched(), out)
        assert pid is None
        assert _NO_STOCK_SUFFIX in reason
        assert out == ["PLA (Earth Brown)"]

    @pytest.mark.asyncio
    async def test_event_fires_once_across_a_power_cycle(self, queue_db):
        item_id = await _add_item(queue_db, filament_overrides=json.dumps(FORCED))
        await _set_on(queue_db)
        scheduler = _sched()
        fired = 0
        for connected in (False, True, False, True, False):
            with (
                patch(
                    "backend.app.services.print_scheduler.printer_manager.is_connected",
                    MagicMock(return_value=connected),
                ),
                patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
                patch.object(scheduler, "_tray_reading", MagicMock(return_value=self.RAW)),
                patch.object(scheduler, "_missing_force_color_pairs", _pairs()),
                _entries((0, 0.0)),
                patch(NOTIFY, AsyncMock()) as notify,
            ):
                await _run(queue_db, scheduler, short=set(), launched=MagicMock())
            fired += notify.await_count
        assert fired == 1
        assert _NO_STOCK_SUFFIX in (await _get_item(queue_db, item_id)).waiting_reason


class TestSettingOff:
    """With the setting off (the default) the #3590 behaviour is skipped entirely."""

    @pytest.mark.asyncio
    async def test_model_path_reason_is_plain_and_stock_is_never_read(self, queue_db):
        scheduler = _sched(False)
        stock, inventory = AsyncMock(return_value=_stock((0, 0.0))), AsyncMock(return_value=_stock((0, 0.0)))
        out: list[str] = []
        with (
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
            patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
            patch.object(scheduler, "_missing_force_color_pairs", _pairs()),
            patch(ENTRIES, stock),
            patch(INVENTORY, inventory),
        ):
            async with queue_db.session_maker() as db:
                pid, reason = await scheduler._find_idle_printer_for_model(
                    db, "X1C", set(), filament_overrides=FORCED, out_of_stock=out
                )
        assert pid is None
        assert reason == "No matching material/color. Waiting on PLA (Earth Brown)"
        assert out == []
        stock.assert_not_awaited()
        inventory.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_powered_off_path_is_plain(self, queue_db):
        scheduler = _sched(False)
        stock = AsyncMock(return_value=_stock((0, 0.0)))
        with (
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=False)),
            patch.object(scheduler, "_tray_reading", MagicMock(return_value=TestPoweredOffPrinters.RAW)),
            patch(ENTRIES, stock),
        ):
            async with queue_db.session_maker() as db:
                _, reason = await scheduler._find_idle_printer_for_model(
                    db, "X1C", set(), filament_overrides=FORCED, out_of_stock=[]
                )
        assert _NO_STOCK_SUFFIX not in reason
        stock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pinned_path_is_plain_and_silent(self):
        scheduler = _pinned_scheduler(False)
        item = _pinned_item(FORCED)
        stock = AsyncMock(return_value=_stock((0, 0.0)))
        with (
            patch(ENTRIES, stock),
            patch(NOTIFY, AsyncMock()) as notify,
            patch("backend.app.services.print_scheduler.notification_service.on_queue_job_waiting", AsyncMock()),
        ):
            assert await scheduler._block_on_unmatched_filament(AsyncMock(), item) is True
        assert item.waiting_reason == "Needs PLA Earth Brown"
        stock.assert_not_awaited()
        notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_queue_pass_with_the_setting_unset_or_false(self, queue_db):
        item_id = await _add_item(queue_db, filament_overrides=json.dumps(FORCED))
        stock = AsyncMock(return_value=_stock((0, 0.0)))
        for value in (None, False):
            if value is not None:
                await _setting(queue_db, value)
            with (
                patch.object(PrintScheduler, "_missing_force_color_pairs", _pairs()),
                patch(ENTRIES, stock),
                patch(NOTIFY, AsyncMock()) as notify,
            ):
                await _run(queue_db, PrintScheduler(), short=set(), launched=MagicMock())
            notify.assert_not_awaited()
            assert _NO_STOCK_SUFFIX not in (await _get_item(queue_db, item_id)).waiting_reason
        stock.assert_not_awaited()
