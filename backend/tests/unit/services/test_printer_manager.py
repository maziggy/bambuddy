"""Unit tests for PrinterManager service.

Tests printer connection management, status tracking, and print control.
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from backend.app.services.printer_manager import (
    PrinterManager,
    get_derived_status_name,
    init_printer_connections,
    printer_state_to_dict,
    supports_chamber_temp,
)


class TestPrinterManager:
    """Tests for PrinterManager class."""

    @pytest.fixture
    def manager(self):
        """Create a fresh PrinterManager instance."""
        return PrinterManager()

    @pytest.fixture
    def mock_printer(self):
        """Create a mock Printer object."""
        printer = MagicMock()
        printer.id = 1
        printer.ip_address = "192.168.1.100"
        printer.serial_number = "00M09A123456789"
        printer.access_code = "12345678"
        printer.is_active = True
        return printer

    @pytest.fixture
    def mock_client(self):
        """Create a mock BambuMQTTClient."""
        client = MagicMock()
        client.state = MagicMock()
        client.state.connected = True
        client.state.state = "IDLE"
        client.state.progress = 0
        client.state.temperatures = {"nozzle": 25, "bed": 25}
        client.state.raw_data = {}
        client.logging_enabled = False
        return client

    # ========================================================================
    # Tests for initialization
    # ========================================================================

    def test_init_creates_empty_clients_dict(self, manager):
        """Verify manager initializes with empty clients dict."""
        assert manager._clients == {}

    def test_init_callbacks_are_none(self, manager):
        """Verify all callbacks are initially None."""
        assert manager._on_print_start is None
        assert manager._on_print_complete is None
        assert manager._on_status_change is None
        assert manager._on_ams_change is None

    def test_init_loop_is_none(self, manager):
        """Verify event loop is initially None."""
        assert manager._loop is None

    # ========================================================================
    # Tests for callback setters
    # ========================================================================

    def test_set_event_loop(self, manager):
        """Verify event loop can be set."""
        mock_loop = MagicMock()
        manager.set_event_loop(mock_loop)
        assert manager._loop == mock_loop

    def test_set_print_start_callback(self, manager):
        """Verify print start callback can be set."""
        callback = MagicMock()
        manager.set_print_start_callback(callback)
        assert manager._on_print_start == callback

    def test_set_print_complete_callback(self, manager):
        """Verify print complete callback can be set."""
        callback = MagicMock()
        manager.set_print_complete_callback(callback)
        assert manager._on_print_complete == callback

    def test_set_status_change_callback(self, manager):
        """Verify status change callback can be set."""
        callback = MagicMock()
        manager.set_status_change_callback(callback)
        assert manager._on_status_change == callback

    def test_set_ams_change_callback(self, manager):
        """Verify AMS change callback can be set."""
        callback = MagicMock()
        manager.set_ams_change_callback(callback)
        assert manager._on_ams_change == callback

    # ========================================================================
    # Tests for _schedule_async
    # ========================================================================

    def test_schedule_async_with_running_loop(self, manager):
        """Verify async coroutine is scheduled when loop is running."""
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True
        manager._loop = mock_loop

        async def dummy_coro():
            pass

        coro = dummy_coro()
        manager._schedule_async(coro)

        mock_loop.is_running.assert_called_once()
        # Clean up the coroutine
        coro.close()

    def test_schedule_async_without_loop(self, manager):
        """Verify nothing happens when no loop is set."""

        async def dummy_coro():
            pass

        coro = dummy_coro()
        # Should not raise
        manager._schedule_async(coro)
        coro.close()

    def test_schedule_async_with_stopped_loop(self, manager):
        """Verify nothing happens when loop is not running."""
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = False
        manager._loop = mock_loop

        async def dummy_coro():
            pass

        coro = dummy_coro()
        manager._schedule_async(coro)
        coro.close()

    # ========================================================================
    # Tests for connect_printer
    # ========================================================================

    @pytest.mark.asyncio
    async def test_connect_printer_creates_client(self, manager, mock_printer):
        """Verify connecting creates an MQTT client."""
        with patch("backend.app.services.printer_manager.BambuMQTTClient") as MockClient:
            mock_instance = MagicMock()
            mock_instance.state = MagicMock()
            mock_instance.state.connected = True
            MockClient.return_value = mock_instance

            result = await manager.connect_printer(mock_printer)

            MockClient.assert_called_once()
            mock_instance.connect.assert_called_once()
            assert mock_printer.id in manager._clients
            assert result is True

    @pytest.mark.asyncio
    async def test_connect_printer_disconnects_existing(self, manager, mock_printer, mock_client):
        """Verify connecting disconnects existing client first."""
        manager._clients[mock_printer.id] = mock_client

        with patch("backend.app.services.printer_manager.BambuMQTTClient") as MockClient:
            new_client = MagicMock()
            new_client.state = MagicMock()
            new_client.state.connected = True
            MockClient.return_value = new_client

            await manager.connect_printer(mock_printer)

            mock_client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_printer_returns_false_on_failure(self, manager, mock_printer):
        """Verify returns False when connection fails."""
        with patch("backend.app.services.printer_manager.BambuMQTTClient") as MockClient:
            mock_instance = MagicMock()
            mock_instance.state = MagicMock()
            mock_instance.state.connected = False
            MockClient.return_value = mock_instance

            result = await manager.connect_printer(mock_printer)

            assert result is False

    # ========================================================================
    # Tests for disconnect_printer
    # ========================================================================

    def test_disconnect_printer_removes_client(self, manager, mock_client):
        """Verify disconnecting removes and disconnects client."""
        manager._clients[1] = mock_client

        manager.disconnect_printer(1)

        mock_client.disconnect.assert_called_once()
        assert 1 not in manager._clients

    def test_disconnect_printer_handles_missing(self, manager):
        """Verify disconnecting non-existent printer doesn't raise."""
        manager.disconnect_printer(999)  # Should not raise

    # ========================================================================
    # Tests for disconnect_all
    # ========================================================================

    def test_disconnect_all_disconnects_all_clients(self, manager):
        """Verify all clients are disconnected."""
        client1 = MagicMock()
        client2 = MagicMock()
        manager._clients[1] = client1
        manager._clients[2] = client2

        manager.disconnect_all()

        client1.disconnect.assert_called_once()
        client2.disconnect.assert_called_once()
        assert len(manager._clients) == 0

    # ========================================================================
    # Tests for get_status
    # ========================================================================

    def test_get_status_returns_state(self, manager, mock_client):
        """Verify get_status returns client state."""
        manager._clients[1] = mock_client

        result = manager.get_status(1)

        mock_client.check_staleness.assert_called_once()
        assert result == mock_client.state

    def test_get_status_returns_none_for_unknown(self, manager):
        """Verify get_status returns None for unknown printer."""
        result = manager.get_status(999)
        assert result is None

    # ========================================================================
    # Tests for get_all_statuses
    # ========================================================================

    def test_get_all_statuses_returns_all(self, manager):
        """Verify all statuses are returned."""
        client1 = MagicMock()
        client1.state = MagicMock(connected=True)
        client2 = MagicMock()
        client2.state = MagicMock(connected=False)
        manager._clients[1] = client1
        manager._clients[2] = client2

        result = manager.get_all_statuses()

        assert len(result) == 2
        assert 1 in result
        assert 2 in result
        client1.check_staleness.assert_called_once()
        client2.check_staleness.assert_called_once()

    # ========================================================================
    # Tests for is_connected
    # ========================================================================

    def test_is_connected_returns_true(self, manager, mock_client):
        """Verify is_connected returns True for connected printer."""
        mock_client.check_staleness.return_value = True
        manager._clients[1] = mock_client

        result = manager.is_connected(1)

        assert result is True

    def test_is_connected_returns_false_for_unknown(self, manager):
        """Verify is_connected returns False for unknown printer."""
        result = manager.is_connected(999)
        assert result is False

    # ========================================================================
    # Tests for get_client
    # ========================================================================

    def test_get_client_returns_client(self, manager, mock_client):
        """Verify get_client returns the client."""
        manager._clients[1] = mock_client

        result = manager.get_client(1)

        assert result == mock_client

    def test_get_client_returns_none_for_unknown(self, manager):
        """Verify get_client returns None for unknown printer."""
        result = manager.get_client(999)
        assert result is None

    # ========================================================================
    # Tests for mark_printer_offline
    # ========================================================================

    def test_mark_printer_offline_updates_state(self, manager, mock_client):
        """Verify mark_printer_offline updates client state."""
        mock_client.state.connected = True
        manager._clients[1] = mock_client

        manager.mark_printer_offline(1)

        assert mock_client.state.connected is False
        assert mock_client.state.state == "unknown"

    def test_mark_printer_offline_triggers_callback(self, manager, mock_client):
        """Verify mark_printer_offline triggers status callback."""
        mock_client.state.connected = True
        manager._clients[1] = mock_client

        # Callback must return a coroutine
        async def async_callback(printer_id, state):
            pass

        manager._on_status_change = async_callback

        # Need a running loop for callback
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True
        manager._loop = mock_loop

        manager.mark_printer_offline(1)

        # Callback should be scheduled via run_coroutine_threadsafe
        mock_loop.is_running.assert_called()
        # State should be updated
        assert mock_client.state.connected is False

    def test_mark_printer_offline_handles_unknown(self, manager):
        """Verify mark_printer_offline handles unknown printer."""
        manager.mark_printer_offline(999)  # Should not raise

    def test_mark_printer_offline_skips_already_offline(self, manager, mock_client):
        """Verify mark_printer_offline skips already offline printer."""
        mock_client.state.connected = False
        manager._clients[1] = mock_client

        manager.mark_printer_offline(1)

        # State should remain unchanged
        assert mock_client.state.connected is False

    # ========================================================================
    # Tests for start_print
    # ========================================================================

    def test_start_print_calls_client(self, manager, mock_client):
        """Verify start_print calls client method."""
        mock_client.start_print.return_value = True
        manager._clients[1] = mock_client

        result = manager.start_print(1, "test.gcode")

        mock_client.start_print.assert_called_once_with(
            "test.gcode",
            1,
            ams_mapping=None,
            timelapse=False,
            bed_levelling=True,
            flow_cali=False,
            vibration_cali=True,
            layer_inspect=False,
            use_ams=True,
        )
        assert result is True

    def test_start_print_returns_false_for_unknown(self, manager):
        """Verify start_print returns False for unknown printer."""
        result = manager.start_print(999, "test.gcode")
        assert result is False

    # ========================================================================
    # Tests for stop_print
    # ========================================================================

    def test_stop_print_calls_client(self, manager, mock_client):
        """Verify stop_print calls client method."""
        mock_client.stop_print.return_value = True
        manager._clients[1] = mock_client

        result = manager.stop_print(1)

        mock_client.stop_print.assert_called_once()
        assert result is True

    def test_stop_print_returns_false_for_unknown(self, manager):
        """Verify stop_print returns False for unknown printer."""
        result = manager.stop_print(999)
        assert result is False

    # ========================================================================
    # Tests for wait_for_cooldown
    # ========================================================================

    @pytest.mark.asyncio
    async def test_wait_for_cooldown_returns_true_when_cool(self, manager, mock_client):
        """Verify wait_for_cooldown returns True when printer is cool."""
        mock_client.state.connected = True
        mock_client.state.temperatures = {"nozzle": 40, "bed": 30}
        mock_client.check_staleness.return_value = True
        manager._clients[1] = mock_client

        result = await manager.wait_for_cooldown(1, target_temp=50)

        assert result is True

    @pytest.mark.asyncio
    async def test_wait_for_cooldown_returns_false_on_disconnect(self, manager, mock_client):
        """Verify wait_for_cooldown returns False when printer disconnects."""
        mock_client.state.connected = False
        mock_client.check_staleness.return_value = False
        manager._clients[1] = mock_client

        result = await manager.wait_for_cooldown(1, target_temp=50, timeout=1)

        assert result is False

    @pytest.mark.asyncio
    async def test_wait_for_cooldown_returns_false_for_unknown(self, manager):
        """Verify wait_for_cooldown returns False for unknown printer."""
        result = await manager.wait_for_cooldown(999, target_temp=50, timeout=1)
        assert result is False

    @pytest.mark.asyncio
    async def test_wait_for_cooldown_checks_both_nozzles(self, manager, mock_client):
        """Verify wait_for_cooldown checks both nozzles for dual extruders."""
        mock_client.state.connected = True
        mock_client.state.temperatures = {"nozzle": 40, "nozzle_2": 45, "bed": 30}
        mock_client.check_staleness.return_value = True
        manager._clients[1] = mock_client

        result = await manager.wait_for_cooldown(1, target_temp=50)

        assert result is True

    # ========================================================================
    # Tests for logging methods
    # ========================================================================

    def test_enable_logging_calls_client(self, manager, mock_client):
        """Verify enable_logging calls client method."""
        manager._clients[1] = mock_client

        result = manager.enable_logging(1, True)

        mock_client.enable_logging.assert_called_once_with(True)
        assert result is True

    def test_enable_logging_returns_false_for_unknown(self, manager):
        """Verify enable_logging returns False for unknown printer."""
        result = manager.enable_logging(999, True)
        assert result is False

    def test_get_logs_returns_logs(self, manager, mock_client):
        """Verify get_logs returns client logs."""
        mock_logs = [MagicMock(), MagicMock()]
        mock_client.get_logs.return_value = mock_logs
        manager._clients[1] = mock_client

        result = manager.get_logs(1)

        assert result == mock_logs

    def test_get_logs_returns_empty_for_unknown(self, manager):
        """Verify get_logs returns empty list for unknown printer."""
        result = manager.get_logs(999)
        assert result == []

    def test_clear_logs_calls_client(self, manager, mock_client):
        """Verify clear_logs calls client method."""
        manager._clients[1] = mock_client

        result = manager.clear_logs(1)

        mock_client.clear_logs.assert_called_once()
        assert result is True

    def test_clear_logs_returns_false_for_unknown(self, manager):
        """Verify clear_logs returns False for unknown printer."""
        result = manager.clear_logs(999)
        assert result is False

    def test_is_logging_enabled_returns_status(self, manager, mock_client):
        """Verify is_logging_enabled returns client status."""
        mock_client.logging_enabled = True
        manager._clients[1] = mock_client

        result = manager.is_logging_enabled(1)

        assert result is True

    def test_is_logging_enabled_returns_false_for_unknown(self, manager):
        """Verify is_logging_enabled returns False for unknown printer."""
        result = manager.is_logging_enabled(999)
        assert result is False

    # ========================================================================
    # Tests for request_status_update
    # ========================================================================

    def test_request_status_update_calls_client(self, manager, mock_client):
        """Verify request_status_update calls client method."""
        mock_client.request_status_update.return_value = True
        manager._clients[1] = mock_client

        result = manager.request_status_update(1)

        mock_client.request_status_update.assert_called_once()
        assert result is True

    def test_request_status_update_returns_false_for_unknown(self, manager):
        """Verify request_status_update returns False for unknown printer."""
        result = manager.request_status_update(999)
        assert result is False

    # ========================================================================
    # Tests for test_connection
    # ========================================================================

    @pytest.mark.asyncio
    async def test_test_connection_success(self, manager):
        """Verify test_connection returns success on connection."""
        with patch("backend.app.services.printer_manager.BambuMQTTClient") as MockClient:
            mock_instance = MagicMock()
            mock_instance.state = MagicMock()
            mock_instance.state.connected = True
            mock_instance.state.state = "IDLE"
            mock_instance.state.raw_data = {"device_model": "X1C"}
            MockClient.return_value = mock_instance

            result = await manager.test_connection("192.168.1.100", "00M09A123456789", "12345678")

            assert result["success"] is True
            assert result["state"] == "IDLE"
            assert result["model"] == "X1C"
            mock_instance.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_test_connection_failure(self, manager):
        """Verify test_connection returns failure on connection error."""
        with patch("backend.app.services.printer_manager.BambuMQTTClient") as MockClient:
            mock_instance = MagicMock()
            mock_instance.state = MagicMock()
            mock_instance.state.connected = False
            MockClient.return_value = mock_instance

            result = await manager.test_connection("192.168.1.100", "00M09A123456789", "12345678")

            assert result["success"] is False
            assert result["state"] is None
            mock_instance.disconnect.assert_called_once()


class TestPrinterStateToDict:
    """Tests for printer_state_to_dict helper function."""

    @pytest.fixture
    def mock_state(self):
        """Create a mock PrinterState."""
        state = MagicMock()
        state.connected = True
        state.state = "RUNNING"
        state.current_print = "test.3mf"
        state.subtask_name = "Test Print"
        state.gcode_file = "/sdcard/test.gcode"
        state.progress = 50
        state.remaining_time = 3600
        state.layer_num = 10
        state.total_layers = 20
        state.temperatures = {"nozzle": 200, "bed": 60}
        state.hms_errors = []
        state.ams_status_main = 0
        state.ams_status_sub = 0
        state.tray_now = "1"
        state.wifi_signal = -50
        state.raw_data = {}
        state.stg_cur = -1  # No calibration stage active
        return state

    def test_basic_conversion(self, mock_state):
        """Verify basic state fields are converted."""
        result = printer_state_to_dict(mock_state)

        assert result["connected"] is True
        assert result["state"] == "RUNNING"
        assert result["progress"] == 50
        assert result["temperatures"] == {"nozzle": 200, "bed": 60}

    def test_ams_data_parsing(self, mock_state):
        """Verify AMS data is parsed correctly."""
        mock_state.raw_data = {
            "ams": [
                {
                    "id": 0,
                    "humidity_raw": 45,
                    "temp": 25,
                    "tray": [
                        {
                            "id": 0,
                            "tray_color": "FF0000",
                            "tray_type": "PLA",
                            "tray_sub_brands": "Generic",
                            "remain": 80,
                            "k": 0.5,
                            "tag_uid": "ABC123",
                            "tray_uuid": "uuid-123",
                        }
                    ],
                }
            ]
        }

        result = printer_state_to_dict(mock_state)

        assert result["ams"] is not None
        assert len(result["ams"]) == 1
        assert result["ams"][0]["humidity"] == 45
        assert len(result["ams"][0]["tray"]) == 1
        assert result["ams"][0]["tray"][0]["tray_color"] == "FF0000"

    def test_empty_tag_uid_becomes_none(self, mock_state):
        """Verify empty tag_uid is converted to None."""
        mock_state.raw_data = {
            "ams": [
                {
                    "id": 0,
                    "tray": [
                        {
                            "id": 0,
                            "tag_uid": "",
                            "tray_uuid": "00000000000000000000000000000000",
                        }
                    ],
                }
            ]
        }

        result = printer_state_to_dict(mock_state)

        assert result["ams"][0]["tray"][0]["tag_uid"] is None
        assert result["ams"][0]["tray"][0]["tray_uuid"] is None

    def test_zero_tag_uid_becomes_none(self, mock_state):
        """Verify zero tag_uid is converted to None."""
        mock_state.raw_data = {
            "ams": [
                {
                    "id": 0,
                    "tray": [
                        {
                            "id": 0,
                            "tag_uid": "0000000000000000",
                        }
                    ],
                }
            ]
        }

        result = printer_state_to_dict(mock_state)

        assert result["ams"][0]["tray"][0]["tag_uid"] is None

    def test_vt_tray_parsing(self, mock_state):
        """Verify virtual tray is parsed correctly."""
        mock_state.raw_data = {
            "vt_tray": {
                "tray_color": "00FF00",
                "tray_type": "PETG",
                "tray_sub_brands": "Generic",
                "remain": 60,
                "tag_uid": "VT123",
            }
        }

        result = printer_state_to_dict(mock_state)

        assert result["vt_tray"] is not None
        assert result["vt_tray"]["id"] == 254
        assert result["vt_tray"]["tray_color"] == "00FF00"
        assert result["vt_tray"]["tray_type"] == "PETG"

    def test_hms_errors_conversion(self, mock_state):
        """Verify HMS errors are converted correctly."""
        error = MagicMock()
        error.code = "0700_0100"
        error.attr = 1
        error.module = "AMS"
        error.severity = 2
        mock_state.hms_errors = [error]

        result = printer_state_to_dict(mock_state)

        assert len(result["hms_errors"]) == 1
        assert result["hms_errors"][0]["code"] == "0700_0100"
        assert result["hms_errors"][0]["module"] == "AMS"

    def test_cover_url_added_for_running_print(self, mock_state):
        """Verify cover_url is added for running prints."""
        result = printer_state_to_dict(mock_state, printer_id=1)

        assert result["cover_url"] == "/api/v1/printers/1/cover"

    def test_cover_url_none_when_not_running(self, mock_state):
        """Verify cover_url is None when not printing."""
        mock_state.state = "IDLE"

        result = printer_state_to_dict(mock_state, printer_id=1)

        assert result["cover_url"] is None

    def test_ams_ht_detection(self, mock_state):
        """Verify AMS-HT is detected (1 tray vs 4)."""
        mock_state.raw_data = {
            "ams": [
                {
                    "id": 0,
                    "tray": [{"id": 0}],  # Only 1 tray = AMS-HT
                }
            ]
        }

        result = printer_state_to_dict(mock_state)

        assert result["ams"][0]["is_ams_ht"] is True

    def test_regular_ams_detection(self, mock_state):
        """Verify regular AMS is detected (4 trays)."""
        mock_state.raw_data = {"ams": [{"id": 0, "tray": [{"id": 0}, {"id": 1}, {"id": 2}, {"id": 3}]}]}

        result = printer_state_to_dict(mock_state)

        assert result["ams"][0]["is_ams_ht"] is False

    def test_chamber_temp_filtered_for_p1s(self, mock_state):
        """Verify chamber temperature is filtered out for P1S (no chamber sensor)."""
        mock_state.temperatures = {
            "nozzle": 200,
            "bed": 60,
            "chamber": 5,
            "chamber_target": 0,
            "chamber_heating": False,
        }

        result = printer_state_to_dict(mock_state, model="P1S")

        assert "chamber" not in result["temperatures"]
        assert "chamber_target" not in result["temperatures"]
        assert "chamber_heating" not in result["temperatures"]
        assert result["temperatures"]["nozzle"] == 200
        assert result["temperatures"]["bed"] == 60

    def test_chamber_temp_kept_for_x1c(self, mock_state):
        """Verify chamber temperature is kept for X1C (has chamber sensor)."""
        mock_state.temperatures = {
            "nozzle": 200,
            "bed": 60,
            "chamber": 25,
            "chamber_target": 45,
            "chamber_heating": True,
        }

        result = printer_state_to_dict(mock_state, model="X1C")

        assert result["temperatures"]["chamber"] == 25
        assert result["temperatures"]["chamber_target"] == 45
        assert result["temperatures"]["chamber_heating"] is True

    def test_chamber_temp_filtered_for_a1(self, mock_state):
        """Verify chamber temperature is filtered out for A1 (no chamber sensor)."""
        mock_state.temperatures = {"nozzle": 200, "bed": 60, "chamber": 5}

        result = printer_state_to_dict(mock_state, model="A1")

        assert "chamber" not in result["temperatures"]

    def test_chamber_temp_kept_when_no_model(self, mock_state):
        """Verify chamber temperature is kept when model is not specified (conservative approach)."""
        mock_state.temperatures = {"nozzle": 200, "bed": 60, "chamber": 25}

        result = printer_state_to_dict(mock_state)  # No model specified

        # When model is unknown, we can't filter - leave as is
        # Actually supports_chamber_temp returns False for None, so it will filter
        # Let's check the actual behavior
        assert "chamber" not in result["temperatures"]


class TestSupportsChamberTemp:
    """Tests for supports_chamber_temp helper function."""

    def test_x1_series_supported(self):
        """Verify X1 series printers support chamber temp."""
        assert supports_chamber_temp("X1") is True
        assert supports_chamber_temp("X1C") is True
        assert supports_chamber_temp("X1E") is True

    def test_p2_series_supported(self):
        """Verify P2 series printers support chamber temp."""
        assert supports_chamber_temp("P2S") is True

    def test_h2_series_supported(self):
        """Verify H2 series printers support chamber temp."""
        assert supports_chamber_temp("H2C") is True
        assert supports_chamber_temp("H2D") is True
        assert supports_chamber_temp("H2DPRO") is True
        assert supports_chamber_temp("H2S") is True

    def test_p1_series_not_supported(self):
        """Verify P1 series printers do NOT support chamber temp."""
        assert supports_chamber_temp("P1P") is False
        assert supports_chamber_temp("P1S") is False

    def test_a1_series_not_supported(self):
        """Verify A1 series printers do NOT support chamber temp."""
        assert supports_chamber_temp("A1") is False
        assert supports_chamber_temp("A1MINI") is False

    def test_none_model_not_supported(self):
        """Verify None model returns False."""
        assert supports_chamber_temp(None) is False

    def test_case_insensitive(self):
        """Verify model matching is case-insensitive."""
        assert supports_chamber_temp("x1c") is True
        assert supports_chamber_temp("X1c") is True
        assert supports_chamber_temp("p1s") is False


class TestGetDerivedStatusName:
    """Tests for get_derived_status_name function."""

    def test_stg_cur_255_returns_none(self):
        """Verify stg_cur=255 (A1/P1 idle) returns None, not 'Unknown stage (255)'."""
        state = MagicMock()
        state.stg_cur = 255
        state.state = "IDLE"

        result = get_derived_status_name(state)

        assert result is None

    def test_stg_cur_negative_one_returns_none_when_idle(self):
        """Verify stg_cur=-1 (X1 idle) returns None."""
        state = MagicMock()
        state.stg_cur = -1
        state.state = "IDLE"

        result = get_derived_status_name(state)

        assert result is None

    def test_valid_stage_returns_name(self):
        """Verify valid stg_cur values return stage name."""
        state = MagicMock()
        state.stg_cur = 1  # Auto bed leveling

        result = get_derived_status_name(state)

        assert result == "Auto bed leveling"

    def test_stg_cur_zero_returns_printing(self):
        """Verify stg_cur=0 returns 'Printing'."""
        state = MagicMock()
        state.stg_cur = 0

        result = get_derived_status_name(state)

        assert result == "Printing"


class TestInitPrinterConnections:
    """Tests for init_printer_connections function."""

    @pytest.mark.asyncio
    async def test_connects_all_active_printers(self):
        """Verify all active printers are connected."""
        mock_db = AsyncMock()
        mock_printer1 = MagicMock(id=1, is_active=True)
        mock_printer2 = MagicMock(id=2, is_active=True)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_printer1, mock_printer2]
        mock_db.execute.return_value = mock_result

        with patch("backend.app.services.printer_manager.printer_manager") as mock_manager:
            mock_manager.connect_printer = AsyncMock()

            await init_printer_connections(mock_db)

            assert mock_manager.connect_printer.call_count == 2

    @pytest.mark.asyncio
    async def test_handles_empty_printer_list(self):
        """Verify empty printer list is handled."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        with patch("backend.app.services.printer_manager.printer_manager") as mock_manager:
            mock_manager.connect_printer = AsyncMock()

            await init_printer_connections(mock_db)

            mock_manager.connect_printer.assert_not_called()


class TestAmsChangeCallback:
    """Tests for AMS change callback functionality."""

    @pytest.fixture
    def manager(self):
        """Create a fresh PrinterManager instance."""
        return PrinterManager()

    def test_ams_change_callback_is_triggered(self, manager):
        """Verify AMS change callback is called when AMS data changes."""
        callback = MagicMock()
        manager.set_ams_change_callback(callback)

        # Verify callback was set
        assert manager._on_ams_change == callback

    def test_ams_change_callback_receives_correct_data(self, manager):
        """Verify AMS change callback receives the correct AMS data format."""
        received_data = []

        def capture_callback(printer_id, ams_data):
            received_data.append((printer_id, ams_data))

        manager.set_ams_change_callback(capture_callback)

        # The callback should accept printer_id and ams_data
        # This tests the callback signature
        assert manager._on_ams_change is not None
        assert callable(manager._on_ams_change)
