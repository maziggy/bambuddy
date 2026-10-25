"""Regression tests for the #1337 bed-type override on slice requests.

The SliceRequest schema accepts an optional `bed_type` string that the
slice route patches onto the resolved process-profile JSON as
`curr_bed_type` before forwarding to the sidecar. Without this hook,
slicing high-temp filaments (ABS, ASA, PC) onto a process preset whose
default plate is "Cool Plate" fails with the slicer-CLI error
"Plate 1: Cool Plate does not support filament 1" and the user has no
way to switch plates short of cloning the preset.
"""

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.app.api.routes.library import _patch_process_bed_type
from backend.app.api.routes.pipeline_runs import _slice_request_from_pipeline
from backend.app.models.slicer_pipeline import SlicerPipeline
from backend.app.schemas.slicer import PresetRef, SliceRequest
from backend.app.schemas.slicer_pipeline import SlicerPipelineCreate, SlicerPipelineResponse, SlicerPipelineUpdate


def _request(bed_type):
    return SliceRequest(
        printer_preset=PresetRef(source="local", id="1"),
        process_preset=PresetRef(source="local", id="2"),
        filament_preset=PresetRef(source="local", id="3"),
        bed_type=bed_type,
    )


class TestSliceRequestBedTypeField:
    def test_bed_type_defaults_to_none(self):
        req = SliceRequest(
            printer_preset=PresetRef(source="local", id="1"),
            process_preset=PresetRef(source="local", id="2"),
            filament_preset=PresetRef(source="local", id="3"),
        )
        assert req.bed_type is None

    @pytest.mark.parametrize(
        "value",
        [
            "Cool Plate",
            "Engineering Plate",
            "High Temp Plate",
            "Textured PEI Plate",
            "Supertack Plate",
            "Textured Cool Plate",
        ],
    )
    def test_bed_type_accepts_the_slicers_names(self, value):
        assert _request(value).bed_type == value

    @pytest.mark.parametrize(
        ("old", "slicer_name"),
        [("Cool Plate (SuperTack)", "Supertack Plate"), ("Smooth PEI Plate", "High Temp Plate")],
    )
    def test_the_names_bambuddy_used_to_send_become_the_slicers_names(self, old, slicer_name):
        """The slicer CLI slices an unknown name as Cool Plate (#3359)."""
        assert _request(old).bed_type == slicer_name

    @pytest.mark.parametrize("value", ["Smooth PEI", "cool plate", "Textured Plate", "Default Plate"])
    def test_a_name_no_slicer_knows_is_refused(self, value):
        with pytest.raises(ValidationError, match="unknown bed_type"):
            _request(value)

    def test_an_empty_bed_type_is_still_no_override(self):
        assert _request("").bed_type == ""

    def test_bed_type_rejects_overlong_input(self):
        with pytest.raises(ValidationError):
            SliceRequest(
                printer_preset=PresetRef(source="local", id="1"),
                process_preset=PresetRef(source="local", id="2"),
                filament_preset=PresetRef(source="local", id="3"),
                bed_type="x" * 65,
            )


class TestPatchProcessBedType:
    def test_overwrites_existing_curr_bed_type(self):
        process_json = json.dumps({"name": "0.20mm Standard", "curr_bed_type": "Cool Plate"})
        result = _patch_process_bed_type(process_json, "Textured PEI Plate")
        assert json.loads(result)["curr_bed_type"] == "Textured PEI Plate"

    def test_adds_curr_bed_type_when_missing(self):
        process_json = json.dumps({"name": "0.20mm Standard"})
        result = _patch_process_bed_type(process_json, "Engineering Plate")
        parsed = json.loads(result)
        assert parsed["curr_bed_type"] == "Engineering Plate"
        # Other fields preserved
        assert parsed["name"] == "0.20mm Standard"

    def test_returns_input_unchanged_when_json_is_invalid(self):
        # The slicer would error on this anyway; the patch helper is a
        # straight passthrough so failure modes stay attributable to the
        # original input rather than the patch.
        bogus = "not a json document"
        assert _patch_process_bed_type(bogus, "Cool Plate") is bogus

    def test_returns_input_unchanged_when_json_is_not_a_dict(self):
        not_a_dict = json.dumps(["this", "is", "an", "array"])
        assert _patch_process_bed_type(not_a_dict, "Cool Plate") is not_a_dict


class TestAPipelineSavedWithAnOldName:
    """Pipelines store the bed type the modal sent before #3359."""

    def _pipeline(self, bed_type):
        return SlicerPipeline(
            name="p",
            printer_preset_source="local",
            printer_preset_id="1",
            process_preset_source="local",
            process_preset_id="2",
            filament_presets_json='[{"source": "local", "id": "3"}]',
            bed_type=bed_type,
        )

    def test_its_run_slices_on_the_supertack_plate(self):
        assert _slice_request_from_pipeline(self._pipeline("Cool Plate (SuperTack)")).bed_type == "Supertack Plate"

    def test_it_reads_back_with_the_slicers_name(self):
        response = SlicerPipelineResponse(
            id=1,
            created_by=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            name="p",
            printer_preset=PresetRef(source="local", id="1"),
            process_preset=PresetRef(source="local", id="2"),
            filament_presets=[PresetRef(source="local", id="3")],
            bed_type="Smooth PEI Plate",
        )
        assert response.bed_type == "High Temp Plate"

    def test_a_row_with_an_unknown_name_still_reads_back(self):
        """The list endpoint must not break on a row it would now refuse."""
        response = SlicerPipelineResponse(
            id=1,
            created_by=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            name="p",
            printer_preset=PresetRef(source="local", id="1"),
            process_preset=PresetRef(source="local", id="2"),
            filament_presets=[PresetRef(source="local", id="3")],
            bed_type="Glass Plate",
        )
        assert response.bed_type == "Glass Plate"

    @pytest.mark.parametrize("schema", [SlicerPipelineCreate, SlicerPipelineUpdate])
    def test_saving_one_converts_old_names_and_refuses_unknown_ones(self, schema):
        fields = {
            "name": "p",
            "printer_preset": {"source": "local", "id": "1"},
            "process_preset": {"source": "local", "id": "2"},
            "filament_presets": [{"source": "local", "id": "3"}],
        }
        assert schema(**fields, bed_type="Cool Plate (SuperTack)").bed_type == "Supertack Plate"
        with pytest.raises(ValidationError, match="unknown bed_type"):
            schema(**fields, bed_type="Glass Plate")
