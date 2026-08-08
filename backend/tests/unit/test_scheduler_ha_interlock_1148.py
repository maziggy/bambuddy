"""The Home Assistant sensor interlock, seen from the scheduler (#1148).

The feature exists because the reporter wanted to know his enclosure was shut
before starting a print remotely. Displaying the door state only helps if he
looks; the interlock is what makes it act on its own.

It is a *hold*, never a failure: the item stays pending and dispatches by
itself once the door closes. And it holds only on a positive, freshly read
finding — a Home Assistant that is unreachable holds nothing, because a queue
that stops whenever an unrelated service goes down is worse than the problem.
"""

from contextlib import ExitStack
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
from backend.app.services.print_scheduler import PrintScheduler


@pytest.fixture
async def queue_db():
    """Two X1Cs, so a model-based job has somewhere else to go."""
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


async def _add_item(ctx, *, printer_id=None, target_model=None):
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
        item = PrintQueueItem(
            status="pending",
            position=1,
            printer_id=printer_id,
            target_model=target_model,
            library_file_id=lib.id,
        )
        db.add(item)
        await db.commit()
        return item.id


async def _run(ctx, scheduler, blocked, launched, finder=None, idle=True, drying=None):
    patches = [
        patch("backend.app.services.print_scheduler.async_session", ctx.session_maker),
        patch("backend.app.core.database.async_session", ctx.session_maker),
        patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
        patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=None)),
        patch(
            "backend.app.services.print_scheduler.ha_sensor_manager.blocked_printers",
            AsyncMock(return_value=blocked),
        ),
        patch(
            "backend.app.services.notification_service.notification_service.on_queue_job_waiting",
            AsyncMock(),
        ),
        patch(
            "backend.app.services.notification_service.notification_service.on_queue_job_assigned",
            AsyncMock(),
        ),
        patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=idle)),
        patch.object(scheduler, "_check_auto_drying", drying or AsyncMock()),
        patch.object(scheduler, "_ensure_ams_mapping", AsyncMock(return_value=None)),
        patch.object(scheduler, "_block_on_filament_deficit", AsyncMock(return_value=False)),
        patch.object(scheduler, "_launch_uploads", launched),
    ]
    if finder is not None:
        patches.append(patch.object(scheduler, "_find_idle_printer_for_model", finder))
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return await scheduler.check_queue()


async def _get_item(ctx, item_id):
    async with ctx.session_maker() as db:
        return (await db.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))).scalar_one()


class TestFixedPrinter:
    @pytest.mark.asyncio
    async def test_holds_the_item_and_says_why(self, queue_db):
        item_id = await _add_item(queue_db, printer_id=1)
        launched = MagicMock()

        await _run(queue_db, PrintScheduler(), {1: "Enclosure Door"}, launched)

        launched.assert_not_called()
        item = await _get_item(queue_db, item_id)
        assert item.status == "pending"
        assert item.waiting_reason == "Waiting on Enclosure Door"

    @pytest.mark.asyncio
    async def test_holding_is_not_failing(self, queue_db):
        """The door being open is a thing the user fixes in five seconds. The
        job must be there waiting when they do, not failed."""
        item_id = await _add_item(queue_db, printer_id=1)

        await _run(queue_db, PrintScheduler(), {1: "Enclosure Door"}, MagicMock())

        item = await _get_item(queue_db, item_id)
        assert item.status == "pending"
        assert item.error_message is None
        assert item.completed_at is None

    @pytest.mark.asyncio
    async def test_dispatches_once_the_hold_clears(self, queue_db):
        item_id = await _add_item(queue_db, printer_id=1)
        scheduler = PrintScheduler()
        await _run(queue_db, scheduler, {1: "Enclosure Door"}, MagicMock())

        launched = MagicMock()
        await _run(queue_db, scheduler, {}, launched)

        launched.assert_called_once()
        assert launched.call_args[0][0] == [item_id]

    @pytest.mark.asyncio
    async def test_the_stale_reason_is_cleared_on_dispatch(self, queue_db):
        """Otherwise the queue shows "Waiting on Enclosure Door" against a job
        that is already printing."""
        item_id = await _add_item(queue_db, printer_id=1)
        scheduler = PrintScheduler()
        await _run(queue_db, scheduler, {1: "Enclosure Door"}, MagicMock())

        await _run(queue_db, scheduler, {}, MagicMock())

        assert (await _get_item(queue_db, item_id)).waiting_reason is None

    @pytest.mark.asyncio
    async def test_the_reason_clears_even_when_the_printer_is_still_busy(self, queue_db):
        """You shut the door, but the printer is midway through something else.

        The hold has lifted and the queue must say so. Clearing the reason only
        at dispatch would leave a shut door reading "Waiting on Enclosure Door"
        for the rest of a ten-hour print.
        """
        item_id = await _add_item(queue_db, printer_id=1)
        scheduler = PrintScheduler()
        await _run(queue_db, scheduler, {1: "Enclosure Door"}, MagicMock())

        launched = MagicMock()
        await _run(queue_db, scheduler, {}, launched, idle=False)

        launched.assert_not_called()
        assert (await _get_item(queue_db, item_id)).waiting_reason is None

    @pytest.mark.asyncio
    async def test_another_printers_sensor_does_not_hold_this_one(self, queue_db):
        item_id = await _add_item(queue_db, printer_id=1)
        launched = MagicMock()

        await _run(queue_db, PrintScheduler(), {2: "Enclosure Door"}, launched)

        launched.assert_called_once()
        assert launched.call_args[0][0] == [item_id]

    @pytest.mark.asyncio
    async def test_nothing_blocked_dispatches_as_before(self, queue_db):
        item_id = await _add_item(queue_db, printer_id=1)
        launched = MagicMock()

        await _run(queue_db, PrintScheduler(), {}, launched)

        launched.assert_called_once()
        assert launched.call_args[0][0] == [item_id]

    @pytest.mark.asyncio
    async def test_a_held_printer_is_not_reported_as_busy(self, queue_db):
        """A held printer is idle, not printing, and the rest of the scheduler
        must keep seeing it that way.

        busy_printers looks like the obvious place to put a hold, but
        _check_auto_drying reads that set as "is currently printing" and would
        route an idle-but-held printer down the mid-print drying path — capped
        temperature, and past the queue-only gating. Auto-drying must see this
        printer exactly as it did before the interlock existed.
        """
        await _add_item(queue_db, printer_id=1)
        scheduler = PrintScheduler()
        drying = AsyncMock()

        await _run(queue_db, scheduler, {1: "Enclosure Door"}, MagicMock(), drying=drying)

        busy_printers = drying.await_args[0][2]
        assert 1 not in busy_printers

    @pytest.mark.asyncio
    async def test_a_failing_interlock_lookup_never_stops_the_queue(self, queue_db):
        """If the check itself breaks, the answer is "no holds", not "no prints"."""
        item_id = await _add_item(queue_db, printer_id=1)
        launched = MagicMock()
        broken = AsyncMock(side_effect=RuntimeError("HA sensor table is on fire"))

        with patch(
            "backend.app.services.print_scheduler.ha_sensor_manager.blocked_printers",
            broken,
        ):
            await _run(queue_db, PrintScheduler(), {}, launched)

        launched.assert_called_once()
        assert launched.call_args[0][0] == [item_id]


class TestModelBased:
    def _finder(self):
        """Stand-in matcher that respects busy_printers, as the real one does.

        That is the whole contract under test here: the interlock folds held
        printers into busy_printers, so a matcher that honours the set honours
        the interlock without knowing it exists.
        """

        async def finder(db, model, busy, *args, **kwargs):
            for printer_id in (1, 2):
                if printer_id not in busy:
                    return printer_id, None
            return None, "All printers busy"

        return finder

    @pytest.mark.asyncio
    async def test_an_interlocked_printer_is_passed_over_for_its_sibling(self, queue_db):
        """The whole reason the hold is folded into busy_printers: an "Any X1C"
        job should run on the printer whose door is shut, not queue behind the
        one whose door is open."""
        item_id = await _add_item(queue_db, target_model="X1C")
        launched = MagicMock()

        await _run(
            queue_db,
            PrintScheduler(),
            {1: "Enclosure Door"},
            launched,
            finder=self._finder(),
        )

        launched.assert_called_once()
        assert launched.call_args[0][0] == [item_id]
        assert (await _get_item(queue_db, item_id)).printer_id == 2

    @pytest.mark.asyncio
    async def test_every_printer_held_leaves_the_job_waiting(self, queue_db):
        item_id = await _add_item(queue_db, target_model="X1C")
        launched = MagicMock()

        await _run(
            queue_db,
            PrintScheduler(),
            {1: "Enclosure Door", 2: "Enclosure Door"},
            launched,
            finder=self._finder(),
        )

        launched.assert_not_called()
        item = await _get_item(queue_db, item_id)
        assert item.status == "pending"
        assert item.printer_id is None
