"""Auto-drying versus the plate-clear hold, from check_queue (#2801).

A printer in FINISH whose plate has not been acknowledged, with something
pending in its queue, stopped and restarted AMS drying once per scheduler tick
for as long as the plate stayed unacknowledged -- roughly 2000 state changes
over ten days on the reporter's P2S. Drying never ran long enough to do
anything, and manual cycles on other AMS units of the same printer were killed
with it.

Two ideas were tangled together. Plate-clear answers "is the bed ready for the
next job"; it is not a statement about whether the AMS may heat. And the
"print takes priority" stop was reached only when the print was NOT going to
start, so it spent the drying cycle for nothing.

These tests drive the real check_queue so the wiring is covered end to end,
not just the predicates.
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core.database import Base
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.services.print_scheduler import PrintScheduler


@pytest.fixture
async def queue_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with session_maker() as db:
        db.add(
            Printer(
                id=1,
                name="P2S-1",
                serial_number="P2S0001",
                ip_address="10.0.0.1",
                access_code="x",
                model="P2S",
                is_active=True,
            )
        )
        await db.commit()

    try:
        yield SimpleNamespace(session_maker=session_maker)
    finally:
        await engine.dispose()


async def _add_item(ctx):
    async with ctx.session_maker() as db:
        lib = LibraryFile(
            filename="job.gcode.3mf",
            file_path="/library/job.gcode.3mf",
            file_size=10,
            file_type="gcode.3mf",
            file_metadata={"sliced_for_model": "P2S"},
        )
        db.add(lib)
        await db.flush()
        db.add(
            PrintQueueItem(
                status="pending",
                position=1,
                printer_id=1,
                library_file_id=lib.id,
            )
        )
        await db.commit()


async def _run(ctx, scheduler, *, idle, stop_drying, drying=None, deficit=False, launched=None):
    """One check_queue pass with the plate hold expressed through _is_printer_idle."""
    patches = [
        patch("backend.app.services.print_scheduler.async_session", ctx.session_maker),
        patch("backend.app.core.database.async_session", ctx.session_maker),
        patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
        patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=None)),
        patch(
            "backend.app.services.print_scheduler.ha_sensor_manager.blocked_printers",
            AsyncMock(return_value={}),
        ),
        patch(
            "backend.app.services.notification_service.notification_service.on_queue_job_waiting",
            AsyncMock(),
        ),
        patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=idle)),
        patch.object(scheduler, "_check_auto_drying", drying or AsyncMock()),
        patch.object(scheduler, "_ensure_ams_mapping", AsyncMock(return_value=None)),
        patch.object(scheduler, "_block_on_filament_deficit", AsyncMock(return_value=deficit)),
        patch.object(scheduler, "_launch_uploads", launched or MagicMock()),
        patch.object(scheduler, "_stop_drying", stop_drying),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return await scheduler.check_queue()


@pytest.mark.asyncio
async def test_drying_is_not_stopped_for_a_print_that_cannot_start(queue_db):
    """The reported loop. Plate unacknowledged, so nothing dispatches -- and
    stopping the cycle could not have changed that, because drying is not one
    of the things _is_printer_idle looks at."""
    await _add_item(queue_db)
    scheduler = PrintScheduler()
    scheduler._drying_in_progress[1] = 1.0
    stop = AsyncMock()

    await _run(queue_db, scheduler, idle=False, stop_drying=stop)

    stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_plate_held_printer_is_not_offered_to_drying_as_printing(queue_db):
    """It lands in busy_printers so the queue leaves it alone, but auto-drying
    is handed the narrow set and must not see it there -- otherwise it takes
    the mid-print path, which caps the temperature and skips the idle gate."""
    await _add_item(queue_db)
    scheduler = PrintScheduler()
    drying = AsyncMock()

    await _run(queue_db, scheduler, idle=False, stop_drying=AsyncMock(), drying=drying)

    dispatching = drying.await_args[0][2]
    assert 1 not in dispatching


@pytest.mark.asyncio
async def test_print_takes_priority_still_stops_drying_when_it_can_dispatch(queue_db):
    """The setting keeps its meaning: on hardware that cannot dry through a
    print, a dispatch that is actually going to happen stops the cycle."""
    await _add_item(queue_db)
    scheduler = PrintScheduler()
    scheduler._drying_in_progress[1] = 1.0
    stop = AsyncMock()

    with patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=False)):
        await _run(queue_db, scheduler, idle=True, stop_drying=stop)

    stop.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_block_mode_holds_the_print_and_keeps_the_cycle(queue_db):
    """queue_drying_block on: the print waits, and the cycle is never touched.

    The setting previously had no observable effect on dispatch -- both
    branches skipped the item anyway, and all it really decided was whether
    drying got needlessly killed. Now it does what it says.
    """
    await _add_item(queue_db)
    scheduler = PrintScheduler()
    scheduler._drying_in_progress[1] = 1.0
    stop = AsyncMock()
    launched = MagicMock()

    with patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)):
        await _run(queue_db, scheduler, idle=True, stop_drying=stop, launched=launched)

    stop.assert_not_awaited()
    assert not launched.called


@pytest.mark.asyncio
async def test_drying_survives_an_item_that_is_skipped_after_the_idle_check(queue_db):
    """The idle check is not the last thing that can stop a dispatch.

    A failed previous print, an unmappable item, a filament deficit or a
    contested library row all skip the item further down the loop. Deciding on
    drying before those is the same defect in a smaller costume: the cycle goes
    and the print still does not happen.
    """
    await _add_item(queue_db)
    scheduler = PrintScheduler()
    scheduler._drying_in_progress[1] = 1.0
    stop = AsyncMock()

    with patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=False)):
        # Deficit gate holds the item back, after the printer passed as idle.
        await _run(queue_db, scheduler, idle=True, stop_drying=stop, deficit=True)

    stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_capable_hardware_keeps_drying_through_the_print(queue_db):
    """#2758's finding stands: where the printer dries happily while printing
    and the user has allowed it, the cycle is left running."""
    await _add_item(queue_db)
    scheduler = PrintScheduler()
    scheduler._drying_in_progress[1] = 1.0
    stop = AsyncMock()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_drying_may_continue_through_print", AsyncMock(return_value=True)),
    ):
        await _run(queue_db, scheduler, idle=True, stop_drying=stop)

    stop.assert_not_awaited()
