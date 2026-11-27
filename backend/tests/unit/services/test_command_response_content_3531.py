"""#3531: the debug log records the content of commands the printer reports back.

The H3C touchscreen's Unload All is one command, ``multi_slot_ams_change_filament``,
whose fields are not documented anywhere. The printer reports it back like every
command, but the debug log kept only its name. push_status (the status itself)
and project_file (which may carry a signed download URL) stay name-only.
"""

import logging

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient


@pytest.fixture
def mqtt_client():
    return BambuMQTTClient(ip_address="192.168.1.100", serial_number="TEST123", access_code="12345678")


def _content_lines(caplog):
    return [r.getMessage() for r in caplog.records if " content: " in r.getMessage()]


def test_a_reported_command_is_logged_with_its_fields(mqtt_client, caplog):
    caplog.set_level(logging.DEBUG, logger="backend.app.services.bambu_mqtt")

    mqtt_client._process_message(
        {"print": {"command": "multi_slot_ams_change_filament", "sequence_id": "7", "slots": [{"ams_id": 128}]}}
    )

    lines = _content_lines(caplog)
    assert len(lines) == 1
    assert "multi_slot_ams_change_filament content:" in lines[0]
    assert '"slots": [{"ams_id": 128}]' in lines[0]


@pytest.mark.parametrize("command", ["push_status", "project_file"])
def test_status_and_print_start_stay_name_only(mqtt_client, caplog, command):
    caplog.set_level(logging.DEBUG, logger="backend.app.services.bambu_mqtt")

    mqtt_client._process_message({"print": {"command": command, "url": "https://example.invalid/signed?token=x"}})

    assert _content_lines(caplog) == []


def test_long_content_is_capped(mqtt_client, caplog):
    caplog.set_level(logging.DEBUG, logger="backend.app.services.bambu_mqtt")

    mqtt_client._process_message({"print": {"command": "gcode_line", "param": "G1 X1\n" * 2000}})

    (line,) = _content_lines(caplog)
    assert len(line.split(" content: ", 1)[1]) == 4000
