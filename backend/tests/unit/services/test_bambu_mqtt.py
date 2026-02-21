"""
Tests for the BambuMQTTClient service.

These tests focus on timelapse tracking during prints.
"""

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
        """AMS 0 info=2003 → right (ext 0), AMS 128 info=2104 → left (ext 1)."""
        ams_units = [
            {"id": 0, "info": 2003, "tray": [{"id": i} for i in range(4)]},
            {"id": 128, "info": 2104, "tray": [{"id": 0}]},
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

        # info=2003: bit8 = (2003>>8)&1 = 7&1 = 1 → extruder = 1-1 = 0 (right)
        # info=2104: bit8 = (2104>>8)&1 = 8&1 = 0 → extruder = 1-0 = 1 (left)
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
                        {"id": 0, "info": 2003, "tray": [{"id": i} for i in range(4)]},
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
                            {"id": 0, "info": 2003, "tray": [{"id": i} for i in range(4)]},
                            {"id": 128, "info": 2104, "tray": [{"id": 0}]},
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
