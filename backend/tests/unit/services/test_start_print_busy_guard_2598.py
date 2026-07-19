"""start_print() must not publish project_file to a busy printer (#2598).

The firmware rejects a start command while the printer is not idle with
0500_4004 ("Device is busy and cannot start a new task"), and on an A1 mini
that error cancels the RUNNING job. Because every dispatch path (queue
scheduler, manual start, webhook, Virtual-Printer forward) funnels through
BambuMQTTClient.start_print, a run-state guard here covers them all.

IDLE / FINISH / FAILED are valid start targets; only PREPARE / SLICING /
RUNNING / PAUSE are refused.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient


def _connected_client() -> BambuMQTTClient:
    client = BambuMQTTClient(ip_address="127.0.0.1", serial_number="TEST123", access_code="12345678")
    client._client = MagicMock()
    client.state.connected = True
    return client


@pytest.mark.parametrize("busy_state", ["RUNNING", "PREPARE", "PAUSE", "SLICING"])
def test_start_print_refused_when_printer_busy(busy_state):
    client = _connected_client()
    client.state.state = busy_state

    result = client.start_print("job.3mf")

    assert result is False, f"start_print should refuse while {busy_state}"
    client._client.publish.assert_not_called()


@pytest.mark.parametrize("idle_state", ["IDLE", "FINISH", "FAILED"])
def test_start_print_publishes_when_printer_idle(idle_state):
    client = _connected_client()
    client.state.state = idle_state

    result = client.start_print("job.3mf")

    assert result is True, f"start_print should proceed while {idle_state}"
    client._client.publish.assert_called_once()
    topic, payload = client._client.publish.call_args.args[:2]
    assert json.loads(payload)["print"]["command"] == "project_file"


def test_busy_guard_takes_precedence_over_disconnected():
    """A busy printer is refused even if the connection flag is stale/false —
    the guard runs before the connection check, so no publish is attempted."""
    client = _connected_client()
    client.state.connected = False
    client.state.state = "RUNNING"

    assert client.start_print("job.3mf") is False
    client._client.publish.assert_not_called()
