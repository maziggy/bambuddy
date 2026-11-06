"""Skipping objects is confirmed by the printer, or the log says it was not.

skip_objects marked the objects skipped as soon as the command was published
and never read the answer. Only the printer's own s_obj list corrected that,
and nothing recorded a skip the printer refused or never applied.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from backend.app.services import bambu_mqtt
from backend.app.services.bambu_mqtt import BambuMQTTClient


@pytest.fixture
def client():
    c = BambuMQTTClient(ip_address="10.0.0.9", serial_number="SN-SKIP", access_code="x", model="X1C")
    c._client = MagicMock()
    c.state.connected = True
    c.state.state = "RUNNING"
    return c


def _sent_seq(c) -> str:
    import json

    return json.loads(c._client.publish.call_args.args[1])["print"]["sequence_id"]


def test_a_refusal_is_logged_and_undone(client, caplog):
    assert client.skip_objects([5, 6])
    seq = _sent_seq(client)

    with caplog.at_level(logging.ERROR):
        client._process_message(
            {"print": {"command": "skip_objects", "sequence_id": seq, "result": "fail", "reason": "busy"}}
        )

    assert client.state.skipped_objects == []
    assert any("refused" in r.message and "busy" in r.message for r in caplog.records)


def test_the_printers_list_confirms_it(client, caplog):
    with patch.object(bambu_mqtt.time, "monotonic", return_value=1_000.0):
        client.skip_objects([5])
        client._process_message({"print": {"command": "push_status", "s_obj": [5]}})

    with (
        patch.object(bambu_mqtt.time, "monotonic", return_value=1_000.0 + bambu_mqtt.SKIP_CONFIRM_TIMEOUT_S + 1),
        caplog.at_level(logging.ERROR),
    ):
        client._process_message({"print": {"command": "push_status", "mc_percent": 50}})

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_no_confirmation_is_logged_once(client, caplog):
    with patch.object(bambu_mqtt.time, "monotonic", return_value=1_000.0):
        client.skip_objects([7])

    with (
        patch.object(bambu_mqtt.time, "monotonic", return_value=1_000.0 + bambu_mqtt.SKIP_CONFIRM_TIMEOUT_S + 1),
        caplog.at_level(logging.ERROR),
    ):
        client._process_message({"print": {"command": "push_status", "mc_percent": 50}})
        client._process_message({"print": {"command": "push_status", "mc_percent": 51}})

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "did not confirm" in errors[0].message


def test_a_print_that_ends_first_logs_nothing(client, caplog):
    with patch.object(bambu_mqtt.time, "monotonic", return_value=1_000.0):
        client.skip_objects([7])
    client._process_message({"print": {"command": "push_status", "gcode_state": "FINISH"}})

    with (
        patch.object(bambu_mqtt.time, "monotonic", return_value=1_000.0 + bambu_mqtt.SKIP_CONFIRM_TIMEOUT_S + 1),
        caplog.at_level(logging.ERROR),
    ):
        client._process_message({"print": {"command": "push_status", "mc_percent": 0}})

    assert not [r for r in caplog.records if "confirm" in r.message]
