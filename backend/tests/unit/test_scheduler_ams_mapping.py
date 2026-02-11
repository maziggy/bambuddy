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
        assert result[0]["global_tray_id"] == 128  # AMS-HT uses ams_id directly

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

    def test_build_loaded_filaments_handles_string_ids(self, scheduler):
        """IDs should be converted to integers even if they come as strings."""

        class MockStatus:
            raw_data = {
                "ams": [
                    {
                        "id": "0",  # String ID
                        "tray": [
                            {"id": "0", "tray_type": "PLA", "tray_color": "FF0000"},  # String tray ID
                            {"id": "1", "tray_type": "PETG", "tray_color": "00FF00"},
                        ],
                    },
                    {
                        "id": "128",  # String AMS-HT ID
                        "tray": [{"id": "0", "tray_type": "PLA-CF", "tray_color": "000000"}],
                    },
                ]
            }

        result = scheduler._build_loaded_filaments(MockStatus())
        assert len(result) == 3

        # Regular AMS trays with string IDs should be converted
        assert result[0]["ams_id"] == 0
        assert result[0]["tray_id"] == 0
        assert result[0]["global_tray_id"] == 0  # 0 * 4 + 0

        assert result[1]["ams_id"] == 0
        assert result[1]["tray_id"] == 1
        assert result[1]["global_tray_id"] == 1  # 0 * 4 + 1

        # AMS-HT with string ID should be converted and handle >= 128 check correctly
        assert result[2]["ams_id"] == 128
        assert result[2]["tray_id"] == 0
        assert result[2]["global_tray_id"] == 128  # Should use ams_id directly since >= 128


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

    def test_match_by_tray_info_idx_priority(self, scheduler):
        """tray_info_idx match should have highest priority over color match."""
        required = [{"slot_id": 1, "type": "PLA", "color": "#000000", "tray_info_idx": "GFA00"}]
        loaded = [
            {
                "type": "PLA",
                "color": "#000000",
                "global_tray_id": 0,
                "tray_info_idx": "GFB00",
            },  # Same color, different spool
            {
                "type": "PLA",
                "color": "#000000",
                "global_tray_id": 1,
                "tray_info_idx": "GFA00",
            },  # Same color, exact spool
            {
                "type": "PLA",
                "color": "#000000",
                "global_tray_id": 2,
                "tray_info_idx": "GFC00",
            },  # Same color, different spool
        ]

        result = scheduler._match_filaments_to_slots(required, loaded)
        assert result == [1]  # Should pick tray 1 (exact tray_info_idx match)

    def test_match_by_tray_info_idx_with_different_colors(self, scheduler):
        """tray_info_idx match should work even if colors differ slightly."""
        required = [{"slot_id": 1, "type": "PLA", "color": "#000000", "tray_info_idx": "P4d64437"}]
        loaded = [
            {"type": "PLA", "color": "#000000", "global_tray_id": 0, "tray_info_idx": ""},  # No idx
            {
                "type": "PLA",
                "color": "#000010",
                "global_tray_id": 3,
                "tray_info_idx": "P4d64437",
            },  # Exact spool (slightly different color reported)
        ]

        result = scheduler._match_filaments_to_slots(required, loaded)
        assert result == [3]  # Should pick tray 3 (exact tray_info_idx match)

    def test_match_fallback_to_color_when_no_tray_info_idx(self, scheduler):
        """Should fall back to color matching when tray_info_idx is empty."""
        required = [{"slot_id": 1, "type": "PLA", "color": "#FF0000", "tray_info_idx": ""}]
        loaded = [
            {"type": "PLA", "color": "#00FF00", "global_tray_id": 0, "tray_info_idx": "GFA00"},
            {"type": "PLA", "color": "#FF0000", "global_tray_id": 1, "tray_info_idx": "GFB00"},  # Color match
        ]

        result = scheduler._match_filaments_to_slots(required, loaded)
        assert result == [1]  # Should pick tray 1 (color match)

    def test_match_fallback_to_color_when_no_matching_tray_info_idx(self, scheduler):
        """Should fall back to color when tray_info_idx doesn't match any loaded spool."""
        required = [{"slot_id": 1, "type": "PLA", "color": "#FF0000", "tray_info_idx": "OLD_SPOOL"}]
        loaded = [
            {
                "type": "PLA",
                "color": "#FF0000",
                "global_tray_id": 0,
                "tray_info_idx": "NEW_SPOOL",
            },  # Different idx but same color
        ]

        result = scheduler._match_filaments_to_slots(required, loaded)
        assert result == [0]  # Should fall back to color match

    def test_match_multiple_same_color_with_tray_info_idx(self, scheduler):
        """Multiple identical filaments should be matched by tray_info_idx (H2D Pro scenario)."""
        # This is the exact scenario from issue #245 - 3 black PLA spools
        required = [
            {"slot_id": 1, "type": "PLA", "color": "#000000", "tray_info_idx": "GFA03"},  # Wants tray 3
        ]
        loaded = [
            {"type": "PLA", "color": "#000000", "global_tray_id": 0, "tray_info_idx": "GFA00"},  # Tray 0
            {"type": "PLA", "color": "#000000", "global_tray_id": 1, "tray_info_idx": "GFA01"},  # Tray 1
            {"type": "PLA", "color": "#000000", "global_tray_id": 2, "tray_info_idx": "GFA02"},  # Tray 2
            {
                "type": "PLA",
                "color": "#000000",
                "global_tray_id": 3,
                "tray_info_idx": "GFA03",
            },  # Tray 3 - the one we want
        ]

        result = scheduler._match_filaments_to_slots(required, loaded)
        assert result == [3]  # Should pick tray 3, not tray 0

    def test_match_tray_info_idx_not_reused(self, scheduler):
        """tray_info_idx matched trays should not be reused for other slots."""
        required = [
            {"slot_id": 1, "type": "PLA", "color": "#000000", "tray_info_idx": "GFA00"},
            {"slot_id": 2, "type": "PLA", "color": "#000000", "tray_info_idx": "GFA01"},
        ]
        loaded = [
            {"type": "PLA", "color": "#000000", "global_tray_id": 0, "tray_info_idx": "GFA00"},
            {"type": "PLA", "color": "#000000", "global_tray_id": 1, "tray_info_idx": "GFA01"},
        ]

        result = scheduler._match_filaments_to_slots(required, loaded)
        assert result == [0, 1]  # Each slot gets its specific tray

    def test_match_non_unique_tray_info_idx_uses_color(self, scheduler):
        """Non-unique tray_info_idx should fall back to color matching.

        This is the scenario where multiple trays have the same tray_info_idx
        (e.g., two spools of generic PLA both have GFA00). The color should
        be used as tiebreaker instead of just picking the first match.
        """
        # User sliced with green PLA (tray_info_idx=GFA00)
        # Two trays have GFA00: tray 3 (white) and tray 4 (green)
        # Should pick tray 4 because the color matches
        required = [
            {"slot_id": 2, "type": "PLA", "color": "#00FF00", "tray_info_idx": "GFA00"},  # Green PLA
        ]
        loaded = [
            {"type": "PLA", "color": "#FFFFFF", "global_tray_id": 3, "tray_info_idx": "GFA00"},  # White PLA
            {"type": "PLA", "color": "#00FF00", "global_tray_id": 4, "tray_info_idx": "GFA00"},  # Green PLA
        ]

        result = scheduler._match_filaments_to_slots(required, loaded)
        assert result == [-1, 4]  # Should pick tray 4 (color match), not tray 3 (first match)

    def test_match_non_unique_tray_info_idx_same_color(self, scheduler):
        """Non-unique tray_info_idx with identical colors picks first match.

        When multiple trays have the same tray_info_idx AND same color,
        there's no way to differentiate, so first match is used.
        """
        required = [
            {"slot_id": 2, "type": "PLA", "color": "#FFFFFF", "tray_info_idx": "GFA00"},
        ]
        loaded = [
            {"type": "PLA", "color": "#FFFFFF", "global_tray_id": 3, "tray_info_idx": "GFA00"},
            {"type": "PLA", "color": "#FFFFFF", "global_tray_id": 4, "tray_info_idx": "GFA00"},
        ]

        result = scheduler._match_filaments_to_slots(required, loaded)
        # Both have same color, so first is used
        assert result == [-1, 3]


class TestBuildLoadedFilamentsTrayInfoIdx:
    """Test tray_info_idx extraction in _build_loaded_filaments."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    def test_build_loaded_filaments_includes_tray_info_idx(self, scheduler):
        """Should extract tray_info_idx from AMS trays."""

        class MockStatus:
            raw_data = {
                "ams": [
                    {
                        "id": 0,
                        "tray": [
                            {"id": 0, "tray_type": "PLA", "tray_color": "000000", "tray_info_idx": "GFA00"},
                            {"id": 1, "tray_type": "PLA", "tray_color": "000000", "tray_info_idx": "GFA01"},
                        ],
                    }
                ]
            }

        result = scheduler._build_loaded_filaments(MockStatus())
        assert len(result) == 2
        assert result[0]["tray_info_idx"] == "GFA00"
        assert result[1]["tray_info_idx"] == "GFA01"

    def test_build_loaded_filaments_empty_tray_info_idx(self, scheduler):
        """Missing tray_info_idx should default to empty string."""

        class MockStatus:
            raw_data = {
                "ams": [
                    {
                        "id": 0,
                        "tray": [
                            {"id": 0, "tray_type": "PLA", "tray_color": "FF0000"},  # No tray_info_idx
                        ],
                    }
                ]
            }

        result = scheduler._build_loaded_filaments(MockStatus())
        assert len(result) == 1
        assert result[0]["tray_info_idx"] == ""

    def test_build_loaded_filaments_external_spool_tray_info_idx(self, scheduler):
        """Should extract tray_info_idx from external spool."""

        class MockStatus:
            raw_data = {"vt_tray": {"tray_type": "TPU", "tray_color": "0000FF", "tray_info_idx": "P4d64437"}}

        result = scheduler._build_loaded_filaments(MockStatus())
        assert len(result) == 1
        assert result[0]["tray_info_idx"] == "P4d64437"
        assert result[0]["is_external"] is True
