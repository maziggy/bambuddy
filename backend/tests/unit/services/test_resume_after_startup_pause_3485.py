"""A print paused when Bambuddy starts resumes as the same print (#3485).

A power cut takes down the printers and the Bambuddy host together. Bambuddy
comes back while the printers sit paused in power-loss recovery, so the first
state it sees is PAUSE, and the printer's PAUSE -> RUNNING was taken for a new
print: a second archive, left "printing". It is now the restart recovery that
a print found RUNNING at startup already gets (#1304, #1485).
"""

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient

FILE = "chocolate-bite-buddy_plate_2.3mf"


@pytest.fixture
def client():
    client = BambuMQTTClient(ip_address="192.168.1.100", serial_number="TEST123", access_code="12345678")
    client.starts = []
    client.recovered = []
    client.completions = []
    client.on_print_start = client.starts.append
    client.on_print_running_observed = client.recovered.append
    client.on_print_complete = client.completions.append
    return client


def _push(client, state: str, file: str | None = FILE) -> None:
    payload = {"gcode_state": state}
    if file is not None:
        payload.update(gcode_file=file, subtask_name=file)
    client._process_message({"print": payload})


def test_the_resume_is_not_a_new_print(client):
    _push(client, "PAUSE")
    # The resume frame carries no file name, as on the reporter's A1 mini.
    _push(client, "RUNNING", file=None)

    assert client.starts == []
    assert len(client.recovered) == 1
    assert client.recovered[0]["filename"] == FILE


def test_the_resumed_print_still_completes(client):
    _push(client, "PAUSE")
    _push(client, "RUNNING", file=None)
    _push(client, "FINISH")

    assert [c["status"] for c in client.completions] == ["completed"]


def test_a_later_pause_and_resume_is_still_the_same_print(client):
    _push(client, "PAUSE")
    _push(client, "RUNNING", file=None)
    _push(client, "PAUSE")
    _push(client, "RUNNING")

    assert client.starts == []


def test_a_print_after_the_paused_one_ends_is_new(client):
    _push(client, "PAUSE")
    _push(client, "IDLE", file="")
    _push(client, "RUNNING", file="next.3mf")

    assert len(client.starts) == 1
    assert client.starts[0]["filename"] == "next.3mf"


def test_a_next_print_paused_before_it_runs_is_still_new(client):
    _push(client, "PAUSE")
    _push(client, "IDLE", file="")
    _push(client, "PREPARE", file="next.3mf")
    _push(client, "PAUSE", file="next.3mf")
    _push(client, "RUNNING", file="next.3mf")

    assert [s["filename"] for s in client.starts] == ["next.3mf"]


def test_a_print_started_after_startup_is_new(client):
    _push(client, "IDLE", file="")
    _push(client, "RUNNING")

    assert len(client.starts) == 1
    assert client.recovered == []


def test_a_pause_seen_while_running_resumes_quietly(client):
    _push(client, "IDLE", file="")
    _push(client, "RUNNING")
    _push(client, "PAUSE")
    _push(client, "RUNNING")

    assert len(client.starts) == 1
    assert client.recovered == []
