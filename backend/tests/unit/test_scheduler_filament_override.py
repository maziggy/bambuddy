"""Tests for the filament override feature in the print scheduler."""

from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.print_scheduler import PrintScheduler


class TestCountOverrideColorMatches:
    """Test the _count_override_color_matches method."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_no_status_returns_zero(self, mock_pm, scheduler):
        """When printer_manager.get_status() returns None, should return 0."""
        mock_pm.get_status.return_value = None

        result = scheduler._count_override_color_matches(1, [{"type": "PLA", "color": "#FF0000"}])
        assert result == 0

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_exact_match(self, mock_pm, scheduler):
        """Override with matching type+color on printer returns 1."""
        mock_pm.get_status.return_value = MagicMock(
            raw_data={
                "ams": [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA", "tray_color": "FF0000FF"}]}],
            }
        )

        result = scheduler._count_override_color_matches(1, [{"type": "PLA", "color": "#FF0000"}])
        assert result == 1

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_no_match(self, mock_pm, scheduler):
        """Override with type+color not on printer returns 0."""
        mock_pm.get_status.return_value = MagicMock(
            raw_data={
                "ams": [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA", "tray_color": "FF0000FF"}]}],
            }
        )

        result = scheduler._count_override_color_matches(1, [{"type": "PETG", "color": "#00FF00"}])
        assert result == 0

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_multiple_overrides_partial_match(self, mock_pm, scheduler):
        """2 overrides, only 1 matching = returns 1."""
        mock_pm.get_status.return_value = MagicMock(
            raw_data={
                "ams": [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA", "tray_color": "FF0000FF"}]}],
            }
        )

        overrides = [
            {"type": "PLA", "color": "#FF0000"},  # Matches
            {"type": "PETG", "color": "#00FF00"},  # Does not match
        ]
        result = scheduler._count_override_color_matches(1, overrides)
        assert result == 1

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_color_normalization(self, mock_pm, scheduler):
        """Override color '#FF0000' matches printer tray_color 'FF0000FF' (with alpha)."""
        mock_pm.get_status.return_value = MagicMock(
            raw_data={
                "ams": [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA", "tray_color": "FF0000FF"}]}],
            }
        )

        # Override uses #-prefixed color; printer uses 8-char RGBA without hash
        result = scheduler._count_override_color_matches(1, [{"type": "PLA", "color": "#FF0000"}])
        assert result == 1

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_external_spool_match(self, mock_pm, scheduler):
        """Override matches filament in vt_tray."""
        mock_pm.get_status.return_value = MagicMock(
            raw_data={
                "ams": [],
                "vt_tray": [{"tray_type": "TPU", "tray_color": "0000FFFF"}],
            }
        )

        result = scheduler._count_override_color_matches(1, [{"type": "TPU", "color": "#0000FF"}])
        assert result == 1


class TestFilamentOverrideInMatching:
    """Test that when overrides are applied to filament requirements, the matching uses overridden values."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    def _apply_overrides(self, filament_reqs, overrides):
        """Simulate override application as done in _compute_ams_mapping_for_printer."""
        override_map = {o["slot_id"]: o for o in overrides}
        for req in filament_reqs:
            if req["slot_id"] in override_map:
                override = override_map[req["slot_id"]]
                req["type"] = override["type"]
                req["color"] = override["color"]
                req["tray_info_idx"] = ""  # Clear for override
        return filament_reqs

    def test_override_changes_color_match(self, scheduler):
        """Original req has color A, loaded has color B. Override to color B gives exact match."""
        filament_reqs = [{"slot_id": 1, "type": "PLA", "color": "#000000", "tray_info_idx": ""}]
        loaded = [
            {"type": "PLA", "color": "#FF0000", "global_tray_id": 0},
        ]

        # Without override: type-only match (colors differ)
        result_without = scheduler._match_filaments_to_slots(filament_reqs, loaded)
        assert result_without == [0]  # Matches by type only

        # Now apply override changing color to match loaded
        overrides = [{"slot_id": 1, "type": "PLA", "color": "#FF0000"}]
        filament_reqs_overridden = [{"slot_id": 1, "type": "PLA", "color": "#000000", "tray_info_idx": ""}]
        self._apply_overrides(filament_reqs_overridden, overrides)

        result_with = scheduler._match_filaments_to_slots(filament_reqs_overridden, loaded)
        assert result_with == [0]  # Exact color match now
        # Verify the override actually changed the color in the requirement
        assert filament_reqs_overridden[0]["color"] == "#FF0000"

    def test_override_clears_tray_info_idx(self, scheduler):
        """When tray_info_idx is cleared, matching falls to color-based instead of tray_info_idx-based."""
        loaded = [
            {"type": "PLA", "color": "#FF0000", "global_tray_id": 0, "tray_info_idx": "GFA00"},
            {"type": "PLA", "color": "#00FF00", "global_tray_id": 1, "tray_info_idx": "GFB00"},
        ]

        # Without override: tray_info_idx "GFA00" matches tray 0 (red)
        filament_reqs_original = [{"slot_id": 1, "type": "PLA", "color": "#FF0000", "tray_info_idx": "GFA00"}]
        result_original = scheduler._match_filaments_to_slots(filament_reqs_original, loaded)
        assert result_original == [0]  # Matched by tray_info_idx

        # With override: tray_info_idx is cleared, color changed to green -> matches tray 1
        filament_reqs_overridden = [{"slot_id": 1, "type": "PLA", "color": "#FF0000", "tray_info_idx": "GFA00"}]
        overrides = [{"slot_id": 1, "type": "PLA", "color": "#00FF00"}]
        self._apply_overrides(filament_reqs_overridden, overrides)

        assert filament_reqs_overridden[0]["tray_info_idx"] == ""  # Cleared
        result_overridden = scheduler._match_filaments_to_slots(filament_reqs_overridden, loaded)
        assert result_overridden == [1]  # Now matches tray 1 by color

    def test_override_type_change(self, scheduler):
        """Override changes type from PLA to PETG, loaded has PETG -> matches."""
        loaded = [
            {"type": "PETG", "color": "#FF0000", "global_tray_id": 0},
        ]

        # Without override: PLA requirement, PETG loaded -> no match
        filament_reqs_original = [{"slot_id": 1, "type": "PLA", "color": "#FF0000", "tray_info_idx": ""}]
        result_original = scheduler._match_filaments_to_slots(filament_reqs_original, loaded)
        assert result_original == [-1]  # Type mismatch

        # With override: type changed to PETG -> matches
        filament_reqs_overridden = [{"slot_id": 1, "type": "PLA", "color": "#FF0000", "tray_info_idx": ""}]
        overrides = [{"slot_id": 1, "type": "PETG", "color": "#FF0000"}]
        self._apply_overrides(filament_reqs_overridden, overrides)

        result_overridden = scheduler._match_filaments_to_slots(filament_reqs_overridden, loaded)
        assert result_overridden == [0]  # Exact match now

    def test_partial_override(self, scheduler):
        """2 slots, only slot 1 overridden. Slot 1 uses override, slot 2 uses original."""
        loaded = [
            {"type": "PLA", "color": "#FF0000", "global_tray_id": 0},
            {"type": "PETG", "color": "#00FF00", "global_tray_id": 1},
        ]

        filament_reqs = [
            {"slot_id": 1, "type": "PLA", "color": "#000000", "tray_info_idx": "GFA00"},
            {"slot_id": 2, "type": "PETG", "color": "#00FF00", "tray_info_idx": "GFG02"},
        ]

        # Override only slot 1: change color to red
        overrides = [{"slot_id": 1, "type": "PLA", "color": "#FF0000"}]
        self._apply_overrides(filament_reqs, overrides)

        # Slot 1: overridden to PLA/#FF0000, tray_info_idx cleared -> matches tray 0 by exact color
        assert filament_reqs[0]["color"] == "#FF0000"
        assert filament_reqs[0]["tray_info_idx"] == ""

        # Slot 2: NOT overridden, retains original tray_info_idx
        assert filament_reqs[1]["color"] == "#00FF00"
        assert filament_reqs[1]["tray_info_idx"] == "GFG02"

        result = scheduler._match_filaments_to_slots(filament_reqs, loaded)
        assert result == [0, 1]  # Slot 1 -> tray 0 (red PLA), slot 2 -> tray 1 (green PETG)

    def test_nozzle_filtering_with_override(self, scheduler):
        """Override to a type only available on the wrong nozzle returns -1."""
        loaded = [
            # PETG on RIGHT nozzle (extruder 0) only
            {"type": "PETG", "color": "#FF0000", "global_tray_id": 0, "extruder_id": 0},
            # PLA on LEFT nozzle (extruder 1) only
            {"type": "PLA", "color": "#00FF00", "global_tray_id": 4, "extruder_id": 1},
        ]

        # Override to PETG on LEFT nozzle — but PETG is only on RIGHT
        filament_reqs = [{"slot_id": 1, "type": "PLA", "color": "#000000", "tray_info_idx": "GFA00", "nozzle_id": 1}]
        overrides = [{"slot_id": 1, "type": "PETG", "color": "#FF0000"}]
        self._apply_overrides(filament_reqs, overrides)

        result = scheduler._match_filaments_to_slots(filament_reqs, loaded)
        # Nozzle filter limits to extruder 1 (LEFT) which only has PLA.
        # Override changed type to PETG, so no type match on LEFT nozzle -> -1
        assert result == [-1]
