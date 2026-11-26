"""Pure-logic tests for the mid-print tray-split math (#1793).

The helper lives in ``backend/app/utils/tray_split.py`` and is exercised
by both inventory backends (``usage_tracker`` and ``spoolman_tracking``).
These tests pin the algorithm so a change in one caller can't silently
break the other — cross-inventory parity is a HARD RULE for this project.
"""

from __future__ import annotations

from backend.app.utils.tray_split import compute_tray_split_grams


class TestComputeTraySplitGrams:
    """Segment-attribution algorithm — gcode preferred, linear fallback, equal split."""

    def test_empty_tray_changes_returns_empty(self):
        assert (
            compute_tray_split_grams(
                tray_changes=[],
                total_weight=100.0,
                slot_id=1,
                layer_usage=None,
                density=1.24,
                diameter=1.75,
                total_layers=200,
                last_layer_num=200,
            )
            == []
        )

    def test_single_segment_charges_everything_to_that_tray(self):
        segments = compute_tray_split_grams(
            tray_changes=[(0, 0)],
            total_weight=72.56,
            slot_id=1,
            layer_usage=None,
            density=1.24,
            diameter=1.75,
            total_layers=100,
            last_layer_num=100,
        )
        assert segments == [(0, 0, 72.56)]

    def test_two_segments_linear_split_by_layer_ratio(self):
        # Runout at layer 37 of 100 total; no gcode available → linear.
        # Segment 0 (tray 0, layers 0-37) = 100 * 37/100 = 37g
        # Segment 1 (tray 1, layers 37-end) = 100 - 37 = 63g (remainder)
        segments = compute_tray_split_grams(
            tray_changes=[(0, 0), (1, 37)],
            total_weight=100.0,
            slot_id=1,
            layer_usage=None,
            density=1.24,
            diameter=1.75,
            total_layers=100,
            last_layer_num=100,
        )
        assert segments == [(0, 0, 37.0), (1, 1, 63.0)]

    def test_two_segments_gcode_preferred_over_linear(self):
        # layer_usage stores mm of filament extruded per (layer, filament_id).
        # Values are cumulative-per-key inside get_cumulative_usage_at_layer.
        # 20 layers, filament_id=0 (slot_id=1 → filament_id 0):
        #   layer 10 → 100mm cumulative
        #   layer 20 → 300mm cumulative
        # tray change at layer 10 → seg 0 spans layers 0-10 (mm 0 → 100),
        #                            seg 1 spans layers 10-end.
        # mm_to_grams(100, 1.75, 1.24) ≈ 0.298g; last segment absorbs the rest.
        layer_usage = {
            5: {0: 50.0},
            10: {0: 100.0},
            15: {0: 200.0},
            20: {0: 300.0},
        }
        segments = compute_tray_split_grams(
            tray_changes=[(0, 0), (1, 10)],
            total_weight=1.0,  # sentinel — we assert the seg1 remainder
            slot_id=1,
            layer_usage=layer_usage,
            density=1.24,
            diameter=1.75,
            total_layers=20,
            last_layer_num=20,
        )
        # Seg 0 charged from gcode delta (mm 0 → 100).
        # Seg 1 gets total_weight - seg0 as remainder.
        assert segments[0][0] == 0
        assert segments[0][1] == 0  # tray 0
        assert segments[0][2] > 0  # non-zero gcode contribution
        assert segments[1][0] == 1
        assert segments[1][1] == 1  # tray 1
        # Sum equals the input total by construction (last segment absorbs).
        assert round(segments[0][2] + segments[1][2], 6) == 1.0

    def test_three_segments_last_absorbs_rounding_drift(self):
        # 100g over three segments at layers 30 and 60 of 90; linear fallback.
        # Seg 0: 100 * 30/90 = 33.3333...
        # Seg 1: 100 * 30/90 = 33.3333...
        # Seg 2: remainder = 100 - 66.6666... = 33.3333... — exact by construction
        segments = compute_tray_split_grams(
            tray_changes=[(0, 0), (1, 30), (2, 60)],
            total_weight=100.0,
            slot_id=1,
            layer_usage=None,
            density=1.24,
            diameter=1.75,
            total_layers=90,
            last_layer_num=90,
        )
        assert len(segments) == 3
        assert round(sum(g for _, _, g in segments), 6) == 100.0
        assert segments[0][1] == 0
        assert segments[1][1] == 1
        assert segments[2][1] == 2

    def test_no_layer_info_at_all_falls_to_equal_split(self):
        # Denominator 0 → last-resort equal-split; last segment absorbs remainder.
        segments = compute_tray_split_grams(
            tray_changes=[(0, 0), (1, 50)],
            total_weight=90.0,
            slot_id=1,
            layer_usage=None,
            density=1.24,
            diameter=1.75,
            total_layers=0,
            last_layer_num=0,
        )
        # 90g / 2 = 45g each; sum still 90 by remainder mechanic.
        assert segments == [(0, 0, 45.0), (1, 1, 45.0)]

    def test_last_layer_num_used_when_total_layers_zero(self):
        # P1S firmware-reset scenario: total_layers=0 at completion, but the
        # captured last_layer_num survives. Should give the same linear split
        # as if total_layers had held its value (#1771 cascade).
        segments_captured = compute_tray_split_grams(
            tray_changes=[(0, 0), (1, 30)],
            total_weight=100.0,
            slot_id=1,
            layer_usage=None,
            density=1.24,
            diameter=1.75,
            total_layers=0,
            last_layer_num=100,
        )
        segments_normal = compute_tray_split_grams(
            tray_changes=[(0, 0), (1, 30)],
            total_weight=100.0,
            slot_id=1,
            layer_usage=None,
            density=1.24,
            diameter=1.75,
            total_layers=100,
            last_layer_num=100,
        )
        assert segments_captured == segments_normal

    def test_slot_id_maps_to_zero_based_filament_id_in_gcode(self):
        # slot_id 2 → filament_id 1 in layer_usage. If we mistakenly used
        # slot_id as-is, we'd read filament_id 2 which is absent → 0mm delta
        # → seg 0 gets 0, seg 1 (remainder) gets the whole total. Guard
        # against that regression.
        layer_usage = {
            5: {0: 0.0, 1: 40.0},
            10: {0: 0.0, 1: 80.0},
            20: {0: 0.0, 1: 160.0},
        }
        segments = compute_tray_split_grams(
            tray_changes=[(0, 0), (1, 10)],
            total_weight=1.0,
            slot_id=2,
            layer_usage=layer_usage,
            density=1.24,
            diameter=1.75,
            total_layers=20,
            last_layer_num=20,
        )
        # Seg 0 gcode delta on filament_id=1 is non-zero → not 0g.
        assert segments[0][2] > 0


class TestSlotTrayChanges3650:
    """Per-slot tray changes for a multi-filament print with an AMS backup switch (#3650).

    Slot 1 (black) is mapped to tray 3, slot 2 (yellow) to tray 0. Tray 2 holds
    the same black filament and is the AMS backup for tray 3; it is in no slot
    of the mapping. A colour change loads a mapped tray, a backup switch loads
    tray 2.
    """

    MAPPING = [3, 0]
    SLOTS = [1, 2]
    FIL = {3: ("PLA", "000000"), 2: ("PLA", "000000"), 0: ("PLA", "FFFF00")}

    def _run(self, log, mapping=None, slots=None, fil=None):
        from backend.app.utils.tray_split import slot_tray_changes

        return slot_tray_changes(
            log,
            self.MAPPING if mapping is None else mapping,
            self.SLOTS if slots is None else slots,
            self.FIL if fil is None else fil,
        )

    def test_runout_moves_slot_to_backup(self):
        # Colour changes back to slot 1 after the runout load tray 2 directly,
        # so tray 2 stays with slot 1 even though tray 0 came right before it.
        log = [(3, 0), (0, 10), (3, 11), (2, 40), (0, 41), (2, 42)]
        assert self._run(log) == ({1: [(3, 0), (2, 40)]}, None)

    def test_colour_changes_only_no_split(self):
        assert self._run([(3, 0), (0, 10), (3, 11), (0, 20)]) == ({}, None)

    def test_second_runout_chains(self):
        fil = {**self.FIL, 1: ("PLA", "000000")}
        log = [(3, 0), (2, 40), (0, 41), (2, 42), (1, 70)]
        assert self._run(log, fil=fil) == ({1: [(3, 0), (2, 40), (1, 70)]}, None)

    def test_log_entries_as_lists_are_accepted(self):
        # Restored from the database the log comes back as lists, not tuples.
        assert self._run([[3, 0], [0, 10], [3, 11], [2, 40]]) == ({1: [(3, 0), (2, 40)]}, None)

    def test_leftover_tray_at_layer_zero_ignored(self):
        # Tray 9 was still loaded from the previous print when this one started.
        log = [(9, 0), (3, 0), (0, 5), (3, 6), (2, 40)]
        assert self._run(log) == ({1: [(3, 0), (2, 40)]}, None)

    def test_leftover_tray_past_layer_zero_falls_back(self):
        per_slot, reason = self._run([(9, 0), (3, 4), (2, 40)])
        assert per_slot == {}
        assert reason

    def test_shared_tray_ambiguous_falls_back(self):
        per_slot, reason = self._run([(3, 0), (2, 40)], mapping=[3, 3])
        assert per_slot == {}
        assert reason

    def test_material_mismatch_falls_back(self):
        per_slot, reason = self._run([(3, 0), (0, 10), (3, 11), (2, 40)], fil={**self.FIL, 2: ("PETG", "000000")})
        assert per_slot == {}
        assert reason

    def test_colour_mismatch_falls_back(self):
        per_slot, reason = self._run([(3, 0), (0, 10), (3, 11), (2, 40)], fil={**self.FIL, 2: ("PLA", "FF0000")})
        assert per_slot == {}
        assert reason

    def test_unknown_filament_does_not_block(self):
        log = [(3, 0), (0, 10), (3, 11), (2, 40), (0, 41), (2, 42)]
        assert self._run(log, fil={**self.FIL, 2: ("", "")}) == ({1: [(3, 0), (2, 40)]}, None)
        assert self._run(log, fil={}) == ({1: [(3, 0), (2, 40)]}, None)

    def test_no_mapping_or_single_slot_does_nothing(self):
        assert self._run([(3, 0), (2, 40)], mapping=[]) == ({}, None)
        assert self._run([(3, 0), (2, 40)], slots=[1]) == ({}, None)

    def test_tray_filaments_from_raw_normalises(self):
        from backend.app.utils.tray_ids import TrayLayout
        from backend.app.utils.tray_split import tray_filaments_from_raw

        raw = {
            "ams": [
                {
                    "id": "0",
                    "tray": [
                        {"id": "0", "tray_type": "pla", "tray_color": "1A1A1AFF"},
                        {"id": "1", "tray_type": "", "tray_color": ""},
                        "garbage",
                        {"id": "x", "tray_type": "PLA"},
                    ],
                },
                "garbage",
            ],
            "vt_tray": [{"id": "254", "tray_type": "PETG", "tray_color": "#00ff00"}],
        }
        layout = TrayLayout.from_state(type("S", (), {"raw_data": raw})())
        fil = tray_filaments_from_raw(raw, layout)
        assert fil[0] == ("PLA", "1A1A1A")
        assert fil[1] == ("", "")
        assert fil[254] == ("PETG", "00FF00")
        assert tray_filaments_from_raw(None, layout) == {}

    def test_used_slot_without_a_tray_falls_back(self):
        # Review: slot 2 fed from the external spool, flat mapping -1 (#3166). Its
        # tray is then outside the mapping without being a backup; reading it as
        # one spread slot 1's grams over the external spool.
        fil = {0: ("PLA", "000000"), 254: ("", "")}
        log = [(0, 0), (254, 5), (0, 6), (254, 7)]
        per_slot, reason = self._run(log, mapping=[0, -1], fil=fil)
        assert per_slot == {}
        assert reason
        per_slot, reason = self._run(log, mapping=[0], fil=fil)
        assert per_slot == {}
        assert reason

    def test_used_slot_without_a_tray_single_switch_falls_back(self):
        # Slot 2 fed from the external spool (-1) and the log shows one load of
        # it: no alternation to catch, so only the slot's missing tray keeps
        # 254 from reading as slot 1's backup.
        fil = {0: ("PLA", "000000"), 254: ("", "")}
        per_slot, reason = self._run([(0, 0), (254, 5)], mapping=[0, -1], fil=fil)
        assert per_slot == {}
        assert reason

    def test_alternating_between_mapped_and_backup_falls_back(self):
        # Review: slot 1's tray 0 ran out while loading, so the printer went from
        # slot 2's tray 1 straight to backup tray 2. Read as slot 2's backup, slot 2
        # then alternates 1, 2, 1, 2: a backup chain never does that.
        fil = {0: ("PLA", "000000"), 1: ("PLA", "000000"), 2: ("PLA", "000000")}
        per_slot, reason = self._run([(0, 0), (1, 5), (2, 6), (1, 7), (2, 8)], mapping=[0, 1], fil=fil)
        assert per_slot == {}
        assert reason

    def test_mapped_tray_coming_back_once_still_splits(self):
        # The emptied tray refilled and loaded again: one return is a real change.
        log = [(3, 0), (0, 10), (3, 11), (2, 40), (0, 41), (2, 42), (3, 80)]
        assert self._run(log) == ({1: [(3, 0), (2, 40), (3, 80)]}, None)
