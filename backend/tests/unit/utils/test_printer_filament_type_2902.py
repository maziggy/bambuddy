"""What a spool's material becomes when it is written into an AMS slot (#2902).

The reporter had eSUN PLA+ in his inventory. Bambuddy wrote "PLA+" into the
slot's ``tray_type``, and neither slicer has such a type -- so a plate sliced
with any PLA profile could not use that slot. The same string also went into
the generic-filament-id lookup, which missed, so the slot went out with an
empty ``tray_info_idx`` on top.

"PLA+" is not a special case. The bundled colour catalogue's material column
is the vendor's product line, and roughly forty of its values are not filament
types: HTPLA, PolyTerra PLA, PLA Matte, ASA Extrafill, Flexfill TPU 98A.
"""

import pytest

from backend.app.core.catalog_defaults import DEFAULT_COLOR_CATALOG
from backend.app.utils.filament_ids import filament_id_to_setting_id
from backend.app.utils.filament_types import (
    _PRINTER_TYPES,
    is_material_name,
    printer_filament_type,
)


class TestTheReportedCase:
    def test_pla_plus_is_pla(self):
        assert printer_filament_type("PLA+") == "PLA"

    def test_so_is_every_other_way_a_vendor_writes_pla(self):
        # All four are shipped catalogue values, so all four could be sitting in
        # someone's material field right now.
        assert printer_filament_type("HTPLA") == "PLA"
        assert printer_filament_type("PolyTerra PLA") == "PLA"
        assert printer_filament_type("PLA Matte") == "PLA"
        assert printer_filament_type("Pro PLA+") == "PLA"


class TestItLeavesAlonePreciselyWhatItCannotPlace:
    """The load-bearing half of the contract. Anything this cannot recognise has
    to come back untouched, because that is what the four assignment paths sent
    before -- so the change can only ever fix a slot, never break a working one.
    """

    @pytest.mark.parametrize("unknown", ["CPE HG100", "FiberSilk Metallic", "XT", "NylonX"])
    def test_an_unrecognised_material_survives_verbatim(self, unknown):
        assert printer_filament_type(unknown) == unknown

    @pytest.mark.parametrize(
        "preset_id",
        [
            "GFL99",  # Bambu generic
            "GFSL99",  # its setting_id form
            "GFA01",  # Bambu official
            "P4d64437",  # local user preset
            "PFUS9ac902733670a9",  # cloud user preset
            "PFCN0123abcd",  # cloud shared preset
        ],
    )
    def test_a_filament_id_is_never_mistaken_for_a_material(self, preset_id):
        """slicer_filament_resolver runs candidate tray_info_idx values through
        this to decide whether they are really a material name in disguise. A
        preset id that came back as "PA" would be discarded as junk and the slot
        would silently lose the user's calibrated profile."""
        assert printer_filament_type(preset_id) == preset_id

    def test_a_word_that_merely_starts_like_a_type_is_not_that_type(self):
        assert printer_filament_type("PLASTIC") == "PLASTIC"

    @pytest.mark.parametrize("name", ["Pastel", "Sparkle", "Pearl"])
    def test_two_letter_types_are_not_hunted_inside_words(self, name):
        """ "PA" is nylon and "Pastel" is not. There is no rule that separates
        them by shape, so PA/PC/PE/PP are only ever read as a word of their own."""
        assert printer_filament_type(name) == name

    def test_but_a_spool_that_really_is_bare_nylon_still_says_so(self):
        assert printer_filament_type("PA") == "PA"
        assert printer_filament_type("PC") == "PC"
        assert printer_filament_type("Nylon") == "PA"


class TestHowItReadsAName:
    def test_a_type_at_the_end_of_a_word_counts(self):
        # How vendors write their own: Protopasta's HTPLA, Filamentum's rPETG.
        assert printer_filament_type("HTPLA") == "PLA"
        assert printer_filament_type("rPETG") == "PETG"
        assert printer_filament_type("ReForm rPLA") == "PLA"

    def test_a_type_at_the_start_counts_only_when_a_non_letter_follows(self):
        assert printer_filament_type("PLA+") == "PLA"
        assert printer_filament_type("PETG-HS") == "PETG"
        assert printer_filament_type("PLA-ST") == "PLA"
        assert printer_filament_type("PLASTIC") == "PLASTIC"

    def test_the_longest_type_wins(self):
        """Otherwise a carbon-filled spool would be configured as plain PLA and
        print at the wrong temperature."""
        assert printer_filament_type("Hyper PLA-CF") == "PLA-CF"
        assert printer_filament_type("PLA-CF") == "PLA-CF"
        assert printer_filament_type("PAHT-CF") == "PAHT-CF"

    def test_a_blend_reads_as_its_first_named_type(self):
        # PLA/PHA prints as PLA, and PLA is the half a slicer profile targets.
        assert printer_filament_type("PLA/PHA") == "PLA"

    def test_case_and_padding_do_not_matter(self):
        assert printer_filament_type("pla") == "PLA"
        assert printer_filament_type("  PLA  ") == "PLA"
        assert printer_filament_type("pLa MaTtE") == "PLA"

    @pytest.mark.parametrize("empty", [None, "", "   "])
    def test_nothing_in_nothing_out(self, empty):
        assert printer_filament_type(empty) == ""


class TestTheShippedCatalogue:
    """The catalogue is where the reporter's PLA+ came from, so it is also the
    honest test set: every material Bambuddy itself puts in front of a user."""

    def test_every_catalogue_material_lands_on_a_type_or_is_left_alone(self):
        # Names that carry no filament type at all. Nothing can be made of them,
        # so they must pass through -- exactly as they did before #2902.
        unplaceable = {"CPE HG100", "FiberSilk Metallic", "XT", "NylonX", "NylonG"}
        known = set(_PRINTER_TYPES)

        for entry in DEFAULT_COLOR_CATALOG:
            material = entry[3]
            result = printer_filament_type(material)
            if material in unplaceable:
                assert result == material, f"{material!r} should have been left alone"
            else:
                assert result in known, f"{material!r} reduced to {result!r}, which is not a filament type"

    def test_the_ones_that_cannot_be_placed_are_still_only_those_five(self):
        """A guard on the list above: if a catalogue sync adds a sixth, this
        fails and someone gets to decide what it is, rather than a slot quietly
        going out with a product name in it again."""
        known = set(_PRINTER_TYPES)
        leftover = {e[3] for e in DEFAULT_COLOR_CATALOG if printer_filament_type(e[3]) not in known}
        assert leftover == {"CPE HG100", "FiberSilk Metallic", "XT", "NylonX", "NylonG"}


class TestIsMaterialName:
    """The question the slicer-filament resolver and the slot-reuse check both
    ask of a candidate filament id, and have to answer the same way."""

    @pytest.mark.parametrize(
        "name",
        ["PLA", "PETG", "PA-CF", "PETG HF", "NYLON", "  PLA  ", "PLA+", "HTPLA", "PolyTerra PLA", "Pro PLA+"],
    )
    def test_a_material_or_a_product_line_is_one(self, name):
        assert is_material_name(name) is True

    @pytest.mark.parametrize(
        "preset_id",
        [
            "GFL99",
            "GFA01",
            "GFSL05_07",
            "GFNC0",
            "P4d64437",
            "PFUS9ac902733670a9",
            "PFCN0123abcd",
            # The trap this guard exists for. Bambu ids carry letters in these
            # positions (GFNC0 does), so an id that happens to end in a material
            # name is not far-fetched -- and reading it as one would throw the
            # user's calibrated preset out of the slot.
            "GFPLA",
            "GFSABS",
            "GFTPU",
            "GFPVA",
            "GFASA",
        ],
    )
    def test_a_preset_id_never_is(self, preset_id):
        assert is_material_name(preset_id) is False

    def test_no_shipped_bambu_id_reads_as_a_material(self):
        """The exhaustive version of the case above, over the whole catalogue
        this repo knows, in both the filament_id and setting_id spellings."""
        from backend.app.api.routes.cloud import _BUILTIN_FILAMENT_NAMES

        for fid in _BUILTIN_FILAMENT_NAMES:
            assert not is_material_name(fid), fid
            assert not is_material_name(filament_id_to_setting_id(fid)), fid

    @pytest.mark.parametrize("unknown", ["PCTG", "PPS-CF", "CPE HG100", "XT", "", None])
    def test_anything_it_cannot_place_is_left_for_the_caller_to_use(self, unknown):
        """False means "keep it". A type the tables do not carry is not proof
        the value is junk, and discarding it would empty the slot's filament id
        for no reason."""
        assert is_material_name(unknown) is False
