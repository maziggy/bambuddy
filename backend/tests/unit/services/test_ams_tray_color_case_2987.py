"""AMS tray colours must go to the printer in UPPERCASE hex (#2987).

P1S firmware 01.10.00.00 reads every lowercase hex letter in ``tray_color`` as a
zero, and says nothing about it: the command response echoes the value that was
sent and reports ``result: "success"``. Only the next AMS push shows what was
really stored. From the reporter's support bundle, where the spool's ``rgba``
column holds lowercase and went out verbatim:

    sent 09ff00ff  ->  AMS reports 09000000
    sent ff5100ff  ->  AMS reports 00510000
    sent 090000FF  ->  AMS reports 090000FF

The damage is not cosmetic. The auto-unlink sweep asks whether the tray still
matches the spool assigned to it, so the tray Bambuddy had just written stopped
matching the spool that asked for it and the assignment was deleted seconds
later -- which is the disappearing assignment the issue reports.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient, wire_tray_color


def _client(model: str = "P1S") -> BambuMQTTClient:
    client = BambuMQTTClient(ip_address="10.0.0.1", serial_number="P1S2987", access_code="c", model=model)
    client._client = MagicMock()
    client.state.connected = True
    return client


def _sent(client: BambuMQTTClient) -> dict:
    return json.loads(client._client.publish.call_args[0][1])["print"]


def _set(client: BambuMQTTClient, tray_color: str, **overrides):
    kwargs = {
        "ams_id": 0,
        "tray_id": 2,
        "tray_info_idx": "GFSNL03",
        "tray_type": "PLA Matte",
        "tray_sub_brands": "Sunlu PLA Matte",
        "tray_color": tray_color,
        "nozzle_temp_min": 200,
        "nozzle_temp_max": 240,
    }
    kwargs.update(overrides)
    assert client.ams_set_filament_setting(**kwargs)
    return _sent(client)


class TestWireTrayColor:
    @pytest.mark.parametrize(
        ("stored", "expected"),
        [
            pytest.param("09ff00ff", "09FF00FF", id="the-reported-spool"),
            pytest.param("ff5100ff", "FF5100FF", id="the-second-spool-in-the-bundle"),
            pytest.param("090000FF", "090000FF", id="already-uppercase-is-untouched"),
            pytest.param("AbCdEf12", "ABCDEF12", id="mixed-case"),
            pytest.param("  09ff00ff  ", "09FF00FF", id="surrounding-whitespace"),
            pytest.param("#09ff00ff", "09FF00FF", id="css-style-hash-is-stripped"),
        ],
    )
    def test_the_wire_form_is_uppercase_bare_hex(self, stored: str, expected: str):
        assert wire_tray_color(stored) == expected

    @pytest.mark.parametrize("blank", ["", "   ", None])
    def test_a_blank_colour_stays_blank(self, blank):
        """Clearing a slot sends an empty colour; it must not become "NONE"."""
        assert wire_tray_color(blank) == ""

    def test_only_the_case_changes(self):
        """No padding, no alpha invented, no six-to-eight widening -- the
        firmware's own format is whatever the caller resolved, and guessing at
        it here would be a second bug wearing the first one's clothes."""
        assert wire_tray_color("09ff00") == "09FF00"
        assert wire_tray_color("09ff00ff00") == "09FF00FF00"


class TestWhatReachesThePrinter:
    def test_a_lowercase_colour_is_uppercased_on_the_wire(self):
        """The reported case, end to end through the publisher."""
        assert _set(_client(), "09ff00ff")["tray_color"] == "09FF00FF"

    def test_an_uppercase_colour_is_unchanged(self):
        assert _set(_client(), "090000FF")["tray_color"] == "090000FF"

    def test_every_other_field_is_left_alone(self):
        """Only the colour is normalised. tray_type and tray_sub_brands are
        free text the printer stores verbatim, and case carries meaning there --
        "PLA Matte" is a product line, "PLA MATTE" is not."""
        sent = _set(_client(), "09ff00ff")
        assert sent["tray_type"] == "PLA Matte"
        assert sent["tray_sub_brands"] == "Sunlu PLA Matte"
        assert sent["tray_info_idx"] == "GFSNL03"
        assert (sent["nozzle_temp_min"], sent["nozzle_temp_max"]) == (200, 240)

    def test_the_external_spool_is_normalised_too(self):
        """ams_id 255 takes a different branch to build its wire ids, so the
        colour has to be normalised where the command is assembled rather than
        inside any one of them."""
        assert _set(_client("X1C"), "09ff00ff", ams_id=255, tray_id=0)["tray_color"] == "09FF00FF"

    def test_an_ams_ht_is_normalised_too(self):
        assert _set(_client(), "09ff00ff", ams_id=128, tray_id=0)["tray_color"] == "09FF00FF"

    def test_an_a2l_lite_slot_is_normalised_too(self):
        assert _set(_client("A2L"), "09ff00ff", ams_id=6, tray_id=2)["tray_color"] == "09FF00FF"

    def test_a_blank_colour_reaches_the_printer_blank(self):
        assert _set(_client(), "")["tray_color"] == ""
