"""A job cancelled while it is being sent stays cancelled when the send then fails."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import update

from backend.app.models.print_queue import PrintQueueItem
from backend.tests.unit.test_scheduler_busy_reasons_3018 import _scheduler, one_printer  # noqa: F401

pytestmark = pytest.mark.asyncio


async def test_a_cancel_during_the_upload_is_not_turned_into_a_failure(one_printer):  # noqa: F811
    item_id = await one_printer.add_item()

    async def upload_then_cancelled(*args, **kwargs):
        # The user cancels while the file is on its way; then the upload fails.
        async with one_printer.session_maker() as other:
            await other.execute(update(PrintQueueItem).where(PrintQueueItem.id == item_id).values(status="cancelled"))
            await other.commit()
        return False

    failed = AsyncMock()
    toast = AsyncMock()
    async with _scheduler(one_printer, idle=True) as scheduler:
        with (
            patch("backend.app.services.print_scheduler.upload_file_async", upload_then_cancelled),
            patch("backend.app.services.notification_service.notification_service.on_queue_job_failed", failed),
            patch("backend.app.services.print_scheduler.ws_manager.send_queue_item_failed", toast),
        ):
            await scheduler.check_queue()
            for task, _pid in list(scheduler._inflight.values()):
                await task

    async with one_printer.session_maker() as db:
        item = await db.get(PrintQueueItem, item_id)
    assert item.status == "cancelled"
    failed.assert_not_awaited()
    # The dispatch toast is closed, not left spinning.
    assert toast.await_args.kwargs["reason"] == "cancelled_mid_dispatch"


async def test_a_failed_upload_still_fails_the_job(one_printer):  # noqa: F811
    item_id = await one_printer.add_item()

    async with _scheduler(one_printer, idle=True) as scheduler:
        with patch("backend.app.services.print_scheduler.upload_file_async", AsyncMock(return_value=False)):
            await scheduler.check_queue()
            for task, _pid in list(scheduler._inflight.values()):
                await task

    async with one_printer.session_maker() as db:
        item = await db.get(PrintQueueItem, item_id)
    assert item.status == "failed"


async def test_a_stop_while_the_printer_turns_busy_is_not_undone(db_session, printer_factory):
    from backend.app.services.print_scheduler import PrintScheduler

    printer = await printer_factory()
    stopped = PrintQueueItem(printer_id=printer.id, status="cancelled", position=0)
    printing = PrintQueueItem(printer_id=printer.id, status="printing", position=1)
    db_session.add_all([stopped, printing])
    await db_session.commit()
    stopped_id, printing_id = stopped.id, printing.id

    for item_id in (stopped_id, printing_id):
        await PrintScheduler._defer_until_printer_idle(db_session, await db_session.get(PrintQueueItem, item_id))

    db_session.expire_all()
    assert (await db_session.get(PrintQueueItem, stopped_id)).status == "cancelled"
    assert (await db_session.get(PrintQueueItem, printing_id)).status == "pending"
