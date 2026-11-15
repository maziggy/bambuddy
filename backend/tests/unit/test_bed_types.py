"""Build plate vocabulary (#1306)."""

import pytest

from backend.app.models.build_plate import DEFAULT_BUILD_PLATES
from backend.app.utils.bed_types import (
    BED_TYPES,
    PLATE_TYPE_ANY,
    InstalledPlate,
    normalize_bed_type,
    plate_mismatch,
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


def test_every_seeded_plate_has_a_known_base_type():
    for key, _name, base_type, *_rest in DEFAULT_BUILD_PLATES:
        assert base_type in BED_TYPES, key


def test_3d_effect_plates_are_smooth_pei_and_off_by_default():
    effect = [row for row in DEFAULT_BUILD_PLATES if row[0].startswith("3d_effect_")]
    assert {row[3] for row in effect} == {"Carbon Fiber", "Starry", "Diamond", "Galaxy"}
    for _key, _name, base_type, _pattern, _image, enabled, _order in effect:
        assert base_type == "smooth_pei"
        assert enabled is False


SMOOTH = InstalledPlate(id=1, name="Smooth PEI Plate", base_type="smooth_pei")
CARBON = InstalledPlate(id=2, name="3D Effect – Carbon Fiber", base_type="smooth_pei")


class TestPlateMismatch:
    def test_untracked_printer_takes_anything(self):
        assert plate_mismatch(None, "textured_pei", 5) is None

    def test_no_requirement(self):
        assert plate_mismatch(SMOOTH, None, None) is None
        assert plate_mismatch(SMOOTH, PLATE_TYPE_ANY, None) is None

    def test_base_type(self):
        assert plate_mismatch(SMOOTH, "smooth_pei", None) is None
        assert plate_mismatch(SMOOTH, "textured_pei", None) == "needs Textured PEI Plate, has Smooth PEI Plate"

    def test_pattern_counts_as_its_type(self):
        assert plate_mismatch(CARBON, "smooth_pei", None) is None

    def test_specific_plate(self):
        assert plate_mismatch(CARBON, "smooth_pei", 2) is None
        assert (
            plate_mismatch(SMOOTH, "smooth_pei", 2, "3D Effect – Carbon Fiber")
            == "needs 3D Effect – Carbon Fiber, has Smooth PEI Plate"
        )

    def test_specific_plate_whose_type_was_edited_away(self):
        """A custom plate re-typed after a job asked for it no longer fits."""
        retyped = InstalledPlate(id=2, name="Custom", base_type="textured_pei")
        assert plate_mismatch(retyped, "smooth_pei", 2) == "needs Smooth PEI / High Temp Plate, has Custom"
        assert plate_mismatch(retyped, PLATE_TYPE_ANY, 2) is None
