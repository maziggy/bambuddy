"""Unit tests for machine depreciation cost calculation.

Tests the compute_machine_cost helper in archives route and the
_compute_machine_cost method in ArchiveComparisonService.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from backend.app.api.routes.archives import compute_machine_cost
from backend.app.services.archive_comparison import ArchiveComparisonService


def _make_printer(price=None, lifespan_hours=None):
    """Create a mock Printer with price and lifespan."""
    printer = MagicMock()
    printer.price = price
    printer.lifespan_hours = lifespan_hours
    return printer


def _make_archive(
    printer=None,
    status="completed",
    started_at=None,
    completed_at=None,
    print_time_seconds=None,
):
    """Create a mock PrintArchive with timing and printer."""
    archive = MagicMock()
    archive.printer = printer
    archive.status = status
    archive.started_at = started_at
    archive.completed_at = completed_at
    archive.print_time_seconds = print_time_seconds
    return archive


class TestComputeMachineCost:
    """Tests for the compute_machine_cost route helper."""

    def test_basic_calculation_with_actual_duration(self):
        """Machine cost = (price / lifespan_hours) * actual_duration_hours."""
        printer = _make_printer(price=1500.0, lifespan_hours=5000)
        started = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        completed = started + timedelta(hours=2)
        archive = _make_archive(
            printer=printer,
            started_at=started,
            completed_at=completed,
            print_time_seconds=7200,
        )

        result = compute_machine_cost(archive)

        # (1500 / 5000) * 2 = 0.6
        assert result == 0.6

    def test_falls_back_to_slicer_estimate(self):
        """Uses print_time_seconds when actual duration is not available."""
        printer = _make_printer(price=1000.0, lifespan_hours=2000)
        archive = _make_archive(
            printer=printer,
            status="completed",
            started_at=None,
            completed_at=None,
            print_time_seconds=3600,  # 1 hour
        )

        result = compute_machine_cost(archive)

        # (1000 / 2000) * 1 = 0.5
        assert result == 0.5

    def test_returns_none_when_no_printer(self):
        """Returns None when archive has no printer."""
        archive = _make_archive(printer=None, print_time_seconds=3600)

        result = compute_machine_cost(archive)

        assert result is None

    def test_returns_none_when_no_price(self):
        """Returns None when printer has no price set."""
        printer = _make_printer(price=None, lifespan_hours=5000)
        archive = _make_archive(printer=printer, print_time_seconds=3600)

        result = compute_machine_cost(archive)

        assert result is None

    def test_returns_none_when_no_lifespan(self):
        """Returns None when printer has no lifespan_hours set."""
        printer = _make_printer(price=1500.0, lifespan_hours=None)
        archive = _make_archive(printer=printer, print_time_seconds=3600)

        result = compute_machine_cost(archive)

        assert result is None

    def test_returns_none_when_lifespan_zero(self):
        """Returns None when lifespan_hours is zero (avoid division by zero)."""
        printer = _make_printer(price=1500.0, lifespan_hours=0)
        archive = _make_archive(printer=printer, print_time_seconds=3600)

        result = compute_machine_cost(archive)

        assert result is None

    def test_returns_none_when_lifespan_negative(self):
        """Returns None when lifespan_hours is negative."""
        printer = _make_printer(price=1500.0, lifespan_hours=-100)
        archive = _make_archive(printer=printer, print_time_seconds=3600)

        result = compute_machine_cost(archive)

        assert result is None

    def test_returns_none_when_no_duration(self):
        """Returns None when archive has no duration data at all."""
        printer = _make_printer(price=1500.0, lifespan_hours=5000)
        archive = _make_archive(
            printer=printer,
            started_at=None,
            completed_at=None,
            print_time_seconds=None,
        )

        result = compute_machine_cost(archive)

        assert result is None

    def test_prefers_actual_over_slicer_estimate(self):
        """When both actual and slicer durations exist, uses actual."""
        printer = _make_printer(price=1000.0, lifespan_hours=1000)
        started = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        completed = started + timedelta(hours=3)  # 3 hours actual
        archive = _make_archive(
            printer=printer,
            started_at=started,
            completed_at=completed,
            print_time_seconds=7200,  # 2 hours estimated
        )

        result = compute_machine_cost(archive)

        # Should use actual 3 hours, not estimated 2 hours
        # (1000 / 1000) * 3 = 3.0
        assert result == 3.0

    def test_ignores_actual_for_non_completed(self):
        """For non-completed prints, falls back to slicer estimate."""
        printer = _make_printer(price=1000.0, lifespan_hours=1000)
        started = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        completed = started + timedelta(hours=1)
        archive = _make_archive(
            printer=printer,
            status="failed",
            started_at=started,
            completed_at=completed,
            print_time_seconds=7200,  # 2 hours estimated
        )

        result = compute_machine_cost(archive)

        # Failed print: actual time ignored, uses slicer estimate
        # (1000 / 1000) * 2 = 2.0
        assert result == 2.0

    def test_result_is_rounded(self):
        """Result is rounded to 2 decimal places."""
        printer = _make_printer(price=999.0, lifespan_hours=7000)
        archive = _make_archive(
            printer=printer,
            print_time_seconds=5400,  # 1.5 hours
        )

        result = compute_machine_cost(archive)

        # (999 / 7000) * 1.5 = 0.21407... → rounded to 0.21
        assert result == round((999.0 / 7000) * 1.5, 2)

    def test_zero_print_time_seconds(self):
        """Returns None when print_time_seconds is zero."""
        printer = _make_printer(price=1500.0, lifespan_hours=5000)
        archive = _make_archive(
            printer=printer,
            print_time_seconds=0,
        )

        result = compute_machine_cost(archive)

        assert result is None

    def test_30_minute_print(self):
        """30-minute print converts seconds to hours correctly."""
        printer = _make_printer(price=1000.0, lifespan_hours=5000)
        archive = _make_archive(
            printer=printer,
            print_time_seconds=1800,  # 30 minutes = 0.5 hours
        )

        result = compute_machine_cost(archive)

        # cost_per_hour = 1000 / 5000 = 0.2
        # machine_cost = 0.2 * 0.5 = 0.1
        assert result == 0.1

    def test_15_minute_print(self):
        """15-minute print converts seconds to hours correctly."""
        printer = _make_printer(price=1200.0, lifespan_hours=4000)
        archive = _make_archive(
            printer=printer,
            print_time_seconds=900,  # 15 minutes = 0.25 hours
        )

        result = compute_machine_cost(archive)

        # cost_per_hour = 1200 / 4000 = 0.3
        # machine_cost = 0.3 * 0.25 = 0.075 → rounded to 0.07 (banker's rounding)
        expected = round(0.3 * 0.25, 2)
        assert result == expected

    def test_45_second_print(self):
        """Very short 45-second print converts correctly."""
        printer = _make_printer(price=1800.0, lifespan_hours=6000)
        archive = _make_archive(
            printer=printer,
            print_time_seconds=45,  # 45 seconds = 0.0125 hours
        )

        result = compute_machine_cost(archive)

        # cost_per_hour = 1800 / 6000 = 0.3
        # machine_cost = 0.3 * (45/3600) = 0.3 * 0.0125 = 0.00375 → rounded to 0.0
        assert result == 0.0

    def test_90_seconds_print(self):
        """90-second print (1.5 minutes) converts correctly."""
        printer = _make_printer(price=3000.0, lifespan_hours=1000)
        archive = _make_archive(
            printer=printer,
            print_time_seconds=90,  # 90 seconds = 0.025 hours
        )

        result = compute_machine_cost(archive)

        # cost_per_hour = 3000 / 1000 = 3.0
        # machine_cost = 3.0 * (90/3600) = 3.0 * 0.025 = 0.075 → rounded to 0.08
        assert result == 0.08

    def test_actual_duration_in_minutes(self):
        """Actual duration of 45 minutes converts seconds to hours correctly."""
        printer = _make_printer(price=2000.0, lifespan_hours=5000)
        started = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        completed = started + timedelta(minutes=45)  # 45 min = 0.75 hours
        archive = _make_archive(
            printer=printer,
            started_at=started,
            completed_at=completed,
        )

        result = compute_machine_cost(archive)

        # cost_per_hour = 2000 / 5000 = 0.4
        # machine_cost = 0.4 * 0.75 = 0.3
        assert result == 0.3

    def test_actual_duration_minutes_and_seconds(self):
        """Actual duration of 1h 23m 17s converts correctly."""
        printer = _make_printer(price=1500.0, lifespan_hours=5000)
        started = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        completed = started + timedelta(hours=1, minutes=23, seconds=17)
        archive = _make_archive(
            printer=printer,
            started_at=started,
            completed_at=completed,
        )

        result = compute_machine_cost(archive)

        # duration = 1*3600 + 23*60 + 17 = 4997 seconds
        # duration_hours = 4997 / 3600 = 1.38805...
        # cost_per_hour = 1500 / 5000 = 0.3
        # machine_cost = 0.3 * 1.38805... = 0.41641... → rounded to 0.42
        expected = round((1500.0 / 5000) * (4997 / 3600), 2)
        assert result == expected
        assert result == 0.42

    def test_long_print_12_hours_37_minutes(self):
        """Long 12h37m print converts correctly."""
        printer = _make_printer(price=1500.0, lifespan_hours=5000)
        archive = _make_archive(
            printer=printer,
            print_time_seconds=45420,  # 12h 37m = 12*3600 + 37*60 = 45420s
        )

        result = compute_machine_cost(archive)

        # duration_hours = 45420 / 3600 = 12.61666...
        # cost_per_hour = 1500 / 5000 = 0.3
        # machine_cost = 0.3 * 12.61666... = 3.785
        expected = round((1500.0 / 5000) * (45420 / 3600), 2)
        assert result == expected


class TestComparisonServiceComputeMachineCost:
    """Tests for ArchiveComparisonService._compute_machine_cost."""

    def test_matches_route_helper(self):
        """Comparison service method produces same result as route helper."""
        printer = _make_printer(price=1500.0, lifespan_hours=5000)
        started = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        completed = started + timedelta(hours=2)
        archive = _make_archive(
            printer=printer,
            started_at=started,
            completed_at=completed,
            print_time_seconds=7200,
        )

        route_result = compute_machine_cost(archive)
        comparison_result = ArchiveComparisonService._compute_machine_cost(archive)

        assert route_result is not None
        assert comparison_result is not None
        assert round(comparison_result, 2) == route_result

    def test_returns_none_when_no_printer(self):
        """Returns None when archive has no printer."""
        archive = _make_archive(printer=None, print_time_seconds=3600)

        result = ArchiveComparisonService._compute_machine_cost(archive)

        assert result is None
