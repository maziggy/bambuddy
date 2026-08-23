"""Unit tests for color_utils — hex color similarity comparison."""

from backend.app.utils.color_utils import color_match_key, colors_similar, spoolman_color_hex


class TestColorsSimilar:
    """Tests for colors_similar()."""

    def test_exact_match(self):
        assert colors_similar("FF0000FF", "FF0000FF") is True

    def test_exact_match_case_insensitive(self):
        assert colors_similar("ff0000ff", "FF0000FF") is True

    def test_similar_colors_within_threshold(self):
        # Real-world case: RFID read variation (distance ~43.6)
        assert colors_similar("7CC4D5FF", "56B7E6FF") is True

    def test_different_colors_beyond_threshold(self):
        # Red vs blue (distance ~360)
        assert colors_similar("FF0000FF", "0000FFFF") is False

    def test_ignores_alpha_channel(self):
        # Same RGB, different alpha — should match
        assert colors_similar("FF000000", "FF0000FF") is True

    def test_six_digit_hex(self):
        assert colors_similar("FF0000", "FF0000") is True

    def test_short_string_returns_false(self):
        assert colors_similar("FFF", "FF0000") is False
        assert colors_similar("", "FF0000") is False

    def test_empty_strings_match(self):
        """Two empty strings are exact match (both missing data)."""
        assert colors_similar("", "") is True

    def test_invalid_hex_returns_false(self):
        assert colors_similar("ZZZZZZ", "FF0000") is False

    def test_whitespace_stripped(self):
        assert colors_similar(" FF0000 ", "FF0000") is True

    def test_custom_threshold(self):
        # Distance ~43.6 — within 50 but outside 30
        assert colors_similar("7CC4D5FF", "56B7E6FF", threshold=30) is False
        assert colors_similar("7CC4D5FF", "56B7E6FF", threshold=50) is True

    def test_black_and_near_black(self):
        # (10, 10, 10) distance from (0, 0, 0) = ~17.3
        assert colors_similar("000000", "0A0A0A") is True

    def test_white_and_off_white(self):
        assert colors_similar("FFFFFF", "F0F0F0") is True


class TestSpoolmanColorHex:
    """#2912 — what a colour is stored as in Spoolman's color_hex."""

    def test_opaque_value_stays_six_characters(self):
        """The common case must be byte-identical to what is already stored, or
        every opaque spool gets rewritten on its next touch."""
        assert spoolman_color_hex("FF0000FF") == "FF0000"

    def test_translucent_value_keeps_its_alpha(self):
        assert spoolman_color_hex("FF000080") == "FF000080"

    def test_fully_transparent_keeps_its_alpha(self):
        """The reported case: a clear spool reads as 00000000 and must not be
        stored as opaque black."""
        assert spoolman_color_hex("00000000") == "00000000"

    def test_six_character_input_passes_through(self):
        assert spoolman_color_hex("00FF00") == "00FF00"

    def test_normalises_case_and_hash_prefix(self):
        assert spoolman_color_hex("#ff000080") == "FF000080"

    def test_none_and_empty_return_none(self):
        assert spoolman_color_hex(None) is None
        assert spoolman_color_hex("") is None

    def test_short_value_passes_through_rather_than_being_padded(self):
        """A malformed value is reported as it is, not reshaped into something
        that looks valid."""
        assert spoolman_color_hex("FFF") == "FFF"


class TestColorMatchKey:
    """#2912 — two colours match exactly when storing them would give the same value."""

    def test_opaque_value_matches_its_six_character_twin(self):
        """The upgrade hazard: a user's existing filaments are all stored six
        characters. If an opaque 8-char value stopped matching them, the next AMS
        sync would mint a duplicate filament for every spool on the instance."""
        assert color_match_key("FF0000FF") == color_match_key("FF0000")

    def test_translucent_value_does_not_match_its_opaque_twin(self):
        """Both directions. A clear roll must not attach to the black filament,
        and — the case that only exists once 8-char values are storable — a black
        roll must not attach to a clear one and inherit its swatch and name."""
        assert color_match_key("00000000") != color_match_key("000000")
        assert color_match_key("00000000") != color_match_key("000000FF")

    def test_differs_when_the_rgb_differs(self):
        assert color_match_key("FF0000FF") != color_match_key("00FF00FF")

    def test_is_the_stored_shape(self):
        """Stated as an invariant because three separate comparisons rely on it."""
        for value in ("FF0000", "FF0000FF", "FF000080", "00000000"):
            assert color_match_key(value) == spoolman_color_hex(value)

    def test_normalises_case_and_hash_prefix(self):
        assert color_match_key("#ff0000") == "FF0000"

    def test_missing_value_is_empty_string(self):
        assert color_match_key(None) == ""
        assert color_match_key("") == ""
