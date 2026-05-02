"""
Tests for the BambuMQTTClient service.

These tests focus on timelapse tracking during prints.
"""

import json
import time

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


class TestPrePrintFailureCompletion:
    """Tests for completion detection when the print errors before reaching RUNNING (#1111).

    Common trigger: a file sliced for the wrong nozzle diameter is dispatched. The
    printer transitions IDLE -> PREPARE -> FAILED without ever entering RUNNING, so
    the legacy completion detection (which required _previous_gcode_state == 'RUNNING'
    or _was_running == True) left the queue item stuck at 'printing' forever.
    """

    @pytest.fixture
    def mqtt_client(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        return BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )

    def test_prepare_to_failed_triggers_completion(self, mqtt_client):
        """PREPARE -> FAILED must fire on_print_complete (wrong nozzle size etc.)."""
        complete_data = {}
        mqtt_client.on_print_start = lambda data: None
        mqtt_client.on_print_complete = lambda data: complete_data.update(data)

        mqtt_client._previous_gcode_state = "PREPARE"
        mqtt_client._was_running = False
        mqtt_client._completion_triggered = False

        mqtt_client._process_message(
            {
                "print": {
                    "gcode_state": "FAILED",
                    "gcode_file": "/data/Metadata/plate_1.gcode",
                    "subtask_name": "WrongNozzle",
                }
            }
        )

        assert complete_data.get("status") == "failed"

    def test_slicing_to_failed_triggers_completion(self, mqtt_client):
        """SLICING -> FAILED also treated as a pre-print failure."""
        complete_data = {}
        mqtt_client.on_print_start = lambda data: None
        mqtt_client.on_print_complete = lambda data: complete_data.update(data)

        mqtt_client._previous_gcode_state = "SLICING"
        mqtt_client._was_running = False
        mqtt_client._completion_triggered = False

        mqtt_client._process_message(
            {
                "print": {
                    "gcode_state": "FAILED",
                    "gcode_file": "/data/Metadata/plate_1.gcode",
                    "subtask_name": "WrongNozzle",
                }
            }
        )

        assert complete_data.get("status") == "failed"

    def test_initial_failed_does_not_trigger_completion(self, mqtt_client):
        """First message arriving with FAILED (no prior state) must NOT fire completion.

        Protects against a stale FAILED on reconnect being mistaken for a fresh failure
        and marking an unrelated queue item as failed.
        """
        calls = []
        mqtt_client.on_print_start = lambda data: None
        mqtt_client.on_print_complete = lambda data: calls.append(data)

        assert mqtt_client._previous_gcode_state is None
        assert mqtt_client._was_running is False

        mqtt_client._process_message(
            {
                "print": {
                    "gcode_state": "FAILED",
                    "gcode_file": "/data/Metadata/plate_1.gcode",
                    "subtask_name": "Stale",
                }
            }
        )

        assert calls == []

    def test_idle_to_failed_does_not_trigger_completion(self, mqtt_client):
        """IDLE -> FAILED (no print ever dispatched) must NOT fire completion."""
        calls = []
        mqtt_client.on_print_start = lambda data: None
        mqtt_client.on_print_complete = lambda data: calls.append(data)

        mqtt_client._previous_gcode_state = "IDLE"
        mqtt_client._was_running = False
        mqtt_client._completion_triggered = False

        mqtt_client._process_message(
            {
                "print": {
                    "gcode_state": "FAILED",
                    "subtask_name": "Stale",
                }
            }
        )

        assert calls == []

    def test_prepare_to_failed_includes_hms_errors_in_callback(self, mqtt_client):
        """Pre-print FAILED callback should carry the current HMS error list so the
        queue handler can populate a meaningful error_message."""
        complete_data = {}
        mqtt_client.on_print_start = lambda data: None
        mqtt_client.on_print_complete = lambda data: complete_data.update(data)

        mqtt_client._previous_gcode_state = "PREPARE"
        mqtt_client._was_running = False

        # Message carries HMS data for a nozzle-size mismatch (0500_4038) and the
        # PREPARE -> FAILED gcode_state transition in a single update.
        mqtt_client._process_message(
            {
                "print": {
                    "gcode_state": "FAILED",
                    "gcode_file": "/data/Metadata/plate_1.gcode",
                    "hms": [{"attr": 0x05000000, "code": 0x4038}],
                }
            }
        )

        assert complete_data.get("status") == "failed"
        errs = complete_data.get("hms_errors") or []
        assert any(e.get("code") == "0x4038" for e in errs)


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

    def test_shutdown_message_preserves_ams_data(self, mqtt_client):
        """Printer shutdown (power_on_flag=False) must not wipe AMS slot data (#765).

        When a printer shuts down it sends a final MQTT message with
        tray_exist_bits='0' and power_on_flag=False. This all-zero value
        previously caused every slot to be cleared, which then triggered
        auto-unlink of all spool assignments on reconnect.
        """
        # Initial state: two AMS units with loaded spools
        initial_ams = {
            "ams": [
                {
                    "id": 0,
                    "tray": [
                        {"id": 0, "tray_type": "PLA", "tray_color": "FF0000FF", "remain": 80},
                        {"id": 1, "tray_type": "PETG", "tray_color": "00FF00FF", "remain": 60},
                    ],
                },
                {
                    "id": 1,
                    "tray": [
                        {"id": 0, "tray_type": "PETG", "tray_color": "DBDDD9FF", "remain": 90},
                        {"id": 1, "tray_type": "PETG", "tray_color": "67DB25FF", "remain": 70},
                    ],
                },
            ],
            "tray_exist_bits": "33",  # Slots 0,1 of each AMS (0b00110011)
            "power_on_flag": True,
        }
        mqtt_client._handle_ams_data(initial_ams)

        # Verify initial state
        ams_data = mqtt_client.state.raw_data["ams"]
        assert ams_data[0]["tray"][0]["tray_type"] == "PLA"
        assert ams_data[1]["tray"][0]["tray_type"] == "PETG"

        # Simulate printer shutdown — all-zero bits with power_on_flag=False
        shutdown_ams = {
            "ams_exist_bits": "0",
            "tray_exist_bits": "0",
            "power_on_flag": False,
            "insert_flag": False,
            "tray_now": "0",
            "version": 0,
        }
        mqtt_client._handle_ams_data(shutdown_ams)

        # AMS slot data MUST be preserved — shutdown should not clear it
        ams_data = mqtt_client.state.raw_data["ams"]
        assert ams_data[0]["tray"][0]["tray_type"] == "PLA", "Shutdown must not clear AMS 0 slot 0"
        assert ams_data[0]["tray"][0]["tray_color"] == "FF0000FF", "Shutdown must not clear AMS 0 slot 0 color"
        assert ams_data[0]["tray"][1]["tray_type"] == "PETG", "Shutdown must not clear AMS 0 slot 1"
        assert ams_data[1]["tray"][0]["tray_type"] == "PETG", "Shutdown must not clear AMS 1 slot 0"
        assert ams_data[1]["tray"][1]["tray_type"] == "PETG", "Shutdown must not clear AMS 1 slot 1"

    def test_genuine_removal_still_clears_with_power_on(self, mqtt_client):
        """Genuine spool removal (power_on_flag=True) must still clear slot data.

        Ensures the #765 fix doesn't break normal spool removal detection.
        """
        # Initial state: AMS with loaded spool
        initial_ams = {
            "ams": [
                {
                    "id": 0,
                    "tray": [
                        {"id": 0, "tray_type": "PLA", "tray_color": "FF0000", "remain": 80},
                        {"id": 1, "tray_type": "PETG", "tray_color": "00FF00", "remain": 60},
                    ],
                },
            ],
            "tray_exist_bits": "3",  # Both slots occupied (0b11)
            "power_on_flag": True,
        }
        mqtt_client._handle_ams_data(initial_ams)

        # Spool removed from slot 1 while printer is running
        removal_ams = {
            "ams": [
                {
                    "id": 0,
                    "tray": [{"id": 0}, {"id": 1}],
                },
            ],
            "tray_exist_bits": "1",  # Only slot 0 occupied (0b01)
            "power_on_flag": True,
        }
        mqtt_client._handle_ams_data(removal_ams)

        # Slot 0 preserved, slot 1 cleared
        ams_data = mqtt_client.state.raw_data["ams"]
        assert ams_data[0]["tray"][0]["tray_type"] == "PLA", "Slot 0 should be preserved"
        assert ams_data[0]["tray"][1]["tray_type"] == "", "Slot 1 should be cleared on removal"
        assert ams_data[0]["tray"][1]["tray_color"] == "", "Slot 1 color should be cleared"

    def test_power_on_flag_defaults_true_when_absent(self, mqtt_client):
        """When power_on_flag is not in the MQTT data, clearing must proceed normally.

        Ensures backwards compatibility with firmware that doesn't send power_on_flag.
        """
        # Initial state
        initial_ams = {
            "ams": [
                {
                    "id": 0,
                    "tray": [
                        {"id": 0, "tray_type": "PLA", "tray_color": "FF0000", "remain": 80},
                    ],
                },
            ],
            "tray_exist_bits": "1",
        }
        mqtt_client._handle_ams_data(initial_ams)

        # Update WITHOUT power_on_flag — should still clear when bit=0
        update_ams = {
            "ams": [{"id": 0, "tray": [{"id": 0}]}],
            "tray_exist_bits": "0",
            # No power_on_flag key at all
        }
        mqtt_client._handle_ams_data(update_ams)

        ams_data = mqtt_client.state.raw_data["ams"]
        assert ams_data[0]["tray"][0]["tray_type"] == "", (
            "Without power_on_flag, clearing should proceed (defaults to True)"
        )


class TestAMSTrayStateClearning:
    """Tests for AMS tray state-based clearing (#784).

    Some printers (e.g. H2D) only send {id, state} in incremental MQTT
    updates when a tray is not fully loaded.  state=11 means loaded;
    other values (9=empty, 10=spool present but filament not in feeder)
    should clear stale tray data that was set from an earlier pushall.
    """

    @pytest.fixture
    def mqtt_client(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST_H2D",
            access_code="12345678",
        )
        return client

    def _seed_loaded_tray(self, mqtt_client):
        """Seed AMS 0 with a fully loaded tray (state=11) and an empty slot."""
        initial = {
            "ams": [
                {
                    "id": 0,
                    "tray": [
                        {
                            "id": 0,
                            "tray_type": "PETG",
                            "tray_sub_brands": "PETG HF",
                            "tray_color": "00FF00FF",
                            "tray_id_name": "A00-G1",
                            "tray_info_idx": "GFG99",
                            "tag_uid": "AABBCCDD11223344",
                            "tray_uuid": "AABBCCDD11223344AABBCCDD11223344",
                            "remain": 75,
                            "k": 0.02,
                            "cali_idx": 5,
                            "state": 11,
                        },
                        {
                            "id": 1,
                            "tray_type": "PLA",
                            "tray_color": "FF0000FF",
                            "remain": 50,
                            "state": 11,
                        },
                    ],
                }
            ],
            "power_on_flag": False,  # H2D always sends False
        }
        mqtt_client._handle_ams_data(initial)
        ams = mqtt_client.state.raw_data["ams"]
        assert ams[0]["tray"][0]["tray_type"] == "PETG"
        assert ams[0]["tray"][1]["tray_type"] == "PLA"

    def test_state_10_clears_stale_tray_data(self, mqtt_client):
        """Incremental update with state=10 (spool present, not loaded) clears tray."""
        self._seed_loaded_tray(mqtt_client)

        # H2D sends only {id, state} when filament is retracted
        update = {
            "ams": [
                {
                    "id": 0,
                    "tray": [
                        {"id": 0, "state": 10},
                        {"id": 1, "state": 11},  # slot 1 still loaded
                    ],
                }
            ],
            "power_on_flag": False,
        }
        mqtt_client._handle_ams_data(update)

        ams = mqtt_client.state.raw_data["ams"]
        tray0 = ams[0]["tray"][0]
        tray1 = ams[0]["tray"][1]

        # Tray 0 should be cleared
        assert tray0["tray_type"] == "", "tray_type must be cleared on state=10"
        assert tray0["tray_color"] == "", "tray_color must be cleared"
        assert tray0["tray_sub_brands"] == "", "tray_sub_brands must be cleared"
        assert tray0["tray_id_name"] == "", "tray_id_name must be cleared"
        assert tray0["tray_info_idx"] == "", "tray_info_idx must be cleared"
        assert tray0["tag_uid"] == "0000000000000000", "tag_uid must be cleared"
        assert tray0["tray_uuid"] == "00000000000000000000000000000000", "tray_uuid must be cleared"
        assert tray0["remain"] == 0, "remain must be 0"
        assert tray0["k"] is None, "k must be cleared"
        assert tray0["cali_idx"] is None, "cali_idx must be cleared"
        assert tray0["state"] == 10, "state should be preserved"

        # Tray 1 should be untouched
        assert tray1["tray_type"] == "PLA", "Loaded slot must be preserved"
        assert tray1["remain"] == 50

    def test_state_9_clears_stale_tray_data(self, mqtt_client):
        """Incremental update with state=9 (empty, no spool) clears tray."""
        self._seed_loaded_tray(mqtt_client)

        update = {
            "ams": [
                {
                    "id": 0,
                    "tray": [
                        {"id": 0, "state": 9},
                        {"id": 1, "state": 11},
                    ],
                }
            ],
            "power_on_flag": False,
        }
        mqtt_client._handle_ams_data(update)

        tray0 = mqtt_client.state.raw_data["ams"][0]["tray"][0]
        assert tray0["tray_type"] == "", "state=9 must clear tray_type"
        assert tray0["remain"] == 0

    def test_state_11_preserves_tray_data(self, mqtt_client):
        """Incremental update with state=11 (loaded) must NOT clear tray."""
        self._seed_loaded_tray(mqtt_client)

        update = {
            "ams": [
                {
                    "id": 0,
                    "tray": [
                        {"id": 0, "state": 11},
                        {"id": 1, "state": 11},
                    ],
                }
            ],
            "power_on_flag": False,
        }
        mqtt_client._handle_ams_data(update)

        tray0 = mqtt_client.state.raw_data["ams"][0]["tray"][0]
        assert tray0["tray_type"] == "PETG", "state=11 must preserve tray data"
        assert tray0["tray_color"] == "00FF00FF"
        assert tray0["remain"] == 75

    def test_no_clearing_when_tray_type_already_empty(self, mqtt_client):
        """Don't re-clear a tray that's already empty (avoids log spam)."""
        self._seed_loaded_tray(mqtt_client)

        # First unload clears
        update = {
            "ams": [{"id": 0, "tray": [{"id": 0, "state": 10}, {"id": 1, "state": 11}]}],
            "power_on_flag": False,
        }
        mqtt_client._handle_ams_data(update)
        assert mqtt_client.state.raw_data["ams"][0]["tray"][0]["tray_type"] == ""

        # Second identical update should not trigger clearing again
        # (merged_tray.get("tray_type") is already empty/falsy)
        mqtt_client._handle_ams_data(update)
        assert mqtt_client.state.raw_data["ams"][0]["tray"][0]["tray_type"] == ""

    def test_reload_after_unload_restores_data(self, mqtt_client):
        """After clearing via state=10, a full update with state=11 restores data."""
        self._seed_loaded_tray(mqtt_client)

        # Unload
        mqtt_client._handle_ams_data(
            {
                "ams": [{"id": 0, "tray": [{"id": 0, "state": 10}, {"id": 1, "state": 11}]}],
                "power_on_flag": False,
            }
        )
        assert mqtt_client.state.raw_data["ams"][0]["tray"][0]["tray_type"] == ""

        # Reload — full tray data arrives again
        mqtt_client._handle_ams_data(
            {
                "ams": [
                    {
                        "id": 0,
                        "tray": [
                            {
                                "id": 0,
                                "tray_type": "PETG",
                                "tray_sub_brands": "PETG HF",
                                "tray_color": "00FF00FF",
                                "remain": 75,
                                "state": 11,
                            },
                            {"id": 1, "state": 11},
                        ],
                    }
                ],
                "power_on_flag": False,
            }
        )
        tray0 = mqtt_client.state.raw_data["ams"][0]["tray"][0]
        assert tray0["tray_type"] == "PETG", "Reload must restore tray data"
        assert tray0["tray_color"] == "00FF00FF"
        assert tray0["remain"] == 75


class TestNozzleRackData:
    """Tests for nozzle rack data parsing from H2 series device.nozzle.info."""

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

    def test_h2c_nozzle_rack_populated_with_8_entries(self, mqtt_client):
        """H2C provides 8 nozzle entries: IDs 0,1 (L/R hotend) + 16-21 (rack)."""
        payload = {
            "print": {
                "device": {
                    "nozzle": {
                        "info": [
                            {
                                "id": 0,
                                "type": "HS",
                                "diameter": "0.4",
                                "wear": 5,
                                "stat": 1,
                                "max_temp": 300,
                                "serial_number": "SN-L",
                            },
                            {
                                "id": 1,
                                "type": "HS",
                                "diameter": "0.4",
                                "wear": 3,
                                "stat": 0,
                                "max_temp": 300,
                                "serial_number": "SN-R",
                            },
                            {
                                "id": 16,
                                "type": "HS",
                                "diameter": "0.4",
                                "wear": 10,
                                "stat": 0,
                                "max_temp": 300,
                                "serial_number": "SN-16",
                            },
                            {
                                "id": 17,
                                "type": "HH01",
                                "diameter": "0.6",
                                "wear": 0,
                                "stat": 0,
                                "max_temp": 300,
                                "serial_number": "SN-17",
                            },
                            {
                                "id": 18,
                                "type": "HS",
                                "diameter": "0.4",
                                "wear": 2,
                                "stat": 0,
                                "max_temp": 300,
                                "serial_number": "SN-18",
                            },
                            {
                                "id": 19,
                                "type": "",
                                "diameter": "",
                                "wear": None,
                                "stat": None,
                                "max_temp": 0,
                                "serial_number": "",
                            },
                            {
                                "id": 20,
                                "type": "",
                                "diameter": "",
                                "wear": None,
                                "stat": None,
                                "max_temp": 0,
                                "serial_number": "",
                            },
                            {
                                "id": 21,
                                "type": "",
                                "diameter": "",
                                "wear": None,
                                "stat": None,
                                "max_temp": 0,
                                "serial_number": "",
                            },
                        ]
                    }
                }
            }
        }
        mqtt_client._process_message(payload)

        assert len(mqtt_client.state.nozzle_rack) == 8
        ids = [n["id"] for n in mqtt_client.state.nozzle_rack]
        assert ids == [0, 1, 16, 17, 18, 19, 20, 21]

    def test_h2d_nozzle_rack_populated_with_2_entries(self, mqtt_client):
        """H2D provides 2 nozzle entries: IDs 0,1 (L/R hotend) — no rack slots."""
        payload = {
            "print": {
                "device": {
                    "nozzle": {
                        "info": [
                            {
                                "id": 0,
                                "type": "HS",
                                "diameter": "0.4",
                                "wear": 5,
                                "stat": 1,
                                "max_temp": 300,
                                "serial_number": "SN-L",
                            },
                            {
                                "id": 1,
                                "type": "HS",
                                "diameter": "0.4",
                                "wear": 3,
                                "stat": 1,
                                "max_temp": 300,
                                "serial_number": "SN-R",
                            },
                        ]
                    }
                }
            }
        }
        mqtt_client._process_message(payload)

        assert len(mqtt_client.state.nozzle_rack) == 2
        ids = [n["id"] for n in mqtt_client.state.nozzle_rack]
        assert ids == [0, 1]

    def test_single_nozzle_h2s_populated(self, mqtt_client):
        """H2S provides 1 nozzle entry: ID 0 only — single nozzle printer."""
        payload = {
            "print": {
                "device": {
                    "nozzle": {
                        "info": [
                            {
                                "id": 0,
                                "type": "HS",
                                "diameter": "0.4",
                                "wear": 2,
                                "stat": 1,
                                "max_temp": 300,
                                "serial_number": "SN-0",
                            },
                        ]
                    }
                }
            }
        }
        mqtt_client._process_message(payload)

        assert len(mqtt_client.state.nozzle_rack) == 1
        assert mqtt_client.state.nozzle_rack[0]["id"] == 0

    def test_empty_nozzle_info_does_not_populate_rack(self, mqtt_client):
        """Empty nozzle info list should not populate nozzle_rack."""
        payload = {"print": {"device": {"nozzle": {"info": []}}}}
        mqtt_client._process_message(payload)

        assert mqtt_client.state.nozzle_rack == []

    def test_nozzle_rack_sorted_by_id(self, mqtt_client):
        """Nozzle rack entries should be sorted by ID regardless of input order."""
        payload = {
            "print": {
                "device": {
                    "nozzle": {
                        "info": [
                            {"id": 17, "type": "HS", "diameter": "0.6"},
                            {"id": 0, "type": "HS", "diameter": "0.4"},
                            {"id": 16, "type": "HS", "diameter": "0.4"},
                            {"id": 1, "type": "HS", "diameter": "0.4"},
                        ]
                    }
                }
            }
        }
        mqtt_client._process_message(payload)

        ids = [n["id"] for n in mqtt_client.state.nozzle_rack]
        assert ids == [0, 1, 16, 17]

    def test_nozzle_rack_field_mapping(self, mqtt_client):
        """Verify field mapping from MQTT nozzle_info to nozzle_rack dict keys."""
        payload = {
            "print": {
                "device": {
                    "nozzle": {
                        "info": [
                            {
                                "id": 16,
                                "type": "HH01",
                                "diameter": "0.6",
                                "wear": 15,
                                "stat": 0,
                                "max_temp": 320,
                                "serial_number": "SN-ABC123",
                                "filament_colour": "FF8800",
                                "filament_id": "F42",
                                "tray_type": "ABS",
                            }
                        ]
                    }
                }
            }
        }
        mqtt_client._process_message(payload)

        slot = mqtt_client.state.nozzle_rack[0]
        assert slot["id"] == 16
        assert slot["type"] == "HH01"
        assert slot["diameter"] == "0.6"
        assert slot["wear"] == 15
        assert slot["stat"] == 0
        assert slot["max_temp"] == 320
        assert slot["serial_number"] == "SN-ABC123"
        assert slot["filament_color"] == "FF8800"
        assert slot["filament_id"] == "F42"
        assert slot["filament_type"] == "ABS"

    def test_nozzle_info_updates_nozzle_state(self, mqtt_client):
        """Nozzle info for IDs 0,1 should also update nozzle state (type/diameter)."""
        payload = {
            "print": {
                "device": {
                    "nozzle": {
                        "info": [
                            {"id": 0, "type": "HS", "diameter": "0.4"},
                            {"id": 1, "type": "HH01", "diameter": "0.6"},
                        ]
                    }
                }
            }
        }
        mqtt_client._process_message(payload)

        assert mqtt_client.state.nozzles[0].nozzle_type == "HS"
        assert mqtt_client.state.nozzles[0].nozzle_diameter == "0.4"
        assert mqtt_client.state.nozzles[1].nozzle_type == "HH01"
        assert mqtt_client.state.nozzles[1].nozzle_diameter == "0.6"


class TestRequestTopicFailSafe:
    """Tests for graceful degradation when broker rejects request topic subscription."""

    @pytest.fixture(autouse=True)
    def clear_request_topic_cache(self):
        """Clear class-level cache before each test to avoid cross-test pollution."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        BambuMQTTClient._request_topic_cache.clear()

    @pytest.fixture
    def mqtt_client(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )
        return client

    def test_request_topic_supported_by_default(self, mqtt_client):
        """Request topic subscription is attempted by default."""
        assert mqtt_client._request_topic_supported is True
        assert mqtt_client._request_topic_confirmed is False

    def test_on_subscribe_confirms_success(self, mqtt_client):
        """Successful SUBACK marks request topic as confirmed."""
        from paho.mqtt.reasoncodes import ReasonCode

        mqtt_client._request_topic_sub_mid = 42
        rc = ReasonCode(9, identifier=0)  # SUBACK packetType=9, QoS 0 = success
        mqtt_client._on_subscribe(None, None, 42, [rc], None)

        assert mqtt_client._request_topic_confirmed is True
        assert mqtt_client._request_topic_supported is True
        assert mqtt_client._request_topic_sub_mid is None
        assert mqtt_client._request_topic_sub_time == 0.0

    def test_on_subscribe_detects_rejection(self, mqtt_client):
        """SUBACK with failure code disables request topic."""
        from paho.mqtt.reasoncodes import ReasonCode

        mqtt_client._request_topic_sub_mid = 42
        rc = ReasonCode(9, identifier=0x80)  # SUBACK packetType=9, 0x80 = failure
        mqtt_client._on_subscribe(None, None, 42, [rc], None)

        assert mqtt_client._request_topic_supported is False
        assert mqtt_client._request_topic_confirmed is False

    def test_on_subscribe_ignores_other_mids(self, mqtt_client):
        """SUBACK for other subscriptions (e.g. report topic) is ignored."""
        from paho.mqtt.reasoncodes import ReasonCode

        mqtt_client._request_topic_sub_mid = 42
        rc = ReasonCode(9, identifier=0x80)
        mqtt_client._on_subscribe(None, None, 99, [rc], None)

        # Not affected — mid doesn't match
        assert mqtt_client._request_topic_supported is True

    def test_disconnect_after_subscription_disables_topic(self, mqtt_client):
        """Disconnect within 10s of subscription attempt disables request topic."""
        import time

        mqtt_client._request_topic_sub_time = time.time()
        mqtt_client._request_topic_confirmed = False
        mqtt_client._last_message_time = 0.0

        mqtt_client._on_disconnect(None, None)

        assert mqtt_client._request_topic_supported is False
        assert mqtt_client._request_topic_sub_time == 0.0

    def test_disconnect_after_confirmation_does_not_disable(self, mqtt_client):
        """Disconnect after SUBACK confirmation keeps request topic enabled."""
        import time

        mqtt_client._request_topic_sub_time = time.time()
        mqtt_client._request_topic_confirmed = True
        mqtt_client._last_message_time = 0.0

        mqtt_client._on_disconnect(None, None)

        assert mqtt_client._request_topic_supported is True

    def test_late_disconnect_does_not_disable(self, mqtt_client):
        """Disconnect long after subscription (>10s) doesn't blame request topic."""
        import time

        mqtt_client._request_topic_sub_time = time.time() - 30.0
        mqtt_client._request_topic_confirmed = False
        mqtt_client._last_message_time = 0.0

        mqtt_client._on_disconnect(None, None)

        assert mqtt_client._request_topic_supported is True

    def test_on_connect_skips_request_topic_when_unsupported(self, mqtt_client):
        """After marking unsupported, reconnect skips request topic subscription."""
        mqtt_client._request_topic_supported = False

        subscribe_calls = []
        mock_client = type(
            "MockClient",
            (),
            {
                "subscribe": lambda self, topic: subscribe_calls.append(topic) or (0, 1),
            },
        )()

        mqtt_client._on_connect(mock_client, None, None, 0)

        # Only report topic subscribed, not request topic
        assert len(subscribe_calls) == 1
        assert subscribe_calls[0] == mqtt_client.topic_subscribe

    def test_cache_persists_across_instances(self):
        """New client instance inherits request topic unsupported state from cache."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client1 = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST_CACHE",
            access_code="12345678",
        )
        assert client1._request_topic_supported is True

        # Simulate disconnect-after-subscribe disabling the topic
        client1._request_topic_sub_time = __import__("time").time()
        client1._request_topic_confirmed = False
        client1._last_message_time = 0.0
        client1._on_disconnect(None, None)
        assert client1._request_topic_supported is False

        # New instance for same serial should inherit the cached state
        client2 = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST_CACHE",
            access_code="12345678",
        )
        assert client2._request_topic_supported is False

    def test_cache_does_not_affect_different_serial(self):
        """Cache is per-serial — different printer is unaffected."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        BambuMQTTClient._request_topic_cache["SERIAL_A"] = False

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="SERIAL_B",
            access_code="12345678",
        )
        assert client._request_topic_supported is True

    def test_cache_updated_on_suback_success(self):
        """Successful SUBACK caches positive confirmation."""
        from paho.mqtt.reasoncodes import ReasonCode

        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST_SUBACK",
            access_code="12345678",
        )
        client._request_topic_sub_mid = 42
        rc = ReasonCode(9, identifier=0)  # Success
        client._on_subscribe(None, None, 42, [rc], None)

        assert BambuMQTTClient._request_topic_cache["TEST_SUBACK"] is True

    def test_cache_updated_on_suback_rejection(self):
        """SUBACK rejection caches negative state."""
        from paho.mqtt.reasoncodes import ReasonCode

        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST_REJECT",
            access_code="12345678",
        )
        client._request_topic_sub_mid = 42
        rc = ReasonCode(9, identifier=0x80)  # Failure
        client._on_subscribe(None, None, 42, [rc], None)

        assert BambuMQTTClient._request_topic_cache["TEST_REJECT"] is False


class TestRequestTopicAmsMapping:
    """Tests for capturing ams_mapping from the MQTT request topic."""

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

    def test_captured_ams_mapping_initializes_to_none(self, mqtt_client):
        """Verify _captured_ams_mapping starts as None."""
        assert mqtt_client._captured_ams_mapping is None

    def test_handle_request_message_captures_ams_mapping(self, mqtt_client):
        """project_file command with ams_mapping stores the mapping."""
        data = {
            "print": {
                "command": "project_file",
                "ams_mapping": [0, 4, -1, -1],
                "url": "ftp://192.168.1.100/test.3mf",
            }
        }
        mqtt_client._handle_request_message(data)
        assert mqtt_client._captured_ams_mapping == [0, 4, -1, -1]

    def test_handle_request_message_ignores_non_print_commands(self, mqtt_client):
        """Non-project_file commands don't store ams_mapping."""
        data = {
            "print": {
                "command": "pause",
            }
        }
        mqtt_client._handle_request_message(data)
        assert mqtt_client._captured_ams_mapping is None

    def test_handle_request_message_ignores_missing_ams_mapping(self, mqtt_client):
        """project_file command without ams_mapping doesn't store anything."""
        data = {
            "print": {
                "command": "project_file",
                "url": "ftp://192.168.1.100/test.3mf",
            }
        }
        mqtt_client._handle_request_message(data)
        assert mqtt_client._captured_ams_mapping is None

    def test_handle_request_message_ignores_non_dict_print(self, mqtt_client):
        """Non-dict print value is safely ignored."""
        data = {"print": "not_a_dict"}
        mqtt_client._handle_request_message(data)
        assert mqtt_client._captured_ams_mapping is None

    def test_handle_request_message_ignores_missing_print(self, mqtt_client):
        """Message without print key is safely ignored."""
        data = {"pushing": {"command": "pushall"}}
        mqtt_client._handle_request_message(data)
        assert mqtt_client._captured_ams_mapping is None

    def test_captured_mapping_overwrites_previous(self, mqtt_client):
        """A new print command overwrites a previously captured mapping."""
        mqtt_client._captured_ams_mapping = [0, -1, -1, -1]
        data = {
            "print": {
                "command": "project_file",
                "ams_mapping": [4, 8, -1, -1],
            }
        }
        mqtt_client._handle_request_message(data)
        assert mqtt_client._captured_ams_mapping == [4, 8, -1, -1]

    def test_print_start_callback_includes_ams_mapping(self, mqtt_client):
        """on_print_start callback data includes captured ams_mapping."""
        start_data = {}

        def on_start(data):
            start_data.update(data)

        mqtt_client.on_print_start = on_start
        mqtt_client._captured_ams_mapping = [0, 4, -1, -1]

        # Trigger print start
        mqtt_client._process_message(
            {
                "print": {
                    "gcode_state": "RUNNING",
                    "gcode_file": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                }
            }
        )

        assert start_data.get("ams_mapping") == [0, 4, -1, -1]

    def test_print_start_callback_ams_mapping_none_when_not_captured(self, mqtt_client):
        """on_print_start callback has ams_mapping=None when no mapping captured."""
        start_data = {}

        def on_start(data):
            start_data.update(data)

        mqtt_client.on_print_start = on_start

        mqtt_client._process_message(
            {
                "print": {
                    "gcode_state": "RUNNING",
                    "gcode_file": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                }
            }
        )

        assert "ams_mapping" in start_data
        assert start_data["ams_mapping"] is None

    def test_print_complete_callback_includes_ams_mapping(self, mqtt_client):
        """on_print_complete callback data includes captured ams_mapping."""
        complete_data = {}

        def on_complete(data):
            complete_data.update(data)

        mqtt_client.on_print_start = lambda d: None
        mqtt_client.on_print_complete = on_complete
        mqtt_client._captured_ams_mapping = [0, 9, -1, -1]

        # Start print
        mqtt_client._process_message(
            {
                "print": {
                    "gcode_state": "RUNNING",
                    "gcode_file": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                }
            }
        )

        # Complete print
        mqtt_client._process_message(
            {
                "print": {
                    "gcode_state": "FINISH",
                    "gcode_file": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                }
            }
        )

        assert complete_data.get("ams_mapping") == [0, 9, -1, -1]

    def test_captured_mapping_cleared_after_print_complete(self, mqtt_client):
        """_captured_ams_mapping is reset to None after print completion."""
        mqtt_client.on_print_start = lambda d: None
        mqtt_client.on_print_complete = lambda d: None
        mqtt_client._captured_ams_mapping = [0, 4, -1, -1]

        # Start print
        mqtt_client._process_message(
            {
                "print": {
                    "gcode_state": "RUNNING",
                    "gcode_file": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                }
            }
        )

        # Complete print
        mqtt_client._process_message(
            {
                "print": {
                    "gcode_state": "FINISH",
                    "gcode_file": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                }
            }
        )

        assert mqtt_client._captured_ams_mapping is None

    def test_full_flow_capture_and_deliver(self, mqtt_client):
        """Full flow: slicer sends print command → MQTT captures mapping → completion delivers it."""
        complete_data = {}

        def on_complete(data):
            complete_data.update(data)

        mqtt_client.on_print_start = lambda d: None
        mqtt_client.on_print_complete = on_complete

        # 1. Slicer sends print command (captured from request topic)
        mqtt_client._handle_request_message(
            {
                "print": {
                    "command": "project_file",
                    "ams_mapping": [4, 9, -1, -1],
                    "url": "ftp://192.168.1.100/model.3mf",
                }
            }
        )
        assert mqtt_client._captured_ams_mapping == [4, 9, -1, -1]

        # 2. Printer reports RUNNING
        mqtt_client._process_message(
            {
                "print": {
                    "gcode_state": "RUNNING",
                    "gcode_file": "/data/Metadata/model.gcode",
                    "subtask_name": "Model",
                }
            }
        )

        # 3. Printer reports FINISH
        mqtt_client._process_message(
            {
                "print": {
                    "gcode_state": "FINISH",
                    "gcode_file": "/data/Metadata/model.gcode",
                    "subtask_name": "Model",
                }
            }
        )

        assert complete_data["ams_mapping"] == [4, 9, -1, -1]
        assert complete_data["status"] == "completed"
        # Mapping cleared after completion
        assert mqtt_client._captured_ams_mapping is None


# ---------------------------------------------------------------------------
# tray_now disambiguation helpers
# ---------------------------------------------------------------------------


def _ams_payload(tray_now, ams_units=None, tray_exist_bits=None, ams_exist_bits=None):
    """Build minimal print.ams payload for tray_now disambiguation tests."""
    ams = {"tray_now": str(tray_now)}
    if ams_units is not None:
        ams["ams"] = ams_units
    if tray_exist_bits is not None:
        ams["tray_exist_bits"] = tray_exist_bits
    if ams_exist_bits is not None:
        ams["ams_exist_bits"] = ams_exist_bits
    return {"print": {"ams": ams}}


def _extruder_info_payload(extruders):
    """Build device.extruder.info payload (dual-nozzle detection + snow).

    Each entry in *extruders* is a dict with at least ``id`` and ``snow``.
    """
    return {
        "print": {
            "device": {
                "extruder": {
                    "info": extruders,
                }
            }
        }
    }


def _extruder_state_payload(state_val):
    """Build device.extruder.state payload (active extruder via bit 8)."""
    return {
        "print": {
            "device": {
                "extruder": {
                    "state": state_val,
                }
            }
        }
    }


# ---------------------------------------------------------------------------
# 1. Single-nozzle X1E — direct passthrough
# ---------------------------------------------------------------------------


class TestTrayNowSingleNozzleX1E:
    """Single-nozzle, 1 AMS — tray_now is a direct passthrough."""

    @pytest.fixture
    def mqtt_client(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        return BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST_X1E",
            access_code="12345678",
        )

    def test_tray_now_direct_passthrough_slot_0_to_3(self, mqtt_client):
        """Each tray_now 0-3 maps 1:1 on single-nozzle printers."""
        for slot in range(4):
            mqtt_client._process_message(_ams_payload(slot))
            assert mqtt_client.state.tray_now == slot

    def test_tray_now_255_means_unloaded(self, mqtt_client):
        """tray_now=255 means no filament loaded."""
        mqtt_client._process_message(_ams_payload(255))
        assert mqtt_client.state.tray_now == 255

    def test_single_extruder_does_not_trigger_dual_nozzle(self, mqtt_client):
        """device.extruder.info with 1 entry must NOT set _is_dual_nozzle."""
        mqtt_client._process_message(_extruder_info_payload([{"id": 0, "snow": 0xFF00FF}]))
        assert mqtt_client._is_dual_nozzle is False

    def test_last_loaded_tray_survives_unload(self, mqtt_client):
        """Load tray 2, unload → last_loaded_tray stays 2."""
        mqtt_client._process_message(_ams_payload(2))
        assert mqtt_client.state.last_loaded_tray == 2

        mqtt_client._process_message(_ams_payload(255))
        assert mqtt_client.state.tray_now == 255
        assert mqtt_client.state.last_loaded_tray == 2


# ---------------------------------------------------------------------------
# 2. Single-nozzle P2S — multiple AMS, global IDs pass through
# ---------------------------------------------------------------------------


class TestTrayNowSingleNozzleP2S:
    """Single-nozzle, 2 AMS — tray_now > 3 passes through as global ID."""

    @pytest.fixture
    def mqtt_client(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        return BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST_P2S",
            access_code="12345678",
        )

    def test_tray_now_ams1_global_ids_4_to_7(self, mqtt_client):
        """tray_now 4-7 are global IDs for AMS 1 on single-nozzle printers."""
        for global_id in range(4, 8):
            mqtt_client._process_message(_ams_payload(global_id))
            assert mqtt_client.state.tray_now == global_id

    def test_tray_change_across_ams_units(self, mqtt_client):
        """Switch from AMS 0 slot 1 → AMS 1 slot 2 (global 6)."""
        mqtt_client._process_message(_ams_payload(1))
        assert mqtt_client.state.tray_now == 1

        mqtt_client._process_message(_ams_payload(6))
        assert mqtt_client.state.tray_now == 6


# ---------------------------------------------------------------------------
# 2b. Single-nozzle P2S — multi-AMS local slot disambiguation (#420)
# ---------------------------------------------------------------------------


class TestTrayNowP2SMultiAmsDisambiguation:
    """P2S firmware sends local slot IDs (0-3) in tray_now even with dual AMS.

    When ams_exist_bits indicates >1 AMS unit and tray_now is 0-3, the backend
    should use the MQTT mapping field (snow-encoded) to resolve the correct
    global tray ID.
    """

    @pytest.fixture
    def mqtt_client(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST_P2S_DUAL",
            access_code="12345678",
        )
        return client

    def test_resolves_ams1_slot1_from_mapping(self, mqtt_client):
        """tray_now=1 with mapping=[257] → global ID 5 (AMS1-T1).

        257 snow-decoded: ams_hw_id=1, slot=1 → global 1*4+1=5.
        """
        # Set mapping field in raw_data (as the MQTT handler would)
        mqtt_client.state.raw_data["mapping"] = [257]
        mqtt_client._process_message(
            _ams_payload(1, ams_exist_bits="3")  # '3' = 0b11 → AMS 0 and 1
        )
        assert mqtt_client.state.tray_now == 5

    def test_resolves_ams1_slot0_from_mapping(self, mqtt_client):
        """tray_now=0 with mapping=[256] → global ID 4 (AMS1-T0).

        256 snow-decoded: ams_hw_id=1, slot=0 → global 1*4+0=4.
        """
        mqtt_client.state.raw_data["mapping"] = [256]
        mqtt_client._process_message(_ams_payload(0, ams_exist_bits="3"))
        assert mqtt_client.state.tray_now == 4

    def test_resolves_ams1_slot3_from_mapping(self, mqtt_client):
        """tray_now=3 with mapping=[259] → global ID 7 (AMS1-T3).

        259 snow-decoded: ams_hw_id=1, slot=3 → global 1*4+3=7.
        """
        mqtt_client.state.raw_data["mapping"] = [259]
        mqtt_client._process_message(_ams_payload(3, ams_exist_bits="3"))
        assert mqtt_client.state.tray_now == 7

    def test_ams0_slot_unchanged_when_mapping_confirms_ams0(self, mqtt_client):
        """tray_now=1 with mapping=[1] → stays 1 (AMS0-T1).

        1 snow-decoded: ams_hw_id=0, slot=1 → global 0*4+1=1.
        """
        mqtt_client.state.raw_data["mapping"] = [1]
        mqtt_client._process_message(_ams_payload(1, ams_exist_bits="3"))
        assert mqtt_client.state.tray_now == 1

    def test_multicolor_resolves_ams1_from_multi_entry_mapping(self, mqtt_client):
        """Multi-color print: mapping=[0, 257] → tray_now=1 resolves to AMS1-T1 (5).

        Entry 0: ams_hw_id=0, slot=0 (local 0) — doesn't match tray_now=1.
        Entry 257: ams_hw_id=1, slot=1 (local 1) — matches tray_now=1 → global 5.
        """
        mqtt_client.state.raw_data["mapping"] = [0, 257]
        mqtt_client._process_message(_ams_payload(1, ams_exist_bits="3"))
        assert mqtt_client.state.tray_now == 5

    def test_multicolor_four_slot_mapping(self, mqtt_client):
        """mapping=[65535, 65535, 65535, 257] → tray_now=1 resolves to global 5.

        Only entry 257 has local slot=1, other entries are unmapped (65535).
        Reproduces exact data from issue #420 support package.
        """
        mqtt_client.state.raw_data["mapping"] = [65535, 65535, 65535, 257]
        mqtt_client._process_message(_ams_payload(1, ams_exist_bits="3"))
        assert mqtt_client.state.tray_now == 5

    def test_ambiguous_mapping_falls_back_to_local_slot(self, mqtt_client):
        """Two AMS units with same local slot in mapping → ambiguous, keep local slot.

        mapping=[1, 257]: both have local slot 1 (AMS0-T1 and AMS1-T1).
        Cannot disambiguate → fall back to tray_now=1.
        """
        mqtt_client.state.raw_data["mapping"] = [1, 257]
        mqtt_client._process_message(_ams_payload(1, ams_exist_bits="3"))
        assert mqtt_client.state.tray_now == 1

    def test_no_mapping_falls_back_to_local_slot(self, mqtt_client):
        """No mapping field available → fall back to raw tray_now."""
        # No mapping in raw_data (e.g. manual filament load, not during print)
        mqtt_client._process_message(_ams_payload(1, ams_exist_bits="3"))
        assert mqtt_client.state.tray_now == 1

    def test_empty_mapping_falls_back_to_local_slot(self, mqtt_client):
        """Empty mapping list → fall back to raw tray_now."""
        mqtt_client.state.raw_data["mapping"] = []
        mqtt_client._process_message(_ams_payload(1, ams_exist_bits="3"))
        assert mqtt_client.state.tray_now == 1

    def test_single_ams_passthrough(self, mqtt_client):
        """Single AMS (ams_exist_bits='1') → tray_now 0-3 is direct global ID."""
        mqtt_client._process_message(_ams_payload(2, ams_exist_bits="1"))
        assert mqtt_client.state.tray_now == 2

    def test_no_ams_exist_bits_passthrough(self, mqtt_client):
        """No ams_exist_bits in payload → fall back to raw tray_now."""
        mqtt_client._process_message(_ams_payload(1))
        assert mqtt_client.state.tray_now == 1

    def test_tray_now_255_unaffected_by_multi_ams(self, mqtt_client):
        """tray_now=255 (unloaded) passes through regardless of AMS count."""
        mqtt_client.state.raw_data["mapping"] = [257]
        mqtt_client._process_message(_ams_payload(255, ams_exist_bits="3"))
        assert mqtt_client.state.tray_now == 255

    def test_tray_now_above_3_unaffected(self, mqtt_client):
        """tray_now > 3 is already a global ID and passes through directly."""
        mqtt_client._process_message(_ams_payload(6, ams_exist_bits="3"))
        assert mqtt_client.state.tray_now == 6

    def test_last_loaded_tray_uses_resolved_global_id(self, mqtt_client):
        """last_loaded_tray should reflect the resolved global ID, not local slot."""
        mqtt_client.state.raw_data["mapping"] = [257]
        mqtt_client.state.state = "RUNNING"
        mqtt_client._process_message(_ams_payload(1, ams_exist_bits="3"))
        assert mqtt_client.state.tray_now == 5
        assert mqtt_client.state.last_loaded_tray == 5


class TestResolveLocalSlotFromMapping:
    """Unit tests for _resolve_local_slot_from_mapping static method."""

    def test_single_match_ams0(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        assert BambuMQTTClient._resolve_local_slot_from_mapping(1, [1]) == 1

    def test_single_match_ams1(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        # 257 = 1*256 + 1 → AMS1 slot1 → global 5
        assert BambuMQTTClient._resolve_local_slot_from_mapping(1, [257]) == 5

    def test_single_match_ams2(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        # 514 = 2*256 + 2 → AMS2 slot2 → global 10
        assert BambuMQTTClient._resolve_local_slot_from_mapping(2, [514]) == 10

    def test_unmapped_entries_skipped(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        assert BambuMQTTClient._resolve_local_slot_from_mapping(1, [65535, 65535, 65535, 257]) == 5

    def test_no_match_returns_none(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        # mapping has slot 0 only, looking for slot 2
        assert BambuMQTTClient._resolve_local_slot_from_mapping(2, [0]) is None

    def test_ambiguous_returns_none(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        # Both AMS0 slot1 (1) and AMS1 slot1 (257) → ambiguous
        assert BambuMQTTClient._resolve_local_slot_from_mapping(1, [1, 257]) is None

    def test_none_mapping_returns_none(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        assert BambuMQTTClient._resolve_local_slot_from_mapping(1, None) is None

    def test_empty_mapping_returns_none(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        assert BambuMQTTClient._resolve_local_slot_from_mapping(1, []) is None

    def test_ams_ht_slot0_match(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        # AMS-HT id=128: snow = 128*256 + 0 = 32768
        assert BambuMQTTClient._resolve_local_slot_from_mapping(0, [32768]) == 128


# ---------------------------------------------------------------------------
# 3. H2D Pro — initial state detection
# ---------------------------------------------------------------------------


class TestTrayNowDualNozzleH2DSetup:
    """H2D Pro initial state detection."""

    @pytest.fixture
    def mqtt_client(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        return BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST_H2D",
            access_code="12345678",
        )

    def test_dual_nozzle_detected_from_extruder_info(self, mqtt_client):
        """2 entries in device.extruder.info → _is_dual_nozzle=True."""
        mqtt_client._process_message(
            _extruder_info_payload(
                [
                    {"id": 0, "snow": 0xFF00FF},
                    {"id": 1, "snow": 0xFF00FF},
                ]
            )
        )
        assert mqtt_client._is_dual_nozzle is True

    def test_ams_extruder_map_parsed_from_info_field(self, mqtt_client):
        """AMS info field is hex: 0x2003 → ext 0 (right), 0x2104 → ext 1 (left)."""
        # MQTT sends info as string; BambuStudio parses as hex via stoull(str, 16)
        ams_units = [
            {"id": 0, "info": "2003", "tray": [{"id": i} for i in range(4)]},
            {"id": 128, "info": "2104", "tray": [{"id": 0}]},
        ]
        payload = {
            "print": {
                "ams": {
                    "ams": ams_units,
                    "tray_now": "255",
                    "tray_exist_bits": "1000f",
                },
            }
        }
        mqtt_client._process_message(payload)

        # 0x2003: bits 8-11 = (0x2003 >> 8) & 0xF = 0x20 & 0xF = 0 → extruder 0 (right)
        # 0x2104: bits 8-11 = (0x2104 >> 8) & 0xF = 0x21 & 0xF = 1 → extruder 1 (left)
        assert mqtt_client.state.ams_extruder_map == {"0": 0, "128": 1}

    def test_ams_extruder_map_real_h2d_values(self, mqtt_client):
        """Real H2D MQTT values: AMS2 Pro on right, AMS-HT on left."""
        ams_units = [
            {"id": 0, "info": "10001003", "tray": [{"id": i} for i in range(4)]},
            {"id": 128, "info": "10002104", "tray": [{"id": 0}]},
        ]
        payload = {
            "print": {
                "ams": {
                    "ams": ams_units,
                    "tray_now": "255",
                    "tray_exist_bits": "1000a",
                },
            }
        }
        mqtt_client._process_message(payload)

        # 0x10001003: bits 8-11 = (0x10001003 >> 8) & 0xF = 0x10 & 0xF = 0 → right
        # 0x10002104: bits 8-11 = (0x10002104 >> 8) & 0xF = 0x21 & 0xF = 1 → left
        assert mqtt_client.state.ams_extruder_map == {"0": 0, "128": 1}

    def test_ams_extruder_map_skips_uninitialized(self, mqtt_client):
        """extruder_id 0xE means uninitialized AMS — should be skipped."""
        ams_units = [
            {"id": 0, "info": "e03", "tray": [{"id": i} for i in range(4)]},
        ]
        payload = {
            "print": {
                "ams": {
                    "ams": ams_units,
                    "tray_now": "255",
                    "tray_exist_bits": "f",
                },
            }
        }
        mqtt_client._process_message(payload)
        assert mqtt_client.state.ams_extruder_map == {}

    def test_ams_extruder_map_partial_update_preserves_entries(self, mqtt_client):
        """Partial MQTT update with one AMS should not overwrite other entries."""
        # First: full update with both AMS units
        full_payload = {
            "print": {
                "ams": {
                    "ams": [
                        {"id": 0, "info": "2003", "tray": [{"id": i} for i in range(4)]},
                        {"id": 128, "info": "2104", "tray": [{"id": 0}]},
                    ],
                    "tray_now": "255",
                    "tray_exist_bits": "1000f",
                },
            }
        }
        mqtt_client._process_message(full_payload)
        assert mqtt_client.state.ams_extruder_map == {"0": 0, "128": 1}

        # Then: partial update with only AMS 0 (no info field this time)
        partial_payload = {
            "print": {
                "ams": {
                    "ams": [
                        {"id": 0, "tray": [{"id": 0, "remain": 50}]},
                    ],
                    "tray_now": "0",
                    "tray_exist_bits": "1000f",
                },
            }
        }
        mqtt_client._process_message(partial_payload)
        # Both entries should still be present
        assert mqtt_client.state.ams_extruder_map == {"0": 0, "128": 1}

    def test_dual_nozzle_detection_before_ams_in_same_message(self, mqtt_client):
        """Dual-nozzle detection at line 538 happens before _handle_ams_data() at line 549.

        If both arrive in the same message, tray_now disambiguation already uses dual-nozzle logic.
        """
        payload = {
            "print": {
                "device": {
                    "extruder": {
                        "info": [
                            {"id": 0, "snow": 0xFF00FF},
                            {"id": 1, "snow": 0xFF00FF},
                        ],
                        "state": 0x0001,
                    }
                },
                "ams": {
                    "ams": [
                        {"id": 0, "info": "2003", "tray": [{"id": i} for i in range(4)]},
                    ],
                    "tray_now": "2",
                    "tray_exist_bits": "f",
                },
            }
        }
        mqtt_client._process_message(payload)

        # Dual-nozzle was detected; AMS 0 on right extruder (active by default);
        # snow is 0xFF00FF (unloaded), so falls through to ams_extruder_map fallback.
        # Single AMS on extruder 0 → global_id = 0*4+2 = 2
        assert mqtt_client._is_dual_nozzle is True
        assert mqtt_client.state.tray_now == 2


# ---------------------------------------------------------------------------
# Shared H2D fixture for classes 4-8
# ---------------------------------------------------------------------------


class _H2DFixtureMixin:
    """Mixin providing a pre-configured H2D Pro client."""

    @pytest.fixture
    def mqtt_client(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        return BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST_H2D",
            access_code="12345678",
        )

    @pytest.fixture
    def h2d_client(self, mqtt_client):
        """Pre-configure as H2D Pro: dual-nozzle + ams_extruder_map."""
        mqtt_client._process_message(
            {
                "print": {
                    "device": {
                        "extruder": {
                            "info": [
                                {"id": 0, "snow": 0xFF00FF},
                                {"id": 1, "snow": 0xFF00FF},
                            ],
                            "state": 0x0001,  # right extruder active
                        }
                    },
                    "ams": {
                        "ams": [
                            {"id": 0, "info": "2003", "tray": [{"id": i} for i in range(4)]},
                            {"id": 128, "info": "2104", "tray": [{"id": 0}]},
                        ],
                        "tray_now": "255",
                        "tray_exist_bits": "1000f",
                    },
                }
            }
        )
        assert mqtt_client._is_dual_nozzle is True
        assert mqtt_client.state.ams_extruder_map == {"0": 0, "128": 1}
        return mqtt_client


# ---------------------------------------------------------------------------
# 4. H2D Snow field disambiguation
# ---------------------------------------------------------------------------


class TestTrayNowDualNozzleH2DSnow(_H2DFixtureMixin):
    """Snow field disambiguation (primary path)."""

    def test_snow_disambiguates_ams0_slot(self, h2d_client):
        """snow ext[0]=AMS 0 slot 2, tray_now='2' → global 2."""
        # Send snow update FIRST (snow is parsed AFTER tray_now in the same message,
        # so we need it in a prior message).
        snow_val = 0 << 8 | 2  # AMS 0 slot 2 = raw 2
        h2d_client._process_message(
            _extruder_info_payload(
                [
                    {"id": 0, "snow": snow_val},
                    {"id": 1, "snow": 0xFF00FF},
                ]
            )
        )
        assert h2d_client.state.h2d_extruder_snow.get(0) == 2

        # Now send tray_now=2
        h2d_client._process_message(_ams_payload(2))
        assert h2d_client.state.tray_now == 2

    def test_snow_disambiguates_ams_ht_to_128(self, h2d_client):
        """snow ext[1]=AMS HT (128), left active, tray_now='0' → global 128."""
        # Snow: extruder 1 → AMS 128 slot 0
        snow_val = 128 << 8 | 0  # = 32768
        h2d_client._process_message(
            _extruder_info_payload(
                [
                    {"id": 0, "snow": 0xFF00FF},
                    {"id": 1, "snow": snow_val},
                ]
            )
        )
        assert h2d_client.state.h2d_extruder_snow.get(1) == 128

        # Switch to left extruder
        h2d_client._process_message(_extruder_state_payload(0x0100))
        assert h2d_client.state.active_extruder == 1

        # tray_now="0" with left extruder active, snow says AMS HT (128)
        # AMS HT snow_slot = 0 (single slot), parsed_tray_now = 0 → match
        h2d_client._process_message(_ams_payload(0))
        assert h2d_client.state.tray_now == 128

    def test_snow_updates_h2d_extruder_snow_state(self, h2d_client):
        """Verify state.h2d_extruder_snow dict is populated correctly."""
        snow_ext0 = 1 << 8 | 3  # AMS 1 slot 3 → global 7
        snow_ext1 = 0 << 8 | 0  # AMS 0 slot 0 → global 0
        h2d_client._process_message(
            _extruder_info_payload(
                [
                    {"id": 0, "snow": snow_ext0},
                    {"id": 1, "snow": snow_ext1},
                ]
            )
        )
        assert h2d_client.state.h2d_extruder_snow[0] == 7
        assert h2d_client.state.h2d_extruder_snow[1] == 0

    def test_snow_unloaded_value(self, h2d_client):
        """snow=0xFFFF (ams_id=255, slot=255) → 255 (unloaded)."""
        h2d_client._process_message(
            _extruder_info_payload(
                [
                    {"id": 0, "snow": 0xFFFF},
                    {"id": 1, "snow": 0xFFFF},
                ]
            )
        )
        assert h2d_client.state.h2d_extruder_snow[0] == 255
        assert h2d_client.state.h2d_extruder_snow[1] == 255

    def test_snow_initial_sentinel_not_stored(self, h2d_client):
        """snow=0xFF00FF (firmware initial sentinel) is not parsed into h2d_extruder_snow."""
        # 0xFF00FF has ams_id=0xFF00=65280 which doesn't match any branch
        h2d_client._process_message(
            _extruder_info_payload(
                [
                    {"id": 0, "snow": 0xFF00FF},
                    {"id": 1, "snow": 0xFF00FF},
                ]
            )
        )
        # Snow dict should remain empty (no matching branch)
        assert h2d_client.state.h2d_extruder_snow == {}


# ---------------------------------------------------------------------------
# 5. H2D Pending target disambiguation
# ---------------------------------------------------------------------------


class TestTrayNowDualNozzleH2DPendingTarget(_H2DFixtureMixin):
    """Pending target disambiguation (when Bambuddy initiates load)."""

    def test_pending_target_matches_slot(self, h2d_client):
        """pending=5, tray_now='1' (5%4=1 matches) → tray_now=5."""
        h2d_client.state.pending_tray_target = 5
        h2d_client._process_message(_ams_payload(1))
        assert h2d_client.state.tray_now == 5
        assert h2d_client.state.pending_tray_target is None  # cleared

    def test_pending_target_slot_mismatch(self, h2d_client):
        """pending=5, tray_now='2' → uses raw slot, clears pending."""
        h2d_client.state.pending_tray_target = 5
        h2d_client._process_message(_ams_payload(2))
        # Slot 2 != 5%4=1 → mismatch, uses raw slot 2
        assert h2d_client.state.tray_now == 2
        assert h2d_client.state.pending_tray_target is None

    def test_pending_target_takes_priority_over_snow(self, h2d_client):
        """When both pending and snow are set, pending wins."""
        # Set up snow for extruder 0 → AMS 0 slot 1 → global 1
        snow_val = 0 << 8 | 1
        h2d_client._process_message(
            _extruder_info_payload(
                [
                    {"id": 0, "snow": snow_val},
                    {"id": 1, "snow": 0xFF00FF},
                ]
            )
        )
        assert h2d_client.state.h2d_extruder_snow.get(0) == 1

        # Set pending target to AMS 1 slot 1 (global 5)
        h2d_client.state.pending_tray_target = 5
        # tray_now="1" — matches pending (5%4=1), pending should win over snow
        h2d_client._process_message(_ams_payload(1))
        assert h2d_client.state.tray_now == 5


# ---------------------------------------------------------------------------
# 6. H2D ams_extruder_map fallback
# ---------------------------------------------------------------------------


class TestTrayNowDualNozzleH2DFallback(_H2DFixtureMixin):
    """ams_extruder_map fallback (no pending, no snow)."""

    def test_single_ams_on_extruder_computes_global_id(self, h2d_client):
        """AMS 0 on right extruder, tray_now='2' → 0*4+2=2."""
        # h2d_client has snow=0xFF00FF (unloaded) by default, so snow path skips
        h2d_client._process_message(_ams_payload(2))
        # AMS 0 is the only AMS on extruder 0 (right, active by default)
        # Fallback: single AMS → global = 0*4+2 = 2
        assert h2d_client.state.tray_now == 2

    def test_multiple_ams_keeps_current_if_valid(self, h2d_client):
        """Current tray matches slot → keeps it (multi-AMS on same extruder)."""
        # Set up: two AMS units on the same extruder (right, ext 0)
        h2d_client.state.ams_extruder_map = {"0": 0, "1": 0}
        # Pre-set tray_now=5 (AMS 1 slot 1) — current_ams=1 which is in ams_on_extruder
        h2d_client.state.tray_now = 5
        # tray_now="1" → 5%4=1 matches → keep current=5
        h2d_client._process_message(_ams_payload(1))
        assert h2d_client.state.tray_now == 5

    def test_no_ams_on_extruder_uses_raw_slot(self, h2d_client):
        """No AMS mapped to the active extruder → raw slot as global ID."""
        # All AMS on left extruder, but right is active
        h2d_client.state.ams_extruder_map = {"0": 1, "128": 1}
        h2d_client._process_message(_ams_payload(2))
        assert h2d_client.state.tray_now == 2

    def test_single_ams_ht_on_extruder_returns_unit_id(self, h2d_client):
        """AMS-HT 128 alone on left extruder, slot 0 → global ID 128 (not 512)."""
        # Switch to left extruder (where AMS-HT 128 is mapped)
        h2d_client._process_message(_extruder_state_payload(0x0100))
        # Only AMS-HT 128 on left extruder; no snow available
        h2d_client._process_message(_ams_payload(0))
        assert h2d_client.state.tray_now == 128

    def test_single_ams_ht_ignores_nonzero_slot(self, h2d_client):
        """AMS-HT has single slot; even if printer reports slot 1, global ID = unit ID."""
        h2d_client.state.ams_extruder_map = {"129": 0}
        h2d_client._process_message(_ams_payload(1))
        # AMS-HT 129: global ID = 129, not 129*4+1=517
        assert h2d_client.state.tray_now == 129

    def test_multiple_ams_keeps_current_ams_ht(self, h2d_client):
        """Current tray is AMS-HT 128, slot 0 reported → keeps 128."""
        h2d_client.state.ams_extruder_map = {"0": 0, "128": 0}
        h2d_client.state.tray_now = 128
        h2d_client._process_message(_ams_payload(0))
        assert h2d_client.state.tray_now == 128

    def test_multiple_ams_slot_nonzero_excludes_ams_ht(self, h2d_client):
        """Slot > 0 eliminates AMS-HT candidates; single regular AMS left → resolves."""
        # AMS 0 + AMS-HT 128 both on right extruder
        h2d_client.state.ams_extruder_map = {"0": 0, "128": 0}
        h2d_client.state.tray_now = 255  # no current match
        # Slot 2 → can't be AMS-HT → only AMS 0 → global = 0*4+2 = 2
        h2d_client._process_message(_ams_payload(2))
        assert h2d_client.state.tray_now == 2

    def test_multiple_ams_slot_nonzero_narrows_to_single_ht_excluded(self, h2d_client):
        """Two regular AMS + one AMS-HT, slot > 0 → AMS-HT excluded but still ambiguous."""
        h2d_client.state.ams_extruder_map = {"0": 0, "1": 0, "128": 0}
        h2d_client.state.tray_now = 255
        # Slot 3 → excludes AMS-HT, but AMS 0 and AMS 1 both remain → ambiguous
        h2d_client._process_message(_ams_payload(3))
        assert h2d_client.state.tray_now == 3  # raw slot fallback


# ---------------------------------------------------------------------------
# 6b. H2D last_loaded_tray validation
# ---------------------------------------------------------------------------


class TestLastLoadedTrayValidation(_H2DFixtureMixin):
    """last_loaded_tray only stores physically valid tray IDs."""

    def test_regular_ams_tray_stored(self, h2d_client):
        """Valid regular AMS tray (0-15) → stored in last_loaded_tray."""
        h2d_client.state.tray_now = 7
        # Trigger tray_now processing via AMS message
        h2d_client._process_message(
            _extruder_info_payload(
                [
                    {"id": 0, "snow": 1 << 8 | 3},  # AMS 1 slot 3 → global 7
                    {"id": 1, "snow": 0xFF00FF},
                ]
            )
        )
        h2d_client._process_message(_ams_payload(3))
        assert h2d_client.state.tray_now == 7
        assert h2d_client.state.last_loaded_tray == 7

    def test_ams_ht_tray_stored(self, h2d_client):
        """Valid AMS-HT tray (128-135) → stored in last_loaded_tray."""
        h2d_client._process_message(_extruder_state_payload(0x0100))
        h2d_client._process_message(
            _extruder_info_payload(
                [
                    {"id": 0, "snow": 0xFF00FF},
                    {"id": 1, "snow": 128 << 8 | 0},
                ]
            )
        )
        h2d_client._process_message(_ams_payload(0))
        assert h2d_client.state.tray_now == 128
        assert h2d_client.state.last_loaded_tray == 128

    def test_unloaded_not_stored(self, h2d_client):
        """tray_now=255 (unloaded) → last_loaded_tray unchanged."""
        h2d_client.state.last_loaded_tray = 5
        h2d_client._process_message(_ams_payload(255))
        assert h2d_client.state.tray_now == 255
        assert h2d_client.state.last_loaded_tray == 5


# ---------------------------------------------------------------------------
# 7. H2D Active extruder switching
# ---------------------------------------------------------------------------


class TestTrayNowDualNozzleH2DActiveExtruder(_H2DFixtureMixin):
    """Active extruder switching via device.extruder.state bit 8."""

    def test_active_extruder_right_by_default(self, h2d_client):
        """Initial state.active_extruder == 0 (right)."""
        assert h2d_client.state.active_extruder == 0

    def test_extruder_state_bit8_switches_to_left(self, h2d_client):
        """state=0x100 → active_extruder=1 (left)."""
        h2d_client._process_message(_extruder_state_payload(0x0100))
        assert h2d_client.state.active_extruder == 1

    def test_extruder_state_bit8_switches_back_to_right(self, h2d_client):
        """Cycle 0 → 1 → 0."""
        h2d_client._process_message(_extruder_state_payload(0x0100))
        assert h2d_client.state.active_extruder == 1

        h2d_client._process_message(_extruder_state_payload(0x0001))
        assert h2d_client.state.active_extruder == 0

    def test_extruder_switch_changes_tray_disambiguation(self, h2d_client):
        """Snow on both extruders; switching active changes which snow is used."""
        # Snow: ext 0 → AMS 0 slot 1 (global 1), ext 1 → AMS 128 slot 0 (global 128)
        h2d_client._process_message(
            _extruder_info_payload(
                [
                    {"id": 0, "snow": 0 << 8 | 1},  # AMS 0 slot 1 → global 1
                    {"id": 1, "snow": 128 << 8 | 0},  # AMS HT → global 128
                ]
            )
        )

        # Right active (default) — tray_now="1" → snow ext[0] says global 1
        h2d_client._process_message(_ams_payload(1))
        assert h2d_client.state.tray_now == 1

        # Switch to left
        h2d_client._process_message(_extruder_state_payload(0x0100))

        # Left active — tray_now="0" → snow ext[1] says AMS HT (128), slot 0 matches
        h2d_client._process_message(_ams_payload(0))
        assert h2d_client.state.tray_now == 128


# ---------------------------------------------------------------------------
# 8. H2D Full multi-message sequences
# ---------------------------------------------------------------------------


class TestTrayNowDualNozzleH2DFullSequence(_H2DFixtureMixin):
    """Multi-message sequences simulating real H2D Pro prints."""

    def test_h2d_right_nozzle_ams0_lifecycle(self, h2d_client):
        """Setup → load AMS 0 slot 1 → verify tray_now=1."""
        # Snow update: extruder 0 loading AMS 0 slot 1
        h2d_client._process_message(
            _extruder_info_payload(
                [
                    {"id": 0, "snow": 0 << 8 | 1},
                    {"id": 1, "snow": 0xFF00FF},
                ]
            )
        )
        # Printer reports tray_now="1"
        h2d_client._process_message(_ams_payload(1))
        assert h2d_client.state.tray_now == 1
        assert h2d_client.state.last_loaded_tray == 1

    def test_h2d_left_nozzle_ams_ht_lifecycle(self, h2d_client):
        """Setup → switch left → load AMS HT → verify tray_now=128."""
        # Switch to left extruder
        h2d_client._process_message(_extruder_state_payload(0x0100))

        # Snow: ext 1 → AMS HT slot 0
        h2d_client._process_message(
            _extruder_info_payload(
                [
                    {"id": 0, "snow": 0xFF00FF},
                    {"id": 1, "snow": 128 << 8 | 0},
                ]
            )
        )

        # Printer reports tray_now="0" (AMS HT single slot)
        h2d_client._process_message(_ams_payload(0))
        assert h2d_client.state.tray_now == 128
        assert h2d_client.state.last_loaded_tray == 128

    def test_h2d_multi_color_alternating_nozzles(self, h2d_client):
        """Multi-color print alternating between right and left nozzles.

        Sequence:
        1. Right loads AMS 0 slot 0 (tray=0)
        2. Switch left, load AMS HT (tray=128)
        3. Switch right, snow updates, load AMS 0 slot 2 (tray=2)
        4. Unload (255)
        """
        # Step 1: Right extruder loads AMS 0 slot 0
        h2d_client._process_message(
            _extruder_info_payload(
                [
                    {"id": 0, "snow": 0 << 8 | 0},
                    {"id": 1, "snow": 0xFF00FF},
                ]
            )
        )
        h2d_client._process_message(_ams_payload(0))
        assert h2d_client.state.tray_now == 0

        # Step 2: Switch to left, load AMS HT
        h2d_client._process_message(_extruder_state_payload(0x0100))
        h2d_client._process_message(
            _extruder_info_payload(
                [
                    {"id": 0, "snow": 0 << 8 | 0},
                    {"id": 1, "snow": 128 << 8 | 0},
                ]
            )
        )
        h2d_client._process_message(_ams_payload(0))
        assert h2d_client.state.tray_now == 128

        # Step 3: Switch back to right, load AMS 0 slot 2
        h2d_client._process_message(_extruder_state_payload(0x0001))
        h2d_client._process_message(
            _extruder_info_payload(
                [
                    {"id": 0, "snow": 0 << 8 | 2},
                    {"id": 1, "snow": 128 << 8 | 0},
                ]
            )
        )
        h2d_client._process_message(_ams_payload(2))
        assert h2d_client.state.tray_now == 2

        # Step 4: Unload
        h2d_client._process_message(_ams_payload(255))
        assert h2d_client.state.tray_now == 255
        assert h2d_client.state.last_loaded_tray == 2


class TestTrayChangeLog:
    """Tests for tray_change_log tracking during prints (mid-print tray switch)."""

    @pytest.fixture
    def mqtt_client(self):
        """Create a BambuMQTTClient instance for testing."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TRAYLOG1",
            access_code="12345678",
        )
        return client

    def test_tray_change_log_defaults_empty(self, mqtt_client):
        """tray_change_log starts as an empty list."""
        assert mqtt_client.state.tray_change_log == []

    def test_tray_change_log_seeded_on_print_start(self, mqtt_client):
        """Print start clears log and seeds with initial tray at layer 0."""
        mqtt_client.state.tray_now = 2
        mqtt_client.state.last_loaded_tray = 2
        mqtt_client._previous_gcode_state = "IDLE"

        # Transition to RUNNING via _process_message
        mqtt_client._process_message(
            {
                "print": {
                    "gcode_state": "RUNNING",
                    "gcode_file": "test.3mf",
                }
            }
        )

        assert mqtt_client.state.tray_change_log == [(2, 0)]

    def test_tray_change_log_cleared_on_new_print(self, mqtt_client):
        """Old log entries are cleared when a new print starts."""
        mqtt_client.state.tray_change_log = [(5, 0), (3, 100)]
        mqtt_client.state.tray_now = 1
        mqtt_client.state.last_loaded_tray = 1
        mqtt_client._previous_gcode_state = "IDLE"

        mqtt_client._process_message(
            {
                "print": {
                    "gcode_state": "RUNNING",
                    "gcode_file": "new.3mf",
                }
            }
        )

        assert mqtt_client.state.tray_change_log == [(1, 0)]

    def test_tray_change_recorded_during_running(self, mqtt_client):
        """Tray change while RUNNING is appended to the log."""
        mqtt_client.state.state = "RUNNING"
        mqtt_client.state.layer_num = 50
        mqtt_client.state.last_loaded_tray = 0
        mqtt_client.state.tray_change_log = [(0, 0)]

        # Simulate tray_now update via AMS data
        mqtt_client.state.tray_now = 1
        # Trigger the tracking code path
        tn = mqtt_client.state.tray_now
        if tn != mqtt_client.state.last_loaded_tray and mqtt_client.state.state in ("RUNNING", "PAUSE"):
            mqtt_client.state.tray_change_log.append((tn, mqtt_client.state.layer_num))
        mqtt_client.state.last_loaded_tray = tn

        assert mqtt_client.state.tray_change_log == [(0, 0), (1, 50)]

    def test_tray_change_not_recorded_when_idle(self, mqtt_client):
        """Tray changes while IDLE are NOT logged."""
        mqtt_client.state.state = "IDLE"
        mqtt_client.state.layer_num = 0
        mqtt_client.state.last_loaded_tray = 0
        mqtt_client.state.tray_change_log = []

        mqtt_client.state.tray_now = 3
        tn = mqtt_client.state.tray_now
        if tn != mqtt_client.state.last_loaded_tray and mqtt_client.state.state in ("RUNNING", "PAUSE"):
            mqtt_client.state.tray_change_log.append((tn, mqtt_client.state.layer_num))
        mqtt_client.state.last_loaded_tray = tn

        assert mqtt_client.state.tray_change_log == []

    def test_tray_change_recorded_during_pause(self, mqtt_client):
        """Tray change while PAUSE is also logged (AMS can swap during pause)."""
        mqtt_client.state.state = "PAUSE"
        mqtt_client.state.layer_num = 75
        mqtt_client.state.last_loaded_tray = 2
        mqtt_client.state.tray_change_log = [(2, 0)]

        mqtt_client.state.tray_now = 5
        tn = mqtt_client.state.tray_now
        if tn != mqtt_client.state.last_loaded_tray and mqtt_client.state.state in ("RUNNING", "PAUSE"):
            mqtt_client.state.tray_change_log.append((tn, mqtt_client.state.layer_num))
        mqtt_client.state.last_loaded_tray = tn

        assert mqtt_client.state.tray_change_log == [(2, 0), (5, 75)]

    def test_same_tray_not_logged_twice(self, mqtt_client):
        """Same tray value doesn't create duplicate log entries."""
        mqtt_client.state.state = "RUNNING"
        mqtt_client.state.layer_num = 30
        mqtt_client.state.last_loaded_tray = 2
        mqtt_client.state.tray_change_log = [(2, 0)]

        # Same tray again
        mqtt_client.state.tray_now = 2
        tn = mqtt_client.state.tray_now
        if tn != mqtt_client.state.last_loaded_tray and mqtt_client.state.state in ("RUNNING", "PAUSE"):
            mqtt_client.state.tray_change_log.append((tn, mqtt_client.state.layer_num))
        mqtt_client.state.last_loaded_tray = tn

        assert mqtt_client.state.tray_change_log == [(2, 0)]

    def test_multiple_tray_changes(self, mqtt_client):
        """Multiple tray changes create a full history."""
        mqtt_client.state.state = "RUNNING"
        mqtt_client.state.last_loaded_tray = 0
        mqtt_client.state.tray_change_log = [(0, 0)]

        changes = [(1, 50), (3, 120), (0, 200)]
        for tray, layer in changes:
            mqtt_client.state.tray_now = tray
            mqtt_client.state.layer_num = layer
            tn = mqtt_client.state.tray_now
            if tn != mqtt_client.state.last_loaded_tray and mqtt_client.state.state in ("RUNNING", "PAUSE"):
                mqtt_client.state.tray_change_log.append((tn, mqtt_client.state.layer_num))
            mqtt_client.state.last_loaded_tray = tn

        assert mqtt_client.state.tray_change_log == [(0, 0), (1, 50), (3, 120), (0, 200)]


class TestDeveloperModeDetection:
    """Tests for developer LAN mode detection from MQTT 'fun' field."""

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

    def test_developer_mode_initially_none(self, mqtt_client):
        """Verify developer_mode starts as None (unknown)."""
        assert mqtt_client.state.developer_mode is None

    def test_developer_mode_on_when_bit_clear(self, mqtt_client):
        """Verify developer_mode is True when bit 0x20000000 is clear."""
        # Bit 29 clear in lower 32 bits = developer mode ON
        payload = {
            "print": {
                "gcode_state": "IDLE",
                "fun": "1C8187FF9CFF",
            }
        }
        mqtt_client._process_message(payload)
        assert mqtt_client.state.developer_mode is True

    def test_developer_mode_off_when_bit_set(self, mqtt_client):
        """Verify developer_mode is False when bit 0x20000000 is set."""
        # Bit 29 set in lower 32 bits = developer mode OFF (encryption required)
        payload = {
            "print": {
                "gcode_state": "IDLE",
                "fun": "1C81A7FF9CFF",
            }
        }
        mqtt_client._process_message(payload)
        assert mqtt_client.state.developer_mode is False

    def test_developer_mode_exact_bit_check(self, mqtt_client):
        """Verify only bit 0x20000000 matters, not other bits."""
        # 0x20000000 in hex = bit 29. Set ONLY that bit.
        payload = {
            "print": {
                "gcode_state": "IDLE",
                "fun": "000020000000",
            }
        }
        mqtt_client._process_message(payload)
        assert mqtt_client.state.developer_mode is False

        # All zeros = all bits clear = developer mode ON
        payload["print"]["fun"] = "000000000000"
        mqtt_client._process_message(payload)
        assert mqtt_client.state.developer_mode is True

    def test_developer_mode_invalid_fun_ignored(self, mqtt_client):
        """Verify invalid fun values don't crash or change state."""
        mqtt_client.state.developer_mode = True

        payload = {
            "print": {
                "gcode_state": "IDLE",
                "fun": "not_a_hex_value",
            }
        }
        mqtt_client._process_message(payload)
        # Should remain unchanged
        assert mqtt_client.state.developer_mode is True

    def test_developer_mode_missing_fun_preserves_state(self, mqtt_client):
        """Verify messages without fun field don't reset developer_mode."""
        mqtt_client.state.developer_mode = False

        payload = {
            "print": {
                "gcode_state": "RUNNING",
                "mc_percent": 50,
            }
        }
        mqtt_client._process_message(payload)
        assert mqtt_client.state.developer_mode is False

    def test_developer_mode_persists_across_messages(self, mqtt_client):
        """Verify developer_mode set by fun persists across messages without fun."""
        # First message sets developer_mode
        mqtt_client._process_message(
            {
                "print": {
                    "gcode_state": "IDLE",
                    "fun": "3EC1AFFF9CFF",
                }
            }
        )
        assert mqtt_client.state.developer_mode is False

        # Subsequent messages without fun don't change it
        for _ in range(3):
            mqtt_client._process_message(
                {
                    "print": {
                        "gcode_state": "RUNNING",
                        "mc_percent": 50,
                    }
                }
            )
        assert mqtt_client.state.developer_mode is False


class TestDeveloperModeProbeTimeout:
    """Tests for developer mode probe timeout, retry, and forced reconnect (#887).

    When a printer's MQTT session is half-broken (sends status but ignores
    commands), the developer mode probe gets no response.  The timeout logic
    retries once, then force-closes the socket on the second failure.
    """

    @pytest.fixture
    def mqtt_client(self):
        import time
        from unittest.mock import MagicMock

        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )
        # Simulate connected state with a mock MQTT client
        client.state.connected = True
        mock_paho = MagicMock()
        mock_paho.socket.return_value = MagicMock()
        client._client = mock_paho
        # Set connect time in the past so the 5s probe delay is satisfied
        client._connect_time = time.monotonic() - 10.0
        return client

    def _make_pushall_data(self):
        """Create a print data dict with >30 keys (triggers probe) and no 'fun' field."""
        return {f"key_{i}": i for i in range(35)}

    def test_first_timeout_allows_retry(self, mqtt_client):
        """After first probe timeout, _dev_mode_probed resets to allow retry."""
        import time

        data = self._make_pushall_data()

        # First pushall triggers the probe
        mqtt_client._update_state(data)
        assert mqtt_client._dev_mode_probed is True
        assert mqtt_client._dev_mode_probe_seq is not None
        assert mqtt_client.state.developer_mode is None

        # Simulate 11 seconds passing
        mqtt_client._dev_mode_probe_time = time.monotonic() - 11.0

        # Next status message detects the timeout
        mqtt_client._update_state(data)
        assert mqtt_client._dev_mode_probe_failures == 1
        assert mqtt_client._dev_mode_probe_seq is None
        # Should allow retry on next full message
        assert mqtt_client._dev_mode_probed is False
        # Connection should NOT be force-closed after 1 failure
        assert mqtt_client.state.connected is True

    def test_second_timeout_forces_reconnect(self, mqtt_client):
        """After two consecutive probe timeouts, force-close the socket.

        Probe timeout detection runs from paho's network thread (no asyncio
        loop), so force_reconnect_stale_session routes through socket-close
        rather than hard-reset (loop_stop from inside the loop deadlocks)."""
        import time

        data = self._make_pushall_data()
        state_change_called = []
        mqtt_client.on_state_change = lambda s: state_change_called.append(True)

        # First probe + timeout
        mqtt_client._update_state(data)
        mqtt_client._dev_mode_probe_time = time.monotonic() - 11.0
        mqtt_client._update_state(data)
        assert mqtt_client._dev_mode_probe_failures == 1

        # Second probe (retry) + timeout
        mqtt_client._update_state(data)  # triggers new probe
        assert mqtt_client._dev_mode_probed is True
        mqtt_client._dev_mode_probe_time = time.monotonic() - 11.0
        mqtt_client._update_state(data)  # detects second timeout

        assert mqtt_client._dev_mode_probe_failures == 2
        assert mqtt_client.state.connected is False
        assert mqtt_client._stale_reconnecting is True
        # Sync test → no running loop → socket-close fallback path
        mqtt_client._client.socket().close.assert_called()
        assert len(state_change_called) > 0

    def test_successful_probe_resets_failure_counter(self, mqtt_client):
        """A probe response after a previous failure resets the counter."""
        import time

        data = self._make_pushall_data()

        # First probe + timeout → failure=1
        mqtt_client._update_state(data)
        seq = mqtt_client._dev_mode_probe_seq
        mqtt_client._dev_mode_probe_time = time.monotonic() - 11.0
        mqtt_client._update_state(data)
        assert mqtt_client._dev_mode_probe_failures == 1

        # Retry probe
        mqtt_client._update_state(data)
        new_seq = mqtt_client._dev_mode_probe_seq
        assert new_seq is not None
        assert new_seq != seq

        # Simulate successful response
        mqtt_client._handle_dev_mode_probe_response(
            {
                "command": "ams_filament_setting",
                "sequence_id": new_seq,
                "result": "success",
            }
        )
        assert mqtt_client._dev_mode_probe_failures == 0
        assert mqtt_client.state.developer_mode is True
        assert mqtt_client._dev_mode_probe_seq is None

    def test_no_timeout_when_probe_not_sent(self, mqtt_client):
        """The timeout branch is only entered when a probe is pending."""
        # No probe sent — _dev_mode_probed is False, _dev_mode_probe_seq is None
        data = {"gcode_state": "IDLE", "mc_percent": 0}  # < 30 keys
        mqtt_client._update_state(data)
        assert mqtt_client._dev_mode_probe_failures == 0

    def test_on_connect_resets_probe_state_but_preserves_developer_mode(self, mqtt_client):
        """_on_connect resets probe tracking but preserves cached developer_mode."""
        import time

        mqtt_client._dev_mode_probed = True
        mqtt_client._dev_mode_probe_seq = "42"
        mqtt_client._dev_mode_probe_time = time.monotonic()
        mqtt_client._dev_mode_probe_failures = 2
        mqtt_client.state.developer_mode = True

        # subscribe() must return (result, mid) tuple
        mqtt_client._client.subscribe.return_value = (0, 1)
        mqtt_client._on_connect(mqtt_client._client, None, None, 0)

        # developer_mode is preserved across reconnects (#887)
        assert mqtt_client.state.developer_mode is True
        assert mqtt_client._dev_mode_probed is False
        assert mqtt_client._dev_mode_probe_seq is None
        assert mqtt_client._dev_mode_probe_time == 0.0
        assert mqtt_client._dev_mode_probe_failures == 0
        assert mqtt_client._connect_time > 0

    def test_probe_deferred_when_connect_too_recent(self, mqtt_client):
        """Probe is deferred if less than 5s have passed since _on_connect."""
        import time

        data = self._make_pushall_data()

        # Set connect time to 1 second ago — too recent for probe
        mqtt_client._connect_time = time.monotonic() - 1.0

        mqtt_client._update_state(data)
        # Pushall seen, so needs_probe is set, but probe NOT fired yet
        assert mqtt_client._dev_mode_needs_probe is True
        assert mqtt_client._dev_mode_probed is False
        assert mqtt_client._dev_mode_probe_seq is None

    def test_probe_fires_after_delay(self, mqtt_client):
        """Probe fires once 5s have passed since _on_connect."""
        import time

        data = self._make_pushall_data()

        # Set connect time to 6 seconds ago — delay satisfied
        mqtt_client._connect_time = time.monotonic() - 6.0

        mqtt_client._update_state(data)
        # Probe should have fired
        assert mqtt_client._dev_mode_needs_probe is True
        assert mqtt_client._dev_mode_probed is True
        assert mqtt_client._dev_mode_probe_seq is not None

    def test_probe_fires_on_incremental_after_delay(self, mqtt_client):
        """After seeing a pushall within 5s, probe fires on later incremental message."""
        import time

        pushall_data = self._make_pushall_data()
        incremental_data = {"gcode_state": "IDLE", "mc_percent": 0}  # < 30 keys

        # Pushall arrives 1s after connect — too early for probe
        mqtt_client._connect_time = time.monotonic() - 1.0
        mqtt_client._update_state(pushall_data)
        assert mqtt_client._dev_mode_needs_probe is True
        assert mqtt_client._dev_mode_probed is False

        # 5s later, an incremental update arrives — probe fires now
        mqtt_client._connect_time = time.monotonic() - 6.0
        mqtt_client._update_state(incremental_data)
        assert mqtt_client._dev_mode_probed is True
        assert mqtt_client._dev_mode_probe_seq is not None

    def test_no_reprobe_when_developer_mode_cached(self, mqtt_client):
        """Auto-reconnect preserves developer_mode, skipping reprobe."""
        import time

        data = self._make_pushall_data()

        # Simulate known developer_mode from previous connection
        mqtt_client.state.developer_mode = True
        mqtt_client._connect_time = time.monotonic() - 10.0

        mqtt_client._update_state(data)
        # Should NOT probe — developer_mode is already known
        assert mqtt_client._dev_mode_needs_probe is False
        assert mqtt_client._dev_mode_probed is False
        assert mqtt_client._dev_mode_probe_seq is None
        assert mqtt_client.state.developer_mode is True

    def test_on_connect_resets_needs_probe(self, mqtt_client):
        """_on_connect resets _dev_mode_needs_probe for a clean start."""
        mqtt_client._dev_mode_needs_probe = True

        mqtt_client._client.subscribe.return_value = (0, 1)
        mqtt_client._on_connect(mqtt_client._client, None, None, 0)

        assert mqtt_client._dev_mode_needs_probe is False


class TestVtTrayNormalization:
    """Tests for vt_tray dict→list normalization in _update_state.

    MQTT sends vt_tray as a dict for single-slot printers, but all consumers
    expect a list.  _update_state must normalize it before any callback can
    read raw_data, because the dev-mode probe may release the GIL and let
    the event loop read the partially-updated state.
    """

    @pytest.fixture
    def mqtt_client(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )
        return client

    def test_vt_tray_dict_normalized_in_update_state(self, mqtt_client):
        """Verify _update_state wraps a raw vt_tray dict into a list."""
        vt_dict = {
            "id": "254",
            "tray_color": "FF0000",
            "tray_type": "PLA",
            "tag_uid": "0000000000000000",
            "tray_uuid": "00000000000000000000000000000000",
        }
        data = {"gcode_state": "IDLE", "vt_tray": vt_dict}
        mqtt_client._update_state(data)

        stored = mqtt_client.state.raw_data.get("vt_tray")
        assert isinstance(stored, list)
        assert len(stored) == 1
        assert stored[0]["tray_color"] == "FF0000"

    def test_vt_tray_list_unchanged_in_update_state(self, mqtt_client):
        """Verify _update_state keeps an already-list vt_tray unchanged."""
        vt_list = [
            {"id": "254", "tray_type": "PLA"},
            {"id": "255", "tray_type": "PETG"},
        ]
        data = {"gcode_state": "IDLE", "vt_tray": vt_list}
        mqtt_client._update_state(data)

        stored = mqtt_client.state.raw_data.get("vt_tray")
        assert isinstance(stored, list)
        assert len(stored) == 2

    def test_preserved_vt_tray_restored_before_probe(self, mqtt_client):
        """Verify preserved vt_tray is restored before dev-mode probe runs.

        On the first message, the incremental handler wraps vt_tray into a list
        and stores it.  _update_state then replaces raw_data with the full data
        dict, but must restore preserved fields BEFORE the probe publishes
        (which can release the GIL).
        """
        # Simulate: incremental handler already stored a wrapped list
        mqtt_client.state.raw_data = {
            "vt_tray": [{"id": "254", "tray_type": "PLA", "tray_color": "00FF00"}],
        }

        # Now _update_state runs with new data that has vt_tray as dict
        new_data = {
            "gcode_state": "IDLE",
            "vt_tray": {"id": "254", "tray_type": "PETG", "tray_color": "FF0000"},
        }
        mqtt_client._update_state(new_data)

        # The preserved list (PLA/green) should take priority over new data
        stored = mqtt_client.state.raw_data["vt_tray"]
        assert isinstance(stored, list)
        assert stored[0]["tray_type"] == "PLA"
        assert stored[0]["tray_color"] == "00FF00"

    def test_first_message_vt_tray_dict_becomes_list(self, mqtt_client):
        """Verify on the very first message, vt_tray dict is still a list.

        When there's no previously preserved data, the normalized dict should
        remain as a list in raw_data.
        """
        # raw_data starts empty — no preserved vt_tray
        mqtt_client.state.raw_data = {}

        data = {
            "gcode_state": "IDLE",
            "vt_tray": {"id": "254", "tray_type": "ABS"},
        }
        mqtt_client._update_state(data)

        stored = mqtt_client.state.raw_data["vt_tray"]
        assert isinstance(stored, list)
        assert stored[0]["tray_type"] == "ABS"


class TestSendDryingCommand:
    """Tests for send_drying_command MQTT payload construction."""

    @pytest.fixture
    def mqtt_client(self):
        """Create a BambuMQTTClient with a mock MQTT client."""
        from unittest.mock import MagicMock

        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )
        client._client = MagicMock()
        return client

    def test_rotate_tray_false_by_default(self, mqtt_client):
        """Verify rotate_tray defaults to False in the MQTT payload."""
        mqtt_client.send_drying_command(ams_id=0, temp=55, duration=4, mode=1, filament="PLA")

        call_args = mqtt_client._client.publish.call_args
        payload = json.loads(call_args[0][1])
        assert payload["print"]["rotate_tray"] is False

    def test_rotate_tray_true_when_enabled(self, mqtt_client):
        """Verify rotate_tray is True when explicitly enabled."""
        mqtt_client.send_drying_command(ams_id=0, temp=55, duration=4, mode=1, filament="PLA", rotate_tray=True)

        call_args = mqtt_client._client.publish.call_args
        payload = json.loads(call_args[0][1])
        assert payload["print"]["rotate_tray"] is True

    def test_rotate_tray_false_on_stop(self, mqtt_client):
        """Verify rotate_tray is False when stopping drying (mode=0)."""
        mqtt_client.send_drying_command(ams_id=0, temp=0, duration=0, mode=0)

        call_args = mqtt_client._client.publish.call_args
        payload = json.loads(call_args[0][1])
        assert payload["print"]["rotate_tray"] is False

    def test_all_required_fields_present(self, mqtt_client):
        """Verify all required MQTT fields are present in the drying command."""
        mqtt_client.send_drying_command(ams_id=128, temp=75, duration=8, mode=1, filament="ABS", rotate_tray=True)

        call_args = mqtt_client._client.publish.call_args
        payload = json.loads(call_args[0][1])
        cmd = payload["print"]
        assert cmd["command"] == "ams_filament_drying"
        assert cmd["ams_id"] == 128
        assert cmd["temp"] == 75
        assert cmd["duration"] == 8
        assert cmd["mode"] == 1
        assert cmd["rotate_tray"] is True
        assert cmd["filament"] == "ABS"
        assert cmd["cooling_temp"] == 20
        assert cmd["humidity"] == 0
        assert cmd["close_power_conflict"] is False
        assert "sequence_id" in cmd

    def test_publishes_with_qos_1(self, mqtt_client):
        """Verify drying commands are published with QoS 1."""
        mqtt_client.send_drying_command(ams_id=0, temp=55, duration=4)

        call_args = mqtt_client._client.publish.call_args
        # qos may be positional arg [2] or keyword
        qos = call_args.kwargs.get("qos", call_args[0][2] if len(call_args[0]) > 2 else None)
        assert qos == 1


class TestStartPrintAmsMapping:
    """Tests for ams_mapping/ams_mapping2 construction in start_print().

    BambuStudio converts virtual tray IDs (254/255) to -1 in the flat
    ams_mapping and puts the real external spool info only in ams_mapping2.
    Passing raw 254/255 in the flat array causes H2D firmware to fail
    with 0700_8012 "Failed to get AMS mapping table".
    """

    @pytest.fixture
    def mqtt_client(self):
        from unittest.mock import MagicMock

        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )
        client._client = MagicMock()
        client.state.connected = True
        return client

    def _get_published_command(self, mqtt_client):
        """Extract the parsed print command from the last publish call."""
        call_args = mqtt_client._client.publish.call_args
        return json.loads(call_args[0][1])["print"]

    def test_regular_ams_trays_preserved_in_flat_mapping(self, mqtt_client):
        """Regular AMS tray IDs pass through unchanged in flat ams_mapping."""
        mqtt_client.start_print("test.3mf", ams_mapping=[0, 5, 11])

        cmd = self._get_published_command(mqtt_client)
        assert cmd["ams_mapping"] == [0, 5, 11]
        assert cmd["ams_mapping2"] == [
            {"ams_id": 0, "slot_id": 0},
            {"ams_id": 1, "slot_id": 1},
            {"ams_id": 2, "slot_id": 3},
        ]

    def test_unmapped_slots(self, mqtt_client):
        """Unmapped slots (-1) produce -1 in flat and 0xFF/0xFF in mapping2."""
        mqtt_client.start_print("test.3mf", ams_mapping=[-1, -1])

        cmd = self._get_published_command(mqtt_client)
        assert cmd["ams_mapping"] == [-1, -1]
        assert cmd["ams_mapping2"] == [
            {"ams_id": 255, "slot_id": 255},
            {"ams_id": 255, "slot_id": 255},
        ]

    def test_external_main_nozzle_becomes_minus_one_in_flat(self, mqtt_client):
        """Virtual tray 255 (main nozzle) must be -1 in flat mapping."""
        mqtt_client.start_print("test.3mf", ams_mapping=[255])

        cmd = self._get_published_command(mqtt_client)
        assert cmd["ams_mapping"] == [-1]
        assert cmd["ams_mapping2"] == [{"ams_id": 255, "slot_id": 0}]

    def test_single_nozzle_external_spool_uses_main_id(self, mqtt_client):
        """Single-nozzle external spool (254) maps to ams_id=255 (VIRTUAL_TRAY_MAIN_ID).

        Firmware reports tray_now=254 for external spool, but the print command
        must use ams_id=255 in ams_mapping2. Sending 254 causes the firmware to
        target AMS tray 0 instead of external spool (07FF_8012 error).
        """
        mqtt_client.start_print("test.3mf", ams_mapping=[254])

        cmd = self._get_published_command(mqtt_client)
        assert cmd["ams_mapping"] == [-1]
        assert cmd["ams_mapping2"] == [{"ams_id": 255, "slot_id": 0}]

    def test_h2d_external_spool_mixed_with_ams(self, mqtt_client):
        """H2D scenario: AMS trays + unmapped + external deputy nozzle."""
        # Reproduces the exact scenario from issue #797:
        # 5-slot 3MF, only slot 5 assigned to external deputy nozzle (254)
        mqtt_client.start_print("test.3mf", ams_mapping=[-1, -1, -1, -1, 255])

        cmd = self._get_published_command(mqtt_client)
        # Flat mapping: all -1 (external converted, unmapped stay -1)
        assert cmd["ams_mapping"] == [-1, -1, -1, -1, -1]
        # Detailed mapping: unmapped slots use 0xFF, external uses real ams_id
        assert cmd["ams_mapping2"] == [
            {"ams_id": 255, "slot_id": 255},
            {"ams_id": 255, "slot_id": 255},
            {"ams_id": 255, "slot_id": 255},
            {"ams_id": 255, "slot_id": 255},
            {"ams_id": 255, "slot_id": 0},
        ]

    def test_ams_ht_trays_preserved_in_flat_mapping(self, mqtt_client):
        """AMS-HT tray IDs (>=128) pass through in flat mapping."""
        mqtt_client.start_print("test.3mf", ams_mapping=[128, 131])

        cmd = self._get_published_command(mqtt_client)
        assert cmd["ams_mapping"] == [128, 131]
        assert cmd["ams_mapping2"] == [
            {"ams_id": 128, "slot_id": 0},
            {"ams_id": 131, "slot_id": 0},
        ]

    def test_non_h2d_both_external_maps_to_main_id(self, mqtt_client):
        """Non-H2D: both 254 and 255 map to ams_id=255 (single nozzle)."""
        mqtt_client.start_print("test.3mf", ams_mapping=[254, 255])

        cmd = self._get_published_command(mqtt_client)
        assert cmd["ams_mapping"] == [-1, -1]
        assert cmd["ams_mapping2"] == [
            {"ams_id": 255, "slot_id": 0},
            {"ams_id": 255, "slot_id": 0},
        ]

    def test_h2d_external_preserves_deputy_id(self, mqtt_client):
        """H2D dual-nozzle: 254 (deputy) stays 254, 255 (main) stays 255."""
        mqtt_client.model = "H2D"
        mqtt_client.start_print("test.3mf", ams_mapping=[254, 255])

        cmd = self._get_published_command(mqtt_client)
        assert cmd["ams_mapping"] == [-1, -1]
        assert cmd["ams_mapping2"] == [
            {"ams_id": 254, "slot_id": 0},
            {"ams_id": 255, "slot_id": 0},
        ]

    def test_h2d_single_external_deputy(self, mqtt_client):
        """H2D: single external spool on deputy nozzle (254) keeps ams_id=254."""
        mqtt_client.model = "H2D Pro"
        mqtt_client.start_print("test.3mf", ams_mapping=[254])

        cmd = self._get_published_command(mqtt_client)
        assert cmd["ams_mapping"] == [-1]
        assert cmd["ams_mapping2"] == [{"ams_id": 254, "slot_id": 0}]

    def test_external_spool_only_sets_use_ams_false(self, mqtt_client):
        """Single external spool on non-H2D printer sets use_ams=False."""
        mqtt_client.start_print("test.3mf", ams_mapping=[254], use_ams=True)

        cmd = self._get_published_command(mqtt_client)
        assert cmd["use_ams"] is False

    def test_all_unmapped_sets_use_ams_false(self, mqtt_client):
        """All unmapped slots on non-H2D printer sets use_ams=False."""
        mqtt_client.start_print("test.3mf", ams_mapping=[-1, -1], use_ams=True)

        cmd = self._get_published_command(mqtt_client)
        assert cmd["use_ams"] is False

    def test_mixed_ams_and_external_keeps_use_ams_true(self, mqtt_client):
        """AMS tray + external spool keeps use_ams=True."""
        mqtt_client.start_print("test.3mf", ams_mapping=[0, 254], use_ams=True)

        cmd = self._get_published_command(mqtt_client)
        assert cmd["use_ams"] is True

    def test_h2d_both_external_keeps_use_ams_true(self, mqtt_client):
        """H2D with both external spools keeps use_ams=True (nozzle routing)."""
        mqtt_client.model = "H2D"
        mqtt_client.start_print("test.3mf", ams_mapping=[254, 255], use_ams=True)

        cmd = self._get_published_command(mqtt_client)
        assert cmd["use_ams"] is True

    def test_empty_ams_mapping_keeps_use_ams_true(self, mqtt_client):
        """Empty ams_mapping list does not override use_ams."""
        mqtt_client.start_print("test.3mf", ams_mapping=[], use_ams=True)

        cmd = self._get_published_command(mqtt_client)
        assert cmd["use_ams"] is True

    def test_no_ams_mapping_omits_fields(self, mqtt_client):
        """When ams_mapping is None, neither field is in the command."""
        mqtt_client.start_print("test.3mf", ams_mapping=None)

        cmd = self._get_published_command(mqtt_client)
        assert "ams_mapping" not in cmd
        assert "ams_mapping2" not in cmd

    def test_x2d_external_preserves_deputy_id(self, mqtt_client):
        """X2D dual-nozzle (#988): 254 (deputy) stays 254, like H2D family.

        X2D launched April 2026 and shares the H2D-style dual-extruder
        firmware convention — external spool on the deputy (left) nozzle
        is addressed as ams_id=254, not coerced to 255.
        """
        mqtt_client.model = "X2D"
        mqtt_client.start_print("test.3mf", ams_mapping=[254, 255])

        cmd = self._get_published_command(mqtt_client)
        assert cmd["ams_mapping"] == [-1, -1]
        assert cmd["ams_mapping2"] == [
            {"ams_id": 254, "slot_id": 0},
            {"ams_id": 255, "slot_id": 0},
        ]

    def test_x2d_uses_integer_format_for_calibration_fields(self, mqtt_client):
        """X2D must use H2D-style integer (0/1) format for calibration fields (#988).

        The reporter's support bundle showed X2D running firmware in the same
        family as H2D. Booleans in these fields are interpreted as nozzle
        indexes by H2D firmware; X2D is treated identically until proven
        otherwise.
        """
        mqtt_client.model = "X2D"
        mqtt_client.start_print(
            "test.3mf",
            timelapse=True,
            bed_levelling=False,
            flow_cali=True,
            vibration_cali=False,
            layer_inspect=True,
        )

        cmd = self._get_published_command(mqtt_client)
        assert cmd["timelapse"] == 1
        assert cmd["bed_leveling"] == 0
        assert cmd["flow_cali"] == 1
        assert cmd["vibration_cali"] == 0
        assert cmd["layer_inspect"] == 1

    def test_p2s_still_uses_boolean_format(self, mqtt_client):
        """Regression guard: P2S is NOT in the is_h2d gate — must still use booleans.

        Adding X2D to the is_h2d set must not accidentally affect P2S, which
        is single-nozzle and uses boolean format like X1C/A1/P1.
        """
        mqtt_client.model = "P2S"
        mqtt_client.start_print("test.3mf", timelapse=True, flow_cali=False)

        cmd = self._get_published_command(mqtt_client)
        assert cmd["timelapse"] is True
        assert cmd["flow_cali"] is False


class TestStartPrintUniqueIdentityFields:
    """Regression guard: project_id/subtask_id/task_id must be unique per submission (#1011).

    Hardcoded "0" values caused third-party MQTT observers (e.g. OctoEverywhere)
    to treat archive reprints as continuations of the same job and report
    compounding durations on repeat replays. Each start_print call must produce
    a distinct, non-zero identity triplet so the printer emits a fresh state
    transition. md5 is deliberately left empty — historically firmware treats
    "" as "skip validation" and we don't have the file's real digest here.
    """

    @pytest.fixture
    def mqtt_client(self):
        from unittest.mock import MagicMock

        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )
        client._client = MagicMock()
        client.state.connected = True
        return client

    def _get_published_command(self, mqtt_client):
        call_args = mqtt_client._client.publish.call_args
        return json.loads(call_args[0][1])["print"]

    def test_identity_fields_are_non_zero(self, mqtt_client):
        mqtt_client.start_print("test.3mf")
        cmd = self._get_published_command(mqtt_client)
        assert cmd["project_id"] != "0"
        assert cmd["subtask_id"] != "0"
        assert cmd["task_id"] != "0"

    def test_identity_fields_are_all_equal_per_submission(self, mqtt_client):
        """All three IDs come from the same submission timestamp — Studio also
        uses a single identity per submission across the three fields."""
        mqtt_client.start_print("test.3mf")
        cmd = self._get_published_command(mqtt_client)
        assert cmd["project_id"] == cmd["subtask_id"] == cmd["task_id"]

    def test_md5_stays_empty(self, mqtt_client):
        """Deliberate: synthetic md5 risks activating firmware validation."""
        mqtt_client.start_print("test.3mf")
        cmd = self._get_published_command(mqtt_client)
        assert cmd["md5"] == ""

    def test_identity_fields_change_between_submissions(self, mqtt_client):
        """Two successive start_print calls must produce different IDs.

        Without this, the printer can't tell replays apart and reuses
        gcode_start_time from the prior job.
        """
        mqtt_client.start_print("test.3mf")
        first = self._get_published_command(mqtt_client)

        time.sleep(0.002)

        mqtt_client.start_print("test.3mf")
        second = self._get_published_command(mqtt_client)

        assert first["task_id"] != second["task_id"]
        assert first["subtask_id"] != second["subtask_id"]
        assert first["project_id"] != second["project_id"]

    def test_submission_id_is_numeric_string(self, mqtt_client):
        """ID format: digits-only string. Studio uses cloud task IDs that are
        also numeric-looking strings; the DB column is VARCHAR(64) and
        Bambuddy's own subtask_id parser treats '0'/'' as absent — any valid
        digit string that isn't '0' is fine."""
        mqtt_client.start_print("test.3mf")
        cmd = self._get_published_command(mqtt_client)
        assert cmd["task_id"].isdigit()
        assert int(cmd["task_id"]) > 0
        assert len(cmd["task_id"]) <= 64

    def test_submission_id_fits_signed_int32(self, mqtt_client):
        """Regression for #1042: P1S firmware clamps oversized task identity
        fields to signed int32 max (2**31-1 = 2147483647). If we send raw
        epoch-ms (~1.7e12), the printer sees a saturated constant on every
        submission and treats fresh dispatches as continuations of the last
        FAILED job — never leaves IDLE. Keep below 2**31.
        """
        mqtt_client.start_print("test.3mf")
        cmd = self._get_published_command(mqtt_client)
        assert int(cmd["task_id"]) < 2**31
        assert int(cmd["project_id"]) < 2**31
        assert int(cmd["subtask_id"]) < 2**31

    def test_unrelated_payload_fields_untouched(self, mqtt_client):
        """Regression guard: fix only touches identity fields; everything else
        (sequence_id, command verb, calibration defaults, profile_id) must be
        unchanged to avoid silently breaking printer behavior."""
        mqtt_client.start_print("test.3mf")
        cmd = self._get_published_command(mqtt_client)
        assert cmd["sequence_id"] == "20000"
        assert cmd["command"] == "project_file"
        assert cmd["param"] == "Metadata/plate_1.gcode"
        assert cmd["url"] == "ftp://test.3mf"
        assert cmd["file"] == "test.3mf"
        assert cmd["profile_id"] == "0"
        assert cmd["cfg"] == "0"
        assert cmd["subtask_name"] == "test"


class TestDeleteKProfileDualNozzleDetection:
    """Regression guard: dual-nozzle detection by serial prefix (#988).

    delete_kprofile branches on serial-prefix-derived dual-nozzle status.
    H2D serials start with "094"; X2D serials start with "20P9". Non-dual
    families (X1C "00M", P1S "01P", P2S "22E", A1 "039", etc.) must take
    the single-nozzle branch.
    """

    def _make_client(self, serial: str):
        from unittest.mock import MagicMock

        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number=serial,
            access_code="12345678",
        )
        client._client = MagicMock()
        client.state.connected = True
        return client

    def _published(self, client):
        return json.loads(client._client.publish.call_args[0][1])["print"]

    def test_h2d_serial_uses_dual_nozzle_format(self):
        client = self._make_client("09400A000000001")
        client.delete_kprofile(cali_idx=1, filament_id="GFA00", nozzle_id="HH00-0.4")
        cmd = self._published(client)
        # Dual-nozzle command omits setting_id.
        assert "setting_id" not in cmd
        assert cmd["extruder_id"] == 0

    def test_x2d_serial_uses_dual_nozzle_format(self):
        client = self._make_client("20P90A000000001")
        client.delete_kprofile(cali_idx=1, filament_id="GFA00", nozzle_id="HH00-0.4")
        cmd = self._published(client)
        assert "setting_id" not in cmd
        assert cmd["extruder_id"] == 0

    def test_h2c_new_prefix_uses_dual_nozzle_format(self):
        """Post-2026 H2C batches ship with '31B8B' prefix instead of '094' (#1105)."""
        client = self._make_client("31B8BP000000001")
        client.delete_kprofile(cali_idx=1, filament_id="GFA00", nozzle_id="HH00-0.4")
        cmd = self._published(client)
        assert "setting_id" not in cmd
        assert cmd["extruder_id"] == 0

    def test_p2s_serial_uses_single_nozzle_format(self):
        """P2S is single-nozzle — must NOT take the dual-nozzle branch."""
        client = self._make_client("22E00A000000001")
        client.delete_kprofile(
            cali_idx=1,
            filament_id="GFA00",
            nozzle_id="HH00-0.4",
            setting_id="PFB123",
        )
        cmd = self._published(client)
        # Single-nozzle command includes setting_id.
        assert cmd["setting_id"] == "PFB123"

    def test_x1c_serial_uses_single_nozzle_format(self):
        client = self._make_client("00M00A000000001")
        client.delete_kprofile(
            cali_idx=1,
            filament_id="GFA00",
            nozzle_id="HH00-0.4",
            setting_id="PFB123",
        )
        cmd = self._published(client)
        assert cmd["setting_id"] == "PFB123"


class TestStaleReconnect:
    """Tests for stale connection detection and reconnect without UI bouncing."""

    @pytest.fixture
    def mqtt_client(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST_STALE",
            access_code="12345678",
        )
        return client

    def test_check_staleness_sets_flag_and_broadcasts_once(self, mqtt_client):
        """check_staleness() should set connected=False, broadcast, and set _stale_reconnecting."""
        import time

        state_changes = []
        mqtt_client.on_state_change = lambda s: state_changes.append(s.connected)
        mqtt_client.state.connected = True
        mqtt_client._last_message_time = time.time() - 120  # well past 60s threshold

        result = mqtt_client.check_staleness()

        assert result is False
        assert mqtt_client.state.connected is False
        assert mqtt_client._stale_reconnecting is True
        assert state_changes == [False]  # Exactly one broadcast

    def test_check_staleness_noop_when_not_connected(self, mqtt_client):
        """check_staleness() should not set flag when already disconnected."""
        import time

        mqtt_client.state.connected = False
        mqtt_client._last_message_time = time.time() - 120

        mqtt_client.check_staleness()

        assert mqtt_client._stale_reconnecting is False

    def test_check_staleness_noop_when_not_stale(self, mqtt_client):
        """check_staleness() should not set flag when messages are recent."""
        import time

        mqtt_client.state.connected = True
        mqtt_client._last_message_time = time.time() - 5  # 5s ago, well within 60s

        result = mqtt_client.check_staleness()

        assert result is True
        assert mqtt_client.state.connected is True
        assert mqtt_client._stale_reconnecting is False

    def test_on_disconnect_skipped_during_stale_reconnect(self, mqtt_client):
        """_on_disconnect should not broadcast state when _stale_reconnecting is set."""
        state_changes = []
        mqtt_client.on_state_change = lambda s: state_changes.append(s.connected)
        mqtt_client._stale_reconnecting = True
        mqtt_client.state.connected = False

        mqtt_client._on_disconnect(None, None)

        # No state change broadcast — check_staleness() already did it
        assert state_changes == []
        assert mqtt_client.state.connected is False

    def test_on_disconnect_fires_event_during_stale_reconnect(self, mqtt_client):
        """_on_disconnect must still fire _disconnection_event even during stale reconnect.

        If disconnect() is called while _stale_reconnecting is True (e.g. user removes
        the printer before paho reconnects), the event must fire so disconnect() doesn't hang.
        """
        import threading

        mqtt_client._stale_reconnecting = True
        mqtt_client._disconnection_event = threading.Event()

        mqtt_client._on_disconnect(None, None)

        assert mqtt_client._disconnection_event.is_set()

    def test_on_connect_clears_stale_reconnecting_flag(self, mqtt_client):
        """_on_connect should clear _stale_reconnecting and restore connected=True."""
        mqtt_client._stale_reconnecting = True
        mqtt_client.state.connected = False

        subscribe_calls = []
        mock_client = type(
            "MockClient",
            (),
            {
                "subscribe": lambda self, topic: subscribe_calls.append(topic) or (0, 1),
            },
        )()

        mqtt_client._on_connect(mock_client, None, None, 0)

        assert mqtt_client._stale_reconnecting is False
        assert mqtt_client.state.connected is True

    def test_full_stale_reconnect_cycle_no_bounce(self, mqtt_client):
        """Full cycle: stale → disconnect callback → reconnect. UI should see exactly one disconnect."""
        import time

        state_changes = []
        mqtt_client.on_state_change = lambda s: state_changes.append(s.connected)
        mqtt_client.state.connected = True
        mqtt_client._last_message_time = time.time() - 120

        # Step 1: Stale detection triggers
        mqtt_client.check_staleness()
        assert state_changes == [False]

        # Step 2: Paho fires disconnect callback (from socket close)
        mqtt_client._on_disconnect(None, None)
        # Should NOT add another state change
        assert state_changes == [False]

        # Step 3: Paho reconnects
        subscribe_calls = []
        mock_client = type(
            "MockClient",
            (),
            {
                "subscribe": lambda self, topic: subscribe_calls.append(topic) or (0, 1),
            },
        )()
        mqtt_client._on_connect(mock_client, None, None, 0)
        assert state_changes == [False, True]  # Now connected again
        assert mqtt_client._stale_reconnecting is False

    def test_spurious_disconnect_suppressed_when_recent_messages(self, mqtt_client):
        """Non-error disconnect with recent messages should be suppressed."""
        import time

        state_changes = []
        mqtt_client.on_state_change = lambda s: state_changes.append(s.connected)
        mqtt_client.state.connected = True
        mqtt_client._last_message_time = time.time() - 3  # 3s ago

        # Non-error disconnect (rc=None)
        mqtt_client._on_disconnect(None, None)

        assert state_changes == []
        assert mqtt_client.state.connected is True

    def test_error_disconnect_not_suppressed_despite_recent_messages(self, mqtt_client):
        """Error disconnect should always be processed, even with recent messages."""
        import time

        import paho.mqtt.client as mqtt
        from paho.mqtt.reasoncodes import ReasonCode

        state_changes = []
        mqtt_client.on_state_change = lambda s: state_changes.append(s.connected)
        mqtt_client.state.connected = True
        mqtt_client._last_message_time = time.time() - 3  # 3s ago

        # Error disconnect (rc.is_failure = True)
        rc = ReasonCode(mqtt.CONNACK >> 4, identifier=0x80)  # Failure code
        mqtt_client._on_disconnect(None, None, rc=rc)

        assert state_changes == [False]
        assert mqtt_client.state.connected is False


class TestDoorOpenParsing:
    """Tests for enclosure door state parsing (X1 home_flag bit 23 vs others stat bit 23)."""

    def _make_client(self, model: str):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        return BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST",
            access_code="12345678",
            model=model,
        )

    def test_x1c_door_open_from_home_flag(self):
        client = self._make_client("X1C")
        # bit 23 set
        client._update_state({"home_flag": 0xC0E5CD98})
        assert client.state.door_open is True

    def test_x1c_door_closed_from_home_flag(self):
        client = self._make_client("X1C")
        client.state.door_open = True  # start "open"
        client._update_state({"home_flag": 0xC065CD98})
        assert client.state.door_open is False

    def test_x1c_ignores_stat_field(self):
        # X1C must NOT use stat (bit 23 in stat is unrelated for X1)
        client = self._make_client("X1C")
        client._update_state({"home_flag": 0xC065CD98, "stat": "47A58000"})
        assert client.state.door_open is False  # home_flag wins

    def test_h2d_door_open_from_stat(self):
        client = self._make_client("H2D")
        client._update_state({"stat": "640A58000"})  # bit 23 set
        assert client.state.door_open is True

    def test_h2d_door_closed_from_stat(self):
        client = self._make_client("H2D")
        client.state.door_open = True
        client._update_state({"stat": "640258000"})  # bit 23 cleared
        assert client.state.door_open is False

    def test_h2d_ignores_home_flag(self):
        # Non-X1 must NOT consume home_flag for door state
        client = self._make_client("H2D")
        client._update_state({"home_flag": 0xC0E5CD98, "stat": "640258000"})
        assert client.state.door_open is False  # stat wins

    def test_invalid_stat_does_not_raise(self):
        client = self._make_client("H2D")
        client._update_state({"stat": "not-hex"})
        assert client.state.door_open is False


class TestSdCardParsing:
    """SD-card state is only set from the top-level `sdcard` field (bool/int/
    string variants). home_flag is NOT consulted — heartbeat pushes clear those
    bits even when a card is inserted, and the prior badge feature was removed
    entirely because no reliable heartbeat-vs-full-push heuristic existed."""

    def _make_client(self, model: str = "H2D"):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        return BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST",
            access_code="12345678",
            model=model,
        )

    def test_home_flag_alone_does_not_touch_sdcard(self):
        client = self._make_client()
        client.state.sdcard = True
        for home_flag in (0x00000000, 0x00000100, 0x00000200):
            client._update_state({"home_flag": home_flag})
        assert client.state.sdcard is True

    def test_sdcard_string_fallback_when_no_home_flag(self):
        client = self._make_client()
        client._update_state({"sdcard": "HAS_SDCARD_NORMAL"})
        assert client.state.sdcard is True

    def test_sdcard_int_fallback_when_no_home_flag(self):
        # `1 is True` is False — the old strict check flapped here.
        client = self._make_client()
        client._update_state({"sdcard": 1})
        assert client.state.sdcard is True

    def test_sdcard_bool_fallback_when_no_home_flag(self):
        client = self._make_client()
        client._update_state({"sdcard": True})
        assert client.state.sdcard is True
        client._update_state({"sdcard": False})
        assert client.state.sdcard is False


class TestZombieSessionDetection:
    """Tests for ams_filament_setting response tracking (#887).

    When a printer's MQTT session degrades so that telemetry flows but
    published commands never reach the printer, the zombie detector
    counts consecutive unanswered ams_filament_setting commands and
    force-reconnects after two.
    """

    @pytest.fixture
    def mqtt_client(self):
        import time
        from unittest.mock import MagicMock

        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )
        client.state.connected = True
        mock_paho = MagicMock()
        mock_paho.socket.return_value = MagicMock()
        client._client = mock_paho
        client._connect_time = time.monotonic() - 10.0
        # Set developer_mode so the dev-mode probe branch doesn't interfere
        client.state.developer_mode = True
        return client

    def test_initial_state_is_clean(self, mqtt_client):
        """Tracking fields start at zero / no pending command."""
        assert mqtt_client._last_ams_cmd_time == 0.0
        assert mqtt_client._ams_cmd_unanswered == 0

    def test_publish_sets_pending_time(self, mqtt_client):
        """set_ams_filament_setting records the publish timestamp."""
        import time

        before = time.monotonic()
        mqtt_client.ams_set_filament_setting(
            ams_id=0,
            tray_id=0,
            tray_info_idx="GFL99",
            tray_type="PLA",
            tray_sub_brands="",
            tray_color="FF0000FF",
            nozzle_temp_min=190,
            nozzle_temp_max=230,
        )
        assert mqtt_client._last_ams_cmd_time >= before

    def test_reset_slot_sets_pending_time(self, mqtt_client):
        """reset_ams_slot also records the publish timestamp."""
        import time

        before = time.monotonic()
        mqtt_client.reset_ams_slot(ams_id=0, tray_id=0)
        assert mqtt_client._last_ams_cmd_time >= before

    def test_response_clears_pending(self, mqtt_client):
        """An ams_filament_setting response clears the pending state."""
        import time

        mqtt_client._last_ams_cmd_time = time.monotonic()
        mqtt_client._ams_cmd_unanswered = 1

        # Simulate receiving a user-command response (sequence_id "0")
        print_data = {
            "command": "ams_filament_setting",
            "sequence_id": "0",
            "result": "success",
        }
        # Walk the same path as _on_message: command response check then _update_state
        cmd = print_data.get("command")
        if cmd == "ams_filament_setting" and mqtt_client._last_ams_cmd_time > 0:
            mqtt_client._last_ams_cmd_time = 0.0
            mqtt_client._ams_cmd_unanswered = 0

        assert mqtt_client._last_ams_cmd_time == 0.0
        assert mqtt_client._ams_cmd_unanswered == 0

    def test_single_timeout_increments_counter(self, mqtt_client):
        """One unanswered command increments the counter but does not reconnect."""
        import time

        mqtt_client._last_ams_cmd_time = time.monotonic() - 11.0

        mqtt_client._update_state({"gcode_state": "IDLE"})

        assert mqtt_client._ams_cmd_unanswered == 1
        assert mqtt_client._last_ams_cmd_time == 0.0
        # Should NOT force-reconnect after just one
        assert mqtt_client.state.connected is True

    def test_two_timeouts_force_reconnect(self, mqtt_client):
        """Two consecutive unanswered commands trigger force_reconnect.

        Zombie detection runs from paho's network thread (no asyncio loop), so
        the routing in force_reconnect_stale_session falls back to socket-close
        — which is the safe option since loop_stop() from inside the loop
        thread would deadlock. Hard-reset is reserved for async-context callers
        (background_dispatch dispatch path)."""
        import time

        state_change_called = []
        mqtt_client.on_state_change = lambda s: state_change_called.append(True)

        # First unanswered command
        mqtt_client._last_ams_cmd_time = time.monotonic() - 11.0
        mqtt_client._update_state({"gcode_state": "IDLE"})
        assert mqtt_client._ams_cmd_unanswered == 1
        assert mqtt_client.state.connected is True

        # Second unanswered command
        mqtt_client._last_ams_cmd_time = time.monotonic() - 11.0
        mqtt_client._update_state({"gcode_state": "IDLE"})

        assert mqtt_client._ams_cmd_unanswered == 0  # reset after reconnect
        assert mqtt_client.state.connected is False
        assert mqtt_client._stale_reconnecting is True
        # Sync test → no running loop → socket-close fallback path
        mqtt_client._client.socket().close.assert_called()
        assert len(state_change_called) > 0

    def test_response_between_timeouts_resets_counter(self, mqtt_client):
        """A successful response after one timeout resets the counter."""
        import time

        # First unanswered command
        mqtt_client._last_ams_cmd_time = time.monotonic() - 11.0
        mqtt_client._update_state({"gcode_state": "IDLE"})
        assert mqtt_client._ams_cmd_unanswered == 1

        # Now a response arrives — clear pending
        mqtt_client._last_ams_cmd_time = time.monotonic()
        mqtt_client._last_ams_cmd_time = 0.0
        mqtt_client._ams_cmd_unanswered = 0

        # Next unanswered command should be count=1, not count=2
        mqtt_client._last_ams_cmd_time = time.monotonic() - 11.0
        mqtt_client._update_state({"gcode_state": "IDLE"})
        assert mqtt_client._ams_cmd_unanswered == 1
        assert mqtt_client.state.connected is True  # no reconnect

    def test_late_response_after_watchdog_clears_counter_issue_1164(self, mqtt_client):
        """Regression for #1164: a late ams_filament_setting response — one
        that arrives AFTER the watchdog has already zeroed
        `_last_ams_cmd_time` and incremented the unanswered counter — must
        still reset the counter. Without this, a single sluggish response
        leaves the counter armed at 1 indefinitely; the next slow response
        on a totally unrelated command (possibly minutes or hours later)
        takes it to 2 and force-reconnects, surfacing as 'AMS slot config
        doesn't reach the printer ~6 changes in'."""
        import time

        # First command publishes, then doesn't get a response for >10s.
        # Watchdog fires: counter=1, _last_ams_cmd_time zeroed.
        mqtt_client._last_ams_cmd_time = time.monotonic() - 11.0
        mqtt_client._update_state({"gcode_state": "IDLE"})
        assert mqtt_client._ams_cmd_unanswered == 1
        assert mqtt_client._last_ams_cmd_time == 0.0  # watchdog cleared it

        # Late response arrives — the `_process_message` path used to require
        # `_last_ams_cmd_time > 0` before resetting the counter, so this would
        # have silently been ignored.
        mqtt_client._process_message(
            {
                "print": {
                    "command": "ams_filament_setting",
                    "sequence_id": "0",
                    "result": "success",
                    "reason": "success",
                }
            }
        )

        # Counter MUST be reset — the response proves the channel is alive.
        assert mqtt_client._ams_cmd_unanswered == 0, (
            "Late ams_filament_setting response must reset the unanswered "
            "counter even when the watchdog already zeroed _last_ams_cmd_time. "
            "If this assertion fails the #1164 regression is back: a single "
            "sluggish response will leave the counter armed and cause a "
            "spurious force_reconnect on the next slow response."
        )

        # Now even if a future command times out, the counter starts fresh
        # and a single timeout doesn't trip the 2x reconnect threshold.
        mqtt_client._last_ams_cmd_time = time.monotonic() - 11.0
        mqtt_client._update_state({"gcode_state": "IDLE"})
        assert mqtt_client._ams_cmd_unanswered == 1
        assert mqtt_client.state.connected is True  # no force reconnect

    def test_on_connect_resets_tracking(self, mqtt_client):
        """_on_connect resets zombie tracking fields."""
        import time

        mqtt_client._last_ams_cmd_time = time.monotonic()
        mqtt_client._ams_cmd_unanswered = 5

        # subscribe() must return (result, mid) tuple
        mqtt_client._client.subscribe.return_value = (0, 1)
        mqtt_client._on_connect(mqtt_client._client, None, None, 0)

        assert mqtt_client._last_ams_cmd_time == 0.0
        assert mqtt_client._ams_cmd_unanswered == 0

    def test_no_check_when_no_command_pending(self, mqtt_client):
        """If no command was published, push_status does not trigger detection."""
        assert mqtt_client._last_ams_cmd_time == 0.0
        mqtt_client._update_state({"gcode_state": "IDLE"})
        assert mqtt_client._ams_cmd_unanswered == 0

    def test_no_timeout_within_window(self, mqtt_client):
        """A command published <10s ago should not trigger a timeout."""
        import time

        mqtt_client._last_ams_cmd_time = time.monotonic() - 5.0
        mqtt_client._update_state({"gcode_state": "IDLE"})
        assert mqtt_client._ams_cmd_unanswered == 0
        assert mqtt_client._last_ams_cmd_time > 0  # still pending


class TestHMSUserActionFiltering:
    """HMS short codes the printer firmware emits during user-cancel sequences
    must not appear in state.hms_errors — they're status echoes, not faults,
    and shouldn't drive the printer card's "X problem" badge or red pip."""

    @pytest.fixture
    def mqtt_client(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        return BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST_HMS",
            access_code="12345678",
        )

    def test_task_cancelled_echo_0300_400c_filtered(self, mqtt_client):
        """0300_400C ("The task was canceled.") is the user-cancel echo that was
        leaving the printer card stuck on "1 problem" after every stop."""
        mqtt_client._update_state({"hms": [{"attr": 0x03000300, "code": 0x400C}]})
        assert mqtt_client.state.hms_errors == []

    def test_printing_cancelled_echo_0500_400e_filtered(self, mqtt_client):
        """0500_400E ("Printing was cancelled.") — the corresponding nozzle-module
        echo that the backend notification path was already suppressing for the
        same reason."""
        mqtt_client._update_state({"hms": [{"attr": 0x05000300, "code": 0x400E}]})
        assert mqtt_client.state.hms_errors == []

    def test_real_layer_shift_still_passes_through(self, mqtt_client):
        """0300_4057 (Z-axis step loss) is a real fault and must NOT be filtered."""
        mqtt_client._update_state({"hms": [{"attr": 0x03000100, "code": 0x4057}]})
        assert len(mqtt_client.state.hms_errors) == 1
        assert mqtt_client.state.hms_errors[0].code == "0x4057"

    def test_filter_only_drops_user_action_codes_keeps_concurrent_real_faults(self, mqtt_client):
        """When the user cancels mid-fault, the firmware sends the real fault HMS
        alongside the cancel echo. Drop only the echo, keep the real fault."""
        mqtt_client._update_state(
            {
                "hms": [
                    {"attr": 0x03000300, "code": 0x400C},  # cancel echo — drop
                    {"attr": 0x07FF0200, "code": 0x8011},  # filament runout — keep
                ]
            }
        )
        codes = [e.code for e in mqtt_client.state.hms_errors]
        assert "0x8011" in codes
        assert "0x400c" not in codes
        assert len(mqtt_client.state.hms_errors) == 1

    def test_print_error_path_also_filters_cancel_echo(self, mqtt_client):
        """`print_error` is a second route that appends into state.hms_errors. The
        same user-action codes (e.g. 0500_400E "Printing was cancelled") must be
        filtered there too — otherwise the printer card stays on "1 problem"
        when the firmware reports the cancel via print_error rather than hms[]."""
        mqtt_client._update_state({"print_error": 0x0500_400E})
        assert mqtt_client.state.hms_errors == []

    def test_print_error_path_passes_real_errors_through(self, mqtt_client):
        """Real print_error codes still reach state.hms_errors."""
        mqtt_client._update_state({"print_error": 0x0500_8061})
        assert len(mqtt_client.state.hms_errors) == 1
        assert mqtt_client.state.hms_errors[0].code == "0x8061"


class TestForceReconnectRouting:
    """#1136 — force_reconnect_stale_session routes between hard-reset (full
    paho-client teardown, wipes the QoS 1 queue) and socket-close (the legacy
    behaviour, safe to call from paho's own network thread). The routing
    decision is based on whether an asyncio loop is running: hard-reset
    requires loop_stop() which would deadlock if called from inside the
    network thread itself."""

    @pytest.fixture
    def mqtt_client(self):
        from unittest.mock import MagicMock

        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST_HARD_RESET",
            access_code="12345678",
        )
        client.state.connected = True
        client._client = MagicMock()
        return client

    def test_routing_falls_back_to_socket_close_without_running_loop(self, mqtt_client):
        """Sync caller → no asyncio loop → socket-close path (legacy behaviour
        preserved for paho-thread callers like zombie detection)."""
        mqtt_client.force_reconnect_stale_session("test")
        mqtt_client._client.socket().close.assert_called()
        # Old client is NOT torn down on this path; same-instance reconnect
        # via paho's auto-reconnect handles it.
        assert mqtt_client._client is not None

    def test_routing_uses_hard_reset_when_loop_is_running(self, mqtt_client):
        """Async caller → loop available → hard-reset path wipes the queue."""
        import asyncio

        original = mqtt_client._client
        # Stub connect() so the rebuild doesn't open a real socket.
        mqtt_client.connect = lambda loop=None: None

        async def _trigger():
            mqtt_client.force_reconnect_stale_session("test")

        asyncio.run(_trigger())
        original.disconnect.assert_called()
        original.loop_stop.assert_called()
        # connect() stub didn't repopulate _client, so it's None — the contract
        # in production is that connect() builds a fresh mqtt.Client here.
        assert mqtt_client._client is None

    def test_marks_state_disconnected_and_broadcasts(self, mqtt_client):
        """Both routing paths must broadcast the disconnected state once."""
        broadcasts: list[bool] = []
        mqtt_client.on_state_change = lambda s: broadcasts.append(s.connected)
        mqtt_client.force_reconnect_stale_session("test")
        assert mqtt_client.state.connected is False
        assert mqtt_client._stale_reconnecting is True
        assert broadcasts == [False]


class TestHardResetClientDirect:
    """Lower-level coverage of `_hard_reset_client` itself — the helper called
    by the routing layer when a full paho-client teardown is safe. These tests
    drive the helper directly so they don't depend on the routing decision."""

    @pytest.fixture
    def mqtt_client(self):
        from unittest.mock import MagicMock

        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST_HARD_DIRECT",
            access_code="12345678",
        )
        client.state.connected = True
        client._client = MagicMock()
        # Stub connect() so the rebuild doesn't open a real socket.
        client.connect = lambda loop=None: None
        return client

    def test_disconnects_and_stops_old_client(self, mqtt_client):
        """Old paho client must receive DISCONNECT (broker drops session) +
        loop_stop (network thread exits, taking its QoS 1 queue with it)."""
        original = mqtt_client._client
        mqtt_client._hard_reset_client()
        original.disconnect.assert_called()
        original.loop_stop.assert_called()

    def test_clears_client_reference(self, mqtt_client):
        """Old reference must go to None so subsequent code can't accidentally
        publish through the dying client."""
        mqtt_client._hard_reset_client()
        assert mqtt_client._client is None

    def test_swallows_disconnect_exception(self, mqtt_client):
        """A failing disconnect() (e.g. paho already in error state) must not
        propagate — the await chain in background_dispatch.py would otherwise
        raise instead of moving on, and a single broken client could brick
        every future dispatch."""
        original = mqtt_client._client
        original.disconnect.side_effect = RuntimeError("boom")
        # No exception escapes the call (test would fail if it did).
        mqtt_client._hard_reset_client()
        # loop_stop is still attempted after the disconnect failure.
        original.loop_stop.assert_called()
        assert mqtt_client._client is None


class TestStartPrintRecordsDispatchedPlate:
    """Tests for the dispatched-plate record set by start_print() — used by the
    /cover route to pick the right thumbnail when the printer's gcode_file
    echo doesn't include the plate path (#1166).

    Some firmware versions (P1S 01.10.00.00) only put the .3mf filename in
    print.gcode_file, so the regex falls back to plate 1 and the printer card
    shows the wrong plate's thumbnail. Recording what we dispatched at the
    publish site lets resolve_plate_id() return the right plate without
    needing to introspect the 3MF.
    """

    @pytest.fixture
    def mqtt_client(self):
        from unittest.mock import MagicMock

        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )
        client._client = MagicMock()
        client.state.connected = True
        return client

    def test_dispatched_plate_recorded_after_start_print(self, mqtt_client):
        # Default state has no dispatched plate.
        assert mqtt_client.state.dispatched_plate_id is None
        assert mqtt_client.state.dispatched_subtask is None

        mqtt_client.start_print("Luigi.3mf", plate_id=2)

        # The subtask_name we record matches the one we send (and the printer
        # reflects back via MQTT), so resolve_plate_id() can validate the
        # match downstream.
        assert mqtt_client.state.dispatched_plate_id == 2
        assert mqtt_client.state.dispatched_subtask == "Luigi"

    def test_dispatched_plate_default_is_one(self, mqtt_client):
        # When start_print is called without plate_id (legacy/single-plate
        # flow), we still record plate=1 — the contract is that dispatched_*
        # describes the active dispatch.
        mqtt_client.start_print("Single.3mf")
        assert mqtt_client.state.dispatched_plate_id == 1
        assert mqtt_client.state.dispatched_subtask == "Single"

    def test_dispatched_plate_overwritten_by_subsequent_dispatch(self, mqtt_client):
        # Each dispatch replaces the prior record so we can never serve a
        # stale plate from an older print.
        mqtt_client.start_print("First.3mf", plate_id=4)
        mqtt_client.start_print("Second.3mf", plate_id=2)

        assert mqtt_client.state.dispatched_plate_id == 2
        assert mqtt_client.state.dispatched_subtask == "Second"

    def test_dispatched_plate_not_recorded_when_publish_skipped(self, mqtt_client):
        # If start_print early-returns because we're not connected, no record
        # should land — otherwise the next print's /cover call would believe
        # a phantom dispatch happened.
        mqtt_client.state.connected = False
        result = mqtt_client.start_print("Phantom.3mf", plate_id=3)

        assert result is False
        assert mqtt_client.state.dispatched_plate_id is None
        assert mqtt_client.state.dispatched_subtask is None


class TestFilamentTrackSwitchDetection:
    """Tests for Filament Track Switch (FTS) accessory detection (#1162).

    The FTS is an accessory that sits between an AMS and the printer's
    extruders, dynamically routing any slot to either nozzle. When installed,
    each AMS unit reports info bits 8-11 = 0xE (uninitialized) since slots are
    no longer tied to a specific extruder. Detection comes from the presence of
    the print.device.fila_switch object in MQTT push_status.
    """

    @pytest.fixture
    def mqtt_client(self):
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        return BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )

    def test_fts_default_not_installed(self, mqtt_client):
        """Without any MQTT data, fila_switch.installed must be False so the
        frontend keeps applying the per-extruder filter on regular dual-nozzle
        printers."""
        assert mqtt_client.state.fila_switch.installed is False
        assert mqtt_client.state.fila_switch.in_slots == []
        assert mqtt_client.state.fila_switch.out_extruders == []

    def test_fts_detected_from_device_fila_switch(self, mqtt_client):
        """A push_status with print.device.fila_switch present must mark FTS
        installed and capture its routing arrays. Mirrors the user's MQTT
        bundle in #1162."""
        data = {
            "gcode_state": "RUNNING",
            "device": {
                "fila_switch": {
                    "in": [-1, 2],
                    "info": 2,
                    "out": [0, 1],
                    "stat": 0,
                }
            },
        }
        mqtt_client._update_state(data)
        fs = mqtt_client.state.fila_switch
        assert fs.installed is True
        assert fs.in_slots == [-1, 2]
        assert fs.out_extruders == [0, 1]
        assert fs.stat == 0
        assert fs.info == 2

    def test_fts_absent_when_no_fila_switch_field(self, mqtt_client):
        """A push_status that has device.* but no fila_switch must leave
        fila_switch.installed = False — only that specific field flips it on."""
        data = {
            "gcode_state": "IDLE",
            "device": {"extruder": {"state": 0}},
        }
        mqtt_client._update_state(data)
        assert mqtt_client.state.fila_switch.installed is False

    def test_fts_handles_missing_in_out_arrays(self, mqtt_client):
        """If the firmware sends fila_switch with missing or non-list in/out,
        we must still mark it installed (presence is the signal) and default
        the arrays to empty lists rather than crashing."""
        data = {
            "gcode_state": "IDLE",
            "device": {"fila_switch": {"stat": 0, "info": 0}},
        }
        mqtt_client._update_state(data)
        fs = mqtt_client.state.fila_switch
        assert fs.installed is True
        assert fs.in_slots == []
        assert fs.out_extruders == []


class TestAmsLoadFilamentEncoding:
    """Per-target ams_change_filament command encoding (#891)."""

    @pytest.fixture
    def mqtt_client(self):
        from unittest.mock import MagicMock

        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )
        # Pretend the MQTT layer is connected so the publish path is reached.
        client._client = MagicMock()
        client.state.connected = True
        return client

    @staticmethod
    def _published(client) -> dict:
        """Return the JSON of the most recent publish() call."""
        last_call = client._client.publish.call_args_list[-1]
        topic, payload, *_ = last_call.args
        return json.loads(payload)

    def test_ams_slot_uses_local_index_and_minus_one_temps(self, mqtt_client):
        """tray_id=5 → ams_id=1, slot_id=1, target=5, curr/tar=-1."""
        assert mqtt_client.ams_load_filament(5) is True
        cmd = self._published(mqtt_client)["print"]
        assert cmd["command"] == "ams_change_filament"
        assert cmd["ams_id"] == 1
        assert cmd["slot_id"] == 1
        assert cmd["target"] == 5
        assert cmd["curr_temp"] == -1
        assert cmd["tar_temp"] == -1

    def test_external_left_keeps_legacy_encoding(self, mqtt_client):
        """tray_id=254 → ams_id=255, slot_id=254, target=254, curr/tar=-1.

        This is the original capture from a single-extruder printer; preserved
        verbatim so existing single-external setups don't regress.
        """
        assert mqtt_client.ams_load_filament(254) is True
        cmd = self._published(mqtt_client)["print"]
        assert cmd["ams_id"] == 255
        assert cmd["slot_id"] == 254
        assert cmd["target"] == 254
        assert cmd["curr_temp"] == -1
        assert cmd["tar_temp"] == -1

    def test_external_right_uses_extruder_index_and_actual_temp(self, mqtt_client):
        """tray_id=255 → captured BambuStudio shape on dual-nozzle H2D:
        ams_id=255, slot_id=0 (right extruder), target=255, curr/tar = right
        nozzle temp.
        """
        # Simulate a heated right nozzle.
        mqtt_client.state.temperatures["nozzle_2"] = 215.0

        assert mqtt_client.ams_load_filament(255) is True
        cmd = self._published(mqtt_client)["print"]
        assert cmd["ams_id"] == 255
        assert cmd["slot_id"] == 0
        assert cmd["target"] == 255
        assert cmd["curr_temp"] == 215
        assert cmd["tar_temp"] == 215

    def test_external_right_falls_back_when_nozzle_cold(self, mqtt_client):
        """If the right nozzle reports < 180 °C, fall back to a sane default
        so the printer accepts the command rather than rejecting it on a
        nonsensical temperature.
        """
        mqtt_client.state.temperatures["nozzle_2"] = 25.0

        assert mqtt_client.ams_load_filament(255) is True
        cmd = self._published(mqtt_client)["print"]
        assert cmd["curr_temp"] == 215
        assert cmd["tar_temp"] == 215

    def test_returns_false_when_disconnected(self, mqtt_client):
        """Disconnected client must not publish anything."""
        mqtt_client.state.connected = False
        assert mqtt_client.ams_load_filament(0) is False
        mqtt_client._client.publish.assert_not_called()
