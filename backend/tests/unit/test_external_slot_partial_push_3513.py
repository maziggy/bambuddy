"""A partial vt_tray push updates the external slot instead of replacing it (#3513).

The A1 and A1 mini report a change to the external spool as a delta with only
the changed keys, often without the slot id: a colour-only change pushes just
``{"tray_color": ...}``. Stored as it came, it emptied the slot (no type, no
preset) until the next full push, and without its id the assignment check
could not find slot 254 and timed out on the old preset.
"""

from unittest.mock import MagicMock

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient

pytestmark = pytest.mark.unit

# The reporter's slot after a full push.
FULL = {
    "id": "254",
    "tray_type": "PLA",
    "tray_sub_brands": "",
    "tray_color": "C0C0C0FF",
    "tray_info_idx": "P0ea739d",
    "nozzle_temp_min": "190",
    "nozzle_temp_max": "230",
    "k": 0.02,
    "cali_idx": 5,
    "remain": 0,
}


def _client(model: str = "A1 mini") -> BambuMQTTClient:
    client = BambuMQTTClient(ip_address="10.0.0.1", serial_number="SN", access_code="c", model=model)
    client._process_message({"print": {"command": "push_status", "vt_tray": dict(FULL)}})
    return client


def _push(client: BambuMQTTClient, vt_tray) -> dict:
    client._process_message({"print": {"command": "push_status", "vt_tray": vt_tray}})
    return client.state.raw_data["vt_tray"]


class TestTheDelta:
    def test_a_colour_only_change_keeps_the_rest_of_the_slot(self):
        client = _client()

        slots = _push(client, {"tray_color": "FFFFFFFF"})

        assert slots == [{**FULL, "tray_color": "FFFFFFFF"}]
        assert client._find_verify_tray(255, 0) is slots[0]

    def test_the_same_id_merges_too(self):
        slots = _push(_client(), {"id": "254", "tray_info_idx": "GFL99"})

        assert slots == [{**FULL, "tray_info_idx": "GFL99"}]

    def test_the_delta_confirms_a_pending_assignment(self):
        client = _client()
        seen = MagicMock()
        client.on_assignment_verified = seen
        client.register_assignment_verification(255, 0, "GFL03", "FFFFFFFF", None)

        _push(client, {"tray_info_idx": "GFL03", "tray_color": "FFFFFFFF"})

        seen.assert_called_once()
        assert seen.call_args.args[:3] == (255, 0, True)


class TestWholesale:
    def test_an_emptied_slot_keeps_nothing_of_the_spool(self):
        emptied = {"id": "254", "tray_type": "", "tray_color": "", "tray_info_idx": ""}

        assert _push(_client(), emptied) == [emptied]

    def test_another_slot_id_replaces_it(self):
        other = {"id": "255", "tray_type": "PETG"}

        assert _push(_client(), other) == [other]

    def test_two_external_slots_ignore_a_single_dict(self):
        client = BambuMQTTClient(ip_address="10.0.0.1", serial_number="SN", access_code="c", model="H2D")
        both = [{**FULL, "id": "254"}, {**FULL, "id": "255", "tray_type": "PETG"}]
        client._process_message({"print": {"command": "push_status", "vir_slot": [dict(s) for s in both]}})

        assert _push(client, {"tray_color": "FFFFFFFF"}) == both
