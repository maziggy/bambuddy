"""Unit tests for _safe_int, _safe_float, and _map_spoolman_spool helpers."""

import math

import pytest

from backend.app.api.routes._spoolman_helpers import (
    _map_spoolman_spool,
    _safe_float,
    _safe_int,
)

# ---------------------------------------------------------------------------
# _safe_int
# ---------------------------------------------------------------------------


class TestSafeInt:
    def test_normal_int(self):
        assert _safe_int(1000, 0) == 1000

    def test_float_rounds_down(self):
        assert _safe_int(750.9, 0) == 750

    def test_none_returns_fallback(self):
        assert _safe_int(None, 999) == 999

    def test_nan_returns_fallback(self):
        assert _safe_int(math.nan, 999) == 999

    def test_inf_returns_fallback(self):
        assert _safe_int(math.inf, 999) == 999

    def test_neg_inf_returns_fallback(self):
        assert _safe_int(-math.inf, 999) == 999

    def test_string_numeric(self):
        assert _safe_int("500", 0) == 500

    def test_string_non_numeric_returns_fallback(self):
        assert _safe_int("abc", 42) == 42

    def test_zero(self):
        assert _safe_int(0, 999) == 0


# ---------------------------------------------------------------------------
# _safe_float
# ---------------------------------------------------------------------------


class TestSafeFloat:
    def test_normal_float(self):
        assert _safe_float(123.45, 0.0) == pytest.approx(123.45)

    def test_none_returns_fallback(self):
        assert _safe_float(None, -1.0) == -1.0

    def test_nan_returns_fallback(self):
        assert _safe_float(math.nan, -1.0) == -1.0

    def test_inf_returns_fallback(self):
        assert _safe_float(math.inf, -1.0) == -1.0

    def test_neg_inf_returns_fallback(self):
        assert _safe_float(-math.inf, -1.0) == -1.0

    def test_string_numeric(self):
        assert _safe_float("3.14", 0.0) == pytest.approx(3.14)

    def test_string_non_numeric_returns_fallback(self):
        assert _safe_float("bad", 0.0) == 0.0

    def test_zero(self):
        assert _safe_float(0.0, 99.0) == 0.0


# ---------------------------------------------------------------------------
# _map_spoolman_spool
# ---------------------------------------------------------------------------


MINIMAL_SPOOL = {
    "id": 1,
    "filament": {
        "material": "PLA",
        "name": "PLA Basic",
        "color_hex": "FF0000",
        "weight": 1000.0,
        "vendor": {"name": "Bambu Lab"},
    },
    "used_weight": 250.0,
    "archived": False,
    "registered": "2024-01-01T00:00:00Z",
}


class TestMapSpoolmanSpool:
    def test_basic_mapping(self):
        result = _map_spoolman_spool(MINIMAL_SPOOL)
        assert result["id"] == 1
        assert result["material"] == "PLA"
        assert result["rgba"] == "FF0000FF"
        assert result["label_weight"] == 1000
        assert result["weight_used"] == pytest.approx(250.0)
        assert result["data_origin"] == "spoolman"

    def test_missing_id_raises(self):
        spool = {k: v for k, v in MINIMAL_SPOOL.items() if k != "id"}
        with pytest.raises(ValueError, match="missing required 'id'"):
            _map_spoolman_spool(spool)

    def test_none_id_raises(self):
        with pytest.raises(ValueError):
            _map_spoolman_spool({**MINIMAL_SPOOL, "id": None})

    def test_string_id_raises(self):
        with pytest.raises(ValueError, match="not a valid integer"):
            _map_spoolman_spool({**MINIMAL_SPOOL, "id": "abc"})

    def test_numeric_string_id_accepted(self):
        result = _map_spoolman_spool({**MINIMAL_SPOOL, "id": "42"})
        assert result["id"] == 42

    def test_zero_price_not_converted_to_none(self):
        spool = {**MINIMAL_SPOOL, "price": 0.0}
        result = _map_spoolman_spool(spool)
        assert result["cost_per_kg"] == 0.0

    def test_nonzero_price_preserved(self):
        spool = {**MINIMAL_SPOOL, "price": 9.99}
        result = _map_spoolman_spool(spool)
        assert result["cost_per_kg"] == pytest.approx(9.99)

    def test_none_price_stays_none(self):
        spool = {**MINIMAL_SPOOL, "price": None}
        result = _map_spoolman_spool(spool)
        assert result["cost_per_kg"] is None

    def test_infinity_weight_falls_back(self):
        spool = {**MINIMAL_SPOOL, "filament": {**MINIMAL_SPOOL["filament"], "weight": math.inf}}
        result = _map_spoolman_spool(spool)
        assert result["label_weight"] == 1000

    def test_nan_used_weight_falls_back(self):
        spool = {**MINIMAL_SPOOL, "used_weight": math.nan}
        result = _map_spoolman_spool(spool)
        assert result["weight_used"] == 0.0

    def test_invalid_color_hex_falls_back_to_grey(self):
        spool = {**MINIMAL_SPOOL, "filament": {**MINIMAL_SPOOL["filament"], "color_hex": "ZZZZZZ"}}
        result = _map_spoolman_spool(spool)
        assert result["rgba"] == "808080FF"

    def test_short_color_hex_falls_back(self):
        spool = {**MINIMAL_SPOOL, "filament": {**MINIMAL_SPOOL["filament"], "color_hex": "FFF"}}
        result = _map_spoolman_spool(spool)
        assert result["rgba"] == "808080FF"

    def test_eight_char_color_hex_falls_back(self):
        # Only 6-char hex is valid from Spoolman; 8-char (RGBA) should fall back
        spool = {**MINIMAL_SPOOL, "filament": {**MINIMAL_SPOOL["filament"], "color_hex": "FF0000FF"}}
        result = _map_spoolman_spool(spool)
        assert result["rgba"] == "808080FF"

    def test_color_hex_with_hash_prefix_stripped(self):
        spool = {**MINIMAL_SPOOL, "filament": {**MINIMAL_SPOOL["filament"], "color_hex": "#00FF00"}}
        result = _map_spoolman_spool(spool)
        assert result["rgba"] == "00FF00FF"

    def test_color_hex_lowercase_normalised(self):
        spool = {**MINIMAL_SPOOL, "filament": {**MINIMAL_SPOOL["filament"], "color_hex": "ff0000"}}
        result = _map_spoolman_spool(spool)
        assert result["rgba"] == "FF0000FF"

    def test_none_filament(self):
        spool = {**MINIMAL_SPOOL, "filament": None}
        result = _map_spoolman_spool(spool)
        assert result["material"] == ""
        assert result["rgba"] == "808080FF"
        assert result["label_weight"] == 1000

    def test_archived_spool_has_archived_at(self):
        spool = {**MINIMAL_SPOOL, "archived": True}
        result = _map_spoolman_spool(spool)
        assert result["archived_at"] is not None

    def test_subtype_strips_material_prefix(self):
        spool = {**MINIMAL_SPOOL, "filament": {**MINIMAL_SPOOL["filament"], "material": "PLA", "name": "PLA Basic"}}
        result = _map_spoolman_spool(spool)
        assert result["subtype"] == "Basic"

    def test_brand_from_vendor(self):
        result = _map_spoolman_spool(MINIMAL_SPOOL)
        assert result["brand"] == "Bambu Lab"

    def test_no_vendor_brand_is_none(self):
        spool = {**MINIMAL_SPOOL, "filament": {**MINIMAL_SPOOL["filament"], "vendor": None}}
        result = _map_spoolman_spool(spool)
        assert result["brand"] is None

    def test_spoolman_location_mapped_to_storage_location(self):
        spool = {**MINIMAL_SPOOL, "location": "Shelf A"}
        result = _map_spoolman_spool(spool)
        assert result["storage_location"] == "Shelf A"

    def test_no_location_gives_none_storage_location(self):
        result = _map_spoolman_spool(MINIMAL_SPOOL)
        assert result["storage_location"] is None

    def test_empty_location_gives_none_storage_location(self):
        spool = {**MINIMAL_SPOOL, "location": ""}
        result = _map_spoolman_spool(spool)
        assert result["storage_location"] is None

    def test_spoolman_location_key_not_in_result(self):
        spool = {**MINIMAL_SPOOL, "location": "Shelf A"}
        result = _map_spoolman_spool(spool)
        assert "spoolman_location" not in result
