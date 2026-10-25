"""Bulk edit leaves a row a dispatch claimed during the edit (DM/BULK).

The bulk edit checked the claim on the rows it loaded, then awaited cost and
budget checks per row before changing it. A row the scheduler claimed for
dispatch in between was changed anyway, so its printer or cost centre no
longer matched the upload already under way.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.finance import CostCenter
from backend.app.models.print_queue import PrintQueueItem

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_a_row_claimed_during_the_edit_is_skipped(
    async_client, db_session, test_engine, printer_factory, archive_factory
):
    printer = await printer_factory()
    other_printer = await printer_factory(name="Other")
    archive = await archive_factory(printer.id)
    center = CostCenter(name="Lab", is_active=True, is_private=False)
    db_session.add(center)
    claimed = PrintQueueItem(printer_id=printer.id, archive_id=archive.id, status="pending", target_model="X1C")
    free = PrintQueueItem(printer_id=printer.id, archive_id=archive.id, status="pending")
    db_session.add_all([claimed, free])
    await db_session.commit()
    other = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def dispatch_claims(*_args, exclude_queue_item_id=None, **_kwargs):
        if exclude_queue_item_id == claimed.id:
            async with other() as db:
                await db.execute(
                    update(PrintQueueItem)
                    .where(PrintQueueItem.id == claimed.id)
                    .values(dispatching_at=datetime.now(timezone.utc))
                )
                await db.commit()

    with patch("backend.app.api.routes.print_queue.validate_print_budget", AsyncMock(side_effect=dispatch_claims)):
        response = await async_client.patch(
            "/api/v1/queue/bulk",
            json={"item_ids": [claimed.id, free.id], "cost_center_id": center.id, "printer_id": other_printer.id},
        )

    assert response.status_code == 200, response.text
    assert (response.json()["updated_count"], response.json()["skipped_count"]) == (1, 1)
    async with other() as db:
        claimed_row = await db.get(PrintQueueItem, claimed.id)
        free_row = await db.get(PrintQueueItem, free.id)
    assert (claimed_row.printer_id, claimed_row.cost_center_id, claimed_row.target_model) == (printer.id, None, "X1C")
    assert (free_row.printer_id, free_row.cost_center_id) == (other_printer.id, center.id)
