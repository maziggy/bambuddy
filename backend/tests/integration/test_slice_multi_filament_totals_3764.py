"""A multi-colour slice reports the whole print's filament (#3764).

Sidecar images before the fix read only the first value of the G-code's
per-filament totals ("; total filament weight [g] : 18.40,18.33"), so a
two-colour print came back as X-Filament-Used-G: 18.4 and X-Filament-Used-Mm:
6169. The sliced 3MF's slice_info says 36.73 g, and its grams now win over the
header; Bambuddy's own G-code header fallback sums the values as well.
"""

import io
import zipfile
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.api.routes.library import slice_and_persist, slice_and_persist_as_archive
from backend.app.core.printer_scope import ALL_PRINTERS
from backend.app.schemas.slicer import SliceRequest
from backend.app.services.slicer_api import SliceResult

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

HEADER = """; HEADER_BLOCK_START
; BambuStudio 02.09.01.70
; total layer number: 50
; total filament length [mm] : 6169.00,6147.26
; total filament volume [cm^3] : 14839.60,14787.31
; total filament weight [g] : 18.40,18.33
; filament: 1,2
; HEADER_BLOCK_END
G1 X0 Y0
"""

SLICE_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="index" value="1"/>
    <metadata key="prediction" value="3600"/>
    <metadata key="weight" value="36.73"/>
    <filament id="1" type="PLA" color="#FF0000" used_m="6.17" used_g="18.40"/>
    <filament id="2" type="PLA" color="#0000FF" used_m="6.15" used_g="18.33"/>
  </plate>
</config>
"""


def _sliced_3mf(with_slice_info: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
        zf.writestr("Metadata/plate_1.gcode", HEADER)
        if with_slice_info:
            zf.writestr("Metadata/slice_info.config", SLICE_INFO)
    return buf.getvalue()


def _old_sidecar(content: bytes, grams: float = 18.4, mm: float = 6169.0):
    """The slicer answering with the first filament's figures in its headers."""
    return patch(
        "backend.app.api.routes.library._run_slicer_with_fallback",
        AsyncMock(return_value=(SliceResult(content, 3600, grams, mm), False)),
    )


@pytest.fixture(autouse=True)
def data_root(monkeypatch, tmp_path):
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "base_dir", tmp_path)
    monkeypatch.setattr(settings, "archive_dir", tmp_path / "archive")


REQUEST = SliceRequest(printer_preset_id=1, process_preset_id=2, filament_preset_id=3)


async def _to_library(db_session, content: bytes, **sidecar):
    with _old_sidecar(content, **sidecar):
        return await slice_and_persist(
            db_session,
            model_bytes=b"model",
            model_filename="Two cubes.3mf",
            folder_id=None,
            extra_metadata=None,
            request=REQUEST,
            current_user_id=None,
            printer_scope=ALL_PRINTERS,
        )


async def _to_archive(db_session, source, content: bytes):
    with _old_sidecar(content):
        return await slice_and_persist_as_archive(
            db_session,
            model_bytes=b"model",
            model_filename="Two cubes.3mf",
            request=REQUEST,
            source_archive=source,
            current_user_id=None,
            printer_scope=ALL_PRINTERS,
        )


class TestTheSlicedGramsWin:
    async def test_a_library_slice(self, db_session):
        response = await _to_library(db_session, _sliced_3mf())

        assert response.filament_used_g == pytest.approx(36.73)

    async def test_an_archive_slice(self, db_session, archive_factory, printer_factory):
        from backend.app.models.archive import PrintArchive

        printer = await printer_factory()
        source = await archive_factory(printer.id, print_name="Two cubes", filename="Two cubes.3mf")

        response = await _to_archive(db_session, source, _sliced_3mf())

        assert response.filament_used_g == pytest.approx(36.73)
        new_archive = await db_session.get(PrintArchive, response.archive_id)
        assert new_archive.extra_data["filament_used_g"] == pytest.approx(36.73)


class TestTheHeaderFallbackSums:
    async def test_without_slice_info_or_sidecar_headers(self, db_session):
        # A build that leaves the X-Filament-Used-* headers unset, on a 3MF
        # without slice_info: only the G-code header is left.
        response = await _to_library(db_session, _sliced_3mf(with_slice_info=False), grams=0.0, mm=0.0)

        assert response.filament_used_g == pytest.approx(36.73)
        assert response.filament_used_mm == pytest.approx(12316.26)
