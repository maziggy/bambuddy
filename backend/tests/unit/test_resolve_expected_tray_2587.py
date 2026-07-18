"""Unit tests for runout expected-slot resolution (#2587).

When a print pauses on an AMS filament runout, the firmware reports the slot it
now expects (``tray_tar``) and the slot that ran out (``tray_pre``) as bare
numbers whose meaning depends on the AMS layout. ``resolve_expected_tray``
globalises them to the same numbering the AMS graphic already highlights, so the
UI can point the operator at the right physical slot — critical with AMS
Filament Backup, which advances to the next compatible slot rather than
re-accepting the depleted one (reporter @Jostxxl saw the print wait on Slot 3
after Slot 2 ran out).
"""

from backend.app.services.printer_manager import resolve_expected_tray


def _single_regular():
    return [(0, False)]


def _dual_regular():
    return [(0, False), (1, False)]


class TestSingleRegularAms:
    """One 4-slot AMS: the local slot IS the global tray ID (matches tray_now)."""

    def test_reporter_scenario_tray_tar_2_is_slot_3(self):
        # @Jostxxl: tray_tar=2 (zero-based) -> physical Slot 3.
        assert resolve_expected_tray(2, _single_regular(), None) == 2

    def test_reporter_scenario_tray_pre_1_is_slot_2(self):
        # @Jostxxl: tray_pre=1 (zero-based) -> physical Slot 2 (the one that ran out).
        assert resolve_expected_tray(1, _single_regular(), None) == 1

    def test_all_four_slots_pass_through(self):
        for slot in range(4):
            assert resolve_expected_tray(slot, _single_regular(), None) == slot

    def test_single_ams_with_nonzero_id(self):
        # A lone AMS reporting id=1 globalises to 4+slot, not the bare slot.
        assert resolve_expected_tray(2, [(1, False)], None) == 6


class TestSentinels:
    """255 = none/idle, 254 = external spool, -1 = never-set."""

    def test_none_input(self):
        assert resolve_expected_tray(None, _single_regular(), None) is None

    def test_255_is_none(self):
        assert resolve_expected_tray(255, _single_regular(), None) is None

    def test_minus_one_is_none(self):
        assert resolve_expected_tray(-1, _single_regular(), None) is None

    def test_254_external_passes_through(self):
        assert resolve_expected_tray(254, _single_regular(), None) == 254


class TestAmsHt:
    """AMS-HT reports a global ID (128-135) directly — return it unchanged."""

    def test_ht_global_id_passthrough(self):
        assert resolve_expected_tray(129, [(129, True)], None) == 129

    def test_ht_alongside_regular(self):
        layout = [(0, False), (128, True)]
        assert resolve_expected_tray(128, layout, None) == 128
        # A 0-3 target still resolves against the single regular unit.
        assert resolve_expected_tray(3, layout, None) == 3


class TestMultiRegularAms:
    """Several 4-slot AMS: local slot is ambiguous; resolve via the mapping field.

    The snow-encoded mapping is ``ams_hw_id*256 + slot`` per entry.
    """

    def test_resolves_unambiguous_slot_via_mapping(self):
        # Mapping says slot 2 lives on AMS 1 -> global 1*4+2 = 6.
        mapping = [1 * 256 + 2]
        assert resolve_expected_tray(2, _dual_regular(), mapping) == 6

    def test_resolves_slot_on_ams0(self):
        mapping = [0 * 256 + 3]
        assert resolve_expected_tray(3, _dual_regular(), mapping) == 3

    def test_ambiguous_when_two_units_match_returns_none(self):
        # Both AMS 0 and AMS 1 have slot 1 mapped -> can't disambiguate.
        mapping = [0 * 256 + 1, 1 * 256 + 1]
        assert resolve_expected_tray(1, _dual_regular(), mapping) is None

    def test_no_mapping_returns_none(self):
        # Honest "can't determine" rather than guessing AMS 0.
        assert resolve_expected_tray(2, _dual_regular(), None) is None

    def test_unmapped_sentinel_ignored(self):
        # 65535 = unmapped; only the real entry counts.
        mapping = [65535, 1 * 256 + 0]
        assert resolve_expected_tray(0, _dual_regular(), mapping) == 4

    def test_ht_in_multi_layout_via_mapping(self):
        # A slot-0 target that maps to an AMS-HT hw id resolves to that global ID.
        layout = [(0, False), (1, False), (128, True)]
        mapping = [128 * 256 + 0]
        assert resolve_expected_tray(0, layout, mapping) == 128


class TestGlobalAndOutOfRange:
    def test_already_global_regular_id_passthrough(self):
        # 4-15 is already a global regular-AMS ID.
        assert resolve_expected_tray(6, _dual_regular(), None) == 6

    def test_out_of_range_returns_none(self):
        assert resolve_expected_tray(200, _single_regular(), None) is None

    def test_zero_slot_no_regular_ams_returns_none(self):
        # Only an AMS-HT present but a 0-3 target — can't place it.
        assert resolve_expected_tray(1, [(128, True)], None) is None
