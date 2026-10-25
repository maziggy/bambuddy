"""Archiving a pending upload into a project checks the project first.

The project id from the request was stored on the archive and the pending
upload unchecked, after the file had already been archived: an unknown project
failed with a server error on PostgreSQL (foreign key) with the archive files
already written, and was stored as a broken link on SQLite. 0 skipped the
archive but was stored on the pending upload.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.archive import PrintArchive
from backend.app.models.pending_upload import PendingUpload

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _pending(db_session, tmp_path) -> PendingUpload:
    file = tmp_path / "part.3mf"
    file.write_bytes(b"3mf")
    upload = PendingUpload(
        filename="part.3mf", file_path=str(file), file_size=3, source_ip="192.168.1.100", status="pending"
    )
    db_session.add(upload)
    await db_session.commit()
    await db_session.refresh(upload)
    return upload


async def test_an_unknown_project_is_refused_before_anything_is_archived(
    async_client: AsyncClient, db_session, tmp_path
):
    upload_id = (await _pending(db_session, tmp_path)).id

    with patch("backend.app.api.routes.pending_uploads.ArchiveService.archive_print", new=AsyncMock()) as archive:
        response = await async_client.post(f"/api/v1/pending-uploads/{upload_id}/archive", json={"project_id": 9999})

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Project not found"
    archive.assert_not_awaited()
    db_session.expire_all()
    row = (
        await db_session.execute(
            select(PendingUpload.status, PendingUpload.project_id).where(PendingUpload.id == upload_id)
        )
    ).one()
    assert tuple(row) == ("pending", None)
    assert (tmp_path / "part.3mf").exists()


async def _fake_archive_print(self, **_kwargs) -> PrintArchive:
    """Stands in for archiving a real 3MF: one archive row in the route's session."""
    archive = PrintArchive(filename="part.3mf", file_path="archive/part.3mf", file_size=3, status="archived")
    self.db.add(archive)
    await self.db.flush()
    return archive


@pytest.mark.parametrize("given", ["known", 0, None])
async def test_a_known_project_or_none_is_stored(async_client: AsyncClient, db_session, tmp_path, given):
    upload_id = (await _pending(db_session, tmp_path)).id
    project_id = None
    if given == "known":
        project_id = (await async_client.post("/api/v1/projects/", json={"name": "P"})).json()["id"]

    with patch("backend.app.api.routes.pending_uploads.ArchiveService.archive_print", new=_fake_archive_print):
        response = await async_client.post(
            f"/api/v1/pending-uploads/{upload_id}/archive",
            json={"project_id": project_id if given == "known" else given},
        )

    assert response.status_code == 200, response.text
    db_session.expire_all()
    stored = (
        await db_session.execute(select(PrintArchive.project_id).where(PrintArchive.id == response.json()["id"]))
    ).scalar()
    pending = (await db_session.execute(select(PendingUpload.project_id).where(PendingUpload.id == upload_id))).scalar()
    assert stored == project_id
    assert pending == project_id
