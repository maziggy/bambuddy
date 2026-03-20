"""Tests for _is_busy_only() in the print scheduler."""

from backend.app.services.print_scheduler import PrintScheduler


class TestIsBusyOnly:
    """Test the _is_busy_only static method."""

    def test_single_busy(self):
        assert PrintScheduler._is_busy_only("Busy: Printer1") is True

    def test_multiple_busy(self):
        assert PrintScheduler._is_busy_only("Busy: Printer1, Printer2") is True

    def test_busy_and_offline(self):
        assert PrintScheduler._is_busy_only("Busy: Printer1 | Offline: Printer2") is False

    def test_busy_and_filament(self):
        assert PrintScheduler._is_busy_only("Busy: Printer1 | Waiting for filament: Printer2 (needs PLA)") is False

    def test_offline_only(self):
        assert PrintScheduler._is_busy_only("Offline: Printer1") is False

    def test_filament_only(self):
        assert PrintScheduler._is_busy_only("Waiting for filament: Printer1 (needs PLA)") is False

    def test_no_matching_color(self):
        assert PrintScheduler._is_busy_only("No matching material/color. Waiting on PLA (Blue)") is False

    def test_no_available_printers(self):
        assert PrintScheduler._is_busy_only("No available P1S printers configured") is False
