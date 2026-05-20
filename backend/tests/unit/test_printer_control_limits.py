"""Unit tests for per-model printer control limits."""

import pytest

from backend.app.utils.printer_control_limits import (
    FAN_AUX,
    FAN_CHAMBER,
    FAN_PART,
    fan_percent_to_pwm,
    get_printer_control_limits,
    validate_bed_target,
    validate_fan,
    validate_nozzle_target,
)


class TestGetPrinterControlLimits:
    def test_a1_limits(self):
        limits = get_printer_control_limits("A1")
        assert limits.bed_max == 100
        assert limits.nozzle_max == 300
        assert limits.chamber_max == 0
        assert limits.fans == frozenset({FAN_PART})
        assert limits.dual_nozzle is False

    def test_x1c_has_chamber_and_aux_fans(self):
        limits = get_printer_control_limits("X1C")
        assert limits.bed_max == 120
        assert limits.chamber_max == 65
        assert FAN_AUX in limits.fans
        assert FAN_CHAMBER in limits.fans

    def test_h2d_dual_nozzle_and_high_temp(self):
        limits = get_printer_control_limits("H2D", nozzle_count=2)
        assert limits.nozzle_max == 350
        assert limits.dual_nozzle is True

    def test_unknown_model_conservative_defaults(self):
        limits = get_printer_control_limits("Unknown Printer XYZ")
        assert limits.bed_max == 100
        assert limits.nozzle_max == 300
        assert limits.fans == frozenset({FAN_PART})


class TestValidation:
    def test_bed_target_out_of_range(self):
        limits = get_printer_control_limits("A1")
        with pytest.raises(ValueError, match="Bed temperature"):
            validate_bed_target(150, limits)

    def test_nozzle_target_out_of_range(self):
        limits = get_printer_control_limits("X1C")
        with pytest.raises(ValueError, match="Nozzle temperature"):
            validate_nozzle_target(400, limits)

    def test_fan_not_available_on_a1(self):
        limits = get_printer_control_limits("A1")
        with pytest.raises(ValueError, match="not available"):
            validate_fan(FAN_CHAMBER, 50, limits)

    def test_fan_speed_percent_bounds(self):
        limits = get_printer_control_limits("X1C")
        with pytest.raises(ValueError, match="0 and 100"):
            validate_fan(FAN_PART, 150, limits)


class TestFanPwmConversion:
    def test_fan_percent_to_pwm(self):
        assert fan_percent_to_pwm(0) == 0
        assert fan_percent_to_pwm(100) == 255
        assert fan_percent_to_pwm(50) == 128
