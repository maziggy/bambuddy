"""Smart-plug power-on for class-targeted queue items (#2786).

Powering a printer on for a queued job has existed since smart plugs did, but
only on the branch that handles an item pinned to one printer. An item queued
as "Any X1C" carries no ``printer_id``, takes the model-based branch, and that
branch's matcher drops an offline printer into a "Offline:" waiting reason
without ever looking at its plugs. With every matching printer switched off the
job sat pending indefinitely.

The reporter's log is the controlled experiment: the same item, same plug, same
Auto On setting, powered a printer on the moment they edited it onto a specific
printer -- and did nothing for the thirteen minutes before that.
"""

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core.database import Base
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.smart_plug import SmartPlug
from backend.app.services.print_scheduler import PrintScheduler


@pytest.fixture
async def queue_db():
    """Two X1Cs, each on its own plug, so "which one" is a real question."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with session_maker() as db:
        db.add_all(
            [
                Printer(
                    id=1,
                    name="X1C-1",
                    serial_number="X1C0001",
                    ip_address="10.0.0.1",
                    access_code="x",
                    model="X1C",
                    is_active=True,
                ),
                Printer(
                    id=2,
                    name="X1C-2",
                    serial_number="X1C0002",
                    ip_address="10.0.0.2",
                    access_code="x",
                    model="X1C",
                    is_active=True,
                ),
            ]
        )
        await db.commit()

    try:
        yield SimpleNamespace(session_maker=session_maker)
    finally:
        await engine.dispose()


async def _add_plug(ctx, printer_id, *, auto_on=True, enabled=True, name=None):
    async with ctx.session_maker() as db:
        plug = SmartPlug(
            name=name or f"Plug {printer_id}",
            plug_type="tasmota",
            ip_address=f"10.0.1.{printer_id}",
            printer_id=printer_id,
            enabled=enabled,
            auto_on=auto_on,
        )
        db.add(plug)
        await db.commit()
        return plug.id


async def _add_item(
    ctx, *, printer_id=None, target_model=None, sliced_for="X1C", position=1, scheduled_time=None, manual_start=False
):
    async with ctx.session_maker() as db:
        lib = LibraryFile(
            filename="job.gcode.3mf",
            file_path="/library/job.gcode.3mf",
            file_size=10,
            file_type="gcode.3mf",
            file_metadata={"sliced_for_model": sliced_for},
        )
        db.add(lib)
        await db.flush()
        item = PrintQueueItem(
            status="pending",
            position=position,
            printer_id=printer_id,
            target_model=target_model,
            library_file_id=lib.id,
            scheduled_time=scheduled_time,
            manual_start=manual_start,
        )
        db.add(item)
        await db.commit()
        return item.id


async def _run(
    ctx,
    scheduler,
    *,
    power_on=AsyncMock,
    connected=False,
    awaiting_plate_clear=(),
    require_plate_clear=True,
    launched=None,
):
    """Run one queue pass with every printer offline unless told otherwise.

    ``power_on`` is the patched ``_power_on_and_wait``; the tests assert on the
    printer ids it was called with, which is the whole behaviour under test.
    """
    power_on_mock = power_on() if isinstance(power_on, type) else power_on
    with ExitStack() as stack:
        for p in [
            patch("backend.app.services.print_scheduler.async_session", ctx.session_maker),
            patch("backend.app.core.database.async_session", ctx.session_maker),
            patch(
                "backend.app.services.print_scheduler.printer_manager.is_connected",
                MagicMock(side_effect=lambda pid: pid in connected if connected else False),
            ),
            patch(
                "backend.app.services.print_scheduler.printer_manager.is_awaiting_plate_clear",
                MagicMock(side_effect=lambda pid: pid in awaiting_plate_clear),
            ),
            patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=None)),
            patch(
                "backend.app.services.print_scheduler.ha_sensor_manager.blocked_printers",
                AsyncMock(return_value={}),
            ),
            patch(
                "backend.app.services.notification_service.notification_service.on_queue_job_waiting",
                AsyncMock(),
            ),
            patch(
                "backend.app.services.notification_service.notification_service.on_queue_job_assigned",
                AsyncMock(),
            ),
            patch.object(scheduler, "_check_auto_drying", AsyncMock()),
            patch.object(scheduler, "_ensure_ams_mapping", AsyncMock(return_value=None)),
            patch.object(scheduler, "_block_on_filament_deficit", AsyncMock(return_value=False)),
            patch.object(scheduler, "_launch_uploads", launched or MagicMock()),
            patch.object(scheduler, "_power_on_and_wait", power_on_mock),
            patch.object(
                scheduler,
                "_get_bool_setting",
                AsyncMock(
                    side_effect=lambda db, key, default=False: (
                        require_plate_clear if key == "require_plate_clear" else default
                    )
                ),
            ),
        ]:
            stack.enter_context(p)
        await scheduler.check_queue()
    return power_on_mock


async def _get_item(ctx, item_id):
    async with ctx.session_maker() as db:
        return (await db.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))).scalar_one()


def _woken_printer_ids(power_on_mock):
    """Printer ids ``_power_on_and_wait(plug, printer_id, db)`` was called for."""
    return [call.args[1] for call in power_on_mock.await_args_list]


class TestClassTargetWakesAPrinter:
    @pytest.mark.asyncio
    async def test_offline_printers_are_powered_on_for_an_any_model_job(self, queue_db):
        """The bug: this used to do nothing at all."""
        await _add_plug(queue_db, 1)
        await _add_plug(queue_db, 2)
        item_id = await _add_item(queue_db, target_model="X1C")

        power_on = await _run(queue_db, PrintScheduler(), power_on=AsyncMock(return_value=True))

        assert _woken_printer_ids(power_on) == [1]
        # Assignment is left to the next pass, once the printer has reported.
        item = await _get_item(queue_db, item_id)
        assert item.status == "pending"
        assert item.printer_id is None

    @pytest.mark.asyncio
    async def test_a_printer_awaiting_plate_clear_is_passed_over(self, queue_db):
        """Waking it buys nothing -- the plate-clear gate would hold it anyway.

        This is what the reporter's log shows after a fixed-printer wake: the
        printer booted and then reported ``awaiting_plate_clear=True`` every 30
        seconds for the next 80 minutes. The flag is Bambuddy-side and
        persisted, so it is readable while the printer is still switched off.
        """
        await _add_plug(queue_db, 1)
        await _add_plug(queue_db, 2)
        await _add_item(queue_db, target_model="X1C")

        power_on = await _run(
            queue_db,
            PrintScheduler(),
            power_on=AsyncMock(return_value=True),
            awaiting_plate_clear=(1,),
        )

        assert _woken_printer_ids(power_on) == [2]

    @pytest.mark.asyncio
    async def test_nothing_is_woken_when_every_candidate_awaits_plate_clear(self, queue_db):
        await _add_plug(queue_db, 1)
        await _add_plug(queue_db, 2)
        await _add_item(queue_db, target_model="X1C")

        power_on = await _run(
            queue_db,
            PrintScheduler(),
            power_on=AsyncMock(return_value=True),
            awaiting_plate_clear=(1, 2),
        )

        power_on.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_plate_clear_gate_off_wakes_anyway(self, queue_db):
        """With the gate disabled the flag is not a reason to skip a printer."""
        await _add_plug(queue_db, 1)
        await _add_item(queue_db, target_model="X1C")

        power_on = await _run(
            queue_db,
            PrintScheduler(),
            power_on=AsyncMock(return_value=True),
            awaiting_plate_clear=(1,),
            require_plate_clear=False,
        )

        assert _woken_printer_ids(power_on) == [1]

    @pytest.mark.asyncio
    async def test_at_most_one_printer_is_woken_per_pass(self, queue_db):
        """Each wake blocks the queue loop for the boot wait.

        Ten class-targeted jobs must not switch on ten printers inside one
        check; the next pass wakes the next one.
        """
        await _add_plug(queue_db, 1)
        await _add_plug(queue_db, 2)
        await _add_item(queue_db, target_model="X1C", position=1)
        await _add_item(queue_db, target_model="X1C", position=2)

        power_on = await _run(queue_db, PrintScheduler(), power_on=AsyncMock(return_value=True))

        assert _woken_printer_ids(power_on) == [1]

    @pytest.mark.asyncio
    async def test_a_failed_power_on_is_not_retried_in_the_same_pass(self, queue_db):
        await _add_plug(queue_db, 1)
        await _add_plug(queue_db, 2)
        item_a = await _add_item(queue_db, target_model="X1C", position=1)
        item_b = await _add_item(queue_db, target_model="X1C", position=2)

        power_on = await _run(queue_db, PrintScheduler(), power_on=AsyncMock(return_value=False))

        assert _woken_printer_ids(power_on) == [1]
        # A printer we failed to switch on is off, not busy. Calling it busy
        # would misdescribe it here and, because an all-busy reason is treated
        # as needing no user action, silence the notification as well.
        for item_id in (item_a, item_b):
            reason = (await _get_item(queue_db, item_id)).waiting_reason or ""
            assert "Busy" not in reason
            assert "Offline" in reason

    @pytest.mark.asyncio
    async def test_a_dead_plug_does_not_starve_its_siblings(self, queue_db):
        """One unreachable plug must not hold every sibling of its model hostage.

        Candidates are walked in id order and a pass spends only one power-on
        attempt, so without a cool-off the broken printer is picked again on
        every pass and the healthy one behind it is never reached. It also
        costs a full boot timeout out of each 30s pass, which delays the whole
        queue rather than just this job.
        """
        await _add_plug(queue_db, 1)
        await _add_plug(queue_db, 2)
        await _add_item(queue_db, target_model="X1C")

        scheduler = PrintScheduler()
        power_on = AsyncMock(return_value=False)
        await _run(queue_db, scheduler, power_on=power_on)
        await _run(queue_db, scheduler, power_on=power_on)

        assert _woken_printer_ids(power_on) == [1, 2]

    @pytest.mark.asyncio
    async def test_a_printer_is_tried_again_once_its_cooloff_expires(self, queue_db):
        """The skip is a cool-off, not a blacklist — a fixed plug is picked up."""
        await _add_plug(queue_db, 1)
        await _add_item(queue_db, target_model="X1C")

        scheduler = PrintScheduler()
        scheduler._wake_failure_cooloff = 0
        power_on = AsyncMock(return_value=False)
        await _run(queue_db, scheduler, power_on=power_on)
        await _run(queue_db, scheduler, power_on=power_on)

        assert _woken_printer_ids(power_on) == [1, 1]

    @pytest.mark.asyncio
    async def test_an_expired_cooloff_is_not_left_behind(self, queue_db):
        """The map holds one key per currently-failing printer, not per printer
        this process has ever failed to wake."""
        await _add_plug(queue_db, 1)
        await _add_item(queue_db, target_model="X1C")

        scheduler = PrintScheduler()
        scheduler._wake_failure_cooloff = 0
        await _run(queue_db, scheduler, power_on=AsyncMock(return_value=False))
        assert 1 in scheduler._wake_failures

        await _run(queue_db, scheduler, power_on=AsyncMock(return_value=True))
        assert scheduler._wake_failures == {}


class TestWhatIsNotWokenUp:
    @pytest.mark.asyncio
    async def test_a_printer_with_no_auto_on_plug_is_left_alone_and_said_so(self, queue_db):
        """The first question asked of the reporter was whether Auto On was on.

        "Offline" and "offline with no Auto On plug" are different problems and
        only the second is one the user has to go and fix, so they must not
        share a waiting reason.
        """
        await _add_plug(queue_db, 1, auto_on=False)
        await _add_plug(queue_db, 2, enabled=False)
        item_id = await _add_item(queue_db, target_model="X1C")

        power_on = await _run(queue_db, PrintScheduler(), power_on=AsyncMock(return_value=True))

        power_on.assert_not_awaited()
        item = await _get_item(queue_db, item_id)
        assert item.waiting_reason is not None
        assert "no Auto On smart plug" in item.waiting_reason
        assert "X1C-1" in item.waiting_reason and "X1C-2" in item.waiting_reason

    @pytest.mark.asyncio
    async def test_an_incompatible_file_never_wakes_anything(self, queue_db):
        """The cross-model gate (#2578) runs before the wake, not after it.

        Switching a printer on for a file that can never legally run on it is
        worse than leaving it off: the job still cannot start, and now the
        printer is drawing power.
        """
        await _add_plug(queue_db, 1)
        await _add_item(queue_db, target_model="X1C", sliced_for="A1")

        power_on = await _run(queue_db, PrintScheduler(), power_on=AsyncMock(return_value=True))

        power_on.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_job_scheduled_for_later_does_not_switch_anything_on_now(self, queue_db):
        """Otherwise a print set for 3am powers a printer up the moment it is queued."""
        await _add_plug(queue_db, 1)
        await _add_item(
            queue_db,
            target_model="X1C",
            scheduled_time=datetime.now(timezone.utc) + timedelta(hours=6),
        )

        power_on = await _run(queue_db, PrintScheduler(), power_on=AsyncMock(return_value=True))

        power_on.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_manual_start_job_does_not_switch_anything_on(self, queue_db):
        """Manual start means the user presses play; nothing happens until they do."""
        await _add_plug(queue_db, 1)
        await _add_item(queue_db, target_model="X1C", manual_start=True)

        power_on = await _run(queue_db, PrintScheduler(), power_on=AsyncMock(return_value=True))

        power_on.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_already_connected_printer_is_not_powered_on(self, queue_db):
        await _add_plug(queue_db, 1)
        await _add_plug(queue_db, 2)
        await _add_item(queue_db, target_model="X1C")

        power_on = await _run(
            queue_db,
            PrintScheduler(),
            power_on=AsyncMock(return_value=True),
            connected=(1, 2),
        )

        power_on.assert_not_awaited()


class TestFixedPrinterBranchStillWakes:
    @pytest.mark.asyncio
    async def test_an_item_pinned_to_a_printer_still_powers_it_on(self, queue_db):
        """The branch that always worked, pinned so a refactor cannot drop it."""
        await _add_plug(queue_db, 2)
        await _add_item(queue_db, printer_id=2)

        power_on = await _run(queue_db, PrintScheduler(), power_on=AsyncMock(return_value=True))

        assert _woken_printer_ids(power_on) == [2]
