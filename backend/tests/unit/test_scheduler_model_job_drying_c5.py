"""Drying applies to "Any <model>" jobs too (SP-C1).

The drying hold ("drying blocks the queue"), the stop before a print starts and
queue-only drying all lived in the branch for jobs pinned to one printer. A job
for "Any X1C" went straight to the matcher: it could start on an X1C whose AMS
was mid-dry with the hold switched on, the dry cycle was never stopped for it,
and queue-only drying never pre-dried anything for it.

Now a drying printer is passed over for a model job while the hold is on (the
job takes another printer of the model, or waits), the printer a model job
lands on has its drying stopped like a pinned job's, and a model job counts for
every printer of its model its creator may use when drying decides what to dry.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.core.printer_scope import PrinterScope
from backend.app.models.print_queue import PrintQueueItem, PrintQueueVariant
from backend.app.models.printer import Printer
from backend.app.services.print_scheduler import PrintScheduler
from backend.tests.unit.test_scheduler_model_filament_short_3137 import queue_db  # noqa: F401 - fixture
from backend.tests.unit.test_scheduler_pinned_waiting_reason_3074 import (
    _add_item,
    _item,
    _run,
    _set,
    ctx,  # noqa: F401 - fixture
)


async def _add_printer(ctx, printer_id: int, model: str = "X1C") -> None:
    async with ctx.session_maker() as db:
        db.add(
            Printer(
                id=printer_id,
                name=f"{model}-0{printer_id}",
                serial_number=f"{model}000{printer_id}",
                ip_address=f"10.0.0.{printer_id}",
                access_code="x",
                model=model,
                is_active=True,
            )
        )
        await db.commit()


def _dispatched_to(launched) -> dict[int, int]:
    return launched.call_args[0][1] if launched.called else {}


class TestTheHoldCoversModelJobs:
    @pytest.mark.asyncio
    async def test_a_drying_printer_is_passed_over_for_another_of_the_model(self, ctx):
        await _set(ctx, "queue_drying_block", "true")
        await _add_printer(ctx, 2)
        item_id = await _add_item(ctx, printer_id=None, target_model="X1C")
        scheduler = PrintScheduler()
        scheduler._drying_in_progress[1] = 1.0

        launched = await _run(ctx, scheduler, idle=True)

        assert _dispatched_to(launched).get(item_id) == 2

    @pytest.mark.asyncio
    async def test_it_waits_while_every_printer_of_the_model_dries(self, ctx):
        await _set(ctx, "queue_drying_block", "true")
        item_id = await _add_item(ctx, printer_id=None, target_model="X1C")
        scheduler = PrintScheduler()
        scheduler._drying_in_progress[1] = 1.0

        launched = await _run(ctx, scheduler, idle=True)

        launched.assert_not_called()
        item = await _item(ctx, item_id)
        assert item.printer_id is None
        # Busy-shaped: the cycle ends on its own, nothing to alert about. Named
        # like a pinned job's, so the wait reads as drying, not as printing.
        assert item.waiting_reason == "Busy: X1C-01 (drying)"

    @pytest.mark.asyncio
    async def test_a_parked_timer_does_not_hold_it(self, ctx):
        await _set(ctx, "queue_drying_block", "true")
        item_id = await _add_item(ctx, printer_id=None, target_model="X1C")
        scheduler = PrintScheduler()
        scheduler._drying_in_progress[1] = 1.0

        with (
            patch.object(PrintScheduler, "_drying_is_only_parked", staticmethod(lambda printer_id: True)),
            patch.object(scheduler, "_stop_drying", AsyncMock()),
            patch.object(scheduler, "_drying_may_continue_through_print", AsyncMock(return_value=True)),
        ):
            launched = await _run(ctx, scheduler, idle=True)

        assert _dispatched_to(launched).get(item_id) == 1

    @pytest.mark.asyncio
    async def test_without_the_hold_it_may_take_a_drying_printer(self, ctx):
        item_id = await _add_item(ctx, printer_id=None, target_model="X1C")
        scheduler = PrintScheduler()
        scheduler._drying_in_progress[1] = 1.0

        with (
            patch.object(scheduler, "_stop_drying", AsyncMock()),
            patch.object(scheduler, "_drying_may_continue_through_print", AsyncMock(return_value=True)),
        ):
            launched = await _run(ctx, scheduler, idle=True)

        assert _dispatched_to(launched).get(item_id) == 1


class TestDryingStopsForTheModelJobsPrint:
    @pytest.mark.asyncio
    async def test_the_printer_it_lands_on_stops_drying(self, ctx):
        await _add_item(ctx, printer_id=None, target_model="X1C")
        scheduler = PrintScheduler()
        scheduler._drying_in_progress[1] = 1.0
        stop = AsyncMock()

        with (
            patch.object(scheduler, "_stop_drying", stop),
            patch.object(scheduler, "_drying_may_continue_through_print", AsyncMock(return_value=False)),
        ):
            launched = await _run(ctx, scheduler, idle=True)

        launched.assert_called_once()
        stop.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_drying_that_may_continue_through_the_print_is_left_alone(self, ctx):
        await _add_item(ctx, printer_id=None, target_model="X1C")
        scheduler = PrintScheduler()
        scheduler._drying_in_progress[1] = 1.0
        stop = AsyncMock()

        with (
            patch.object(scheduler, "_stop_drying", stop),
            patch.object(scheduler, "_drying_may_continue_through_print", AsyncMock(return_value=True)),
        ):
            await _run(ctx, scheduler, idle=True)

        stop.assert_not_awaited()


async def _items(ctx) -> list[PrintQueueItem]:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    async with ctx.session_maker() as db:
        query = select(PrintQueueItem).options(
            selectinload(PrintQueueItem.variants).selectinload(PrintQueueVariant.library_file),
            selectinload(PrintQueueItem.archive),
            selectinload(PrintQueueItem.library_file),
        )
        return list((await db.execute(query)).scalars().all())


async def _considered_for_drying(ctx, scheduler, *, scope=None) -> set[int]:
    """The printers auto-drying looked at past the queue gates: each one it
    reaches is asked whether it is idle (answered no, so nothing starts)."""
    reached: set[int] = set()

    def idle(pid, require_plate_clear=False):
        reached.add(pid)
        return False

    state = SimpleNamespace(state="IDLE", firmware_version="01.09.00.00", raw_data={"ams": []})
    pm = MagicMock()
    pm.get_status.return_value = state
    pm.get_model.return_value = "X1C"
    pm.is_connected.return_value = True
    items = await _items(ctx)
    async with ctx.session_maker() as db:
        with (
            patch("backend.app.services.print_scheduler.printer_manager", pm),
            patch.object(scheduler, "_is_printer_idle", idle),
            patch("backend.app.core.auth.is_auth_enabled", AsyncMock(return_value=scope is not None)),
            patch(
                "backend.app.services.print_scheduler.resolve_user_id_printer_scope",
                AsyncMock(return_value=scope),
            ),
        ):
            await scheduler._check_auto_drying(db, items, set())
    return reached


class TestQueueOnlyDryingCountsModelJobs:
    @pytest.mark.asyncio
    async def test_a_scheduled_model_job_pre_dries_every_printer_of_its_model(self, ctx):
        from datetime import datetime, timedelta

        await _set(ctx, "queue_drying_enabled", "true")
        await _add_printer(ctx, 2)
        await _add_printer(ctx, 3, model="P1S")
        item_id = await _add_item(ctx, printer_id=None, target_model="X1C")
        async with ctx.session_maker() as db:
            item = await db.get(PrintQueueItem, item_id)
            item.scheduled_time = datetime.utcnow() + timedelta(hours=2)
            await db.commit()

        assert await _considered_for_drying(ctx, PrintScheduler()) == {1, 2}

    @pytest.mark.asyncio
    async def test_only_the_printers_its_creator_may_use(self, ctx):
        from datetime import datetime, timedelta

        await _set(ctx, "queue_drying_enabled", "true")
        await _add_printer(ctx, 2)
        item_id = await _add_item(ctx, printer_id=None, target_model="X1C")
        async with ctx.session_maker() as db:
            item = await db.get(PrintQueueItem, item_id)
            item.scheduled_time = datetime.utcnow() + timedelta(hours=2)
            item.created_by_id = 42
            await db.commit()

        reached = await _considered_for_drying(ctx, PrintScheduler(), scope=PrinterScope(frozenset({2})))

        assert reached == {2}

    @pytest.mark.asyncio
    async def test_a_cross_model_job_counts_every_model_it_was_sliced_for(self, ctx):
        from datetime import datetime, timedelta

        from backend.app.models.library import LibraryFile

        await _set(ctx, "queue_drying_enabled", "true")
        await _add_printer(ctx, 3, model="P1S")
        await _add_printer(ctx, 4, model="A1")
        item_id = await _add_item(ctx, printer_id=None, target_model=None)
        async with ctx.session_maker() as db:
            item = await db.get(PrintQueueItem, item_id)
            item.scheduled_time = datetime.utcnow() + timedelta(hours=2)
            for position, model in enumerate(("X1C", "P1S")):
                lib = LibraryFile(
                    filename=f"{model}.gcode.3mf", file_path=f"/library/{model}.gcode.3mf", file_size=1, file_type="3mf"
                )
                db.add(lib)
                await db.flush()
                db.add(
                    PrintQueueVariant(
                        queue_item_id=item_id, position=position, library_file_id=lib.id, target_model=model
                    )
                )
            await db.commit()

        assert await _considered_for_drying(ctx, PrintScheduler()) == {1, 3}

    @pytest.mark.asyncio
    async def test_an_unscheduled_model_job_pre_dries_nothing(self, ctx):
        await _set(ctx, "queue_drying_enabled", "true")
        await _add_item(ctx, printer_id=None, target_model="X1C")

        assert await _considered_for_drying(ctx, PrintScheduler()) == set()

    @pytest.mark.asyncio
    async def test_the_hold_keeps_drying_from_starting_where_a_model_job_waits(self, ctx):
        """As for a pinned job: with drying set to block the queue, a printer a
        pending job could take does not start a new cycle."""
        await _set(ctx, "ambient_drying_enabled", "true")
        await _set(ctx, "queue_drying_block", "true")
        await _add_printer(ctx, 3, model="P1S")
        await _add_item(ctx, printer_id=None, target_model="X1C")

        assert await _considered_for_drying(ctx, PrintScheduler()) == {3}


class TestALowFilamentHoldIsNotMovedOntoADryingPrinter:
    """The #3137 release moves a held model job to another printer of the
    model: not to one that drying holds, where it would wait the whole cycle."""

    @pytest.mark.asyncio
    async def test_it_goes_to_the_printer_that_is_not_drying(self, queue_db):
        from backend.app.models.settings import Settings
        from backend.tests.unit.test_scheduler_model_filament_short_3137 import (
            _add_item as add_held,
            _get_item,
            _run as run_held,
        )

        async with queue_db.session_maker() as db:
            db.add(Settings(key="queue_drying_block", value="true"))
            await db.commit()
        item_id = await add_held(queue_db, printer_id=1, manual_start=True, filament_short=True)
        scheduler = PrintScheduler()
        scheduler._drying_in_progress[2] = 1.0
        launched = MagicMock()

        with (
            patch.object(scheduler, "_stop_drying", AsyncMock()),
            patch.object(scheduler, "_drying_may_continue_through_print", AsyncMock(return_value=True)),
        ):
            await run_held(queue_db, scheduler, short={1}, launched=launched)

        assert (await _get_item(queue_db, item_id)).printer_id == 3
