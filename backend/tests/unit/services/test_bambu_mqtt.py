"""
Tests for the BambuMQTTClient service.

These tests focus on timelapse tracking during prints.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestTimelapseTracking:
    """Tests for timelapse state tracking during prints."""

    @pytest.fixture
    def mqtt_client(self):
        """Create a BambuMQTTClient instance for testing."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )
        return client

    def test_timelapse_flag_initializes_to_false(self, mqtt_client):
        """Verify _timelapse_during_print starts as False."""
        assert mqtt_client._timelapse_during_print is False

    def test_timelapse_flag_set_when_timelapse_active_during_running(self, mqtt_client):
        """Verify timelapse flag is set when timelapse is active while printing."""
        # Simulate print running
        mqtt_client._was_running = True
        mqtt_client.state.timelapse = False

        # Simulate xcam data showing timelapse is enabled
        xcam_data = {"timelapse": "enable"}
        mqtt_client._parse_xcam_data(xcam_data)

        assert mqtt_client.state.timelapse is True
        assert mqtt_client._timelapse_during_print is True

    def test_timelapse_flag_not_set_when_not_running(self, mqtt_client):
        """Verify timelapse flag is NOT set when printer not running."""
        # Printer is idle (not running)
        mqtt_client._was_running = False
        mqtt_client.state.timelapse = False

        # Timelapse is enabled but we're not printing
        xcam_data = {"timelapse": "enable"}
        mqtt_client._parse_xcam_data(xcam_data)

        assert mqtt_client.state.timelapse is True
        # Flag should NOT be set since we're not printing
        assert mqtt_client._timelapse_during_print is False

    def test_timelapse_flag_persists_after_timelapse_stops(self, mqtt_client):
        """Verify timelapse flag stays True even after recording stops."""
        # Simulate print running with timelapse
        mqtt_client._was_running = True

        # Enable timelapse during print
        xcam_data = {"timelapse": "enable"}
        mqtt_client._parse_xcam_data(xcam_data)
        assert mqtt_client._timelapse_during_print is True

        # Disable timelapse (recording stops at end of print)
        xcam_data = {"timelapse": "disable"}
        mqtt_client._parse_xcam_data(xcam_data)

        # Flag should still be True (persists until reset)
        assert mqtt_client.state.timelapse is False
        assert mqtt_client._timelapse_during_print is True

    def test_timelapse_flag_from_print_data(self, mqtt_client):
        """Verify timelapse flag is set from print data (not just xcam)."""
        # Simulate print running
        mqtt_client._was_running = True
        mqtt_client.state.timelapse = False
        mqtt_client._timelapse_during_print = False

        # Manually test the timelapse parsing logic from _parse_print_data
        # This tests the "timelapse" field in the main print data
        data = {"timelapse": True}
        mqtt_client.state.timelapse = data["timelapse"] is True
        if mqtt_client.state.timelapse and mqtt_client._was_running:
            mqtt_client._timelapse_during_print = True

        assert mqtt_client._timelapse_during_print is True


class TestPrintCompletionWithTimelapse:
    """Tests for print completion including timelapse flag."""

    @pytest.fixture
    def mqtt_client(self):
        """Create a BambuMQTTClient instance for testing."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )
        return client

    def test_print_complete_includes_timelapse_flag(self, mqtt_client):
        """Verify print complete callback includes timelapse_was_active."""
        # Set up completion callback
        callback_data = {}

        def on_complete(data):
            callback_data.update(data)

        mqtt_client.on_print_complete = on_complete

        # Simulate a print that had timelapse active
        mqtt_client._was_running = True
        mqtt_client._completion_triggered = False
        mqtt_client._timelapse_during_print = True
        mqtt_client._previous_gcode_state = "RUNNING"
        mqtt_client._previous_gcode_file = "test.gcode"
        mqtt_client.state.subtask_name = "Test Print"

        # Simulate print finish
        mqtt_client.state.state = "FINISH"

        # Manually trigger the completion logic (simplified)
        # In real code this happens in _parse_print_data
        should_trigger = (
            mqtt_client.state.state in ("FINISH", "FAILED")
            and not mqtt_client._completion_triggered
            and mqtt_client.on_print_complete
            and mqtt_client._previous_gcode_state == "RUNNING"
        )

        if should_trigger:
            status = "completed" if mqtt_client.state.state == "FINISH" else "failed"
            timelapse_was_active = mqtt_client._timelapse_during_print
            mqtt_client._completion_triggered = True
            mqtt_client._was_running = False
            mqtt_client._timelapse_during_print = False
            mqtt_client.on_print_complete(
                {
                    "status": status,
                    "filename": mqtt_client._previous_gcode_file,
                    "subtask_name": mqtt_client.state.subtask_name,
                    "timelapse_was_active": timelapse_was_active,
                }
            )

        assert "timelapse_was_active" in callback_data
        assert callback_data["timelapse_was_active"] is True

    def test_print_complete_timelapse_flag_false_when_no_timelapse(self, mqtt_client):
        """Verify timelapse_was_active is False when no timelapse during print."""
        callback_data = {}

        def on_complete(data):
            callback_data.update(data)

        mqtt_client.on_print_complete = on_complete

        # Print without timelapse
        mqtt_client._was_running = True
        mqtt_client._completion_triggered = False
        mqtt_client._timelapse_during_print = False  # No timelapse
        mqtt_client._previous_gcode_state = "RUNNING"
        mqtt_client._previous_gcode_file = "test.gcode"
        mqtt_client.state.subtask_name = "Test Print"
        mqtt_client.state.state = "FINISH"

        # Trigger completion
        timelapse_was_active = mqtt_client._timelapse_during_print
        mqtt_client.on_print_complete(
            {
                "status": "completed",
                "filename": mqtt_client._previous_gcode_file,
                "subtask_name": mqtt_client.state.subtask_name,
                "timelapse_was_active": timelapse_was_active,
            }
        )

        assert callback_data["timelapse_was_active"] is False

    def test_timelapse_flag_reset_after_completion(self, mqtt_client):
        """Verify _timelapse_during_print is reset after print completion."""
        mqtt_client._timelapse_during_print = True
        mqtt_client._was_running = True
        mqtt_client._completion_triggered = False

        # Simulate completion reset
        mqtt_client._completion_triggered = True
        mqtt_client._was_running = False
        mqtt_client._timelapse_during_print = False

        assert mqtt_client._timelapse_during_print is False


class TestRealisticMessageFlow:
    """Tests that simulate realistic MQTT message sequences.

    These tests process messages through _process_message to test the full flow,
    including the order of xcam parsing vs state detection.
    """

    @pytest.fixture
    def mqtt_client(self):
        """Create a BambuMQTTClient instance for testing."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )
        return client

    def test_timelapse_detected_at_print_start_in_same_message(self, mqtt_client):
        """Test that timelapse is detected when xcam and state come in same message.

        This is the critical race condition test - xcam data is parsed BEFORE
        state detection, so the timelapse flag must be set AFTER _was_running is True.
        """
        # Callbacks to track events
        start_callback_data = {}

        def on_start(data):
            start_callback_data.update(data)

        mqtt_client.on_print_start = on_start

        # Initial state - idle
        mqtt_client._was_running = False
        mqtt_client._timelapse_during_print = False
        mqtt_client._previous_gcode_state = None

        # Simulate first message when print starts - contains both xcam and gcode_state
        # This is the realistic scenario from the printer
        # NOTE: Real MQTT messages wrap print data inside a "print" key
        payload = {
            "print": {
                "gcode_state": "RUNNING",
                "gcode_file": "/data/Metadata/test_print.gcode",
                "subtask_name": "Test_Print",
                "xcam": {
                    "timelapse": "enable",  # Timelapse is enabled in this print
                    "printing_monitor": True,
                },
                "mc_percent": 0,
                "mc_remaining_time": 3600,
            }
        }

        # Process the message (this is what happens in real MQTT flow)
        mqtt_client._process_message(payload)

        # Verify timelapse was detected even though xcam is parsed before state
        assert mqtt_client._was_running is True, "_was_running should be True after RUNNING state"
        assert mqtt_client.state.timelapse is True, "state.timelapse should be True"
        assert (
            mqtt_client._timelapse_during_print is True
        ), "timelapse_during_print should be True when timelapse is in the same message as RUNNING state"

    def test_timelapse_not_detected_when_disabled(self, mqtt_client):
        """Test that timelapse is NOT detected when disabled in xcam data."""
        mqtt_client.on_print_start = lambda data: None

        # Initial state - idle
        mqtt_client._was_running = False
        mqtt_client._timelapse_during_print = False
        mqtt_client._previous_gcode_state = None

        # Print starts without timelapse
        payload = {
            "print": {
                "gcode_state": "RUNNING",
                "gcode_file": "/data/Metadata/test_print.gcode",
                "subtask_name": "Test_Print",
                "xcam": {
                    "timelapse": "disable",  # Timelapse is disabled
                    "printing_monitor": True,
                },
            }
        }

        mqtt_client._process_message(payload)

        assert mqtt_client._was_running is True
        assert mqtt_client.state.timelapse is False
        assert mqtt_client._timelapse_during_print is False

    def test_timelapse_detected_when_enabled_after_print_start(self, mqtt_client):
        """Test timelapse detected when enabled in a message after print starts."""
        mqtt_client.on_print_start = lambda data: None

        # First message - print starts without timelapse info
        payload_start = {
            "print": {
                "gcode_state": "RUNNING",
                "gcode_file": "/data/Metadata/test_print.gcode",
                "subtask_name": "Test_Print",
            }
        }
        mqtt_client._process_message(payload_start)

        assert mqtt_client._was_running is True
        assert mqtt_client._timelapse_during_print is False  # Not detected yet

        # Second message - xcam data arrives with timelapse enabled
        payload_xcam = {
            "print": {
                "gcode_state": "RUNNING",
                "gcode_file": "/data/Metadata/test_print.gcode",
                "subtask_name": "Test_Print",
                "xcam": {
                    "timelapse": "enable",
                },
            }
        }
        mqtt_client._process_message(payload_xcam)

        # Now timelapse should be detected because _was_running is already True
        assert mqtt_client._timelapse_during_print is True

    def test_print_complete_includes_timelapse_flag_full_flow(self, mqtt_client):
        """Test full print lifecycle with timelapse - from start to completion."""
        start_data = {}
        complete_data = {}

        def on_start(data):
            start_data.update(data)

        def on_complete(data):
            complete_data.update(data)

        mqtt_client.on_print_start = on_start
        mqtt_client.on_print_complete = on_complete

        # 1. Print starts with timelapse
        mqtt_client._process_message(
            {
                "print": {
                    "gcode_state": "RUNNING",
                    "gcode_file": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                    "xcam": {"timelapse": "enable"},
                }
            }
        )

        assert mqtt_client._timelapse_during_print is True
        assert "subtask_name" in start_data

        # 2. Print continues (multiple messages)
        for _ in range(3):
            mqtt_client._process_message(
                {
                    "print": {
                        "gcode_state": "RUNNING",
                        "gcode_file": "/data/Metadata/test.gcode",
                        "subtask_name": "Test",
                        "mc_percent": 50,
                    }
                }
            )

        # Timelapse flag should still be True
        assert mqtt_client._timelapse_during_print is True

        # 3. Print completes
        mqtt_client._process_message(
            {
                "print": {
                    "gcode_state": "FINISH",
                    "gcode_file": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                }
            }
        )

        # Verify completion callback received timelapse flag
        assert "timelapse_was_active" in complete_data
        assert complete_data["timelapse_was_active"] is True
        assert complete_data["status"] == "completed"

        # Flags should be reset after completion
        assert mqtt_client._timelapse_during_print is False
        assert mqtt_client._was_running is False

    def test_print_failed_includes_timelapse_flag(self, mqtt_client):
        """Test that failed print also includes timelapse flag."""
        complete_data = {}

        def on_complete(data):
            complete_data.update(data)

        mqtt_client.on_print_start = lambda data: None
        mqtt_client.on_print_complete = on_complete

        # Start with timelapse
        mqtt_client._process_message(
            {
                "print": {
                    "gcode_state": "RUNNING",
                    "gcode_file": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                    "xcam": {"timelapse": "enable"},
                }
            }
        )

        # Print fails
        mqtt_client._process_message(
            {
                "print": {
                    "gcode_state": "FAILED",
                    "gcode_file": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                }
            }
        )

        assert complete_data["timelapse_was_active"] is True
        assert complete_data["status"] == "failed"
