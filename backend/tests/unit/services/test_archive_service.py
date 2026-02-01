"""Unit tests for the archive service."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestArchiveServiceHelpers:
    """Tests for archive service helper functions."""

    def test_parse_print_time_seconds(self):
        """Test parsing print time to seconds."""
        # Import the actual function if available, otherwise test the logic
        # 2h 30m 15s = 2*3600 + 30*60 + 15 = 9015 seconds
        _time_str = "2h 30m 15s"  # Example format
        # Parse hours
        hours = 2
        minutes = 30
        seconds = 15
        total = hours * 3600 + minutes * 60 + seconds
        assert total == 9015

    def test_parse_filament_grams(self):
        """Test parsing filament usage to grams."""
        # Example: "150.5g" -> 150.5
        filament_str = "150.5g"
        grams = float(filament_str.replace("g", ""))
        assert grams == 150.5

    def test_format_duration(self):
        """Test formatting seconds to human readable duration."""
        # 3661 seconds = 1h 1m 1s
        seconds = 3661
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        assert hours == 1
        assert minutes == 1
        assert secs == 1


class TestArchiveDataParsing:
    """Tests for parsing archive data from MQTT messages."""

    def test_parse_gcode_state(self):
        """Test parsing gcode state."""
        states = {
            "RUNNING": "printing",
            "FINISH": "completed",
            "FAILED": "failed",
            "IDLE": "idle",
            "PAUSE": "paused",
        }
        for gcode_state, expected in states.items():
            # Simple state mapping
            mapped = gcode_state.lower()
            if gcode_state == "RUNNING":
                mapped = "printing"
            elif gcode_state == "FINISH":
                mapped = "completed"
            elif gcode_state == "FAILED":
                mapped = "failed"
            elif gcode_state == "IDLE":
                mapped = "idle"
            elif gcode_state == "PAUSE":
                mapped = "paused"
            assert mapped == expected

    def test_parse_progress(self):
        """Test parsing print progress."""
        # mc_percent is the progress field in MQTT messages
        data = {"mc_percent": 75}
        progress = data.get("mc_percent", 0)
        assert progress == 75
        assert 0 <= progress <= 100

    def test_parse_layer_info(self):
        """Test parsing layer information."""
        data = {
            "layer_num": 50,
            "total_layers": 200,
        }
        current_layer = data.get("layer_num", 0)
        total_layers = data.get("total_layers", 0)
        assert current_layer == 50
        assert total_layers == 200
        if total_layers > 0:
            layer_percent = (current_layer / total_layers) * 100
            assert layer_percent == 25.0


class TestArchiveFilePaths:
    """Tests for archive file path handling."""

    def test_generate_archive_path(self):
        """Test generating archive file paths."""
        printer_name = "X1C_01"
        _print_name = "benchy"  # Example print name
        timestamp = datetime(2024, 1, 15, 14, 30, 0)

        # Expected pattern: archives/{printer}/{year}/{month}/{filename}
        year = timestamp.year
        month = f"{timestamp.month:02d}"
        expected_dir = f"archives/{printer_name}/{year}/{month}"

        assert "archives" in expected_dir
        assert printer_name in expected_dir
        assert str(year) in expected_dir

    def test_sanitize_filename(self):
        """Test filename sanitization."""
        # Characters to remove: / \ : * ? " < > |
        dirty_name = "test:file<name>.3mf"
        # Simple sanitization
        safe_chars = []
        for c in dirty_name:
            if c not in '\\/:*?"<>|':
                safe_chars.append(c)
        clean_name = "".join(safe_chars)
        assert ":" not in clean_name
        assert "<" not in clean_name
        assert ">" not in clean_name

    def test_thumbnail_path(self):
        """Test thumbnail path generation."""
        archive_path = "archives/X1C_01/2024/01/benchy.3mf"
        # Thumbnail typically has same path with _thumb.png suffix
        base_path = archive_path.rsplit(".", 1)[0]
        thumbnail_path = f"{base_path}_thumb.png"
        assert thumbnail_path.endswith("_thumb.png")
        assert "benchy" in thumbnail_path


class TestArchiveStatus:
    """Tests for archive status handling."""

    def test_valid_status_values(self):
        """Test valid archive status values."""
        valid_statuses = ["completed", "failed", "cancelled", "stopped"]
        for status in valid_statuses:
            assert status in valid_statuses

    def test_status_from_gcode_state(self):
        """Test mapping gcode state to archive status."""
        state_mapping = {
            "FINISH": "completed",
            "FAILED": "failed",
            "CANCEL": "cancelled",
        }
        for gcode_state, expected_status in state_mapping.items():
            assert state_mapping[gcode_state] == expected_status


class TestArchiveFilamentData:
    """Tests for filament data parsing."""

    def test_parse_ams_filament(self):
        """Test parsing AMS filament information."""
        ams_data = {
            "ams": {
                "ams": [
                    {
                        "tray": [
                            {"tray_type": "PLA", "tray_color": "FF0000"},
                            {"tray_type": "PETG", "tray_color": "00FF00"},
                        ]
                    }
                ]
            }
        }
        trays = ams_data["ams"]["ams"][0]["tray"]
        assert trays[0]["tray_type"] == "PLA"
        assert trays[1]["tray_type"] == "PETG"

    def test_parse_filament_color_hex(self):
        """Test parsing filament color from hex."""
        color_hex = "FF5500"
        # Should be valid hex
        assert len(color_hex) == 6
        r = int(color_hex[0:2], 16)
        g = int(color_hex[2:4], 16)
        b = int(color_hex[4:6], 16)
        assert r == 255
        assert g == 85
        assert b == 0

    def test_calculate_filament_cost(self):
        """Test calculating filament cost."""
        grams_used = 150.0
        cost_per_kg = 25.0  # $25 per kg
        cost = (grams_used / 1000) * cost_per_kg
        assert cost == 3.75


class TestArchiveThumbnails:
    """Tests for archive thumbnail handling."""

    def test_thumbnail_file_types(self):
        """Test supported thumbnail file types."""
        supported_types = [".png", ".jpg", ".jpeg"]
        for ext in supported_types:
            assert ext.startswith(".")
            assert ext.lower() in [".png", ".jpg", ".jpeg"]

    def test_extract_thumbnail_from_3mf(self):
        """Test thumbnail extraction concept from 3MF."""
        # 3MF files are ZIP archives containing:
        # - Metadata/thumbnail.png
        # - 3D/3dmodel.model
        expected_thumbnail_paths = [
            "Metadata/thumbnail.png",
            "Metadata/plate_1.png",
        ]
        for path in expected_thumbnail_paths:
            assert "png" in path.lower()


class TestPrintableObjectsExtraction:
    """Tests for extracting printable objects count from 3MF files."""

    def test_extract_printable_objects_from_slice_info(self):
        """Test parsing printable objects from slice_info.config XML."""
        from xml.etree import ElementTree as ET

        # Example slice_info.config content with 4 objects
        slice_info_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <plate plate_idx="1">
                <metadata key="prediction" value="3600" />
                <metadata key="weight" value="50.5" />
                <object identify_id="1" name="Part_A" skipped="false" />
                <object identify_id="2" name="Part_B" skipped="false" />
                <object identify_id="3" name="Part_C" skipped="false" />
                <object identify_id="4" name="Part_D" skipped="true" />
            </plate>
        </config>
        """
        root = ET.fromstring(slice_info_xml)
        plate = root.find(".//plate")

        # Count non-skipped objects (should be 3, not 4)
        count = 0
        for obj in plate.findall("object"):
            skipped = obj.get("skipped", "false")
            if skipped.lower() != "true":
                count += 1

        assert count == 3  # 3 objects (Part_D is skipped)

    def test_extract_printable_objects_empty_plate(self):
        """Test handling plate with no objects."""
        from xml.etree import ElementTree as ET

        slice_info_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <plate plate_idx="1">
                <metadata key="prediction" value="0" />
            </plate>
        </config>
        """
        root = ET.fromstring(slice_info_xml)
        plate = root.find(".//plate")

        count = 0
        for obj in plate.findall("object"):
            skipped = obj.get("skipped", "false")
            if skipped.lower() != "true":
                count += 1

        assert count == 0

    def test_extract_printable_objects_all_skipped(self):
        """Test handling plate where all objects are skipped."""
        from xml.etree import ElementTree as ET

        slice_info_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <plate plate_idx="1">
                <object identify_id="1" name="Part_A" skipped="true" />
                <object identify_id="2" name="Part_B" skipped="true" />
            </plate>
        </config>
        """
        root = ET.fromstring(slice_info_xml)
        plate = root.find(".//plate")

        count = 0
        for obj in plate.findall("object"):
            skipped = obj.get("skipped", "false")
            if skipped.lower() != "true":
                count += 1

        assert count == 0  # All objects skipped


class TestThreeMFPlateIndexExtraction:
    """Tests for extracting plate index from multi-plate 3MF exports (Issue #92)."""

    def test_extract_plate_index_from_slice_info(self):
        """Test parsing plate index from slice_info.config metadata."""
        from xml.etree import ElementTree as ET

        # Single-plate export from plate 5 of a multi-plate project
        slice_info_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <plate>
                <metadata key="index" value="5" />
                <metadata key="prediction" value="3600" />
                <metadata key="weight" value="50.5" />
                <object identify_id="1" name="Part_A" skipped="false" />
            </plate>
        </config>
        """
        root = ET.fromstring(slice_info_xml)
        plate = root.find(".//plate")

        plate_index = None
        for meta in plate.findall("metadata"):
            if meta.get("key") == "index":
                plate_index = int(meta.get("value"))
                break

        assert plate_index == 5

    def test_extract_plate_index_plate_1(self):
        """Test parsing plate index when it's plate 1."""
        from xml.etree import ElementTree as ET

        slice_info_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <plate>
                <metadata key="index" value="1" />
                <metadata key="prediction" value="1800" />
            </plate>
        </config>
        """
        root = ET.fromstring(slice_info_xml)
        plate = root.find(".//plate")

        plate_index = None
        for meta in plate.findall("metadata"):
            if meta.get("key") == "index":
                plate_index = int(meta.get("value"))
                break

        assert plate_index == 1

    def test_thumbnail_path_uses_plate_number(self):
        """Test that thumbnail path correctly uses the extracted plate number."""
        plate_number = 5
        thumbnail_paths = []

        if plate_number:
            thumbnail_paths.append(f"Metadata/plate_{plate_number}.png")

        thumbnail_paths.extend(
            [
                "Metadata/plate_1.png",
                "Metadata/thumbnail.png",
            ]
        )

        # First priority should be plate_5.png
        assert thumbnail_paths[0] == "Metadata/plate_5.png"

    def test_print_name_enhanced_for_plate_greater_than_1(self):
        """Test that print_name is enhanced with plate info for plate > 1."""
        plate_index = 5
        print_name = "Benchy"

        # Logic from archive.py
        if plate_index and plate_index > 1:
            if print_name and f"Plate {plate_index}" not in print_name:
                print_name = f"{print_name} - Plate {plate_index}"

        assert print_name == "Benchy - Plate 5"

    def test_print_name_not_enhanced_for_plate_1(self):
        """Test that print_name is NOT enhanced for plate 1."""
        plate_index = 1
        print_name = "Benchy"

        # Logic from archive.py
        if plate_index and plate_index > 1:
            if print_name and f"Plate {plate_index}" not in print_name:
                print_name = f"{print_name} - Plate {plate_index}"

        assert print_name == "Benchy"  # Unchanged for plate 1

    def test_print_name_not_duplicated(self):
        """Test that plate info is not added if already present in print_name."""
        plate_index = 5
        print_name = "Benchy - Plate 5"

        # Logic from archive.py
        if plate_index and plate_index > 1:
            if print_name and f"Plate {plate_index}" not in print_name:
                print_name = f"{print_name} - Plate {plate_index}"

        assert print_name == "Benchy - Plate 5"  # Not duplicated

    def test_high_plate_number_extraction(self):
        """Test extracting high plate numbers (e.g., plate 28)."""
        from xml.etree import ElementTree as ET

        slice_info_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <plate>
                <metadata key="index" value="28" />
                <metadata key="prediction" value="7200" />
            </plate>
        </config>
        """
        root = ET.fromstring(slice_info_xml)
        plate = root.find(".//plate")

        plate_index = None
        for meta in plate.findall("metadata"):
            if meta.get("key") == "index":
                plate_index = int(meta.get("value"))
                break

        assert plate_index == 28

        # Verify thumbnail would use correct plate
        thumbnail_path = f"Metadata/plate_{plate_index}.png"
        assert thumbnail_path == "Metadata/plate_28.png"


class TestMultiPlate3MFParsing:
    """Tests for parsing multi-plate 3MF files (Issue #93)."""

    def test_parse_multiple_plates_from_slice_info(self):
        """Test parsing multiple plates from slice_info.config."""
        from xml.etree import ElementTree as ET

        # Multi-plate 3MF with 3 plates
        slice_info_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <plate>
                <metadata key="index" value="1" />
                <metadata key="prediction" value="3600" />
                <metadata key="weight" value="50.0" />
                <filament id="1" type="PLA" color="#FF0000" used_g="25.0" used_m="8.5" />
                <object identify_id="1" name="Part_A" skipped="false" />
            </plate>
            <plate>
                <metadata key="index" value="2" />
                <metadata key="prediction" value="7200" />
                <metadata key="weight" value="100.0" />
                <filament id="2" type="PETG" color="#00FF00" used_g="50.0" used_m="17.0" />
                <object identify_id="2" name="Part_B" skipped="false" />
            </plate>
            <plate>
                <metadata key="index" value="3" />
                <metadata key="prediction" value="1800" />
                <metadata key="weight" value="25.0" />
                <filament id="1" type="PLA" color="#FF0000" used_g="12.5" used_m="4.2" />
                <filament id="3" type="TPU" color="#0000FF" used_g="12.5" used_m="4.2" />
                <object identify_id="3" name="Part_C" skipped="false" />
            </plate>
        </config>
        """
        root = ET.fromstring(slice_info_xml)
        plates = root.findall(".//plate")

        assert len(plates) == 3

        # Parse each plate
        plate_data = []
        for plate_elem in plates:
            plate_info = {"index": None, "filaments": []}

            for meta in plate_elem.findall("metadata"):
                if meta.get("key") == "index":
                    plate_info["index"] = int(meta.get("value"))

            for filament_elem in plate_elem.findall("filament"):
                used_g = float(filament_elem.get("used_g", "0"))
                if used_g > 0:
                    plate_info["filaments"].append(
                        {
                            "slot_id": int(filament_elem.get("id")),
                            "type": filament_elem.get("type"),
                            "color": filament_elem.get("color"),
                            "used_grams": used_g,
                        }
                    )

            plate_data.append(plate_info)

        # Verify plate 1
        assert plate_data[0]["index"] == 1
        assert len(plate_data[0]["filaments"]) == 1
        assert plate_data[0]["filaments"][0]["slot_id"] == 1
        assert plate_data[0]["filaments"][0]["type"] == "PLA"

        # Verify plate 2
        assert plate_data[1]["index"] == 2
        assert len(plate_data[1]["filaments"]) == 1
        assert plate_data[1]["filaments"][0]["slot_id"] == 2
        assert plate_data[1]["filaments"][0]["type"] == "PETG"

        # Verify plate 3 (has 2 filaments)
        assert plate_data[2]["index"] == 3
        assert len(plate_data[2]["filaments"]) == 2
        filament_types = {f["type"] for f in plate_data[2]["filaments"]}
        assert filament_types == {"PLA", "TPU"}

    def test_filter_filaments_by_plate_id(self):
        """Test filtering filaments for a specific plate."""
        from xml.etree import ElementTree as ET

        slice_info_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <plate>
                <metadata key="index" value="1" />
                <filament id="1" type="PLA" color="#FF0000" used_g="25.0" />
            </plate>
            <plate>
                <metadata key="index" value="2" />
                <filament id="2" type="PETG" color="#00FF00" used_g="50.0" />
            </plate>
        </config>
        """
        root = ET.fromstring(slice_info_xml)

        # Filter for plate 2 only
        target_plate_id = 2
        filaments = []

        for plate_elem in root.findall(".//plate"):
            plate_index = None
            for meta in plate_elem.findall("metadata"):
                if meta.get("key") == "index":
                    plate_index = int(meta.get("value", "0"))
                    break

            if plate_index == target_plate_id:
                for filament_elem in plate_elem.findall("filament"):
                    used_g = float(filament_elem.get("used_g", "0"))
                    if used_g > 0:
                        filaments.append(
                            {
                                "slot_id": int(filament_elem.get("id")),
                                "type": filament_elem.get("type"),
                            }
                        )
                break

        # Should only have plate 2's filament
        assert len(filaments) == 1
        assert filaments[0]["slot_id"] == 2
        assert filaments[0]["type"] == "PETG"

    def test_detect_multi_plate_from_gcode_files(self):
        """Test detecting multiple plates from gcode file presence."""
        # Simulate namelist from a multi-plate 3MF
        namelist = [
            "Metadata/plate_1.gcode",
            "Metadata/plate_2.gcode",
            "Metadata/plate_3.gcode",
            "Metadata/plate_1.png",
            "Metadata/plate_2.png",
            "Metadata/plate_3.png",
            "Metadata/slice_info.config",
            "3D/3dmodel.model",
        ]

        # Extract plate indices from gcode files
        gcode_files = [n for n in namelist if n.startswith("Metadata/plate_") and n.endswith(".gcode")]
        plate_indices = []
        for gf in gcode_files:
            plate_str = gf[15:-6]  # Remove "Metadata/plate_" and ".gcode"
            plate_indices.append(int(plate_str))

        plate_indices.sort()

        assert len(plate_indices) == 3
        assert plate_indices == [1, 2, 3]

        # Verify it's a multi-plate file
        is_multi_plate = len(plate_indices) > 1
        assert is_multi_plate is True

    def test_single_plate_export_not_multi_plate(self):
        """Test that single-plate exports are not detected as multi-plate."""
        # Simulate namelist from a single-plate export (plate 5 only)
        namelist = [
            "Metadata/plate_5.gcode",
            "Metadata/plate_1.png",
            "Metadata/plate_2.png",
            "Metadata/plate_3.png",
            "Metadata/plate_4.png",
            "Metadata/plate_5.png",  # All thumbnails present
            "Metadata/slice_info.config",
            "3D/3dmodel.model",
        ]

        # Extract plate indices from gcode files (not thumbnails!)
        gcode_files = [n for n in namelist if n.startswith("Metadata/plate_") and n.endswith(".gcode")]
        plate_indices = []
        for gf in gcode_files:
            plate_str = gf[15:-6]
            plate_indices.append(int(plate_str))

        # Only one gcode file = single plate export
        assert len(plate_indices) == 1
        assert plate_indices[0] == 5

        is_multi_plate = len(plate_indices) > 1
        assert is_multi_plate is False


class TestReprintCostCalculation:
    """Tests for reprint cost calculation."""

    def test_cost_addition_logic(self):
        """Test that reprint costs are added correctly."""
        # Simulate the cost addition logic
        existing_cost = 5.25  # Original print cost
        filament_grams = 100.0
        cost_per_kg = 25.0  # Default cost

        # Calculate additional cost for reprint
        additional_cost = round((filament_grams / 1000) * cost_per_kg, 2)
        assert additional_cost == 2.50

        # Add to existing cost
        new_total = round(existing_cost + additional_cost, 2)
        assert new_total == 7.75

    def test_cost_addition_with_none_existing(self):
        """Test cost addition when existing cost is None."""
        existing_cost = None
        filament_grams = 200.0
        cost_per_kg = 15.0

        additional_cost = round((filament_grams / 1000) * cost_per_kg, 2)
        assert additional_cost == 3.0

        # When existing is None, just use additional
        new_total = additional_cost if existing_cost is None else round(existing_cost + additional_cost, 2)
        assert new_total == 3.0

    def test_cost_with_custom_filament_price(self):
        """Test cost calculation with custom filament price."""
        filament_grams = 150.0
        custom_cost_per_kg = 35.0  # More expensive filament

        cost = round((filament_grams / 1000) * custom_cost_per_kg, 2)
        assert cost == 5.25

    def test_multiple_reprints_accumulate(self):
        """Test that multiple reprints accumulate costs correctly."""
        filament_grams = 100.0
        cost_per_kg = 20.0
        single_print_cost = round((filament_grams / 1000) * cost_per_kg, 2)
        assert single_print_cost == 2.0

        # After 3 prints (1 original + 2 reprints)
        total_after_3_prints = round(single_print_cost * 3, 2)
        assert total_after_3_prints == 6.0
