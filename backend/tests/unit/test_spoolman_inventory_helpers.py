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

    def test_zero_id_raises(self):
        with pytest.raises(ValueError, match="positive integer"):
            _map_spoolman_spool({**MINIMAL_SPOOL, "id": 0})

    def test_negative_id_raises(self):
        with pytest.raises(ValueError, match="positive integer"):
            _map_spoolman_spool({**MINIMAL_SPOOL, "id": -5})

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

    def test_color_name_uses_explicit_field_when_present(self):
        """When Spoolman's filament has color_name set, that wins over the subtype fallback."""
        spool = {
            **MINIMAL_SPOOL,
            "filament": {**MINIMAL_SPOOL["filament"], "color_name": "Sunrise Orange"},
        }
        result = _map_spoolman_spool(spool)
        assert result["color_name"] == "Sunrise Orange"

    def test_color_name_falls_back_to_subtype_when_field_missing(self):
        """Spoolman doesn't standardise color_name; the LinkSpoolModal would
        otherwise show 'Unknown color' for every Spoolman spool. The mapper
        falls back to the filament's name minus material prefix (which the
        subtype field already carries) so the user can tell spools apart at a
        glance even on installs that don't fill color_name.
        """
        spool = {
            **MINIMAL_SPOOL,
            "filament": {
                **MINIMAL_SPOOL["filament"],
                "name": "PLA Basic Red",
                # No color_name field — the common case for default Spoolman installs.
            },
        }
        result = _map_spoolman_spool(spool)
        # subtype is filament_name minus material prefix → "Basic Red"
        assert result["subtype"] == "Basic Red"
        # color_name falls back to subtype.
        assert result["color_name"] == "Basic Red"

    def test_color_name_none_when_both_fields_empty(self):
        """If neither color_name nor a usable subtype exists, return None — UI
        falls back to its own 'Unknown color' string rather than showing a
        misleading material-only label.
        """
        spool = {
            **MINIMAL_SPOOL,
            "filament": {
                **MINIMAL_SPOOL["filament"],
                "name": "PLA",  # name == material → subtype becomes None
            },
        }
        result = _map_spoolman_spool(spool)
        assert result["subtype"] is None
        assert result["color_name"] is None

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

    def test_core_weight_from_filament_spool_weight(self):
        spool = {**MINIMAL_SPOOL, "filament": {**MINIMAL_SPOOL["filament"], "spool_weight": 196}}
        result = _map_spoolman_spool(spool)
        assert result["core_weight"] == 196

    def test_core_weight_fallback_when_spool_weight_missing(self):
        result = _map_spoolman_spool(MINIMAL_SPOOL)
        assert result["core_weight"] == 250

    def test_core_weight_fallback_when_spool_weight_none(self):
        spool = {**MINIMAL_SPOOL, "filament": {**MINIMAL_SPOOL["filament"], "spool_weight": None}}
        result = _map_spoolman_spool(spool)
        assert result["core_weight"] == 250

    def test_core_weight_float_truncated_to_int(self):
        spool = {**MINIMAL_SPOOL, "filament": {**MINIMAL_SPOOL["filament"], "spool_weight": 180.9}}
        result = _map_spoolman_spool(spool)
        assert result["core_weight"] == 180

    def test_spool_level_spool_weight_takes_priority_over_filament(self):
        spool = {**MINIMAL_SPOOL, "spool_weight": 300, "filament": {**MINIMAL_SPOOL["filament"], "spool_weight": 196}}
        assert _map_spoolman_spool(spool)["core_weight"] == 300

    def test_spool_level_zero_spool_weight_not_treated_as_missing(self):
        spool = {**MINIMAL_SPOOL, "spool_weight": 0, "filament": {**MINIMAL_SPOOL["filament"], "spool_weight": 196}}
        assert _map_spoolman_spool(spool)["core_weight"] == 0

    def test_spool_level_none_falls_back_to_filament(self):
        spool = {**MINIMAL_SPOOL, "spool_weight": None, "filament": {**MINIMAL_SPOOL["filament"], "spool_weight": 196}}
        assert _map_spoolman_spool(spool)["core_weight"] == 196

    def test_spool_level_absent_falls_back_to_filament(self):
        spool = {**MINIMAL_SPOOL, "filament": {**MINIMAL_SPOOL["filament"], "spool_weight": 196}}
        assert _map_spoolman_spool(spool)["core_weight"] == 196

    def test_both_levels_none_uses_fallback(self):
        spool = {**MINIMAL_SPOOL, "spool_weight": None, "filament": {**MINIMAL_SPOOL["filament"], "spool_weight": None}}
        assert _map_spoolman_spool(spool)["core_weight"] == 250


# ---------------------------------------------------------------------------
# F4: _safe_optional_float unit tests
# ---------------------------------------------------------------------------


class TestSafeOptionalFloat:
    """F4: Direct unit tests for _safe_optional_float (NaN/Inf safety)."""

    def test_normal_value(self):
        import pytest

        from backend.app.api.routes._spoolman_helpers import _safe_optional_float

        assert _safe_optional_float(9.99) == pytest.approx(9.99)

    def test_none_returns_none(self):
        from backend.app.api.routes._spoolman_helpers import _safe_optional_float

        assert _safe_optional_float(None) is None

    def test_nan_returns_none(self):
        import math

        from backend.app.api.routes._spoolman_helpers import _safe_optional_float

        assert _safe_optional_float(math.nan) is None

    def test_inf_returns_none(self):
        import math

        from backend.app.api.routes._spoolman_helpers import _safe_optional_float

        assert _safe_optional_float(math.inf) is None

    def test_neg_inf_returns_none(self):
        import math

        from backend.app.api.routes._spoolman_helpers import _safe_optional_float

        assert _safe_optional_float(-math.inf) is None

    def test_zero_returns_zero(self):
        from backend.app.api.routes._spoolman_helpers import _safe_optional_float

        assert _safe_optional_float(0.0) == 0.0

    def test_string_numeric(self):
        import pytest

        from backend.app.api.routes._spoolman_helpers import _safe_optional_float

        assert _safe_optional_float("3.14") == pytest.approx(3.14)

    def test_string_non_numeric_returns_none(self):
        from backend.app.api.routes._spoolman_helpers import _safe_optional_float

        assert _safe_optional_float("bad") is None


class TestMapSpoolmanSpoolSlicerFilament:
    """slicer_filament round-trip via Spoolman extra dict.

    Spoolman has no native slicer_filament field, so we persist BambuStudio
    presets under bambu_slicer_filament[_name] keys in the spool's extra
    dict (JSON-encoded strings, like every Spoolman extra value). The map
    function unwraps those values and exposes them as slicer_filament /
    slicer_filament_name on the InventorySpool shape. Without this round-trip
    the user's selected slicer preset is silently dropped on save (#1114).
    """

    def test_slicer_filament_unwrapped_from_extra(self):
        spool = {
            **MINIMAL_SPOOL,
            "extra": {
                "bambu_slicer_filament": '"PFUSf543b298f8ea66"',
                "bambu_slicer_filament_name": '"Devil Design PLA Basic @Bambu Lab H2D 0.4 nozzle (Custom)"',
            },
        }
        result = _map_spoolman_spool(spool)
        assert result["slicer_filament"] == "PFUSf543b298f8ea66"
        assert result["slicer_filament_name"] == "Devil Design PLA Basic @Bambu Lab H2D 0.4 nozzle (Custom)"

    def test_slicer_filament_falls_back_to_filament_name(self):
        # Spool has no bambu_slicer_filament_name override → use Spoolman's filament.name
        spool = {**MINIMAL_SPOOL, "extra": {}}
        result = _map_spoolman_spool(spool)
        assert result["slicer_filament"] is None
        assert result["slicer_filament_name"] == "PLA Basic"  # from filament.name

    def test_empty_string_extra_treated_as_unset(self):
        # JSON-encoded empty string is how the user clears the field
        spool = {
            **MINIMAL_SPOOL,
            "extra": {
                "bambu_slicer_filament": '""',
                "bambu_slicer_filament_name": '""',
            },
        }
        result = _map_spoolman_spool(spool)
        assert result["slicer_filament"] is None
        # Falls back to filament.name when the override is cleared
        assert result["slicer_filament_name"] == "PLA Basic"

    def test_non_json_extra_value_passed_through(self):
        # Tolerate bare-string values written without JSON encoding
        # (older data, manual writes via Spoolman UI, etc.)
        spool = {
            **MINIMAL_SPOOL,
            "extra": {"bambu_slicer_filament": "GFL05"},
        }
        result = _map_spoolman_spool(spool)
        assert result["slicer_filament"] == "GFL05"


class TestExtractExtraStr:
    """JSON-encoded extra-string unwrapper used by _map_spoolman_spool."""

    def test_unwraps_quoted_string(self):
        from backend.app.api.routes._spoolman_helpers import _extract_extra_str

        assert _extract_extra_str({"k": '"hello"'}, "k") == "hello"

    def test_returns_empty_for_missing_key(self):
        from backend.app.api.routes._spoolman_helpers import _extract_extra_str

        assert _extract_extra_str({}, "k") == ""

    def test_returns_empty_for_non_string_value(self):
        from backend.app.api.routes._spoolman_helpers import _extract_extra_str

        # Spoolman extra values are stringified; numeric values shouldn't sneak in
        # but if they do we treat them as unset rather than crashing
        assert _extract_extra_str({"k": 42}, "k") == ""

    def test_returns_empty_for_json_null(self):
        from backend.app.api.routes._spoolman_helpers import _extract_extra_str

        # null isn't a string after decode → treat as unset
        assert _extract_extra_str({"k": "null"}, "k") == ""

    def test_passes_through_bare_string_on_decode_error(self):
        from backend.app.api.routes._spoolman_helpers import _extract_extra_str

        # Tolerate non-JSON-encoded values
        assert _extract_extra_str({"k": "GFL05"}, "k") == "GFL05"


class TestMapSpoolmanSpoolPrice:
    """F4: NaN/Inf price in _map_spoolman_spool gives None cost_per_kg."""

    def test_nan_price_gives_none_cost_per_kg(self):
        import math

        from backend.app.api.routes._spoolman_helpers import _map_spoolman_spool

        spool = {**MINIMAL_SPOOL, "price": math.nan}
        assert _map_spoolman_spool(spool)["cost_per_kg"] is None

    def test_inf_price_gives_none_cost_per_kg(self):
        import math

        from backend.app.api.routes._spoolman_helpers import _map_spoolman_spool

        spool = {**MINIMAL_SPOOL, "price": math.inf}
        assert _map_spoolman_spool(spool)["cost_per_kg"] is None
