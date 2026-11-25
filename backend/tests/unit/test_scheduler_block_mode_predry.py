"""With drying set to block the queue, a job scheduled for later may still have
its printer pre-dried.

The block refused a new drying cycle on any printer with a pending job, and a
scheduled job is one, so queue-only drying -- whose purpose is drying ahead of
scheduled prints -- never dried anything in this mode. A job waiting for its
start time is not held up by a cycle meant to end before then; if the cycle is
still running when it is due, the hold applies as usual.
"""

from datetime import datetime, timedelta

import pytest

from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.print_scheduler import PrintScheduler
from backend.tests.unit.test_scheduler_model_job_drying_c5 import _considered_for_drying
from backend.tests.unit.test_scheduler_pinned_waiting_reason_3074 import (
    _add_item,
    _set,
    ctx,  # noqa: F401 - fixture
)


async def _schedule(ctx, item_id: int, delta: timedelta) -> None:
    async with ctx.session_maker() as db:
        item = await db.get(PrintQueueItem, item_id)
        item.scheduled_time = datetime.utcnow() + delta
        await db.commit()


@pytest.mark.asyncio
async def test_a_job_scheduled_for_later_lets_its_printer_pre_dry(ctx):
    await _set(ctx, "queue_drying_enabled", "true")
    await _set(ctx, "queue_drying_block", "true")
    item_id = await _add_item(ctx, printer_id=1)
    await _schedule(ctx, item_id, timedelta(hours=2))

    assert await _considered_for_drying(ctx, PrintScheduler()) == {1}


@pytest.mark.asyncio
async def test_a_job_that_could_start_now_still_blocks_a_new_cycle(ctx):
    await _set(ctx, "ambient_drying_enabled", "true")
    await _set(ctx, "queue_drying_block", "true")
    await _add_item(ctx, printer_id=1)

    assert await _considered_for_drying(ctx, PrintScheduler()) == set()


@pytest.mark.asyncio
async def test_a_job_whose_time_has_come_blocks_too(ctx):
    await _set(ctx, "ambient_drying_enabled", "true")
    await _set(ctx, "queue_drying_block", "true")
    item_id = await _add_item(ctx, printer_id=1)
    await _schedule(ctx, item_id, timedelta(minutes=-5))

    assert await _considered_for_drying(ctx, PrintScheduler()) == set()
