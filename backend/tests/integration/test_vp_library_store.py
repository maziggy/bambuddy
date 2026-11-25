"""A virtual printer's upload stored in the File Manager (library_store)."""

from pathlib import Path

import pytest
from sqlalchemy import func, select

from backend.app.models.library import LibraryFile, LibraryFolder
from backend.app.models.virtual_printer import VirtualPrinter
from backend.app.services.virtual_printer.library_store import store_upload, upload_folder, upload_settings

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(autouse=True)
def _library_in_tmp(tmp_path, monkeypatch):
    """The File Manager writes below archive_dir: keep it out of the real one."""
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "archive_dir", tmp_path / "archive")


def _upload(tmp_path: Path, name: str = "Plate_1.gcode.3mf", content: bytes = b"plate one") -> Path:
    path = tmp_path / "uploads" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(content)
    return path


async def _files(db) -> int:
    return (await db.execute(select(func.count(LibraryFile.id)))).scalar()


async def test_stored_with_its_name_and_copied_from_disk(db_session, tmp_path):
    source = _upload(tmp_path)

    library_file, reused = await store_upload(db_session, source_path=source, filename=source.name, folder_id=None)

    assert reused is False
    assert library_file.filename == "Plate_1.gcode.3mf"
    assert library_file.source_type == "virtual_printer"
    assert library_file.file_size == len(b"plate one")
    from backend.app.api.routes.library import get_library_files_dir

    assert (get_library_files_dir() / Path(library_file.file_path).name).read_bytes() == b"plate one"
    assert source.exists(), "the caller removes its temp file, not the store"


async def test_the_same_file_sent_again_is_the_file_already_there(db_session, tmp_path):
    first, _ = await store_upload(
        db_session, source_path=_upload(tmp_path), filename="Plate_1.gcode.3mf", folder_id=None
    )

    again, reused = await store_upload(
        db_session, source_path=_upload(tmp_path), filename="Plate_1.gcode.3mf", folder_id=None
    )

    assert reused is True
    assert again.id == first.id
    assert await _files(db_session) == 1


@pytest.mark.parametrize(
    ("name", "content"),
    [("Plate_1.gcode.3mf", b"changed plate"), ("Other.gcode.3mf", b"plate one")],
    ids=["other-content", "other-name"],
)
async def test_other_content_or_another_name_is_a_new_file(db_session, tmp_path, name, content):
    await store_upload(db_session, source_path=_upload(tmp_path), filename="Plate_1.gcode.3mf", folder_id=None)

    _, reused = await store_upload(
        db_session, source_path=_upload(tmp_path, name, content), filename=name, folder_id=None
    )

    assert reused is False
    assert await _files(db_session) == 2


async def test_a_file_put_there_by_hand_is_never_taken_over(db_session, tmp_path):
    first, _ = await store_upload(
        db_session, source_path=_upload(tmp_path), filename="Plate_1.gcode.3mf", folder_id=None
    )
    first.source_type = None  # as an upload through the File Manager would be
    await db_session.commit()

    again, reused = await store_upload(
        db_session, source_path=_upload(tmp_path), filename="Plate_1.gcode.3mf", folder_id=None
    )

    assert reused is False
    assert again.id != first.id


async def test_the_configured_folder_is_used(db_session):
    folder = LibraryFolder(name="Class 7b", shared=True)
    db_session.add(folder)
    await db_session.flush()

    assert (await upload_folder(db_session, folder.id)).id == folder.id


@pytest.mark.parametrize("case", ["none", "deleted", "external"])
async def test_otherwise_the_shared_virtual_printer_folder(db_session, tmp_path, case):
    folder_id = None
    if case == "deleted":
        folder_id = 4242
    elif case == "external":
        mount = LibraryFolder(name="NAS", is_external=True, external_path=str(tmp_path), shared=True)
        db_session.add(mount)
        await db_session.flush()
        folder_id = mount.id

    folder = await upload_folder(db_session, folder_id)

    assert folder.name == "Virtual Printer"
    assert folder.parent_id is None
    assert folder.is_external is False
    assert folder.shared is True
    # One shared folder, not one per upload.
    assert (await upload_folder(db_session, None)).id == folder.id


async def test_upload_settings_are_read_from_the_virtual_printer(db_session):
    folder = LibraryFolder(name="Queue", shared=True)
    db_session.add(folder)
    await db_session.flush()
    vp = VirtualPrinter(name="Q", mode="queue", library_folder_id=folder.id, queue_remove_after_print=True)
    db_session.add(vp)
    await db_session.flush()

    assert await upload_settings(db_session, vp.id) == (folder.id, True)
    assert await upload_settings(db_session, 9999) == (None, False)


async def test_a_users_private_folder_of_that_name_is_not_the_shared_one(db_session):
    # With auth on, a user may make a private top-level "Virtual Printer"
    # folder; uploads of every VP without a folder must not vanish into it.
    from backend.app.models.user import User

    alice = User(username="alice", password_hash="x", role="user")
    db_session.add(alice)
    await db_session.flush()
    private = LibraryFolder(name="Virtual Printer", created_by_id=alice.id, shared=False)
    db_session.add(private)
    await db_session.flush()

    folder = await upload_folder(db_session, None)

    assert folder.id != private.id
    assert folder.shared is True
    assert folder.created_by_id is None


async def test_a_file_a_queue_job_removes_once_it_prints_is_not_shared(db_session, tmp_path):
    from backend.app.models.print_queue import PrintQueueItem

    # The queue VP with "Remove from File Manager once it prints" gets a file
    # of its own, even with the same file already in the folder ...
    kept, _ = await store_upload(
        db_session, source_path=_upload(tmp_path), filename="Plate_1.gcode.3mf", folder_id=None
    )
    own, reused = await store_upload(
        db_session, source_path=_upload(tmp_path), filename="Plate_1.gcode.3mf", folder_id=None, reuse=False
    )
    assert reused is False
    assert own.id != kept.id
    db_session.add(PrintQueueItem(library_file_id=own.id, status="pending", cleanup_library_after_dispatch=True))
    await db_session.commit()

    # ... and a later upload of it never takes that one, which its print removes.
    again, reused = await store_upload(
        db_session, source_path=_upload(tmp_path), filename="Plate_1.gcode.3mf", folder_id=None
    )
    assert reused is True
    assert again.id == kept.id
    await db_session.delete(kept)
    await db_session.commit()
    fresh, reused = await store_upload(
        db_session, source_path=_upload(tmp_path), filename="Plate_1.gcode.3mf", folder_id=None
    )
    assert reused is False
    assert fresh.id not in (kept.id, own.id)
