"""Unit tests for OrcaSlicer profile import service.

Tests _guess_profile_type, _parse_material_from_name, _parse_vendor_from_name,
and extract_core_fields.
"""

import json

import pytest


class TestGuessProfileType:
    """Tests for _guess_profile_type()."""

    def test_explicit_filament_type(self):
        from backend.app.services.orca_profiles import _guess_profile_type

        data = {"type": "filament", "name": "Some Filament"}
        assert _guess_profile_type(data) == "filament"

    def test_explicit_process_type(self):
        from backend.app.services.orca_profiles import _guess_profile_type

        data = {"type": "process", "name": "0.20mm Standard"}
        assert _guess_profile_type(data) == "process"

    def test_explicit_machine_type(self):
        from backend.app.services.orca_profiles import _guess_profile_type

        data = {"type": "machine", "name": "Bambu Lab X1C"}
        assert _guess_profile_type(data) == "printer"

    def test_explicit_printer_type(self):
        from backend.app.services.orca_profiles import _guess_profile_type

        data = {"type": "printer", "name": "Bambu Lab X1C"}
        assert _guess_profile_type(data) == "printer"

    def test_explicit_print_type(self):
        from backend.app.services.orca_profiles import _guess_profile_type

        data = {"type": "print", "name": "0.20mm Standard"}
        assert _guess_profile_type(data) == "process"

    def test_path_hint_filament(self):
        from backend.app.services.orca_profiles import _guess_profile_type

        data = {"name": "Some Preset"}
        assert _guess_profile_type(data, path_hint="filament/MyPreset.json") == "filament"

    def test_path_hint_process(self):
        from backend.app.services.orca_profiles import _guess_profile_type

        data = {"name": "Some Preset"}
        assert _guess_profile_type(data, path_hint="process/MyProcess.json") == "process"

    def test_path_hint_machine(self):
        from backend.app.services.orca_profiles import _guess_profile_type

        data = {"name": "Some Preset"}
        assert _guess_profile_type(data, path_hint="machine/MyPrinter.json") == "printer"

    def test_print_settings_id_indicates_process(self):
        from backend.app.services.orca_profiles import _guess_profile_type

        data = {"name": "# 0.08mm Extra Fine @BBL H2D", "print_settings_id": "# 0.08mm Extra Fine @BBL H2D"}
        assert _guess_profile_type(data) == "process"

    def test_filament_settings_id_indicates_filament(self):
        from backend.app.services.orca_profiles import _guess_profile_type

        data = {"name": "eSUN PLA", "filament_settings_id": "eSUN PLA"}
        assert _guess_profile_type(data) == "filament"

    def test_printer_settings_id_indicates_printer(self):
        from backend.app.services.orca_profiles import _guess_profile_type

        data = {"name": "Bambu Lab X1C", "printer_settings_id": "Bambu Lab X1C"}
        assert _guess_profile_type(data) == "printer"

    def test_prime_tower_keys_indicate_process(self):
        from backend.app.services.orca_profiles import _guess_profile_type

        data = {
            "name": "# 0.16mm High Quality",
            "prime_tower_width": "20",
            "prime_tower_max_speed": "100",
        }
        assert _guess_profile_type(data) == "process"

    def test_outer_wall_speed_indicates_process(self):
        from backend.app.services.orca_profiles import _guess_profile_type

        data = {"name": "H2D eSUN PETG Process", "outer_wall_speed": ["150"]}
        assert _guess_profile_type(data) == "process"

    def test_layer_height_indicates_process(self):
        from backend.app.services.orca_profiles import _guess_profile_type

        data = {"name": "Standard", "layer_height": "0.2", "first_layer_height": "0.2"}
        assert _guess_profile_type(data) == "process"

    def test_machine_keys_indicate_printer(self):
        from backend.app.services.orca_profiles import _guess_profile_type

        data = {"name": "My Printer", "machine_max_speed_x": "500", "bed_shape": "0x0,220x0,220x220,0x220"}
        assert _guess_profile_type(data) == "printer"

    def test_filament_type_indicates_filament(self):
        from backend.app.services.orca_profiles import _guess_profile_type

        data = {"name": "Generic PLA", "filament_type": ["PLA"]}
        assert _guess_profile_type(data) == "filament"

    def test_name_with_layer_height_pattern(self):
        from backend.app.services.orca_profiles import _guess_profile_type

        data = {"name": "0.20mm Standard @BBL X1C"}
        assert _guess_profile_type(data) == "process"

    def test_name_ending_with_process(self):
        from backend.app.services.orca_profiles import _guess_profile_type

        data = {"name": "H2D eSUN PETG Process"}
        assert _guess_profile_type(data) == "process"

    def test_default_to_filament(self):
        from backend.app.services.orca_profiles import _guess_profile_type

        data = {"name": "Unknown Preset"}
        assert _guess_profile_type(data) == "filament"

    def test_override_keys_only_process(self):
        """Test realistic override-only process preset (inheritance unresolved)."""
        from backend.app.services.orca_profiles import _guess_profile_type

        data = {
            "from": "User",
            "inherits": "0.08mm Extra Fine @BBL H2D",
            "name": "# 0.08mm Extra Fine @BBL H2D",
            "prime_tower_max_speed": "100",
            "prime_tower_rib_wall": "0",
            "prime_tower_width": "20",
            "print_extruder_id": ["1", "1"],
            "print_settings_id": "# 0.08mm Extra Fine @BBL H2D",
            "version": "2.3.0.4",
        }
        assert _guess_profile_type(data) == "process"


class TestParseMaterialFromName:
    """Tests for _parse_material_from_name()."""

    def test_pla_in_name(self):
        from backend.app.services.orca_profiles import _parse_material_from_name

        assert _parse_material_from_name("Overture PLA Matte @BBL X1C") == "PLA"

    def test_abs_in_name(self):
        from backend.app.services.orca_profiles import _parse_material_from_name

        assert _parse_material_from_name("CR3D ABS+ @Bambu Lab X1 Carbon") == "ABS"

    def test_petg_in_name(self):
        from backend.app.services.orca_profiles import _parse_material_from_name

        assert _parse_material_from_name("eSUN PETG Silk @Bambu Lab X1 Carbon") == "PETG"

    def test_tpu_in_name(self):
        from backend.app.services.orca_profiles import _parse_material_from_name

        assert _parse_material_from_name("Sunlu TPU @Bambu Lab X1 Carbon") == "TPU"

    def test_no_material_in_name(self):
        from backend.app.services.orca_profiles import _parse_material_from_name

        assert _parse_material_from_name("# 0.20mm Standard @BBL X1C") is None

    def test_material_word_boundary(self):
        from backend.app.services.orca_profiles import _parse_material_from_name

        # "PLA" should match as a word, not inside "DISPLAY"
        assert _parse_material_from_name("Bambu PLA Basic @BBL X1C") == "PLA"

    def test_asa_in_name(self):
        from backend.app.services.orca_profiles import _parse_material_from_name

        assert _parse_material_from_name("Bambu ASA-CF @BBL H2D") == "ASA"

    def test_pa_in_name(self):
        from backend.app.services.orca_profiles import _parse_material_from_name

        # "PA12" doesn't match \bPA\b because 1 is a word char — PA needs word boundary
        assert _parse_material_from_name("Fiberlogy PA12+CF15") is None
        assert _parse_material_from_name("Fiberlogy PA @BBL X1C") == "PA"


class TestParseVendorFromName:
    """Tests for _parse_vendor_from_name()."""

    def test_overture_vendor(self):
        from backend.app.services.orca_profiles import _parse_vendor_from_name

        assert _parse_vendor_from_name("Overture PLA Matte @BBL X1C") == "Overture"

    def test_esun_vendor(self):
        from backend.app.services.orca_profiles import _parse_vendor_from_name

        assert _parse_vendor_from_name("eSUN PETG @Bambu Lab H2D") == "eSUN"

    def test_bambu_vendor(self):
        from backend.app.services.orca_profiles import _parse_vendor_from_name

        assert _parse_vendor_from_name("Bambu PLA Basic @BBL X1C") == "Bambu"

    def test_devil_design_vendor(self):
        from backend.app.services.orca_profiles import _parse_vendor_from_name

        assert _parse_vendor_from_name("Devil Design PLA @Bambu Lab X1 Carbon") == "Devil Design"

    def test_no_vendor_process_name(self):
        from backend.app.services.orca_profiles import _parse_vendor_from_name

        assert _parse_vendor_from_name("# 0.20mm Standard @BBL X1C") is None

    def test_strips_at_suffix(self):
        from backend.app.services.orca_profiles import _parse_vendor_from_name

        # Should strip @BBL X1C before parsing
        result = _parse_vendor_from_name("Azurefilm PLA Wood @Bambu Lab H2D 0.4 nozzle")
        assert result == "Azurefilm"

    def test_single_char_vendor_rejected(self):
        from backend.app.services.orca_profiles import _parse_vendor_from_name

        # Vendor must be >1 char
        assert _parse_vendor_from_name("X PLA") is None


class TestExtractCoreFields:
    """Tests for extract_core_fields()."""

    def test_filament_type_array(self):
        from backend.app.services.orca_profiles import extract_core_fields

        core = extract_core_fields({"filament_type": ["PLA"]})
        assert core["filament_type"] == "PLA"

    def test_filament_type_string(self):
        from backend.app.services.orca_profiles import extract_core_fields

        core = extract_core_fields({"filament_type": "ABS"})
        assert core["filament_type"] == "ABS"

    def test_filament_vendor_array(self):
        from backend.app.services.orca_profiles import extract_core_fields

        core = extract_core_fields({"filament_vendor": ["Bambu Lab"]})
        assert core["filament_vendor"] == "Bambu Lab"

    def test_nozzle_temp_from_array(self):
        from backend.app.services.orca_profiles import extract_core_fields

        core = extract_core_fields({"nozzle_temperature": ["220"]})
        assert core["nozzle_temp_min"] == 220
        assert core["nozzle_temp_max"] == 220

    def test_nozzle_temp_range_override(self):
        from backend.app.services.orca_profiles import extract_core_fields

        core = extract_core_fields(
            {
                "nozzle_temperature": ["220"],
                "nozzle_temperature_range_low": ["190"],
                "nozzle_temperature_range_high": ["230"],
            }
        )
        assert core["nozzle_temp_min"] == 190
        assert core["nozzle_temp_max"] == 230

    def test_pressure_advance_array(self):
        from backend.app.services.orca_profiles import extract_core_fields

        core = extract_core_fields({"pressure_advance": ["0.04"]})
        assert core["pressure_advance"] == json.dumps(["0.04"])

    def test_default_filament_colour(self):
        from backend.app.services.orca_profiles import extract_core_fields

        core = extract_core_fields({"default_filament_colour": ["#FFAA00"]})
        assert "#FFAA00" in core["default_filament_colour"]

    def test_filament_cost_array(self):
        from backend.app.services.orca_profiles import extract_core_fields

        core = extract_core_fields({"filament_cost": ["24.99"]})
        assert core["filament_cost"] == "24.99"

    def test_filament_density(self):
        from backend.app.services.orca_profiles import extract_core_fields

        core = extract_core_fields({"filament_density": ["1.24"]})
        assert core["filament_density"] == "1.24"

    def test_compatible_printers(self):
        from backend.app.services.orca_profiles import extract_core_fields

        core = extract_core_fields({"compatible_printers": ["Bambu Lab X1 Carbon", "Bambu Lab P1S"]})
        parsed = json.loads(core["compatible_printers"])
        assert "Bambu Lab X1 Carbon" in parsed
        assert "Bambu Lab P1S" in parsed

    def test_empty_data(self):
        from backend.app.services.orca_profiles import extract_core_fields

        core = extract_core_fields({})
        assert core == {}

    def test_full_resolved_preset(self):
        """Test extraction from a realistic fully resolved preset."""
        from backend.app.services.orca_profiles import extract_core_fields

        data = {
            "filament_type": ["PETG"],
            "filament_vendor": ["eSUN"],
            "nozzle_temperature": ["240"],
            "nozzle_temperature_range_low": ["220"],
            "nozzle_temperature_range_high": ["260"],
            "pressure_advance": ["0.035"],
            "default_filament_colour": ["#4A90D9"],
            "filament_cost": ["19.99"],
            "filament_density": ["1.27"],
            "compatible_printers": ["Bambu Lab X1 Carbon 0.4 nozzle"],
        }
        core = extract_core_fields(data)
        assert core["filament_type"] == "PETG"
        assert core["filament_vendor"] == "eSUN"
        assert core["nozzle_temp_min"] == 220
        assert core["nozzle_temp_max"] == 260
        assert core["filament_cost"] == "19.99"
        assert core["filament_density"] == "1.27"
