"""Unit tests for 3MF parsing utilities (threemf_tools.py).

Tests G-code parsing, filament length-to-weight conversion,
and cumulative layer usage lookup.
"""

import math

import pytest

from backend.app.utils.threemf_tools import (
    get_cumulative_usage_at_layer,
    mm_to_grams,
    parse_gcode_layer_filament_usage,
)


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
