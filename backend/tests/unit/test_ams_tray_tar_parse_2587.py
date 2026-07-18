"""_handle_ams_data must capture tray_tar / tray_pre for the runout UI (#2587).

The firmware reports the slot a paused print now expects (``tray_tar``) and the
slot loaded before (``tray_pre``) alongside ``tray_now``. Bambuddy historically
parsed only ``tray_now`` and dropped the other two, so "which slot does the
print now expect" never reached the API. These tests lock in that the raw values
are stored on PrinterState (globalisation happens later, at the API boundary).
"""

from unittest.mock import MagicMock, patch

from backend.app.services.bambu_mqtt import BambuMQTTClient


def _client() -> BambuMQTTClient:
    return BambuMQTTClient(ip_address="10.0.0.1", serial_number="SERIAL", access_code="code", model="P1S")


def _ams_frame(**extra):
    frame = {
        "ams": [
            {"id": 0, "tray": [{"id": 0}, {"id": 1}, {"id": 2}, {"id": 3}]},
        ],
    }
    frame.update(extra)
    return frame


class TestTrayTarPreCapture:
    def test_reporter_pause_values_are_stored(self):
        client = _client()
        client.state.state = "PAUSE"
        # @Jostxxl's capture: ran out in slot 2 (tray_pre=1), expects slot 3 (tray_tar=2).
        client._handle_ams_data(_ams_frame(tray_now=255, tray_tar=2, tray_pre=1))
        assert client.state.tray_tar == 2
        assert client.state.tray_pre == 1

    def test_string_values_are_coerced(self):
        client = _client()
        client._handle_ams_data(_ams_frame(tray_tar="2", tray_pre="1"))
        assert client.state.tray_tar == 2
        assert client.state.tray_pre == 1

    def test_defaults_untouched_when_absent(self):
        client = _client()
        # A frame without tray_tar/tray_pre leaves the 255 sentinel in place.
        client._handle_ams_data(_ams_frame(tray_now=3))
        assert client.state.tray_tar == 255
        assert client.state.tray_pre == 255

    def test_unparseable_value_falls_back_to_sentinel(self):
        client = _client()
        client._handle_ams_data(_ams_frame(tray_tar="not-a-number"))
        assert client.state.tray_tar == 255

    def test_change_while_paused_is_logged(self):
        client = _client()
        client.state.state = "PAUSE"
        client.state.tray_tar = 255
        with patch("backend.app.services.bambu_mqtt.logger") as log:
            client._handle_ams_data(_ams_frame(tray_tar=2, tray_pre=1))
        logged = " ".join(str(c) for c in log.info.call_args_list)
        assert "tray_tar" in logged and "#2587" in logged

    def test_no_log_when_not_paused(self):
        client = _client()
        client.state.state = "RUNNING"
        client.state.tray_tar = 255
        with patch("backend.app.services.bambu_mqtt.logger") as log:
            client._handle_ams_data(_ams_frame(tray_tar=2))
        # A healthy print's tar churn must not spam the log.
        for call in log.info.call_args_list:
            assert "#2587" not in " ".join(str(a) for a in call.args)
