"""Unit tests for color_utils — hex color similarity comparison."""

from backend.app.utils.color_utils import colors_similar


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
