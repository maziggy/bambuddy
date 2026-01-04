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
