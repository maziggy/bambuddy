"""Which ``.gcode`` member of a 3MF a plate resolves to.

A sliced multi-plate 3MF holds one toolpath per plate, and the order they sit
in the zip is whatever the slicer wrote — not plate order. A real two-plate
export measured for this fix stores ``Metadata/plate_2.gcode`` ahead of
``Metadata/plate_1.gcode``, so every caller that took the first member was
opening plate 2 on a file whose first plate is plate 1.
"""

from backend.app.utils.threemf_tools import (
    default_plate_gcode_name,
    default_plate_number,
    select_plate_gcode_name,
)

# The exact member order of the reporter's AMS_Rack.gcode.3mf.
REVERSED_ORDER = ["Metadata/plate_2.gcode", "Metadata/plate_1.gcode"]


class TestDefaultPlateGcodeName:
    def test_picks_the_lowest_plate_not_the_first_member(self):
        assert default_plate_gcode_name(REVERSED_ORDER) == "Metadata/plate_1.gcode"

    def test_ignores_non_gcode_members(self):
        names = ["Metadata/plate_1.png", "Metadata/plate_2.gcode", "3D/3dmodel.model", "Metadata/plate_1.gcode"]
        assert default_plate_gcode_name(names) == "Metadata/plate_1.gcode"

    def test_a_gcode_md5_sidecar_is_not_mistaken_for_the_toolpath(self):
        # Bambu writes plate_N.gcode.md5 next to each plate; it ends in .md5,
        # so it must not win the lowest-plate sort.
        names = ["Metadata/plate_1.gcode.md5", "Metadata/plate_2.gcode", "Metadata/plate_1.gcode"]
        assert default_plate_gcode_name(names) == "Metadata/plate_1.gcode"

    def test_falls_back_to_first_member_when_nothing_is_plate_numbered(self):
        # Slicers that don't use the convention have no numbering to sort by.
        assert default_plate_gcode_name(["out.gcode", "other.gcode"]) == "out.gcode"

    def test_double_digit_plates_sort_numerically_not_lexically(self):
        names = ["Metadata/plate_10.gcode", "Metadata/plate_2.gcode"]
        assert default_plate_gcode_name(names) == "Metadata/plate_2.gcode"

    def test_returns_none_for_an_unsliced_file(self):
        assert default_plate_gcode_name(["3D/3dmodel.model"]) is None


class TestDefaultPlateNumber:
    """The integer a dispatch caller needs when no ``plate_id`` was asked for
    (#2947). Composes ``default_plate_gcode_name`` with the numeric index its
    chosen member encodes.
    """

    def test_picks_the_lowest_plate_not_the_first_member(self):
        assert default_plate_number(REVERSED_ORDER) == 1

    def test_single_plate_file_numbered_two_resolves_to_two(self):
        # The exact shape of a plate cut out of a multi-plate project: one
        # G-code member, keeping its ORIGINAL (non-1) plate number. Dispatch
        # hardcoding a plate-1 fallback here is #2947.
        assert default_plate_number(["Metadata/plate_2.gcode"]) == 2

    def test_a_gcode_md5_sidecar_is_not_mistaken_for_the_toolpath(self):
        names = ["Metadata/plate_1.gcode.md5", "Metadata/plate_2.gcode", "Metadata/plate_1.gcode"]
        assert default_plate_number(names) == 1

    def test_double_digit_plates_sort_numerically_not_lexically(self):
        names = ["Metadata/plate_10.gcode", "Metadata/plate_2.gcode"]
        assert default_plate_number(names) == 2

    def test_returns_none_for_an_unsliced_file(self):
        assert default_plate_number(["3D/3dmodel.model"]) is None

    def test_returns_none_when_the_default_member_has_no_plate_number(self):
        # default_plate_gcode_name falls back to the first .gcode member for
        # a slicer that doesn't use the plate_N convention; that member
        # carries no parseable number, and this must not invent one — the
        # dispatch caller is the one that falls back to plate 1.
        assert default_plate_number(["out.gcode", "other.gcode"]) is None


class TestSelectPlateGcodeName:
    def test_selects_the_named_plate_regardless_of_zip_order(self):
        assert select_plate_gcode_name(REVERSED_ORDER, 1) == "Metadata/plate_1.gcode"
        assert select_plate_gcode_name(REVERSED_ORDER, 2) == "Metadata/plate_2.gcode"

    def test_zero_padded_names_match_the_index_the_plates_endpoint_reports(self):
        assert select_plate_gcode_name(["Metadata/plate_01.gcode"], 1) == "Metadata/plate_01.gcode"

    def test_returns_none_for_a_plate_the_file_does_not_hold(self):
        # Never a silent fallback: the caller asked for a specific plate, and
        # serving a different one is how the viewer showed the wrong toolpath.
        assert select_plate_gcode_name(REVERSED_ORDER, 3) is None

    def test_returns_none_without_a_plate_id(self):
        assert select_plate_gcode_name(REVERSED_ORDER, None) is None

    def test_does_not_match_a_prefix_of_a_longer_number(self):
        assert select_plate_gcode_name(["Metadata/plate_12.gcode"], 1) is None
