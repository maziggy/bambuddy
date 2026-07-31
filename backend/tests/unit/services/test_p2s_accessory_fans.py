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

    def test_packed_state_decodes_from_low_8_bits(self, mqtt_client):
        """`state` is bit-packed like its sibling `range` (end << 16 | start).

        Bambu Studio decodes it with get_flag_bits(state, 0, 8), so only the low
        byte carries the percentage. Without the mask a packed value would clamp
        to 100 instead of decoding to the real speed.
        """
        packed = (60 << 16) | 45  # sibling field in the high bits, 45% in the low byte
        parts = [{"func": 5, "id": 160, "range": 6553600, "state": packed, "tar_state": 0}]
        mqtt_client._update_state(_airduct_device(parts))
        assert mqtt_client.state.left_aux_fan_speed == 45

    def test_unpacked_state_is_unaffected_by_the_mask(self, mqtt_client):
        # Plain 0-100 values (what a P2S actually sends) must round-trip exactly.
        for speed in (0, 30, 80, 100):
            parts = [{"func": 5, "id": 160, "range": 6553600, "state": speed, "tar_state": speed}]
            mqtt_client._update_state(_airduct_device(parts))
            assert mqtt_client.state.left_aux_fan_speed == speed

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


class TestPartialPartsFrames:
    """A `parts` list that is not a full inventory must not retract presence.

    `device.airduct` is pushed field by field — the `modeCur` handler reads it
    with an `in` check for exactly that reason — so a frame can carry `parts`
    without carrying every fan. Absence is what tells us a kit is not fitted, so
    it is only trustworthy on a complete list. Read as gospel, a truncated frame
    would make both accessory badges vanish mid-print and start rejecting
    ``fan=aux2`` on a printer that does have the fan.

    Completeness is judged on ids 1 (part cooling) and 2 (aux) being present:
    neither is optional on any machine that reports an airduct at all, and both
    appear in every layout in the support-package archive (P2S base 1,2 /
    P2S+kit 1,2,3 / X2D 1,2,3,10 / H2C,H2D,H2S 1,2,3,6).
    """

    def test_partial_frame_does_not_retract_the_left_aux_fan(self, mqtt_client):
        mqtt_client._update_state(_airduct_device(P2S_PARTS_LEFT_AUX_80))
        assert mqtt_client.state.left_aux_fan_speed == 80

        # Only the part cooling fan changed — the frame says nothing about the
        # left aux fan, which is not the same as saying it is gone.
        mqtt_client._update_state(
            _airduct_device([{"func": 0, "id": 16, "range": 6553600, "state": 70, "tar_state": 70}])
        )

        assert mqtt_client.state.left_aux_fan_speed == 80

    def test_partial_frame_does_not_retract_the_exhaust_fan(self, mqtt_client):
        mqtt_client._update_state(_airduct_device(P2S_PARTS_LEFT_AUX_80))
        assert mqtt_client.state.exhaust_fan_present is True

        mqtt_client._update_state(
            _airduct_device([{"func": 0, "id": 16, "range": 6553600, "state": 70, "tar_state": 70}])
        )

        assert mqtt_client.state.exhaust_fan_present is True

    def test_a_partial_frame_still_applies_the_speed_it_carries(self, mqtt_client):
        """Not-authoritative-for-absence is not the same as ignored."""
        mqtt_client._update_state(_airduct_device(P2S_PARTS_LEFT_AUX_80))
        assert mqtt_client.state.left_aux_fan_speed == 80

        mqtt_client._update_state(
            _airduct_device([{"func": 5, "id": 160, "range": 6553600, "state": 25, "tar_state": 25}])
        )

        assert mqtt_client.state.left_aux_fan_speed == 25

    def test_a_partial_frame_can_still_reveal_a_fan(self, mqtt_client):
        """Presence may always be added — only retraction needs a full list."""
        mqtt_client._update_state(
            _airduct_device([{"func": 2, "id": 48, "range": 6553600, "state": 70, "tar_state": 70}])
        )

        assert mqtt_client.state.exhaust_fan_present is True

    def test_a_full_frame_still_retracts_both(self, mqtt_client):
        """The kits really can be removed, and a complete list must say so —
        this is the behaviour the presence gate exists for."""
        mqtt_client._update_state(_airduct_device(P2S_PARTS_LEFT_AUX_80))
        assert mqtt_client.state.left_aux_fan_speed == 80
        assert mqtt_client.state.exhaust_fan_present is True

        # Base P2S layout: part cooling + aux only.
        mqtt_client._update_state(
            _airduct_device(
                [
                    {"func": 0, "id": 16, "range": 6553600, "state": 0, "tar_state": 0},
                    {"func": 6, "id": 32, "range": 6553600, "state": 0, "tar_state": 0},
                ]
            )
        )

        assert mqtt_client.state.left_aux_fan_speed is None
        assert mqtt_client.state.exhaust_fan_present is False

    def test_an_empty_parts_list_changes_nothing(self, mqtt_client):
        mqtt_client._update_state(_airduct_device(P2S_PARTS_LEFT_AUX_80))
        mqtt_client._update_state(_airduct_device([]))

        assert mqtt_client.state.left_aux_fan_speed == 80
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


class TestExhaustFanLabelModels:
    """P2S/X2D call the big_fan2 enclosure fan "Exhaust"; others say "Chamber"."""

    def test_p2s_and_x2d_use_exhaust_label(self):
        from backend.app.utils.printer_models import uses_exhaust_fan_label

        for model in ("P2S", "X2D", "p2s", " P2S ", "N7", "N6"):
            assert uses_exhaust_fan_label(model) is True, model

    def test_other_enclosed_models_keep_chamber_label(self):
        from backend.app.utils.printer_models import uses_exhaust_fan_label

        for model in ("X1C", "X1", "X1E", "P1S", "H2D", "H2C", "H2S", "A1"):
            assert uses_exhaust_fan_label(model) is False, model

    def test_unknown_or_missing_model_defaults_to_chamber(self):
        from backend.app.utils.printer_models import uses_exhaust_fan_label

        assert uses_exhaust_fan_label(None) is False
        assert uses_exhaust_fan_label("") is False
        assert uses_exhaust_fan_label("SomeFutureModel") is False


class TestExhaustLabelModelListsAgree:
    """The exhaust-label model list is duplicated across the stack.

    The backend keeps ``EXHAUST_FAN_LABEL_MODELS`` (display names plus the N7/N6
    internal codes, since the API can be handed either) and the frontend keeps
    ``MODELS_WITH_EXHAUST_LABEL`` in PrintersPage.tsx (display names only —
    ``printer.model`` is always a display name by the time it reaches the card).
    Both are correct as written, but nothing stopped them drifting apart: adding
    a model to one and forgetting the other silently produces a card labelled
    "Exhaust" whose control toast says "Chamber fan", or vice versa.
    """

    def _frontend_models(self) -> set[str]:
        import re
        from pathlib import Path

        import pytest

        # Walk up rather than hard-coding a parent depth, so the test survives
        # the file being moved and works whatever directory pytest runs from.
        relative = Path("frontend") / "src" / "pages" / "PrintersPage.tsx"
        source = next(
            (candidate for parent in Path(__file__).resolve().parents if (candidate := parent / relative).is_file()),
            None,
        )
        if source is None:
            pytest.skip("frontend sources not present in this checkout")
        text = source.read_text(encoding="utf-8")
        match = re.search(
            r"const MODELS_WITH_EXHAUST_LABEL:[^=]*=\s*new Set\(\[(.*?)\]\)",
            text,
            re.DOTALL,
        )
        assert match, "MODELS_WITH_EXHAUST_LABEL not found in PrintersPage.tsx"
        return set(re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)))

    def test_frontend_list_is_the_display_name_subset_of_the_backend_list(self):
        from backend.app.utils.printer_models import EXHAUST_FAN_LABEL_MODELS

        frontend = self._frontend_models()
        assert frontend, "frontend list parsed as empty"
        missing = frontend - set(EXHAUST_FAN_LABEL_MODELS)
        assert not missing, (
            f"models {sorted(missing)} label the fan 'Exhaust' in the UI but the backend "
            f"would report 'Chamber fan' — add them to EXHAUST_FAN_LABEL_MODELS"
        )

    def test_every_backend_display_name_is_handled_by_the_frontend(self):
        from backend.app.utils.printer_models import EXHAUST_FAN_LABEL_MODELS

        # N7/N6 are internal codes that never reach the card, so exclude them.
        internal_codes = {"N7", "N6"}
        backend_display = set(EXHAUST_FAN_LABEL_MODELS) - internal_codes
        missing = backend_display - self._frontend_models()
        assert not missing, (
            f"models {sorted(missing)} say 'Exhaust fan' in the API response but the card "
            f"would still show 'Chamber Fan' — add them to MODELS_WITH_EXHAUST_LABEL"
        )
