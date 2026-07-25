"""After a drying stop, the firmware can emit straggler AMS reports that
still carry the old dry_time countdown, resurrecting the drying badge in
the UI. The client clamps dry_time to 0 for a short window after a stop it
sent; a new start lifts the clamp immediately.
"""

import time
from unittest.mock import MagicMock

from backend.app.services.bambu_mqtt import BambuMQTTClient


def _client():
    c = BambuMQTTClient(ip_address="10.0.0.1", serial_number="P2S", access_code="c", model="P2S")
    c._client = MagicMock()
    return c


def _push(client, dry_time):
    client._handle_ams_data({"ams": [{"id": 0, "dry_time": dry_time, "humidity": "30", "temp": "40"}]})


def _reported_dry_time(client):
    return int(client.state.raw_data["ams"][0]["dry_time"])


def test_straggler_dry_time_clamped_after_stop():
    c = _client()
    _push(c, 715)
    assert _reported_dry_time(c) == 715
    c.send_drying_command(0, 0, 0, mode=0)
    _push(c, 715)
    assert _reported_dry_time(c) == 0


def test_stop_clamp_fires_drying_complete_once():
    c = _client()
    completions = []
    c.on_drying_complete = completions.append
    _push(c, 715)
    c.send_drying_command(0, 0, 0, mode=0)
    _push(c, 715)
    _push(c, 700)
    assert completions == [0]


def test_new_start_lifts_suppression():
    c = _client()
    c.send_drying_command(0, 0, 0, mode=0)
    c.send_drying_command(0, 65, 8, mode=1, filament="PLA")
    _push(c, 480)
    assert _reported_dry_time(c) == 480


def test_suppression_expires():
    c = _client()
    c.send_drying_command(0, 0, 0, mode=0)
    c._drying_stop_times[0] = time.monotonic() - (BambuMQTTClient.DRYING_STOP_SUPPRESS_SECONDS + 1)
    _push(c, 715)
    assert _reported_dry_time(c) == 715
