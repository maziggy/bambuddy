"""A print start the printer refused is logged, and its answer is not read as
an accepted print (FW-C1).

The printer answers a ``project_file`` command on the report topic. A refusal
went unlogged, so a support bundle showed nothing of it; and its job id was
copied into the state like any status field, which the start watchdog reads
as "the printer took the file" and so waited out its full 180 s before giving
up.
"""

import logging

from backend.app.services.bambu_mqtt import BambuMQTTClient


def _client() -> BambuMQTTClient:
    client = BambuMQTTClient(ip_address="10.0.0.9", serial_number="SN-FW", access_code="x", model="X1C")
    client.state.subtask_id = "100"
    client.state.subtask_name = "previous"
    return client


def _ack(result: str) -> dict:
    return {
        "print": {
            "command": "project_file",
            "result": result,
            "reason": "file not found",
            "subtask_id": "200",
            "subtask_name": "refused",
            "url": "ftp://refused.3mf",
        }
    }


def test_a_refusal_is_logged_and_changes_no_job(caplog):
    client = _client()

    with caplog.at_level(logging.INFO, logger="backend.app.services.bambu_mqtt"):
        client._process_message(_ack("FAIL"))

    assert (client.state.subtask_id, client.state.subtask_name) == ("100", "previous")
    assert any("refused" in r.getMessage() and "FAIL" in r.getMessage() for r in caplog.records)


def test_an_accepted_start_still_names_its_job():
    client = _client()

    client._process_message(_ack("SUCCESS"))

    assert (client.state.subtask_id, client.state.subtask_name) == ("200", "refused")


def test_a_refusal_is_remembered_until_the_next_start_is_sent():
    from unittest.mock import MagicMock

    client = _client()
    client._process_message(_ack("FAIL"))
    assert client.last_start_refusal == ("FAIL", "file not found")

    client._client = MagicMock()
    client.state.connected = True
    client.start_print("next.3mf")

    assert client.last_start_refusal is None


def test_success_in_lower_case_is_not_a_refusal():
    client = _client()

    client._process_message(_ack("success"))

    assert client.last_start_refusal is None
    assert client.state.subtask_id == "200"
