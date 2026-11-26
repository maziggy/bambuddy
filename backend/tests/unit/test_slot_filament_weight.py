"""The weight Bambuddy sends a slot from the inventory, and when a slot needs it again."""

import pytest

from backend.app.services import slot_filament_weight as weights
from backend.app.services.slot_filament_weight import FilamentWeight, filament_weight

BAMBU_TRAY_UUID = "EB335968299543078925C71D83DA3864"
SLOT = (1, 32, 4)
SPOOL_7 = (weights.SPOOLMAN, 7)


class TestFilamentWeight:
    def test_spoolman_grams_are_rounded(self):
        assert filament_weight(1000.4, 965.6) == FilamentWeight(1000, 966)

    def test_the_printer_reports_a_truncated_percentage(self):
        # Verified on an H3C: 965 g of 1000 g came back as remain 96.
        assert FilamentWeight(1000, 965).percent == 96

    def test_more_left_than_the_spool_holds_is_a_full_spool(self):
        assert filament_weight(250, 260) == FilamentWeight(250, 250)

    def test_a_bambu_spools_remaining_weight_is_capped_at_1000_g(self):
        assert filament_weight(3000, 2500, rfid=True) == FilamentWeight(3000, 1000, rfid=True)
        assert filament_weight(3000, 2500) == FilamentWeight(3000, 2500)

    @pytest.mark.parametrize(
        ("total", "remain"),
        [
            (None, 500),  # no net weight in Spoolman
            (1000, None),  # no remaining weight
            (1000, 0.4),  # empty: Studio takes 1 g at least
            (0, 0),
            (10001, 500),  # over Studio's 10000 g
            (True, 500),  # not a number
            (float("nan"), 500),
        ],
    )
    def test_no_usable_weight(self, total, remain):
        assert filament_weight(total, remain) is None


class TestWhenASlotNeedsItsWeight:
    def test_a_tray_without_a_weight_needs_one(self):
        assert weights.needs_weight(SLOT, SPOOL_7, {"remain": -1})

    def test_a_tray_with_the_printers_own_figure_is_left_alone(self):
        assert not weights.needs_weight(SLOT, SPOOL_7, {"remain": 100, "tray_uuid": BAMBU_TRAY_UUID})

    def test_once_per_fill(self):
        weights.weight_handled(SLOT, SPOOL_7)
        assert not weights.needs_weight(SLOT, SPOOL_7, {"remain": -1})

    def test_a_reinserted_spool_needs_it_again(self):
        weights.weight_handled(SLOT, SPOOL_7)
        weights.mark_pending(SLOT)
        assert weights.needs_weight(SLOT, SPOOL_7, {"remain": 100})

    def test_another_spool_in_the_slot_needs_its_own(self):
        weights.weight_handled(SLOT, SPOOL_7)
        assert weights.needs_weight(SLOT, (weights.SPOOLMAN, 8), {"remain": -1})

    def test_the_two_inventories_spool_ids_are_kept_apart(self):
        """Built-in spool 7 and Spoolman spool 7 are different spools."""
        weights.weight_handled(SLOT, (weights.INVENTORY, 7))
        assert weights.needs_weight(SLOT, SPOOL_7, {"remain": -1})


class TestReadsAs:
    @pytest.mark.parametrize(
        ("remaining", "remain_pct", "rfid"),
        [
            (965.4, 96, False),  # truncated, as the printer reports it
            (559.56, 56, False),  # sent as 560 g
            (559.56, 55, False),  # as it stands
            (1100, 100, False),  # more left than the spool holds: sent as full
        ],
    )
    def test_a_figure_that_comes_to_the_printers_percentage(self, remaining, remain_pct, rfid):
        assert weights.reads_as(1000, remaining, remain_pct, rfid=rfid)

    def test_a_bambu_spool_as_sent_capped_at_1000_g(self):
        assert weights.reads_as(1250, 1100, 80, rfid=True)
        assert not weights.reads_as(1250, 1100, 80)

    @pytest.mark.parametrize("remaining", [None, True, float("nan"), 540.0])
    def test_not_otherwise(self, remaining):
        assert not weights.reads_as(1000, remaining, 55)


def test_a_bambu_tag_makes_an_rfid_tray():
    assert weights.is_rfid_tray({"tray_uuid": BAMBU_TRAY_UUID})
    assert not weights.is_rfid_tray({"tray_uuid": "0" * 32})
    assert not weights.is_rfid_tray({})


def test_tray_and_spool_tags_meet_in_spoolmans_forms():
    # An untagged tray reports zeros: no tags, so the tag check doesn't apply.
    assert weights.tray_tags({"tray_uuid": "0" * 32, "tag_uid": "0" * 16}) == set()
    tray = weights.tray_tags({"tray_uuid": BAMBU_TRAY_UUID.lower(), "tag_uid": "D3E68F3200000100"})
    # extra.tag is stored as JSON; a native tag holds the chip's own 4-byte UID.
    assert weights.spool_tags({"extra": {"tag": f'"{BAMBU_TRAY_UUID}"'}}) & tray
    assert weights.spool_tags({"tags": [{"uid": "D3E68F32"}]}) & tray
    assert not weights.spool_tags({"extra": {"tag": '"AAAA0000"'}, "tags": []}) & tray
