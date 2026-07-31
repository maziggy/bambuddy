"""Tests for carrying a 3MF designer's process tweaks across a re-slice (#2622).

The layout asserted here — ``different_settings_to_system`` being
``[process, *filaments, printer]`` — was verified against real BambuStudio files
at 2, 3 and 4 filament slots before this was written. Getting the index wrong
would carry ``machine_start_gcode`` from the designer's printer onto the user's,
so the parser refuses any file whose array length disagrees with its own filament
count rather than guessing.
"""

import io
import json
import zipfile

from backend.app.services.design_settings import (
    DesignOverride,
    apply_design_overrides,
    extract_design_process_overrides,
    is_printer_coupled,
    overrides_from_config,
)


def _config(**overrides) -> dict:
    """A minimal project_settings.config with two filament slots."""
    base = {
        "print_settings_id": "0.20mm Standard @BBL A1",
        "printer_settings_id": "Bambu Lab A1 0.4 nozzle",
        "filament_settings_id": ["Bambu PLA Basic @BBL A1", "Bambu PLA Matte @BBL A1"],
        "wall_loops": "5",
        "sparse_infill_density": "100%",
        "initial_layer_print_height": "0.1",
        "outer_wall_speed": "200",
        "machine_start_gcode": "G28 ; designer printer",
        "different_settings_to_system": [
            "wall_loops;sparse_infill_density;initial_layer_print_height;outer_wall_speed",
            "",
            "",
            "machine_start_gcode",
        ],
    }
    base.update(overrides)
    return base


def _3mf(config: dict | None, *, include_config: bool = True) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
        if include_config:
            zf.writestr("Metadata/project_settings.config", json.dumps(config))
    return buffer.getvalue()


class TestClassification:
    def test_geometry_and_quality_keys_are_portable(self):
        for key in (
            "wall_loops",
            "sparse_infill_density",
            "sparse_infill_pattern",
            "initial_layer_print_height",
            "layer_height",
            "enable_support",
            "brim_type",
            "seam_position",
            "ironing_type",
        ):
            assert is_printer_coupled(key) is False, key

    def test_kinematic_and_thermal_keys_are_printer_coupled(self):
        for key in (
            "outer_wall_speed",
            "inner_wall_speed",
            "internal_solid_infill_speed",
            "travel_acceleration",
            "default_acceleration",
            "default_jerk",
            "overhang_fan_speed",
            "nozzle_temperature",
            "prime_tower_max_speed",
            "prime_tower_width",
            "prime_tower_rib_wall",
            "prime_tower_infill_gap",
            "enable_prime_tower",
            "independent_support_layer_height",
            "precise_z_height",
        ):
            assert is_printer_coupled(key) is True, key


class TestExtraction:
    def test_reads_the_process_slot_and_classifies_each_key(self):
        overrides = extract_design_process_overrides(_3mf(_config()))

        assert [o.key for o in overrides] == [
            "initial_layer_print_height",
            "outer_wall_speed",
            "sparse_infill_density",
            "wall_loops",
        ]
        by_key = {o.key: o for o in overrides}
        assert by_key["wall_loops"].value == "5"
        assert by_key["sparse_infill_density"].value == "100%"
        assert by_key["wall_loops"].printer_coupled is False
        assert by_key["outer_wall_speed"].printer_coupled is True

    def test_never_surfaces_the_printer_slot(self):
        # machine_start_gcode is listed in the printer entry, not the process
        # one. Carrying it would push the designer's start G-code onto another
        # machine — the exact failure the length check exists to prevent.
        overrides = extract_design_process_overrides(_3mf(_config()))
        assert "machine_start_gcode" not in {o.key for o in overrides}

    def test_rejects_an_array_whose_length_contradicts_the_filament_count(self):
        # Three entries for two filaments: the layout is not the one we know,
        # so index 0 might not be the process slot. Refuse rather than guess.
        cfg = _config(different_settings_to_system=["wall_loops", "", ""])
        assert extract_design_process_overrides(_3mf(cfg)) == []

    def test_skips_keys_absent_from_the_flattened_config(self):
        cfg = _config(different_settings_to_system=["wall_loops;renamed_in_a_later_slicer", "", "", ""])
        assert [o.key for o in extract_design_process_overrides(_3mf(cfg))] == ["wall_loops"]

    def test_empty_process_entry_yields_nothing(self):
        cfg = _config(different_settings_to_system=["", "", "", "machine_start_gcode"])
        assert extract_design_process_overrides(_3mf(cfg)) == []

    def test_files_without_the_field_yield_nothing(self):
        cfg = _config()
        del cfg["different_settings_to_system"]
        assert extract_design_process_overrides(_3mf(cfg)) == []

    def test_files_without_project_settings_yield_nothing(self):
        assert extract_design_process_overrides(_3mf(None, include_config=False)) == []

    def test_malformed_input_yields_nothing(self):
        assert extract_design_process_overrides(b"not a zip") == []
        assert overrides_from_config("not a dict") == []
        assert overrides_from_config({"different_settings_to_system": "not a list"}) == []

    def test_tolerates_a_missing_filament_list(self):
        # No filament_settings_id to cross-check against — index 0 is still the
        # documented process slot, so parse it rather than bailing out.
        cfg = _config()
        del cfg["filament_settings_id"]
        assert [o.key for o in extract_design_process_overrides(_3mf(cfg))] == [
            "initial_layer_print_height",
            "outer_wall_speed",
            "sparse_infill_density",
            "wall_loops",
        ]


class TestApply:
    def _overrides(self) -> list[DesignOverride]:
        return extract_design_process_overrides(_3mf(_config()))

    def test_writes_only_the_selected_keys(self):
        process = json.dumps({"inherits": "0.20mm Standard @BBL X1C", "from": "system", "wall_loops": "2"})

        patched = json.loads(apply_design_overrides(process, self._overrides(), ["wall_loops"]))

        assert patched["wall_loops"] == "5"
        # Not selected — the picked preset's own value must survive.
        assert "sparse_infill_density" not in patched
        assert "outer_wall_speed" not in patched
        # The inherits stub is what makes the patch win over the flattened
        # parent inside the sidecar; it must not be disturbed.
        assert patched["inherits"] == "0.20mm Standard @BBL X1C"
        assert patched["from"] == "system"

    def test_a_key_the_source_never_flagged_is_ignored(self):
        process = json.dumps({"inherits": "x"})

        patched = json.loads(apply_design_overrides(process, self._overrides(), ["layer_height"]))

        assert "layer_height" not in patched

    def test_printer_coupled_keys_apply_when_explicitly_selected(self):
        process = json.dumps({"inherits": "x"})

        patched = json.loads(apply_design_overrides(process, self._overrides(), ["outer_wall_speed"]))

        assert patched["outer_wall_speed"] == "200"

    def test_no_selection_is_a_no_op(self):
        process = json.dumps({"inherits": "x"})
        assert apply_design_overrides(process, self._overrides(), []) == process

    def test_unparseable_process_json_is_returned_untouched(self):
        assert apply_design_overrides("{not json", self._overrides(), ["wall_loops"]) == "{not json"
