"""Bambu Studio's global tray numbering (utils.tray_ids), for every AMS kind.

Studio (DevFilaSystem::GetTrayIndexMap, DevAmsTray::get_nozzle_addr_id):
regular AMS ams_id*4+slot, A2L AMS Lite 24+slot, AMS 3H 28+slot, AMS-B
37+slot, AMS-HT its unit id, external inputs their own vir_slot id -- 254/255,
and 247-255 on a printer with a direct-connected nozzle rack, where input v
feeds hotend 255-v.
"""

import pytest

from backend.app.utils.tray_ids import (
    AmsType,
    TrayLayout,
    ams_type_from_info,
    decode_wire_slot,
    external_hotend,
    external_slot_index,
    external_tray_id,
    global_tray_id,
    is_external_tray,
    is_rack_ams,
    legacy_tray_index,
    mapped_trays_with_slot,
    tray_flag_bit,
    unit_ams_type,
    unit_label,
)

# The H3C's AMS 3H as the printer reports it (info captured 2026-11-06).
H3C_UNITS = [{"id": "32", "info": "240842026", "tray": [{"id": str(k)} for k in range(9)]}]
H3C_VT = [{"id": str(v)} for v in range(247, 256)]
H3C = TrayLayout.from_units(H3C_UNITS, H3C_VT)


class TestAmsType:
    @pytest.mark.parametrize(
        ("info", "kind"),
        [
            ("10001001", AmsType.AMS),
            ("2002", AmsType.AMS_LITE),
            ("10003", AmsType.N3F),
            ("1104", AmsType.N3S),
            ("5", AmsType.AMS_LITE_MIXED),
            ("240842026", AmsType.ZMULTI_A),
            ("240002006", AmsType.ZMULTI_A),
            ("7", AmsType.ZMULTI_B),
        ],
    )
    def test_low_four_bits_of_the_hex_info(self, info, kind):
        assert ams_type_from_info(info) == kind

    @pytest.mark.parametrize("info", [None, "", "zz", True, 3.5])
    def test_unreadable_info_is_none(self, info):
        assert ams_type_from_info(info) is None

    def test_type_told_from_the_id_without_info(self):
        assert unit_ams_type(0) == AmsType.AMS
        assert unit_ams_type(3) == AmsType.AMS
        assert unit_ams_type(128) == AmsType.N3S
        assert unit_ams_type(135) == AmsType.N3S
        assert unit_ams_type(32) == AmsType.ZMULTI_A

    def test_info_outranks_the_id_except_for_an_ams_ht(self):
        assert unit_ams_type(32, "7") == AmsType.ZMULTI_B
        assert unit_ams_type(128, "1") == AmsType.N3S

    def test_rack_types(self):
        assert is_rack_ams(AmsType.ZMULTI_A) and is_rack_ams(AmsType.ZMULTI_B)
        assert not any(is_rack_ams(k) for k in (AmsType.AMS, AmsType.N3F, AmsType.N3S, AmsType.AMS_LITE_MIXED))


class TestGlobalTrayId:
    @pytest.mark.parametrize(
        ("ams_id", "slot", "kind", "expected"),
        [
            (0, 0, AmsType.AMS, 0),
            (1, 2, AmsType.AMS, 6),
            (3, 3, AmsType.N3F, 15),
            (6, 1, AmsType.AMS_LITE, 25),  # the A2L's AMS Lite, stored as unit 6
            (16, 1, AmsType.AMS_LITE_MIXED, 25),  # ...and Studio's own numbering of its unit 16
            (32, 0, AmsType.ZMULTI_A, 28),
            (32, 8, AmsType.ZMULTI_A, 36),
            (33, 0, AmsType.ZMULTI_B, 37),
            (33, 4, AmsType.ZMULTI_B, 41),
            (128, 0, AmsType.N3S, 128),
            (135, 0, AmsType.N3S, 135),
        ],
    )
    def test_every_ams_kind(self, ams_id, slot, kind, expected):
        assert global_tray_id(ams_id, slot, kind) == expected

    def test_without_a_type_regular_ht_and_the_ams3h_by_their_ids(self):
        assert [global_tray_id(1, s) for s in range(4)] == [4, 5, 6, 7]
        assert global_tray_id(129, 0) == 129
        assert [global_tray_id(32, s) for s in range(9)] == list(range(28, 37))

    def test_the_ams3h_no_longer_lands_in_the_ams_ht_range(self):
        ams3h = {global_tray_id(32, s) for s in range(9)}
        assert ams3h.isdisjoint(range(128, 136))
        assert ams3h.isdisjoint(range(0, 16))


class TestLayout:
    def test_h3c_is_a_rack_printer(self):
        assert H3C.rack
        assert H3C.rack_units == ((32, AmsType.ZMULTI_A),)
        assert H3C.ams_type(32) == AmsType.ZMULTI_A

    def test_both_directions_on_the_h3c(self):
        for slot in range(9):
            tray = H3C.global_tray_id(32, slot)
            assert tray == 28 + slot
            assert H3C.slot_key(tray) == (32, slot)
            assert H3C.tray_from_slot_key(32, slot) == tray

    def test_both_directions_on_a_regular_printer(self):
        layout = TrayLayout.from_units([{"id": "0", "info": "1001"}, {"id": "1", "info": "1001"}, {"id": "128"}])
        assert not layout.rack
        for ams_id in (0, 1):
            for slot in range(4):
                tray = layout.global_tray_id(ams_id, slot)
                assert tray == ams_id * 4 + slot
                assert layout.slot_key(tray) == (ams_id, slot)
                assert layout.tray_from_slot_key(ams_id, slot) == tray
        assert layout.global_tray_id(128, 0) == 128
        assert layout.slot_key(128) == (128, 0)

    def test_without_live_data_both_directions_agree(self):
        # No AMS data at all: 28-36 are the AMS 3H's (unit 32) and 247-253 the
        # per-hotend inputs, the same way round as tray_from_slot_key numbers them.
        empty = TrayLayout()
        for slot in range(9):
            assert empty.tray_from_slot_key(32, slot) == 28 + slot
            assert empty.slot_key(28 + slot) == (32, slot)
        for index in range(9):
            assert empty.slot_key(empty.tray_from_slot_key(255, index)) == (255, index)

    def test_a_listed_regular_unit_7_keeps_28_to_31(self):
        layout = TrayLayout.from_units([{"id": "7", "info": "1001"}])
        assert layout.slot_key(28) == (7, 0)
        assert layout.slot_key(31) == (7, 3)
        assert layout.tray_from_slot_key(7, 2) == 30

    def test_ams_b_by_its_info(self):
        layout = TrayLayout.from_units([{"id": "40", "info": "7"}])
        assert layout.rack
        assert layout.global_tray_id(40, 2) == 39
        assert layout.slot_key(39) == (40, 2)

    def test_api_units_with_an_explicit_type(self):
        layout = TrayLayout.from_units([{"id": 32, "ams_type": 6}])
        assert layout.global_tray_id(32, 4) == 32
        assert layout.slot_key(32) == (32, 4)

    def test_raw_tray_now_of_the_rack(self):
        assert [H3C.raw_slot_to_global(k) for k in range(9)] == list(range(28, 37))
        assert H3C.raw_slot_to_global(255) is None
        assert TrayLayout.from_units([{"id": "0"}]).raw_slot_to_global(2) is None

    def test_loaded_trays(self):
        regular = TrayLayout.from_units([{"id": "0"}])
        for tray in (0, 15, 24, 27, 128, 135, 254):
            assert regular.is_loaded_tray(tray)
        for tray in (16, 28, 250, 255):
            assert not regular.is_loaded_tray(tray)
        assert H3C.is_loaded_tray(28) and H3C.is_loaded_tray(36) and H3C.is_loaded_tray(247)
        assert not H3C.is_loaded_tray(255)

    def test_layout_from_a_state(self):
        class _State:
            raw_data = {"ams": H3C_UNITS, "vt_tray": H3C_VT}

        assert TrayLayout.from_state(_State()) == H3C
        assert TrayLayout.from_state(None) == TrayLayout()

    def test_an_ams3h_model_without_ams_data_gets_its_ams3h(self):
        class _NoAms:
            raw_data: dict = {}

        assert TrayLayout.from_state(None, ams3h_model=True) == TrayLayout.ams3h()
        assert TrayLayout.from_state(_NoAms(), ams3h_model=True).slot_key(250) == (255, 5)
        assert TrayLayout.ams3h().is_external(250)

        # Live units always win over the model.
        class _Regular:
            raw_data = {"ams": [{"id": "0", "info": "1001"}]}

        assert TrayLayout.from_state(_Regular(), ams3h_model=True).rack_units == ()


class TestWireSlot:
    """(ams_id << 8) | slot, as print.mapping and the snow fields carry a slot."""

    def test_regular_ams_ht_and_external(self):
        assert decode_wire_slot((1 << 8) | 2) == 6
        assert decode_wire_slot((0 << 8) | 0x13) == 3  # slot bits & 3
        assert decode_wire_slot(129 << 8) == 129
        assert decode_wire_slot((254 << 8) | 0) == 254
        assert decode_wire_slot((255 << 8) | 0) == 254
        assert decode_wire_slot((254 << 8) | 255) == 255
        assert decode_wire_slot(0xFFFF) is None
        assert decode_wire_slot(-1) is None
        assert decode_wire_slot((16 << 8) | 1) is None
        assert decode_wire_slot("258") == 6

    def test_ams3h_slots_with_and_without_a_layout(self):
        assert [decode_wire_slot((32 << 8) | k, H3C) for k in range(9)] == list(range(28, 37))
        assert decode_wire_slot((32 << 8) | 6) == 34
        assert decode_wire_slot((32 << 8) | 9, H3C) is None

    def test_a_rack_printer_keeps_its_external_inputs(self):
        # On a rack printer every input 247-255 is its own hotend's.
        assert decode_wire_slot((250 << 8) | 0, H3C) == 250
        assert decode_wire_slot((255 << 8) | 0, H3C) == 255
        assert decode_wire_slot((254 << 8) | 0, H3C) == 254
        assert decode_wire_slot((250 << 8) | 0) is None

    def test_trays_with_a_local_slot(self):
        mapping = [(0 << 8) | 1, (1 << 8) | 1, (1 << 8) | 2, 128 << 8, 254 << 8, 0xFFFF]
        assert mapped_trays_with_slot(mapping, 1) == {1: 0, 5: 1}
        assert mapped_trays_with_slot(mapping, 0) == {128: 128}
        assert mapped_trays_with_slot(None, 0) == {}

    def test_legacy_index_is_the_flat_four_per_unit_number(self):
        assert legacy_tray_index(1, 2) == 6
        assert legacy_tray_index(16, 3) == 67  # the A2L AMS Lite's physical tray
        assert legacy_tray_index(255, 1) == 1021


class TestExternals:
    def test_254_255_are_external_everywhere(self):
        for tray in (254, 255):
            assert is_external_tray(tray)
            assert is_external_tray(tray, rack=True)

    def test_247_to_253_only_on_a_rack_printer(self):
        for tray in range(247, 254):
            assert not is_external_tray(tray)
            assert is_external_tray(tray, rack=True)
            assert H3C.is_external(tray)
        assert not TrayLayout.from_units([{"id": "0"}], [{"id": "254"}, {"id": "255"}]).is_external(250)

    def test_ams_slots_are_not_external(self):
        for tray in (0, 15, 28, 36, 128):
            assert not is_external_tray(tray, rack=True)

    def test_an_input_feeds_hotend_255_minus_its_id(self):
        assert [external_hotend(v) for v in range(255, 246, -1)] == list(range(9))

    def test_stored_under_ams_255(self):
        assert external_slot_index(254) == 0 and external_slot_index(255) == 1
        assert [external_slot_index(v) for v in range(247, 254)] == list(range(2, 9))
        for v in range(247, 256):
            assert external_tray_id(external_slot_index(v)) == v
            assert H3C.slot_key(v) == (255, external_slot_index(v))
            assert H3C.tray_from_slot_key(255, external_slot_index(v)) == v

    def test_a_reported_input_247_to_253_makes_a_rack_printer(self):
        assert TrayLayout.from_units([], H3C_VT).rack
        assert not TrayLayout.from_units([], [{"id": "254"}, {"id": "255"}]).rack

    def test_a_regular_printer_keeps_its_external_slot_keys(self):
        layout = TrayLayout.from_units([{"id": "0"}], [{"id": "254"}])
        assert layout.slot_key(254) == (255, 0)
        assert layout.slot_key(255) == (255, 1)
        # 247-253 are only ever external inputs, so they keep their stored key everywhere.
        assert layout.slot_key(250) == (255, 5)
        assert not layout.is_external(250)


class TestTrayBitsAndLabels:
    def test_tray_flag_bits(self):
        assert tray_flag_bit(1, 2) == 6
        assert tray_flag_bit(128, 0) == 16
        assert tray_flag_bit(130, 0) == 18
        assert tray_flag_bit(6, 1, AmsType.AMS_LITE_MIXED) == 25
        assert [tray_flag_bit(32, k, AmsType.ZMULTI_A) for k in range(9)] == list(range(28, 37))
        assert tray_flag_bit(40, 0, AmsType.AMS) is None

    def test_unit_labels(self):
        assert unit_label(0) == "AMS-A"
        assert unit_label(129) == "HT-B"
        assert unit_label(32) == "AMS 3H"


def test_a_unit_listed_twice_counts_once():
    # A push's units are combined with the ones already known: unit 32 twice is one AMS 3H.
    layout = TrayLayout.from_units([{"id": "32", "info": "240002006"}, {"id": "32", "info": "240002006"}])
    assert layout.rack_units == ((32, AmsType.ZMULTI_A),)
    assert layout.raw_slot_to_global(4) == 32
