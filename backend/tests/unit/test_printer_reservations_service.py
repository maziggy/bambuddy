"""Reservation window expansion and conflict checks (#2785)."""

from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from backend.app.services.printer_reservations import (
    RESERVATION_HOLD_PREFIX,
    describe_hold,
    find_conflict,
    is_reservation_hold_reason,
    occurrences,
)

BERLIN = ZoneInfo("Europe/Berlin")
MON, TUE, WED, THU, FRI, SAT, SUN = (1 << d for d in range(7))
WEEKDAYS = MON | TUE | WED | THU | FRI


def _res(**kw):
    defaults = {
        "id": 1,
        "label": "Project X",
        "printer_ids": [1],
        "days_mask": WEEKDAYS,
        "all_day": False,
        "start_minute": 15 * 60,
        "end_minute": 5 * 60,
        "valid_from": None,
        "valid_until": None,
        "created_by_id": 7,
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _utc(local: str) -> datetime:
    """A Berlin wall-clock time as naive UTC."""
    return datetime.fromisoformat(local).replace(tzinfo=BERLIN).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


class TestOccurrences:
    def test_overnight_window_runs_into_the_next_day(self):
        # 2026-11-16 is a Monday.
        occ = occurrences([_res()], _utc("2026-11-16 00:00"), _utc("2026-11-17 00:00"), BERLIN)
        # Sunday's window is not selected, so only Monday 15:00 → Tuesday 05:00.
        assert [(o.start, o.end) for o in occ] == [(_utc("2026-11-16 15:00"), _utc("2026-11-17 05:00"))]

    def test_window_from_the_day_before_reaches_into_range(self):
        occ = occurrences([_res()], _utc("2026-11-17 02:00"), _utc("2026-11-17 03:00"), BERLIN)
        assert len(occ) == 1
        assert occ[0].start == _utc("2026-11-16 15:00")

    def test_friday_night_runs_into_saturday_but_saturday_has_none(self):
        occ = occurrences([_res()], _utc("2026-11-21 00:00"), _utc("2026-11-22 23:59"), BERLIN)
        assert [(o.start, o.end) for o in occ] == [(_utc("2026-11-20 15:00"), _utc("2026-11-21 05:00"))]

    def test_all_day_covers_local_midnight_to_midnight(self):
        res = _res(all_day=True, days_mask=MON | TUE | WED)
        occ = occurrences([res], _utc("2026-11-16 00:00"), _utc("2026-11-23 00:00"), BERLIN)
        assert [(o.start, o.end) for o in occ] == [
            (_utc("2026-11-16 00:00"), _utc("2026-11-17 00:00")),
            (_utc("2026-11-17 00:00"), _utc("2026-11-18 00:00")),
            (_utc("2026-11-18 00:00"), _utc("2026-11-19 00:00")),
        ]

    def test_valid_range_bounds_the_start_day(self):
        res = _res(all_day=True, days_mask=0b1111111, valid_from=date(2026, 11, 18), valid_until=date(2026, 11, 19))
        occ = occurrences([res], _utc("2026-11-16 00:00"), _utc("2026-11-23 00:00"), BERLIN)
        assert [o.start for o in occ] == [_utc("2026-11-18 00:00"), _utc("2026-11-19 00:00")]

    def test_wall_clock_survives_dst(self):
        # Clocks go back on 2026-10-25: the 15:00 start stays 15:00 local.
        res = _res(days_mask=0b1111111, start_minute=15 * 60, end_minute=16 * 60)
        occ = occurrences([res], _utc("2026-10-24 00:00"), _utc("2026-10-26 00:00"), BERLIN)
        assert [o.start for o in occ] == [_utc("2026-10-24 15:00"), _utc("2026-10-25 15:00")]
        assert occ[0].start.hour == 13 and occ[1].start.hour == 14  # UTC shifts by an hour

    def test_no_printers_no_windows(self):
        assert occurrences([_res(printer_ids=[])], _utc("2026-11-16 00:00"), _utc("2026-11-23 00:00"), BERLIN) == []


class TestFindConflict:
    def test_reserved_right_now(self):
        now = _utc("2026-11-16 16:00")
        occ = find_conflict([_res()], 1, now, now, tz=BERLIN)
        assert occ is not None and occ.reservation_id == 1

    def test_print_that_would_run_into_the_window(self):
        start = _utc("2026-11-16 10:00")
        assert find_conflict([_res()], 1, start, _utc("2026-11-16 14:59"), tz=BERLIN) is None
        assert find_conflict([_res()], 1, start, _utc("2026-11-16 15:30"), tz=BERLIN) is not None

    def test_free_during_the_day(self):
        now = _utc("2026-11-16 09:00")
        assert find_conflict([_res()], 1, now, now, tz=BERLIN) is None

    def test_other_printer_is_free(self):
        now = _utc("2026-11-16 16:00")
        assert find_conflict([_res()], 2, now, now, tz=BERLIN) is None

    def test_the_owner_is_not_blocked_by_their_own_reservation(self):
        now = _utc("2026-11-16 16:00")
        assert find_conflict([_res()], 1, now, now, owner_id=7, tz=BERLIN) is None
        assert find_conflict([_res()], 1, now, now, owner_id=8, tz=BERLIN) is not None


def test_hold_reason_is_recognisable():
    now = _utc("2026-11-16 16:00")
    occ = find_conflict([_res()], 1, now, now, tz=BERLIN)
    reason = describe_hold(occ, printer_name="H2D", starts_now=True, tz=BERLIN)
    assert reason.startswith(RESERVATION_HOLD_PREFIX)
    assert "H2D (Project X)" in reason and "Tue 05:00" in reason
    assert is_reservation_hold_reason(reason)
    assert not is_reservation_hold_reason("Busy: H2D")
