"""Tests for the AMS mapping computation in the print scheduler."""

import io
import json
import zipfile

import pytest

from backend.app.services.print_scheduler import PrintScheduler
from backend.app.utils.threemf_tools import extract_nozzle_mapping_from_3mf


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
            raw_data = {"vt_tray": [{"tray_type": "TPU", "tray_color": "0000FF"}]}

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
            raw_data = {"vt_tray": [{"tray_type": "TPU", "tray_color": "0000FF", "tray_info_idx": "P4d64437"}]}

        result = scheduler._build_loaded_filaments(MockStatus())
        assert len(result) == 1
        assert result[0]["tray_info_idx"] == "P4d64437"
        assert result[0]["is_external"] is True


def _make_3mf_zip(project_settings: dict | None = None) -> zipfile.ZipFile:
    """Create an in-memory ZipFile mimicking a 3MF with project_settings.config."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if project_settings is not None:
            zf.writestr("Metadata/project_settings.config", json.dumps(project_settings))
    buf.seek(0)
    return zipfile.ZipFile(buf, "r")


class TestExtractNozzleMappingFrom3mf:
    """Test the extract_nozzle_mapping_from_3mf utility."""

    def test_dual_nozzle_mapping(self):
        """Should return slot->extruder mapping for dual-nozzle files."""
        zf = _make_3mf_zip(
            {
                "filament_nozzle_map": ["0", "1", "0"],
                "physical_extruder_map": ["0", "1"],
            }
        )
        result = extract_nozzle_mapping_from_3mf(zf)
        assert result == {1: 0, 2: 1, 3: 0}
        zf.close()

    def test_single_nozzle_returns_none(self):
        """All slots on same extruder should return None (single-nozzle)."""
        zf = _make_3mf_zip(
            {
                "filament_nozzle_map": ["0", "0", "0"],
                "physical_extruder_map": ["0"],
            }
        )
        result = extract_nozzle_mapping_from_3mf(zf)
        assert result is None
        zf.close()

    def test_missing_project_settings_returns_none(self):
        """Missing project_settings.config should return None."""
        zf = _make_3mf_zip(None)
        result = extract_nozzle_mapping_from_3mf(zf)
        assert result is None
        zf.close()

    def test_missing_fields_returns_none(self):
        """Missing filament_nozzle_map or physical_extruder_map should return None."""
        zf = _make_3mf_zip({"some_other_key": "value"})
        result = extract_nozzle_mapping_from_3mf(zf)
        assert result is None
        zf.close()

    def test_physical_extruder_map_remapping(self):
        """Should apply physical_extruder_map to remap slicer extruder to MQTT extruder."""
        # Slicer ext 0 -> MQTT ext 1, slicer ext 1 -> MQTT ext 0
        zf = _make_3mf_zip(
            {
                "filament_nozzle_map": ["0", "1"],
                "physical_extruder_map": ["1", "0"],
            }
        )
        result = extract_nozzle_mapping_from_3mf(zf)
        assert result == {1: 1, 2: 0}
        zf.close()


class TestNozzleAwareMapping:
    """Test nozzle-aware filament matching in the print scheduler."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    def test_dual_nozzle_matching(self, scheduler):
        """Filaments assigned to different nozzles should match to correct AMS units."""
        required = [
            {"slot_id": 1, "type": "PLA", "color": "#FF0000", "nozzle_id": 0},  # Right nozzle
            {"slot_id": 2, "type": "PLA", "color": "#00FF00", "nozzle_id": 1},  # Left nozzle
        ]
        loaded = [
            {"type": "PLA", "color": "#00FF00", "global_tray_id": 0, "extruder_id": 0},  # AMS0 on right
            {"type": "PLA", "color": "#FF0000", "global_tray_id": 4, "extruder_id": 1},  # AMS1 on left
        ]
        # Without nozzle filtering, slot 1 (red, right) would match tray 4 (red, left) by color.
        # With nozzle filtering, slot 1 (right nozzle) can only use tray 0 (right extruder),
        # and slot 2 (left nozzle) can only use tray 4 (left extruder).
        result = scheduler._match_filaments_to_slots(required, loaded)
        assert result == [0, 4]

    def test_nozzle_fallback_when_no_match(self, scheduler):
        """Should fall back to unfiltered list when nozzle-filtered list is empty."""
        required = [
            {"slot_id": 1, "type": "PLA", "color": "#FF0000", "nozzle_id": 0},  # Right nozzle
        ]
        loaded = [
            # Only a tray on the left nozzle, none on right
            {"type": "PLA", "color": "#FF0000", "global_tray_id": 4, "extruder_id": 1},
        ]
        # No trays on extruder 0, so fallback to unfiltered -> should still match
        result = scheduler._match_filaments_to_slots(required, loaded)
        assert result == [4]

    def test_no_nozzle_id_skips_filtering(self, scheduler):
        """When nozzle_id is None, no nozzle filtering should be applied."""
        required = [
            {"slot_id": 1, "type": "PLA", "color": "#FF0000"},  # No nozzle_id
        ]
        loaded = [
            {"type": "PLA", "color": "#FF0000", "global_tray_id": 0, "extruder_id": 0},
            {"type": "PLA", "color": "#FF0000", "global_tray_id": 4, "extruder_id": 1},
        ]
        # Should match first available (tray 0) regardless of extruder
        result = scheduler._match_filaments_to_slots(required, loaded)
        assert result == [0]

    def test_extruder_id_in_loaded_filaments(self, scheduler):
        """_build_loaded_filaments should include extruder_id from ams_extruder_map."""

        class MockStatus:
            raw_data = {
                "ams": [
                    {"id": 0, "tray": [{"id": 0, "tray_type": "PLA", "tray_color": "FF0000"}]},
                    {"id": 1, "tray": [{"id": 0, "tray_type": "PLA", "tray_color": "00FF00"}]},
                ],
                "ams_extruder_map": {"0": 0, "1": 1},
            }

        result = scheduler._build_loaded_filaments(MockStatus())
        assert len(result) == 2
        assert result[0]["extruder_id"] == 0
        assert result[1]["extruder_id"] == 1

    def test_extruder_id_none_without_map(self, scheduler):
        """extruder_id should be None when ams_extruder_map is absent."""

        class MockStatus:
            raw_data = {
                "ams": [
                    {"id": 0, "tray": [{"id": 0, "tray_type": "PLA", "tray_color": "FF0000"}]},
                ]
            }

        result = scheduler._build_loaded_filaments(MockStatus())
        assert len(result) == 1
        assert result[0]["extruder_id"] is None

    def test_external_spool_extruder_id(self, scheduler):
        """External spool should have extruder_id=0 when ams_extruder_map exists."""

        class MockStatus:
            raw_data = {
                "vt_tray": [{"tray_type": "TPU", "tray_color": "0000FF"}],
                "ams_extruder_map": {"0": 0},
            }

        result = scheduler._build_loaded_filaments(MockStatus())
        assert len(result) == 1
        assert result[0]["extruder_id"] == 0
        assert result[0]["is_external"] is True

    def test_external_spool_no_extruder_map(self, scheduler):
        """External spool extruder_id should be None without ams_extruder_map."""

        class MockStatus:
            raw_data = {"vt_tray": [{"tray_type": "TPU", "tray_color": "0000FF"}]}

        result = scheduler._build_loaded_filaments(MockStatus())
        assert len(result) == 1
        assert result[0]["extruder_id"] is None

    def test_dual_nozzle_with_tray_info_idx(self, scheduler):
        """Nozzle filtering should work together with tray_info_idx matching."""
        required = [
            {"slot_id": 1, "type": "PLA", "color": "#000000", "tray_info_idx": "GFA00", "nozzle_id": 0},
            {"slot_id": 2, "type": "PLA", "color": "#000000", "tray_info_idx": "GFA01", "nozzle_id": 1},
        ]
        loaded = [
            {"type": "PLA", "color": "#000000", "global_tray_id": 0, "tray_info_idx": "GFA00", "extruder_id": 0},
            {"type": "PLA", "color": "#000000", "global_tray_id": 4, "tray_info_idx": "GFA01", "extruder_id": 1},
        ]
        result = scheduler._match_filaments_to_slots(required, loaded)
        assert result == [0, 4]
