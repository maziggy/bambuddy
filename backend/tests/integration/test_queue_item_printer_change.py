"""A queued job moved to another printer leaves the old printer's settings behind."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.print_queue import PrintQueueItem, PrintQueueVariant

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

RESOLVED_FOR_A = {
    "ams_mapping": "[2]",
    "skip_filament_check": True,
    "nozzle_mapping": json.dumps([1]),
    "nozzle_rack_choice": json.dumps({"0": 3}),
}


async def _item(db_session, printer_id: int, archive_id: int, **extra) -> int:
    item = PrintQueueItem(
        printer_id=printer_id,
        archive_id=archive_id,
        status="pending",
        position=1,
        plate_id=2,
        use_ams=False,
        **{**RESOLVED_FOR_A, **extra},
    )
    db_session.add(item)
    await db_session.commit()
    return item.id


async def _row(db_session, item_id: int) -> PrintQueueItem:
    db_session.expire_all()
    return await db_session.get(PrintQueueItem, item_id)


def _resolved(item: PrintQueueItem) -> tuple:
    return item.ams_mapping, item.skip_filament_check, item.nozzle_mapping, item.nozzle_rack_choice


async def test_a_bulk_move_drops_what_was_worked_out_for_the_old_printer(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    a_id, b_id = a.id, b.id
    archive_id = (await archive_factory(a_id)).id
    moved = await _item(db_session, a_id, archive_id)
    stays = await _item(db_session, b_id, archive_id)
    model_job = await _item(db_session, a_id, archive_id, target_model="X1C", target_location="Lab")

    response = await async_client.patch(
        "/api/v1/queue/bulk", json={"item_ids": [moved, stays, model_job], "printer_id": b_id}
    )

    assert response.status_code == 200, response.text
    item = await _row(db_session, moved)
    assert item.printer_id == b_id
    assert _resolved(item) == (None, False, None, None)
    assert (item.plate_id, item.use_ams) == (2, False)
    assert _resolved(await _row(db_session, stays)) == tuple(RESOLVED_FOR_A.values())
    pinned = await _row(db_session, model_job)
    assert (pinned.printer_id, pinned.target_model, pinned.target_location) == (b_id, None, None)


async def test_a_bulk_move_leaves_jobs_with_printer_alternatives_alone(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    a_id, b_id = a.id, b.id
    archive_id = (await archive_factory(a_id)).id
    item_id = await _item(db_session, a_id, archive_id, target_model="X1C")
    from backend.app.models.library import LibraryFile

    file = LibraryFile(filename="part.3mf", file_path="part.3mf", file_type="3mf", file_size=1)
    db_session.add(file)
    await db_session.flush()
    db_session.add(PrintQueueVariant(queue_item_id=item_id, position=0, target_model="X1C", library_file_id=file.id))
    await db_session.commit()

    response = await async_client.patch("/api/v1/queue/bulk", json={"item_ids": [item_id], "printer_id": b_id})

    assert response.json()["skipped_count"] == 1
    assert (await _row(db_session, item_id)).printer_id == a_id


async def test_editing_a_job_onto_another_printer_keeps_only_what_was_sent_for_it(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    a_id, b_id = a.id, b.id
    archive_id = (await archive_factory(a_id)).id
    item_id = await _item(db_session, a_id, archive_id)

    # What the edit dialog sends: the new printer's mapping, no Print Anyway.
    response = await async_client.patch(f"/api/v1/queue/{item_id}", json={"printer_id": b_id, "ams_mapping": [5]})

    assert response.status_code == 200, response.text
    item = await _row(db_session, item_id)
    assert _resolved(item) == ("[5]", False, None, None)


async def test_editing_a_job_on_the_same_printer_keeps_its_settings(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    a = await printer_factory(name="A")
    a_id = a.id
    archive_id = (await archive_factory(a_id)).id
    item_id = await _item(db_session, a_id, archive_id)

    response = await async_client.patch(f"/api/v1/queue/{item_id}", json={"printer_id": a_id})

    assert response.status_code == 200, response.text
    assert _resolved(await _row(db_session, item_id)) == tuple(RESOLVED_FOR_A.values())


async def test_copies_of_a_model_job_wait_for_any_printer_of_the_model(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    from backend.tests.integration.test_print_batch_orders import _create_order

    a = await printer_factory(name="A", model="X1C")
    a_id = a.id
    archive_id = (await archive_factory(a_id)).id
    order = await _create_order(async_client, archive_id, [{"plate_id": 2, "quantity_target": 3}])
    # Run 1 as the scheduler leaves it: its model, the printer it picked, and
    # what it worked out for that printer.
    source_id = await _item(db_session, a_id, archive_id, target_model="X1C", batch_id=order["id"])

    response = await async_client.post(f"/api/v1/queue/batches/{order['id']}/dispatch", json={})

    assert response.status_code == 200, response.text
    db_session.expire_all()
    copies = (
        (
            await db_session.execute(
                select(PrintQueueItem)
                .where(PrintQueueItem.batch_id == order["id"])
                .where(PrintQueueItem.id != source_id)
            )
        )
        .scalars()
        .all()
    )
    assert copies
    for copy in copies:
        assert (copy.printer_id, copy.target_model) == (None, "X1C")
        assert _resolved(copy) == (None, False, None, None)
        assert (copy.plate_id, copy.use_ams) == (2, False)
