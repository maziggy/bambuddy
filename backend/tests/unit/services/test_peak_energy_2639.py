"""Peak energy windows decide when prints and drying may start (#2639).

Pure-function tests: real zoneinfo zones, real datetimes, no fakes needed.
The reference schedule is a typical US time-of-use plan, 17:00-21:00 on
weekdays in America/Denver.
"""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.app.services.peak_energy import (
    Allow,
    Defer,
    DeferReason,
    PeakSchedule,
    PeakWindow,
    PrintPolicy,
    decide_drying_start,
    decide_print_start,
    parse_windows,
    should_stop_drying,
)

DENVER = ZoneInfo("America/Denver")
WEEKDAYS = frozenset(range(5))
WEEKDAY_EVENINGS = PeakSchedule(windows=(PeakWindow(WEEKDAYS, time(17), time(21)),), zone=DENVER)


def local(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=DENVER)


# 2026-11-16 is a Monday.
MON = (2026, 11, 16)
FRI = (2026, 11, 20)
SAT = (2026, 11, 21)


class TestActiveWindow:
    def test_inside_window_reports_its_end(self):
        assert WEEKDAY_EVENINGS.active_until(local(*MON, 18)) == local(*MON, 21)

    def test_window_start_is_inclusive_end_is_exclusive(self):
        assert WEEKDAY_EVENINGS.active_until(local(*MON, 17)) == local(*MON, 21)
        assert WEEKDAY_EVENINGS.active_until(local(*MON, 21)) is None

    def test_weekend_is_off_peak(self):
        assert WEEKDAY_EVENINGS.active_until(local(*SAT, 18)) is None

    def test_next_start_skips_the_weekend(self):
        assert WEEKDAY_EVENINGS.next_start(local(*FRI, 22)) == local(2026, 11, 23, 17)

    def test_overnight_window_spans_midnight(self):
        overnight = PeakSchedule(windows=(PeakWindow(frozenset({0}), time(22), time(6)),), zone=DENVER)
        # Tuesday 02:00 is inside Monday's 22:00-06:00 window.
        assert overnight.active_until(local(2026, 11, 17, 2)) == local(2026, 11, 17, 6)

    def test_back_to_back_windows_merge(self):
        split = PeakSchedule(
            windows=(PeakWindow(WEEKDAYS, time(14), time(17)), PeakWindow(WEEKDAYS, time(17), time(21))),
            zone=DENVER,
        )
        assert split.active_until(local(*MON, 15)) == local(*MON, 21)

    def test_equal_start_and_end_is_a_full_day(self):
        all_monday = PeakSchedule(windows=(PeakWindow(frozenset({0}), time(0), time(0)),), zone=DENVER)
        assert all_monday.active_until(local(*MON, 12)) == local(2026, 11, 17, 0)


class TestDaylightSaving:
    def test_window_keeps_wall_clock_meaning_across_fall_back(self):
        # 2026-11-01 is the US fall-back Sunday; use a daily window.
        daily = PeakSchedule(windows=(PeakWindow(frozenset(range(7)), time(17), time(21)),), zone=DENVER)
        before = daily.intervals(local(2026, 10, 31, 0), local(2026, 10, 31, 23))
        after = daily.intervals(local(2026, 11, 2, 0), local(2026, 11, 2, 23))
        assert [s.astimezone(DENVER).hour for s, _ in before] == [17]
        assert [s.astimezone(DENVER).hour for s, _ in after] == [17]
        # The UTC offset moved by an hour, the local window did not.
        assert before[0][0].hour == 23 and after[0][0].hour == 0

    def test_window_inside_the_spring_forward_gap_still_has_positive_length(self):
        # 02:00-03:00 does not exist on 2026-03-08 in Denver.
        gap = PeakSchedule(windows=(PeakWindow(frozenset({6}), time(2), time(4)),), zone=DENVER)
        [(start, end)] = gap.intervals(local(2026, 3, 8, 0), local(2026, 3, 8, 12))
        assert end > start


class TestPrintStartOutside:
    def test_off_peak_print_starts(self):
        decision = decide_print_start(WEEKDAY_EVENINGS, local(*MON, 10), timedelta(hours=2), PrintPolicy.START_OUTSIDE)
        assert decision == Allow()

    def test_print_inside_window_waits_for_its_end(self):
        decision = decide_print_start(WEEKDAY_EVENINGS, local(*MON, 18), timedelta(hours=2), PrintPolicy.START_OUTSIDE)
        assert decision == Defer(local(*MON, 21), DeferReason.IN_PEAK_WINDOW)

    def test_long_print_may_run_into_a_window(self):
        decision = decide_print_start(WEEKDAY_EVENINGS, local(*MON, 16), timedelta(hours=10), PrintPolicy.START_OUTSIDE)
        assert decision == Allow()


class TestPrintFinishBefore:
    def test_print_that_fits_before_the_window_starts(self):
        decision = decide_print_start(WEEKDAY_EVENINGS, local(*MON, 12), timedelta(hours=4), PrintPolicy.FINISH_BEFORE)
        assert decision == Allow()

    def test_print_that_would_overlap_waits_until_after_the_window(self):
        decision = decide_print_start(WEEKDAY_EVENINGS, local(*MON, 15), timedelta(hours=4), PrintPolicy.FINISH_BEFORE)
        assert decision == Defer(local(*MON, 21), DeferReason.WOULD_OVERLAP_PEAK)

    def test_margin_counts_toward_the_fit(self):
        decision = decide_print_start(
            WEEKDAY_EVENINGS,
            local(*MON, 13),
            timedelta(hours=3, minutes=50),
            PrintPolicy.FINISH_BEFORE,
            margin=timedelta(minutes=15),
        )
        assert decision == Defer(local(*MON, 21), DeferReason.WOULD_OVERLAP_PEAK)

    def test_friday_evening_print_uses_the_whole_weekend_gap(self):
        decision = decide_print_start(WEEKDAY_EVENINGS, local(*FRI, 18), timedelta(hours=40), PrintPolicy.FINISH_BEFORE)
        assert decision == Defer(local(*FRI, 21), DeferReason.IN_PEAK_WINDOW)

    def test_weekday_print_too_long_for_the_overnight_gap_waits_for_the_weekend(self):
        # 21:00 to 17:00 is a 20 h gap on weekdays; a 30 h print fits only
        # in the gap that starts Friday 21:00.
        decision = decide_print_start(WEEKDAY_EVENINGS, local(*MON, 22), timedelta(hours=30), PrintPolicy.FINISH_BEFORE)
        assert decision == Defer(local(*FRI, 21), DeferReason.WOULD_OVERLAP_PEAK)

    def test_print_longer_than_every_gap_falls_back_to_start_outside(self):
        decision = decide_print_start(WEEKDAY_EVENINGS, local(*MON, 10), timedelta(days=5), PrintPolicy.FINISH_BEFORE)
        assert decision == Allow()
        decision = decide_print_start(WEEKDAY_EVENINGS, local(*MON, 18), timedelta(days=5), PrintPolicy.FINISH_BEFORE)
        assert decision == Defer(local(*MON, 21), DeferReason.IN_PEAK_WINDOW)

    def test_unknown_estimate_behaves_like_start_outside(self):
        decision = decide_print_start(WEEKDAY_EVENINGS, local(*MON, 16, 59), None, PrintPolicy.FINISH_BEFORE)
        assert decision == Allow()


class TestDrying:
    def test_drying_waits_out_the_window(self):
        assert decide_drying_start(WEEKDAY_EVENINGS, local(*MON, 20)) == Defer(
            local(*MON, 21), DeferReason.IN_PEAK_WINDOW
        )

    def test_running_drying_stops_when_a_window_begins(self):
        assert not should_stop_drying(WEEKDAY_EVENINGS, local(*MON, 16, 59))
        assert should_stop_drying(WEEKDAY_EVENINGS, local(*MON, 17))


class TestEmptySchedule:
    def test_no_windows_never_defers(self):
        empty = PeakSchedule(windows=(), zone=timezone.utc)
        now = datetime(2026, 11, 16, 18, tzinfo=timezone.utc)
        assert decide_print_start(empty, now, timedelta(hours=50), PrintPolicy.FINISH_BEFORE) == Allow()
        assert decide_drying_start(empty, now) == Allow()


class TestParseWindows:
    def test_parses_the_setting_format(self):
        windows = parse_windows('[{"days": [0, 1, 2, 3, 4], "start": "17:00", "end": "21:00"}]')
        assert windows == (PeakWindow(WEEKDAYS, time(17), time(21)),)

    def test_empty_string_means_no_windows(self):
        assert parse_windows("") == ()

    @pytest.mark.parametrize(
        "raw",
        [
            "{}",
            "not json",
            '[{"days": [], "start": "17:00", "end": "21:00"}]',
            '[{"days": [7], "start": "17:00", "end": "21:00"}]',
            '[{"days": [true], "start": "17:00", "end": "21:00"}]',
            '[{"days": [0], "start": "5pm", "end": "21:00"}]',
            '[{"days": [0], "start": "24:00", "end": "21:00"}]',
        ],
    )
    def test_rejects_malformed_input(self, raw: str):
        with pytest.raises(ValueError):
            parse_windows(raw)

    def test_naive_datetimes_are_rejected(self):
        with pytest.raises(ValueError):
            WEEKDAY_EVENINGS.active_until(datetime(2026, 11, 16, 18))
