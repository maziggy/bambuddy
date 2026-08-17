"""Telling our own print dispatch from a slicer's (#2843 follow-up).

The old test was ``sequence_id != "20000"``, on the belief that 20000 was
Bambuddy's alone. Measured on the wire 2026-08-17: OrcaSlicer dispatched
``20000`` and then ``20001``, while BambuStudio was on ``20009`` / ``20010`` --
both counting up from the same base, which is also the value
``virtual_printer/bind_server`` documents the slicer sending during detect. So
the check swallowed whichever slicer dispatch landed on 20000, which after a
slicer restart is the first one.

It matters beyond the log line: counting Studio-versus-Orca dispatches across
support bundles is how the size of the internal-storage problem gets measured,
and an undercount there is silent.
"""

import json
import logging

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient


@pytest.fixture
def client():
    return BambuMQTTClient(ip_address="192.168.1.100", serial_number="TEST123", access_code="12345678")


def _project_file(sequence_id, file="Cube.gcode.3mf", url="ftp://Cube.gcode.3mf", subtask="Cube"):
    return {
        "print": {
            "sequence_id": sequence_id,
            "command": "project_file",
            "file": file,
            "url": url,
            "subtask_name": subtask,
            "ams_mapping": [0],
        }
    }


def _external_lines(caplog):
    return [r for r in caplog.records if "External project_file payload" in r.getMessage()]


class TestSlicerDispatchIsReported:
    @pytest.mark.parametrize("seq", ["20000", "20001", "20009", "20010"])
    def test_every_slicer_sequence_id_is_logged(self, client, caplog, seq):
        """20000 included -- that is the one the old check threw away."""
        with caplog.at_level(logging.INFO, logger="backend.app.services.bambu_mqtt"):
            client._handle_request_message(_project_file(seq))

        assert len(_external_lines(caplog)) == 1

    def test_the_payload_is_logged_verbatim(self, client, caplog):
        """It exists to be diffed against ours, so it has to be complete."""
        with caplog.at_level(logging.INFO, logger="backend.app.services.bambu_mqtt"):
            client._handle_request_message(_project_file("20000"))

        logged = json.loads(_external_lines(caplog)[0].getMessage().split("payload: ", 1)[1])
        assert logged["url"] == "ftp://Cube.gcode.3mf"
        assert logged["sequence_id"] == "20000"


class TestOwnDispatchIsNotReported:
    def test_our_own_echo_is_recognised(self, client, caplog):
        """Bambuddy publishes to the topic it subscribes to, so it sees its own
        dispatch come back."""
        ours = _project_file("20000", url="ftp://MyPrint.3mf", file="MyPrint.3mf", subtask="MyPrint")
        client._own_project_file_key = client._project_file_key(ours["print"])

        with caplog.at_level(logging.INFO, logger="backend.app.services.bambu_mqtt"):
            client._handle_request_message(ours)

        assert _external_lines(caplog) == []

    def test_the_marker_is_consumed(self, client, caplog):
        """One-shot. A second identical dispatch is somebody else's -- a reprint
        from the slicer of the same file must not hide behind our last one."""
        ours = _project_file("20000")
        client._own_project_file_key = client._project_file_key(ours["print"])

        with caplog.at_level(logging.INFO, logger="backend.app.services.bambu_mqtt"):
            client._handle_request_message(ours)
            client._handle_request_message(ours)

        assert len(_external_lines(caplog)) == 1

    def test_a_slicer_sharing_our_sequence_id_is_still_reported(self, client, caplog):
        """The exact collision that motivated this: same 20000, different file."""
        ours = _project_file("20000", file="Ours.3mf", url="ftp://Ours.3mf", subtask="Ours")
        client._own_project_file_key = client._project_file_key(ours["print"])

        with caplog.at_level(logging.INFO, logger="backend.app.services.bambu_mqtt"):
            client._handle_request_message(_project_file("20000", file="Theirs.3mf", url="brtc://emmc/Theirs.3mf"))

        assert len(_external_lines(caplog)) == 1


class TestUnaffectedBehaviour:
    def test_the_project_url_is_captured_either_way(self, client):
        """The storage verdict must not depend on who dispatched -- it is read
        before the ours/theirs test and drives whether the FTPS sweep runs."""
        ours = _project_file("20000", url="ftp://Ours.3mf", file="Ours.3mf", subtask="Ours")
        client._own_project_file_key = client._project_file_key(ours["print"])
        client._handle_request_message(ours)
        assert client.state.current_project_url == "ftp://Ours.3mf"

        client._handle_request_message(_project_file("20009", url="brtc://emmc/Theirs.3mf"))
        assert client.state.current_project_url == "brtc://emmc/Theirs.3mf"

    def test_ams_mapping_is_captured_either_way(self, client):
        ours = _project_file("20000")
        client._own_project_file_key = client._project_file_key(ours["print"])
        client._handle_request_message(ours)
        assert client._captured_ams_mapping == [0]
