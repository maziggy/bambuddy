"""Build plate vocabulary (#1306)."""

import pytest

from backend.app.utils.bed_types import (
    BED_TYPES,
    PLATE_TYPE_ANY,
    InstalledPlate,
    normalize_bed_type,
    plate_label,
    plate_mismatch,
    same_pattern,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Bambu Studio curr_bed_type values
        ("Cool Plate", "cool_plate"),
        ("Engineering Plate", "engineering"),
        ("High Temp Plate", "smooth_pei"),
        ("Textured PEI Plate", "textured_pei"),
        ("Supertack Plate", "supertack"),
        # OrcaSlicer adds one
        ("Textured Cool Plate", "textured_cool_plate"),
        # UI labels and older spellings the frontend already recognises
        ("Smooth PEI Plate", "smooth_pei"),
        ("PEI Plate", "textured_pei"),
        ("PC Plate", "cool_plate"),
        ("Cool Plate (SuperTack)", "supertack"),
        ("Bambu Cool Plate SuperTack", "supertack"),
        # case and whitespace
        ("  high temp plate ", "smooth_pei"),
        # base-type keys pass through
        ("smooth_pei", "smooth_pei"),
    ],
)
def test_normalize(raw, expected):
    assert normalize_bed_type(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "Default Plate", "Glass Plate", "any"])
def test_normalize_unknown_is_no_constraint(raw):
    assert normalize_bed_type(raw) is None


def test_labels():
    assert plate_label("smooth_pei") == "Smooth PEI Plate"
    assert plate_label("smooth_pei", "Carbon Fiber") == "Smooth PEI Plate · Carbon Fiber"
    assert plate_label(None) == ""
    assert set(BED_TYPES) == {
        "cool_plate",
        "textured_cool_plate",
        "supertack",
        "engineering",
        "smooth_pei",
        "textured_pei",
    }


def test_patterns_compare_ignoring_case_and_spacing():
    assert same_pattern("Carbon Fiber", " carbon fiber ")
    assert same_pattern(None, "")
    assert not same_pattern("Carbon Fiber", "Starry")


SMOOTH = InstalledPlate(base_type="smooth_pei")
CARBON = InstalledPlate(base_type="smooth_pei", pattern="Carbon Fiber")
TEXTURED = InstalledPlate(base_type="textured_pei")


class TestPlateMismatch:
    def test_untracked_printer_takes_anything(self):
        assert plate_mismatch(None, "textured_pei", "Carbon Fiber") is None

    def test_no_requirement(self):
        assert plate_mismatch(SMOOTH, None) is None
        assert plate_mismatch(SMOOTH, PLATE_TYPE_ANY) is None
        # A pattern means nothing without its type.
        assert plate_mismatch(SMOOTH, PLATE_TYPE_ANY, "Carbon Fiber") is None

    def test_base_type(self):
        assert plate_mismatch(SMOOTH, "smooth_pei") is None
        assert plate_mismatch(SMOOTH, "textured_pei") == "needs Textured PEI Plate, has Smooth PEI Plate"

    def test_pattern_counts_as_its_type(self):
        assert plate_mismatch(CARBON, "smooth_pei") is None

    def test_pattern_requirement(self):
        assert plate_mismatch(CARBON, "smooth_pei", "carbon fiber") is None
        assert plate_mismatch(SMOOTH, "smooth_pei", "Carbon Fiber") == (
            "needs Smooth PEI Plate · Carbon Fiber, has Smooth PEI Plate"
        )
        assert plate_mismatch(TEXTURED, "smooth_pei", "Carbon Fiber") == (
            "needs Smooth PEI Plate · Carbon Fiber, has Textured PEI Plate"
        )
