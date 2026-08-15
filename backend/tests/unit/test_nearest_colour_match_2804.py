"""Regression tests for slot-order-dependent colour matching (#2804).

When no spool matches a required colour exactly, the matcher falls back to
spools inside a per-channel tolerance. It took the first of those in tray order,
so the winner depended on which slot a spool sat in rather than on which colour
was closest.

The worked example throughout is the maintainer's: a required ``#3A7BD5`` with a
purple ``#6253AD`` in tray 1 (40/40/40 off — admitted by the box) and a
near-identical ``#3B7AD2`` in tray 3. Both qualify; the purple used to win on
position alone.

Ranking is by CIEDE2000 delta-E, so the two are ~18.5 and ~0.5 apart rather
than the ~69 and ~3 an RGB metric reported. Eligibility is still the per-channel
RGB box, unchanged — only the ordering within it is perceptual.
"""

import pytest

from backend.app.services.print_scheduler import PrintScheduler

REQUIRED_COLOR = "#3A7BD5"
NEAR = "3B7AD2FF"  # dE00 ~0.5
FAR_BUT_ADMITTED = "6253ADFF"  # 40/40/40 off — inside the box, dE00 ~18.5


@pytest.fixture
def scheduler():
    return PrintScheduler.__new__(PrintScheduler)


def tray(global_tray_id, color, type_="PETG"):
    return {
        "global_tray_id": global_tray_id,
        "type": type_,
        "color": color,
        "tray_info_idx": "",
        "extruder_id": 0,
        "remain": 100,
    }


def req(color=REQUIRED_COLOR, slot_id=1, type_="PETG", tray_info_idx=""):
    return {"slot_id": slot_id, "type": type_, "color": color, "tray_info_idx": tray_info_idx}


class TestColorDistance:
    def test_identical_colours_are_zero_apart(self, scheduler):
        assert scheduler._color_distance("#3A7BD5", "3A7BD5FF") == 0

    def test_alpha_is_ignored_so_a_transparent_filament_matches_itself(self, scheduler):
        """The alpha a slicer writes is not a colour the user chose."""
        assert scheduler._color_distance("#76D9F4", "76D9F400") == 0

    def test_distance_is_perceptual_not_per_channel(self, scheduler):
        # A near-match is ranked by how different it looks, not by how far
        # apart the numbers are. Scale is CIEDE2000 delta-E, where ~1 is a
        # just-noticeable difference — see test_perceptual_color_distance.py
        # for the formula's verification against published reference data.
        assert scheduler._color_distance("#000000", "#282828") == pytest.approx(9.91, abs=0.01)

    def test_a_perceptually_nearer_colour_beats_a_numerically_nearer_one(self, scheduler):
        # Against a green requirement, a purple is the closer of the two by RGB
        # distance (49.7 vs 56.9) and much the further once measured
        # perceptually. Both are inside the tolerance, so ranking alone decides.
        required = "#1E4821"
        assert scheduler._color_distance("#43683E", required) < scheduler._color_distance("#38202F", required)

    def test_unusable_input_is_none_rather_than_a_number(self, scheduler):
        assert scheduler._color_distance(None, "#3A7BD5") is None
        assert scheduler._color_distance("", "#3A7BD5") is None
        assert scheduler._color_distance("#abc", "#3A7BD5") is None
        assert scheduler._color_distance("#zzzzzz", "#3A7BD5") is None


class TestNearestSimilarWins:
    def test_closest_admitted_colour_wins_regardless_of_slot_order(self, scheduler):
        loaded = [tray(1, FAR_BUT_ADMITTED), tray(3, NEAR)]
        assert scheduler._match_filaments_to_slots([req()], loaded) == [3]

    def test_result_does_not_depend_on_tray_order(self, scheduler):
        """The bug in one line: reversing the AMS used to reverse the answer."""
        forward = scheduler._match_filaments_to_slots([req()], [tray(1, FAR_BUT_ADMITTED), tray(3, NEAR)])
        reversed_ = scheduler._match_filaments_to_slots([req()], [tray(3, NEAR), tray(1, FAR_BUT_ADMITTED)])
        assert forward == reversed_ == [3]

    def test_an_exact_match_still_outranks_a_near_one(self, scheduler):
        loaded = [tray(1, NEAR), tray(2, "3A7BD5FF")]
        assert scheduler._match_filaments_to_slots([req()], loaded) == [2]

    def test_eligibility_is_unchanged_so_a_far_colour_is_still_type_only(self, scheduler):
        """Ranking must not admit spools the tolerance excluded: 41 off on one
        channel fails the box and can only win as a type-only fallback."""
        loaded = [tray(5, "3A7BFEFF")]  # blue channel 41 away
        assert scheduler._match_filaments_to_slots([req()], loaded) == [5]

    def test_ties_keep_the_caller_order_so_prefer_lowest_still_decides(self, scheduler):
        """Two spools equally close: the incoming order wins, which is the
        prefer-lowest sort when that preference is on.

        Same colour in both trays, so the tie is exact. Two *different* colours
        at equal RGB distance are not perceptually tied — that is the whole
        point of the metric — so they cannot be used to test this any more.
        """
        loaded = [tray(2, NEAR), tray(7, NEAR)]
        assert scheduler._match_filaments_to_slots([req()], loaded) == [2]
        assert scheduler._match_filaments_to_slots([req()], list(reversed(loaded))) == [7]

    def test_nearest_applies_within_a_shared_tray_info_idx_too(self, scheduler):
        """The tray_info_idx subset walks its own colour comparison."""
        loaded = [
            tray(1, FAR_BUT_ADMITTED) | {"tray_info_idx": "GFG99"},
            tray(3, NEAR) | {"tray_info_idx": "GFG99"},
        ]
        assert scheduler._match_filaments_to_slots([req(tray_info_idx="GFG99")], loaded) == [3]

    def test_type_is_still_a_hard_filter(self, scheduler):
        """A perfect colour in the wrong material never wins."""
        loaded = [tray(1, "3A7BD5FF", type_="ASA"), tray(3, NEAR)]
        assert scheduler._match_filaments_to_slots([req()], loaded) == [3]

    def test_each_slot_consumes_its_tray(self, scheduler):
        """Two slots wanting the same colour take different spools, nearest first."""
        loaded = [tray(1, FAR_BUT_ADMITTED), tray(3, NEAR)]
        mapping = scheduler._match_filaments_to_slots([req(slot_id=1), req(slot_id=2)], loaded)
        assert mapping == [3, 1]
