"""The tray-change log has to leave the process.

``PrinterState.tray_change_log`` is what the usage tracker splits filament
weight on when AMS filament backup swaps in a fresh spool mid-print. It lived
only in memory, so a restart during a long print erased the segment boundaries
and the whole job got charged to the tray that finished it. The client now
reports every appended entry so main.py can persist it.
"""

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient


def _tray_msg(tray_now: int):
    """A partial AMS update carrying only tray_now, as P-series and H2D send."""
    return {"print": {"ams": {"tray_now": str(tray_now)}}}


class TestTrayChangeCallback:
    @pytest.fixture
    def mqtt_client(self):
        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )
        client._was_running = True
        client._completion_triggered = False
        return client

    def test_every_logged_change_is_reported(self, mqtt_client):
        seen: list[tuple[int, int]] = []
        mqtt_client.on_tray_change = lambda tray, layer: seen.append((tray, layer))

        mqtt_client.state.layer_num = 0
        mqtt_client._process_message(_tray_msg(2))
        mqtt_client.state.layer_num = 670
        mqtt_client._process_message(_tray_msg(254))
        mqtt_client.state.layer_num = 675
        mqtt_client._process_message(_tray_msg(3))

        assert seen == [(2, 0), (254, 670), (3, 675)]
        assert mqtt_client.state.tray_change_log == [(2, 0), (254, 670), (3, 675)]

    def test_repeat_of_the_same_tray_is_not_reported(self, mqtt_client):
        """The printer republishes tray_now on every push; only transitions
        are segment boundaries."""
        seen: list[tuple[int, int]] = []
        mqtt_client.on_tray_change = lambda tray, layer: seen.append((tray, layer))

        mqtt_client._process_message(_tray_msg(2))
        mqtt_client.state.layer_num = 40
        mqtt_client._process_message(_tray_msg(2))

        assert seen == [(2, 0)]

    def test_no_callback_outside_a_running_print(self, mqtt_client):
        seen: list[tuple[int, int]] = []
        mqtt_client.on_tray_change = lambda tray, layer: seen.append((tray, layer))
        mqtt_client._was_running = False

        mqtt_client._process_message(_tray_msg(2))

        assert seen == []
        assert mqtt_client.state.tray_change_log == []

    def test_missing_callback_does_not_break_logging(self, mqtt_client):
        """The callback is optional — the in-memory log still has to work."""
        mqtt_client.on_tray_change = None

        mqtt_client._process_message(_tray_msg(2))

        assert mqtt_client.state.tray_change_log == [(2, 0)]
