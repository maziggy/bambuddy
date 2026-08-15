"""Tests for ScheduledDrying schemas (#2638)."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.app.schemas.scheduled_drying import ScheduledDryingCreate


def test_create_normalizes_aware_datetime_to_naive_utc():
    payload = ScheduledDryingCreate(
        printer_id=1,
        temp=65,
        duration_hours=8,
        start_after=datetime(2026, 7, 23, 18, 0, tzinfo=timezone.utc),
    )
    assert payload.start_after == datetime(2026, 7, 23, 18, 0)
    assert payload.start_after.tzinfo is None


def test_create_accepts_naive_datetime_unchanged():
    payload = ScheduledDryingCreate(printer_id=1, temp=45, duration_hours=1, start_after=datetime(2026, 7, 23, 18, 0))
    assert payload.start_after == datetime(2026, 7, 23, 18, 0)


def test_temp_out_of_range_rejected():
    with pytest.raises(ValidationError):
        ScheduledDryingCreate(printer_id=1, temp=90, duration_hours=8)
    with pytest.raises(ValidationError):
        ScheduledDryingCreate(printer_id=1, temp=44, duration_hours=8)


def test_duration_out_of_range_rejected():
    with pytest.raises(ValidationError):
        ScheduledDryingCreate(printer_id=1, temp=65, duration_hours=0)
    with pytest.raises(ValidationError):
        ScheduledDryingCreate(printer_id=1, temp=65, duration_hours=25)


def test_over_long_filament_rejected():
    """String(50) column: unbounded input 500s on PostgreSQL, passes on SQLite."""
    with pytest.raises(ValidationError):
        ScheduledDryingCreate(printer_id=1, temp=65, duration_hours=8, filament="X" * 51)
    assert ScheduledDryingCreate(printer_id=1, temp=65, duration_hours=8, filament="X" * 50).filament == "X" * 50
