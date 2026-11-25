"""Identity of an inventory spool for a filament override (#3590)."""

from types import SimpleNamespace

import pytest

from backend.app.services.spool_identity import (
    identity_matches,
    spool_override_identity,
    spool_tray_type,
)


def _spool(**kw):
    base = {
        "material": "PLA",
        "subtype": None,
        "brand": None,
        "rgba": "FF0000FF",
        "slicer_filament": None,
        "slicer_filament_name": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


class TestSpoolTrayType:
    def test_product_line_reduced(self):
        assert spool_tray_type("PLA+") == "PLA"

    def test_nylon_alias(self):
        assert spool_tray_type("Nylon") == "PA"

    def test_type_override_wins(self):
        assert spool_tray_type("PLA", type_override="PETG") == "PETG"

    def test_support_id(self):
        assert spool_tray_type("PLA", tray_info_idx="GFS00") == "PLA-S"

    def test_support_name_hint(self):
        assert spool_tray_type("PLA", name_hint="Some Support for PLA") == "PLA-S"

    def test_plain_pla_untouched(self):
        assert spool_tray_type("PLA", name_hint="Bambu PLA Basic") == "PLA"


class TestOverrideIdentity:
    def test_specific_id_kept(self):
        ident = spool_override_identity(_spool(slicer_filament="GFA00"))
        assert (ident.type, ident.color, ident.tray_info_idx) == ("PLA", "#FF0000", "GFA00")

    def test_setting_id_normalised(self):
        assert spool_override_identity(_spool(slicer_filament="GFSL05_07")).tray_info_idx == "GFL05"

    @pytest.mark.parametrize(
        "sf", ["GFL99", "GFG99", "GFB98", "GFL96", "PLA", "PFUS990b6e19965353", "PFCN123", "38", "", None, "Pbd31b30"]
    )
    def test_generic_or_unresolvable_gives_no_idx(self, sf):
        assert spool_override_identity(_spool(slicer_filament=sf)).tray_info_idx == ""

    def test_uuid_gives_no_idx(self):
        assert (
            spool_override_identity(_spool(slicer_filament="123e4567-e89b-12d3-a456-426614174000")).tray_info_idx == ""
        )

    def test_null_rgba_is_white(self):
        assert spool_override_identity(_spool(rgba=None)).color == "#FFFFFF"

    def test_lowercase_rgba_upper(self):
        assert spool_override_identity(_spool(rgba="aabbccdd")).color == "#AABBCC"

    def test_support_spool(self):
        ident = spool_override_identity(_spool(slicer_filament="GFS02"))
        assert ident.type == "PLA-S"

    def test_support_by_name(self):
        ident = spool_override_identity(_spool(slicer_filament_name="Acme Support for PLA"))
        assert ident.type == "PLA-S"

    def test_plus_material(self):
        assert spool_override_identity(_spool(material="PLA+", brand="eSUN")).type == "PLA"


class TestMatchingRule:
    def test_idx_only_when_both_set(self):
        assert identity_matches("PLA", "#ff0000", "", "PLA", "#FF0000", "GFA00")
        assert identity_matches("PLA", "#FF0000", "GFA00", "PLA", "#FF0000", "")
        assert not identity_matches("PLA", "#FF0000", "GFA00", "PLA", "#FF0000", "GFA01")

    def test_colour_and_type(self):
        assert not identity_matches("PLA", "#FF0000", "", "PLA", "#00FF00", "")
        assert not identity_matches("PLA", "#FF0000", "", "PETG", "#FF0000", "")
        assert identity_matches("PA-CF", "#FF0000", "", "PAHT-CF", "#FF0000", "")
