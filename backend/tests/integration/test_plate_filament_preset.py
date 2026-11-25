"""Each plate filament carries the slicer preset it was sliced with (SN-C9).

The plate's type is only "PLA"; the colour catalogue is keyed by product line
("PLA Matte"), where the same hex has another name. The queue page names a
colour from these plate listings, so each needs the preset id (tray_info_idx)
that names the line. All three listings are copies of one parser.
"""

import io
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _sliced_3mf() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "Metadata/slice_info.config",
            "<?xml version='1.0'?><config><plate><metadata key='index' value='1'/>"
            "<filament id='1' tray_info_idx='GFA01' type='PLA' color='#000000' used_g='5' used_m='1.6'/>"
            "<filament id='2' type='PLA' color='#FFFFFF' used_g='3' used_m='1'/>"
            "</plate></config>",
        )
        zf.writestr("Metadata/plate_1.gcode", "; plate 1")
    return buf.getvalue()


def _presets(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [f["tray_info_idx"] for f in response.json()["plates"][0]["filaments"]]


@pytest.fixture
def base_dir(monkeypatch, tmp_path) -> Path:
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "base_dir", tmp_path)
    return tmp_path


async def test_archive_plates(async_client, archive_factory, printer_factory, base_dir):
    (base_dir / "matte.3mf").write_bytes(_sliced_3mf())
    printer = await printer_factory()
    archive = await archive_factory(printer.id, filename="matte.3mf", file_path="matte.3mf")

    response = await async_client.get(f"/api/v1/archives/{archive.id}/plates")

    assert _presets(response) == ["GFA01", ""]


async def test_library_file_plates(async_client, db_session, base_dir):
    from backend.app.models.library import LibraryFile

    path = base_dir / "matte.3mf"
    path.write_bytes(_sliced_3mf())
    lib_file = LibraryFile(filename="matte.3mf", file_path=str(path), file_type="3mf", file_size=1)
    db_session.add(lib_file)
    await db_session.commit()

    response = await async_client.get(f"/api/v1/library/files/{lib_file.id}/plates")

    assert _presets(response) == ["GFA01", ""]


async def test_printer_file_plates(async_client, printer_factory):
    printer = await printer_factory()

    with patch("backend.app.api.routes.printers._download_printer_file_bytes", AsyncMock(return_value=_sliced_3mf())):
        response = await async_client.get(
            f"/api/v1/printers/{printer.id}/files/plates", params={"path": "/cache/matte.3mf"}
        )

    assert _presets(response) == ["GFA01", ""]
