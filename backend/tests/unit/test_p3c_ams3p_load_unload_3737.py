"""Load, unload and Unload all on the P3C's AMS 3P (#3737).

Tested by the reporter on a P3C (firmware 01.01.00.00) over MQTT:

- The load/unload routes refused the AMS 3P's tray ids 37-43, which only the
  AMS 3H's 28-36 passed.
- The MQTT client refused to load an AMS 3P slot, after Bambu Studio's
  CanAutoFeed. The printer does accept the load and asks for a hand feed.
- The AMS 3P reports tray state 27 for every slot, loaded or not, even with no
  hotend behind it, so "loaded" is the hotend's snow, (33 << 8) | slot.
"""

import copy
import json
from unittest.mock import MagicMock

from backend.app.services.bambu_mqtt import BambuMQTTClient
from backend.app.services.printer_manager import ams_units_to_dicts
from backend.app.utils.tray_ids import AmsType, is_rack_tray_id, rack_slot_in_hotend
from backend.tests.unit.test_p3c_ams3p import FRAME


def _empty_snow(slot: int) -> int:
    return ((255 - slot) << 8) | 0xFF


# Five hotends: slots 0-2 loaded, 3 and 4 empty, as in the reporter's test.
NOZZLES = {
    "device": {
        "nozzle": {
            "exist": 0x1F0000,
            "info": [
                {"id": 16 + slot, "addr_id": slot, "snow": (33 << 8) | slot if slot < 3 else _empty_snow(slot)}
                for slot in range(5)
            ],
        }
    }
}


def _client(frame: dict) -> BambuMQTTClient:
    client = BambuMQTTClient(ip_address="10.0.0.1", serial_number="P3C", access_code="c", model="P3C")
    client._process_message({"print": copy.deepcopy(frame)})
    client._client = MagicMock()
    client.state.connected = True
    return client


def _with_nozzles() -> BambuMQTTClient:
    return _client({**FRAME, **NOZZLES})


def _sent(client: BambuMQTTClient) -> dict:
    return json.loads(client._client.publish.call_args[0][1])["print"]


class TestWhatIsLoaded:
    def test_the_hotends_decide_not_the_tray_state(self):
        assert _with_nozzles().rack_loaded_slots() == [(33, 0), (33, 1), (33, 2)]

    def test_state_27_alone_is_not_loaded(self):
        # FRAME has no hotend report: every slot says state 27 or 283, none loaded.
        assert _client(FRAME).rack_loaded_slots() == []

    def test_the_status_api_says_which_slot_is_in_its_hotend(self):
        client = _with_nozzles()
        (unit,) = ams_units_to_dicts(client.state.raw_data["ams"], lambda *a: None, None, client.state.nozzle_rack)
        assert [tray["in_hotend"] for tray in unit["tray"]] == [True, True, True, False, False, False, False]

    def test_the_ams3h_keeps_its_tray_state(self):
        assert rack_slot_in_hotend(AmsType.ZMULTI_A, 32, 0, 27, []) is True
        assert rack_slot_in_hotend(AmsType.ZMULTI_A, 32, 0, "26", [{"snow": (32 << 8)}]) is False

    def test_any_other_unit_has_no_answer(self):
        assert rack_slot_in_hotend(AmsType.AMS, 0, 0, 11, []) is None

    def test_a_regular_unit_reports_none_in_the_status(self):
        (unit,) = ams_units_to_dicts([{"id": 0, "tray": [{"id": 0, "state": 11}]}], lambda *a: None, None, [])
        assert unit["tray"][0]["in_hotend"] is None


class TestLoadAndUnload:
    def test_a_slot_loads_and_the_printer_asks_for_the_hand_feed(self):
        client = _with_nozzles()

        assert client.ams_load_filament(41) is True

        sent = _sent(client)
        assert (sent["command"], sent["ams_id"], sent["target"], sent["slot_id"]) == ("ams_change_filament", 33, 33, 4)

    def test_a_slot_without_a_spool_is_not_loaded(self):
        client = _with_nozzles()

        assert client.ams_load_filament(42) is False  # slot 6: no filament set
        client._client.publish.assert_not_called()

    def test_a_slot_unloads_through_its_feeder(self):
        client = _with_nozzles()

        assert client.ams_unload_filament(38) is True

        sent = _sent(client)
        assert (sent["ams_id"], sent["target"], sent["slot_id"]) == (33, 255, 254)

    def test_unload_all_lists_only_the_loaded_slots(self):
        client = _with_nozzles()

        assert client.ams_unload_all() == 3

        sent = _sent(client)
        assert sent["command"] == "multi_slot_ams_change_filament"
        assert [(r["ams_id"], r["slot_id"]) for r in sent["request_array"]] == [(33, 255), (33, 254), (33, 253)]


class TestTrayIds:
    def test_both_rack_ranges_are_rack_trays(self):
        assert [t for t in range(256) if is_rack_tray_id(t)] == list(range(28, 44))
        assert is_rack_tray_id("37") is False
