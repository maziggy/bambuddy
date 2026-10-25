"""A webhook reprint keeps the slicer's AMS slots, as a reprint from the UI does (F9).

An archive printed through the virtual printer remembers which AMS slots the
slicer picked on its printer. Queued again for that printer from the UI, the
job reuses them; queued through the webhook it did not, and the scheduler
matched slots by colour and type again, possibly other spools.
"""

import pytest
from sqlalchemy import select

from backend.app.models.print_queue import PrintQueueItem
from backend.tests.integration.test_queue_creation_paths_c4 import _webhook_key

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _archive(db_session, archive_factory, printer_id: int):
    archive = await archive_factory(printer_id)
    archive.extra_data = {"slicer_ams_mapping": {"printer_id": printer_id, "mapping": [2, 0]}}
    await db_session.commit()
    return archive


@pytest.mark.parametrize("for_its_printer", [True, False])
async def test_the_webhook_reuses_the_slots_for_the_printer_they_were_picked_on(
    async_client, db_session, printer_factory, archive_factory, for_its_printer
):
    printer = await printer_factory()
    other = await printer_factory(name="Other")
    archive = await _archive(db_session, archive_factory, printer.id)

    response = await async_client.post(
        "/api/v1/webhook/queue/add",
        json={"printer_id": printer.id if for_its_printer else other.id, "archive_id": archive.id},
        headers=await _webhook_key(db_session),
    )

    assert response.status_code == 200, response.text
    item = await db_session.scalar(select(PrintQueueItem).where(PrintQueueItem.id == response.json()["id"]))
    # Slot numbers only mean something on the printer they were picked on
    assert item.ams_mapping == ("[2, 0]" if for_its_printer else None)
