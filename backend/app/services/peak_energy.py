"""Peak energy windows: decide whether a print or drying run may start now (#2639).

Many users pay time-of-use electricity rates and want the queue to stay out of
the expensive hours (for example 17:00-21:00 on weekdays). This module is the
pure decision core: it takes the configured windows, the current instant and
what a job knows about itself, and says "start now" or "not before T". It does
no I/O and reads no settings, so the scheduler wiring stays thin and every rule
here is testable with plain values.

Windows are wall-clock times in one IANA zone (the server's ``TZ`` by default,
see ``utils.local_time.local_zone``). All instants in and out are aware
datetimes; callers holding naive-UTC column values convert at the boundary.

Prototype for the design in docs/design/peak-energy-scheduling.md.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from enum import StrEnum

# How far ahead to look for a gap a print fits into. A week covers every
# weekly pattern; one more day covers a window that starts late on day 7.
SEARCH_HORIZON = timedelta(days=8)

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class PrintPolicy(StrEnum):
    """How strictly a queued print avoids peak windows."""

    # Never start inside a window. A print started before a window may run
    # into it. Cheapest to explain, never strands a long print.
    START_OUTSIDE = "start_outside"
    # Also hold a print whose estimate says it would run into the next window,
    # until a gap it fits into. Prints longer than every gap fall back to
    # START_OUTSIDE so they still run.
    FINISH_BEFORE = "finish_before"


class DeferReason(StrEnum):
    IN_PEAK_WINDOW = "in_peak_window"
    WOULD_OVERLAP_PEAK = "would_overlap_peak"


@dataclass(frozen=True)
class PeakWindow:
    """One recurring window: on each of ``days``, from ``start`` to ``end``.

    ``days`` are Python weekdays (0 = Monday) on which the window *starts*.
    ``end <= start`` means it ends the next day (22:00-06:00); ``end == start``
    means a full 24 hours.
    """

    days: frozenset[int]
    start: time
    end: time

    def __post_init__(self) -> None:
        if not self.days:
            raise ValueError("a peak window needs at least one day")
        if any(d < 0 or d > 6 for d in self.days):
            raise ValueError(f"weekday out of range: {sorted(self.days)}")

    def occurrence_on(self, day: date, zone: tzinfo) -> tuple[datetime, datetime] | None:
        """The UTC interval this window covers when it starts on local ``day``."""
        if day.weekday() not in self.days:
            return None
        end_day = day if self.end > self.start else day + timedelta(days=1)
        # Combine in the local zone, then normalise through UTC. A wall time
        # that does not exist (spring-forward gap) comes out shifted by the
        # gap; an ambiguous one (fall-back) takes its first occurrence. Either
        # way the window keeps its wall-clock meaning on DST days.
        start = datetime.combine(day, self.start, tzinfo=zone).astimezone(timezone.utc)
        end = datetime.combine(end_day, self.end, tzinfo=zone).astimezone(timezone.utc)
        return start, end


Interval = tuple[datetime, datetime]


def _merge(intervals: Iterable[Interval]) -> list[Interval]:
    merged: list[Interval] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


@dataclass(frozen=True)
class PeakSchedule:
    windows: tuple[PeakWindow, ...]
    zone: tzinfo

    def intervals(self, start: datetime, end: datetime) -> list[Interval]:
        """Merged UTC intervals of every window that overlaps [start, end)."""
        _require_aware(start)
        _require_aware(end)
        # A window can start the local day before ``start`` and still be open.
        first = start.astimezone(self.zone).date() - timedelta(days=1)
        last = end.astimezone(self.zone).date()
        found: list[Interval] = []
        day = first
        while day <= last:
            for window in self.windows:
                occ = window.occurrence_on(day, self.zone)
                if occ and occ[0] < end and occ[1] > start:
                    found.append(occ)
            day += timedelta(days=1)
        return _merge(found)

    def active_until(self, now: datetime) -> datetime | None:
        """When the peak period containing ``now`` ends, or None if off-peak.

        Back-to-back windows count as one period, so the answer is the first
        off-peak instant.
        """
        for start, end in self.intervals(now, now + SEARCH_HORIZON):
            if start <= now < end:
                return end
        return None

    def next_start(self, now: datetime) -> datetime | None:
        """Start of the next peak period strictly after ``now``."""
        for start, _ in self.intervals(now, now + SEARCH_HORIZON):
            if start > now:
                return start
        return None


@dataclass(frozen=True)
class Allow:
    pass


@dataclass(frozen=True)
class Defer:
    not_before: datetime
    reason: DeferReason


StartDecision = Allow | Defer


def decide_print_start(
    schedule: PeakSchedule,
    now: datetime,
    estimated: timedelta | None,
    policy: PrintPolicy,
    margin: timedelta = timedelta(0),
) -> StartDecision:
    """May a queued print start at ``now``?

    ``estimated`` is the slicer's print time; None when unknown, which makes
    FINISH_BEFORE behave like START_OUTSIDE (we cannot promise a finish time
    we cannot estimate). ``margin`` pads the estimate for slicer optimism.
    """
    _require_aware(now)
    in_peak_until = schedule.active_until(now)

    if policy is PrintPolicy.START_OUTSIDE or estimated is None:
        if in_peak_until is None:
            return Allow()
        return Defer(in_peak_until, DeferReason.IN_PEAK_WINDOW)

    needed = estimated + margin
    horizon = now + SEARCH_HORIZON
    # Look past the horizon by the print's own length, so a candidate near the
    # horizon is judged against the windows it would really run into rather
    # than against an empty tail.
    periods = schedule.intervals(now, horizon + needed)
    # Candidate start times: now, and the end of each peak period. The first
    # one that is off-peak and leaves room before the next period wins.
    candidates = [now] + [end for _, end in periods if now < end <= horizon]
    for candidate in candidates:
        if _inside(periods, candidate):
            continue
        following = next((s for s, _ in periods if s > candidate), None)
        if following is None or candidate + needed <= following:
            if candidate == now:
                return Allow()
            reason = DeferReason.IN_PEAK_WINDOW if in_peak_until else DeferReason.WOULD_OVERLAP_PEAK
            return Defer(candidate, reason)

    # Longer than every off-peak gap in the horizon: waiting would never
    # help, so fall back to "just don't start inside a window".
    if in_peak_until is None:
        return Allow()
    return Defer(in_peak_until, DeferReason.IN_PEAK_WINDOW)


def decide_drying_start(schedule: PeakSchedule, now: datetime) -> StartDecision:
    """May a drying run start at ``now``? Drying is interruptible, so only the
    start instant matters; a run that reaches a window is stopped separately."""
    _require_aware(now)
    until = schedule.active_until(now)
    return Allow() if until is None else Defer(until, DeferReason.IN_PEAK_WINDOW)


def should_stop_drying(schedule: PeakSchedule, now: datetime) -> bool:
    """True once a running drying run has reached a peak window."""
    return schedule.active_until(now) is not None


def parse_windows(raw: str) -> tuple[PeakWindow, ...]:
    """Parse the ``peak_energy_windows`` setting.

    Format: a JSON list of ``{"days": [0-6, ...], "start": "HH:MM", "end": "HH:MM"}``.
    An empty string means no windows. Raises ValueError with a message fit for
    a 422 response on anything malformed.
    """
    if not raw.strip():
        return ()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"peak_energy_windows is not valid JSON: {e.msg}") from e
    if not isinstance(data, list):
        raise ValueError("peak_energy_windows must be a JSON list")
    windows: list[PeakWindow] = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError(f"window {i}: must be an object")
        days = entry.get("days")
        if not isinstance(days, list) or not all(isinstance(d, int) and not isinstance(d, bool) for d in days):
            raise ValueError(f"window {i}: days must be a list of weekday numbers 0-6")
        windows.append(
            PeakWindow(
                days=frozenset(days),
                start=_parse_hhmm(entry.get("start"), f"window {i}: start"),
                end=_parse_hhmm(entry.get("end"), f"window {i}: end"),
            )
        )
    return tuple(windows)


def _parse_hhmm(value: object, label: str) -> time:
    if not isinstance(value, str) or not (m := _HHMM.match(value)):
        raise ValueError(f"{label} must be HH:MM (24-hour)")
    return time(int(m.group(1)), int(m.group(2)))


def _inside(periods: list[Interval], instant: datetime) -> bool:
    return any(start <= instant < end for start, end in periods)


def _require_aware(dt: datetime) -> None:
    if dt.tzinfo is None:
        raise ValueError("peak energy decisions need timezone-aware datetimes")
