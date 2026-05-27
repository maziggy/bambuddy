"""Schema validation for enhanced filament colour fields (#1154).

Two new fields land on Spool and ColorCatalogEntry:

- `extra_colors`: comma-separated 6- or 8-char hex tokens. Stored canonical
  form is lowercase, no `#`, no whitespace. Bounded to MAX_EXTRA_COLOR_STOPS
  stops so a paste of arbitrary text can't blow up the column.
- `effect_type`: one of {sparkle, wood, marble, glow, matte}. Independent of
  Spool.subtype — purely a rendering hint.
"""

import pytest
from pydantic import ValidationError

from backend.app.schemas.spool import (
    ALLOWED_EFFECT_TYPES,
    MAX_EXTRA_COLOR_STOPS,
    SpoolCreate,
    SpoolUpdate,
    normalize_effect_type,
    normalize_extra_colors,
)


class TestNormalizeExtraColors:
    """The shared helper that both Spool and ColorCatalog schemas delegate to."""

    def test_none_passthrough(self):
        assert normalize_extra_colors(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_extra_colors("") is None
        assert normalize_extra_colors("   ") is None

    def test_strips_hash_prefix(self):
        assert normalize_extra_colors("#FF0000,#00FF00") == "ff0000,00ff00"

    def test_lowercases(self):
        assert normalize_extra_colors("AABBCC") == "aabbcc"

    def test_accepts_8char_alpha(self):
        assert normalize_extra_colors("AABBCC80,DDEEFF40") == "aabbcc80,ddeeff40"

    def test_mixed_6_and_8_char(self):
        assert normalize_extra_colors("FF0000,00FF0080") == "ff0000,00ff0080"

    def test_handles_whitespace_around_tokens(self):
        # 3dfilamentprofiles.com paste sometimes has spaces after commas.
        assert normalize_extra_colors("EC984C, #6CD4BC ,A66EB9, D87694") == "ec984c,6cd4bc,a66eb9,d87694"

    def test_drops_empty_tokens(self):
        # ",,FF0000," is a degenerate paste — keep what's valid.
        assert normalize_extra_colors(",,FF0000,") == "ff0000"

    def test_rejects_too_many_stops(self):
        too_many = ",".join(["FF0000"] * (MAX_EXTRA_COLOR_STOPS + 1))
        with pytest.raises(ValueError, match="at most"):
            normalize_extra_colors(too_many)

    def test_accepts_max_stops(self):
        boundary = ",".join(["FF0000"] * MAX_EXTRA_COLOR_STOPS)
        out = normalize_extra_colors(boundary)
        assert out is not None
        assert out.count(",") == MAX_EXTRA_COLOR_STOPS - 1

    def test_rejects_non_hex(self):
        with pytest.raises(ValueError, match="not valid hex"):
            normalize_extra_colors("FF0000,GGHHII")

    def test_rejects_wrong_length(self):
        with pytest.raises(ValueError, match="6 or 8 hex"):
            normalize_extra_colors("FFF")
        with pytest.raises(ValueError, match="6 or 8 hex"):
            normalize_extra_colors("FFFFF")
        with pytest.raises(ValueError, match="6 or 8 hex"):
            normalize_extra_colors("FFFFFFFFF")


class TestNormalizeEffectType:
    def test_none_passthrough(self):
        assert normalize_effect_type(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_effect_type("") is None
        assert normalize_effect_type("  ") is None

    def test_lowercases(self):
        assert normalize_effect_type("SPARKLE") == "sparkle"
        assert normalize_effect_type("Wood") == "wood"

    def test_accepts_all_known_types(self):
        for effect in ALLOWED_EFFECT_TYPES:
            assert normalize_effect_type(effect) == effect

    def test_canonicalizes_space_to_dash(self):
        # User pastes the spool-subtype label "Dual Color" / "Tri Color".
        assert normalize_effect_type("Dual Color") == "dual-color"
        assert normalize_effect_type("Tri Color") == "tri-color"

    def test_canonicalizes_underscore_to_dash(self):
        assert normalize_effect_type("dual_color") == "dual-color"

    def test_accepts_structural_variants(self):
        # Gradient / Multicolor were added in #1154 follow-up so the catalog
        # can express the full spool variant vocabulary.
        for variant in ("gradient", "dual-color", "tri-color", "multicolor"):
            assert normalize_effect_type(variant) == variant

    def test_accepts_sheen_variants(self):
        for sheen in ("silk", "galaxy", "rainbow", "metal", "translucent"):
            assert normalize_effect_type(sheen) == sheen

    def test_rejects_unknown_type(self):
        # "neon" isn't in the allowed set — must reject.
        with pytest.raises(ValueError, match="effect_type must be one of"):
            normalize_effect_type("neon")


class TestSpoolCreateColorExtensions:
    def test_accepts_extra_colors_paste(self):
        spool = SpoolCreate(material="PLA", extra_colors="EC984C,#6CD4BC,A66EB9,D87694")
        assert spool.extra_colors == "ec984c,6cd4bc,a66eb9,d87694"

    def test_accepts_effect_type(self):
        spool = SpoolCreate(material="PLA", effect_type="sparkle")
        assert spool.effect_type == "sparkle"

    def test_defaults_to_none(self):
        spool = SpoolCreate(material="PLA")
        assert spool.extra_colors is None
        assert spool.effect_type is None

    def test_rejects_bad_extra_colors(self):
        with pytest.raises(ValidationError, match="extra_colors"):
            SpoolCreate(material="PLA", extra_colors="not-hex")

    def test_rejects_bad_effect_type(self):
        with pytest.raises(ValidationError, match="effect_type"):
            SpoolCreate(material="PLA", effect_type="not-a-real-variant")


class TestSpoolUpdateColorExtensions:
    def test_clears_extra_colors_via_empty_string(self):
        # Frontend sends "" to clear; normalizer maps that to None.
        update = SpoolUpdate(extra_colors="")
        assert update.extra_colors is None

    def test_clears_effect_type_via_explicit_null(self):
        update = SpoolUpdate(effect_type=None)
        assert update.effect_type is None

    def test_round_trips_canonical_form(self):
        update = SpoolUpdate(extra_colors="FF0000,00FF00", effect_type="MATTE")
        assert update.extra_colors == "ff0000,00ff00"
        assert update.effect_type == "matte"


class TestColorCatalogSchemas:
    """The catalog Create/Update mirror the spool fields."""

    def test_create_accepts_8char_hex_color(self):
        from backend.app.api.routes.inventory import ColorEntryCreate

        entry = ColorEntryCreate(
            manufacturer="Bambu Lab",
            color_name="Galaxy",
            hex_color="#112233AA",
        )
        assert entry.hex_color == "#112233AA"

    def test_create_still_accepts_6char_hex_color(self):
        # Backward compat: existing #RRGGBB rows must keep working.
        from backend.app.api.routes.inventory import ColorEntryCreate

        entry = ColorEntryCreate(
            manufacturer="Bambu Lab",
            color_name="Jade White",
            hex_color="#A1B2C3",
        )
        assert entry.hex_color == "#A1B2C3"

    def test_create_rejects_hex_without_hash(self):
        from backend.app.api.routes.inventory import ColorEntryCreate

        with pytest.raises(ValidationError, match="hex_color"):
            ColorEntryCreate(manufacturer="X", color_name="Y", hex_color="A1B2C3")

    def test_create_threads_extra_colors_and_effect(self):
        from backend.app.api.routes.inventory import ColorEntryCreate

        entry = ColorEntryCreate(
            manufacturer="3dfilamentprofiles",
            color_name="Aurora",
            hex_color="#EC984C",
            extra_colors="EC984C,#6CD4BC,A66EB9,D87694",
            effect_type="sparkle",
        )
        assert entry.extra_colors == "ec984c,6cd4bc,a66eb9,d87694"
        assert entry.effect_type == "sparkle"

    def test_update_validators_match_create(self):
        from backend.app.api.routes.inventory import ColorEntryUpdate

        with pytest.raises(ValidationError, match="extra_colors"):
            ColorEntryUpdate(
                manufacturer="X",
                color_name="Y",
                hex_color="#A1B2C3",
                extra_colors="not-hex",
            )
