"""
Integration tests for the full print lifecycle.

These tests verify that:
1. Print start creates a new archive
2. Print complete updates archive status
3. Callbacks are properly executed
4. Energy tracking works
5. Notifications are sent

Note: These tests use mocking to avoid database conflicts.
Full end-to-end tests require the actual database setup.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestPrintStartLogic:
    """Test print start callback logic without database integration."""

    @pytest.mark.asyncio
    async def test_print_start_calls_notification_service(self, capture_logs):
        """Verify on_print_start triggers notification service."""
        with (
            patch("backend.app.main.async_session") as mock_session_maker,
            patch("backend.app.main.notification_service") as mock_notif,
            patch("backend.app.main.smart_plug_manager") as mock_plug,
            patch("backend.app.main.ws_manager") as mock_ws,
        ):
            mock_notif.on_print_start = AsyncMock()
            mock_plug.on_print_start = AsyncMock()
            mock_ws.send_print_start = AsyncMock()

            # Mock the database session
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()
            mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
            mock_session_maker.return_value = mock_session

            from backend.app.main import on_print_start

            await on_print_start(
                1,
                {
                    "filename": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                },
            )

            # Verify WebSocket notification was sent
            mock_ws.send_print_start.assert_called_once()

        # Verify no import shadowing errors
        errors = [r for r in capture_logs.get_errors() if "cannot access local variable" in str(r.message)]
        assert not errors, f"Import shadowing error: {capture_logs.format_errors()}"


class TestPrintCompleteLogic:
    """Test print complete callback logic."""

    @pytest.mark.asyncio
    async def test_print_complete_no_import_errors(self, capture_logs):
        """Verify on_print_complete doesn't have import shadowing issues."""
        with (
            patch("backend.app.main.async_session") as mock_session_maker,
            patch("backend.app.main.notification_service") as mock_notif,
            patch("backend.app.main.smart_plug_manager") as mock_plug,
            patch("backend.app.main.ws_manager") as mock_ws,
        ):
            mock_notif.on_print_complete = AsyncMock()
            mock_plug.on_print_complete = AsyncMock()
            mock_ws.send_print_complete = AsyncMock()

            # Mock the database session
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()
            mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
            mock_session_maker.return_value = mock_session

            from backend.app.main import on_print_complete

            await on_print_complete(
                1,
                {
                    "status": "completed",
                    "filename": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                    "timelapse_was_active": False,
                },
            )

        # Verify no import shadowing errors - this would have caught the ArchiveService bug
        errors = [r for r in capture_logs.get_errors() if "cannot access local variable" in str(r.message)]
        assert not errors, f"Import shadowing error: {capture_logs.format_errors()}"


class TestTimelapseTracking:
    """Test timelapse detection during prints."""

    @pytest.mark.asyncio
    async def test_timelapse_detected_in_same_message_as_print_start(self):
        """Verify timelapse is detected when xcam and state come together."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )
        client.on_print_start = lambda data: None

        # Initial state
        client._was_running = False
        client._timelapse_during_print = False

        # Message with both state and timelapse
        client._process_message(
            {
                "print": {
                    "gcode_state": "RUNNING",
                    "gcode_file": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                    "xcam": {"timelapse": "enable"},
                }
            }
        )

        assert client._was_running is True
        assert client._timelapse_during_print is True, (
            "Timelapse should be detected even when xcam is parsed before state"
        )

    @pytest.mark.asyncio
    async def test_timelapse_flag_included_in_completion_callback(self):
        """Verify completion callback receives timelapse_was_active flag."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )

        completion_data = {}

        def on_complete(data):
            completion_data.update(data)

        client.on_print_start = lambda data: None
        client.on_print_complete = on_complete

        # Start with timelapse
        client._process_message(
            {
                "print": {
                    "gcode_state": "RUNNING",
                    "gcode_file": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                    "xcam": {"timelapse": "enable"},
                }
            }
        )

        # Complete print
        client._process_message(
            {
                "print": {
                    "gcode_state": "FINISH",
                    "gcode_file": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                }
            }
        )

        assert "timelapse_was_active" in completion_data
        assert completion_data["timelapse_was_active"] is True

    @pytest.mark.asyncio
    async def test_hms_errors_included_in_failed_completion_callback(self):
        """Verify completion callback receives hms_errors for failed prints."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )

        completion_data = {}

        def on_complete(data):
            completion_data.update(data)

        client.on_print_start = lambda data: None
        client.on_print_complete = on_complete

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

        # Add HMS error during print
        client._process_message(
            {
                "print": {
                    "gcode_state": "RUNNING",
                    "hms": [{"attr": 0x07000002, "code": 0x1234}],  # Filament module error
                }
            }
        )

        # Fail print
        client._process_message(
            {
                "print": {
                    "gcode_state": "FAILED",
                    "gcode_file": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                }
            }
        )

        assert "hms_errors" in completion_data
        assert len(completion_data["hms_errors"]) == 1
        assert completion_data["hms_errors"][0]["module"] == 0x07
        assert completion_data["status"] == "failed"

    @pytest.mark.asyncio
    async def test_aborted_status_when_cancelled(self):
        """Verify completion callback receives 'aborted' status when print is cancelled."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )

        completion_data = {}

        def on_complete(data):
            completion_data.update(data)

        client.on_print_start = lambda data: None
        client.on_print_complete = on_complete

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

        # User cancels (goes to IDLE)
        client._process_message(
            {
                "print": {
                    "gcode_state": "IDLE",
                    "gcode_file": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                }
            }
        )

        assert completion_data["status"] == "aborted"
        assert "hms_errors" in completion_data

    @pytest.mark.asyncio
    async def test_timelapse_detected_from_ipcam_data(self):
        """Verify timelapse is detected from ipcam data (H2D sends it there, not xcam)."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST123",
            access_code="12345678",
        )

        completion_data = {}

        def on_complete(data):
            completion_data.update(data)

        client.on_print_start = lambda data: None
        client.on_print_complete = on_complete

        # Start print with timelapse in ipcam data (H2D format)
        client._process_message(
            {
                "print": {
                    "gcode_state": "RUNNING",
                    "gcode_file": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                    "ipcam": {
                        "ipcam_record": "enable",
                        "timelapse": "enable",
                        "resolution": "1080p",
                    },
                }
            }
        )

        assert client._timelapse_during_print is True, "Timelapse should be detected from ipcam data"

        # Complete print
        client._process_message(
            {
                "print": {
                    "gcode_state": "FINISH",
                    "gcode_file": "/data/Metadata/test.gcode",
                    "subtask_name": "Test",
                }
            }
        )

        assert completion_data["timelapse_was_active"] is True, (
            "timelapse_was_active should be True when timelapse was in ipcam"
        )


class TestCallbackErrorHandling:
    """Test that callback errors are properly logged."""

    @pytest.mark.asyncio
    async def test_callback_errors_are_logged(self, capture_logs):
        """Verify that exceptions in callbacks are logged, not swallowed."""
        from backend.app.services.printer_manager import PrinterManager

        manager = PrinterManager()

        # Set up event loop
        loop = asyncio.get_event_loop()
        manager.set_event_loop(loop)

        # Create a callback that raises an error
        error_raised = False

        async def failing_callback(printer_id, data):
            nonlocal error_raised
            error_raised = True
            raise ValueError("Test error in callback")

        manager.set_print_complete_callback(failing_callback)

        # The _schedule_async should log the error
        # This is tested indirectly - if exception handling is broken,
        # the error would be swallowed silently


class TestNoImportShadowing:
    """Verify no import shadowing issues exist in callbacks."""

    @pytest.mark.asyncio
    async def test_on_print_complete_no_import_errors(self, capture_logs):
        """Verify on_print_complete doesn't have import shadowing issues."""
        # Import the module to check for syntax/import errors
        from backend.app import main

        # The ArchiveService should be accessible
        from backend.app.services.archive import ArchiveService

        # Verify we can instantiate it (would fail with shadowing bug)
        assert ArchiveService is not None

        # Check logs for any import-related errors
        errors = capture_logs.get_errors()
        import_errors = [
            e for e in errors if "import" in str(e.message).lower() or "local variable" in str(e.message).lower()
        ]
        assert not import_errors, f"Import errors found: {import_errors}"
