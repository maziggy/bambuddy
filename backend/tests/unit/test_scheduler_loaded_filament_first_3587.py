"""Queue mode "loaded filament first" with a maximum wait (#3587).

With the mode on, a job whose filament is not on its printer is skipped instead
of held: it stays pending, the printer stays free for the jobs behind it, and it
starts by itself once the spool is loaded. After the maximum wait it gets
priority: its printer is kept free for it and the user is told once.

With the mode off nothing changes; the "mode off" tests below pin the staging
behaviour of the three gates this feature routes around.
"""

import json
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core.database import Base
from backend.app.core.filament_scope import (
    file_hold_message,
    print_anyway_hold_message,
    spool_hold_message,
    unlisted_file_hold_message,
)
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem, PrintQueueVariant
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.services import plate_clear
from backend.app.services.filament_deficit import FilamentDeficit
from backend.app.services.print_scheduler import (
    FilamentScopeHold,
    PrintScheduler,
    _describe_deficit,
    _filament_wait_suffix,
    _is_filament_wait,
)

_DEFICIT = [
    FilamentDeficit(
        slot_id=1,
        ams_id=0,
        tray_id=0,
        filament_type="PLA",
        required_grams=120.0,
        remaining_grams=40.0,
    )
]
_DEFICIT_TEXT = "PLA (40 g left, needs 120 g)"
_BROWN = [{"slot_id": 1, "type": "PLA", "color": "Earth Brown"}]
_BROWN_TEXT = "PLA Earth Brown"


def _naive_utc(hours_ago: float) -> datetime:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).replace(tzinfo=None)


@pytest.fixture
async def ctx():
    """Three X1Cs and two P1Ss, in id order, so "the first idle printer" is defined."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with session_maker() as db:
        db.add_all(
            [
                Printer(
                    id=pid,
                    name=f"{model}-{pid}",
                    serial_number=f"SN000{pid}",
                    ip_address=f"10.0.0.{pid}",
                    access_code="x",
                    model=model,
                    is_active=True,
                )
                for pid, model in ((1, "X1C"), (2, "X1C"), (3, "X1C"), (4, "P1S"), (5, "P1S"))
            ]
        )
        await db.commit()

    try:
        yield SimpleNamespace(session_maker=session_maker)
    finally:
        await engine.dispose()


async def _set(ctx, key, value):
    async with ctx.session_maker() as db:
        row = (await db.execute(select(Settings).where(Settings.key == key))).scalar_one_or_none()
        if row is None:
            db.add(Settings(key=key, value=value))
        else:
            row.value = value
        await db.commit()


async def _mode(ctx, on=True, hours="48"):
    await _set(ctx, "queue_loaded_filament_first", "true" if on else "false")
    if hours is not None:
        await _set(ctx, "queue_max_wait_hours", hours)


async def _add_item(ctx, *, age_hours=10, variants=None, **fields):
    """A pending item created ``age_hours`` ago (back-dated in the row)."""
    async with ctx.session_maker() as db:
        lib = LibraryFile(
            filename="job.gcode.3mf",
            file_path="/library/job.gcode.3mf",
            file_size=10,
            file_type="gcode.3mf",
            file_metadata={"sliced_for_model": "X1C"},
        )
        db.add(lib)
        await db.flush()
        values = {
            "status": "pending",
            "position": 1,
            "library_file_id": lib.id,
            "created_at": _naive_utc(age_hours),
        }
        if not variants and "printer_id" not in fields:
            values["target_model"] = "X1C"
        values.update(fields)
        item = PrintQueueItem(**values)
        db.add(item)
        await db.flush()
        for position, model in enumerate(variants or []):
            vlib = LibraryFile(
                filename=f"job_{model}.gcode.3mf",
                file_path=f"/library/job_{model}.gcode.3mf",
                file_size=10,
                file_type="gcode.3mf",
                file_metadata={"sliced_for_model": model},
            )
            db.add(vlib)
            await db.flush()
            db.add(
                PrintQueueVariant(queue_item_id=item.id, position=position, library_file_id=vlib.id, target_model=model)
            )
        await db.commit()
        return item.id


async def _get(ctx, item_id):
    async with ctx.session_maker() as db:
        return (await db.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))).scalar_one()


async def _run(
    ctx,
    scheduler,
    *,
    launched=None,
    waiting=None,
    deficit_items=frozenset(),
    unmatched_items=frozenset(),
    short=frozenset(),
    unmatched_on=frozenset(),
    idle=lambda pid: True,
    unmappable=None,
    real_unmatched=False,
    real_short=False,
    connected=lambda pid: True,
    extra_patches=(),
    assigned=None,
):
    """One check_queue pass.

    ``deficit_items`` / ``unmatched_items``: item ids whose pinned gate finds a
    deficit / the brown filament unmatched. ``short``: printers short for the
    matcher, a set of printer ids or ``{item_id: {printer ids}}``. ``unmatched_on``:
    printers the matcher cannot map. ``idle``: printer id -> idle. ``real_unmatched``
    / ``real_short`` keep the scheduler's own finder instead of the fake.
    ``connected``: printer id -> connected. ``extra_patches``: more patches for
    the pass (entered last, so they win). ``assigned``: the mock standing in for
    the "job assigned" notification.
    """
    launched = launched if launched is not None else MagicMock()
    waiting = waiting if waiting is not None else AsyncMock()
    assigned = assigned if assigned is not None else AsyncMock()

    async def deficit(db, item, *, printer_id=None, ams_mapping=None, require_known=False):
        return _DEFICIT if item.id in deficit_items else []

    async def unmatched_requirements(db, item, printer_id, mapping):
        return list(_BROWN) if item.id in unmatched_items else []

    async def short_on(db, item, printer_id, *, require_known=False):
        pids = short.get(item.id, ()) if isinstance(short, dict) else short
        return printer_id in pids

    async def unmatched_on_check(db, item, printer_id):
        return ["PLA #FFFFFF"] if printer_id in unmatched_on else []

    patches = [
        patch("backend.app.services.print_scheduler.async_session", ctx.session_maker),
        patch("backend.app.core.database.async_session", ctx.session_maker),
        patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(side_effect=connected)),
        patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=None)),
        patch(
            "backend.app.services.print_scheduler.printer_manager.is_awaiting_plate_clear",
            MagicMock(return_value=False),
        ),
        patch(
            "backend.app.services.print_scheduler.ha_sensor_manager.blocked_printers",
            AsyncMock(return_value={}),
        ),
        patch("backend.app.services.notification_service.notification_service.on_queue_job_waiting", waiting),
        patch("backend.app.services.notification_service.notification_service.on_queue_job_assigned", assigned),
        patch("backend.app.services.notification_service.notification_service.on_queue_job_failed", AsyncMock()),
        patch("backend.app.services.print_batch.refresh_batches_after", AsyncMock()),
        patch("backend.app.services.print_scheduler.compute_deficit_for_queue_item", AsyncMock(side_effect=deficit)),
        patch.object(scheduler, "_unmatched_on", AsyncMock(side_effect=unmatched_on_check)),
        patch.object(scheduler, "_is_printer_idle", MagicMock(side_effect=lambda pid, *a, **k: idle(pid))),
        patch.object(scheduler, "_check_auto_drying", AsyncMock()),
        patch.object(scheduler, "_ensure_ams_mapping", AsyncMock(return_value=unmappable)),
        patch.object(scheduler, "_get_smart_plugs", AsyncMock(return_value=[])),
        patch.object(scheduler, "_launch_uploads", launched),
    ]
    if not real_unmatched:
        patches.append(
            patch.object(scheduler, "_unmatched_requirements", AsyncMock(side_effect=unmatched_requirements))
        )
    else:
        # A printer that reported trays, none of them usable: the real finder reads it.
        status = SimpleNamespace(raw_data={}, state="IDLE")
        patches.append(patch.object(scheduler, "_build_loaded_filaments", MagicMock(return_value=[{"x": 1}])))
        patches.append(patch.object(scheduler, "_get_filament_requirements", AsyncMock(return_value=[])))
        patches.append(
            patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=status))
        )
    if not real_short:
        patches.append(patch.object(scheduler, "_filament_short_on", AsyncMock(side_effect=short_on)))
    with ExitStack() as stack:
        for p in [*patches, *extra_patches]:
            stack.enter_context(p)
        await scheduler.check_queue()
    return SimpleNamespace(launched=launched, waiting=waiting, assigned=assigned)


def _dispatched(result) -> list[int]:
    return list(result.launched.call_args[0][0]) if result.launched.called else []


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestWaitSuffix:
    def _item(self, created_hours_ago=None, scheduled_hours_from_now=None, aware=False):
        now = datetime.now(timezone.utc)
        created = now - timedelta(hours=created_hours_ago) if created_hours_ago is not None else None
        scheduled = now + timedelta(hours=scheduled_hours_from_now) if scheduled_hours_from_now is not None else None
        if not aware:
            created = created.replace(tzinfo=None) if created else None
            scheduled = scheduled.replace(tzinfo=None) if scheduled else None
        return SimpleNamespace(created_at=created, scheduled_time=scheduled), now

    def test_no_limit_is_empty_and_never_priority(self):
        item, now = self._item(created_hours_ago=1000)
        assert _filament_wait_suffix(item, now, None) == ("", False)

    def test_counts_down_in_whole_hours_rounded_up(self):
        item, now = self._item(created_hours_ago=10)
        assert _filament_wait_suffix(item, now, 48) == (" · priority in 38 h", False)

    def test_never_below_one_hour(self):
        item, now = self._item(created_hours_ago=47.99)
        assert _filament_wait_suffix(item, now, 48) == (" · priority in 1 h", False)

    def test_at_the_limit_it_has_priority(self):
        item, now = self._item(created_hours_ago=48)
        assert _filament_wait_suffix(item, now, 48) == (" · has priority", True)

    def test_aware_datetimes_work_too(self):
        item, now = self._item(created_hours_ago=100, aware=True)
        assert _filament_wait_suffix(item, now, 48)[1] is True

    def test_the_wait_starts_at_the_scheduled_time(self):
        """Queued long ago for a time that passed one hour ago: one hour of waiting."""
        item, now = self._item(created_hours_ago=100, scheduled_hours_from_now=-1)
        assert _filament_wait_suffix(item, now, 48) == (" · priority in 47 h", False)

    def test_a_schedule_before_creation_does_not_count(self):
        item, now = self._item(created_hours_ago=2, scheduled_hours_from_now=-50)
        assert _filament_wait_suffix(item, now, 48) == (" · priority in 46 h", False)

    def test_no_created_at_means_now(self):
        item, now = self._item()
        assert _filament_wait_suffix(item, now, 48) == (" · priority in 48 h", False)


class TestReasonHelpers:
    def test_is_filament_wait(self):
        assert _is_filament_wait("Waiting for filament: X1C-1 (needs PLA)")
        assert _is_filament_wait("Waiting for filament: X1C-1 (needs PLA) | Busy: X1C-2")
        assert _is_filament_wait("No matching material/color. Waiting on PLA (Red)")
        assert _is_filament_wait("X1C: Waiting for filament: A (needs PLA); P1S: Busy: B")
        assert not _is_filament_wait("Busy: X1C-1")
        assert not _is_filament_wait("Offline: X1C-1")
        assert not _is_filament_wait(None)

    def test_deficit_description(self):
        assert _describe_deficit(_DEFICIT) == _DEFICIT_TEXT
        unknown = [FilamentDeficit(1, 0, 0, "PETG", 80.0, None)]
        assert _describe_deficit(unknown) == "PETG (needs 80 g)"
        assert _describe_deficit(_DEFICIT + unknown) == f"{_DEFICIT_TEXT}, PETG (needs 80 g)"

    @pytest.mark.asyncio
    async def test_optional_int_setting(self, ctx):
        scheduler = PrintScheduler()
        async with ctx.session_maker() as db:
            assert await scheduler._get_optional_int_setting(db, "queue_max_wait_hours", 48) == 48
        for stored, expected in (("12", 12), ("None", None), ("", None), ("junk", 48)):
            await _set(ctx, "queue_max_wait_hours", stored)
            async with ctx.session_maker() as db:
                assert await scheduler._get_optional_int_setting(db, "queue_max_wait_hours", 48) == expected


# ---------------------------------------------------------------------------
# Pinned jobs, mode on
# ---------------------------------------------------------------------------


class TestPinnedSkipInsteadOfHold:
    @pytest.mark.asyncio
    async def test_unmatched_filament_is_skipped(self, ctx):
        await _mode(ctx)
        waiting_item = await _add_item(ctx, printer_id=1, position=1)
        later = await _add_item(ctx, printer_id=1, position=2, age_hours=1)

        result = await _run(ctx, PrintScheduler(), unmatched_items={waiting_item})

        item = await _get(ctx, waiting_item)
        assert item.status == "pending"
        assert item.manual_start is False
        assert item.filament_short is False
        assert item.printer_id == 1
        assert item.waiting_reason == f"Waiting for filament: {_BROWN_TEXT} · priority in 38 h"
        # The printer was left free: the job behind it goes out in the same pass.
        assert _dispatched(result) == [later]
        result.waiting.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_forced_colour_is_skipped(self, ctx):
        """The real unmatched finder, fed a forced colour and an unresolved slot."""
        await _mode(ctx)
        overrides = json.dumps([{"slot_id": 1, "type": "PLA", "color": "#FF0000", "force_color_match": True}])
        waiting_item = await _add_item(
            ctx, printer_id=1, position=1, filament_overrides=overrides, ams_mapping=json.dumps([-1])
        )
        later = await _add_item(ctx, printer_id=1, position=2, age_hours=1)

        result = await _run(ctx, PrintScheduler(), real_unmatched=True)

        item = await _get(ctx, waiting_item)
        assert item.manual_start is False
        assert item.waiting_reason == "Waiting for filament: PLA #FF0000 · priority in 38 h"
        assert _dispatched(result) == [later]

    @pytest.mark.asyncio
    async def test_a_deficit_is_skipped(self, ctx):
        await _mode(ctx)
        waiting_item = await _add_item(ctx, printer_id=1, position=1)
        later = await _add_item(ctx, printer_id=1, position=2, age_hours=1)

        result = await _run(ctx, PrintScheduler(), deficit_items={waiting_item})

        item = await _get(ctx, waiting_item)
        assert item.status == "pending"
        assert item.manual_start is False
        assert item.filament_short is False
        assert item.waiting_reason == f"Waiting for filament: {_DEFICIT_TEXT} · priority in 38 h"
        assert _dispatched(result) == [later]
        result.waiting.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_starts_by_itself_once_it_fits(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx, printer_id=1)
        scheduler = PrintScheduler()
        deficit = {item_id}

        first = await _run(ctx, scheduler, deficit_items=deficit)
        assert _dispatched(first) == []
        assert (await _get(ctx, item_id)).waiting_reason.startswith("Waiting for filament: ")

        deficit.clear()  # the spool was swapped
        second = await _run(ctx, scheduler, deficit_items=deficit)

        assert _dispatched(second) == [item_id]
        assert (await _get(ctx, item_id)).waiting_reason is None

    @pytest.mark.asyncio
    async def test_the_countdown_follows_the_age(self, ctx):
        await _mode(ctx, hours="24")
        item_id = await _add_item(ctx, printer_id=1, age_hours=23.5)

        await _run(ctx, PrintScheduler(), deficit_items={item_id})

        assert (await _get(ctx, item_id)).waiting_reason.endswith(" · priority in 1 h")

    @pytest.mark.asyncio
    async def test_print_anyway_still_dispatches(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx, printer_id=1, skip_filament_check=True)

        result = await _run(ctx, PrintScheduler(), deficit_items={item_id}, unmatched_items={item_id})

        assert _dispatched(result) == [item_id]

    @pytest.mark.asyncio
    async def test_a_staged_item_stays_staged(self, ctx):
        """Staged before the mode was switched on: the manual_start gate runs first."""
        await _mode(ctx)
        item_id = await _add_item(ctx, printer_id=1, manual_start=True, filament_short=True)

        result = await _run(ctx, PrintScheduler(), deficit_items={item_id})

        assert _dispatched(result) == []
        item = await _get(ctx, item_id)
        assert item.manual_start is True
        assert item.filament_short is True
        result.waiting.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unmappable_job_still_fails(self, ctx):
        """#2771 is decided before the filament gates and is not skipped."""
        await _mode(ctx)
        item_id = await _add_item(ctx, printer_id=1)

        await _run(ctx, PrintScheduler(), unmappable="Nothing loaded matches PLA", deficit_items={item_id})

        item = await _get(ctx, item_id)
        assert item.status == "failed"
        assert item.error_message == "Nothing loaded matches PLA"


class TestPinnedMaxWait:
    @pytest.mark.asyncio
    async def test_after_the_max_wait_the_printer_is_reserved(self, ctx):
        await _mode(ctx)
        waiting_item = await _add_item(ctx, printer_id=1, position=1, age_hours=49)
        later = await _add_item(ctx, printer_id=1, position=2, age_hours=1)

        result = await _run(ctx, PrintScheduler(), deficit_items={waiting_item})

        assert _dispatched(result) == []
        first = await _get(ctx, waiting_item)
        assert first.waiting_reason == f"Waiting for filament: {_DEFICIT_TEXT} · has priority"
        assert first.manual_start is False
        assert first.filament_short is False
        assert (await _get(ctx, later)).waiting_reason == "Busy: X1C-1 (kept free for a job waiting for filament)"

    @pytest.mark.asyncio
    async def test_other_printers_are_not_touched(self, ctx):
        await _mode(ctx)
        waiting_item = await _add_item(ctx, printer_id=1, position=1, age_hours=49)
        other = await _add_item(ctx, printer_id=2, position=2, age_hours=1)

        result = await _run(ctx, PrintScheduler(), deficit_items={waiting_item})

        assert _dispatched(result) == [other]

    @pytest.mark.asyncio
    async def test_notified_exactly_once_across_passes_and_not_while_skipping(self, ctx):
        await _mode(ctx, hours="48")
        item_id = await _add_item(ctx, printer_id=1, age_hours=10)
        scheduler = PrintScheduler()
        waiting = AsyncMock()

        for _ in range(3):
            await _run(ctx, scheduler, waiting=waiting, deficit_items={item_id})
        assert waiting.await_count == 0  # the skip phase is silent

        async with ctx.session_maker() as db:
            row = await db.get(PrintQueueItem, item_id)
            row.created_at = _naive_utc(49)  # the limit is reached
            await db.commit()

        for _ in range(4):
            await _run(ctx, scheduler, waiting=waiting, deficit_items={item_id})

        assert waiting.await_count == 1
        kwargs = waiting.await_args.kwargs
        assert kwargs["waiting_reason"] == f"Waiting for filament: {_DEFICIT_TEXT} · has priority"
        assert kwargs["printer_id"] == 1

    @pytest.mark.asyncio
    async def test_a_restart_does_not_repeat_the_notification(self, ctx):
        """The state is the persisted reason, not anything the scheduler remembers."""
        await _mode(ctx)
        item_id = await _add_item(ctx, printer_id=1, age_hours=49)
        waiting = AsyncMock()

        await _run(ctx, PrintScheduler(), waiting=waiting, deficit_items={item_id})
        await _run(ctx, PrintScheduler(), waiting=waiting, deficit_items={item_id})  # a new instance

        assert waiting.await_count == 1


class TestPinnedNoLimit:
    @pytest.mark.asyncio
    async def test_no_suffix_and_never_reserves(self, ctx):
        await _mode(ctx, hours="None")
        waiting_item = await _add_item(ctx, printer_id=1, position=1, age_hours=100000)
        later = await _add_item(ctx, printer_id=1, position=2, age_hours=1)

        result = await _run(ctx, PrintScheduler(), deficit_items={waiting_item})

        assert (await _get(ctx, waiting_item)).waiting_reason == f"Waiting for filament: {_DEFICIT_TEXT}"
        assert _dispatched(result) == [later]

    @pytest.mark.asyncio
    async def test_notified_once_when_it_starts_waiting(self, ctx):
        await _mode(ctx, hours="None")
        item_id = await _add_item(ctx, printer_id=1)
        scheduler = PrintScheduler()
        waiting = AsyncMock()

        for _ in range(4):
            await _run(ctx, scheduler, waiting=waiting, deficit_items={item_id})

        assert waiting.await_count == 1
        assert waiting.await_args.kwargs["waiting_reason"] == f"Waiting for filament: {_DEFICIT_TEXT}"

    @pytest.mark.asyncio
    async def test_a_missing_hours_row_means_the_default_48(self, ctx):
        await _mode(ctx, hours=None)
        item_id = await _add_item(ctx, printer_id=1, age_hours=10)

        await _run(ctx, PrintScheduler(), deficit_items={item_id})

        assert (await _get(ctx, item_id)).waiting_reason.endswith(" · priority in 38 h")


# ---------------------------------------------------------------------------
# "Any <model>" jobs, mode on
# ---------------------------------------------------------------------------


class TestModelJobs:
    @pytest.mark.asyncio
    async def test_short_printers_are_passed_over_for_a_full_one(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx)

        result = await _run(ctx, PrintScheduler(), short={1, 2})

        assert _dispatched(result) == [item_id]
        assert (await _get(ctx, item_id)).printer_id == 3

    @pytest.mark.asyncio
    async def test_waits_unassigned_when_every_printer_is_short(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx)

        result = await _run(ctx, PrintScheduler(), short={1, 2, 3})

        assert _dispatched(result) == []
        item = await _get(ctx, item_id)
        assert item.printer_id is None
        assert item.manual_start is False
        assert item.filament_short is False
        assert item.waiting_reason == (
            "Waiting for filament: X1C-1 (needs more filament); X1C-2 (needs more filament); "
            "X1C-3 (needs more filament) · priority in 38 h"
        )
        result.waiting.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unmatched_printers_wait_with_the_suffix(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx)

        await _run(ctx, PrintScheduler(), unmatched_on={1, 2, 3})

        reason = (await _get(ctx, item_id)).waiting_reason
        assert reason.startswith("Waiting for filament: X1C-1 (needs PLA #FFFFFF)")
        assert reason.endswith(" · priority in 38 h")

    @pytest.mark.asyncio
    async def test_the_suffix_goes_at_the_very_end_after_busy_parts(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx)

        await _run(ctx, PrintScheduler(), short={1, 2}, idle=lambda pid: pid != 3)

        reason = (await _get(ctx, item_id)).waiting_reason
        assert reason == (
            "Waiting for filament: X1C-1 (needs more filament); X1C-2 (needs more filament)"
            " | Busy: X1C-3 · priority in 38 h"
        )

    @pytest.mark.asyncio
    async def test_busy_only_reasons_have_no_suffix(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx)

        result = await _run(ctx, PrintScheduler(), idle=lambda pid: False)

        assert (await _get(ctx, item_id)).waiting_reason == "Busy: X1C-1, X1C-2, X1C-3"
        result.waiting.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_after_the_max_wait_it_reserves_the_first_idle_printer(self, ctx):
        """Printer 1 is printing, 2 and 3 are idle; the job is short on all of them.
        Printer 2 is the one the normal selection would hand it, so 2 is kept free
        and a second job of the model takes 3."""
        await _mode(ctx)
        waiting_item = await _add_item(ctx, position=1, age_hours=49)
        second = await _add_item(ctx, position=2, age_hours=1)

        result = await _run(
            ctx,
            PrintScheduler(),
            short={waiting_item: {1, 2, 3}},
            idle=lambda pid: pid != 1,
        )

        first = await _get(ctx, waiting_item)
        assert first.printer_id is None
        assert first.waiting_reason.endswith(" · has priority")
        assert _dispatched(result) == [second]
        assert (await _get(ctx, second)).printer_id == 3

    @pytest.mark.asyncio
    async def test_the_reservation_follows_the_dispatch_order_setting(self, ctx):
        await _mode(ctx)
        await _set(ctx, "model_dispatch_order", "most_hours")
        async with ctx.session_maker() as db:
            for pid, hours in ((1, 5), (2, 50), (3, 20)):
                printer = await db.get(Printer, pid)
                printer.runtime_seconds = hours * 3600
            await db.commit()
        waiting_item = await _add_item(ctx, position=1, age_hours=49)
        second = await _add_item(ctx, position=2, age_hours=1)

        result = await _run(ctx, PrintScheduler(), short={waiting_item: {1, 2, 3}})

        # Most hours first: 2, then 3, then 1. The job keeps 2; the second takes 3.
        assert _dispatched(result) == [second]
        assert (await _get(ctx, second)).printer_id == 3

    @pytest.mark.asyncio
    async def test_a_busy_fleet_is_no_filament_wait_even_past_the_limit(self, ctx):
        await _mode(ctx)
        waiting_item = await _add_item(ctx, age_hours=49)

        result = await _run(ctx, PrintScheduler(), short={1, 2, 3}, idle=lambda pid: False)

        assert _dispatched(result) == []
        # Nothing idle: the matcher only sees busy printers, which is no filament wait.
        assert (await _get(ctx, waiting_item)).waiting_reason == "Busy: X1C-1, X1C-2, X1C-3"

    @pytest.mark.asyncio
    async def test_an_item_pinned_behind_a_reserved_printer_reads_busy(self, ctx):
        await _mode(ctx)
        waiting_item = await _add_item(ctx, position=1, age_hours=49)
        pinned = await _add_item(ctx, position=2, printer_id=1, age_hours=1)

        result = await _run(ctx, PrintScheduler(), short={waiting_item: {1, 2, 3}})

        # 1 was the first idle printer in order: it is the reserved one.
        assert _dispatched(result) == []
        assert (await _get(ctx, pinned)).waiting_reason == "Busy: X1C-1 (kept free for a job waiting for filament)"

    @pytest.mark.asyncio
    async def test_no_limit_never_reserves(self, ctx):
        await _mode(ctx, hours="None")
        waiting_item = await _add_item(ctx, position=1, age_hours=100000)
        second = await _add_item(ctx, position=2)

        result = await _run(ctx, PrintScheduler(), short={waiting_item: {1, 2, 3}})

        assert _dispatched(result) == [second]
        assert (await _get(ctx, second)).printer_id == 1
        assert not (await _get(ctx, waiting_item)).waiting_reason.endswith(("has priority", " h"))

    @pytest.mark.asyncio
    async def test_notified_once_at_priority_not_while_skipping(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx, age_hours=10)
        scheduler = PrintScheduler()
        waiting = AsyncMock()

        for _ in range(3):
            await _run(ctx, scheduler, waiting=waiting, short={1, 2, 3})
        assert waiting.await_count == 0

        async with ctx.session_maker() as db:
            row = await db.get(PrintQueueItem, item_id)
            row.created_at = _naive_utc(49)
            await db.commit()
        for _ in range(4):
            await _run(ctx, scheduler, waiting=waiting, short={1, 2, 3})

        assert waiting.await_count == 1
        assert waiting.await_args.kwargs["waiting_reason"].endswith(" · has priority")
        assert waiting.await_args.kwargs["target_model"] == "X1C"

    @pytest.mark.asyncio
    async def test_no_limit_notifies_once_at_the_start(self, ctx):
        await _mode(ctx, hours="None")
        await _add_item(ctx)
        scheduler = PrintScheduler()
        waiting = AsyncMock()

        for _ in range(4):
            await _run(ctx, scheduler, waiting=waiting, short={1, 2, 3})

        assert waiting.await_count == 1

    @pytest.mark.asyncio
    async def test_starts_when_a_spool_is_loaded(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx)
        scheduler = PrintScheduler()

        await _run(ctx, scheduler, short={1, 2, 3})
        result = await _run(ctx, scheduler, short={1, 2})

        assert _dispatched(result) == [item_id]
        item = await _get(ctx, item_id)
        assert item.printer_id == 3
        assert item.waiting_reason is None

    @pytest.mark.asyncio
    async def test_print_anyway_still_dispatches(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx, skip_filament_check=True)

        # The real shortfall check answers False for "Print Anyway".
        result = await _run(ctx, PrintScheduler(), real_short=True)

        assert _dispatched(result) == [item_id]

    @pytest.mark.asyncio
    async def test_a_staged_item_stays_staged(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx, printer_id=1, manual_start=True, filament_short=True)

        result = await _run(ctx, PrintScheduler(), deficit_items={item_id})

        assert _dispatched(result) == []
        assert (await _get(ctx, item_id)).manual_start is True

    @pytest.mark.asyncio
    async def test_a_race_after_assignment_releases_and_waits(self, ctx):
        """The matcher saw enough filament; the deficit check at dispatch disagrees."""
        await _mode(ctx)
        item_id = await _add_item(ctx, age_hours=49)

        result = await _run(ctx, PrintScheduler(), deficit_items={item_id})

        assert _dispatched(result) == []
        item = await _get(ctx, item_id)
        assert item.printer_id is None
        assert item.manual_start is False
        assert item.filament_short is False
        assert item.waiting_reason == f"Waiting for filament: {_DEFICIT_TEXT} · has priority"
        assert result.waiting.await_count == 1

    @pytest.mark.asyncio
    async def test_a_race_after_assignment_before_the_limit_is_silent(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx, age_hours=10)

        result = await _run(ctx, PrintScheduler(), deficit_items={item_id})

        item = await _get(ctx, item_id)
        assert item.printer_id is None
        assert item.waiting_reason == f"Waiting for filament: {_DEFICIT_TEXT} · priority in 38 h"
        result.waiting.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_race_on_unmatched_filament_releases_and_waits(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx, age_hours=10)

        result = await _run(ctx, PrintScheduler(), unmatched_items={item_id})

        item = await _get(ctx, item_id)
        assert item.printer_id is None
        assert item.manual_start is False
        assert item.waiting_reason == f"Waiting for filament: {_BROWN_TEXT} · priority in 38 h"
        assert _dispatched(result) == []


class TestModelVariants:
    @pytest.mark.asyncio
    async def test_a_variant_job_reserves_in_candidate_order(self, ctx):
        """X1C first, P1S second. No X1C is idle, so the first idle P1S is kept."""
        await _mode(ctx)
        item_id = await _add_item(ctx, variants=["X1C", "P1S"], age_hours=49)
        pinned = await _add_item(ctx, printer_id=4, position=2, age_hours=1)

        result = await _run(
            ctx,
            PrintScheduler(),
            short={item_id: {1, 2, 3, 4, 5}},
            idle=lambda pid: pid >= 4,
        )

        item = await _get(ctx, item_id)
        assert item.printer_id is None
        assert item.waiting_reason.endswith(" · has priority")
        assert _dispatched(result) == []
        assert (await _get(ctx, pinned)).waiting_reason == "Busy: P1S-4 (kept free for a job waiting for filament)"

    @pytest.mark.asyncio
    async def test_a_variant_job_goes_to_whichever_model_has_the_filament(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx, variants=["X1C", "P1S"])

        result = await _run(ctx, PrintScheduler(), short={1, 2, 3})

        assert _dispatched(result) == [item_id]
        assert (await _get(ctx, item_id)).printer_id == 4


# ---------------------------------------------------------------------------
# Mode off: today's behaviour, with the setting stored as "false" or absent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stored", ["false", None])
class TestModeOff:
    async def _off(self, ctx, stored):
        if stored is not None:
            await _set(ctx, "queue_loaded_filament_first", stored)
            await _set(ctx, "queue_max_wait_hours", "1")

    @pytest.mark.asyncio
    async def test_pinned_unmatched_hold_stages(self, ctx, stored):
        await self._off(ctx, stored)
        item_id = await _add_item(ctx, printer_id=1, age_hours=500)
        later = await _add_item(ctx, printer_id=1, position=2)

        result = await _run(ctx, PrintScheduler(), unmatched_items={item_id})

        item = await _get(ctx, item_id)
        assert item.manual_start is True
        assert item.waiting_reason == f"Needs {_BROWN_TEXT}"
        assert item.printer_id == 1
        assert result.waiting.await_count == 1
        assert result.waiting.await_args.kwargs["waiting_reason"] == f"needs {_BROWN_TEXT}"
        # The next job for the printer goes out, as it always did.
        assert _dispatched(result) == [later]

    @pytest.mark.asyncio
    async def test_pinned_deficit_stages(self, ctx, stored):
        await self._off(ctx, stored)
        item_id = await _add_item(ctx, printer_id=1, age_hours=500)

        result = await _run(ctx, PrintScheduler(), deficit_items={item_id})

        item = await _get(ctx, item_id)
        assert item.manual_start is True
        assert item.filament_short is True
        assert item.waiting_reason is None
        assert _dispatched(result) == []
        assert result.waiting.await_count == 1
        assert result.waiting.await_args.kwargs["waiting_reason"] == "filament_short"

    @pytest.mark.asyncio
    async def test_model_short_fallback_stages_on_the_first_short_printer(self, ctx, stored):
        await self._off(ctx, stored)
        item_id = await _add_item(ctx, age_hours=500)

        result = await _run(ctx, PrintScheduler(), short={1, 2, 3}, deficit_items={item_id})

        item = await _get(ctx, item_id)
        assert item.printer_id == 1
        assert item.manual_start is True
        assert item.filament_short is True
        assert _dispatched(result) == []

    @pytest.mark.asyncio
    async def test_model_unmatched_race_releases_and_stages(self, ctx, stored):
        await self._off(ctx, stored)
        item_id = await _add_item(ctx, age_hours=500)

        await _run(ctx, PrintScheduler(), unmatched_items={item_id})

        item = await _get(ctx, item_id)
        assert item.printer_id is None
        assert item.manual_start is True
        assert item.waiting_reason == f"Needs {_BROWN_TEXT}"

    @pytest.mark.asyncio
    async def test_model_wait_reasons_carry_no_suffix(self, ctx, stored):
        await self._off(ctx, stored)
        item_id = await _add_item(ctx, age_hours=500)

        result = await _run(ctx, PrintScheduler(), unmatched_on={1, 2, 3})

        reason = (await _get(ctx, item_id)).waiting_reason
        assert reason.startswith("Waiting for filament: X1C-1 (needs PLA #FFFFFF)")
        assert "priority" not in reason
        assert result.waiting.await_count == 1


class TestMatcherShortIsMissing:
    async def _find(self, ctx, scheduler, short, **kwargs):
        async def is_short(pid):
            return pid in short

        with (
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
            patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
        ):
            async with ctx.session_maker() as db:
                return await scheduler._find_idle_printer_for_model(db, "X1C", set(), is_short=is_short, **kwargs)

    @pytest.mark.asyncio
    async def test_default_keeps_the_short_fallback(self, ctx):
        assert await self._find(ctx, PrintScheduler(), {1, 2, 3}) == (1, None)

    @pytest.mark.asyncio
    async def test_short_printers_are_missing_not_a_fallback(self, ctx):
        printer_id, reason = await self._find(ctx, PrintScheduler(), {1, 2, 3}, short_is_missing=True)
        assert printer_id is None
        assert reason == (
            "Waiting for filament: X1C-1 (needs more filament); X1C-2 (needs more filament); "
            "X1C-3 (needs more filament)"
        )

    @pytest.mark.asyncio
    async def test_a_full_printer_still_wins(self, ctx):
        assert await self._find(ctx, PrintScheduler(), {1}, short_is_missing=True) == (2, None)

    @pytest.mark.asyncio
    async def test_colour_ranking_does_not_fall_back_to_a_short_printer(self, ctx):
        scheduler = PrintScheduler()
        overrides = [{"slot_id": 1, "type": "PLA", "color": "#FF0000"}]
        with patch.object(scheduler, "_count_override_color_matches", MagicMock(return_value=1)):
            printer_id, reason = await self._find(
                ctx, scheduler, {1, 2, 3}, short_is_missing=True, filament_overrides=overrides
            )
        assert printer_id is None
        assert reason.startswith("Waiting for filament: ")


# ---------------------------------------------------------------------------
# Fix round 1
# ---------------------------------------------------------------------------


class TestNotificationSurvivesBusyFlaps:
    """A busy hold overwrites the saved reason; the notification stays once."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("hours", "age"), [("48", 100), ("None", 1)])
    async def test_pinned_wait_busy_wait_notifies_once(self, ctx, hours, age):
        await _mode(ctx, hours=hours)
        item_id = await _add_item(ctx, printer_id=1, age_hours=age)
        scheduler = PrintScheduler()
        waiting = AsyncMock()

        for idle in (True, False, True, False, True):
            await _run(ctx, scheduler, waiting=waiting, deficit_items={item_id}, idle=lambda pid, idle=idle: idle)

        assert waiting.await_count == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("hours", "age"), [("48", 100), ("None", 1)])
    async def test_model_wait_busy_wait_notifies_once(self, ctx, hours, age):
        await _mode(ctx, hours=hours)
        item_id = await _add_item(ctx, age_hours=age)
        scheduler = PrintScheduler()
        waiting = AsyncMock()

        for idle in (True, False, True, False, True):
            await _run(ctx, scheduler, waiting=waiting, unmatched_on={1, 2, 3}, idle=lambda pid, idle=idle: idle)

        assert (await _get(ctx, item_id)).waiting_reason.startswith("Waiting for filament")
        assert waiting.await_count == 1

    @pytest.mark.asyncio
    async def test_the_priority_moment_still_notifies_after_a_start_notification(self, ctx):
        await _mode(ctx, hours="48")
        item_id = await _add_item(ctx, printer_id=1, age_hours=10)
        scheduler = PrintScheduler()
        waiting = AsyncMock()

        await _run(ctx, scheduler, waiting=waiting, deficit_items={item_id})
        await _run(ctx, scheduler, waiting=waiting, deficit_items={item_id}, idle=lambda pid: False)
        async with ctx.session_maker() as db:
            row = await db.get(PrintQueueItem, item_id)
            row.created_at = _naive_utc(49)
            await db.commit()
        await _run(ctx, scheduler, waiting=waiting, deficit_items={item_id})
        await _run(ctx, scheduler, waiting=waiting, deficit_items={item_id}, idle=lambda pid: False)
        await _run(ctx, scheduler, waiting=waiting, deficit_items={item_id})

        assert waiting.await_count == 1
        assert waiting.await_args.kwargs["waiting_reason"].endswith(" · has priority")

    @pytest.mark.asyncio
    async def test_the_memory_is_pruned_to_pending_items(self, ctx):
        await _mode(ctx, hours="None")
        item_id = await _add_item(ctx, printer_id=1)
        scheduler = PrintScheduler()
        await _run(ctx, scheduler, deficit_items={item_id})
        assert item_id in scheduler._filament_wait_notified

        async with ctx.session_maker() as db:
            row = await db.get(PrintQueueItem, item_id)
            row.status = "cancelled"
            await db.commit()
        await _add_item(ctx, printer_id=2)
        await _run(ctx, scheduler)

        assert item_id not in scheduler._filament_wait_notified

    @pytest.mark.asyncio
    async def test_the_memory_is_cleared_when_the_queue_empties(self, ctx):
        await _mode(ctx, hours="None")
        item_id = await _add_item(ctx, printer_id=1)
        scheduler = PrintScheduler()
        await _run(ctx, scheduler, deficit_items={item_id})
        assert scheduler._filament_wait_notified

        async with ctx.session_maker() as db:
            (await db.get(PrintQueueItem, item_id)).status = "cancelled"
            await db.commit()
        await _run(ctx, scheduler)

        assert scheduler._filament_wait_notified == {}


class TestSmartPlugWake:
    """A skipped pinned job must not switch its printer on every pass."""

    _PLUG = SimpleNamespace(
        id=1, name="plug", auto_on=True, enabled=True, printer_id=1, ha_entity_id=None, controls_printer_power=True
    )

    def _patches(self, scheduler, shortfall=()):
        power_on = AsyncMock(return_value=False)
        return power_on, [
            patch.object(scheduler, "_get_smart_plugs", AsyncMock(return_value=[self._PLUG])),
            patch.object(scheduler, "_power_on_and_wait", power_on),
            patch.object(scheduler, "_cached_filament_shortfall", MagicMock(return_value=list(shortfall))),
        ]

    @pytest.mark.asyncio
    async def test_a_known_shortfall_is_not_woken(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx, printer_id=1, age_hours=10)
        scheduler = PrintScheduler()
        power_on, patches = self._patches(scheduler, shortfall=["PLA (Earth Brown)"])

        result = await _run(ctx, scheduler, connected=lambda pid: pid != 1, extra_patches=patches)

        power_on.assert_not_awaited()
        assert (await _get(ctx, item_id)).waiting_reason == "Waiting for filament: PLA (Earth Brown) · priority in 38 h"
        result.waiting.assert_not_awaited()

    @staticmethod
    def _cache(scheduler, trays_by_printer):
        """Last-known trays per printer: {pid: [tray types]}; absent means no reading."""

        def reading(pid):
            types = trays_by_printer.get(pid)
            if types is None:
                return None
            return {"ams": [{"tray": [{"tray_type": t} for t in types]}]}

        return patch.object(scheduler, "_tray_reading", MagicMock(side_effect=reading))

    def _real_patches(self, scheduler, trays_by_printer):
        power_on = AsyncMock(return_value=False)
        return power_on, [
            patch.object(scheduler, "_get_smart_plugs", AsyncMock(return_value=[self._PLUG])),
            patch.object(scheduler, "_power_on_and_wait", power_on),
            self._cache(scheduler, trays_by_printer),
        ]

    @pytest.mark.asyncio
    async def test_a_saved_filament_wait_alone_does_not_decline(self, ctx):
        """Only the printer's cached trays decline, never the reason an earlier pass saved."""
        await _mode(ctx)
        await _add_item(
            ctx, printer_id=1, age_hours=10, waiting_reason="Waiting for filament: PLA (40 g left, needs 120 g)"
        )
        scheduler = PrintScheduler()
        power_on, patches = self._patches(scheduler)

        await _run(ctx, scheduler, connected=lambda pid: pid != 1, extra_patches=patches)

        power_on.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_print_anyway_wakes(self, ctx):
        await _mode(ctx)
        await _add_item(ctx, printer_id=1, age_hours=10, skip_filament_check=True)
        scheduler = PrintScheduler()
        power_on, patches = self._patches(scheduler, shortfall=["PETG"])

        await _run(ctx, scheduler, connected=lambda pid: pid != 1, extra_patches=patches)

        power_on.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_cached_shortfall_still_declines_across_passes(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx, printer_id=1, age_hours=10, required_filament_types=json.dumps(["PETG"]))
        scheduler = PrintScheduler()
        power_on, patches = self._real_patches(scheduler, {1: ["PLA"]})

        await _run(ctx, scheduler, connected=lambda pid: pid != 1, extra_patches=patches)
        await _run(ctx, scheduler, connected=lambda pid: pid != 1, extra_patches=patches)

        power_on.assert_not_awaited()
        assert (await _get(ctx, item_id)).waiting_reason == "Waiting for filament: PETG · priority in 38 h"

    @pytest.mark.asyncio
    async def test_a_job_moved_to_a_printer_with_matching_cached_trays_wakes(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx, printer_id=1, age_hours=10, required_filament_types=json.dumps(["PETG"]))
        scheduler = PrintScheduler()
        power_on, patches = self._real_patches(scheduler, {1: ["PLA"], 2: ["PETG"]})
        await _run(ctx, scheduler, connected=lambda pid: pid not in (1, 2), extra_patches=patches)
        power_on.assert_not_awaited()

        async with ctx.session_maker() as db:
            (await db.get(PrintQueueItem, item_id)).printer_id = 2
            await db.commit()
        plug = SimpleNamespace(**{**vars(self._PLUG), "printer_id": 2})
        patches.append(patch.object(scheduler, "_get_smart_plugs", AsyncMock(return_value=[plug])))
        await _run(ctx, scheduler, connected=lambda pid: pid not in (1, 2), extra_patches=patches)

        power_on.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_edited_overrides_that_match_the_cached_trays_wake(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx, printer_id=1, age_hours=10, required_filament_types=json.dumps(["PETG"]))
        scheduler = PrintScheduler()
        power_on, patches = self._real_patches(scheduler, {1: ["PLA"]})
        await _run(ctx, scheduler, connected=lambda pid: pid != 1, extra_patches=patches)
        power_on.assert_not_awaited()

        async with ctx.session_maker() as db:
            (await db.get(PrintQueueItem, item_id)).required_filament_types = json.dumps(["PLA"])
            await db.commit()
        await _run(ctx, scheduler, connected=lambda pid: pid != 1, extra_patches=patches)

        power_on.assert_awaited_once()

    @staticmethod
    def _colour_cache(scheduler, trays):
        """Last-known trays of printer 1 as (type, hex colour) pairs."""
        reading = {"ams": [{"tray": [{"tray_type": t, "tray_color": c} for t, c in trays]}]}
        return patch.object(
            scheduler, "_tray_reading", MagicMock(side_effect=lambda pid: reading if pid == 1 else None)
        )

    @pytest.mark.asyncio
    async def test_an_unforced_colour_override_does_not_decline(self, ctx):
        """A preferred colour is no reason to leave the printer off: switched on, it
        would map the white PLA and print, as it does with the mode off."""
        await _mode(ctx)
        overrides = [{"slot_id": 1, "type": "PLA", "color": "#FF0000", "force_color_match": False}]
        await _add_item(
            ctx,
            printer_id=1,
            age_hours=49,
            required_filament_types=json.dumps(["PLA"]),
            filament_overrides=json.dumps(overrides),
        )
        scheduler = PrintScheduler()
        power_on = AsyncMock(return_value=False)
        patches = [
            patch.object(scheduler, "_get_smart_plugs", AsyncMock(return_value=[self._PLUG])),
            patch.object(scheduler, "_power_on_and_wait", power_on),
            self._colour_cache(scheduler, [("PLA", "FFFFFFFF")]),
        ]

        await _run(ctx, scheduler, connected=lambda pid: pid != 1, extra_patches=patches)

        power_on.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_forced_colour_override_still_declines(self, ctx):
        await _mode(ctx)
        overrides = [{"slot_id": 1, "type": "PLA", "color": "#FF0000", "force_color_match": True}]
        item_id = await _add_item(
            ctx,
            printer_id=1,
            age_hours=10,
            required_filament_types=json.dumps(["PLA"]),
            filament_overrides=json.dumps(overrides),
        )
        scheduler = PrintScheduler()
        power_on = AsyncMock(return_value=False)
        patches = [
            patch.object(scheduler, "_get_smart_plugs", AsyncMock(return_value=[self._PLUG])),
            patch.object(scheduler, "_power_on_and_wait", power_on),
            self._colour_cache(scheduler, [("PLA", "FFFFFFFF")]),
        ]

        await _run(ctx, scheduler, connected=lambda pid: pid != 1, extra_patches=patches)

        power_on.assert_not_awaited()
        assert (await _get(ctx, item_id)).waiting_reason.startswith("Waiting for filament: PLA")

    @pytest.mark.asyncio
    async def test_no_cached_trays_wakes_once(self, ctx):
        await _mode(ctx)
        await _add_item(ctx, printer_id=1, age_hours=10, required_filament_types=json.dumps(["PETG"]))
        scheduler = PrintScheduler()
        power_on, patches = self._real_patches(scheduler, {})

        await _run(ctx, scheduler, connected=lambda pid: pid != 1, extra_patches=patches)

        power_on.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_the_declined_wait_is_notified_once_at_priority(self, ctx):
        await _mode(ctx)
        await _add_item(ctx, printer_id=1, age_hours=49)
        scheduler = PrintScheduler()
        power_on, patches = self._patches(scheduler, shortfall=["PLA (Earth Brown)"])

        result = await _run(ctx, scheduler, connected=lambda pid: pid != 1, extra_patches=patches)
        await _run(ctx, scheduler, waiting=result.waiting, connected=lambda pid: pid != 1, extra_patches=patches)

        power_on.assert_not_awaited()
        assert result.waiting.await_count == 1
        assert result.waiting.await_args.kwargs["waiting_reason"].endswith(" · has priority")

    @pytest.mark.asyncio
    async def test_the_declined_wait_is_notified_once_with_no_limit(self, ctx):
        await _mode(ctx, hours="None")
        await _add_item(ctx, printer_id=1)
        scheduler = PrintScheduler()
        power_on, patches = self._patches(scheduler, shortfall=["PLA (Earth Brown)"])

        result = await _run(ctx, scheduler, connected=lambda pid: pid != 1, extra_patches=patches)
        await _run(ctx, scheduler, waiting=result.waiting, connected=lambda pid: pid != 1, extra_patches=patches)

        assert result.waiting.await_count == 1

    @pytest.mark.asyncio
    async def test_a_prioritised_job_keeps_its_offline_printer_free(self, ctx):
        await _mode(ctx)
        waiting_item = await _add_item(ctx, printer_id=1, position=1, age_hours=49)
        later = await _add_item(ctx, printer_id=1, position=2, age_hours=1)
        scheduler = PrintScheduler()
        power_on, patches = self._patches(scheduler, shortfall=["PLA (Earth Brown)"])

        await _run(ctx, scheduler, connected=lambda pid: pid != 1, extra_patches=patches)

        power_on.assert_not_awaited()
        assert (await _get(ctx, waiting_item)).waiting_reason.endswith(" · has priority")
        assert (await _get(ctx, later)).waiting_reason.startswith("Busy: X1C-1")

    @pytest.mark.asyncio
    async def test_a_job_that_has_not_met_its_printer_wakes_it(self, ctx):
        await _mode(ctx)
        await _add_item(ctx, printer_id=1, age_hours=10)
        scheduler = PrintScheduler()
        power_on, patches = self._patches(scheduler)

        await _run(ctx, scheduler, connected=lambda pid: pid != 1, extra_patches=patches)

        power_on.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mode_off_wakes_as_before(self, ctx):
        await _mode(ctx, on=False)
        await _add_item(ctx, printer_id=1, age_hours=10)
        scheduler = PrintScheduler()
        power_on, patches = self._patches(scheduler, shortfall=["PLA (Earth Brown)"])

        await _run(ctx, scheduler, connected=lambda pid: pid != 1, extra_patches=patches)

        power_on.assert_awaited_once()


class TestReservationDetails:
    @pytest.mark.asyncio
    async def test_a_reserved_printer_is_not_kept_warm(self, ctx):
        await _mode(ctx)
        await _add_item(ctx, printer_id=1, position=1, age_hours=49)
        await _add_item(ctx, printer_id=1, position=2, age_hours=1)
        scheduler = PrintScheduler()
        keep_warm = AsyncMock()

        await _run(
            ctx,
            scheduler,
            deficit_items={1},
            extra_patches=[patch.object(scheduler, "_apply_keep_warm", keep_warm)],
        )

        busy_for_keep_warm = keep_warm.await_args.args[3]
        assert 1 not in busy_for_keep_warm

    @pytest.mark.asyncio
    async def test_the_reservation_skips_a_printer_whose_last_print_failed(self, ctx):
        await _mode(ctx)
        waiting_item = await _add_item(ctx, position=1, age_hours=49, require_previous_success=True)
        second = await _add_item(ctx, position=2, age_hours=1)
        pinned_to_two = await _add_item(ctx, position=3, printer_id=2, age_hours=1)
        scheduler = PrintScheduler()

        async def previous_success(db, item, printer_id=None):
            return printer_id != 1

        result = await _run(
            ctx,
            scheduler,
            short={waiting_item: {1, 2, 3}},
            extra_patches=[patch.object(scheduler, "_check_previous_success", AsyncMock(side_effect=previous_success))],
        )

        # Printer 1 failed its last print, so 2 is the one kept free: the second
        # job takes 1, and a job pinned to 2 reads busy.
        assert _dispatched(result) == [second]
        assert (await _get(ctx, second)).printer_id == 1
        assert (await _get(ctx, pinned_to_two)).waiting_reason.startswith("Busy: X1C-2")

    @pytest.mark.asyncio
    async def test_a_released_race_is_not_announced_as_assigned(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx, age_hours=10)

        result = await _run(ctx, PrintScheduler(), deficit_items={item_id})

        assert (await _get(ctx, item_id)).printer_id is None
        result.assigned.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_assigned_job_is_still_announced(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx, age_hours=10)

        result = await _run(ctx, PrintScheduler())

        assert _dispatched(result) == [item_id]
        result.assigned.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shortest_first_and_the_mode_together(self, ctx):
        """A priority job that a shorter one jumped keeps the printer, then wins it."""
        await _mode(ctx)
        await _set(ctx, "queue_shortest_first", "true")
        long_job = await _add_item(ctx, printer_id=1, position=1, age_hours=10, print_time_seconds=5000)
        short_job = await _add_item(ctx, printer_id=1, position=2, age_hours=1, print_time_seconds=100)
        scheduler = PrintScheduler()

        # Pass 1: the long job lacks its filament and is skipped; a shorter one runs.
        first = await _run(ctx, scheduler, deficit_items={long_job})
        assert _dispatched(first) == [short_job]
        assert (await _get(ctx, long_job)).been_jumped is True

        third = await _add_item(ctx, printer_id=1, position=3, age_hours=1, print_time_seconds=50)
        async with ctx.session_maker() as db:
            (await db.get(PrintQueueItem, short_job)).status = "completed"
            (await db.get(PrintQueueItem, long_job)).created_at = _naive_utc(49)
            await db.commit()

        # Pass 2: still no filament, and now it has priority: the shorter job
        # behind it does not take the printer.
        second = await _run(ctx, scheduler, deficit_items={long_job})
        assert _dispatched(second) == []
        assert (await _get(ctx, long_job)).waiting_reason.endswith(" · has priority")
        assert (await _get(ctx, third)).waiting_reason.startswith("Busy: X1C-1")

        # Pass 3: the filament is loaded and the jumped job goes first.
        third_pass = await _run(ctx, scheduler)
        assert _dispatched(third_pass) == [long_job]


class TestMatcherShortWording:
    async def _find(self, ctx, scheduler, short, colour_missing, **kwargs):
        async def is_short(pid):
            return pid in short

        def missing_slots(pid, overrides, raw=None):
            return ["PLA (Red)"] if pid in colour_missing else []

        with (
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
            patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
            patch.object(scheduler, "_get_missing_force_color_slots", MagicMock(side_effect=missing_slots)),
        ):
            async with ctx.session_maker() as db:
                return await scheduler._find_idle_printer_for_model(
                    db,
                    "X1C",
                    set(),
                    is_short=is_short,
                    short_is_missing=True,
                    filament_overrides=[{"slot_id": 1, "type": "PLA", "color": "#FF0000", "force_color_match": True}],
                    **kwargs,
                )

    @pytest.mark.asyncio
    async def test_short_printers_stay_out_of_the_colours_to_load(self, ctx):
        printer_id, reason = await self._find(ctx, PrintScheduler(), short={2, 3}, colour_missing={1})
        assert printer_id is None
        assert reason == (
            "No matching material/color. Waiting on PLA (Red) | "
            "Waiting for filament: X1C-2 (needs more filament); X1C-3 (needs more filament)"
        )

    @pytest.mark.asyncio
    async def test_only_short_printers_read_as_waiting_for_filament(self, ctx):
        printer_id, reason = await self._find(ctx, PrintScheduler(), short={1, 2, 3}, colour_missing=set())
        assert printer_id is None
        assert reason == (
            "Waiting for filament: X1C-1 (needs more filament); X1C-2 (needs more filament); "
            "X1C-3 (needs more filament)"
        )

    @pytest.mark.asyncio
    async def test_colours_alone_read_as_before(self, ctx):
        printer_id, reason = await self._find(ctx, PrintScheduler(), short=set(), colour_missing={1, 2, 3})
        assert printer_id is None
        assert reason == "No matching material/color. Waiting on PLA (Red)"


class TestOptionalIntRange:
    @pytest.mark.asyncio
    async def test_a_value_outside_the_range_falls_back_to_the_default(self, ctx):
        scheduler = PrintScheduler()
        for stored, expected in (("0", 48), ("8761", 48), ("1", 1), ("8760", 8760), (" 12 ", 12)):
            await _set(ctx, "queue_max_wait_hours", stored)
            async with ctx.session_maker() as db:
                got = await scheduler._get_optional_int_setting(
                    db, "queue_max_wait_hours", 48, min_value=1, max_value=8760
                )
            assert got == expected


# ---------------------------------------------------------------------------
# A job held by its creator's filament rules (#3522) is not a filament wait
# ---------------------------------------------------------------------------

_PETG = [{"type": "PETG", "tray_info_idx": "GFG03"}]
_NOT_ALLOWED = file_hold_message(_PETG)


def _restricted_creator(scheduler, violation):
    """The job's creator is restricted, and ``violation`` is what the file check finds."""
    return [
        patch.object(
            scheduler, "_creator_filament_scope", AsyncMock(return_value=SimpleNamespace(is_unrestricted=False))
        ),
        patch.object(scheduler, "_filament_scope_violation", violation),
    ]


class TestHeldByFilamentRules:
    """Loading a spool does not lift a permission hold, so the mode leaves it alone.

    No countdown, no priority, no printer kept free and no filament-wait
    notification: the row reads as it does with the mode off. A job gets them
    only where it also waits for a filament to be loaded.
    """

    def test_no_rule_hold_reads_as_a_filament_wait(self):
        for reason in (
            _NOT_ALLOWED,
            spool_hold_message(_PETG),
            unlisted_file_hold_message(_PETG),
            print_anyway_hold_message([{"type": "PLA"}], _PETG),
        ):
            assert not _is_filament_wait(reason), reason

    @pytest.mark.asyncio
    @pytest.mark.parametrize("on", [True, False])
    async def test_a_model_job_its_creator_may_not_print_reads_the_same_in_both_modes(self, ctx, on):
        await _mode(ctx, on=on)
        held = await _add_item(ctx, position=1, age_hours=49)
        pinned = await _add_item(ctx, position=2, printer_id=1, age_hours=1)
        scheduler = PrintScheduler()

        async def violation(db, item):
            return _NOT_ALLOWED if item.id == held else None

        result = await _run(
            ctx,
            scheduler,
            extra_patches=_restricted_creator(scheduler, AsyncMock(side_effect=violation)),
        )

        item = await _get(ctx, held)
        assert (item.printer_id, item.waiting_reason) == (None, _NOT_ALLOWED)
        # Past the maximum wait, and still no printer is kept free for it.
        assert _dispatched(result) == [pinned]
        assert scheduler._filament_wait_notified == {}
        result.assigned.assert_not_awaited()
        # The ordinary waiting notification, as with the mode off.
        result.waiting.assert_awaited_once()
        assert result.waiting.await_args.kwargs["waiting_reason"] == _NOT_ALLOWED

    @pytest.mark.asyncio
    async def test_a_variant_that_does_wait_for_filament_still_counts_down(self, ctx):
        """X1C is not allowed, P1S only lacks the spool: that part is a filament wait."""
        await _mode(ctx)
        item_id = await _add_item(ctx, variants=["X1C", "P1S"], age_hours=10)
        scheduler = PrintScheduler()

        async def violation(db, item):
            return _NOT_ALLOWED if item.target_model == "X1C" else None

        await _run(
            ctx,
            scheduler,
            unmatched_on={4, 5},
            extra_patches=_restricted_creator(scheduler, AsyncMock(side_effect=violation)),
        )

        reason = (await _get(ctx, item_id)).waiting_reason
        assert _NOT_ALLOWED in reason
        assert "Waiting for filament: " in reason
        assert reason.endswith(" · priority in 38 h")

    @pytest.mark.asyncio
    async def test_a_pinned_job_is_held_for_the_rule_not_skipped_for_filament(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx, printer_id=1, age_hours=49)
        scheduler = PrintScheduler()

        result = await _run(ctx, scheduler, deficit_items={item_id}, unmappable=FilamentScopeHold(_NOT_ALLOWED))

        assert (await _get(ctx, item_id)).waiting_reason == _NOT_ALLOWED
        assert scheduler._filament_wait_notified == {}
        result.waiting.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_model_job_given_back_for_the_rule_is_not_announced(self, ctx):
        await _mode(ctx)
        item_id = await _add_item(ctx, age_hours=49)
        scheduler = PrintScheduler()

        result = await _run(ctx, scheduler, deficit_items={item_id}, unmappable=FilamentScopeHold(_NOT_ALLOWED))

        item = await _get(ctx, item_id)
        assert (item.printer_id, item.waiting_reason) == (None, _NOT_ALLOWED)
        assert scheduler._filament_wait_notified == {}
        result.assigned.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_switched_off_printer_is_woken_as_with_the_mode_off(self, ctx):
        """The cached trays lack the filament, but the job is not waiting for a spool."""
        await _mode(ctx)
        item_id = await _add_item(ctx, printer_id=1, age_hours=49)
        scheduler = PrintScheduler()
        power_on, patches = TestSmartPlugWake()._patches(scheduler, shortfall=["PETG"])
        patches += _restricted_creator(scheduler, AsyncMock(return_value=_NOT_ALLOWED))

        await _run(ctx, scheduler, connected=lambda pid: pid != 1, extra_patches=patches)

        power_on.assert_awaited_once()
        assert "Waiting for filament" not in ((await _get(ctx, item_id)).waiting_reason or "")
        assert scheduler._filament_wait_notified == {}


class TestAssignmentAnnouncedOnce:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("on", [True, False])
    async def test_one_announcement_per_assignment_in_both_modes(self, ctx, on):
        await _mode(ctx, on=on)
        item_id = await _add_item(ctx, age_hours=10)

        result = await _run(ctx, PrintScheduler())

        assert _dispatched(result) == [item_id]
        result.assigned.assert_awaited_once()
        assert result.assigned.await_args.kwargs["printer_id"] == 1


class TestReservationAndPlateClear:
    """The printer kept free is one the plate-clear gate lets print (#2860)."""

    @pytest.fixture(autouse=True)
    def overrides(self):
        plate_clear.load_overrides({})
        yield plate_clear
        plate_clear.load_overrides({})

    @staticmethod
    async def _reserved(setting: bool) -> int | None:
        """Printers 1 and 2 are both finished and awaiting plate-clear."""
        scheduler = PrintScheduler()
        printers = [SimpleNamespace(id=pid, name=f"X1C-{pid}", model="X1C") for pid in (1, 2)]
        pm = MagicMock()
        pm.is_connected.return_value = True
        pm.get_status.return_value = MagicMock(state="FINISH")
        pm.is_awaiting_plate_clear.return_value = True
        with (
            patch("backend.app.services.print_scheduler.printer_manager", pm),
            patch("backend.app.services.print_scheduler._wrong_nozzle_label", MagicMock(return_value=None)),
            patch.object(scheduler, "_printers_for_model", AsyncMock(return_value=printers)),
        ):
            reserved = await scheduler._reservable_printer_for_model(
                None,
                [SimpleNamespace(target_model="X1C", sliced_nozzle=None)],
                set(),
                SimpleNamespace(target_location=None, require_previous_success=False),
                None,
                setting,
            )
        return reserved.id if reserved else None

    @pytest.mark.asyncio
    async def test_the_app_setting_holds_every_printer(self):
        assert await self._reserved(True) is None
        assert await self._reserved(False) == 1

    @pytest.mark.asyncio
    async def test_a_printer_turned_off_is_kept_free_with_the_setting_on(self, overrides):
        overrides.set_override(2, False)
        assert await self._reserved(True) == 2

    @pytest.mark.asyncio
    async def test_a_printer_turned_on_is_passed_over_with_the_setting_off(self, overrides):
        overrides.set_override(1, True)
        assert await self._reserved(False) == 2
