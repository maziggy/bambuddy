"""Finish words in filament wording, for slot reuse and generic presets."""

import pytest

from backend.app.utils.filament_finish import different_finish, finish_generic_id, finishes


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("PLA Silk+", ["silk"]),
        ("Silk Multi-Color", ["silk"]),
        ("Bambu PETG HF", ["hf"]),
        ("PLA High Speed", ["high speed"]),
        ("Sunlu PLA", []),
        (None, []),
    ],
)
def test_finishes(text, expected):
    assert finishes(text) == expected


@pytest.mark.parametrize(
    ("spool", "slot", "differ"),
    [
        ("PLA Silk", "PLA Matte", True),
        ("PLA Silk+", "Bambu PLA Silk", False),
        ("PETG Basic", "PETG HF", True),
        ("PLA", "PLA Matte", False),
        ("PLA Silk", "", False),
    ],
)
def test_different_finish(spool, slot, differ):
    assert different_finish(spool, slot) is differ


@pytest.mark.parametrize(
    ("material", "text", "generic"),
    [
        ("PLA", "PLA Silk+", "GFL96"),
        ("PLA", "Silk Multi-Color", "GFL96"),
        ("PETG", "PETG HF", "GFG96"),
        ("PLA", "PLA Matte", ""),
        ("PLA", "", ""),
    ],
)
def test_finish_generic_id(material, text, generic):
    assert finish_generic_id(material, text) == generic
