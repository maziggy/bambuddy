"""Verification for the CIEDE2000 colour metric used to rank spool matches.

The matcher ranks the spools its tolerance admits by how far their colour is
from the one the file asks for. That ranking is only as trustworthy as the
metric, so the formula is pinned against the published reference set rather
than against numbers this codebase produced — an implementation that agrees
with 31 independently published values is right; one that agrees with its own
output is merely consistent.

``_ciede2000`` is private, and driven directly here on purpose: the reference
data is expressed in L*a*b*, so going through the hex entry point would fold
the sRGB conversion into what is meant to test the difference formula alone.
"""

import math

import pytest

from backend.app.utils.color_utils import _ciede2000, _hex_to_lab, perceptual_color_distance

# Sharma, Wu & Dalal, "The CIEDE2000 Color-Difference Formula", Table 1.
# Pairs 9-12 straddle the hue-angle discontinuity and are what catch a sign
# error in the mean-hue branch; pairs 30-31 sit near black where the lightness
# weighting dominates.
SHARMA_PAIRS = [
    ((50.0000, 2.6772, -79.7751), (50.0000, 0.0000, -82.7485), 2.0425),
    ((50.0000, 3.1571, -77.2803), (50.0000, 0.0000, -82.7485), 2.8615),
    ((50.0000, 2.8361, -74.0200), (50.0000, 0.0000, -82.7485), 3.4412),
    ((50.0000, -1.3802, -84.2814), (50.0000, 0.0000, -82.7485), 1.0000),
    ((50.0000, -1.1848, -84.8006), (50.0000, 0.0000, -82.7485), 1.0000),
    ((50.0000, -0.9009, -85.5211), (50.0000, 0.0000, -82.7485), 1.0000),
    ((50.0000, 0.0000, 0.0000), (50.0000, -1.0000, 2.0000), 2.3669),
    ((50.0000, -1.0000, 2.0000), (50.0000, 0.0000, 0.0000), 2.3669),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0009), 7.1792),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0010), 7.1792),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0011), 7.2195),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0012), 7.2195),
    ((50.0000, 2.5000, 0.0000), (50.0000, 0.0000, -2.5000), 4.3065),
    ((50.0000, 2.5000, 0.0000), (73.0000, 25.0000, -18.0000), 27.1492),
    ((50.0000, 2.5000, 0.0000), (61.0000, -5.0000, 29.0000), 22.8977),
    ((50.0000, 2.5000, 0.0000), (56.0000, -27.0000, -3.0000), 31.9030),
    ((50.0000, 2.5000, 0.0000), (58.0000, 24.0000, 15.0000), 19.4535),
    ((50.0000, 2.5000, 0.0000), (50.0000, 3.1736, 0.5854), 1.0000),
    ((50.0000, 2.5000, 0.0000), (50.0000, 3.2972, 0.0000), 1.0000),
    ((50.0000, 2.5000, 0.0000), (50.0000, 1.8634, 0.5757), 1.0000),
    ((50.0000, 2.5000, 0.0000), (50.0000, 3.2592, 0.3350), 1.0000),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ((63.0109, -31.0961, -5.8663), (62.8187, -29.7946, -4.0864), 1.2630),
    ((61.2901, 3.7196, -5.3901), (61.4292, 2.2480, -4.9620), 1.8731),
    ((35.0831, -44.1164, 3.7933), (35.0232, -40.0716, 1.5901), 1.8645),
    ((22.7233, 20.0904, -46.6940), (23.0331, 14.9730, -42.5619), 2.0373),
    ((36.4612, 47.8580, 18.3852), (36.2715, 50.5065, 21.2231), 1.4146),
    ((90.8027, -2.0831, 1.4410), (91.1528, -1.6435, 0.0447), 1.4441),
    ((90.9257, -0.5406, -0.9208), (88.6381, -0.8985, -0.7239), 1.5381),
    ((6.7747, -0.2908, -2.4247), (5.8714, -0.0985, -2.2286), 0.6377),
    ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082),
]


class TestAgainstPublishedReference:
    @pytest.mark.parametrize(("lab1", "lab2", "expected"), SHARMA_PAIRS)
    def test_matches_sharma_reference_value(self, lab1, lab2, expected):
        assert _ciede2000(lab1, lab2) == pytest.approx(expected, abs=1e-4)

    @pytest.mark.parametrize(("lab1", "lab2", "_expected"), SHARMA_PAIRS)
    def test_is_symmetric(self, lab1, lab2, _expected):
        """Which spool is 'first' must not change how far apart two colours are."""
        assert _ciede2000(lab1, lab2) == pytest.approx(_ciede2000(lab2, lab1), abs=1e-12)

    def test_a_colour_is_zero_from_itself(self):
        assert _ciede2000((50.0, 2.5, 0.0), (50.0, 2.5, 0.0)) == 0.0


class TestHexEntryPoint:
    def test_identical_colours_are_zero_apart(self):
        assert perceptual_color_distance("#3A7BD5", "3A7BD5FF") == 0.0

    def test_alpha_is_ignored_so_a_transparent_filament_matches_itself(self):
        assert perceptual_color_distance("#76D9F4", "76D9F400") == 0.0

    @pytest.mark.parametrize("bad", [None, "", "#abc", "#zzzzzz", "   "])
    def test_unusable_input_is_none_rather_than_a_number(self, bad):
        assert perceptual_color_distance(bad, "#3A7BD5") is None
        assert perceptual_color_distance("#3A7BD5", bad) is None

    def test_black_and_white_are_the_full_lightness_range_apart(self):
        # L* runs 0..100, and with no chroma difference dE00 reduces to dL/SL.
        assert perceptual_color_distance("#000000", "#FFFFFF") == pytest.approx(100.0, abs=0.01)

    def test_pure_hues_are_far_apart(self):
        assert perceptual_color_distance("#FF0000", "#0000FF") > 50


class TestWhyItReplacedRgbDistance:
    """RGB distance rates a colour by how far apart the numbers are, which is
    not how far apart they look. These are the cases that motivated the swap."""

    def test_rgb_would_rank_a_purple_above_a_green_for_a_green_requirement(self):
        required = "#1E4821"  # dark green
        purple = "#38202F"
        green = "#43683E"

        # Both sit inside the per-channel tolerance, so both are eligible and
        # the ranking alone decides which one prints.
        assert all(
            abs(int(required[1:][i : i + 2], 16) - int(c[1:][i : i + 2], 16)) <= 40
            for c in (purple, green)
            for i in (0, 2, 4)
        )

        def rgb_distance(a, b):
            return math.dist(
                [int(a[1:][i : i + 2], 16) for i in (0, 2, 4)],
                [int(b[1:][i : i + 2], 16) for i in (0, 2, 4)],
            )

        # The old metric put the purple nearer...
        assert rgb_distance(purple, required) < rgb_distance(green, required)
        # ...and the perceptual one puts the green nearer, by a wide margin.
        assert perceptual_color_distance(green, required) < perceptual_color_distance(purple, required)

    def test_equal_rgb_distances_are_not_equally_visible(self):
        """Five steps of blue either side of the same colour are identical in
        RGB and measurably different perceptually — which is why the tie-break
        test uses genuinely identical colours."""
        required = "#3A7BD5"
        assert perceptual_color_distance("#3A7BD0", required) != perceptual_color_distance("#3A7BDA", required)


class TestLabConversion:
    def test_reference_white_maps_to_l100_and_no_chroma(self):
        # Not exact: the sRGB->XYZ matrix and the D65 white point are each
        # rounded independently in the standards, so white lands a few parts in
        # 10^6 off L*=100. Immaterial next to a just-noticeable difference of 1.
        lab = _hex_to_lab("FFFFFF")
        assert lab is not None
        light, a, b = lab
        assert light == pytest.approx(100.0, abs=1e-4)
        assert a == pytest.approx(0.0, abs=1e-3)
        assert b == pytest.approx(0.0, abs=1e-3)

    def test_black_maps_to_the_origin(self):
        assert _hex_to_lab("000000") == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)

    def test_greys_have_no_chroma(self):
        for grey in ("404040", "808080", "C0C0C0"):
            lab = _hex_to_lab(grey)
            assert lab is not None
            assert lab[1] == pytest.approx(0.0, abs=1e-3)
            assert lab[2] == pytest.approx(0.0, abs=1e-3)
