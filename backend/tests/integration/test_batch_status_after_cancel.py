"""An order or grouping reports itself finished however its last job ended."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from backend.app.models.print_queue import PrintQueueItem
from backend.tests.integration.test_print_batch_orders import _create_order, _queue_item

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _batch_status(client: AsyncClient, batch_id: int) -> str:
    response = await client.get(f"/api/v1/queue/batches/{batch_id}")
    assert response.status_code == 200, response.text
    return response.json()["status"]


async def test_a_grouping_whose_last_job_is_cancelled_is_cancelled(
    async_client: AsyncClient, printer_factory, archive_factory
):
    printer = await printer_factory()
    archive = await archive_factory(printer.id)
    ids = [
        (await async_client.post("/api/v1/queue/", json={"printer_id": printer.id, "archive_id": archive.id})).json()[
            "id"
        ]
        for _ in range(2)
    ]
    grouping = (await async_client.post("/api/v1/queue/batches", json={"name": "G", "item_ids": ids})).json()

    for item_id in ids:
        assert (await async_client.post(f"/api/v1/queue/{item_id}/cancel")).status_code == 200

    assert await _batch_status(async_client, grouping["id"]) == "cancelled"


async def test_an_order_whose_last_run_is_closed_by_the_sweep_is_completed(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    import backend.app.core.database as database
    from backend.app.services.print_scheduler import PrintScheduler

    printer = await printer_factory()
    archive = await archive_factory(printer.id)
    order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 1}])
    run = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)
    await db_session.execute(update(PrintQueueItem).where(PrintQueueItem.id == run["id"]).values(status="printing"))
    await db_session.commit()

    scheduler = PrintScheduler()
    scheduler._terminal_since[printer.id] = -10_000.0
    with (
        patch("backend.app.services.print_scheduler.async_session", database.async_session),
        patch(
            "backend.app.services.print_scheduler.printer_manager.get_status",
            MagicMock(return_value=SimpleNamespace(state="FINISH", connected=True)),
        ),
    ):
        await scheduler._close_stranded_printing_items()

    assert await _batch_status(async_client, order["id"]) == "completed"


async def test_a_grouping_whose_file_is_deleted_is_cancelled(async_client: AsyncClient, db_session, printer_factory):
    from backend.app.models.library import LibraryFile
    from backend.app.services.library_trash import release_queue_references

    printer = await printer_factory()
    file = LibraryFile(filename="part.3mf", file_path="part.3mf", file_type="3mf", file_size=1)
    db_session.add(file)
    await db_session.commit()
    item = PrintQueueItem(printer_id=printer.id, library_file_id=file.id, status="pending", position=1)
    db_session.add(item)
    await db_session.commit()
    grouping = (await async_client.post("/api/v1/queue/batches", json={"name": "G", "item_ids": [item.id]})).json()

    await release_queue_references(db_session, [file.id])
    await db_session.commit()

    assert await _batch_status(async_client, grouping["id"]) == "cancelled"


async def _grouping(client: AsyncClient, printer_id: int, archive_id: int) -> tuple[dict, list[int]]:
    ids = [
        (await client.post("/api/v1/queue/", json={"printer_id": printer_id, "archive_id": archive_id})).json()["id"]
        for _ in range(2)
    ]
    grouping = (await client.post("/api/v1/queue/batches", json={"name": "G", "item_ids": ids})).json()
    return grouping, ids


async def test_a_grouping_whose_last_job_fails_at_dispatch_is_finished(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    from backend.app.services.print_scheduler import PrintScheduler

    printer = await printer_factory()
    archive = await archive_factory(printer.id)
    grouping, (done, last) = await _grouping(async_client, printer.id, archive.id)
    await db_session.execute(update(PrintQueueItem).where(PrintQueueItem.id == done).values(status="completed"))
    await db_session.commit()

    await PrintScheduler()._fail_dispatch(db_session, await db_session.get(PrintQueueItem, last), "Printer not found")

    assert await _batch_status(async_client, grouping["id"]) == "completed"


async def test_a_grouping_whose_last_open_job_is_deleted_is_finished(
    async_client: AsyncClient, printer_factory, archive_factory
):
    printer = await printer_factory()
    archive = await archive_factory(printer.id)
    grouping, (first, last) = await _grouping(async_client, printer.id, archive.id)
    assert (await async_client.post(f"/api/v1/queue/{first}/cancel")).status_code == 200

    assert (await async_client.delete(f"/api/v1/queue/{last}")).status_code == 200

    assert await _batch_status(async_client, grouping["id"]) == "cancelled"


async def _settle(db_session, statuses: dict[int, str]) -> None:
    from backend.app.services.print_batch import refresh_batches_for_items

    for item_id, status in statuses.items():
        await db_session.execute(update(PrintQueueItem).where(PrintQueueItem.id == item_id).values(status=status))
    await refresh_batches_for_items(db_session, list(statuses))
    await db_session.commit()


@pytest.mark.parametrize(
    ("ends", "expected"),
    [
        (("failed", "failed"), "failed"),
        (("failed", "cancelled"), "failed"),
        (("failed", "skipped"), "active"),
        (("failed", "completed"), "completed"),
    ],
)
async def test_a_grouping_that_produced_nothing_but_failures_is_failed(
    async_client: AsyncClient, db_session, printer_factory, archive_factory, ends, expected
):
    printer = await printer_factory()
    archive = await archive_factory(printer.id)
    grouping, ids = await _grouping(async_client, printer.id, archive.id)

    await _settle(db_session, dict(zip(ids, ends, strict=True)))

    assert await _batch_status(async_client, grouping["id"]) == expected


async def test_a_failed_grouping_takes_a_new_job_and_is_open_again(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    printer = await printer_factory()
    archive = await archive_factory(printer.id)
    grouping, ids = await _grouping(async_client, printer.id, archive.id)
    await _settle(db_session, dict.fromkeys(ids, "failed"))

    added = await async_client.post(
        "/api/v1/queue/", json={"printer_id": printer.id, "archive_id": archive.id, "batch_id": grouping["id"]}
    )

    assert added.status_code == 200, added.text
    assert await _batch_status(async_client, grouping["id"]) == "active"


async def test_an_order_whose_runs_all_failed_still_owes_them(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    printer = await printer_factory()
    archive = await archive_factory(printer.id)
    order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 1}])
    run = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)

    await _settle(db_session, {run["id"]: "failed"})

    assert await _batch_status(async_client, order["id"]) == "active"
