"""Settings Bambuddy puts on a process preset are marked in the sliced 3MF (#3497).

The slicer CLI writes ``different_settings_to_system`` but leaves it empty, so
a slice-modal override (40% gyroid) was in ``project_settings.config`` without
being listed as changed from the system preset. Bambu Studio opened the
project with the stock preset's values instead, and a re-slice there dropped
the override.
"""

import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.api.routes.library import _run_slicer_with_fallback
from backend.app.schemas.slicer import PresetRef, SliceRequest
from backend.app.schemas.slicer_presets import UnifiedPreset
from backend.app.services.design_settings import changed_process_keys, record_process_changes

pytestmark = pytest.mark.unit

_SETTINGS = "Metadata/project_settings.config"


def _sliced_3mf(changed: list[str], filaments: int = 1, **extra) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("3D/3dmodel.model", "<model/>")
        archive.writestr("Metadata/plate_1.gcode", "M1002 gcode_claim_action : 1\n")
        archive.writestr(
            _SETTINGS,
            json.dumps(
                {
                    "machine_start_gcode": "M1002 gcode_claim_action : 1\nM620 M\n",
                    "print_settings_id": "0.20mm Standard @BBL A1M",
                    "filament_settings_id": ["Bambu PLA Basic @BBL A1M"] * filaments,
                    "different_settings_to_system": changed,
                    **extra,
                }
            ),
        )
    return buffer.getvalue()


def _config(zip_bytes: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        return json.loads(archive.read(_SETTINGS))


class TestRecord:
    def test_the_keys_land_in_the_process_entry(self):
        out = record_process_changes(_sliced_3mf(["", "", ""]), ["sparse_infill_density", "sparse_infill_pattern"])

        assert _config(out)["different_settings_to_system"] == ["sparse_infill_density;sparse_infill_pattern", "", ""]

    def test_keys_already_listed_stay(self):
        out = record_process_changes(
            _sliced_3mf(["wall_loops;enable_support", "filament_flow_ratio", "machine_start_gcode"]),
            ["sparse_infill_density", "wall_loops"],
        )

        assert _config(out)["different_settings_to_system"] == [
            "enable_support;sparse_infill_density;wall_loops",
            "filament_flow_ratio",
            "machine_start_gcode",
        ]

    @pytest.mark.parametrize("filaments", [2, 3, 4])
    def test_only_the_process_entry_changes_with_several_filaments(self, filaments):
        changed = [""] * (filaments + 2)

        out = record_process_changes(_sliced_3mf(changed, filaments), ["sparse_infill_density"])

        assert _config(out)["different_settings_to_system"] == ["sparse_infill_density"] + [""] * (filaments + 1)

    def test_nothing_to_record_leaves_the_file_alone(self):
        source = _sliced_3mf(["", "", ""])

        assert record_process_changes(source, []) is source

    def test_a_list_that_does_not_match_the_filaments_is_left_alone(self):
        source = _sliced_3mf(["", ""])

        assert record_process_changes(source, ["sparse_infill_density"]) is source

    def test_a_file_without_the_list_is_left_alone(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(_SETTINGS, json.dumps({"filament_settings_id": ["x"]}))
        source = buffer.getvalue()

        assert record_process_changes(source, ["sparse_infill_density"]) is source

    def test_not_a_zip_is_left_alone(self):
        assert record_process_changes(b"; plain gcode\n", ["sparse_infill_density"]) == b"; plain gcode\n"

    def test_every_other_member_is_kept(self):
        source = _sliced_3mf(["", "", ""], layer_height="0.2")

        out = record_process_changes(source, ["sparse_infill_density"])

        with zipfile.ZipFile(io.BytesIO(source)) as before, zipfile.ZipFile(io.BytesIO(out)) as after:
            assert before.namelist() == after.namelist()
            assert before.read("Metadata/plate_1.gcode") == after.read("Metadata/plate_1.gcode")
        assert _config(out)["layer_height"] == "0.2"


class TestChangedKeys:
    def test_added_and_changed_keys(self):
        before = json.dumps({"name": "x", "inherits": "x", "wall_loops": "2"})
        after = json.dumps({"name": "x", "inherits": "x", "wall_loops": "3", "sparse_infill_density": "40%"})

        assert changed_process_keys(before, after) == ["sparse_infill_density", "wall_loops"]

    def test_unparseable_json_records_nothing(self):
        assert changed_process_keys("{", json.dumps({"a": "1"})) == []


def _request(
    process_source: str = "standard", process_id: str = "0.20mm Standard @BBL A1M", **overrides
) -> SliceRequest:
    return SliceRequest(
        printer_preset=PresetRef(source="standard", id="Bambu Lab A1 mini 0.4 nozzle"),
        process_preset=PresetRef(source=process_source, id=process_id),
        filament_presets=[PresetRef(source="standard", id="Bambu PLA Basic @BBL A1M")],
        export_3mf=True,
        **overrides,
    )


def _source_3mf() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("3D/3dmodel.model", "<model/>")
        archive.writestr(
            _SETTINGS,
            json.dumps(
                {
                    "enable_support": "1",
                    "support_type": "tree(auto)",
                    "filament_settings_id": ["Bambu PLA Basic @BBL A1M"],
                    "different_settings_to_system": ["enable_support;support_type", "", ""],
                }
            ),
        )
        archive.writestr("Metadata/model_settings.config", "<config><object id='1'/></config>")
        archive.writestr(
            "Metadata/slice_info.config",
            "<config><plate><metadata key='index' value='1'/></plate></config>",
        )
    return buffer.getvalue()


def _cloud_listing(*presets: UnifiedPreset, status: str = "ok"):
    slots = {"printer": [], "process": list(presets), "filament": []}
    return AsyncMock(return_value=(slots, status))


async def _slice(request: SliceRequest, *, model: str = "stl", embedded_fallback: bool = False, cloud_listing=None):
    from backend.app.services import preset_resolver, slicer_api as slicer_api_module

    if cloud_listing is None:
        cloud_listing = _cloud_listing(status="not_authenticated")

    result = slicer_api_module.SliceResult(
        content=_sliced_3mf(["", "", ""]),
        print_time_seconds=600,
        filament_used_g=12.0,
        filament_used_mm=4000.0,
    )
    service = MagicMock()
    service.close = AsyncMock()
    if embedded_fallback:
        service.slice_with_profiles = AsyncMock(side_effect=slicer_api_module.SlicerApiServerError("boom"))
        service.slice_without_profiles = AsyncMock(return_value=result)
    else:
        service.slice_with_profiles = AsyncMock(return_value=result)
        service.slice_without_profiles = AsyncMock()

    async def _setting(_db, key):
        return {"preferred_slicer": "bambu_studio", "bambu_studio_api_url": "http://sidecar:3000"}.get(key)

    async def _resolve(_db, _user, ref, slot):
        if ref.source == "standard":
            return preset_resolver._resolve_standard(ref, slot)
        # A cloud or local preset arrives flattened.
        return json.dumps({"name": ref.id, "from": "system", "type": "process", "wall_loops": "3"})

    with (
        patch("backend.app.api.routes.settings.get_setting", new=AsyncMock(side_effect=_setting)),
        patch("backend.app.services.preset_resolver.resolve_preset_ref", new=AsyncMock(side_effect=_resolve)),
        # The fallback runs only for the file's own printer (#3535): the
        # source and the target name the same one.
        patch(
            "backend.app.services.slicer_3mf_convert.extract_source_printer_model",
            return_value="X2D" if embedded_fallback else None,
        ),
        patch(
            "backend.app.api.routes.library._resolve_target_printer_model",
            new=AsyncMock(return_value="X2D" if embedded_fallback else None),
        ),
        patch.object(slicer_api_module, "SlicerApiService", return_value=service),
        patch.object(slicer_api_module, "get_stall_timeout_seconds", new=AsyncMock(return_value=60.0)),
        patch("backend.app.api.routes.slicer_presets._fetch_cloud_presets", new=cloud_listing),
    ):
        result, _ = await _run_slicer_with_fallback(
            SimpleNamespace(get=AsyncMock(return_value=None)),
            model_bytes=_source_3mf() if model == "3mf" else b"solid cube\nendsolid cube\n",
            model_filename=f"cube.{model}",
            request=request,
            current_user_id=None,
        )
    return _config(result.content)["different_settings_to_system"]


@pytest.mark.asyncio
class TestTheSlice:
    async def test_the_reporters_overrides_are_listed(self):
        listed = await _slice(
            _request(process_overrides={"sparse_infill_density": "40%", "sparse_infill_pattern": "gyroid"})
        )

        assert listed == ["sparse_infill_density;sparse_infill_pattern", "", ""]

    async def test_the_build_plate_pick_is_not_listed(self):
        listed = await _slice(_request(bed_type="Textured PEI Plate", process_overrides={"wall_loops": "3"}))

        assert listed == ["wall_loops", "", ""]

    async def test_the_designer_settings_picked_are_listed(self):
        # support_type was offered and left unticked (#2942): not carried, not listed.
        listed = await _slice(_request(design_overrides=["enable_support"]), model="3mf")

        assert listed == ["enable_support", "", ""]

    async def test_the_carried_support_settings_are_listed(self):
        # A client without the per-key choice gets the #1881 support carry.
        listed = await _slice(_request(), model="3mf")

        assert listed == ["enable_support;support_type", "", ""]

    async def test_no_overrides_lists_nothing(self):
        assert await _slice(_request()) == ["", "", ""]

    async def test_a_local_process_preset_lists_nothing(self):
        cloud_listing = _cloud_listing(status="not_authenticated")
        listed = await _slice(
            _request(process_source="local", process_overrides={"sparse_infill_density": "40%"}),
            cloud_listing=cloud_listing,
        )

        assert listed == ["", "", ""]
        # Nor does it ask Bambu Cloud.
        cloud_listing.assert_not_awaited()

    async def test_bambus_stock_process_preset_on_bambu_cloud_lists_them(self):
        # Signed in, the stock presets are picked from Bambu Cloud (#3386).
        listed = await _slice(
            _request(
                process_source="cloud",
                process_id="GP000",
                process_overrides={"sparse_infill_density": "40%", "sparse_infill_pattern": "gyroid"},
            ),
            cloud_listing=_cloud_listing(
                UnifiedPreset(id="GP000", name="0.20mm Standard @BBL A1M", source="cloud", stock=True)
            ),
        )

        assert listed == ["sparse_infill_density;sparse_infill_pattern", "", ""]

    async def test_a_private_bambu_cloud_process_preset_lists_nothing(self):
        listed = await _slice(
            _request(process_source="cloud", process_id="PPUS1", process_overrides={"sparse_infill_density": "40%"}),
            cloud_listing=_cloud_listing(
                UnifiedPreset(id="PPUS1", name="My 0.20mm", source="cloud", stock=False),
                UnifiedPreset(id="GP000", name="0.20mm Standard @BBL A1M", source="cloud", stock=True),
            ),
        )

        assert listed == ["", "", ""]

    async def test_a_cloud_preset_without_the_listing_lists_nothing(self):
        listed = await _slice(
            _request(process_source="cloud", process_id="GP000", process_overrides={"sparse_infill_density": "40%"}),
            cloud_listing=_cloud_listing(status="unreachable"),
        )

        assert listed == ["", "", ""]

    async def test_the_embedded_settings_fallback_lists_nothing(self):
        listed = await _slice(
            _request(process_overrides={"sparse_infill_density": "40%"}), model="3mf", embedded_fallback=True
        )

        assert listed == ["", "", ""]
