"""Enforce color accepts a colour no eye tells apart (#3763).

The reporter's dual-colour silk spool has the main colour 55B688, which is what
the slot reports, while the 3MF asks for 55B687: the preset's colour, and the
spool's own first extra colour. Enforce color compared digit for digit, so the
job waited forever with exactly that spool loaded. Every forced check now
treats a perceptual difference below a just-noticeable one as the same colour,
and nothing an eye can tell apart.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.inventory_stock import StockEntry, stock_in
from backend.app.services.print_scheduler import PrintScheduler
from backend.app.services.spool_identity import identity_matches
from backend.app.utils.color_utils import forced_colors_match, perceptual_color_distance
from backend.tests.unit.test_force_color_pinned_3280 import _forced_req, _item, _scheduler, _tray

WANTED = "#55B687"
SPOOL = "55B688FF"  # one unit of blue off: a CIEDE2000 delta-E of about 0.26
VISIBLE = "55B68CFF"  # five units off: about 1.3, an eye can tell it apart


def test_the_two_shades_are_below_and_above_a_noticeable_difference():
    assert perceptual_color_distance(WANTED, SPOOL) < 1.0 < perceptual_color_distance(WANTED, VISIBLE)


class TestTheRule:
    def test_the_reported_shades_are_the_same_colour(self):
        assert forced_colors_match(WANTED, SPOOL)
        assert forced_colors_match("55b687", "#55B688")

    def test_a_shade_an_eye_tells_apart_is_not(self):
        assert not forced_colors_match(WANTED, VISIBLE)

    def test_the_same_hex_still_matches_and_a_missing_colour_does_not(self):
        assert forced_colors_match("#FFFFFF", "ffffffFF")
        assert not forced_colors_match(WANTED, "")
        assert not forced_colors_match(None, SPOOL)


def _trays(color):
    return {"ams": [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA", "tray_color": color, "tray_info_idx": ""}]}]}


def _override():
    return {"slot_id": 2, "type": "PLA", "color": WANTED, "force_color_match": True}


class TestTheLoadedTrayCheck:
    """``_missing_force_color_pairs``: does the printer hold the forced colour?"""

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_the_spool_of_the_report_satisfies_the_slot(self, mock_pm):
        mock_pm.get_status.return_value = MagicMock(raw_data=_trays(SPOOL))
        assert PrintScheduler()._get_missing_force_color_slots(4, [_override()]) == []

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_a_visibly_different_shade_does_not(self, mock_pm):
        mock_pm.get_status.return_value = MagicMock(raw_data=_trays(VISIBLE))
        assert PrintScheduler()._get_missing_force_color_slots(4, [_override()]) == ["PLA (#55B687)"]


class TestTheMatcher:
    """``_match_filaments_to_slots`` maps a forced slot onto the same colour."""

    def test_the_spool_of_the_report_is_mapped(self):
        trays = [_tray(0, "000000FF", idx=""), _tray(2, SPOOL, idx="")]
        assert PrintScheduler()._match_filaments_to_slots([_forced_req(WANTED, "")], trays) == [2]

    def test_a_visibly_different_shade_is_not(self):
        trays = [_tray(0, VISIBLE, idx="")]
        assert PrintScheduler()._match_filaments_to_slots([_forced_req(WANTED, "")], trays) == [-1]

    def test_the_very_same_hex_wins_over_a_shade_no_eye_tells_apart(self):
        trays = [_tray(0, SPOOL, idx=""), _tray(1, "55B687FF", idx="")]
        assert PrintScheduler()._match_filaments_to_slots([_forced_req(WANTED, "")], trays) == [1]


REQUIREMENTS = [{"slot_id": 1, "type": "PLA", "color": WANTED, "tray_info_idx": ""}]
FORCED = [{"slot_id": 1, "type": "PLA", "color": WANTED, "tray_info_idx": "", "force_color_match": True}]


class TestTheStoredMapping:
    """The reporter's log: "stored ams_mapping [-1, 2] does not fit ... recomputing"."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("color", "fits"), [(SPOOL, True), (VISIBLE, False)])
    async def test_a_mapping_onto_the_spool_is_kept(self, color, fits):
        scheduler = _scheduler([_tray(2, color, idx="")], requirements=REQUIREMENTS)
        item = _item(ams_mapping=json.dumps([2]), filament_overrides=json.dumps(FORCED))

        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock(raw_data={"ams": [{"id": 0}]}, ams_filament_backup=None)
            conflict = await scheduler._stored_mapping_conflict(AsyncMock(), 4, item, [2])

        assert (conflict is None) is fits


class TestTheStockCheck:
    """``identity_matches`` (#3590): a spool of the same colour is stock of it."""

    def test_the_spool_counts_as_stock_of_the_forced_colour(self):
        assert identity_matches("PLA", WANTED, "", "PLA", "#55B688", "")
        assert not identity_matches("PLA", WANTED, "", "PLA", "#55B68C", "")

    def test_stock_in_finds_it(self):
        entry = StockEntry(
            type="PLA",
            color="#55B688",
            tray_info_idx="",
            subtype="Silk",
            color_name="Jadeite",
            rgba=SPOOL,
            extra_colors="55b687,85898b",
            effect_type="dual-color",
            spool_count=1,
            remaining_grams=800.0,
        )
        assert stock_in([entry], "PLA", WANTED, "") == (1, 800.0)
