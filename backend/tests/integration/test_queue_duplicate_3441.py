"""Duplicate queued and printing jobs (#3441).

POST /queue/duplicate clones each job the way an order's remaining runs are
cloned, so a copy keeps what the queue response cannot carry (its project, an
H2C rack pick, a virtual printer job's slicer mapping) and an "any printer of
a model" copy leaves the original's printer-specific answers behind.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.app.core import database as _database_module
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem, PrintQueueVariant
from backend.app.models.project import Project
from backend.tests.integration import test_queue_review_1620 as review

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _item(db_session, **fields) -> PrintQueueItem:
    item = PrintQueueItem(**{"status": "pending", "position": 1, **fields})
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    return item


async def _library_file(db_session, filename: str) -> LibraryFile:
    library_file = LibraryFile(filename=filename, file_path=f"library/files/{filename}", file_type="3mf", file_size=1)
    db_session.add(library_file)
    await db_session.commit()
    await db_session.refresh(library_file)
    return library_file


async def _load(item_id: int) -> PrintQueueItem:
    async with _database_module.async_session() as session:
        return (await session.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))).scalar_one()


async def _duplicate(async_client, item_ids: list[int], headers: dict | None = None):
    response = await async_client.post("/api/v1/queue/duplicate", json={"item_ids": item_ids}, headers=headers or {})
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_copy_keeps_the_job_and_starts_a_new_run(async_client, db_session, printer_factory, archive_factory):
    printer = await printer_factory()
    archive = await archive_factory(printer.id)
    project = Project(name="Order 42")
    db_session.add(project)
    await db_session.commit()
    other = await _item(db_session, printer_id=printer.id, archive_id=archive.id, position=7)
    original = await _item(
        db_session,
        printer_id=printer.id,
        archive_id=archive.id,
        project_id=project.id,
        plate_id=2,
        ams_mapping="[4, -1]",
        slicer_ams_mapping="[4]",
        nozzle_rack_choice='{"1": 3}',
        skip_filament_check=True,
        timelapse=True,
        manual_start=True,
        scheduled_time=datetime.now() + timedelta(days=1),
        status="printing",
    )

    body = await _duplicate(async_client, [original.id])

    assert body["skipped_count"] == 0
    (copy_id,) = body["created_ids"]
    copy = await _load(copy_id)
    assert copy.id != original.id
    assert (copy.status, copy.position) == ("pending", other.position + 1)
    assert (copy.printer_id, copy.archive_id, copy.plate_id) == (printer.id, archive.id, 2)
    # Not in the queue response, so a copy made in the browser lost them.
    assert copy.project_id == project.id
    assert copy.slicer_ams_mapping == "[4]"
    assert copy.nozzle_rack_choice == '{"1": 3}'
    # The same printer, so its tray pick and "Print Anyway" still apply.
    assert (copy.ams_mapping, copy.skip_filament_check) == ("[4, -1]", True)
    assert (copy.timelapse, copy.manual_start) == (True, True)
    assert copy.scheduled_time is None


async def test_an_any_model_copy_leaves_the_picked_printer_behind(
    async_client, db_session, printer_factory, archive_factory
):
    printer = await printer_factory()
    archive = await archive_factory(printer.id)
    original = await _item(
        db_session,
        printer_id=printer.id,
        target_model="X1C",
        archive_id=archive.id,
        ams_mapping="[2]",
        skip_filament_check=True,
        status="printing",
    )

    (copy_id,) = (await _duplicate(async_client, [original.id]))["created_ids"]

    copy = await _load(copy_id)
    assert (copy.printer_id, copy.target_model) == (None, "X1C")
    # Worked out for the printer the original runs on.
    assert (copy.ams_mapping, copy.skip_filament_check) == (None, False)


async def test_a_copy_is_not_one_of_the_orders_runs(async_client, db_session, printer_factory, archive_factory):
    printer = await printer_factory()
    archive = await archive_factory(printer.id)
    order = await async_client.post(
        "/api/v1/queue/batches",
        json={"name": "Order", "archive_id": archive.id, "plates": [{"plate_id": 1, "quantity_target": 1}]},
    )
    original = await _item(db_session, printer_id=printer.id, archive_id=archive.id, batch_id=order.json()["id"])

    (copy_id,) = (await _duplicate(async_client, [original.id]))["created_ids"]

    assert (await _load(copy_id)).batch_id is None


async def test_a_scheduler_hold_is_not_copied(async_client, db_session, printer_factory, archive_factory):
    printer = await printer_factory()
    archive = await archive_factory(printer.id)
    short = await _item(
        db_session, printer_id=printer.id, archive_id=archive.id, manual_start=True, filament_short=True
    )
    unmatched = await _item(
        db_session, printer_id=printer.id, archive_id=archive.id, manual_start=True, waiting_reason="Needs PLA Red"
    )
    staged = await _item(db_session, printer_id=printer.id, archive_id=archive.id, manual_start=True)

    created = (await _duplicate(async_client, [short.id, unmatched.id, staged.id]))["created_ids"]

    # Staged by the scheduler, a copy would wait for good (#3137); the user's
    # own "wait for manual start" is theirs to keep.
    assert [(await _load(i)).manual_start for i in created] == [False, False, True]


async def test_cross_model_candidates_are_copied(async_client, db_session):
    first = await _library_file(db_session, "a.gcode.3mf")
    second = await _library_file(db_session, "b.gcode.3mf")
    original = await _item(db_session, target_model="X1C")
    db_session.add_all(
        [
            PrintQueueVariant(queue_item_id=original.id, library_file_id=first.id, target_model="X1C", position=0),
            PrintQueueVariant(queue_item_id=original.id, library_file_id=second.id, target_model="H2D", position=1),
        ]
    )
    await db_session.commit()

    (copy_id,) = (await _duplicate(async_client, [original.id]))["created_ids"]

    async with _database_module.async_session() as session:
        variants = (
            (
                await session.execute(
                    select(PrintQueueVariant)
                    .where(PrintQueueVariant.queue_item_id == copy_id)
                    .order_by(PrintQueueVariant.position)
                )
            )
            .scalars()
            .all()
        )
    assert [(v.library_file_id, v.target_model) for v in variants] == [(first.id, "X1C"), (second.id, "H2D")]


async def test_jobs_that_cannot_be_copied_are_skipped(async_client, db_session, printer_factory, archive_factory):
    printer = await printer_factory()
    archive = await archive_factory(printer.id)
    upload = await _library_file(db_session, "card.gcode.3mf")
    queued = await _item(db_session, printer_id=printer.id, archive_id=archive.id)
    finished = await _item(db_session, printer_id=printer.id, archive_id=archive.id, status="completed")
    # A printer-card upload: its file is deleted when the original dispatches (#2819).
    card_upload = await _item(
        db_session, printer_id=printer.id, library_file_id=upload.id, cleanup_library_after_dispatch=True
    )

    body = await _duplicate(async_client, [finished.id, queued.id, card_upload.id, 999999, queued.id])

    assert len(body["created_ids"]) == 1
    assert body["skipped_count"] == 3


async def test_only_jobs_the_caller_may_see_and_queue(async_client, printer_factory, archive_factory):
    printer = await printer_factory(name="Lab")
    archive = await archive_factory(printer.id)
    admin = await review._admin_token(async_client)
    student, _ = await review._user(async_client, admin, "student", review.STUDENT)
    other, _ = await review._user(async_client, admin, "other", review.STUDENT)
    mine = await review._queue(async_client, review._auth(student), printer.id, archive.id)
    theirs = await review._queue(async_client, review._auth(other), printer.id, archive.id)

    body = await _duplicate(async_client, [mine["id"], theirs["id"]], review._auth(student))

    assert body["skipped_count"] == 1
    (copy_id,) = body["created_ids"]
    copy = await _load(copy_id)
    assert copy.created_by_id == (await _load(mine["id"])).created_by_id


async def test_a_copy_by_someone_who_needs_review_waits_for_it(async_client, printer_factory, archive_factory):
    printer = await printer_factory(name="Lab")
    archive = await archive_factory(printer.id)
    admin = await review._admin_token(async_client)
    student, _ = await review._user(async_client, admin, "student", review.STUDENT)
    # A job the student may start (say staff released it): their copy still waits.
    mine = await review._queue(async_client, review._auth(student), printer.id, archive.id)
    async with _database_module.async_session() as session:
        item = await session.get(PrintQueueItem, mine["id"])
        item.manual_start = False
        await session.commit()

    (copy_id,) = (await _duplicate(async_client, [mine["id"]], review._auth(student)))["created_ids"]

    assert await review._manual_start(copy_id) is True


async def test_duplicating_needs_queue_create(async_client, printer_factory, archive_factory):
    printer = await printer_factory(name="Lab")
    archive = await archive_factory(printer.id)
    admin = await review._admin_token(async_client)
    viewer, _ = await review._user(async_client, admin, "viewer", [p for p in review.STUDENT if p != "queue:create"])
    item = await review._queue(async_client, review._auth(admin), printer.id, archive.id)

    response = await async_client.post(
        "/api/v1/queue/duplicate", json={"item_ids": [item["id"]]}, headers=review._auth(viewer)
    )

    assert response.status_code == 403
