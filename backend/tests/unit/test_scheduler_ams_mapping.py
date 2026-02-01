"""Tests for the AMS mapping computation in the print scheduler."""

import pytest

from backend.app.services.print_scheduler import PrintScheduler


class TestSchedulerAmsMappingHelpers:
    """Test the AMS mapping helper methods in PrintScheduler."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    def test_normalize_color_with_hash(self, scheduler):
        """Color with hash should return #RRGGBB format."""
        result = scheduler._normalize_color("#FF5500")
        assert result == "#FF5500"

    def test_normalize_color_without_hash(self, scheduler):
        """Color without hash should add hash prefix."""
        result = scheduler._normalize_color("FF5500")
        assert result == "#FF5500"

    def test_normalize_color_with_alpha(self, scheduler):
        """Color with alpha channel should strip it."""
        result = scheduler._normalize_color("FF5500AA")
        assert result == "#FF5500"

    def test_normalize_color_none(self, scheduler):
        """None color should return default gray."""
        result = scheduler._normalize_color(None)
        assert result == "#808080"

    def test_normalize_color_empty(self, scheduler):
        """Empty color should return default gray."""
        result = scheduler._normalize_color("")
        assert result == "#808080"

    def test_normalize_color_for_compare(self, scheduler):
        """Color for compare should be lowercase without hash."""
        result = scheduler._normalize_color_for_compare("#FF5500")
        assert result == "ff5500"

    def test_normalize_color_for_compare_with_alpha(self, scheduler):
        """Alpha channel should be stripped for comparison."""
        result = scheduler._normalize_color_for_compare("#FF5500AA")
        assert result == "ff5500"

    def test_colors_are_similar_exact_match(self, scheduler):
        """Exact same colors should be similar."""
        assert scheduler._colors_are_similar("#FF5500", "#FF5500") is True

    def test_colors_are_similar_within_threshold(self, scheduler):
        """Colors within threshold should be similar."""
        # Red difference of 10, well within default threshold of 40
        assert scheduler._colors_are_similar("#FF5500", "#F55500") is True

    def test_colors_are_similar_outside_threshold(self, scheduler):
        """Colors outside threshold should not be similar."""
        # Red: FF (255) vs 00 (0) = 255 difference
        assert scheduler._colors_are_similar("#FF0000", "#00FF00") is False

    def test_colors_are_similar_none_colors(self, scheduler):
        """None colors should not be similar."""
        assert scheduler._colors_are_similar(None, "#FF5500") is False
        assert scheduler._colors_are_similar("#FF5500", None) is False


class TestBuildLoadedFilaments:
    """Test the _build_loaded_filaments method."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    def test_build_loaded_filaments_empty_status(self, scheduler):
        """Empty status should return empty list."""

        class MockStatus:
            raw_data = {}

        result = scheduler._build_loaded_filaments(MockStatus())
        assert result == []

    def test_build_loaded_filaments_with_ams(self, scheduler):
        """Should extract filaments from AMS units."""

        class MockStatus:
            raw_data = {
                "ams": [
                    {
                        "id": 0,
                        "tray": [
                            {"id": 0, "tray_type": "PLA", "tray_color": "FF0000"},
                            {"id": 1, "tray_type": "PETG", "tray_color": "00FF00"},
                        ],
                    }
                ]
            }

        result = scheduler._build_loaded_filaments(MockStatus())
        assert len(result) == 2

        # First filament
        assert result[0]["type"] == "PLA"
        assert result[0]["color"] == "#FF0000"
        assert result[0]["ams_id"] == 0
        assert result[0]["tray_id"] == 0
        assert result[0]["global_tray_id"] == 0  # 0 * 4 + 0

        # Second filament
        assert result[1]["type"] == "PETG"
        assert result[1]["global_tray_id"] == 1  # 0 * 4 + 1

    def test_build_loaded_filaments_with_ht_ams(self, scheduler):
        """AMS-HT (single tray) should be marked as is_ht."""

        class MockStatus:
            raw_data = {
                "ams": [
                    {
                        "id": 128,
                        "tray": [{"id": 0, "tray_type": "PLA-CF", "tray_color": "000000"}],
                    }
                ]
            }

        result = scheduler._build_loaded_filaments(MockStatus())
        assert len(result) == 1
        assert result[0]["is_ht"] is True
        assert result[0]["global_tray_id"] == 512  # 128 * 4 + 0

    def test_build_loaded_filaments_with_external(self, scheduler):
        """Should include external spool."""

        class MockStatus:
            raw_data = {"vt_tray": {"tray_type": "TPU", "tray_color": "0000FF"}}

        result = scheduler._build_loaded_filaments(MockStatus())
        assert len(result) == 1
        assert result[0]["type"] == "TPU"
        assert result[0]["is_external"] is True
        assert result[0]["global_tray_id"] == 254

    def test_build_loaded_filaments_skips_empty_trays(self, scheduler):
        """Trays without tray_type should be skipped."""

        class MockStatus:
            raw_data = {
                "ams": [
                    {
                        "id": 0,
                        "tray": [
                            {"id": 0, "tray_type": "PLA", "tray_color": "FF0000"},
                            {"id": 1, "tray_type": "", "tray_color": ""},  # Empty
                            {"id": 2},  # No tray_type key
                        ],
                    }
                ]
            }

        result = scheduler._build_loaded_filaments(MockStatus())
        assert len(result) == 1
        assert result[0]["type"] == "PLA"


class TestMatchFilamentsToSlots:
    """Test the _match_filaments_to_slots method."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    def test_match_empty_required(self, scheduler):
        """Empty required list should return None."""
        result = scheduler._match_filaments_to_slots([], [])
        assert result is None

    def test_match_exact_color(self, scheduler):
        """Should prefer exact color match."""
        required = [{"slot_id": 1, "type": "PLA", "color": "#FF0000"}]
        loaded = [
            {"type": "PLA", "color": "#00FF00", "global_tray_id": 0},  # Wrong color
            {"type": "PLA", "color": "#FF0000", "global_tray_id": 1},  # Exact match
        ]

        result = scheduler._match_filaments_to_slots(required, loaded)
        assert result == [1]  # Should pick tray 1 (exact color match)

    def test_match_similar_color(self, scheduler):
        """Should match similar colors when no exact match."""
        required = [{"slot_id": 1, "type": "PLA", "color": "#FF5500"}]
        loaded = [
            {"type": "PLA", "color": "#FF5510", "global_tray_id": 0},  # Similar
        ]

        result = scheduler._match_filaments_to_slots(required, loaded)
        assert result == [0]

    def test_match_type_only(self, scheduler):
        """Should match by type when colors don't match."""
        required = [{"slot_id": 1, "type": "PLA", "color": "#FF0000"}]
        loaded = [
            {"type": "PLA", "color": "#0000FF", "global_tray_id": 5},  # Type match, color way off
        ]

        result = scheduler._match_filaments_to_slots(required, loaded)
        assert result == [5]

    def test_match_no_match_returns_minus_one(self, scheduler):
        """Unmatched filaments should have -1 in mapping."""
        required = [{"slot_id": 1, "type": "PLA", "color": "#FF0000"}]
        loaded = [
            {"type": "PETG", "color": "#FF0000", "global_tray_id": 0},  # Wrong type
        ]

        result = scheduler._match_filaments_to_slots(required, loaded)
        assert result == [-1]

    def test_match_multiple_filaments(self, scheduler):
        """Should match multiple filaments correctly."""
        required = [
            {"slot_id": 1, "type": "PLA", "color": "#FF0000"},
            {"slot_id": 2, "type": "PETG", "color": "#00FF00"},
        ]
        loaded = [
            {"type": "PLA", "color": "#FF0000", "global_tray_id": 0},
            {"type": "PETG", "color": "#00FF00", "global_tray_id": 1},
        ]

        result = scheduler._match_filaments_to_slots(required, loaded)
        assert result == [0, 1]

    def test_match_avoids_duplicate_assignment(self, scheduler):
        """Same tray should not be assigned to multiple slots."""
        required = [
            {"slot_id": 1, "type": "PLA", "color": "#FF0000"},
            {"slot_id": 2, "type": "PLA", "color": "#FF0000"},  # Same requirements
        ]
        loaded = [
            {"type": "PLA", "color": "#FF0000", "global_tray_id": 0},  # Only one PLA
        ]

        result = scheduler._match_filaments_to_slots(required, loaded)
        # First slot gets the match, second slot gets -1
        assert result == [0, -1]

    def test_match_h2d_pro_ams_ids(self, scheduler):
        """Should work with H2D Pro's high AMS IDs (128+)."""
        required = [{"slot_id": 1, "type": "PLA", "color": "#FF0000"}]
        loaded = [
            {"type": "PLA", "color": "#FF0000", "global_tray_id": 512},  # AMS 128, slot 0
        ]

        result = scheduler._match_filaments_to_slots(required, loaded)
        assert result == [512]

    def test_match_external_spool(self, scheduler):
        """Should match external spool with ID 254."""
        required = [{"slot_id": 1, "type": "TPU", "color": "#0000FF"}]
        loaded = [
            {"type": "TPU", "color": "#0000FF", "global_tray_id": 254, "is_external": True},
        ]

        result = scheduler._match_filaments_to_slots(required, loaded)
        assert result == [254]
