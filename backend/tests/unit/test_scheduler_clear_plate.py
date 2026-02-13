"""Tests for the clear plate queue flow in the print scheduler."""

from unittest.mock import MagicMock, patch

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
