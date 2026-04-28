"""Unit tests for 3MF parsing utilities (threemf_tools.py).

Tests G-code parsing, filament length-to-weight conversion,
and cumulative layer usage lookup.
"""

import io
import json
import math
import zipfile

from backend.app.utils.threemf_tools import (
    extract_filament_usage_from_3mf,
    extract_plate_extruder_set_from_3mf,
    extract_project_filaments_from_3mf,
    get_cumulative_usage_at_layer,
    mm_to_grams,
    parse_gcode_layer_filament_usage,
)


def create_mock_3mf(slice_info_content: str) -> io.BytesIO:
    """Create a mock 3MF file (ZIP) with slice_info.config content."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("Metadata/slice_info.config", slice_info_content)
    buffer.seek(0)
    return buffer


class TestParseGcodeLayerFilamentUsage:
    """Tests for parse_gcode_layer_filament_usage()."""

    def test_single_filament_single_layer(self):
        """Single filament extruding on one layer."""
        gcode = """
M620 S0
G1 X10 Y10 E5.0
G1 X20 Y20 E3.0
"""
        result = parse_gcode_layer_filament_usage(gcode)
        assert result == {0: {0: 8.0}}

    def test_multi_layer_single_filament(self):
        """Single filament across multiple layers."""
        gcode = """
M620 S0
G1 X10 Y10 E10.0
M73 L1
G1 X20 Y20 E5.0
M73 L2
G1 X30 Y30 E7.0
"""
        result = parse_gcode_layer_filament_usage(gcode)
        assert result[0] == {0: 10.0}
        assert result[1] == {0: 15.0}
        assert result[2] == {0: 22.0}

    def test_multi_material(self):
        """Multiple filaments switching via M620."""
        gcode = """
M620 S0
G1 E10.0
M73 L1
M620 S1
G1 E5.0
M620 S0
G1 E3.0
M73 L2
G1 E2.0
"""
        result = parse_gcode_layer_filament_usage(gcode)
        # Layer 0: filament 0 = 10mm
        assert result[0] == {0: 10.0}
        # Layer 1: filament 0 = 13mm (10+3), filament 1 = 5mm
        assert result[1] == {0: 13.0, 1: 5.0}
        # Layer 2: filament 0 = 15mm (13+2)
        assert result[2] == {0: 15.0, 1: 5.0}

    def test_retractions_ignored(self):
        """Negative E values (retractions) should be ignored."""
        gcode = """
M620 S0
G1 E10.0
G1 E-2.0
G1 E5.0
"""
        result = parse_gcode_layer_filament_usage(gcode)
        assert result == {0: {0: 15.0}}

    def test_m620_s255_unloads(self):
        """M620 S255 means unload - extrusion after should be ignored."""
        gcode = """
M620 S0
G1 E10.0
M620 S255
G1 E5.0
"""
        result = parse_gcode_layer_filament_usage(gcode)
        assert result == {0: {0: 10.0}}

    def test_m620_with_suffix(self):
        """M620 S0A format (filament ID with suffix letter)."""
        gcode = """
M620 S0A
G1 E10.0
M620 S1A
G1 E5.0
"""
        result = parse_gcode_layer_filament_usage(gcode)
        assert result == {0: {0: 10.0, 1: 5.0}}

    def test_comments_ignored(self):
        """Comment lines and inline comments are ignored."""
        gcode = """
; This is a comment
M620 S0
G1 X10 E5.0 ; inline comment with E value
G1 E3.0
"""
        result = parse_gcode_layer_filament_usage(gcode)
        assert result == {0: {0: 8.0}}

    def test_empty_gcode(self):
        """Empty G-code returns empty dict."""
        assert parse_gcode_layer_filament_usage("") == {}
        assert parse_gcode_layer_filament_usage("\n\n\n") == {}

    def test_no_extrusion(self):
        """G-code with moves but no extrusion."""
        gcode = """
G1 X10 Y10
G1 X20 Y20
"""
        assert parse_gcode_layer_filament_usage(gcode) == {}

    def test_no_active_filament_extrusion_ignored(self):
        """Extrusion before any M620 is ignored (no active filament)."""
        gcode = """
G1 E10.0
M620 S0
G1 E5.0
"""
        result = parse_gcode_layer_filament_usage(gcode)
        assert result == {0: {0: 5.0}}

    def test_g0_g2_g3_extrusion(self):
        """G0, G2, G3 with E parameter are also tracked."""
        gcode = """
M620 S0
G0 E1.0
G1 E2.0
G2 E3.0
G3 E4.0
"""
        result = parse_gcode_layer_filament_usage(gcode)
        assert result == {0: {0: 10.0}}

    def test_cumulative_across_layers(self):
        """Values are cumulative, not per-layer."""
        gcode = """
M620 S0
G1 E100.0
M73 L1
G1 E100.0
M73 L2
G1 E100.0
"""
        result = parse_gcode_layer_filament_usage(gcode)
        assert result[0] == {0: 100.0}
        assert result[1] == {0: 200.0}
        assert result[2] == {0: 300.0}


class TestMmToGrams:
    """Tests for mm_to_grams()."""

    def test_default_pla_175(self):
        """Default PLA 1.75mm conversion."""
        # 1000mm of 1.75mm PLA at 1.24 g/cm³
        # Volume = π × (0.0875cm)² × 100cm = 2.405cm³
        # Weight = 2.405 × 1.24 = 2.982g
        result = mm_to_grams(1000.0)
        expected = math.pi * (0.0875**2) * 100 * 1.24
        assert abs(result - expected) < 0.001

    def test_zero_length(self):
        """Zero length returns zero weight."""
        assert mm_to_grams(0.0) == 0.0

    def test_custom_diameter(self):
        """Custom diameter (2.85mm) changes result."""
        result_175 = mm_to_grams(1000.0, diameter_mm=1.75)
        result_285 = mm_to_grams(1000.0, diameter_mm=2.85)
        # 2.85mm filament has more volume per mm
        assert result_285 > result_175
        ratio = (2.85 / 1.75) ** 2  # Volume scales with diameter²
        assert abs(result_285 / result_175 - ratio) < 0.001

    def test_custom_density(self):
        """Different density (ABS vs PLA)."""
        pla = mm_to_grams(1000.0, density_g_cm3=1.24)
        abs_ = mm_to_grams(1000.0, density_g_cm3=1.04)
        assert pla > abs_
        assert abs(pla / abs_ - 1.24 / 1.04) < 0.001

    def test_known_value(self):
        """Verify against a known calculation.

        1m (1000mm) of 1.75mm PLA at 1.24 g/cm³:
        r = 0.0875 cm, L = 100 cm
        V = π × 0.0875² × 100 = 2.4053 cm³
        m = 2.4053 × 1.24 = 2.9826 g
        """
        result = mm_to_grams(1000.0, 1.75, 1.24)
        assert abs(result - 2.9826) < 0.01


class TestGetCumulativeUsageAtLayer:
    """Tests for get_cumulative_usage_at_layer()."""

    def test_exact_layer_match(self):
        """Target layer exists exactly in the data."""
        data = {0: {0: 100.0}, 5: {0: 500.0}, 10: {0: 1000.0}}
        assert get_cumulative_usage_at_layer(data, 5) == {0: 500.0}

    def test_between_layers(self):
        """Target is between recorded layers - uses the closest lower one."""
        data = {0: {0: 100.0}, 5: {0: 500.0}, 10: {0: 1000.0}}
        # Layer 7 is between 5 and 10, should return layer 5's data
        assert get_cumulative_usage_at_layer(data, 7) == {0: 500.0}

    def test_beyond_last_layer(self):
        """Target is beyond the last recorded layer."""
        data = {0: {0: 100.0}, 5: {0: 500.0}}
        assert get_cumulative_usage_at_layer(data, 100) == {0: 500.0}

    def test_before_first_layer(self):
        """Target is before any recorded data."""
        data = {5: {0: 500.0}, 10: {0: 1000.0}}
        assert get_cumulative_usage_at_layer(data, 3) == {}

    def test_empty_data(self):
        """Empty layer_usage returns empty dict."""
        assert get_cumulative_usage_at_layer({}, 5) == {}

    def test_none_data(self):
        """None layer_usage returns empty dict."""
        assert get_cumulative_usage_at_layer(None, 5) == {}

    def test_multi_filament(self):
        """Multi-filament data at target layer."""
        data = {
            0: {0: 50.0},
            5: {0: 200.0, 1: 100.0},
            10: {0: 400.0, 1: 250.0, 2: 50.0},
        }
        result = get_cumulative_usage_at_layer(data, 8)
        assert result == {0: 200.0, 1: 100.0}

    def test_layer_zero(self):
        """Target layer 0."""
        data = {0: {0: 10.0}, 1: {0: 20.0}}
        assert get_cumulative_usage_at_layer(data, 0) == {0: 10.0}


class TestExtractFilamentUsageFrom3mf:
    """Tests for extract_filament_usage_from_3mf function."""

    def test_extract_single_filament(self, tmp_path):
        """Test extracting a single filament."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <filament id="1" used_g="50.5" type="PLA" color="#FF0000"/>
        </config>
        """
        mock_3mf = create_mock_3mf(xml_content)
        file_path = tmp_path / "test.3mf"
        file_path.write_bytes(mock_3mf.read())

        result = extract_filament_usage_from_3mf(file_path)

        assert len(result) == 1
        assert result[0]["slot_id"] == 1
        assert result[0]["used_g"] == 50.5
        assert result[0]["type"] == "PLA"
        assert result[0]["color"] == "#FF0000"

    def test_extract_multiple_filaments(self, tmp_path):
        """Test extracting multiple filaments."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <filament id="1" used_g="50.5" type="PLA" color="#FF0000"/>
            <filament id="2" used_g="30.2" type="PETG" color="#00FF00"/>
            <filament id="3" used_g="10.0" type="ABS" color="#0000FF"/>
        </config>
        """
        mock_3mf = create_mock_3mf(xml_content)
        file_path = tmp_path / "test.3mf"
        file_path.write_bytes(mock_3mf.read())

        result = extract_filament_usage_from_3mf(file_path)

        assert len(result) == 3
        assert result[0]["slot_id"] == 1
        assert result[1]["slot_id"] == 2
        assert result[2]["slot_id"] == 3

    def test_extract_filament_with_plate_id(self, tmp_path):
        """Test extracting filament for a specific plate."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <plate>
                <metadata key="index" value="1"/>
                <filament id="1" used_g="25.0" type="PLA" color="#FF0000"/>
            </plate>
            <plate>
                <metadata key="index" value="2"/>
                <filament id="1" used_g="75.0" type="PETG" color="#00FF00"/>
            </plate>
        </config>
        """
        mock_3mf = create_mock_3mf(xml_content)
        file_path = tmp_path / "test.3mf"
        file_path.write_bytes(mock_3mf.read())

        result = extract_filament_usage_from_3mf(file_path, plate_id=2)

        assert len(result) == 1
        assert result[0]["used_g"] == 75.0
        assert result[0]["type"] == "PETG"

    def test_missing_slice_info_returns_empty(self, tmp_path):
        """Test that missing slice_info.config returns empty list."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("other_file.txt", "content")
        buffer.seek(0)

        file_path = tmp_path / "test.3mf"
        file_path.write_bytes(buffer.read())

        result = extract_filament_usage_from_3mf(file_path)

        assert result == []

    def test_invalid_file_returns_empty(self, tmp_path):
        """Test that invalid file returns empty list."""
        file_path = tmp_path / "invalid.3mf"
        file_path.write_text("not a zip file")

        result = extract_filament_usage_from_3mf(file_path)

        assert result == []

    def test_nonexistent_file_returns_empty(self, tmp_path):
        """Test that nonexistent file returns empty list."""
        file_path = tmp_path / "nonexistent.3mf"

        result = extract_filament_usage_from_3mf(file_path)

        assert result == []

    def test_filament_without_id_is_skipped(self, tmp_path):
        """Test that filament without id is skipped."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <filament used_g="50.5" type="PLA" color="#FF0000"/>
            <filament id="2" used_g="30.0" type="PETG" color="#00FF00"/>
        </config>
        """
        mock_3mf = create_mock_3mf(xml_content)
        file_path = tmp_path / "test.3mf"
        file_path.write_bytes(mock_3mf.read())

        result = extract_filament_usage_from_3mf(file_path)

        assert len(result) == 1
        assert result[0]["slot_id"] == 2

    def test_invalid_used_g_is_skipped(self, tmp_path):
        """Test that filament with invalid used_g is skipped."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <filament id="1" used_g="invalid" type="PLA" color="#FF0000"/>
            <filament id="2" used_g="30.0" type="PETG" color="#00FF00"/>
        </config>
        """
        mock_3mf = create_mock_3mf(xml_content)
        file_path = tmp_path / "test.3mf"
        file_path.write_bytes(mock_3mf.read())

        result = extract_filament_usage_from_3mf(file_path)

        assert len(result) == 1
        assert result[0]["slot_id"] == 2

    def test_missing_optional_fields(self, tmp_path):
        """Test that missing type and color default to empty string."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <filament id="1" used_g="50.5"/>
        </config>
        """
        mock_3mf = create_mock_3mf(xml_content)
        file_path = tmp_path / "test.3mf"
        file_path.write_bytes(mock_3mf.read())

        result = extract_filament_usage_from_3mf(file_path)

        assert len(result) == 1
        assert result[0]["type"] == ""
        assert result[0]["color"] == ""


# ---------------------------------------------------------------------------
# Tests for extract_project_filaments_from_3mf — used by the slice modal as
# fallback when the sidecar can't run a preview slice.
# ---------------------------------------------------------------------------


def _make_3mf_with(files: dict[str, bytes | str]) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content if isinstance(content, (bytes, str)) else str(content))
    buf.seek(0)
    return zipfile.ZipFile(buf, "r")


class TestExtractProjectFilamentsFrom3mf:
    """The helper backfills the slice modal when slice_info.config is empty
    (raw project files) and the sidecar is unreachable."""

    def test_returns_empty_when_project_settings_missing(self):
        with _make_3mf_with({"placeholder.txt": "hi"}) as zf:
            assert extract_project_filaments_from_3mf(zf) == []

    def test_happy_path_returns_one_entry_per_slot(self):
        proj = {
            "filament_type": ["PLA", "PETG"],
            "filament_colour": ["#000000", "#FFFFFF"],
        }
        with _make_3mf_with({"Metadata/project_settings.config": json.dumps(proj)}) as zf:
            out = extract_project_filaments_from_3mf(zf)
        assert [(f["slot_id"], f["type"], f["color"]) for f in out] == [
            (1, "PLA", "#000000"),
            (2, "PETG", "#FFFFFF"),
        ]

    def test_mismatched_array_lengths_use_max_with_blanks(self):
        proj = {
            "filament_type": ["PLA", "PETG", "ABS"],
            "filament_colour": ["#000000"],
        }
        with _make_3mf_with({"Metadata/project_settings.config": json.dumps(proj)}) as zf:
            out = extract_project_filaments_from_3mf(zf)
        assert len(out) == 3
        assert out[0]["color"] == "#000000"
        assert out[1]["color"] == ""
        assert out[2]["color"] == ""

    def test_corrupt_json_returns_empty_no_exception(self):
        with _make_3mf_with({"Metadata/project_settings.config": b"{not json"}) as zf:
            assert extract_project_filaments_from_3mf(zf) == []

    def test_root_is_list_returns_empty(self):
        # Defensive: spec says it's a dict, but a file shipping a top-level
        # list (or anything non-dict) shouldn't crash the modal.
        with _make_3mf_with({"Metadata/project_settings.config": json.dumps([])}) as zf:
            assert extract_project_filaments_from_3mf(zf) == []

    def test_empty_arrays_returns_empty(self):
        proj = {"filament_type": [], "filament_colour": []}
        with _make_3mf_with({"Metadata/project_settings.config": json.dumps(proj)}) as zf:
            assert extract_project_filaments_from_3mf(zf) == []


# ---------------------------------------------------------------------------
# Tests for extract_plate_extruder_set_from_3mf — three sources unioned:
# object top-level extruder, per-part extruder, painted-face quadtree leaves.
# ---------------------------------------------------------------------------


def _model_settings(plate_id: int, objects: list[dict]) -> str:
    """Build a minimal model_settings.config XML for tests. Each object dict
    can have: id, extruder (top-level), parts (list of {extruder}).
    The plate references all object ids."""
    parts_xml = []
    for obj in objects:
        oid = obj["id"]
        ext = obj.get("extruder")
        parts = obj.get("parts", [])
        ext_meta = f'<metadata key="extruder" value="{ext}"/>' if ext is not None else ""
        part_blocks = "".join(
            f'<part id="{i}" subtype="normal_part"><metadata key="extruder" value="{p["extruder"]}"/></part>'
            for i, p in enumerate(parts)
            if p.get("extruder") is not None
        )
        parts_xml.append(f'<object id="{oid}"><metadata key="name" value="o{oid}"/>{ext_meta}{part_blocks}</object>')
    instances = "".join(
        f'<model_instance><metadata key="object_id" value="{o["id"]}"/></model_instance>' for o in objects
    )
    plate = f'<plate><metadata key="plater_id" value="{plate_id}"/>{instances}</plate>'
    return f'<?xml version="1.0"?><config>{"".join(parts_xml)}{plate}</config>'


class TestExtractPlateExtruderSetFrom3mf:
    def test_returns_empty_set_when_model_settings_missing(self):
        with _make_3mf_with({"placeholder.txt": "hi"}) as zf:
            assert extract_plate_extruder_set_from_3mf(zf, plate_id=1) == set()

    def test_object_top_level_extruder_only(self):
        xml = _model_settings(plate_id=1, objects=[{"id": "10", "extruder": 2}])
        with _make_3mf_with({"Metadata/model_settings.config": xml}) as zf:
            assert extract_plate_extruder_set_from_3mf(zf, plate_id=1) == {2}

    def test_per_part_extruder_unions_with_top_level(self):
        # Object's default is 1; one of its parts overrides to 3 (multi-color
        # via a sub-mesh). Union both — the slicer needs profiles for both.
        xml = _model_settings(
            plate_id=1,
            objects=[{"id": "10", "extruder": 1, "parts": [{"extruder": 3}]}],
        )
        with _make_3mf_with({"Metadata/model_settings.config": xml}) as zf:
            assert extract_plate_extruder_set_from_3mf(zf, plate_id=1) == {1, 3}

    def test_unknown_plate_id_returns_empty_set(self):
        xml = _model_settings(plate_id=1, objects=[{"id": "10", "extruder": 2}])
        with _make_3mf_with({"Metadata/model_settings.config": xml}) as zf:
            assert extract_plate_extruder_set_from_3mf(zf, plate_id=99) == set()

    def test_corrupt_xml_returns_empty_set_no_exception(self):
        with _make_3mf_with({"Metadata/model_settings.config": "<not valid xml"}) as zf:
            assert extract_plate_extruder_set_from_3mf(zf, plate_id=1) == set()

    def test_zero_extruder_value_ignored(self):
        # Bambu's 0 means "use object default" — not a real slot.
        xml = _model_settings(plate_id=1, objects=[{"id": "10", "extruder": 0}])
        with _make_3mf_with({"Metadata/model_settings.config": xml}) as zf:
            assert extract_plate_extruder_set_from_3mf(zf, plate_id=1) == set()

    def test_painted_face_above_threshold_kept(self):
        # 60/40 split: 60 triangles painted with extruder 1, 40 with ext 2.
        # Threshold is 5%; both above. The dominant ones are real colours.
        triangles = []
        for _ in range(60):
            triangles.append('<triangle v1="0" v2="1" v3="2" paint_color="1"/>')
        for _ in range(40):
            triangles.append('<triangle v1="0" v2="1" v3="2" paint_color="2"/>')
        per_obj = (
            '<?xml version="1.0"?>'
            '<model><resources><object id="100" type="model"><mesh>'
            "<triangles>" + "".join(triangles) + "</triangles>"
            "</mesh></object></resources><build/></model>"
        )
        ms = (
            '<?xml version="1.0"?><config>'
            '<object id="10"><metadata key="name" value="o"/></object>'
            '<plate><metadata key="plater_id" value="1"/>'
            '<model_instance><metadata key="object_id" value="10"/></model_instance>'
            "</plate></config>"
        )
        threed = (
            '<?xml version="1.0"?>'
            '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"'
            ' xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">'
            "<resources>"
            '<object id="10" type="model"><components>'
            '<component p:path="/3D/Objects/o100.model" objectid="100"/>'
            "</components></object>"
            "</resources><build/></model>"
        )
        with _make_3mf_with(
            {
                "Metadata/model_settings.config": ms,
                "3D/3dmodel.model": threed,
                "3D/Objects/o100.model": per_obj,
            }
        ) as zf:
            result = extract_plate_extruder_set_from_3mf(zf, plate_id=1)
        # Both real colours kept (60/40 well above 5% threshold); the dropped
        # threshold case is the regression that motivates this test.
        assert result == {1, 2}

    def test_painted_face_below_threshold_dropped_as_noise(self):
        # 99 triangles at ext 1, 1 triangle at ext 9 (1% — below 5%
        # threshold). The 1% leaf is a single-leaf accident.
        triangles = []
        for _ in range(99):
            triangles.append('<triangle v1="0" v2="1" v3="2" paint_color="1"/>')
        triangles.append('<triangle v1="0" v2="1" v3="2" paint_color="9"/>')
        per_obj = (
            '<?xml version="1.0"?>'
            '<model><resources><object id="100" type="model"><mesh>'
            "<triangles>" + "".join(triangles) + "</triangles>"
            "</mesh></object></resources><build/></model>"
        )
        ms = (
            '<?xml version="1.0"?><config>'
            '<object id="10"><metadata key="name" value="o"/></object>'
            '<plate><metadata key="plater_id" value="1"/>'
            '<model_instance><metadata key="object_id" value="10"/></model_instance>'
            "</plate></config>"
        )
        threed = (
            '<?xml version="1.0"?>'
            '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"'
            ' xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">'
            '<resources><object id="10" type="model"><components>'
            '<component p:path="/3D/Objects/o100.model" objectid="100"/>'
            "</components></object></resources><build/></model>"
        )
        with _make_3mf_with(
            {
                "Metadata/model_settings.config": ms,
                "3D/3dmodel.model": threed,
                "3D/Objects/o100.model": per_obj,
            }
        ) as zf:
            result = extract_plate_extruder_set_from_3mf(zf, plate_id=1)
        # Single-leaf accident at 1% filtered as noise; only the dominant
        # extruder survives.
        assert result == {1}

    def test_missing_per_object_model_file_silently_skipped(self):
        ms = (
            '<?xml version="1.0"?><config>'
            '<object id="10"><metadata key="extruder" value="2"/></object>'
            '<plate><metadata key="plater_id" value="1"/>'
            '<model_instance><metadata key="object_id" value="10"/></model_instance>'
            "</plate></config>"
        )
        threed = (
            '<?xml version="1.0"?>'
            '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"'
            ' xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">'
            '<resources><object id="10" type="model"><components>'
            '<component p:path="/3D/Objects/missing.model" objectid="999"/>'
            "</components></object></resources><build/></model>"
        )
        with _make_3mf_with(
            {"Metadata/model_settings.config": ms, "3D/3dmodel.model": threed},
        ) as zf:
            # Top-level metadata still works; missing component model file
            # is silently skipped without crashing.
            assert extract_plate_extruder_set_from_3mf(zf, plate_id=1) == {2}
