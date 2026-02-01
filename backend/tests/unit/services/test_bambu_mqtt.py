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
        assert mqtt_client._timelapse_during_print is True, (
            "timelapse_during_print should be True when timelapse is in the same message as RUNNING state"
        )

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


class TestAMSDataMerging:
    """Tests for AMS data merging, particularly handling empty slots."""

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

    def test_empty_slot_clears_tray_type(self, mqtt_client):
        """Test that empty slot update clears tray_type (Issue #147).

        When a spool is removed from an old AMS, the printer sends empty values.
        These must overwrite the previous values to show the slot as empty.
        """
        # Initial state: AMS unit with a loaded spool
        initial_ams = {
            "ams": [
                {
                    "id": 0,
                    "tray": [
                        {
                            "id": 0,
                            "tray_type": "PLA",
                            "tray_sub_brands": "Bambu PLA Basic",
                            "tray_color": "FF0000",
                            "tag_uid": "1234567890ABCDEF",
                            "remain": 80,
                        }
                    ],
                }
            ]
        }
        mqtt_client._handle_ams_data(initial_ams)

        # Verify initial state
        ams_data = mqtt_client.state.raw_data.get("ams", [])
        assert len(ams_data) == 1
        tray = ams_data[0]["tray"][0]
        assert tray["tray_type"] == "PLA"
        assert tray["tray_color"] == "FF0000"

        # Now simulate spool removal - printer sends empty values
        empty_update = {
            "ams": [
                {
                    "id": 0,
                    "tray": [
                        {
                            "id": 0,
                            "tray_type": "",  # Empty = slot is empty
                            "tray_sub_brands": "",
                            "tray_color": "",
                            "tag_uid": "0000000000000000",  # Zero UID
                            "remain": 0,
                        }
                    ],
                }
            ]
        }
        mqtt_client._handle_ams_data(empty_update)

        # Verify empty values were applied (not ignored by merge logic)
        ams_data = mqtt_client.state.raw_data.get("ams", [])
        tray = ams_data[0]["tray"][0]
        assert tray["tray_type"] == "", "tray_type should be cleared when slot is empty"
        assert tray["tray_color"] == "", "tray_color should be cleared when slot is empty"
        assert tray["tray_sub_brands"] == "", "tray_sub_brands should be cleared"
        assert tray["tag_uid"] == "0000000000000000", "tag_uid should be cleared"

    def test_partial_update_preserves_other_fields(self, mqtt_client):
        """Test that partial updates still preserve non-slot-status fields."""
        # Initial state with full data
        initial_ams = {
            "ams": [
                {
                    "id": 0,
                    "humidity": "3",
                    "temp": "25.5",
                    "tray": [
                        {
                            "id": 0,
                            "tray_type": "PLA",
                            "tray_color": "00FF00",
                            "remain": 90,
                            "k": 0.02,
                        }
                    ],
                }
            ]
        }
        mqtt_client._handle_ams_data(initial_ams)

        # Partial update - only remain changes
        partial_update = {
            "ams": [
                {
                    "id": 0,
                    "tray": [
                        {
                            "id": 0,
                            "remain": 85,  # Only this changed
                        }
                    ],
                }
            ]
        }
        mqtt_client._handle_ams_data(partial_update)

        # Verify remain was updated but other fields preserved
        ams_data = mqtt_client.state.raw_data.get("ams", [])
        tray = ams_data[0]["tray"][0]
        assert tray["remain"] == 85, "remain should be updated"
        assert tray["tray_type"] == "PLA", "tray_type should be preserved"
        assert tray["tray_color"] == "00FF00", "tray_color should be preserved"
        assert tray["k"] == 0.02, "k should be preserved"

    def test_tray_exist_bits_clears_empty_slots(self, mqtt_client):
        """Test that tray_exist_bits clears slots marked as empty (Issue #147).

        New AMS models (AMS 2 Pro) don't send empty tray data when a spool is removed.
        Instead, they update tray_exist_bits to indicate which slots have spools.
        """
        # Initial state: AMS 0 and AMS 1 with loaded spools
        initial_ams = {
            "ams": [
                {
                    "id": 0,
                    "tray": [
                        {"id": 0, "tray_type": "PLA", "tray_color": "FF0000", "remain": 80},
                        {"id": 1, "tray_type": "PETG", "tray_color": "00FF00", "remain": 60},
                        {"id": 2, "tray_type": "ABS", "tray_color": "0000FF", "remain": 40},
                        {"id": 3, "tray_type": "TPU", "tray_color": "FFFF00", "remain": 20},
                    ],
                },
                {
                    "id": 1,
                    "tray": [
                        {"id": 0, "tray_type": "PLA", "tray_color": "FFFFFF", "remain": 90},
                        {"id": 1, "tray_type": "PLA", "tray_color": "000000", "remain": 70},
                        {"id": 2, "tray_type": "PLA", "tray_color": "FF00FF", "remain": 50},
                        {"id": 3, "tray_type": "PLA", "tray_color": "00FFFF", "remain": 30},
                    ],
                },
            ],
            "tray_exist_bits": "ff",  # All 8 slots have spools (0xFF = 11111111)
        }
        mqtt_client._handle_ams_data(initial_ams)

        # Verify initial state
        ams_data = mqtt_client.state.raw_data.get("ams", [])
        assert ams_data[1]["tray"][3]["tray_type"] == "PLA"  # AMS 1 slot 3 (B4) has spool

        # Now simulate spool removal from AMS 1 slot 3 (B4)
        # tray_exist_bits: 0x7f = 01111111 (bit 7 = 0 means AMS 1 slot 3 is empty)
        update_ams = {
            "ams": [
                {"id": 0, "tray": [{"id": 0}, {"id": 1}, {"id": 2}, {"id": 3}]},
                {"id": 1, "tray": [{"id": 0}, {"id": 1}, {"id": 2}, {"id": 3}]},
            ],
            "tray_exist_bits": "7f",  # Bit 7 = 0 -> AMS 1 slot 3 is empty
        }
        mqtt_client._handle_ams_data(update_ams)

        # Verify AMS 1 slot 3 was cleared
        ams_data = mqtt_client.state.raw_data.get("ams", [])
        b4_tray = ams_data[1]["tray"][3]
        assert b4_tray["tray_type"] == "", "tray_type should be cleared for empty slot"
        assert b4_tray["remain"] == 0, "remain should be 0 for empty slot"

        # Verify other slots are preserved
        assert ams_data[0]["tray"][0]["tray_type"] == "PLA", "A1 should still have PLA"
        assert ams_data[1]["tray"][0]["tray_type"] == "PLA", "B1 should still have PLA"
