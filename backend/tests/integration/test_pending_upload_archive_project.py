"""Approving a pending upload stores it in the File Manager, project checked first.

The project id from the request used to be stored unchecked, after the file
was already stored: an unknown project failed with a server error on
PostgreSQL (foreign key) with the files already written, and was kept as a
broken link on SQLite. 0 means no project.

Approving puts the file in the File Manager folder of the virtual printer it
came in on (review mode); the archive gets it only once it prints.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.library import LibraryFile, LibraryFolder
from backend.app.models.pending_upload import PendingUpload
from backend.app.models.virtual_printer import VirtualPrinter

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(autouse=True)
def _library_in_tmp(tmp_path, monkeypatch):
    """The File Manager writes below archive_dir: keep it out of the real one."""
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "archive_dir", tmp_path / "archive")


async def _pending(db_session, tmp_path, **extra) -> PendingUpload:
    file = tmp_path / "part.3mf"
    file.write_bytes(b"3mf")
    upload = PendingUpload(
        filename="part.3mf", file_path=str(file), file_size=3, source_ip="192.168.1.100", status="pending", **extra
    )
    db_session.add(upload)
    await db_session.commit()
    await db_session.refresh(upload)
    return upload


async def test_an_unknown_project_is_refused_before_anything_is_stored(async_client: AsyncClient, db_session, tmp_path):
    upload_id = (await _pending(db_session, tmp_path)).id

    response = await async_client.post(f"/api/v1/pending-uploads/{upload_id}/archive", json={"project_id": 9999})

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Project not found"
    db_session.expire_all()
    row = (
        await db_session.execute(
            select(PendingUpload.status, PendingUpload.project_id).where(PendingUpload.id == upload_id)
        )
    ).one()
    assert tuple(row) == ("pending", None)
    assert (tmp_path / "part.3mf").exists()
    assert (await db_session.execute(select(LibraryFile.id))).first() is None


@pytest.mark.parametrize("given", ["known", 0, None])
async def test_a_known_project_or_none_is_stored(async_client: AsyncClient, db_session, tmp_path, given):
    upload_id = (await _pending(db_session, tmp_path)).id
    project_id = None
    if given == "known":
        project_id = (await async_client.post("/api/v1/projects/", json={"name": "P"})).json()["id"]

    response = await async_client.post(
        f"/api/v1/pending-uploads/{upload_id}/archive",
        json={"project_id": project_id if given == "known" else given},
    )

    assert response.status_code == 200, response.text
    db_session.expire_all()
    stored = (
        await db_session.execute(select(LibraryFile.project_id).where(LibraryFile.id == response.json()["id"]))
    ).scalar()
    pending = (await db_session.execute(select(PendingUpload.project_id).where(PendingUpload.id == upload_id))).scalar()
    assert stored == project_id
    assert pending == project_id


async def test_approved_goes_to_the_folder_of_its_virtual_printer_with_notes_and_tags(
    async_client: AsyncClient, db_session, tmp_path
):
    folder = LibraryFolder(name="Class 7b", shared=True)
    db_session.add(folder)
    await db_session.flush()
    vp = VirtualPrinter(name="Classroom", mode="review", library_folder_id=folder.id)
    db_session.add(vp)
    await db_session.flush()
    upload_id = (await _pending(db_session, tmp_path, virtual_printer_id=vp.id)).id

    response = await async_client.post(
        f"/api/v1/pending-uploads/{upload_id}/archive",
        json={"notes": "for the science fair", "tags": "Bridge, school ,bridge"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["folder_id"] == folder.id
    db_session.expire_all()
    library_file = await db_session.get(LibraryFile, response.json()["id"])
    await db_session.refresh(library_file, ["tags"])
    assert library_file.filename == "part.3mf"
    assert library_file.source_type == "virtual_printer"
    assert library_file.notes == "for the science fair"
    assert sorted(tag.name for tag in library_file.tags) == ["Bridge", "school"]
    pending = await db_session.get(PendingUpload, upload_id)
    assert pending.status == "approved"
    assert not (tmp_path / "part.3mf").exists()


async def test_an_upload_without_its_virtual_printer_goes_to_the_shared_folder(
    async_client: AsyncClient, db_session, tmp_path
):
    upload_id = (await _pending(db_session, tmp_path)).id

    response = await async_client.post(f"/api/v1/pending-uploads/{upload_id}/archive")

    assert response.status_code == 200, response.text
    folder = await db_session.get(LibraryFolder, response.json()["folder_id"])
    assert folder.name == "Virtual Printer"
    assert folder.parent_id is None
    assert folder.shared is True


async def test_approve_all_stores_every_upload(async_client: AsyncClient, db_session, tmp_path):
    first_id = (await _pending(db_session, tmp_path)).id
    other = tmp_path / "other.3mf"
    other.write_bytes(b"other")
    db_session.add(
        PendingUpload(
            filename="other.3mf", file_path=str(other), file_size=5, source_ip="192.168.1.100", status="pending"
        )
    )
    gone = PendingUpload(
        filename="gone.3mf", file_path=str(tmp_path / "gone.3mf"), file_size=1, source_ip=None, status="pending"
    )
    db_session.add(gone)
    await db_session.commit()
    gone_id = gone.id

    response = await async_client.post("/api/v1/pending-uploads/archive-all")

    assert response.status_code == 200, response.text
    assert response.json() == {"stored": 2, "failed": 1}
    db_session.expire_all()
    names = sorted((await db_session.execute(select(LibraryFile.filename))).scalars())
    assert names == ["other.3mf", "part.3mf"]
    assert (await db_session.get(PendingUpload, first_id)).status == "approved"
    assert (await db_session.get(PendingUpload, gone_id)).status == "discarded"


async def test_approve_all_goes_on_after_one_upload_fails(async_client: AsyncClient, db_session, tmp_path):
    # A failed one is rolled back; the rest must still be stored (the
    # rollback used to leave the loop on expired rows: a 500).
    from unittest.mock import patch

    from backend.app.services.virtual_printer import library_store

    for name in ("a.3mf", "b.3mf", "c.3mf"):
        path = tmp_path / name
        path.write_bytes(name.encode())
        db_session.add(PendingUpload(filename=name, file_path=str(path), file_size=5, source_ip=None, status="pending"))
    await db_session.commit()

    real_store = library_store.store_upload
    calls = 0

    async def flaky_store(db, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            await db.execute(select(LibraryFile.id))  # the failure comes mid-transaction
            raise OSError("disk full")
        return await real_store(db, **kwargs)

    with patch.object(library_store, "store_upload", flaky_store):
        response = await async_client.post("/api/v1/pending-uploads/archive-all")

    assert response.status_code == 200, response.text
    assert response.json() == {"stored": 2, "failed": 1}


@pytest.mark.parametrize(
    ("permissions", "status"),
    [(["queue:create"], 403), (["library:upload"], 403), (["queue:create", "library:upload"], 200)],
    ids=["queue-only", "library-only", "both"],
)
async def test_approving_needs_library_upload_as_well(
    async_client: AsyncClient, db_session, tmp_path, permissions, status
):
    # Approving adds a File Manager file, so it needs what an upload needs.
    from backend.app.core.auth import get_password_hash
    from backend.app.models.group import Group
    from backend.app.models.settings import Settings
    from backend.app.models.user import User

    db_session.add_all(
        [Settings(key="auth_enabled", value="true"), Settings(key="advanced_auth_enabled", value="false")]
    )
    group = Group(name="reviewers", description="t", permissions=permissions, is_system=False)
    db_session.add(group)
    await db_session.flush()
    db_session.add(
        User(username="teacher", password_hash=get_password_hash("Pass1word!"), is_active=True, groups=[group])
    )
    await db_session.commit()
    upload_id = (await _pending(db_session, tmp_path)).id
    login = await async_client.post("/api/v1/auth/login", json={"username": "teacher", "password": "Pass1word!"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    single = await async_client.post(f"/api/v1/pending-uploads/{upload_id}/archive", headers=headers)
    every = await async_client.post("/api/v1/pending-uploads/archive-all", headers=headers)

    assert single.status_code == status, single.text
    assert every.status_code == status, every.text


async def test_the_approved_file_is_the_senders(async_client: AsyncClient, db_session, tmp_path):
    # Sent with a personal access code (#1430): the file is the student's,
    # not the teacher's who approves it.
    from backend.app.models.user import User

    student = User(username="student", password_hash="x", is_active=True)
    db_session.add(student)
    await db_session.flush()
    upload_id = (await _pending(db_session, tmp_path, uploaded_by_id=student.id)).id

    response = await async_client.post(f"/api/v1/pending-uploads/{upload_id}/archive")

    assert response.status_code == 200, response.text
    library_file = await db_session.get(LibraryFile, response.json()["id"])
    assert library_file.created_by_id == student.id


async def test_approving_removes_the_folder_the_upload_waited_in(async_client: AsyncClient, db_session, tmp_path):
    waiting = tmp_path / "uploads" / "3" / "pending" / "abc123" / "part.3mf"
    waiting.parent.mkdir(parents=True)
    waiting.write_bytes(b"3mf")
    upload = PendingUpload(filename="part.3mf", file_path=str(waiting), file_size=3, source_ip=None, status="pending")
    db_session.add(upload)
    await db_session.commit()

    response = await async_client.post(f"/api/v1/pending-uploads/{upload.id}/archive")

    assert response.status_code == 200, response.text
    assert not waiting.parent.exists()
    assert waiting.parent.parent.exists()
