"""An A2L AMS-Lite tray in a captured print command keeps its global id.

The firmware addresses the Lite as physical unit 16: a print command carries
the LOCAL slot 0-3 in the flat ``ams_mapping`` and ``{ams_id: 16, slot_id: n}``
in ``ams_mapping2`` (confirmed against the firmware's own mapping; our dispatch
sends the same). Inside Bambuddy the Lite is unit 6, global trays 24-27.

The captured mapping kept the local slot, so a print from Lite slot 2 (global
26) reached usage tracking as tray 2 -- AMS 0 slot 3, or nothing on a Lite-only
A2L -- and the Lite spool was never charged. The virtual printer took the
slicer's mapping through the same resolver, so a queued A2L job dispatched to
AMS 0 instead of the Lite.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient, resolve_external_spools_in_mapping
from backend.app.services.virtual_printer.manager import _extract_slicer_ams_mapping_json

pytestmark = pytest.mark.unit


def lite(slot: int) -> dict:
    return {"ams_id": 16, "slot_id": slot}


EXT_RIGHT = {"ams_id": 255, "slot_id": 0}
UNMAPPED = {"ams_id": 255, "slot_id": 255}


def a2l_client() -> BambuMQTTClient:
    client = BambuMQTTClient(ip_address="10.0.0.9", serial_number="A2L0001", access_code="12345678", model="N9")
    client._client = MagicMock()
    client.state.connected = True
    return client


class TestTheResolver:
    def test_lite_slot_becomes_its_global_tray(self):
        assert resolve_external_spools_in_mapping([1], [lite(1)], is_dual_nozzle=False) == [25]

    def test_every_lite_slot(self):
        mapping2 = [lite(0), lite(1), lite(2), lite(3)]
        assert resolve_external_spools_in_mapping([0, 1, 2, 3], mapping2, is_dual_nozzle=False) == [24, 25, 26, 27]

    def test_regular_ams_beside_the_lite_is_untouched(self):
        mapping2 = [{"ams_id": 0, "slot_id": 1}, lite(1), EXT_RIGHT, UNMAPPED]
        assert resolve_external_spools_in_mapping([1, 1, -1, -1], mapping2, is_dual_nozzle=False) == [1, 25, 254, -1]

    def test_only_a_minus_one_becomes_an_external_spool(self):
        """The Lite branch moved the external-spool guard; a real flat tray
        must still win over an ams_mapping2 entry that names the external spool."""
        assert resolve_external_spools_in_mapping([3], [EXT_RIGHT], is_dual_nozzle=False) == [3]

    def test_slots_outside_the_lite_stay_as_captured(self):
        assert resolve_external_spools_in_mapping([1, -1], [lite(4), lite(255)], is_dual_nozzle=False) == [1, -1]

    def test_without_mapping2_the_flat_list_stays(self):
        # Nothing names the unit, so there is nothing to resolve from.
        assert resolve_external_spools_in_mapping([1], None, is_dual_nozzle=False) == [1]


class TestTheCapture:
    def test_slicer_command_captures_the_global_tray(self):
        client = a2l_client()
        client._handle_request_message(
            {"print": {"command": "project_file", "url": "ftp://x.3mf", "ams_mapping": [2], "ams_mapping2": [lite(2)]}}
        )
        assert client._captured_ams_mapping == [26]

    def test_our_own_dispatch_round_trips(self):
        """What we send for global 25 must be captured back as 25."""
        client = a2l_client()
        assert client.start_print("lite.3mf", ams_mapping=[25], use_ams=True) is True
        sent = json.loads(client._client.publish.call_args.args[1])
        assert sent["print"]["ams_mapping"] == [1]
        assert sent["print"]["ams_mapping2"] == [lite(1)]

        client._handle_request_message(sent)

        assert client._captured_ams_mapping == [25]

    def test_report_topic_echo_captures_the_global_tray(self):
        """The printer's own project_file response goes through the same capture."""
        client = a2l_client()
        client._process_message(
            {
                "print": {
                    "command": "project_file",
                    "result": "SUCCESS",
                    "url": "file:///userdata/model/history/lite.gcode.3mf",
                    "ams_mapping": [0],
                    "ams_mapping2": [lite(0)],
                }
            }
        )
        assert client._captured_ams_mapping == [24]


class TestTheVirtualPrinterIntake:
    def test_slicer_lite_pick_is_stored_as_the_global_tray(self):
        data = {"ams_mapping": [3], "ams_mapping2": [lite(3)]}
        assert json.loads(_extract_slicer_ams_mapping_json(data, "[VP test]")) == [27]
