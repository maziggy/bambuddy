"""An A2L with an AMS 2 Pro and an AMS Lite shows both on the card (#3499).

The firmware sends unit ids as strings ("0"); the AMS Lite's physical id 16 is
normalised to the int 6. With both units attached the merge sorted "0" against
6 and raised, so the unit list was never stored. The frame's own AMS object
stayed in its place, and every later frame (and the AMS labels API) failed on
its string keys: the card showed only the external spool until one AMS was
unplugged and the printer reconnected.

Frame values follow the reporter's support package: AMS 2 Pro with four bare
slots, AMS Lite with four Bambu PLA Basic spools, ams_exist_bits "1001",
tray_exist_bits "f000000".
"""

from unittest.mock import patch

from backend.app.services.bambu_mqtt import BambuMQTTClient


def _client() -> BambuMQTTClient:
    return BambuMQTTClient(ip_address="10.0.0.1", serial_number="A2L", access_code="c", model="A2L")


def _lite_tray(slot: int, colour: str) -> dict:
    return {
        "id": str(slot),
        "state": 3,
        "remain": -1,
        "tray_type": "PLA",
        "tray_sub_brands": "PLA Basic",
        "tray_info_idx": "GFA00",
        "tray_color": colour,
    }


def _frame(lite_id=16) -> dict:
    return {
        "ams": {
            "ams": [
                {"id": "0", "info": "2003", "tray": [{"id": str(n)} for n in range(4)]},
                {
                    "id": lite_id,
                    "info": "30001005",
                    "tray": [
                        _lite_tray(0, "000000FF"),
                        _lite_tray(1, "3F8E43FF"),
                        _lite_tray(2, "0056B8FF"),
                        _lite_tray(3, "FFFFFFFF"),
                    ],
                },
            ],
            "ams_exist_bits": "1001",
            "tray_exist_bits": "f000000",
            "tray_now": "255",
            "power_on_flag": True,
        },
        "gcode_state": "IDLE",
    }


def _units(client: BambuMQTTClient) -> list:
    units = client.state.raw_data.get("ams")
    assert isinstance(units, list), f"AMS units are {type(units).__name__}, not a list"
    return units


def test_both_units_are_stored():
    client = _client()

    client._process_message({"print": _frame()})

    units = _units(client)
    assert [str(u["id"]) for u in units] == ["0", "6"]
    assert [t["tray_color"] for t in units[1]["tray"]] == ["000000FF", "3F8E43FF", "0056B8FF", "FFFFFFFF"]


def test_a_string_lite_id_is_stored_too():
    client = _client()

    client._process_message({"print": _frame(lite_id="16")})

    assert [str(u["id"]) for u in _units(client)] == ["0", "6"]


def test_later_frames_keep_both_units():
    client = _client()
    for _ in range(3):
        client._process_message({"print": _frame()})

    assert [str(u["id"]) for u in _units(client)] == ["0", "6"]


def test_a_frame_the_ams_handling_fails_on_leaves_no_ams_object_behind():
    client = _client()

    # Any failure in the AMS handling, as the mixed-id sort was.
    with patch.object(client, "_handle_ams_data", side_effect=TypeError("boom")):
        client._process_message({"print": _frame()})

    assert "ams" not in client.state.raw_data


def test_a_stored_ams_object_does_not_block_later_frames():
    # An install already in the broken state: the AMS object in place of the list.
    client = _client()
    client.state.raw_data["ams"] = {"ams": [], "ams_exist_bits": "1001"}

    client._process_message({"print": _frame()})

    assert [str(u["id"]) for u in _units(client)] == ["0", "6"]


def test_units_are_in_numeric_order():
    client = _client()
    frame = _frame()
    frame["ams"]["ams"].append({"id": "1", "tray": [{"id": str(n)} for n in range(4)]})
    frame["ams"]["ams_exist_bits"] = "1003"

    client._process_message({"print": frame})

    assert [str(u["id"]) for u in _units(client)] == ["0", "1", "6"]
