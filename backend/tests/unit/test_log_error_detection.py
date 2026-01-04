"""
Tests that verify no errors are logged during normal operations.

These tests use the capture_logs fixture to detect runtime errors
that might not cause test failures but indicate problems.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestMQTTMessageProcessingNoErrors:
    """Verify MQTT message processing doesn't log errors."""

    def test_process_print_status_message(self, capture_logs):
        """Test processing a typical print status message."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )

        # Process a realistic status message
        message = {
            "print": {
                "gcode_state": "RUNNING",
                "gcode_file": "/data/Metadata/test.gcode",
                "subtask_name": "Test Print",
                "mc_percent": 50,
                "mc_remaining_time": 1800,
                "layer_num": 100,
                "total_layer_num": 200,
                "nozzle_temper": 220.0,
                "bed_temper": 60.0,
            }
        }

        client._process_message(message)

        assert not capture_logs.has_errors(), f"Errors during message processing: {capture_logs.format_errors()}"

    def test_process_xcam_data(self, capture_logs):
        """Test processing xcam (camera/AI) data."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )

        message = {
            "print": {
                "gcode_state": "RUNNING",
                "xcam": {
                    "timelapse": "enable",
                    "printing_monitor": True,
                    "spaghetti_detector": True,
                    "first_layer_inspector": False,
                },
            }
        }

        client._process_message(message)

        assert not capture_logs.has_errors(), f"Errors during xcam processing: {capture_logs.format_errors()}"

    def test_process_ams_data(self, capture_logs):
        """Test processing AMS (Automatic Material System) data."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )

        message = {
            "print": {
                "ams": {
                    "ams": [
                        {
                            "id": "0",
                            "humidity": "3",
                            "temp": "25.0",
                            "tray": [
                                {
                                    "id": "0",
                                    "tray_type": "PLA",
                                    "tray_color": "FF0000",
                                    "remain": 80,
                                }
                            ],
                        }
                    ]
                }
            }
        }

        client._process_message(message)

        assert not capture_logs.has_errors(), f"Errors during AMS processing: {capture_logs.format_errors()}"

    def test_process_hms_errors(self, capture_logs):
        """Test processing HMS (Health Management System) errors."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )

        message = {
            "print": {
                "hms": [
                    {
                        "attr": 0,
                        "code": 117506052,
                    }
                ]
            }
        }

        client._process_message(message)

        assert not capture_logs.has_errors(), f"Errors during HMS processing: {capture_logs.format_errors()}"


class TestPrintLifecycleNoErrors:
    """Verify print lifecycle doesn't log errors."""

    def test_print_start_to_complete(self, capture_logs):
        """Test full print lifecycle from start to completion."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )
        client.on_print_start = lambda data: None
        client.on_print_complete = lambda data: None

        # Start print
        client._process_message(
            {
                "print": {
                    "gcode_state": "RUNNING",
                    "gcode_file": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                    "mc_percent": 0,
                }
            }
        )

        # Progress updates
        for percent in [25, 50, 75]:
            client._process_message(
                {
                    "print": {
                        "gcode_state": "RUNNING",
                        "gcode_file": "/data/Metadata/test.gcode",
                        "mc_percent": percent,
                    }
                }
            )

        # Complete
        client._process_message(
            {
                "print": {
                    "gcode_state": "FINISH",
                    "gcode_file": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                }
            }
        )

        assert not capture_logs.has_errors(), f"Errors during print lifecycle: {capture_logs.format_errors()}"

    def test_print_failure_handling(self, capture_logs):
        """Test print failure is handled without errors."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )
        client.on_print_start = lambda data: None
        client.on_print_complete = lambda data: None

        # Start print
        client._process_message(
            {
                "print": {
                    "gcode_state": "RUNNING",
                    "gcode_file": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                }
            }
        )

        # Fail
        client._process_message(
            {
                "print": {
                    "gcode_state": "FAILED",
                    "gcode_file": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                    "print_error": 117506052,
                }
            }
        )

        assert not capture_logs.has_errors(), f"Errors during print failure: {capture_logs.format_errors()}"


class TestServiceImports:
    """Verify service imports don't have issues."""

    def test_archive_service_import(self, capture_logs):
        """Verify ArchiveService can be imported without errors."""
        from backend.app.services.archive import ArchiveService

        assert ArchiveService is not None
        assert not capture_logs.has_errors()

    def test_notification_service_import(self, capture_logs):
        """Verify NotificationService can be imported without errors."""
        from backend.app.services.notification_service import notification_service

        assert notification_service is not None
        assert not capture_logs.has_errors()

    def test_printer_manager_import(self, capture_logs):
        """Verify PrinterManager can be imported without errors."""
        from backend.app.services.printer_manager import printer_manager

        assert printer_manager is not None
        assert not capture_logs.has_errors()

    def test_main_module_import(self, capture_logs):
        """Verify main module imports cleanly."""
        # This will fail if there are import shadowing issues
        from backend.app import main

        assert main is not None

        # Verify key functions exist
        assert hasattr(main, "on_print_start")
        assert hasattr(main, "on_print_complete")
        assert not capture_logs.has_errors()


class TestEdgeCases:
    """Test edge cases that might cause errors."""

    def test_empty_message(self, capture_logs):
        """Test handling of empty message."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )

        client._process_message({})

        assert not capture_logs.has_errors(), f"Errors with empty message: {capture_logs.format_errors()}"

    def test_message_with_unknown_fields(self, capture_logs):
        """Test handling of message with unknown fields."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )

        client._process_message(
            {
                "print": {
                    "gcode_state": "RUNNING",
                    "unknown_field_1": "value1",
                    "unknown_field_2": 12345,
                    "unknown_nested": {"a": 1, "b": 2},
                }
            }
        )

        assert not capture_logs.has_errors(), f"Errors with unknown fields: {capture_logs.format_errors()}"

    def test_message_with_null_values(self, capture_logs):
        """Test handling of message with null values for optional fields."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )

        # Only test null values for fields that should handle them gracefully
        # mc_percent is expected to be a number when present
        client._process_message(
            {
                "print": {
                    "gcode_state": "IDLE",
                    "gcode_file": None,
                    "subtask_name": None,
                    "bed_temper": 0.0,  # Use 0 instead of None
                }
            }
        )

        assert not capture_logs.has_errors(), f"Errors with null values: {capture_logs.format_errors()}"
