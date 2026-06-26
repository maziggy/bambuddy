"""Regression pin for the filament_type select options exposed by
GET /cloud/fields/filament — used by the Profiles edit modal.

Issue #1686: PLA-CF and other CF/GF variants were missing from the dropdown,
so users could not tag custom presets with the correct material type.
"""

import json
from pathlib import Path

import pytest

FIELDS_PATH = Path(__file__).resolve().parents[2] / "app" / "data" / "filament_fields.json"


@pytest.fixture(scope="module")
def filament_type_options() -> set[str]:
    with FIELDS_PATH.open() as f:
        data = json.load(f)
    field = next(f for f in data["fields"] if f["key"] == "filament_type")
    return {opt["value"] for opt in field["options"]}


@pytest.mark.parametrize(
    "material",
    [
        # Reported in #1686
        "PLA-CF",
        # Other Bambu CF / GF / specialty variants that share the same gap
        "PLA-GF",
        "PLA-AERO",
        "PETG-CF",
        "ABS-GF",
        "ASA-CF",
        "ASA-GF",
        "PCTG",
        "PAHT-CF",
        "PA6-CF",
        "PA6-GF",
        "PPS",
        "PPS-CF",
        "PPS-GF",
    ],
)
def test_filament_type_includes_carbon_and_glass_fiber_variants(filament_type_options: set[str], material: str) -> None:
    assert material in filament_type_options


def test_filament_type_keeps_baseline_materials(
    filament_type_options: set[str],
) -> None:
    baseline = {"PLA", "PETG", "ABS", "ASA", "PC", "PA", "PA-CF", "PET-CF", "TPU", "PVA", "HIPS"}
    assert baseline.issubset(filament_type_options)
