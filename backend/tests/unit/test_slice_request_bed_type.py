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

import pytest
from pydantic import ValidationError

from backend.app.api.routes.library import _patch_process_bed_type
from backend.app.schemas.slicer import PresetRef, SliceRequest


class TestSliceRequestBedTypeField:
    def test_bed_type_defaults_to_none(self):
        req = SliceRequest(
            printer_preset=PresetRef(source="local", id="1"),
            process_preset=PresetRef(source="local", id="2"),
            filament_preset=PresetRef(source="local", id="3"),
        )
        assert req.bed_type is None

    def test_bed_type_accepts_canonical_values(self):
        for value in (
            "Cool Plate",
            "Cool Plate (SuperTack)",
            "Engineering Plate",
            "High Temp Plate",
            "Textured PEI Plate",
            "Smooth PEI Plate",
        ):
            req = SliceRequest(
                printer_preset=PresetRef(source="local", id="1"),
                process_preset=PresetRef(source="local", id="2"),
                filament_preset=PresetRef(source="local", id="3"),
                bed_type=value,
            )
            assert req.bed_type == value

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
