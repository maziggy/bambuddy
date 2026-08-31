"""Deleting a no-3MF archive used to leave every file it owned on disk (#2968).

An archive created without a 3MF carries ``file_path == ""``. Both delete paths
derived the directory to remove from that path, found nothing, and logged

    SECURITY: Refusing to delete files for archive 7 - file_path is empty or invalid: ''

at ERROR. That was accurate once, when such an archive really was an empty row.
It stopped being accurate when a no-3MF archive gained places to put things:
``<archive_dir>/<id>/`` for its timelapse and finish photos (the shared helper
in ``utils.archive_paths``, #1820), and ``archive/no_source/<id>/`` for a source
3MF uploaded onto it afterwards (#1531). Neither was ever removed, so deleting
the archive freed the row and kept the video -- on an H2-series or P2S printer,
where a print sent from Bambu Studio always archives without a 3MF, that is most
of the library.

Reported by @ceasley, whose log carries three of those ERROR lines from a single
afternoon of deleting no-3MF archives.

**The trap this file exists to hold shut.** ``<archive_dir>/<id>`` shares a
namespace with the per-printer folders: a normal archive lives at
``<archive_dir>/<printer_id>/<timestamp>_<name>/``, so ``archive/1`` is printer
1's folder *and* the directory ``resolve_archive_dir`` hands archive id 1.
Archive ids and printer ids are small integers from unrelated sequences, so on
every install the first few archives collide with the printers. An ``rmtree``
there deletes every print that printer ever made. The first draft of this fix
did exactly that, and passed a full suite before the collision was found by
reading the archive layout rather than the tests. Nothing in the delete path may
remove a directory one level under ``archive_dir``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from backend.app.models.archive import PrintArchive
from backend.app.models.printer import Printer
from backend.app.services.archive import ArchiveService


@pytest.fixture
def archive_root(tmp_path, monkeypatch):
    """A data directory both settings bindings agree on.

    ``services.archive`` and ``utils.archive_paths`` each hold their own
    module-level ``settings``; patching one and not the other is how an earlier
    change to this code wrote outside tmp_path and littered a working tree.
    """
    from backend.app.services import archive as archive_module
    from backend.app.utils import archive_paths

    for module in (archive_module, archive_paths):
        monkeypatch.setattr(module.settings, "base_dir", tmp_path, raising=False)
        monkeypatch.setattr(module.settings, "archive_dir", tmp_path / "archive", raising=False)
    (tmp_path / "archive").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _service() -> ArchiveService:
    """The resolvers need no database; ``None`` keeps the test to one subject."""
    return ArchiveService(None)  # type: ignore[arg-type]


def _archive(archive_id: int, file_path: str = "") -> PrintArchive:
    return PrintArchive(id=archive_id, file_path=file_path)


def _printer_folder_with_a_print(archive_root, printer_id: int) -> Path:
    """A printer folder laid out exactly as ``_create_archive`` builds it."""
    directory = archive_root / "archive" / str(printer_id) / "20260828_193000_Benchy"
    directory.mkdir(parents=True)
    (directory / "Benchy.3mf").write_bytes(b"a real archived print")
    return directory


class TestItCannotDeleteAPrinterFolder:
    """The collision above. Every one of these would have destroyed real data."""

    def test_a_no_3mf_archive_whose_id_matches_a_printer(self, archive_root):
        real = _printer_folder_with_a_print(archive_root, 1)

        assert _service()._resolve_archive_dirs_for_delete(_archive(1)) == []
        assert real.exists()

    def test_and_the_purge_leaves_it_standing(self, archive_root):
        """The id-named directory is cleaned in place rather than removed, so
        the purge has to survive the folder being somebody else's."""
        real = _printer_folder_with_a_print(archive_root, 1)

        _service()._purge_id_named_dir(1, (None, None))

        assert (real / "Benchy.3mf").exists()
        assert (archive_root / "archive" / "1").is_dir()

    @pytest.mark.asyncio
    async def test_end_to_end_through_delete_archive(self, archive_root, db_session):
        """Not just the resolver: the whole delete, against real rows."""
        printer = Printer(name="H2D", ip_address="192.0.2.9", access_code="12345678", serial_number="COLLIDE")
        db_session.add(printer)
        await db_session.flush()

        real = archive_root / "archive" / str(printer.id) / "20260828_193000_Benchy"
        real.mkdir(parents=True)
        (real / "Benchy.3mf").write_bytes(b"a real archived print")

        archive = PrintArchive(
            printer_id=printer.id, filename="Cleaner_PRO", file_path="", file_size=0, status="completed"
        )
        db_session.add(archive)
        await db_session.commit()
        if archive.id != printer.id:
            pytest.skip(f"ids did not collide in this fixture (archive {archive.id}, printer {printer.id})")

        assert await ArchiveService(db_session).delete_archive(archive.id) is True
        assert (real / "Benchy.3mf").exists(), "deleting the archive took the printer's whole folder"

    def test_a_corrupted_row_pointing_at_a_printer_folder(self, archive_root, caplog):
        """``archive/1/Benchy.3mf`` -- a file_path that lost a path component.
        Its parent is the printer folder. Refused on depth, and said out loud."""
        real = _printer_folder_with_a_print(archive_root, 1)
        (archive_root / "archive" / "1" / "Benchy.3mf").write_bytes(b"x")

        with caplog.at_level(logging.ERROR):
            dirs = _service()._resolve_archive_dirs_for_delete(_archive(7, "archive/1/Benchy.3mf"))

        assert dirs == []
        assert (real / "Benchy.3mf").exists()
        assert any("not deep enough" in r.getMessage() for r in caplog.records)

    def test_the_archive_root_itself_is_refused(self, archive_root, caplog):
        (archive_root / "archive" / "Benchy.3mf").write_bytes(b"x")

        with caplog.at_level(logging.ERROR):
            dirs = _service()._resolve_archive_dirs_for_delete(_archive(7, "archive/Benchy.3mf"))

        assert dirs == []
        assert (archive_root / "archive").exists()

    def test_nothing_it_returns_is_ever_one_level_deep(self, archive_root):
        """The invariant, stated once against every shape a row can take."""
        _printer_folder_with_a_print(archive_root, 1)
        (archive_root / "archive" / "no_source" / "1").mkdir(parents=True)

        for file_path in ("", "archive/1/x.3mf", "archive/x.3mf", "../escape/x.3mf", "/absolute/x.3mf"):
            for directory in _service()._resolve_archive_dirs_for_delete(_archive(1, file_path)):
                relative = Path(directory).resolve().relative_to((archive_root / "archive").resolve())
                assert len(relative.parts) >= 2, f"{file_path} resolved to {relative}"


class TestANo3mfArchivesFiles:
    def test_its_timelapse_and_photos_are_removed(self, archive_root):
        """The files it really owns, taken by name rather than by rmtree."""
        directory = archive_root / "archive" / "7"
        (directory / "photos").mkdir(parents=True)
        (directory / "photos" / "finish.jpg").write_bytes(b"p")
        (directory / "video_2026-08-27_08-35-49.mp4").write_bytes(b"v")

        _service()._purge_id_named_dir(7, ("archive/7/video_2026-08-27_08-35-49.mp4", None))

        assert not directory.exists()

    def test_its_uploaded_source_directory_is_removed(self, archive_root):
        """``archive/no_source/<id>/`` is two levels down and nested under a
        name no printer id can take, so it is safe to remove whole."""
        source_dir = archive_root / "archive" / "no_source" / "7"
        source_dir.mkdir(parents=True)
        (source_dir / "Cleaner_PRO.3mf").write_bytes(b"x")

        assert _service()._resolve_archive_dirs_for_delete(_archive(7)) == [source_dir]

    def test_no_error_is_logged_for_an_ordinary_empty_path(self, archive_root, caplog):
        """It is the normal shape of a Studio-sent print, not a security event.
        Three of these were the only ERRORs in the reporter's whole log."""
        with caplog.at_level(logging.ERROR):
            _service()._resolve_archive_dirs_for_delete(_archive(7))
            _service()._purge_id_named_dir(7, (None, None))

        assert not [r for r in caplog.records if "SECURITY" in r.getMessage()]

    def test_an_unrecognised_file_keeps_the_directory(self, archive_root):
        """Leaking beats guessing: something this archive did not record stops
        the rmdir, and nothing is removed on a hunch."""
        directory = archive_root / "archive" / "7"
        directory.mkdir(parents=True)
        (directory / "something_else.bin").write_bytes(b"?")

        _service()._purge_id_named_dir(7, (None, None))

        assert (directory / "something_else.bin").exists()

    def test_a_recorded_path_outside_the_directory_is_not_followed(self, archive_root):
        """A row whose timelapse_path names another archive's file must not
        take it with this delete."""
        elsewhere = archive_root / "archive" / "1" / "20260828_193000_Benchy"
        elsewhere.mkdir(parents=True)
        (elsewhere / "video.mp4").write_bytes(b"v")
        (archive_root / "archive" / "7").mkdir(parents=True)

        _service()._purge_id_named_dir(7, ("archive/1/20260828_193000_Benchy/video.mp4", None))

        assert (elsewhere / "video.mp4").exists()

    def test_the_shared_legacy_photo_directory_is_never_touched(self, archive_root):
        """``<base_dir>/photos`` was written to by *every* no-3MF archive at
        once. Removing it on one delete would take the others' photos too."""
        shared = archive_root / "photos"
        shared.mkdir(parents=True)
        (shared / "finish_7.jpg").write_bytes(b"x")

        assert shared not in _service()._resolve_archive_dirs_for_delete(_archive(7))
        _service()._purge_id_named_dir(7, (None, None))

        assert (shared / "finish_7.jpg").exists()


class TestAnArchiveWithA3mf:
    def test_its_own_directory_is_removed(self, archive_root):
        archive_dir = archive_root / "archive" / "1" / "20260828_193000_Benchy"
        archive_dir.mkdir(parents=True)
        (archive_dir / "Benchy.3mf").write_bytes(b"x")

        dirs = _service()._resolve_archive_dirs_for_delete(_archive(7, "archive/1/20260828_193000_Benchy/Benchy.3mf"))

        assert dirs == [archive_dir]

    def test_a_missing_3mf_no_longer_strands_the_directory(self, archive_root):
        """The old code keyed on the 3MF still being there, so an archive whose
        3MF had gone kept its thumbnail and timelapse forever."""
        archive_dir = archive_root / "archive" / "1" / "20260828_193000_Benchy"
        archive_dir.mkdir(parents=True)
        (archive_dir / "thumbnail.png").write_bytes(b"x")

        dirs = _service()._resolve_archive_dirs_for_delete(_archive(7, "archive/1/20260828_193000_Benchy/Benchy.3mf"))

        assert dirs == [archive_dir]

    def test_a_directory_that_does_not_exist_is_not_offered(self, archive_root):
        assert _service()._resolve_archive_dirs_for_delete(_archive(7, "archive/1/gone/Benchy.3mf")) == []

    def test_a_file_where_a_directory_should_be_is_not_offered(self, archive_root):
        """``is_dir()`` rather than ``exists()``: rmtree on a file raises, and
        the delete would take the whole request down with it."""
        (archive_root / "archive" / "no_source").mkdir(parents=True)
        (archive_root / "archive" / "no_source" / "7").write_bytes(b"not a directory")

        assert _service()._resolve_archive_dirs_for_delete(_archive(7)) == []

    def test_a_path_outside_the_archive_tree_is_refused_and_logged(self, archive_root, caplog):
        """Only a corrupted import or hand-edited SQL produces this."""
        outside = archive_root / "elsewhere" / "deep"
        outside.mkdir(parents=True)
        (outside / "Benchy.3mf").write_bytes(b"x")

        with caplog.at_level(logging.ERROR):
            dirs = _service()._resolve_archive_dirs_for_delete(_archive(7, "elsewhere/deep/Benchy.3mf"))

        assert dirs == []
        assert (outside / "Benchy.3mf").exists()
        assert any("outside archive directory" in r.getMessage() for r in caplog.records)


class TestBothDeletePathsUseIt:
    """Hard delete kept its own copy of these rules and had already diverged
    from the helper whose docstring said it was extracted to prevent that."""

    async def _no_3mf_archive_with_files(self, archive_root, db_session, serial: str, ip: str):
        printer = Printer(name="H2D", ip_address=ip, access_code="12345678", serial_number=serial)
        db_session.add(printer)
        await db_session.flush()
        archive = PrintArchive(
            printer_id=printer.id, filename="Cleaner_PRO", file_path="", file_size=0, status="completed"
        )
        db_session.add(archive)
        await db_session.commit()

        video_dir = archive_root / "archive" / str(archive.id)
        video_dir.mkdir(parents=True, exist_ok=True)
        (video_dir / "video.mp4").write_bytes(b"v")
        archive.timelapse_path = f"archive/{archive.id}/video.mp4"
        source_dir = archive_root / "archive" / "no_source" / str(archive.id)
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "Cleaner_PRO.3mf").write_bytes(b"x")
        await db_session.commit()
        return archive, video_dir, source_dir

    @pytest.mark.asyncio
    async def test_soft_delete_removes_the_video_and_the_upload(self, archive_root, db_session):
        archive, video_dir, source_dir = await self._no_3mf_archive_with_files(
            archive_root, db_session, "SOFT1", "192.0.2.1"
        )

        assert await ArchiveService(db_session).soft_delete_archive(archive.id) is True

        assert not video_dir.exists()
        assert not source_dir.exists()

    @pytest.mark.asyncio
    async def test_hard_delete_removes_the_video_and_the_upload(self, archive_root, db_session):
        archive, video_dir, source_dir = await self._no_3mf_archive_with_files(
            archive_root, db_session, "HARD1", "192.0.2.2"
        )

        assert await ArchiveService(db_session).delete_archive(archive.id) is True

        assert not video_dir.exists()
        assert not source_dir.exists()

    @pytest.mark.asyncio
    async def test_hard_delete_still_removes_the_row_when_a_guard_trips(self, archive_root, db_session):
        """A row pointing outside the tree must still be deletable, or the
        archive becomes permanently stuck in the UI."""
        printer = Printer(name="H2D", ip_address="192.0.2.3", access_code="12345678", serial_number="GUARD1")
        db_session.add(printer)
        await db_session.flush()

        outside = archive_root / "elsewhere" / "deep"
        outside.mkdir(parents=True)
        (outside / "Benchy.3mf").write_bytes(b"x")

        archive = PrintArchive(
            printer_id=printer.id,
            filename="Benchy",
            file_path="elsewhere/deep/Benchy.3mf",
            file_size=0,
            status="completed",
        )
        db_session.add(archive)
        await db_session.commit()
        archive_id = archive.id

        assert await ArchiveService(db_session).delete_archive(archive_id) is True
        assert await ArchiveService(db_session).get_archive(archive_id) is None
        assert (outside / "Benchy.3mf").exists()
