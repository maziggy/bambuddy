"""Tests for the clear plate queue flow in the print scheduler."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.print_scheduler import PrintScheduler
from backend.app.services.printer_manager import PrinterManager


class TestPrinterManagerPlateCleared:
    """Test the plate-cleared flag management in PrinterManager."""

    @pytest.fixture
    def manager(self):
        return PrinterManager()

    def test_plate_cleared_initially_false(self, manager):
        """No printers should have plate cleared by default."""
        assert not manager.is_plate_cleared(1)
        assert not manager.is_plate_cleared(999)

    def test_set_plate_cleared(self, manager):
        """Setting plate cleared should make is_plate_cleared return True."""
        manager.set_plate_cleared(1)
        assert manager.is_plate_cleared(1)
        assert not manager.is_plate_cleared(2)

    def test_consume_plate_cleared(self, manager):
        """Consuming plate cleared should reset the flag."""
        manager.set_plate_cleared(1)
        assert manager.is_plate_cleared(1)
        manager.consume_plate_cleared(1)
        assert not manager.is_plate_cleared(1)

    def test_consume_plate_cleared_idempotent(self, manager):
        """Consuming when not set should not raise."""
        manager.consume_plate_cleared(1)  # Should not raise
        assert not manager.is_plate_cleared(1)

    def test_set_plate_cleared_multiple_printers(self, manager):
        """Plate cleared should be tracked per printer."""
        manager.set_plate_cleared(1)
        manager.set_plate_cleared(3)
        assert manager.is_plate_cleared(1)
        assert not manager.is_plate_cleared(2)
        assert manager.is_plate_cleared(3)

    def test_consume_only_affects_target_printer(self, manager):
        """Consuming plate cleared for one printer should not affect others."""
        manager.set_plate_cleared(1)
        manager.set_plate_cleared(2)
        manager.consume_plate_cleared(1)
        assert not manager.is_plate_cleared(1)
        assert manager.is_plate_cleared(2)


class TestSchedulerIdleCheckWithPlateCleared:
    """Test _is_printer_idle with plate-cleared flag interactions."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_idle_state_is_idle(self, mock_pm, scheduler):
        """Printer in IDLE state should be considered idle."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="IDLE")
        assert scheduler._is_printer_idle(1) is True

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_running_state_not_idle(self, mock_pm, scheduler):
        """Printer in RUNNING state should not be idle."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="RUNNING")
        assert scheduler._is_printer_idle(1) is False

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_finish_state_not_idle_without_plate_cleared(self, mock_pm, scheduler):
        """Printer in FINISH state should NOT be idle without plate cleared."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="FINISH")
        mock_pm.is_plate_cleared.return_value = False
        assert scheduler._is_printer_idle(1) is False

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_finish_state_idle_with_plate_cleared(self, mock_pm, scheduler):
        """Printer in FINISH state should be idle when plate is cleared."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="FINISH")
        mock_pm.is_plate_cleared.return_value = True
        assert scheduler._is_printer_idle(1) is True

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_failed_state_not_idle_without_plate_cleared(self, mock_pm, scheduler):
        """Printer in FAILED state should NOT be idle without plate cleared."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="FAILED")
        mock_pm.is_plate_cleared.return_value = False
        assert scheduler._is_printer_idle(1) is False

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_failed_state_idle_with_plate_cleared(self, mock_pm, scheduler):
        """Printer in FAILED state should be idle when plate is cleared."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="FAILED")
        mock_pm.is_plate_cleared.return_value = True
        assert scheduler._is_printer_idle(1) is True

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_disconnected_printer_not_idle(self, mock_pm, scheduler):
        """Disconnected printer should never be idle."""
        mock_pm.is_connected.return_value = False
        assert scheduler._is_printer_idle(1) is False

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_no_status_not_idle(self, mock_pm, scheduler):
        """Printer with no status should not be idle."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = None
        assert scheduler._is_printer_idle(1) is False


class TestSchedulerQueueCheckLogging:
    """Test queue check logging when pending items are found (#374)."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_check_queue_logs_pending_items(self, mock_pm, scheduler, caplog):
        """Verify pending items are logged when found in check_queue."""
        mock_item = MagicMock()
        mock_item.id = 42
        mock_item.printer_id = 1
        mock_item.archive_id = 100
        mock_item.library_file_id = None
        mock_item.scheduled_time = None
        mock_item.manual_start = False
        mock_item.target_model = None

        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="RUNNING")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_item]

        with (
            patch("backend.app.services.print_scheduler.async_session") as mock_session_ctx,
            caplog.at_level(logging.INFO, logger="backend.app.services.print_scheduler"),
        ):
            mock_db = AsyncMock()
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            await scheduler.check_queue()

        queue_logs = [r for r in caplog.records if "Queue check" in r.message]
        assert len(queue_logs) == 1
        assert "1 pending items" in queue_logs[0].message
        assert "42" in queue_logs[0].message  # item ID

    @pytest.mark.asyncio
    async def test_check_queue_no_log_when_empty(self, scheduler, caplog):
        """Verify no queue log when no pending items found."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        with (
            patch("backend.app.services.print_scheduler.async_session") as mock_session_ctx,
            caplog.at_level(logging.INFO, logger="backend.app.services.print_scheduler"),
        ):
            mock_db = AsyncMock()
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            await scheduler.check_queue()

        queue_logs = [r for r in caplog.records if "Queue check" in r.message]
        assert len(queue_logs) == 0
