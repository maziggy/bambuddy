"""H3C "AMS 3H" (model N10): one nine-slot rack AMS, numbered as Bambu Studio does.

The H3C reports its AMS 3H as ONE unit (id 32, AMS type 6 = Studio's ZMULTI_A)
with trays 0-8, tray_now as the raw slot, slot k of the tray bit masks at bit
28+k, and (32 << 8) | k in `mapping` / extruder `snow`. Bambuddy keeps the
unit as it is and gives its slots Bambu Studio's global tray ids 28-36
(utils.tray_ids): tray_now 6 is global 34, a print maps slot k as 28+k with
ams_mapping2 {ams_id: 32, slot_id: k}, and the slot rows are (32, k).
Commands keep the firmware's own {ams_id: 32, slot_id: k}.

``FRAME`` is trimmed from a live H3C capture (firmware 01.01.03.00, AMS 3H
02.00.32.82) taken mid-print on 2026-11-06: tray_now "6", slot 7 empty, and
the hotend bound to slot 6 mounted on the toolhead (nozzle id 0, addr_id 6).
"""

import asyncio
import copy
import json
from unittest.mock import MagicMock

from backend.app.services.bambu_mqtt import (
    BambuMQTTClient,
    ams3h_drying_cooling_temp,
    resolve_external_spools_in_mapping,
)
from backend.app.services.printer_manager import ams_units_to_dicts, resolve_expected_tray
from backend.app.utils.tray_ids import AmsType, TrayLayout

FRAME = {
    "command": "push_status",
    "ams": {
        "tray_now": "6",
        "tray_pre": "6",
        "tray_tar": "6",
        "tray_exist_bits": "17f0000000",
        "tray_is_bbl_bits": "17f0000000",
        "tray_read_done_bits": "17f0000000",
        "tray_reading_bits": "0",
        "ams_exist_bits": "2000",
        "csl": [8193],
        "insert_flag": True,
        "power_on_flag": False,
        "version": 6369,
        "ams": [
            {
                "id": "32",
                "info": "240842026",
                "tray": [
                    {
                        "id": "0",
                        "state": 27,
                        "tray_type": "PLA",
                        "tray_color": "DE4343FF",
                        "remain": -1,
                        "tray_info_idx": "GFL99",
                        "tag_uid": "0000000000000000",
                    },
                    {
                        "id": "1",
                        "state": 27,
                        "tray_type": "PLA",
                        "tray_color": "FFFFFFFF",
                        "remain": -1,
                        "tray_info_idx": "GFL99",
                        "tag_uid": "0000000000000000",
                    },
                    {
                        "id": "2",
                        "state": 27,
                        "tray_type": "PLA",
                        "tray_color": "757575FF",
                        "remain": -1,
                        "tray_info_idx": "GFL99",
                        "tag_uid": "0000000000000000",
                    },
                    {
                        "id": "3",
                        "state": 27,
                        "tray_type": "PLA",
                        "tray_color": "000000FF",
                        "remain": -1,
                        "tray_info_idx": "GFL99",
                        "tag_uid": "0000000000000000",
                    },
                    {
                        "id": "4",
                        "state": 27,
                        "tray_type": "PLA",
                        "tray_color": "0178BFFF",
                        "remain": -1,
                        "tray_info_idx": "GFL99",
                        "tag_uid": "0000000000000000",
                    },
                    {
                        "id": "5",
                        "state": 27,
                        "tray_type": "PLA",
                        "tray_color": "AE96D4FF",
                        "remain": -1,
                        "tray_info_idx": "GFL99",
                        "tag_uid": "0000000000000000",
                    },
                    {
                        "id": "6",
                        "state": 27,
                        "tray_type": "PLA",
                        "tray_color": "F7D959FF",
                        "remain": -1,
                        "tray_info_idx": "GFL99",
                        "tag_uid": "0000000000000000",
                    },
                    {"id": "7", "state": 160},
                    {
                        "id": "8",
                        "state": 27,
                        "tray_type": "PLA",
                        "tray_color": "61C680FF",
                        "remain": -1,
                        "tray_info_idx": "GFL99",
                        "tag_uid": "0000000000000000",
                    },
                ],
            }
        ],
    },
    "mapping": [8193, 8195, 8198, 8192, 8200, 8196, 8192, 8194, 8197],
    "device": {
        "extruder": {
            "state": 1,
            "info": [{"id": 0, "snow": 8198, "spre": 8198, "star": 8198, "hnow": 0, "hpre": 0, "htar": 0}],
        },
        "nozzle": {
            "hc": 511,
            "info": [
                {
                    "id": 16,
                    "addr_id": 0,
                    "snow": 8192,
                    "stat": 168,
                    "type": "HS01",
                    "diameter": 0.4,
                    "color_m": "DE4343FF",
                    "fila_id": "GFL99",
                    "tm": 350,
                    "wear": 0.0,
                },
                {
                    "id": 17,
                    "addr_id": 1,
                    "snow": 8193,
                    "stat": 176,
                    "type": "HS01",
                    "diameter": 0.4,
                    "color_m": "FFFFFFFF",
                    "fila_id": "GFL99",
                    "tm": 350,
                    "wear": 0.0,
                },
                {
                    "id": 18,
                    "addr_id": 2,
                    "snow": 8194,
                    "stat": 184,
                    "type": "HS01",
                    "diameter": 0.4,
                    "color_m": "757575FF",
                    "fila_id": "GFL99",
                    "tm": 350,
                    "wear": 0.0,
                },
                {
                    "id": 19,
                    "addr_id": 3,
                    "snow": 8195,
                    "stat": 184,
                    "type": "HS01",
                    "diameter": 0.4,
                    "color_m": "000000FF",
                    "fila_id": "GFL99",
                    "tm": 350,
                    "wear": 0.0,
                },
                {
                    "id": 20,
                    "addr_id": 4,
                    "snow": 8196,
                    "stat": 184,
                    "type": "HS01",
                    "diameter": 0.4,
                    "color_m": "0178BFFF",
                    "fila_id": "GFL99",
                    "tm": 350,
                    "wear": 0.0,
                },
                {
                    "id": 21,
                    "addr_id": 5,
                    "snow": 8197,
                    "stat": 160,
                    "type": "HS01",
                    "diameter": 0.4,
                    "color_m": "AE96D4FF",
                    "fila_id": "GFL99",
                    "tm": 350,
                    "wear": 0.0,
                },
                {
                    "id": 0,
                    "addr_id": 6,
                    "snow": 8198,
                    "stat": 168,
                    "type": "HS01",
                    "diameter": 0.4,
                    "color_m": "F7D959FF",
                    "fila_id": "GFL99",
                    "tm": 350,
                    "wear": 0.0,
                },
                {
                    "id": 23,
                    "addr_id": 7,
                    "snow": 63488,
                    "stat": 1,
                    "type": "HT01",
                    "diameter": 0.4,
                    "color_m": "00000000",
                    "fila_id": "",
                    "tm": 350,
                    "wear": 0.0,
                },
                {
                    "id": 24,
                    "addr_id": 8,
                    "snow": 8200,
                    "stat": 160,
                    "type": "HS01",
                    "diameter": 0.4,
                    "color_m": "61C680FF",
                    "fila_id": "GFL99",
                    "tm": 350,
                    "wear": 0.0,
                },
            ],
        },
    },
}

# Slot colours of FRAME, physical slot 0-8 (7 is empty).
COLOURS = ["DE4343FF", "FFFFFFFF", "757575FF", "000000FF", "0178BFFF", "AE96D4FF", "F7D959FF", None, "61C680FF"]


def _frame() -> dict:
    return copy.deepcopy(FRAME)


def _client(model: str = "H3C") -> BambuMQTTClient:
    return BambuMQTTClient(ip_address="10.0.0.1", serial_number="H3C", access_code="c", model=model)


def _wired(client: BambuMQTTClient) -> BambuMQTTClient:
    client._client = MagicMock()
    client.state.connected = True
    return client


def _ingested(model: str = "H3C", frame: dict | None = None) -> BambuMQTTClient:
    client = _client(model)
    client._process_message({"print": frame if frame is not None else _frame()})
    return client


def _last_payload(client: BambuMQTTClient) -> dict:
    return json.loads(client._client.publish.call_args[0][1])["print"]


def _unit(client: BambuMQTTClient) -> dict:
    (unit,) = client.state.raw_data["ams"]
    return unit


def _trays_by_id(unit: dict) -> dict[int, dict]:
    return {int(t["id"]): t for t in unit.get("tray", [])}


class TestIngest:
    def test_unit_32_stays_one_unit_with_its_nine_trays(self):
        client = _ingested()
        unit = _unit(client)
        assert unit["id"] == "32"
        trays = _trays_by_id(unit)
        assert sorted(trays) == list(range(9))
        for slot, colour in enumerate(COLOURS):
            assert trays[slot].get("tray_color") == colour

    def test_the_layout_names_the_rack_ams(self):
        layout = _ingested().tray_layout
        assert layout.rack_units == ((32, AmsType.ZMULTI_A),)
        assert [layout.global_tray_id(32, k) for k in range(9)] == list(range(28, 37))

    def test_tray_now_is_the_global_tray_28_plus_slot(self):
        assert _ingested().state.tray_now == 34  # FRAME: raw slot 6
        for slot in ("0", "2", "3", "8"):
            frame = _frame()
            frame["ams"]["tray_now"] = slot
            assert _ingested(frame=frame).state.tray_now == 28 + int(slot)

    def test_nothing_on_the_toolhead_stays_255(self):
        frame = _frame()
        frame["ams"]["tray_now"] = "255"
        assert _ingested(frame=frame).state.tray_now == 255

    def test_a_partial_push_is_globalised_too(self):
        client = _ingested()
        client._process_message({"print": {"ams": {"tray_now": "2"}}})
        assert client.state.tray_now == 30

    def test_a_partial_state_27_push_keeps_the_slots_spool(self):
        # The AMS 3H reports a loaded slot as state 27. The "state != 11 is not
        # loaded" rule for partial {id, state} pushes (#784) must not empty it,
        # as it must not for an AMS-HT (#2594).
        client = _wired(_ingested())
        client._process_message(
            {"print": {"command": "push_status", "ams": {"ams": [{"id": "32", "tray": [{"id": "3", "state": 27}]}]}}}
        )
        assert _trays_by_id(_unit(client))[3]["tray_type"] == "PLA"
        assert client.ams_load_filament(31)

    def test_every_full_push_is_globalised_not_just_the_first(self):
        # The H3C sends the unit list with every push. The push's units plus the
        # ones already known used to list unit 32 twice, which read as two rack
        # AMSes, so from the second push on tray_now stayed the raw slot (seen live).
        client = _ingested()
        for slot in ("4", "0", "8"):
            frame = _frame()
            frame["ams"]["tray_now"] = slot
            client._process_message({"print": frame})
            assert client.state.tray_now == 28 + int(slot)

    def test_loaded_trays_are_the_rack_slots_and_inputs(self):
        layout = _ingested().tray_layout
        assert layout.is_loaded_tray(28) and layout.is_loaded_tray(36)
        assert layout.is_loaded_tray(250)  # a per-hotend external input
        assert not layout.is_loaded_tray(255) and not layout.is_loaded_tray(37)
        regular = _client(model="X1C").tray_layout
        assert not regular.is_loaded_tray(30) and not regular.is_loaded_tray(250)

    def test_tray_exist_bits_mark_only_slot_7_empty(self):
        # "17f0000000": slots 0-6 and 8 at bit 28+k, Studio's get_tray_flag_bit
        trays = _trays_by_id(_unit(_ingested()))
        for slot in range(9):
            assert trays[slot].get("exists") is (slot != 7), slot

    def test_one_extruder_no_ams_extruder_map(self):
        client = _ingested()
        assert client.state.ams_extruder_map == {}
        assert client._is_dual_nozzle is False

    def test_extruder_slot_points_at_unit_32(self):
        slots = _ingested().state.extruder_slots
        loaded = next(iter(slots.values()))
        assert (loaded.ams_id, loaded.slot_id) == (32, 6)

    def test_status_unit_carries_its_ams_type(self):
        (unit,) = ams_units_to_dicts(_ingested().state.raw_data["ams"], lambda *a: None)
        assert (unit["id"], unit["ams_type"], len(unit["tray"])) == (32, 6, 9)
        assert unit["is_ams_ht"] is False

    def test_nozzle_rack_keeps_home_position(self):
        rack = _ingested().state.nozzle_rack
        assert len(rack) == 9
        mounted = [n for n in rack if n["id"] == 0]
        assert len(mounted) == 1 and mounted[0]["addr_id"] == 6
        assert {n["addr_id"] for n in rack} == set(range(9))

    def test_version_module_n10a_reaches_the_unit(self):
        client = _ingested()
        client._process_message(
            {"info": {"command": "get_version", "module": [{"name": "n10a", "sw_ver": "02.00.32.82", "sn": "SN3H"}]}}
        )
        unit = _unit(client)
        assert (unit["sw_ver"], unit["module_type"], unit["sn"]) == ("02.00.32.82", "n10a", "SN3H")

    def test_runout_slots_are_global_tray_ids(self):
        units = ams_units_to_dicts(_ingested().state.raw_data["ams"], lambda *a: None)
        layout = TrayLayout.from_units(units)
        assert resolve_expected_tray(6, [(32, False)], None, tray_layout=layout) == 34
        assert resolve_expected_tray(255, [(32, False)], None, tray_layout=layout) is None

    def test_tray_layout_is_rebuilt_only_when_the_units_change(self):
        client = _ingested()
        first = client.tray_layout
        client._process_message({"print": _frame()})  # same units again
        assert client.tray_layout is first
        client.state.raw_data["ams"] = [{"id": "0", "info": "1001"}]
        assert client.tray_layout is not first
        assert client.tray_layout.rack_units == ()

    def test_an_offline_h3c_keeps_its_numbering(self):
        from backend.app.services.printer_manager import printer_tray_layout

        class _Manager:
            def __init__(self, model):
                self.model = model

            def get_status(self, printer_id):
                return None

            def get_model(self, printer_id):
                return self.model

        h3c = printer_tray_layout(2, _Manager("N10"))
        assert h3c.slot_key(30) == (32, 2) and h3c.tray_from_slot_key(32, 2) == 30
        assert h3c.is_external(250) and h3c.slot_key(250) == (255, 5)
        assert printer_tray_layout(1, _Manager("X1C")) == TrayLayout()

    def test_runout_keeps_an_ams_ht_target_next_to_the_ams3h(self):
        layout = TrayLayout.from_units([{"id": 32, "ams_type": 6}, {"id": 128, "ams_type": 4}])
        assert resolve_expected_tray(128, [(32, False), (128, True)], None, tray_layout=layout) == 128
        assert resolve_expected_tray(3, [(32, False), (128, True)], None, tray_layout=layout) == 31


class TestWire:
    def _setting(self, client: BambuMQTTClient, ams_id: int, tray_id: int) -> bool:
        return client.ams_set_filament_setting(
            ams_id=ams_id,
            tray_id=tray_id,
            tray_info_idx="GFL99",
            tray_type="PLA",
            tray_sub_brands="",
            tray_color="F7D959FF",
            nozzle_temp_min=190,
            nozzle_temp_max=230,
        )

    def test_filament_setting_targets_unit_32(self):
        # Verified on the printer: {ams_id 32, tray_id 8, slot_id 8} changed only slot 9.
        client = _wired(_ingested())
        assert self._setting(client, 32, 6)
        sent = _last_payload(client)
        assert (sent["ams_id"], sent["tray_id"], sent["slot_id"]) == (32, 6, 6)

    def test_reset_slot_targets_unit_32(self):
        client = _wired(_ingested())
        assert client.reset_ams_slot(32, 8)
        sent = _last_payload(client)
        assert (sent["ams_id"], sent["tray_id"], sent["slot_id"]) == (32, 8, 8)

    def test_k_profile_selection_targets_unit_32(self):
        client = _wired(_ingested())
        assert client.extrusion_cali_sel(ams_id=32, tray_id=8, cali_idx=-1, filament_id="GFL99")
        sent = _last_payload(client)
        assert (sent["ams_id"], sent["tray_id"], sent["slot_id"]) == (32, 8, 8)

    def test_rfid_refresh_names_the_loaded_ams3h_slot(self):
        client = _wired(_ingested())  # tray_now 6: global 34
        ok, message = asyncio.run(client.ams_refresh_tray(32, 4))
        assert not ok
        assert message.endswith("Currently loaded: AMS 3H slot 7")

    def test_rfid_reread_targets_unit_32(self):
        frame = _frame()
        frame["ams"]["tray_now"] = "255"
        client = _wired(_ingested(frame=frame))
        client._rfid_ack_timeout = 0.01
        ok, _ = asyncio.run(client.ams_refresh_tray(32, 4))
        assert ok
        sent = _last_payload(client)
        assert (sent["command"], sent["ams_id"], sent["slot_id"]) == ("ams_get_rfid", 32, 4)

    def test_regular_ams_unchanged(self):
        client = _wired(_client(model="X1C"))
        assert self._setting(client, 0, 2)
        sent = _last_payload(client)
        assert (sent["ams_id"], sent["tray_id"], sent["slot_id"]) == (0, 2, 2)

    # Load / unload as Bambu Studio 02.09.01.70 sends them for a direct-connected
    # hotend rack (PageFilament.cpp): unit 32, extruder 0; a load targets the
    # unit at slot k, an unload targets 255 at slot 255-k. Verified on the printer.
    def test_load_feeds_the_slot_into_its_own_hotend(self):
        client = _wired(_ingested())
        assert client.ams_load_filament(30)  # AMS 3H slot 3
        sent = _last_payload(client)
        assert sent["command"] == "ams_change_filament"
        assert (sent["ams_id"], sent["target"], sent["slot_id"], sent["extruder_id"]) == (32, 32, 2, 0)
        assert client.state.pending_tray_target == 30

    def test_unload_names_the_feeder_as_255_minus_slot(self):
        client = _wired(_ingested())
        assert client.ams_unload_filament(36)
        sent = _last_payload(client)
        assert (sent["ams_id"], sent["target"], sent["slot_id"], sent["extruder_id"]) == (32, 255, 247, 0)

    def test_unload_without_a_slot_takes_the_hotend_on_the_toolhead(self):
        client = _wired(_ingested())  # FRAME: tray_now "6" -> 34
        assert client.ams_unload_filament()
        assert _last_payload(client)["slot_id"] == 255 - 6

    def test_temps_are_the_slot_filament_midpoints(self):
        frame = _frame()
        trays = {t["id"]: t for t in frame["ams"]["ams"][0]["tray"]}
        trays["2"].update(nozzle_temp_min="190", nozzle_temp_max="230")
        trays["6"].update(nozzle_temp_min="220", nozzle_temp_max="240")
        client = _wired(_ingested(frame=frame))
        assert client.ams_load_filament(30)
        sent = _last_payload(client)
        assert (sent["tar_temp"], sent["curr_temp"]) == (210, 230)  # slot 2's range; slot 6 is on the toolhead

    def test_temps_unknown_are_minus_one(self):
        client = _wired(_ingested())
        assert client.ams_load_filament(28)
        sent = _last_payload(client)
        assert (sent["tar_temp"], sent["curr_temp"]) == (-1, -1)

    def test_load_refuses_a_slot_with_no_filament(self):
        # Studio offers Load only once the AMS has pulled a spool in; FRAME slot 7 is empty.
        client = _wired(_ingested())
        assert client.rack_tray_has_filament(28) and not client.rack_tray_has_filament(35)
        assert client.ams_load_filament(35) is False
        client._client.publish.assert_not_called()

    def test_only_an_ams3h_slot_loads(self):
        client = _wired(_ingested())
        for tray in (254, 250, 2, 128):
            assert client.ams_load_filament(tray) is False
        client._client.publish.assert_not_called()

    def test_a_regular_printer_has_no_rack(self):
        assert _client(model="X1C").tray_layout.rack_units == ()

    def test_captured_mapping2_slot_becomes_the_global_tray(self):
        detail = [{"ams_id": 32, "slot_id": 6}, {"ams_id": 32, "slot_id": 2}]
        # Studio's flat value is already 28 + k; Bambuddy's own pre-renumbering
        # dispatches wrote the raw slot. ams_mapping2 decides either way.
        assert resolve_external_spools_in_mapping([34, 2], detail, False, _ingested().tray_layout) == [34, 30]
        assert resolve_external_spools_in_mapping([6, 2], detail, False) == [34, 30]

    def test_dispatch_maps_slots_as_bambu_studio_does(self):
        client = _wired(_ingested())
        client.start_print("plate_2.3mf", ams_mapping=[28, 30, 36], use_ams=True)
        sent = _last_payload(client)
        assert sent["ams_mapping"] == [28, 30, 36]
        assert sent["ams_mapping2"] == [
            {"ams_id": 32, "slot_id": 0},
            {"ams_id": 32, "slot_id": 2},
            {"ams_id": 32, "slot_id": 8},
        ]
        assert sent["use_ams"] is True


class TestUsageAttribution:
    """Usage and Spoolman tracking see the AMS 3H through its global ids 28-36, its rows as (32, k)."""

    def test_printer_mapping_decodes_to_global_trays(self):
        from backend.app.services.usage_tracker import _decode_mqtt_mapping

        client = _ingested()
        # FRAME's mapping: slicer filament i -> (32 << 8) | slot
        expected = [28 + (v & 0xFF) for v in FRAME["mapping"]]
        assert _decode_mqtt_mapping(client.state.raw_data["mapping"], client.tray_layout) == expected

    def test_spoolman_tray_lookup_is_keyed_by_global_tray(self):
        from backend.app.services.spoolman_tracking import _global_tray_id_to_ams_slot, build_ams_tray_lookup

        client = _ingested()
        lookup = build_ams_tray_lookup(client.state.raw_data)
        assert sorted(k for k in lookup if k < 128) == list(range(28, 37))
        assert lookup[36]["tray_type"] == "PLA" and not lookup[35]["tray_type"]  # slot 9 loaded, slot 8 empty
        # ...and back to the (unit, slot) pair the slot-assignment rows are stored under
        assert _global_tray_id_to_ams_slot(34, client.tray_layout) == (32, 6)
        assert _global_tray_id_to_ams_slot(36, client.tray_layout) == (32, 8)

    def test_nothing_lands_in_the_ams_ht_range(self):
        from backend.app.services.spoolman_tracking import build_ams_tray_lookup

        lookup = build_ams_tray_lookup(_ingested().state.raw_data)
        assert not any(128 <= k <= 135 for k in lookup)


class TestExternalInputs:
    """The H3C's nine vir_slot entries (247-255) are one external input per hotend."""

    VIR_SLOT = [{"id": str(i), "state": 1, "tray_type": "", "tray_color": "00000000"} for i in range(247, 256)]

    def _with_inputs(self) -> BambuMQTTClient:
        frame = _frame()
        frame["vir_slot"] = copy.deepcopy(self.VIR_SLOT)
        frame["vir_slot"][3].update(tray_type="PETG", tray_color="FF0000FF")  # id 250 -> hotend 5
        return _ingested(frame=frame)

    def test_all_nine_are_kept_with_their_ids(self):
        vt = self._with_inputs().state.raw_data["vt_tray"]
        assert [v["id"] for v in vt] == [str(i) for i in range(247, 256)]

    def test_the_layout_counts_them_as_externals(self):
        layout = self._with_inputs().tray_layout
        assert layout.rack
        assert all(layout.is_external(v) for v in range(247, 256))
        assert layout.slot_key(250) == (255, 5)

    def test_dual_nozzle_external_slots_untouched(self):
        client = _client(model="H2D")
        client._process_message({"print": {"vir_slot": [{"id": "254"}, {"id": "255"}]}})
        assert [v["id"] for v in client.state.raw_data["vt_tray"]] == ["254", "255"]
        assert not client.tray_layout.rack

    def test_filament_setting_names_the_input(self):
        client = _wired(self._with_inputs())
        assert TestWire()._setting(client, 255, 5)  # stored slot 5 = input 250
        sent = _last_payload(client)
        assert (sent["ams_id"], sent["tray_id"], sent["slot_id"]) == (250, 0, 0)

    def test_dispatch_sends_an_input_as_studio_does(self):
        client = _wired(self._with_inputs())
        client.start_print("plate_2.3mf", ams_mapping=[28, 250], use_ams=True)
        sent = _last_payload(client)
        assert sent["ams_mapping"] == [28, -1]
        assert sent["ams_mapping2"][1] == {"ams_id": 250, "slot_id": 0}
        assert sent["use_ams"] is True

    def test_an_input_only_print_turns_the_ams_off(self):
        client = _wired(self._with_inputs())
        client.start_print("plate_2.3mf", ams_mapping=[250], use_ams=True)
        assert _last_payload(client)["use_ams"] is False

    def test_a_captured_input_keeps_its_id(self):
        layout = self._with_inputs().tray_layout
        resolved = resolve_external_spools_in_mapping(
            [-1, -1], [{"ams_id": 250, "slot_id": 0}, {"ams_id": 255, "slot_id": 255}], False, layout
        )
        assert resolved == [250, -1]

    def test_printer_mapping_keeps_its_external_inputs(self):
        # (250 << 8) | 0 is input 250 and (255 << 8) | 0 input 255 (hotend 0), not 254.
        from backend.app.services.spool_assignment_notifications import _decode_mqtt_mapping_to_global_trays
        from backend.app.services.usage_tracker import _decode_mqtt_mapping

        layout = self._with_inputs().tray_layout
        mapping = [(32 << 8) | 1, (250 << 8) | 0, (255 << 8) | 0, 0xFFFF]
        assert _decode_mqtt_mapping(mapping, layout) == [29, 250, 255, -1]
        assert _decode_mqtt_mapping_to_global_trays(mapping, layout) == [29, 250, 255]

    def test_tray_now_255_is_not_a_second_external_holder(self):
        from backend.app.services.usage_tracker import has_second_external_holder

        assert has_second_external_holder(self._with_inputs().state.raw_data) is False
        assert has_second_external_holder({"vt_tray": [{"id": "254"}, {"id": "255"}]}) is True

    def test_not_a_dual_nozzle_printer(self):
        from backend.app.services.slot_nozzle import reports_two_nozzles

        assert reports_two_nozzles(self._with_inputs().state) is False


class TestChamberHeater:
    """H3C: M141 only heats with the airduct in heating mode (captured 2026-11-06)."""

    def _sent(self, client: BambuMQTTClient) -> list[dict]:
        return [json.loads(c[0][1])["print"] for c in client._client.publish.call_args_list]

    def test_heating_mode_is_set_before_m141(self):
        client = _wired(_ingested())
        assert client.state.airduct_mode == 0
        assert client.set_chamber_temperature(45)
        sent = self._sent(client)
        assert [s["command"] for s in sent] == ["set_airduct", "gcode_line"]
        assert sent[0]["modeId"] == 1 and "M141 S45" in sent[1]["param"]

    def test_no_switch_when_already_heating_or_turning_off(self):
        client = _wired(_ingested())
        client.state.airduct_mode = 1
        client.set_chamber_temperature(45)
        client.state.airduct_mode = 0
        client.set_chamber_temperature(0)
        assert [s["command"] for s in self._sent(client)] == ["gcode_line", "gcode_line"]

    def test_h2_series_unchanged(self):
        client = _wired(_client(model="H2D"))
        client.set_chamber_temperature(45)
        assert [s["command"] for s in self._sent(client)] == ["gcode_line"]


class TestDrying:
    """AMS 3H drying is per zone: only a zone holding exactly two slots dries.

    Zones as captured on 2026-11-06 while drying PLA: zone 0 = slots [5, 7]
    (drying, 675 min left), zone 1 = the other seven (ambient).
    """

    ZONES = [
        {
            "zone_id": 0,
            "zone_slots": [5, 7],
            "dry_time": 675,
            "dry_setting": {"dry_duration": 12, "dry_filament": "PLA", "dry_temperature": 55},
            "dry_sf_reason": [6],
            "info": "240842026",
            "temp": "55.5",
            "humidity_raw": "25",
        },
        {
            "zone_id": 1,
            "zone_slots": [0, 1, 2, 3, 4, 6, 8],
            "dry_time": 675,
            "dry_setting": {"dry_duration": -1, "dry_filament": "", "dry_temperature": -1},
            "dry_sf_reason": [6],
            "info": "240042006",
            "temp": "27.6",
            "humidity_raw": "33",
        },
    ]

    def _drying(self, zones: list | None = None) -> BambuMQTTClient:
        frame = _frame()
        unit = frame["ams"]["ams"][0]
        unit["zone_info"] = copy.deepcopy(zones if zones is not None else self.ZONES)
        unit["dry_info"] = "1"
        return _ingested(frame=frame)

    def test_the_drying_zone_goes_on_the_unit(self):
        unit = _unit(self._drying())
        assert unit["dry_time"] == 675
        assert unit["dry_zone_id"] == 0 and unit["dry_zone_slots"] == [5, 7]
        assert unit["dry_setting"]["dry_filament"] == "PLA"
        assert unit["dry_status"] == 2  # Drying, from the unit's info bits 4-7

    def test_start_names_the_zone_and_the_cooling_temp(self):
        client = _wired(self._drying())
        client.send_drying_command(32, temp=55, duration=12, mode=1, filament="PLA", rotate_tray=True)
        sent = _last_payload(client)
        assert sent["command"] == "ams_filament_drying"
        assert (sent["ams_id"], sent["zone_id"], sent["mode"]) == (32, 0, 1)
        assert (sent["temp"], sent["duration"], sent["cooling_temp"]) == (55, 12, 65)
        assert sent["rotate_tray"] is False  # the AMS 3H can't turn spools

    def test_zone_id_follows_whichever_zone_holds_two_slots(self):
        zones = copy.deepcopy(self.ZONES)
        zones[0]["zone_slots"], zones[1]["zone_slots"] = [0, 1, 2, 3, 4, 6, 8], [5, 7]
        client = _wired(self._drying(zones))
        client.send_drying_command(32, temp=55, duration=4, mode=1, filament="PLA")
        assert _last_payload(client)["zone_id"] == 1

    def test_abs_cools_at_its_softening_temperature(self):
        client = _wired(self._drying())
        client.send_drying_command(32, temp=80, duration=8, mode=1, filament="ABS")
        sent = _last_payload(client)
        assert (sent["ams_id"], sent["cooling_temp"]) == (32, 95)

    def test_stop_names_no_zone(self):
        client = _wired(self._drying())
        client.send_drying_command(32, temp=0, duration=0, mode=0)
        sent = _last_payload(client)
        assert (sent["ams_id"], sent["mode"], sent["cooling_temp"]) == (32, 0, 0)
        assert "zone_id" not in sent

    def test_cooling_temps(self):
        assert ams3h_drying_cooling_temp("PLA") == 65
        assert ams3h_drying_cooling_temp("PETG-CF") == 75
        assert ams3h_drying_cooling_temp("TPU") == 70
        assert ams3h_drying_cooling_temp("WOOD") == 50

    def test_other_models_unchanged(self):
        client = _wired(_client(model="H2D"))
        client.send_drying_command(0, temp=55, duration=4, mode=1, filament="PLA")
        sent = _last_payload(client)
        assert "zone_id" not in sent and sent["cooling_temp"] == 20 and sent["ams_id"] == 0

    def test_the_header_reads_the_ambient_zone_and_keeps_the_dryers_own_readings(self):
        # Bambu Studio shows the zone that can't dry as the AMS's humidity and the
        # drying zone's on the dryer; the unit itself reports neither.
        unit = _unit(self._drying())
        assert (unit["humidity_raw"], unit["temp"]) == ("33", "27.6")
        assert (unit["dry_zone_humidity_raw"], unit["dry_zone_temp"]) == ("25", "55.5")

    def test_the_card_gets_the_dryer_slots_and_readings(self):
        (card,) = ams_units_to_dicts(self._drying().state.raw_data["ams"], lambda *a: None)
        assert (card["humidity"], card["temp"]) == (33, 27.6)
        assert (card["dryer_slots"], card["dryer_humidity"], card["dryer_temp"]) == ([5, 7], 25, 55.5)

    def test_no_zone_info_means_no_dryer_on_the_card(self):
        (card,) = ams_units_to_dicts(_ingested().state.raw_data["ams"], lambda *a: None)
        assert (card["dryer_slots"], card["dryer_humidity"], card["dryer_temp"]) == ([], None, None)
        assert card["humidity"] is None
