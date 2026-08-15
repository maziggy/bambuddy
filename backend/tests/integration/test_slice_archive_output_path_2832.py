"""Where a slice-to-archive actually writes (#2832).

``safe_path_component`` is unit-tested. It is only worth anything if the sink
uses it: reverting the sanitiser leaves those tests green, because they never
touch the code that builds the path. These drive ``slice_and_persist_as_archive``
with the slicer stubbed, on the name from the report and on one that tries to
leave the archive directory.
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.app.api.routes.library import slice_and_persist_as_archive
from backend.app.schemas.slicer import SliceRequest
from backend.app.services.slicer_api import SliceResult

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

REPORTED = "Planter Pot with Drip Tray, 12 cm / 5 inches"


@pytest.fixture
def archive_root(monkeypatch, tmp_path):
    """Point both roots at a tmp dir, keeping their real relationship."""
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "base_dir", tmp_path)
    monkeypatch.setattr(settings, "archive_dir", tmp_path / "archive")
    return tmp_path / "archive"


def _stub_slicer(content: bytes = b"PK\x03\x04 not-a-real-3mf"):
    return patch(
        "backend.app.api.routes.library._run_slicer_with_fallback",
        AsyncMock(return_value=(SliceResult(content, 3600, 12.5, 4200.0), False)),
    )


async def _slice(db_session, source_archive, model_filename):
    with _stub_slicer():
        return await slice_and_persist_as_archive(
            db_session,
            model_bytes=b"source model",
            model_filename=model_filename,
            request=SliceRequest(printer_preset_id=1, process_preset_id=2, filament_preset_id=3),
            source_archive=source_archive,
            current_user_id=None,
        )


async def _written_files(archive_root):
    return [p for p in archive_root.rglob("*") if p.is_file()]


class TestTheReportedFailure:
    async def test_the_slice_lands_on_disk(self, db_session, archive_factory, printer_factory, archive_root):
        """The write used to fail with ENOENT: mkdir made the two directories
        the folder name implied, and the file's own join added a third that
        nobody had created."""
        printer = await printer_factory()
        source = await archive_factory(printer.id, print_name=REPORTED, filename=f"{REPORTED}.3mf")

        response = await _slice(db_session, source, f"{REPORTED}.3mf")

        written = await _written_files(archive_root)
        assert [p.name for p in written if p.suffix == ".3mf"] == [
            "Planter Pot with Drip Tray, 12 cm - 5 inches.gcode.3mf"
        ]
        # The display name keeps its punctuation -- only the path is reduced.
        assert "/" in response.name

    async def test_the_folder_is_one_level_deep(self, db_session, archive_factory, printer_factory, archive_root):
        """<archive>/<printer>/<timestamp>_<name>_sliced/<file> and no more.
        The slash used to add a level in the middle of the folder name."""
        printer = await printer_factory()
        source = await archive_factory(printer.id, print_name=REPORTED, filename=f"{REPORTED}.3mf")

        await _slice(db_session, source, f"{REPORTED}.3mf")

        written = [p for p in await _written_files(archive_root) if p.suffix == ".3mf"][0]
        assert written.relative_to(archive_root).parts[:1] == (str(printer.id),)
        assert len(written.relative_to(archive_root).parts) == 3

    async def test_the_archive_row_points_at_the_file(self, db_session, archive_factory, printer_factory, archive_root):
        """A row whose file_path does not exist is the same class of bug one
        step later -- every reprint and rescan reads it back."""
        from backend.app.core.config import settings
        from backend.app.models.archive import PrintArchive

        printer = await printer_factory()
        source = await archive_factory(printer.id, print_name=REPORTED, filename=f"{REPORTED}.3mf")

        response = await _slice(db_session, source, f"{REPORTED}.3mf")

        new_archive = await db_session.get(PrintArchive, response.archive_id)
        assert (settings.base_dir / new_archive.file_path).is_file()


class TestItStaysInTheArchiveDirectory:
    @pytest.mark.parametrize(
        "name",
        [
            "../../../../etc/cron.d/x",
            "../escaped",
            "..",
        ],
    )
    async def test_a_traversing_name_writes_nowhere_else(
        self, db_session, archive_factory, printer_factory, archive_root, tmp_path, name
    ):
        """The display name is free text from the 3MF, so it is whatever its
        author put there."""
        printer = await printer_factory()
        source = await archive_factory(printer.id, print_name=name, filename="source.3mf")

        await _slice(db_session, source, f"{name}.3mf")

        written = await _written_files(archive_root)
        assert written, "nothing was written at all"
        for path in written:
            # Not merely inside the archive root: inside this slice's own
            # folder. A name that only climbs one level lands in the printer's
            # directory, which is still contained and still wrong.
            assert path.parent.name.endswith("_sliced"), path
            assert path.resolve().is_relative_to(archive_root.resolve())
        # And nothing appeared beside the archive root either.
        assert not [p for p in tmp_path.iterdir() if p.name != "archive"]


class TestOrdinaryNamesAreUnchanged:
    async def test_a_plain_name_keeps_its_spelling(self, db_session, archive_factory, printer_factory, archive_root):
        printer = await printer_factory()
        source = await archive_factory(printer.id, print_name="Benchy", filename="Benchy.3mf")

        response = await _slice(db_session, source, "Benchy.3mf")

        assert response.name == "Benchy (re-sliced)"
        assert [p.name for p in await _written_files(archive_root) if p.suffix == ".3mf"] == ["Benchy.gcode.3mf"]
