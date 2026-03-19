"""Unit tests for maintenance rod-type filtering logic."""

import pytest

from backend.app.api.routes.maintenance import _should_apply_to_printer


class TestShouldApplyToPrinter:
    """Tests for _should_apply_to_printer() model-specific filtering."""

    # Carbon rod tasks should only apply to X1/P1 models
    @pytest.mark.parametrize("model", ["X1C", "X1", "X1E", "P1P", "P1S"])
    def test_carbon_rod_tasks_apply_to_carbon_models(self, model: str):
        assert _should_apply_to_printer("Clean Carbon Rods", model) is True

    def test_carbon_rod_tasks_do_not_apply_to_p2s(self):
        """P2S has steel rods, not carbon rods (#640)."""
        assert _should_apply_to_printer("Clean Carbon Rods", "P2S") is False

    def test_carbon_rod_tasks_do_not_apply_to_a1(self):
        assert _should_apply_to_printer("Clean Carbon Rods", "A1") is False

    # Steel rod tasks should only apply to P2S
    def test_steel_rod_tasks_apply_to_p2s(self):
        assert _should_apply_to_printer("Lubricate Steel Rods", "P2S") is True
        assert _should_apply_to_printer("Clean Steel Rods", "P2S") is True

    def test_steel_rod_tasks_do_not_apply_to_x1c(self):
        assert _should_apply_to_printer("Lubricate Steel Rods", "X1C") is False
        assert _should_apply_to_printer("Clean Steel Rods", "X1C") is False

    def test_steel_rod_tasks_do_not_apply_to_a1(self):
        assert _should_apply_to_printer("Lubricate Steel Rods", "A1") is False

    # Linear rail tasks should only apply to A1/H2 models
    @pytest.mark.parametrize("model", ["A1", "A1 Mini", "H2D", "H2C", "H2S"])
    def test_linear_rail_tasks_apply_to_rail_models(self, model: str):
        assert _should_apply_to_printer("Lubricate Linear Rails", model) is True
        assert _should_apply_to_printer("Clean Linear Rails", model) is True

    def test_linear_rail_tasks_do_not_apply_to_p2s(self):
        assert _should_apply_to_printer("Lubricate Linear Rails", "P2S") is False

    # Universal tasks apply to all models
    @pytest.mark.parametrize("model", ["X1C", "P2S", "A1", "H2D"])
    def test_universal_tasks_apply_to_all(self, model: str):
        assert _should_apply_to_printer("Clean Nozzle/Hotend", model) is True
        assert _should_apply_to_printer("Check Belt Tension", model) is True

    # Unknown models default to carbon (legacy behavior)
    def test_unknown_model_defaults_to_carbon(self):
        assert _should_apply_to_printer("Clean Carbon Rods", "UNKNOWN") is True
        assert _should_apply_to_printer("Lubricate Steel Rods", "UNKNOWN") is False
        assert _should_apply_to_printer("Lubricate Linear Rails", "UNKNOWN") is False
