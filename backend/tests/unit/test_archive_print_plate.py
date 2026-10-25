"""The archive records which plate of a multi-plate file was printed.

A print Bambuddy did not dispatch names its plate only in the gcode path the
printer reports; a print Bambuddy did dispatch passes the plate explicitly,
while the path may name none (some firmware reports the .3mf name there).
"""

import zipfile

import pytest

from backend.app.core.config import settings
from backend.app.models.printer import Printer
from backend.app.services import archive as archive_module
from backend.app.services.archive import ArchiveService


def _three_mf(path, plates):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
        for n in plates:
            zf.writestr(f"Metadata/plate_{n}.gcode", "; gcode\n")
    return path


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "base_dir", tmp_path)
    monkeypatch.setattr(settings, "archive_dir", tmp_path / "archive")
    return tmp_path


@pytest.fixture
async def printer_id(db_session):
    row = Printer(name="X1C-1", ip_address="192.168.0.11", access_code="1234", serial_number="PLATETEST")
    db_session.add(row)
    await db_session.commit()
    return row.id


@pytest.fixture
def parsed_plates(monkeypatch):
    seen: list = []
    original = archive_module.ThreeMFParser

    class _Recording(original):
        def __init__(self, file_path, plate_number=None):
            seen.append(plate_number)
            super().__init__(file_path, plate_number=plate_number)

    monkeypatch.setattr(archive_module, "ThreeMFParser", _Recording)
    return seen


@pytest.mark.asyncio
async def test_plate_from_the_gcode_path_is_stored(db_session, dirs, printer_id):
    source = _three_mf(dirs / "multi.3mf", [1, 2, 3])

    archive = await ArchiveService(db_session).archive_print(
        printer_id, source, print_data={"filename": "/data/Metadata/plate_3.gcode"}
    )

    assert archive.plate_id == 3


@pytest.mark.asyncio
async def test_a_plate_the_file_does_not_have_is_not_stored(db_session, dirs, printer_id):
    source = _three_mf(dirs / "x_plate_2.gcode.3mf", [1])

    archive = await ArchiveService(db_session).archive_print(
        printer_id, source, print_data={"filename": "x_plate_2.gcode.3mf"}
    )

    assert archive.plate_id is None


@pytest.mark.asyncio
async def test_the_given_plate_wins_over_the_path(db_session, dirs, printer_id, parsed_plates):
    """The dispatcher knows the plate; the path can be stale (#1166)."""
    source = _three_mf(dirs / "multi.3mf", [1, 2, 3])

    archive = await ArchiveService(db_session).archive_print(
        printer_id, source, print_data={"filename": "/data/Metadata/plate_3.gcode"}, plate_id=2
    )

    assert archive.plate_id == 2
    assert parsed_plates == [2]


@pytest.mark.asyncio
async def test_the_given_plate_is_parsed_when_the_path_names_none(db_session, dirs, printer_id, parsed_plates):
    source = _three_mf(dirs / "multi.3mf", [1, 2, 3])

    await ArchiveService(db_session).archive_print(printer_id, source, print_data={"filename": "multi.3mf"}, plate_id=3)

    assert parsed_plates == [3]
