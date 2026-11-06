"""One table of generic Bambu filament ids, complete for every generic Bambu has.

A PP spool without a preset was sent to the printer with tray_info_idx=""
because the shared table had no PP row, while the Configure dialog's own copy
did (#3273). The copies are gone; these tests keep the table complete.
"""

from __future__ import annotations

import pytest

from backend.app.api.routes import printers
from backend.app.api.routes.cloud import _BUILTIN_FILAMENT_NAMES
from backend.app.utils.filament_ids import (
    GENERIC_FILAMENT_IDS,
    GENERIC_IDS_REPLACED_ON_REUSE,
    filament_id_to_setting_id,
    normalize_slicer_filament,
    setting_id_to_filament_id,
)
from backend.app.utils.filament_types import is_material_name


@pytest.mark.parametrize(
    "material,expected",
    [
        ("PP", "GFP97"),
        ("PE", "GFP99"),
        ("PCTG", "GFG97"),
        ("PLA SILK", "GFL96"),
        ("PLA HIGH SPEED", "GFL95"),
    ],
)
def test_the_materials_that_were_missing_have_their_generic(material, expected):
    assert GENERIC_FILAMENT_IDS[material] == expected


def test_every_id_is_a_bambu_generic_preset():
    for material, filament_id in GENERIC_FILAMENT_IDS.items():
        name = _BUILTIN_FILAMENT_NAMES.get(filament_id, "")
        assert name.startswith("Generic "), f"{material} -> {filament_id} is not a generic preset ({name!r})"
        assert filament_id_to_setting_id(filament_id) == "GFS" + filament_id[2:]


def test_the_reuse_check_replaces_exactly_the_generics_it_always_has():
    """The table grew; what slot reuse replaces must not have grown with it."""
    added = {"GFL96", "GFL95", "GFG97", "GFP99", "GFP97"}
    assert set(GENERIC_FILAMENT_IDS.values()) - added == GENERIC_IDS_REPLACED_ON_REUSE
    assert not hasattr(printers, "_GENERIC_ID_VALUES")


def test_the_configure_route_keeps_no_copy_of_its_own():
    assert not hasattr(printers, "_ORCA_GENERIC_IDS")
    assert printers._orca_generic_filament_id("PP") == "GFP97"
    assert printers._orca_generic_filament_id("PLA Silk") == "GFL96"


@pytest.mark.parametrize("material", ["PP", "PE", "PCTG"])
def test_a_material_with_a_generic_is_a_material_name_not_a_preset_id(material):
    """So the resolver discards it as a preset and the generic rescues the slot."""
    assert is_material_name(material)


@pytest.mark.parametrize(
    "filament_id,setting_id",
    [
        ("GFS99", "GFSS99"),  # Generic PVA
        ("GFS98", "GFSS98"),  # Generic HIPS
        ("GFS00", "GFSS00"),  # Bambu Support W
        ("GFL05", "GFSL05"),
    ],
)
def test_support_filaments_convert_both_ways(filament_id, setting_id):
    """Support filaments are family S, so their filament_id starts with "GFS"
    too. Read as a setting_id, Generic PVA went out as setting_id "GFS99" and
    came back as filament_id "GF99"; Bambu's own profile says GFSS99."""
    assert filament_id_to_setting_id(filament_id) == setting_id
    assert filament_id_to_setting_id(setting_id) == setting_id
    assert setting_id_to_filament_id(setting_id) == filament_id
    assert setting_id_to_filament_id(filament_id) == filament_id
    assert normalize_slicer_filament(filament_id) == (filament_id, setting_id)
    assert normalize_slicer_filament(setting_id + "_07") == (filament_id, setting_id)
