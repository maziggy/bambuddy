"""The G-code fingerprint every archive keeps (#1634).

A print sent from the slicer joins the archive with the same G-code, so each
way an archive gets a file reads it: a new archive, a fallback given its 3MF, a
re-slice, a merge survivor that takes over a file. A restore has no file and
takes the backup's; archives made before are read once after startup. All of
them read it off the event loop: unzipping a large 3MF takes seconds.
"""

from __future__ import annotations

import json
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.core.config import settings as app_settings
from backend.app.models.archive import PrintArchive
from backend.app.services import archive_gcode_hash
from backend.app.services.archive_gcode_hash import NO_GCODE, fill_gcode_hashes, gcode_fingerprint

pytestmark = pytest.mark.integration

DIR = "archives/gcode"
GCODE = b"; HEADER_BLOCK_START\nG28\nG1 X10 Y10 F3000\n"


def _three_mf(path, plates: dict[int, bytes] | None = None, *, model: str = "<model/>", when=(2026, 1, 1, 0, 0, 0)):
    """A sliced 3MF; ``when`` and ``model`` stand for what a new export changes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("3D/3dmodel.model", date_time=when), model)
        for number, gcode in (plates if plates is not None else {1: GCODE}).items():
            zf.writestr(zipfile.ZipInfo(f"Metadata/plate_{number}.gcode", date_time=when), gcode)
    return path


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_settings, "archive_dir", tmp_path / "archive")
    return tmp_path


@pytest.fixture
def off_the_loop(monkeypatch):
    """Records what is read through asyncio.to_thread."""
    import asyncio

    calls: list = []
    real = asyncio.to_thread

    async def to_thread(fn, *args, **kwargs):
        calls.append(fn)
        return await real(fn, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", to_thread)
    return calls


class TestFingerprint:
    def test_a_new_export_of_the_same_slice_has_the_same_fingerprint(self, tmp_path):
        first = _three_mf(tmp_path / "a.3mf")
        again = _three_mf(tmp_path / "b.3mf", model="<model changed='1'/>", when=(2026, 2, 2, 12, 0, 0))

        assert first.read_bytes() != again.read_bytes()
        assert gcode_fingerprint(first) == gcode_fingerprint(again)

    def test_another_slice_has_another(self, tmp_path):
        first = _three_mf(tmp_path / "a.3mf")
        resliced = _three_mf(tmp_path / "b.3mf", {1: GCODE + b"G1 Z0.2\n"})

        assert gcode_fingerprint(first) != gcode_fingerprint(resliced)

    def test_the_same_gcode_on_another_plate_is_another_file(self, tmp_path):
        assert gcode_fingerprint(_three_mf(tmp_path / "a.3mf", {1: GCODE})) != gcode_fingerprint(
            _three_mf(tmp_path / "b.3mf", {2: GCODE})
        )

    def test_every_plate_counts_in_plate_order(self, tmp_path):
        both = _three_mf(tmp_path / "a.3mf", {1: GCODE, 2: b"G28\n"})
        swapped = _three_mf(tmp_path / "b.3mf", {2: b"G28\n", 1: GCODE})
        one = _three_mf(tmp_path / "c.3mf", {1: GCODE})

        assert gcode_fingerprint(both) == gcode_fingerprint(swapped)
        assert gcode_fingerprint(both) != gcode_fingerprint(one)

    def test_a_plain_gcode_file_is_its_bytes(self, tmp_path):
        import hashlib

        path = tmp_path / "cube.gcode"
        path.write_bytes(GCODE)

        assert gcode_fingerprint(path) == hashlib.sha256(GCODE).hexdigest()

    @pytest.mark.parametrize(
        "write",
        [
            lambda p: _three_mf(p, {}),  # a project, not sliced
            lambda p: p.write_bytes(b"PK\x03\x04 not really a zip"),
            lambda p: p.write_bytes(b"solid cube\nendsolid\n"),
        ],
    )
    def test_a_file_without_readable_gcode_has_none(self, tmp_path, write):
        path = tmp_path / "f.3mf"
        write(path)

        assert gcode_fingerprint(path) is None


class TestNewArchives:
    async def test_an_archived_print_keeps_its_fingerprint(self, files, db_session, printer_factory, off_the_loop):
        from backend.app.services.archive import ArchiveService

        printer = await printer_factory()
        source = _three_mf(files / "upload" / "a.gcode.3mf")

        archive = await ArchiveService(db_session).archive_print(printer.id, source)

        assert archive.gcode_hash == gcode_fingerprint(source)
        assert gcode_fingerprint in off_the_loop

    async def test_a_file_without_gcode_is_marked_read(self, files, db_session, printer_factory):
        from backend.app.services.archive import ArchiveService

        printer = await printer_factory()
        source = _three_mf(files / "upload" / "project.3mf", {})

        archive = await ArchiveService(db_session).archive_print(printer.id, source)

        assert archive.gcode_hash == NO_GCODE

    async def test_a_print_given_its_3mf_later_reads_its_fingerprint(self, files, db_session, printer_factory):
        from backend.app.services.archive import ArchiveService

        printer = await printer_factory()
        fallback = PrintArchive(printer_id=printer.id, filename="a.3mf", file_path="", file_size=0, status="printing")
        db_session.add(fallback)
        await db_session.commit()
        source = _three_mf(files / "upload" / "a.gcode.3mf")

        archive = await ArchiveService(db_session).archive_print(printer.id, source, update_archive_id=fallback.id)

        assert (archive.id, archive.gcode_hash) == (fallback.id, gcode_fingerprint(source))

    async def test_a_reslice_reads_its_fingerprint(
        self, files, db_session, archive_factory, printer_factory, off_the_loop
    ):
        from backend.tests.integration.test_slice_archive_output_path_2832 import _slice, _stub_slicer

        printer = await printer_factory()
        source = await archive_factory(printer.id, print_name="Bracket", filename="Bracket.3mf")
        sliced = _three_mf(files / "sliced.3mf").read_bytes()

        # _slice stubs the slicer with a file that is not a 3MF: this one is.
        with patch(
            "backend.tests.integration.test_slice_archive_output_path_2832._stub_slicer", lambda: _stub_slicer(sliced)
        ):
            response = await _slice(db_session, source, "Bracket.3mf")

        archive = await db_session.get(PrintArchive, response.archive_id)
        assert archive.gcode_hash == gcode_fingerprint(files / "sliced.3mf")
        assert archive_gcode_hash.read_gcode_hash in off_the_loop


class TestBackupAndRestore:
    async def test_a_restored_archive_keeps_its_fingerprint(self, db_session, printer_factory):
        """A restore brings back no file to read it from."""
        from backend.app.schemas.github_backup import RestoreCategory
        from backend.app.services.github_backup import github_backup_service
        from backend.app.services.github_restore import GitHubRestoreService

        printer = await printer_factory()
        db_session.add(
            PrintArchive(
                printer_id=printer.id,
                filename="a.3mf",
                file_path="archives/t/a.3mf",
                file_size=1,
                content_hash="hash-a",
                gcode_hash="a" * 64,
                print_name="A",
                status="completed",
            )
        )
        await db_session.commit()
        backup: dict = {}
        await github_backup_service._collect_archives(db_session, backup)
        backup = json.loads(json.dumps(backup))
        await db_session.execute(delete(PrintArchive))
        await db_session.commit()

        await GitHubRestoreService()._apply(db_session, backup, [RestoreCategory.ARCHIVES], False)

        restored = (await db_session.scalars(select(PrintArchive))).one()
        assert (restored.file_path, restored.gcode_hash) == ("", "a" * 64)

    @pytest.mark.parametrize("value", [None, "", "not-a-hash", 42])
    def test_a_backup_without_one_restores_none(self, value):
        from backend.app.services.github_restore import _gcode_hash_of

        assert _gcode_hash_of({"gcode_hash": value} if value is not None else {}) is None


class TestFill:
    async def test_archives_made_before_get_theirs_once(self, files, db_session, test_engine, off_the_loop):
        _three_mf(files / DIR / "a.gcode.3mf")
        older = PrintArchive(filename="a.gcode.3mf", file_path=f"{DIR}/a.gcode.3mf", file_size=1)
        gone = PrintArchive(filename="gone.3mf", file_path=f"{DIR}/gone.3mf", file_size=1)
        read = PrintArchive(filename="r.3mf", file_path=f"{DIR}/a.gcode.3mf", file_size=1, gcode_hash=NO_GCODE)
        db_session.add_all([older, gone, read])
        await db_session.commit()

        sessions = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        assert await fill_gcode_hashes(sessions, batch=1) == 2
        assert await fill_gcode_hashes(sessions) == 0

        for row in (older, gone, read):
            await db_session.refresh(row)
        fingerprint = gcode_fingerprint(files / DIR / "a.gcode.3mf")
        assert (older.gcode_hash, gone.gcode_hash, read.gcode_hash) == (fingerprint, NO_GCODE, NO_GCODE)
        assert archive_gcode_hash.read_gcode_hash in off_the_loop

    async def test_an_archive_read_meanwhile_keeps_what_was_read(self, files, db_session, test_engine, monkeypatch):
        racing = PrintArchive(filename="a.gcode.3mf", file_path="", file_size=0)
        db_session.add(racing)
        await db_session.commit()
        sessions = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

        async def meanwhile(fn, row):
            # Its 3MF recovered and read while the fill reads it.
            async with sessions() as other:
                (await other.get(PrintArchive, racing.id)).gcode_hash = "f" * 64
                await other.commit()
            return fn(row)

        monkeypatch.setattr(archive_gcode_hash, "asyncio", SimpleNamespace(to_thread=meanwhile))

        assert await fill_gcode_hashes(sessions) == 0
        await db_session.refresh(racing)
        assert racing.gcode_hash == "f" * 64
