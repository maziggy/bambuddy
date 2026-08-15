"""Scheduled drying through the real check_queue (#2638).

The dispatch logic is unit-tested by calling ``_check_scheduled_dryings``
directly. This drives the whole queue pass instead, because that method is now
called on every tick of the scheduler's hot path: what matters to an install
that never schedules a dry is that the pass still completes and still dispatches
prints, and what matters to one that does is that the two do not interfere.
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
from backend.app.models.scheduled_drying import ScheduledDrying
from backend.app.services.print_scheduler import PrintScheduler

pytestmark = pytest.mark.unit


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _state(dry_time=0):
    state = MagicMock()
    state.firmware_version = "01.09.00.00"
    state.raw_data = {"ams": [{"id": 0, "dry_time": dry_time, "dry_sf_reason": []}]}
    return state


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


async def _add_print_item(ctx):
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
        db.add(PrintQueueItem(status="pending", position=1, printer_id=1, library_file_id=lib.id))
        await db.commit()


async def _add_drying_row(ctx, **kwargs):
    async with ctx.session_maker() as db:
        defaults = {
            "printer_id": 1,
            "ams_id": 0,
            "temp": 65,
            "duration_hours": 8,
            "start_after": _utcnow_naive() - timedelta(minutes=1),
        }
        defaults.update(kwargs)
        row = ScheduledDrying(**defaults)
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row


async def _run(ctx, scheduler, *, state, launched=None):
    """One real check_queue pass with only the print-side collaborators mocked."""
    patches = [
        patch("backend.app.services.print_scheduler.async_session", ctx.session_maker),
        patch("backend.app.core.database.async_session", ctx.session_maker),
        patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
        patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=state)),
        patch(
            "backend.app.services.print_scheduler.printer_manager.send_drying_command",
            MagicMock(return_value=True),
        ),
        patch(
            "backend.app.services.print_scheduler.ha_sensor_manager.blocked_printers",
            AsyncMock(return_value={}),
        ),
        patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
        patch.object(scheduler, "_ensure_ams_mapping", AsyncMock(return_value=None)),
        patch.object(scheduler, "_block_on_filament_deficit", AsyncMock(return_value=False)),
        patch.object(scheduler, "_launch_uploads", launched or MagicMock()),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return await scheduler.check_queue()


@pytest.mark.asyncio
async def test_a_pass_with_no_scheduled_rows_still_dispatches_prints(queue_db):
    """The case every existing install is in: the feature is present and unused."""
    await _add_print_item(queue_db)
    scheduler = PrintScheduler()
    launched = MagicMock()

    await _run(queue_db, scheduler, state=_state(), launched=launched)

    launched.assert_called_once()
    assert launched.call_args[0][0]  # at least one dispatch id


@pytest.mark.asyncio
async def test_a_due_row_dispatches_through_the_real_queue_pass(queue_db):
    row = await _add_drying_row(queue_db)
    scheduler = PrintScheduler()

    await _run(queue_db, scheduler, state=_state())

    async with queue_db.session_maker() as db:
        stored = (await db.execute(select(ScheduledDrying).where(ScheduledDrying.id == row.id))).scalar_one()
        assert stored.status == "running"
    assert 1 in scheduler._drying_in_progress


@pytest.mark.asyncio
async def test_a_scheduled_row_does_not_stop_the_queue(queue_db):
    """A drying row and a print item in the same pass: the print still goes."""
    await _add_drying_row(queue_db)
    await _add_print_item(queue_db)
    scheduler = PrintScheduler()
    launched = MagicMock()

    await _run(queue_db, scheduler, state=_state(), launched=launched)

    launched.assert_called_once()
    assert launched.call_args[0][0]


@pytest.mark.asyncio
async def test_a_failed_row_does_not_stop_the_queue(queue_db):
    """An unsupported model fails the row at dispatch. That is the one path that
    writes an error mid-pass, so the print behind it must still dispatch."""
    async with queue_db.session_maker() as db:
        printer = (await db.execute(select(Printer).where(Printer.id == 1))).scalar_one()
        printer.model = "P1S"  # drying is screen-only here
        await db.commit()
    await _add_drying_row(queue_db)
    await _add_print_item(queue_db)
    scheduler = PrintScheduler()
    launched = MagicMock()

    await _run(queue_db, scheduler, state=_state(), launched=launched)

    async with queue_db.session_maker() as db:
        stored = (await db.execute(select(ScheduledDrying))).scalars().one()
        assert stored.status == "failed"
        assert stored.error_message
    launched.assert_called_once()
    assert launched.call_args[0][0]
