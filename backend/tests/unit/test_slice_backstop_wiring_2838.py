"""``_run_slicer_with_fallback`` refuses a start-G-code-less slice (#2838).

The check itself is pinned in ``test_slice_output_check_2838.py``. This covers
where it is wired: which slices it judges and which it lets past. Getting that
scope wrong in either direction is worse than the defect — too narrow and the
in-air print still ships, too wide and a user's own printer profile with a
hand-written start block stops slicing.
"""

import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.app.api.routes.library import _run_slicer_with_fallback
from backend.app.schemas.slicer import PresetRef, SliceRequest

pytestmark = pytest.mark.unit

GENERIC_START = "M17 X1.2 Y1.2 Z0.75\nG28 X\nM104 S140\n"
REAL_START = "M1002 gcode_claim_action : 1\nM620 M\nM620.10 A0 F74.8347 H0.4 C\n"


def _sliced_3mf(start_gcode: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("3D/3dmodel.model", "<model/>")
        archive.writestr(
            "Metadata/project_settings.config",
            json.dumps({"machine_start_gcode": start_gcode}),
        )
    return buffer.getvalue()


def _source_3mf() -> bytes:
    """A source file complete enough for the wrapper's 3MF pre-processing.

    The embedded-settings fallback is 3MF-only — there is nothing for an STL
    to fall back *to* — so that case cannot be driven with a plain model.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("3D/3dmodel.model", "<model/>")
        archive.writestr("Metadata/project_settings.config", json.dumps({"layer_height": "0.2"}))
        archive.writestr("Metadata/model_settings.config", "<config><object id='1'/></config>")
        archive.writestr(
            "Metadata/slice_info.config",
            "<config><plate><metadata key='index' value='1'/></plate></config>",
        )
    return buffer.getvalue()


def _request(printer_source: str) -> SliceRequest:
    return SliceRequest(
        printer_preset=PresetRef(source=printer_source, id="Bambu Lab X2D 0.4 nozzle"),
        process_preset=PresetRef(source="standard", id="0.20mm Standard @BBL X2D"),
        filament_presets=[PresetRef(source="standard", id="Bambu PLA Basic @BBL X2D")],
        export_3mf=True,
    )


async def _run(
    request: SliceRequest,
    *,
    start_gcode: str,
    embedded_fallback: bool = False,
):
    """Drive the wrapper with a sidecar that returns exactly these bytes."""
    from backend.app.services import slicer_api as slicer_api_module

    result = slicer_api_module.SliceResult(
        content=_sliced_3mf(start_gcode),
        print_time_seconds=600,
        filament_used_g=12.0,
        filament_used_mm=4000.0,
    )

    service = MagicMock()
    service.close = AsyncMock()
    if embedded_fallback:
        # The real fallback: the CLI dies on the --load-settings path, so the
        # slice is re-run against the settings baked into the source file.
        # A generic failure on purpose — a message that reads as a content
        # rejection is surfaced instead of retried.
        service.slice_with_profiles = AsyncMock(side_effect=slicer_api_module.SlicerApiServerError("boom"))
        service.slice_without_profiles = AsyncMock(return_value=result)
    else:
        service.slice_with_profiles = AsyncMock(return_value=result)
        service.slice_without_profiles = AsyncMock()

    async def _setting(_db, key):
        return {"preferred_slicer": "bambu_studio", "bambu_studio_api_url": "http://sidecar:3000"}.get(key)

    with (
        patch("backend.app.api.routes.settings.get_setting", new=AsyncMock(side_effect=_setting)),
        patch(
            "backend.app.services.preset_resolver.resolve_preset_ref",
            new=AsyncMock(return_value=json.dumps({"name": "x", "from": "system", "type": "machine"})),
        ),
        patch.object(slicer_api_module, "SlicerApiService", return_value=service),
        patch.object(slicer_api_module, "get_stall_timeout_seconds", new=AsyncMock(return_value=60.0)),
    ):
        return await _run_slicer_with_fallback(
            SimpleNamespace(get=AsyncMock(return_value=None)),
            model_bytes=_source_3mf() if embedded_fallback else b"solid cube\nendsolid cube\n",
            model_filename="cube.3mf" if embedded_fallback else "cube.stl",
            request=request,
            current_user_id=None,
        )


class TestItRefusesTheDefect:
    async def test_a_standard_preset_with_no_start_gcode_is_refused(self):
        with pytest.raises(HTTPException) as exc:
            await _run(_request("standard"), start_gcode=GENERIC_START)

        assert exc.value.status_code == 502
        assert "Bambu Lab X2D 0.4 nozzle" in exc.value.detail
        assert "sidecar" in exc.value.detail

    async def test_the_same_slice_with_real_start_gcode_goes_through(self):
        result, used_embedded = await _run(_request("standard"), start_gcode=REAL_START)

        assert used_embedded is False
        assert result.print_time_seconds == 600


class TestItDoesNotJudgeProfilesBambuddyDidNotResolve:
    """The bundle is what makes the absence conclusive. Outside it, the start
    block is the user's to author and an empty one may well be deliberate."""

    @pytest.mark.parametrize("source", ["local", "cloud", "orca_cloud"])
    async def test_other_tiers_slice_normally(self, source):
        result, _ = await _run(_request(source), start_gcode=GENERIC_START)

        assert result.print_time_seconds == 600

    async def test_the_embedded_settings_fallback_is_left_alone(self):
        """That path prints the source file's own settings — the preset we
        picked was never applied, so it cannot be the thing at fault."""
        result, used_embedded = await _run(_request("standard"), start_gcode=GENERIC_START, embedded_fallback=True)

        assert used_embedded is True
        assert result.print_time_seconds == 600
