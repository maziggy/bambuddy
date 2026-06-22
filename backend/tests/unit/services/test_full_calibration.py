"""Unit coverage for the safe native full-printer-calibration action."""

import json
from unittest.mock import MagicMock

import paho.mqtt.client as mqtt
import pytest

from backend.app.services.bambu_mqtt import (
    BambuMQTTClient,
    FullCalibrationInvalidSelectionError,
    FullCalibrationPublishError,
    FullCalibrationUnsupportedError,
    PlateClearConfirmationRequiredError,
    PrinterAlreadyCalibratingError,
    PrinterBusyForCalibrationError,
    PrinterDisconnectedForCalibrationError,
    is_printer_calibrating,
)
from backend.app.services.printer_manager import PrinterManager
from backend.app.utils.printer_models import get_full_calibration_profile, get_supported_calibration_stages


@pytest.fixture
def mqtt_client():
    client = BambuMQTTClient(
        ip_address="192.168.1.100",
        serial_number="TEST123",
        access_code="test-access-code",
        model="P1S",
    )
    client._client = MagicMock()
    client._client.publish.return_value.rc = mqtt.MQTT_ERR_SUCCESS
    client.state.connected = True
    client.state.state = "IDLE"
    return client


@pytest.mark.parametrize(
    ("model", "option"),
    [
        ("X1C", 7),
        ("P1S", 6),
        ("A1 Mini", 14),
        ("P2S", 102),
        ("H2D", 54),
        ("H2S", 102),
        ("O1E", 54),
    ],
)
def test_verified_model_profiles_are_allow_listed(model, option):
    profile = get_full_calibration_profile(model)
    assert profile is not None
    assert profile.option == option


@pytest.mark.parametrize("model", [None, "H2C", "Future Bambu 9000"])
def test_unknown_or_unverified_models_fail_closed(model):
    assert get_full_calibration_profile(model) is None


def test_start_full_calibration_publishes_verified_native_payload_at_qos_1(mqtt_client):
    mqtt_client.start_full_calibration()

    topic, payload = mqtt_client._client.publish.call_args.args
    command = json.loads(payload)
    assert topic == mqtt_client.topic_publish
    assert mqtt_client._client.publish.call_args.kwargs["qos"] == 1
    assert command["print"] == {
        "command": "calibration",
        "sequence_id": "1",
        "option": 6,
    }


def test_p2s_exposes_only_its_verified_native_calibration_stages():
    assert [stage.code for stage in get_supported_calibration_stages("P2S")] == [
        "bed_leveling",
        "vibration_compensation",
        "high_temperature_bed",
        "nozzle_clump_detection",
    ]


def test_selected_stages_publish_only_their_verified_option_bits(mqtt_client):
    mqtt_client.model = "P2S"

    mqtt_client.start_full_calibration(["vibration_compensation", "nozzle_clump_detection"])

    command = json.loads(mqtt_client._client.publish.call_args.args[1])
    assert command["print"]["option"] == 68


def test_start_full_calibration_rejects_disconnected_printer(mqtt_client):
    mqtt_client.state.connected = False

    with pytest.raises(PrinterDisconnectedForCalibrationError):
        mqtt_client.start_full_calibration()
    mqtt_client._client.publish.assert_not_called()


@pytest.mark.parametrize("state", ["RUNNING", "PAUSE", "PREPARE", "SLICING", "UPDATING"])
def test_start_full_calibration_rejects_non_idle_printer(mqtt_client, state):
    mqtt_client.state.state = state

    with pytest.raises(PrinterBusyForCalibrationError):
        mqtt_client.start_full_calibration()
    mqtt_client._client.publish.assert_not_called()


def test_finish_requires_explicit_plate_clear_confirmation(mqtt_client):
    mqtt_client.state.state = "FINISH"

    with pytest.raises(PlateClearConfirmationRequiredError):
        mqtt_client.start_full_calibration(["bed_leveling"])
    mqtt_client._client.publish.assert_not_called()


def test_finish_with_plate_clear_confirmation_publishes_selected_stages(mqtt_client):
    mqtt_client.state.state = "FINISH"

    mqtt_client.start_full_calibration(["bed_leveling"], plate_clear_confirmed=True)

    command = json.loads(mqtt_client._client.publish.call_args.args[1])
    assert command["print"]["option"] == 2


def test_unsupported_or_empty_stage_selection_never_publishes(mqtt_client):
    for stages in ([], ["motor_noise_cancellation"], ["bed_leveling", "bed_leveling"]):
        with pytest.raises(FullCalibrationInvalidSelectionError):
            mqtt_client.start_full_calibration(stages)
    mqtt_client._client.publish.assert_not_called()


def test_start_full_calibration_rejects_existing_calibration(mqtt_client):
    mqtt_client.state.state = "RUNNING"
    mqtt_client.state.gcode_file = "/usr/etc/print/auto_cali_for_user.gcode"
    mqtt_client.state.stg_cur = 3

    with pytest.raises(PrinterAlreadyCalibratingError):
        mqtt_client.start_full_calibration()
    mqtt_client._client.publish.assert_not_called()


def test_start_full_calibration_rejects_unverified_model(mqtt_client):
    mqtt_client.model = "H2C"

    with pytest.raises(FullCalibrationUnsupportedError):
        mqtt_client.start_full_calibration()
    mqtt_client._client.publish.assert_not_called()


def test_start_full_calibration_surfaces_client_publish_failure(mqtt_client):
    mqtt_client._client.publish.return_value.rc = mqtt.MQTT_ERR_NO_CONN

    with pytest.raises(FullCalibrationPublishError):
        mqtt_client.start_full_calibration()


def test_stage_parsing_is_defensive_and_live_calibration_requires_valid_data(mqtt_client):
    mqtt_client.state.state = "RUNNING"
    mqtt_client.state.gcode_file = "/usr/etc/print/auto_cali_for_user.gcode"

    mqtt_client._update_state({"stg_cur": {"not": "a stage"}, "stg": [1, "3", {}, -1, 999]})
    assert mqtt_client.state.stg_cur == -1
    assert mqtt_client.state.stg == [1, 3, -1]
    assert is_printer_calibrating(mqtt_client.state) is False

    mqtt_client._update_state({"stg_cur": "3", "stg": [1, 3]})
    assert mqtt_client.state.stg_cur == 3
    assert is_printer_calibrating(mqtt_client.state) is True

    mqtt_client.state.stg_cur = -1
    assert is_printer_calibrating(mqtt_client.state) is False
    mqtt_client.state.state = "IDLE"
    mqtt_client.state.stg_cur = 3
    assert is_printer_calibrating(mqtt_client.state) is False


def test_manager_delegates_selected_stages_and_plate_confirmation():
    manager = PrinterManager()
    client = MagicMock()
    manager._clients[42] = client

    manager.start_full_calibration(42, ["bed_leveling"], plate_clear_confirmed=True)

    client.start_full_calibration.assert_called_once_with(["bed_leveling"], plate_clear_confirmed=True)


def test_full_calibration_logging_never_includes_the_access_code(mqtt_client, caplog):
    with caplog.at_level("INFO"):
        mqtt_client.start_full_calibration()

    assert "test-access-code" not in caplog.text
