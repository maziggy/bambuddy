"""The same slice sent again from the slicer adds a run to its archive (#1634).

A slicer sending a slice again writes a new 3MF around the same G-code, so the
file's hash never matched and each send became an archive of its own, its
print count stuck at one. A print sent from the slicer whose G-code matches an
archive (same owner, no project, no File Manager file) now joins that archive:
the archive takes the new file in place of its old one and opens this print's
run on it.
"""

from __future__ import annotations

import zipfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from backend.app.core.config import settings as app_settings
from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import INCLUDE_OPEN_RUNS, RUN_OPEN, PrintLogEntry
from backend.app.services import archive_work
from backend.app.services.archive import ArchiveService
from backend.app.services.archive_gcode_hash import gcode_fingerprint
from backend.tests.integration.test_external_print_confirmation_1898 import (
    _clear_print_state,  # noqa: F401 - autouse fixture
    _drive_print_start,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

GCODE = b"; HEADER_BLOCK_START\nG28\nG1 X10 Y10 F3000\n"


def _sliced(path, gcode: bytes = GCODE, *, export: str = "first"):
    """A sliced 3MF; ``export`` stands for what a new export of it changes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("3D/3dmodel.model", f"<model export='{export}'/>")
        zf.writestr("Metadata/plate_1.gcode", gcode)
    return path


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_settings, "archive_dir", tmp_path / "archive")
    return tmp_path


async def _archived(db_session, printer_id: int, files, *, name: str = "Bracket.3mf", **fields) -> PrintArchive:
    """The archive of the first send, printed and done."""
    archive = await ArchiveService(db_session).archive_print(printer_id, _sliced(files / "first" / name))
    for key, value in {"status": "completed", **fields}.items():
        setattr(archive, key, value)
    await db_session.commit()
    return archive


async def _send_again(test_engine, printer, files, gcode: bytes = GCODE, *, owner_id: int | None = None):
    """The slicer sends a print; the printer's 3MF is a new export of ``gcode``."""
    upload = _sliced(files / "upload" / "sent.3mf", gcode, export="again")

    async def download(_ip, _code, _remote, local_path, **_kwargs):
        local_path.write_bytes(upload.read_bytes())
        return True

    return await _drive_print_start(
        test_engine,
        printer,
        download_ok=True,
        extra_patches=(
            patch("backend.app.main.download_file_async", new=AsyncMock(side_effect=download)),
            patch("backend.app.main.ArchiveService", ArchiveService),
            patch("backend.app.main._ask_outcome_for_external_print", new=AsyncMock(return_value=False)),
            patch("backend.app.main._print_user_id_if_still_there", new=AsyncMock(return_value=owner_id)),
        ),
    )


async def _archives(db_session) -> list[PrintArchive]:
    db_session.expire_all()
    return list((await db_session.scalars(select(PrintArchive).order_by(PrintArchive.id))).all())


async def _open_runs(db_session, archive_id: int) -> list[PrintLogEntry]:
    return list(
        (
            await db_session.scalars(
                select(PrintLogEntry)
                .where(PrintLogEntry.archive_id == archive_id, PrintLogEntry.status == RUN_OPEN)
                .execution_options(**{INCLUDE_OPEN_RUNS: True})
            )
        ).all()
    )


class TestJoining:
    async def test_the_same_slice_sent_again_adds_a_run_to_its_archive(
        self, test_engine, db_session, printer_factory, files
    ):
        printer = await printer_factory()
        archive = await _archived(db_session, printer.id, files)

        await _send_again(test_engine, printer, files)

        (row,) = await _archives(db_session)
        assert row.id == archive.id
        assert row.status == "printing"
        assert len(await _open_runs(db_session, archive.id)) == 1
        from backend.app.main import _active_prints

        assert archive.id in _active_prints.values()

    async def test_it_takes_the_new_file_into_its_own_folder(self, test_engine, db_session, printer_factory, files):
        printer = await printer_factory()
        archive = await _archived(db_session, printer.id, files)
        folder = (files / archive.file_path).parent
        old_file = files / archive.file_path
        photo = folder / "photos" / "finish.jpg"
        photo.parent.mkdir()
        photo.write_bytes(b"jpg")
        old_hash = archive.content_hash

        await _send_again(test_engine, printer, files)

        (row,) = await _archives(db_session)
        new_file = files / row.file_path
        assert new_file.parent == folder
        assert new_file.is_file() and new_file != old_file
        assert not old_file.exists()
        assert photo.exists()
        assert row.content_hash != old_hash
        assert row.gcode_hash == gcode_fingerprint(new_file)

    async def test_another_slice_is_archived_anew(self, test_engine, db_session, printer_factory, files):
        printer = await printer_factory()
        archive = await _archived(db_session, printer.id, files)

        await _send_again(test_engine, printer, files, GCODE + b"G1 Z0.2\n")

        rows = await _archives(db_session)
        assert len(rows) == 2
        assert rows[0].id == archive.id and rows[0].status == "completed"

    @pytest.mark.parametrize("which", ["project", "library file", "deleted"])
    async def test_an_archive_that_is_not_the_slicers_is_not_joined(
        self, test_engine, db_session, printer_factory, files, which
    ):
        from backend.app.models.library import LibraryFile
        from backend.app.models.project import Project

        printer = await printer_factory()
        fields: dict = {}
        if which == "project":
            project = Project(name="Brackets")
            db_session.add(project)
            await db_session.commit()
            fields["project_id"] = project.id
        elif which == "library file":
            lib = LibraryFile(filename="Bracket.3mf", file_path="library/Bracket.3mf", file_size=1, file_type="3mf")
            db_session.add(lib)
            await db_session.commit()
            fields["library_file_id"] = lib.id
        else:
            fields["deleted_at"] = datetime.now(timezone.utc)
        await _archived(db_session, printer.id, files, **fields)

        await _send_again(test_engine, printer, files)

        assert len(await _archives(db_session)) == 2

    async def test_the_owner_has_to_match(self, test_engine, db_session, printer_factory, files):
        from backend.app.models.user import User

        user = User(username="maker", password_hash="x", role="user")
        db_session.add(user)
        await db_session.commit()
        user_id = user.id
        printer = await printer_factory()
        owned = await _archived(db_session, printer.id, files, created_by_id=user_id)
        owned_id = owned.id

        # Nobody started this print: the owned archive is not its.
        await _send_again(test_engine, printer, files)
        assert len(await _archives(db_session)) == 2

        # Its owner started the next one, once that print ended.
        from sqlalchemy import update

        await db_session.execute(update(PrintArchive).values(status="completed"))
        await db_session.execute(
            update(PrintLogEntry).values(status="completed").execution_options(**{INCLUDE_OPEN_RUNS: True})
        )
        await db_session.commit()
        await db_session.refresh(printer)
        await _send_again(test_engine, printer, files, owner_id=user_id)
        rows = await _archives(db_session)
        assert len(rows) == 2
        assert len(await _open_runs(db_session, owned_id)) == 1

    async def test_an_archive_being_merged_is_left_alone(self, test_engine, db_session, printer_factory, files):
        printer = await printer_factory()
        archive = await _archived(db_session, printer.id, files)

        with archive_work.merging([archive.id]):
            await _send_again(test_engine, printer, files)

        assert len(await _archives(db_session)) == 2
        assert not archive_work.is_held(archive.id)

    async def test_the_archive_is_released_once_its_run_is_open(self, test_engine, db_session, printer_factory, files):
        printer = await printer_factory()
        archive = await _archived(db_session, printer.id, files)

        await _send_again(test_engine, printer, files)

        assert not archive_work.is_held(archive.id)


class TestPrintingElsewhere:
    async def test_the_file_another_print_reads_is_kept(self, test_engine, db_session, printer_factory, files):
        printer = await printer_factory()
        other = await printer_factory(name="Other")
        archive = await _archived(db_session, printer.id, files, status="printing")
        old_file = files / archive.file_path
        old_hash = archive.content_hash
        db_session.add(
            PrintLogEntry(
                archive_id=archive.id,
                printer_id=other.id,
                status=RUN_OPEN,
                print_name=archive.print_name,
                started_at=datetime.now(timezone.utc),
            )
        )
        await db_session.commit()

        await _send_again(test_engine, printer, files)

        (row,) = await _archives(db_session)
        assert (row.content_hash, row.file_path) == (old_hash, archive.file_path)
        assert old_file.exists()
        assert len(await _open_runs(db_session, archive.id)) == 2


class TestName:
    async def test_a_name_the_file_gave_follows_the_file(self, test_engine, db_session, printer_factory, files):
        printer = await printer_factory()
        archive = await _archived(db_session, printer.id, files, name="Old_name.3mf")
        assert archive.print_name == "Old_name"

        await _send_again(test_engine, printer, files)

        (row,) = await _archives(db_session)
        assert row.print_name == "Bracket_plate_1"  # the name of the file the printer had

    async def test_a_name_set_by_hand_stays(self, test_engine, db_session, printer_factory, files):
        printer = await printer_factory()
        await _archived(db_session, printer.id, files, name="Old_name.3mf", print_name="Wall bracket, left")

        await _send_again(test_engine, printer, files)

        (row,) = await _archives(db_session)
        assert row.print_name == "Wall bracket, left"


class TestWhatTheArchiveKeeps:
    async def test_the_saved_ams_pick_stays(self, test_engine, db_session, printer_factory, files):
        printer = await printer_factory()
        pick = {"mapping": [0, 2], "printer_id": printer.id}
        await _archived(db_session, printer.id, files, extra_data={"slicer_ams_mapping": pick})

        await _send_again(test_engine, printer, files)

        (row,) = await _archives(db_session)
        assert row.extra_data["slicer_ams_mapping"] == pick

    async def test_a_copy_that_fails_its_check_leaves_the_old_file(
        self, test_engine, db_session, printer_factory, files
    ):
        """Same file name: the copy goes to a temporary name first, so a
        truncated one never lands on the archive's file."""
        printer = await printer_factory()
        archive = await _archived(db_session, printer.id, files, name="Bracket_plate_1.gcode.3mf")
        old_file = files / archive.file_path
        old_bytes = old_file.read_bytes()

        def truncated(_src, dst, *_args, **_kwargs):
            dst.write_bytes(b"not a zip")

        with patch("backend.app.services.archive._copy_and_fsync", side_effect=truncated):
            await _send_again(test_engine, printer, files)

        (row,) = await _archives(db_session)
        assert (row.file_path, row.content_hash) == (archive.file_path, archive.content_hash)
        assert old_file.read_bytes() == old_bytes
        assert len(await _open_runs(db_session, archive.id)) == 1

    async def test_a_file_a_queue_dispatch_holds_is_kept(self, test_engine, db_session, printer_factory, files):
        """A queue dispatch holding the archive uploads or prints its file: the
        resend adds its run but does not take the file away under it."""
        printer = await printer_factory()
        archive = await _archived(db_session, printer.id, files)
        old_file = files / archive.file_path

        archive_work.hold(archive.id)
        try:
            await _send_again(test_engine, printer, files)
            assert archive_work.is_held(archive.id), "the dispatch's hold is its own"
        finally:
            archive_work.release(archive.id)

        (row,) = await _archives(db_session)
        assert row.file_path == archive.file_path
        assert old_file.exists()
        assert len(await _open_runs(db_session, archive.id)) == 1


class TestEvents:
    async def test_a_joined_archive_is_updated_not_created(self, test_engine, db_session, printer_factory, files):
        printer = await printer_factory()
        archive = await _archived(db_session, printer.id, files)

        mocks = await _send_again(test_engine, printer, files)

        mocks["ws_manager"].send_archive_created.assert_not_awaited()
        mocks["mqtt_relay"].on_archive_created.assert_not_awaited()
        (event,), _ = mocks["ws_manager"].send_archive_updated.await_args
        assert event["id"] == archive.id

    async def test_a_new_archive_is_created(self, test_engine, db_session, printer_factory, files):
        printer = await printer_factory()

        mocks = await _send_again(test_engine, printer, files)

        mocks["ws_manager"].send_archive_created.assert_awaited_once()
        mocks["mqtt_relay"].on_archive_created.assert_awaited_once()


class TestPrinterDeleted:
    async def test_a_joined_archive_is_not_removed_as_one_just_written(
        self, test_engine, db_session, printer_factory, files
    ):
        """The check after writing removes an archive this start wrote for a
        printer deleted meanwhile. A joined archive is not this start's: it
        holds earlier runs, and its adoption checked the printer itself."""
        printer = await printer_factory()
        archive = await _archived(db_session, printer.id, files)
        drop = AsyncMock(return_value=True)

        with patch("backend.app.main._drop_archive_of_deleted_printer", drop):
            await _send_again(test_engine, printer, files)

        drop.assert_not_awaited()
        (row,) = await _archives(db_session)
        assert row.id == archive.id
        assert len(await _open_runs(db_session, archive.id)) == 1
