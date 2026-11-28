"""Colour families and the coloured dot for notifications (#3641)."""

import json
from pathlib import Path

import pytest

from backend.app.utils.color_utils import color_emoji, color_family, color_note

# The Inventory page's copy of these rules (``colorFamily`` in
# ``frontend/src/utils/colors.ts``) reads the same file, so they can't disagree.
_SHARED = Path(__file__).resolve().parents[3] / "frontend" / "src" / "__tests__" / "utils" / "colorFamilyVectors.json"


@pytest.mark.parametrize("case", json.loads(_SHARED.read_text())["cases"], ids=lambda c: f"{c['hex']}->{c['family']}")
def test_family_matches_the_frontend_vectors(case):
    assert color_family(case["hex"]) == case["family"]


@pytest.mark.parametrize(
    ("hex_value", "emoji"),
    [
        ("FF0000", "\U0001f534"),
        ("FF8000", "\U0001f7e0"),
        ("FFFF00", "\U0001f7e1"),
        ("00FF00", "\U0001f7e2"),
        ("00FFFF", "\U0001f535"),
        ("0000FF", "\U0001f535"),
        ("B39DDB", "\U0001f7e3"),
        ("FF69B4", "\U0001fa77"),
        ("8B4513", "\U0001f7e4"),
        ("FFFFFF", "⚪"),
        ("CCCCCC", "⚪"),
        ("808080", "⚪"),  # Gray, lightness just above 0.5
        ("7F7F7F", "⚫"),  # Gray, lightness just below 0.5
        ("333333", "⚫"),
        ("000000", "⚫"),
        ("00000000", "⚪"),  # clear
        ("FF000080", "\U0001f534"),  # translucent: by its RGB
    ],
)
def test_emoji_per_family(hex_value, emoji):
    assert color_emoji(hex_value) == emoji


@pytest.mark.parametrize("hex_value", [None, "", "abc", "zzzzzz"])
def test_unreadable_colour_has_no_dot(hex_value):
    assert color_emoji(hex_value) == ""


def test_several_colours_are_a_rainbow():
    assert color_emoji("FF0000FF", "00ff00,0000ff") == "\U0001f308"
    assert color_emoji("FF0000FF", "") == "\U0001f534"
    assert color_emoji("FF0000FF", " , ") == "\U0001f534"


def test_note_names_clear_and_translucent():
    assert color_note("00000000") == "clear"
    assert color_note("FF000080") == "translucent"
    assert color_note("FF0000FF") == ""
    assert color_note("FF0000") == ""
    assert color_note(None) == ""
