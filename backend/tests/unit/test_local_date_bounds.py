"""A date filter means a local calendar day (DB-C12).

The Stats page's Today / This Week / This Month sent UTC dates, and the
backend turned each into a UTC day: in Berlin, "Today" at 01:30 showed the
prints from 02:00 the day before until 01:59. A date is now a day in the
zone the caller names (the Stats page sends the browser's), else in the
server's TZ.
"""

from datetime import date, datetime, timezone

import pytest

from backend.app.utils.local_time import local_date_end, local_date_start


@pytest.fixture
def berlin(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Berlin")


def test_a_berlin_day_starts_at_its_midnight(berlin):
    assert local_date_start(date(2026, 10, 16)) == datetime(2026, 10, 15, 22, 0, tzinfo=timezone.utc)


def test_a_berlin_day_ends_just_before_the_next_midnight(berlin):
    end = local_date_end(date(2026, 10, 16))
    assert end == datetime(2026, 10, 16, 21, 59, 59, 999999, tzinfo=timezone.utc)


def test_the_dst_change_day_is_25_hours(berlin):
    start = local_date_start(date(2026, 10, 25))
    end = local_date_end(date(2026, 10, 25))
    assert (end - start).total_seconds() == pytest.approx(25 * 3600, abs=1)


def test_without_a_tz_a_day_is_a_utc_day(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    assert local_date_start(date(2026, 10, 16)) == datetime(2026, 10, 16, tzinfo=timezone.utc)


def test_the_far_ends_of_the_calendar_do_not_overflow(berlin):
    """An open-ended range sent as 0001-01-01 .. 9999-12-31 must not 500."""
    assert local_date_start(date(1, 1, 1)) == datetime.min.replace(tzinfo=timezone.utc)
    assert local_date_end(date(9999, 12, 31)) == datetime.max.replace(tzinfo=timezone.utc)


def test_a_zone_name_that_is_a_folder_falls_back(monkeypatch):
    from backend.app.utils.local_time import day_zone

    monkeypatch.setenv("TZ", "UTC")
    assert str(day_zone("America")) in ("UTC", "UTC+00:00")
    assert str(day_zone("../etc/passwd")) in ("UTC", "UTC+00:00")
