"""How a dispatch that cannot go ahead ends.

Before the upload: an exhausted cost-centre budget, a printer row that is gone
and a disconnected printer each fail the item with their own message, upload
nothing and power the printer off if it was powered on for the job. After the
upload: a start command the printer refused while it stayed idle fails the
item, deletes the uploaded file again and sends the failure notification. A
refusal because the printer turned busy defers instead (#2598, see
test_scheduler_busy_defer_2598).
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.print_scheduler import PrintScheduler
from backend.tests.unit.test_scheduler_busy_defer_2598 import _base_patches, _final_item, dispatch_case  # noqa: F401

IDLE = SimpleNamespace(state="IDLE", subtask_id=None, gcode_file=None)


async def _dispatch(ctx, *, start_print=None, extra=(), before=None):
    scheduler = PrintScheduler()
    upload = AsyncMock(return_value=True)
    start = start_print or MagicMock(return_value=True)
    failed = AsyncMock()
    async with ctx.session_maker() as db:
        item = await db.get(PrintQueueItem, ctx.ids.item_id)
        if before is not None:
            await before(db, item)
        with ExitStack() as stack:
            for p in _base_patches(scheduler, ctx, upload, start, MagicMock(return_value=IDLE)):
                stack.enter_context(p)
            for p in extra:
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "backend.app.services.notification_service.notification_service.on_queue_job_failed",
                    failed,
                )
            )
            delete = stack.enter_context(
                patch("backend.app.services.print_scheduler.delete_file_async", AsyncMock(return_value=True))
            )
            power_off = scheduler._power_off_if_needed
            await scheduler._start_print(db, item)
    return SimpleNamespace(
        power_off=power_off, upload=upload, start=start, delete=delete, failed=failed, item=await _final_item(ctx)
    )


class TestBeforeTheUpload:
    @pytest.mark.asyncio
    async def test_an_exhausted_budget(self, dispatch_case):  # noqa: F811
        refused = AsyncMock(side_effect=HTTPException(400, "Cost center budget exceeded"))

        result = await _dispatch(
            dispatch_case, extra=[patch("backend.app.services.print_scheduler.validate_print_budget", refused)]
        )

        assert (result.item.status, result.item.error_message) == ("failed", "Cost center budget exceeded")
        result.upload.assert_not_awaited()
        result.start.assert_not_called()
        result.power_off.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_printer_that_is_gone(self, dispatch_case):  # noqa: F811
        async def gone(db, item):
            item.printer_id = 9999
            await db.commit()

        result = await _dispatch(dispatch_case, before=gone)

        assert (result.item.status, result.item.error_message) == ("failed", "Printer not found")
        result.upload.assert_not_awaited()
        result.power_off.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_disconnected_printer(self, dispatch_case):  # noqa: F811
        offline = patch(
            "backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=False)
        )

        result = await _dispatch(dispatch_case, extra=[offline])

        assert (result.item.status, result.item.error_message) == ("failed", "Printer not connected")
        result.upload.assert_not_awaited()
        result.start.assert_not_called()
        result.power_off.assert_awaited_once()


class TestARefusedStartOnAnIdlePrinter:
    @pytest.mark.asyncio
    async def test_fails_the_item_and_removes_the_upload(self, dispatch_case):  # noqa: F811
        result = await _dispatch(dispatch_case, start_print=MagicMock(return_value=False))

        assert (result.item.status, result.item.error_message) == ("failed", "Failed to send print command to printer")
        result.upload.assert_awaited_once()
        uploaded_to = result.upload.await_args.args[3]
        # One clears the way before the upload, one removes the upload again
        assert [c.args[2] for c in result.delete.await_args_list] == [uploaded_to, uploaded_to]
        result.failed.assert_awaited_once()
        assert result.failed.await_args.kwargs["printer_id"] == dispatch_case.ids.printer_id
