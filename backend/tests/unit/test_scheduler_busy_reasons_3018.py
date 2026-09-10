"""The queue's per-printer summary says which fact took a printer out of the pass (#3018).

``busy_printers`` holds two opposite things: printers that cannot take work, and
printers this pass has claimed *for* work. The old summary called every one of
them "not available" and printed printer state read at log time rather than the
state the decision was made on. #3018's bundle shows both faults landing at once::

    Queue: printer 1 not available — connected=True, state=IDLE, awaiting_plate_clear=False
    Launching 1 upload(s) (pool 0/4 in flight)
    Starting queue item 18

That is the first line anyone greps when asking why an item did not go out, and
there it is, on the printer that just received one.

The dispatch in that trace is correct and these tests pin it as such: a print
scheduled for later does not reserve the printer, so an unscheduled item behind
it runs while the printer is free. Only the reporting changed.
"""

import asyncio
import logging
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
import backend.app.services.archive as archive_module
import backend.app.services.print_scheduler as scheduler_module
from backend.app.core.database import Base
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.services.print_scheduler import PrintScheduler

SCHEDULER_LOGGER = "backend.app.services.print_scheduler"


@pytest.fixture
async def one_printer(tmp_path):
    """One printer, and a factory for the queue items a test needs on it."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    base_dir = tmp_path / "farm"
    (base_dir / "archives").mkdir(parents=True, exist_ok=True)

    async with session_maker() as db:
        printer = Printer(
            name="Printer",
            serial_number="SERIAL",
            ip_address="10.0.0.1",
            access_code="access-code",
            model="X1C",
        )
        db.add(printer)
        await db.flush()
        printer_id = printer.id
        await db.commit()

    async def add_item(*, scheduled_in: timedelta | None = None, status: str = "pending", position: int = 0):
        async with session_maker() as db:
            archive_rel = Path("archives") / f"job-{position}.3mf"
            (base_dir / archive_rel).write_bytes(b"archive payload")
            archive = PrintArchive(
                printer_id=printer_id,
                filename=f"job-{position}.3mf",
                file_path=str(archive_rel),
                file_size=15,
                print_time_seconds=120,
                status="completed",
            )
            db.add(archive)
            await db.flush()
            item = PrintQueueItem(
                printer_id=printer_id,
                archive_id=archive.id,
                status=status,
                position=position,
                scheduled_time=(datetime.now(timezone.utc) + scheduled_in) if scheduled_in else None,
            )
            db.add(item)
            await db.commit()
            return item.id

    try:
        yield SimpleNamespace(
            session_maker=session_maker,
            base_dir=base_dir,
            printer_id=printer_id,
            add_item=add_item,
        )
    finally:
        await engine.dispose()


@asynccontextmanager
async def _scheduler(ctx, *, idle: bool):
    scheduler = PrintScheduler()

    def _real_spawn(coro, *, name=None):
        return asyncio.create_task(coro, name=name)

    patches = [
        patch.object(scheduler_module.settings, "base_dir", ctx.base_dir),
        patch.object(archive_module.settings, "base_dir", ctx.base_dir),
        patch.object(archive_module.settings, "archive_dir", ctx.base_dir / "archive"),
        patch("backend.app.services.print_scheduler.async_session", ctx.session_maker),
        patch("backend.app.core.database.async_session", ctx.session_maker),
        patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
        patch(
            "backend.app.services.print_scheduler.printer_manager.get_status",
            MagicMock(
                return_value=SimpleNamespace(state="IDLE" if idle else "RUNNING", subtask_id=None, gcode_file=None)
            ),
        ),
        patch(
            "backend.app.services.print_scheduler.printer_manager.is_awaiting_plate_clear",
            MagicMock(return_value=False),
        ),
        patch("backend.app.services.print_scheduler.printer_manager.start_print", MagicMock(return_value=True)),
        patch("backend.app.services.print_scheduler.printer_manager.set_awaiting_plate_clear", MagicMock()),
        patch("backend.app.services.print_scheduler.upload_file_async", AsyncMock(return_value=True)),
        patch("backend.app.services.print_scheduler.delete_file_async", AsyncMock(return_value=True)),
        patch(
            "backend.app.services.print_scheduler.get_ftp_retry_settings",
            AsyncMock(return_value=(False, 0, 0, 1.0)),
        ),
        patch("backend.app.services.print_scheduler.cache_3mf_download", MagicMock()),
        patch("backend.app.services.print_scheduler.spawn_background_task", _real_spawn),
        patch(
            "backend.app.services.notification_service.notification_service.on_queue_job_started",
            AsyncMock(),
        ),
        patch(
            "backend.app.services.notification_service.notification_service.on_queue_job_failed",
            AsyncMock(),
        ),
        patch("backend.app.services.mqtt_relay.mqtt_relay.on_queue_job_started", AsyncMock()),
        patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=idle)),
        patch.object(scheduler, "_propagate_owner_to_printer_manager", AsyncMock()),
        patch.object(scheduler, "_power_off_if_needed", AsyncMock()),
        patch.object(scheduler, "_preheat_and_soak", AsyncMock()),
        patch.object(scheduler, "_check_auto_drying", AsyncMock()),
        patch.object(scheduler, "_watchdog_print_start", AsyncMock()),
    ]
    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        yield scheduler
        tasks = [task for (task, _pid) in scheduler._inflight.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def _printer_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("Queue: printer")]


class TestAPrinterTheQueueIsUsing:
    """#3018's trace: an item goes out, and the summary must not call that unavailable."""

    @pytest.mark.asyncio
    async def test_a_reserved_printer_is_not_called_unavailable(self, one_printer, caplog):
        await one_printer.add_item(scheduled_in=timedelta(hours=6), position=0)
        await one_printer.add_item(position=1)

        with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
            async with _scheduler(one_printer, idle=True) as scheduler:
                await scheduler.check_queue()

        lines = _printer_lines(caplog)
        assert len(lines) == 1, f"expected one line for the one printer, got {lines}"
        assert "reserved" in lines[0]
        assert "selected for dispatch in this pass" in lines[0]
        assert "unavailable" not in lines[0], (
            "the printer that just received the item must not be reported as unable to take one"
        )

    @pytest.mark.asyncio
    async def test_the_scheduled_item_stays_behind_and_the_other_goes(self, one_printer, caplog):
        """The behaviour #3018 reported as the bug. It is the intended one.

        A print scheduled for later does not hold the printer until then -- it is
        skipped as 'scheduled_future' while an unscheduled item uses the idle
        printer. Pinned here because the report turned on the label, not on this.
        """
        scheduled_id = await one_printer.add_item(scheduled_in=timedelta(hours=6), position=0)
        queued_id = await one_printer.add_item(position=1)

        with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
            async with _scheduler(one_printer, idle=True) as scheduler:
                await scheduler.check_queue()

        assert any("'scheduled_future': 1" in r.getMessage() for r in caplog.records)
        async with one_printer.session_maker() as db:
            assert (await db.get(PrintQueueItem, scheduled_id)).status == "pending"
            assert (await db.get(PrintQueueItem, queued_id)).status != "pending"


class TestAPrinterThatCannotTakeWork:
    """The other half of the set still reports, and now says which fact stopped it."""

    @pytest.mark.asyncio
    async def test_an_unavailable_printer_names_the_reason(self, one_printer, caplog):
        await one_printer.add_item(position=0)

        with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
            async with _scheduler(one_printer, idle=False) as scheduler:
                await scheduler.check_queue()

        lines = _printer_lines(caplog)
        assert len(lines) == 1, f"expected one line, got {lines}"
        assert "unavailable" in lines[0]
        assert "not idle" in lines[0]
        assert "reserved" not in lines[0]

    @pytest.mark.asyncio
    async def test_the_live_fields_are_labelled_as_read_now(self, one_printer, caplog):
        """They stay, because a bundle reader wants them -- but not as the reason.

        The old line offered them as the explanation, which is how it came to
        print state=IDLE under the heading 'not available'.
        """
        await one_printer.add_item(position=0)

        with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
            async with _scheduler(one_printer, idle=False) as scheduler:
                await scheduler.check_queue()

        line = _printer_lines(caplog)[0]
        assert "(now: connected=True" in line
        assert line.index("not idle") < line.index("now:"), "the recorded reason leads, the live read follows"

    @pytest.mark.asyncio
    async def test_two_items_on_one_printer_report_once(self, one_printer, caplog):
        """One line per printer, not per item it turned away."""
        await one_printer.add_item(position=0)
        await one_printer.add_item(position=1)

        with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
            async with _scheduler(one_printer, idle=False) as scheduler:
                await scheduler.check_queue()

        assert len(_printer_lines(caplog)) == 1
