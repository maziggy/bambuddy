"""The scheduler defers (never fails) a dispatch that hits a busy printer (#2598).

check_queue gates dispatch on _is_printer_idle(), but that treats FINISH as
idle and a printer can keep reporting FINISH for tens of seconds after it
accepted a project_file; a watchdog revert (#2555) also releases the dispatch
hold. So a re-selected item can reach _start_print while its printer has
actually started printing. Two guards keep that from cancelling the live job:

* pre-dispatch — before the FTP upload, a busy printer defers (item stays
  pending), so there is no wasted upload and no start command;
* post-dispatch — if the printer goes busy in the upload window and
  start_print() returns False, the item is reverted to pending (deferred), not
  marked failed.
"""

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
import backend.app.services.print_scheduler as scheduler_module
from backend.app.core.database import Base
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.services.print_scheduler import PrintScheduler


@pytest.fixture
async def dispatch_case(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    base_dir = tmp_path / "case"
    base_dir.mkdir()
    archive_rel = Path("archives") / "job.3mf"
    archive_abs = base_dir / archive_rel
    archive_abs.parent.mkdir(parents=True, exist_ok=True)
    archive_abs.write_bytes(b"archive payload")

    async with session_maker() as db:
        printer = Printer(
            name="Printer",
            serial_number="SERIAL",
            ip_address="127.0.0.1",
            access_code="access-code",
            model="A1MINI",
        )
        db.add(printer)
        await db.flush()
        archive = PrintArchive(
            printer_id=printer.id,
            filename="job.3mf",
            file_path=str(archive_rel),
            file_size=archive_abs.stat().st_size,
            status="completed",
        )
        db.add(archive)
        await db.flush()
        item = PrintQueueItem(printer_id=printer.id, archive_id=archive.id, status="pending")
        db.add(item)
        await db.commit()
        ids = SimpleNamespace(printer_id=printer.id, archive_id=archive.id, item_id=item.id)

    try:
        yield SimpleNamespace(session_maker=session_maker, base_dir=base_dir, ids=ids)
    finally:
        await engine.dispose()


def _base_patches(scheduler, ctx, upload_mock, start_print_mock, get_status):
    return [
        patch.object(scheduler_module.settings, "base_dir", ctx.base_dir),
        patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
        patch("backend.app.services.print_scheduler.printer_manager.get_status", get_status),
        patch("backend.app.services.print_scheduler.printer_manager.start_print", start_print_mock),
        patch("backend.app.services.print_scheduler.printer_manager.set_awaiting_plate_clear", MagicMock()),
        patch(
            "backend.app.services.print_scheduler.get_ftp_retry_settings",
            AsyncMock(return_value=(False, 0, 0, 1.0)),
        ),
        patch("backend.app.services.print_scheduler.delete_file_async", AsyncMock(return_value=True)),
        patch("backend.app.services.print_scheduler.upload_file_async", upload_mock),
        patch("backend.app.services.print_scheduler.cache_3mf_download", MagicMock()),
        patch("backend.app.services.print_scheduler.spawn_background_task", MagicMock()),
        patch.object(scheduler, "_propagate_owner_to_printer_manager", AsyncMock()),
        patch.object(scheduler, "_power_off_if_needed", AsyncMock()),
        patch.object(scheduler, "_preheat_and_soak", AsyncMock()),
    ]


async def _final_item(ctx):
    async with ctx.session_maker() as db:
        return await db.get(PrintQueueItem, ctx.ids.item_id)


@pytest.mark.asyncio
async def test_pre_dispatch_busy_defers_without_upload(dispatch_case):
    """Printer already RUNNING when _start_print begins → defer, no upload/start."""
    scheduler = PrintScheduler()
    upload = AsyncMock(return_value=True)
    start_print = MagicMock(return_value=True)
    get_status = MagicMock(return_value=SimpleNamespace(state="RUNNING", subtask_id=None, gcode_file=None))

    async with dispatch_case.session_maker() as db:
        item = await db.get(PrintQueueItem, dispatch_case.ids.item_id)
        with ExitStack() as stack:
            for p in _base_patches(scheduler, dispatch_case, upload, start_print, get_status):
                stack.enter_context(p)
            await scheduler._start_print(db, item)

    upload.assert_not_awaited()
    start_print.assert_not_called()
    final = await _final_item(dispatch_case)
    assert final.status == "pending", "a busy printer must defer the item, not consume it"


@pytest.mark.asyncio
async def test_post_dispatch_busy_reverts_to_pending_not_failed(dispatch_case):
    """Printer goes busy in the upload window; start_print returns False → defer."""
    scheduler = PrintScheduler()
    upload = AsyncMock(return_value=True)

    holder = {"state": "IDLE"}

    class _Status:
        subtask_id = None
        gcode_file = None

        @property
        def state(self):
            return holder["state"]

    def _start_print(*args, **kwargs):
        # The printer became busy between the pre-dispatch check and the publish.
        holder["state"] = "RUNNING"
        return False  # start_print() refused: busy

    start_print = MagicMock(side_effect=_start_print)
    get_status = MagicMock(return_value=_Status())

    async with dispatch_case.session_maker() as db:
        item = await db.get(PrintQueueItem, dispatch_case.ids.item_id)
        with ExitStack() as stack:
            for p in _base_patches(scheduler, dispatch_case, upload, start_print, get_status):
                stack.enter_context(p)
            await scheduler._start_print(db, item)

    upload.assert_awaited_once()  # it proceeded past the (idle) pre-dispatch check
    start_print.assert_called_once()
    final = await _final_item(dispatch_case)
    assert final.status == "pending", "a busy-refused start must defer, not fail the item"
    assert final.started_at is None
