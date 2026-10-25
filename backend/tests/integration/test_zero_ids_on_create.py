"""An id of 0 on create means "not given", as on the matching edits.

Queue create, batch create and project create tested these ids for
truthiness: 0 skipped the existence check and was then stored as an id (a
server error on PostgreSQL, a link to nothing on SQLite). Batch create did not
check its archive or library file at all.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.library import LibraryFile
from backend.app.models.print_batch import PrintBatch
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.project import Project

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _library_file(db_session) -> int:
    row = LibraryFile(filename="part.gcode.3mf", file_path="library/part.gcode.3mf", file_size=5, file_type="3mf")
    db_session.add(row)
    await db_session.commit()
    return row.id


async def test_queueing_a_library_file_with_archive_0(async_client: AsyncClient, db_session, printer_factory):
    printer_id = (await printer_factory()).id
    file_id = await _library_file(db_session)

    response = await async_client.post(
        "/api/v1/queue/", json={"archive_id": 0, "library_file_id": file_id, "printer_id": printer_id}
    )

    assert response.status_code == 200, response.text
    db_session.expire_all()
    stored = (await db_session.execute(select(PrintQueueItem.archive_id, PrintQueueItem.library_file_id))).one()
    assert tuple(stored) == (None, file_id)


async def test_queueing_with_both_ids_0_is_refused(async_client: AsyncClient):
    response = await async_client.post("/api/v1/queue/", json={"archive_id": 0, "library_file_id": 0})

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Either archive_id or library_file_id must be provided"


async def test_a_batch_with_archive_0_has_no_archive(async_client: AsyncClient, db_session):
    file_id = await _library_file(db_session)

    response = await async_client.post(
        "/api/v1/queue/batches", json={"name": "B", "archive_id": 0, "library_file_id": file_id}
    )

    assert response.status_code == 200, response.text
    assert (response.json()["archive_id"], response.json()["library_file_id"]) == (None, file_id)


@pytest.mark.parametrize(
    ("field", "detail"), [("archive_id", "Archive not found"), ("library_file_id", "Library file not found")]
)
async def test_a_batch_for_an_unknown_file_is_refused(async_client: AsyncClient, db_session, field, detail):
    response = await async_client.post("/api/v1/queue/batches", json={"name": "B", field: 9999})

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == detail
    assert (await db_session.execute(select(PrintBatch.id))).first() is None


async def test_a_batch_for_a_known_archive_keeps_it(async_client: AsyncClient, printer_factory, archive_factory):
    archive = await archive_factory((await printer_factory()).id)

    response = await async_client.post("/api/v1/queue/batches", json={"name": "B", "archive_id": archive.id})

    assert response.status_code == 200, response.text
    assert response.json()["archive_id"] == archive.id


async def test_a_project_with_parent_0_has_no_parent(async_client: AsyncClient, db_session):
    response = await async_client.post("/api/v1/projects/", json={"name": "P", "parent_id": 0})

    assert response.status_code == 200, response.text
    assert response.json()["parent_id"] is None
    db_session.expire_all()
    assert (await db_session.execute(select(Project.parent_id))).scalar() is None
