"""Tests for the P2S/X2D left auxiliary part cooling fan (#2576).

The "Auxiliary Part Cooling Fan - Left" (also fits X2D) is reported ONLY as
device.airduct part with raw id 160 (decoded id = 160 >> 4 = 10,
AIR_FUN.FAN_REMOTE_COOLING_1 in Bambu Studio) — the firmware does NOT mirror
it into any flat big_fanX_speed field, which is why it was previously dropped.
It is controlled with "M106 P10", exactly like Bambu's official P2S machine-
profile gcode does.

The airduct payloads below are verbatim captures from a live P2S
(fw 01.02.00.00) with the accessory installed.
"""

import pytest


@pytest.fixture
def mqtt_client():
    from backend.app.services.bambu_mqtt import BambuMQTTClient

    return BambuMQTTClient(
        ip_address="192.168.1.100",
        serial_number="TESTP2S",
        access_code="12345678",
    )


def _airduct_device(parts):
    """Wrap airduct parts in the device envelope as pushed by a P2S."""
    return {
        "device": {
            "airduct": {
                "modeCur": 0,
                "modeFunc": 0,
                "modeList": [
                    {"ctrl": [16, 32, 160, 48], "modeId": 0, "off": []},
                    {"ctrl": [16, 32, 48], "modeId": 1, "off": [160]},
                ],
                "modeVisable": 7,
                "parts": parts,
                "subFunc": 0,
                "subMode": 0,
                "subVisable": 7,
                "version": 1,
            },
            "type": 1,
        }
    }


# Verbatim parts list from a live P2S: part cooling ramping (state 30,
# target 90), right aux at 40%, left aux OFF, chamber at 70%.
P2S_PARTS_LEFT_AUX_OFF = [
    {"func": 0, "id": 16, "range": 6553600, "state": 30, "tar_state": 90},
    {"func": 6, "id": 32, "range": 6553600, "state": 40, "tar_state": 40},
    {"func": 5, "id": 160, "range": 6553600, "state": 0, "tar_state": 0},
    {"func": 2, "id": 48, "range": 6553600, "state": 70, "tar_state": 70},
]

# Same printer later in the print: left aux running at 80%.
P2S_PARTS_LEFT_AUX_80 = [
    {"func": 0, "id": 16, "range": 6553600, "state": 60, "tar_state": 60},
    {"func": 6, "id": 32, "range": 6553600, "state": 100, "tar_state": 100},
    {"func": 5, "id": 160, "range": 6553600, "state": 80, "tar_state": 80},
    {"func": 2, "id": 48, "range": 6553600, "state": 80, "tar_state": 80},
]


class TestLeftAuxFanParsing:
    """device.airduct part id 10 (raw 160) -> state.left_aux_fan_speed."""

    def test_defaults_to_none(self, mqtt_client):
        assert mqtt_client.state.left_aux_fan_speed is None

    def test_parses_left_aux_running(self, mqtt_client):
        mqtt_client._update_state(_airduct_device(P2S_PARTS_LEFT_AUX_80))
        assert mqtt_client.state.left_aux_fan_speed == 80

    def test_parses_left_aux_off(self, mqtt_client):
        mqtt_client._update_state(_airduct_device(P2S_PARTS_LEFT_AUX_OFF))
        assert mqtt_client.state.left_aux_fan_speed == 0

    def test_raw_id_is_bit_unpacked(self, mqtt_client):
        """Raw id 160 must decode to part id 10 (id >> 4), NOT match on 160."""
        # A hypothetical raw id of 10 would decode to part id 0 — must not match.
        parts = [{"func": 5, "id": 10, "range": 6553600, "state": 50, "tar_state": 50}]
        mqtt_client._update_state(_airduct_device(parts))
        assert mqtt_client.state.left_aux_fan_speed is None

    def test_parts_without_left_aux_reports_none(self, mqtt_client):
        """A full parts list without id 10 means the fan is not installed."""
        mqtt_client.state.left_aux_fan_speed = 80  # previously seen
        parts = [p for p in P2S_PARTS_LEFT_AUX_80 if p["id"] != 160]
        mqtt_client._update_state(_airduct_device(parts))
        assert mqtt_client.state.left_aux_fan_speed is None

    def test_diff_push_without_device_preserves_value(self, mqtt_client):
        """P-series diff pushes omit device.airduct — value must survive."""
        mqtt_client._update_state(_airduct_device(P2S_PARTS_LEFT_AUX_80))
        mqtt_client._update_state({"nozzle_temper": 250.0})
        assert mqtt_client.state.left_aux_fan_speed == 80

    def test_state_clamped_to_0_100(self, mqtt_client):
        parts = [{"func": 5, "id": 160, "range": 6553600, "state": 250, "tar_state": 0}]
        mqtt_client._update_state(_airduct_device(parts))
        assert mqtt_client.state.left_aux_fan_speed == 100

    def test_malformed_part_entries_ignored(self, mqtt_client):
        parts = [
            "not-a-dict",
            {"func": 5},  # no id/state
            {"id": "garbage", "state": 10},
            {"func": 5, "id": 160, "range": 6553600, "state": 30, "tar_state": 30},
        ]
        mqtt_client._update_state(_airduct_device(parts))
        assert mqtt_client.state.left_aux_fan_speed == 30

    def test_flat_fan_fields_unaffected(self, mqtt_client):
        """Regression: flat fields keep coming from the flat MQTT keys."""
        payload = {
            "cooling_fan_speed": "4",
            "big_fan1_speed": "6",
            "big_fan2_speed": "10",
            "heatbreak_fan_speed": "14",
            **_airduct_device(P2S_PARTS_LEFT_AUX_OFF),
        }
        mqtt_client._update_state(payload)
        assert mqtt_client.state.cooling_fan_speed == 27  # 4/15
        assert mqtt_client.state.big_fan1_speed == 40  # 6/15
        assert mqtt_client.state.big_fan2_speed == 67  # 10/15
        assert mqtt_client.state.heatbreak_fan_speed == 93  # 14/15
        assert mqtt_client.state.left_aux_fan_speed == 0


class TestExhaustFanPresence:
    """device.airduct part id 3 (raw 48) presence -> state.exhaust_fan_present.

    The chamber exhaust fan is a P2S/X2D add-on kit (get_version module "eef").
    Its speed rides on the flat big_fan2_speed field, but the airduct only lists
    part id 3 when the kit is physically installed — so part-3 presence is the
    signal the UI uses to show/hide the Exhaust tile.
    """

    def test_defaults_to_false(self, mqtt_client):
        assert mqtt_client.state.exhaust_fan_present is False

    def test_present_when_part_3_reported(self, mqtt_client):
        # Full P2S parts list includes id 48 (>>4 = 3).
        mqtt_client._update_state(_airduct_device(P2S_PARTS_LEFT_AUX_80))
        assert mqtt_client.state.exhaust_fan_present is True

    def test_absent_when_part_3_missing(self, mqtt_client):
        mqtt_client.state.exhaust_fan_present = True  # previously seen
        parts = [p for p in P2S_PARTS_LEFT_AUX_80 if p["id"] != 48]
        mqtt_client._update_state(_airduct_device(parts))
        assert mqtt_client.state.exhaust_fan_present is False

    def test_base_p2s_only_part_cooling_and_aux(self, mqtt_client):
        # A base P2S (no exhaust kit, no left aux kit) lists only ids 1 and 2.
        parts = [
            {"func": 0, "id": 16, "range": 6553600, "state": 0, "tar_state": 0},
            {"func": 6, "id": 32, "range": 6553600, "state": 0, "tar_state": 0},
        ]
        mqtt_client._update_state(_airduct_device(parts))
        assert mqtt_client.state.exhaust_fan_present is False
        assert mqtt_client.state.left_aux_fan_speed is None

    def test_diff_push_without_device_preserves_value(self, mqtt_client):
        mqtt_client._update_state(_airduct_device(P2S_PARTS_LEFT_AUX_80))
        mqtt_client._update_state({"nozzle_temper": 250.0})
        assert mqtt_client.state.exhaust_fan_present is True


class TestLeftAuxFanCommand:
    """set_fan_speed must accept index 10 and emit M106 P10."""

    def test_set_fan_speed_10_sends_m106_p10(self, mqtt_client, monkeypatch):
        sent = []
        monkeypatch.setattr(mqtt_client, "send_gcode", lambda g: sent.append(g) or True)
        assert mqtt_client.set_fan_speed(10, 204) is True
        assert sent == ["M106 P10 S204"]

    def test_set_left_aux_fan_helper(self, mqtt_client, monkeypatch):
        sent = []
        monkeypatch.setattr(mqtt_client, "send_gcode", lambda g: sent.append(g) or True)
        assert mqtt_client.set_left_aux_fan(255) is True
        assert sent == ["M106 P10 S255"]

    def test_speed_clamped_to_255(self, mqtt_client, monkeypatch):
        sent = []
        monkeypatch.setattr(mqtt_client, "send_gcode", lambda g: sent.append(g) or True)
        mqtt_client.set_left_aux_fan(999)
        assert sent == ["M106 P10 S255"]

    def test_invalid_fan_index_rejected(self, mqtt_client, monkeypatch):
        sent = []
        monkeypatch.setattr(mqtt_client, "send_gcode", lambda g: sent.append(g) or True)
        assert mqtt_client.set_fan_speed(4, 100) is False
        assert mqtt_client.set_fan_speed(11, 100) is False
        assert sent == []

    def test_existing_fan_indexes_still_accepted(self, mqtt_client, monkeypatch):
        sent = []
        monkeypatch.setattr(mqtt_client, "send_gcode", lambda g: sent.append(g) or True)
        for idx in (1, 2, 3):
            assert mqtt_client.set_fan_speed(idx, 128) is True
        assert sent == ["M106 P1 S128", "M106 P2 S128", "M106 P3 S128"]
