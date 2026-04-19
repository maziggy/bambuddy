"""Unit tests for printer model utilities."""

import pytest

from backend.app.services.camera import get_camera_port, supports_rtsp
from backend.app.utils.printer_models import (
    CARBON_ROD_MODELS,
    STEEL_ROD_MODELS,
    get_rod_type,
    has_ethernet,
    normalize_printer_model,
    normalize_printer_model_id,
)


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


class TestX2DModel:
    """X2D printer support (issue #988).

    The X2D is a dual-nozzle enclosed printer launched April 2026. It shares
    the hardened steel rod hardware with P2S (NOT carbon rods) and uses
    RTSP on port 322 like other X/H series printers. Internal SSDP/MQTT
    model code is "N6"; serial numbers begin with "20P9".
    """

    def test_x2d_is_steel_rod_display_name(self):
        assert get_rod_type("X2D") == "steel_rod"

    def test_x2d_is_steel_rod_internal_code(self):
        assert get_rod_type("N6") == "steel_rod"

    def test_x2d_model_id_map(self):
        assert normalize_printer_model_id("N6") == "X2D"

    def test_x2d_model_map(self):
        assert normalize_printer_model("Bambu Lab X2D") == "X2D"

    def test_x2d_has_ethernet_display_name(self):
        assert has_ethernet("X2D") is True

    def test_x2d_has_ethernet_internal_code(self):
        assert has_ethernet("N6") is True

    def test_x2d_supports_rtsp_display_name(self):
        assert supports_rtsp("X2D") is True

    def test_x2d_supports_rtsp_internal_code(self):
        assert supports_rtsp("N6") is True

    def test_x2d_camera_port_is_rtsp(self):
        assert get_camera_port("N6") == 322
        assert get_camera_port("X2D") == 322

    def test_x2d_not_in_carbon_rod_set(self):
        """Regression guard: X2D has hardened steel rods, not carbon (#988).

        A prior PR classified X2D as carbon; the reporter confirmed it uses
        the same stainless steel rod gantry as P2S. This assertion pins the
        classification so a future change that reverts it will fail loudly.
        """
        assert "X2D" not in CARBON_ROD_MODELS
        assert "N6" not in CARBON_ROD_MODELS
        assert "X2D" in STEEL_ROD_MODELS
        assert "N6" in STEEL_ROD_MODELS
