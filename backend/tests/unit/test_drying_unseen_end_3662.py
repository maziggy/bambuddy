"""A drying cycle cut off while the printer was out of sight is no finished cycle (#3662).

The printer lost power mid-cycle and its first report after power came back
read dry_time 0. Against the 668 minutes it last showed, that looked like the
end of a cycle, so the drying auto-off switched the plug off again five
minutes after power-on.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.app.services import bambu_mqtt as mod


@pytest.fixture
def client(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["now"])
    completed: list[int] = []
    ended: list[object] = []
    c = mod.BambuMQTTClient(
        ip_address="192.168.1.100",
        serial_number="TEST-3662",
        access_code="12345678",
        on_drying_complete=completed.append,
        on_drying_cycle_end=ended.append,
    )
    c.state.connected = True
    c._clock, c._completed, c._ended = clock, completed, ended
    return c


def _report(client, dry_time, ams_id=128):
    client._handle_ams_data({"ams": [{"id": str(ams_id), "dry_time": dry_time, "tray": []}]})


def _minutes(client, minutes):
    client._clock["now"] += minutes * 60


def _drop(client):
    client._on_disconnect(None, None, rc=MagicMock(is_failure=True))


def test_a_power_loss_mid_cycle_is_not_a_finished_cycle(client):
    _report(client, 668)
    _drop(client)
    _minutes(client, 31)
    client.state.connected = True
    _report(client, 0)

    assert client._completed == []
    # The cycle is over all the same: its end is recorded, so its spools are
    # judged as for any cycle that ended short.
    assert len(client._ended) == 1


def test_bambuddy_cutting_the_plug_under_a_cycle_is_a_cut_off(client):
    # 3 minutes left, power off for longer: the countdown could have run out,
    # but Bambuddy switched the plug off under it and the connection dropped.
    _report(client, 3)
    client.mark_power_off()
    _drop(client)
    _minutes(client, 30)
    client.state.connected = True
    _report(client, 0)

    assert client._completed == []


def test_a_cycle_that_could_have_finished_unseen_counts_as_finished(client):
    # A network drop near the end of a cycle: it ran out while unseen.
    _report(client, 3)
    _drop(client)
    _minutes(client, 10)
    client.state.connected = True
    _report(client, 0)

    assert client._completed == [128]


def test_a_network_drop_mid_cycle_keeps_the_cycle(client):
    _report(client, 600)
    _drop(client)
    _minutes(client, 5)
    client.state.connected = True
    _report(client, 595)
    assert client._completed == []
    # Nothing about the cycle was forgotten in the gap (#2863).
    assert client._dry_cycles[128]["peak"] == 600
    # Its real end afterwards is a finished cycle again.
    _minutes(client, 595)
    _report(client, 1)
    _report(client, 0)

    assert client._completed == [128]


def test_a_plug_off_the_printer_did_not_feel_changes_nothing(client):
    # The plug did not feed the printer (#2629): it keeps reporting, and its
    # cycle ends normally later.
    _report(client, 3)
    client.mark_power_off()
    _report(client, 2)
    _minutes(client, 2)
    _report(client, 0)

    assert client._completed == [128]


def test_a_stop_on_the_printer_while_watched_is_still_a_finished_cycle(client):
    # No gap: an early end is the firmware's or the user's, as before.
    _report(client, 300)
    _report(client, 0)

    assert client._completed == [128]


def test_each_unit_is_judged_on_its_own_countdown(client):
    client._handle_ams_data({"ams": [{"id": "0", "dry_time": 2, "tray": []}, {"id": "1", "dry_time": 400, "tray": []}]})
    _drop(client)
    _minutes(client, 10)
    client.state.connected = True
    client._handle_ams_data({"ams": [{"id": "0", "dry_time": 0, "tray": []}, {"id": "1", "dry_time": 0, "tray": []}]})

    assert client._completed == [0]


def test_a_plug_off_alone_does_not_make_a_later_end_a_cut_off(client):
    # The connection never dropped after the plug went off, so the power was
    # not cut: the next report decides on the countdown alone.
    _report(client, 3)
    client.mark_power_off()
    _minutes(client, 2)
    _report(client, 0)

    assert client._completed == [128]


def test_a_stale_connection_counts_as_out_of_sight(client, monkeypatch):
    _report(client, 668)
    monkeypatch.setattr(client, "is_stale", lambda: True)
    monkeypatch.setattr(client, "_reset_client_for_reconnect", lambda: None)
    client.check_staleness()
    _minutes(client, 31)
    client.state.connected = True
    _report(client, 0)

    assert client._completed == []


def test_a_forced_reconnect_counts_as_out_of_sight(client):
    # force_reconnect_stale_session closes the socket; its disconnect callback
    # is the one that notes the gap.
    _report(client, 668)
    client._stale_reconnecting = True
    _drop(client)
    _minutes(client, 31)
    client.state.connected = True
    _report(client, 0)

    assert client._completed == []
