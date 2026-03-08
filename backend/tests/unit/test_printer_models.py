"""Unit tests for printer model utilities."""

import pytest

from backend.app.utils.printer_models import get_rod_type


class TestGetRodType:
    """Tests for get_rod_type() rod/rail classification."""

    @pytest.mark.parametrize("model", ["X1C", "X1", "X1E", "P1P", "P1S"])
    def test_carbon_rod_models(self, model: str):
        assert get_rod_type(model) == "carbon"

    @pytest.mark.parametrize("model", ["C11", "C12", "C13"])
    def test_carbon_rod_internal_codes(self, model: str):
        assert get_rod_type(model) == "carbon"

    def test_p2s_is_steel_rod(self):
        """P2S uses hardened steel rods, not carbon rods (#640)."""
        assert get_rod_type("P2S") == "steel_rod"

    def test_p2s_internal_code_is_steel_rod(self):
        """N7 (P2S internal code) uses steel rods."""
        assert get_rod_type("N7") == "steel_rod"

    @pytest.mark.parametrize("model", ["A1", "A1 Mini", "H2D", "H2D Pro", "H2C", "H2S"])
    def test_linear_rail_models(self, model: str):
        assert get_rod_type(model) == "linear_rail"

    @pytest.mark.parametrize("model", ["N1", "N2S", "A11", "A12", "O1D", "O1E", "O2D", "O1C", "O1C2", "O1S"])
    def test_linear_rail_internal_codes(self, model: str):
        assert get_rod_type(model) == "linear_rail"

    def test_unknown_model_returns_none(self):
        assert get_rod_type("UNKNOWN") is None

    def test_none_returns_none(self):
        assert get_rod_type(None) is None

    def test_case_insensitive(self):
        assert get_rod_type("p2s") == "steel_rod"
        assert get_rod_type("x1c") == "carbon"
        assert get_rod_type("a1") == "linear_rail"

    def test_strips_whitespace_and_dashes(self):
        assert get_rod_type(" P2S ") == "steel_rod"
        assert get_rod_type("A1-Mini") == "linear_rail"
