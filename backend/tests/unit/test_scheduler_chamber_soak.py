"""Tests for chamber-soak history tracking and smart soak-time reduction.

`_chamber_soak_remaining()` scans a per-printer deque of
(monotonic_timestamp, celsius) samples and returns how many soak seconds
are still needed, crediting time the chamber has already spent above the
target threshold. Real samples arrive every 3–30 s while a printer is
connected; tests use `_dense_history` to model that cadence, or `_history`
(sparse) when specifically exercising gap-detection behaviour.

Key invariants:
  - Empty history → full soak (conservative)
  - Chamber never dipped, contiguous run < soak → credit the run's span
  - Chamber never dipped, contiguous run ≥ soak → skip (return 0)
  - Chamber dipped → credit only time since last below-threshold sample
  - Chamber currently below → full soak (time_above ≈ 0)
  - Gap in samples larger than the cadence threshold → credit only the
    last contiguous run (disconnect must not be counted as time at temp)

`_sample_chamber_temps()` records one sample per connected printer per
tick, prunes entries older than the 2 h TTL, and evicts per-printer state
whose printer_id disappeared from the manager (printer deleted).
"""

from collections import deque
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.app.services.print_scheduler import (
    _CHAMBER_HISTORY_TTL_SECONDS,
    _CHAMBER_SAMPLE_MAX_GAP_SECONDS,
    PrintScheduler,
)

SOAK = 1800  # seconds (30 min, the typical configured value)
TARGET = 50.0  # °C
PRINTER_ID = 1
NOW = 10_000.0


@pytest.fixture
def scheduler():
    return PrintScheduler()


def _history(*entries):
    """Build a deque of (monotonic_ts, celsius) from sparse offset-celsius pairs.

    Offsets are relative to NOW (negative = seconds before now). Use this
    directly when the test needs an explicit gap between samples
    (disconnect/reconnect scenarios). Otherwise prefer `_dense_history`.
    """
    d = deque()
    for offset, temp in entries:
        d.append((NOW + offset, float(temp)))
    return d, NOW


def _dense_history(*entries, interval=30):
    """Build a deque with samples every `interval` seconds between entries,
    step-filled with the value of the previous entry. Mirrors the real
    sampling cadence, so the contiguity guard sees an unbroken run.
    """
    d = deque()
    if not entries:
        return d, NOW
    sorted_entries = sorted(entries, key=lambda e: e[0])
    prev_offset, prev_temp = sorted_entries[0]
    d.append((NOW + prev_offset, float(prev_temp)))
    for offset, temp in sorted_entries[1:]:
        cur = prev_offset + interval
        while cur < offset:
            d.append((NOW + cur, float(prev_temp)))
            cur += interval
        d.append((NOW + offset, float(temp)))
        prev_offset, prev_temp = offset, temp
    return d, NOW


# ---------------------------------------------------------------------------
# No history
# ---------------------------------------------------------------------------


def test_empty_history_returns_full_soak(scheduler):
    """No samples at all → conservative: return configured soak in full."""
    with patch("backend.app.services.print_scheduler.time") as t:
        t.monotonic.return_value = NOW
        result = scheduler._chamber_soak_remaining(PRINTER_ID, TARGET, SOAK)
    assert result == SOAK


# ---------------------------------------------------------------------------
# Chamber never dropped below threshold — contiguous run credit
# ---------------------------------------------------------------------------


def test_history_shorter_than_soak_credits_span(scheduler):
    """Chamber above target for 600 s of contiguous samples.

    Old behaviour returned full soak (wrong). New behaviour credits the
    600 s we have evidence for → remaining = 1800 - 600 = 1200 s.
    """
    hist, now = _dense_history((-600, 55), (0, 53))
    scheduler._chamber_history[PRINTER_ID] = hist

    with patch("backend.app.services.print_scheduler.time") as t:
        t.monotonic.return_value = now
        result = scheduler._chamber_soak_remaining(PRINTER_ID, TARGET, SOAK)

    assert result == SOAK - 600


def test_history_equal_to_soak_returns_zero(scheduler):
    """Chamber above target for exactly soak_seconds → remaining = 0."""
    hist, now = _dense_history((-SOAK, 55), (0, 52))
    scheduler._chamber_history[PRINTER_ID] = hist

    with patch("backend.app.services.print_scheduler.time") as t:
        t.monotonic.return_value = now
        result = scheduler._chamber_soak_remaining(PRINTER_ID, TARGET, SOAK)

    assert result == 0


def test_history_longer_than_soak_returns_zero(scheduler):
    """Chamber above target for longer than soak_seconds → skip entirely."""
    hist, now = _dense_history((-3600, 56), (0, 52))
    scheduler._chamber_history[PRINTER_ID] = hist

    with patch("backend.app.services.print_scheduler.time") as t:
        t.monotonic.return_value = now
        result = scheduler._chamber_soak_remaining(PRINTER_ID, TARGET, SOAK)

    assert result == 0


# ---------------------------------------------------------------------------
# Chamber dipped below threshold at some point
# ---------------------------------------------------------------------------


def test_recent_dip_credits_only_time_since_dip(scheduler):
    """A real cooldown (10 min below threshold) restarts the credit at its end.

    Samples run at the real 30 s cadence: hot until -1500 s, below threshold
    from -1500 s to -900 s, hot again from -870 s. Credit starts at the last
    below-threshold sample (-900 s), so remaining = 1800 - 900 = 900.
    """
    hist, now = _dense_history((-3000, 55), (-1500, 44), (-870, 55), (0, 52))
    scheduler._chamber_history[PRINTER_ID] = hist

    with patch("backend.app.services.print_scheduler.time") as t:
        t.monotonic.return_value = now
        result = scheduler._chamber_soak_remaining(PRINTER_ID, TARGET, SOAK)

    assert result == SOAK - 900


def test_dip_long_enough_ago_returns_zero(scheduler):
    """A real cooldown that ended longer ago than the soak → fully credited → 0."""
    hist, now = _dense_history((-4000, 55), (-2600, 44), (-1970, 55), (0, 52))
    scheduler._chamber_history[PRINTER_ID] = hist

    with patch("backend.app.services.print_scheduler.time") as t:
        t.monotonic.return_value = now
        result = scheduler._chamber_soak_remaining(PRINTER_ID, TARGET, SOAK)

    assert result == 0


# ---------------------------------------------------------------------------
# Dip debounce: brief sub-threshold readings are artifacts, not lost soak
# ---------------------------------------------------------------------------


def test_brief_dip_does_not_reset_credit(scheduler):
    """A single stray low sample must not discard hours of accumulated soak.

    The chamber cannot physically lose and regain 8°C in one sampling interval
    (measured: ~0.2 C/min), so this is a sensor artifact. Crediting from before
    the blip leaves the full hour, i.e. no soak needed.
    """
    hist, now = _dense_history((-3600, 55), (-600, 47), (-540, 55), (0, 55))
    scheduler._chamber_history[PRINTER_ID] = hist

    with patch("backend.app.services.print_scheduler.time") as t:
        t.monotonic.return_value = now
        result = scheduler._chamber_soak_remaining(PRINTER_ID, TARGET, SOAK)

    assert result == 0


def test_four_minute_door_open_dip_does_not_reset_credit(scheduler):
    """The real-world case: opening the door to clear the plate.

    Modelled on an excursion actually recorded on an X1C — roughly four minutes
    below threshold, bottoming one degree under it, then straight back. That is
    air exchange, not the chamber mass cooling, so the soak still counts.
    """
    hist, now = _dense_history((-3600, 55), (-900, 47), (-660, 55), (0, 55))
    scheduler._chamber_history[PRINTER_ID] = hist

    with patch("backend.app.services.print_scheduler.time") as t:
        t.monotonic.return_value = now
        result = scheduler._chamber_soak_remaining(PRINTER_ID, TARGET, SOAK)

    assert result == 0


def test_dip_past_grace_period_does_reset_credit(scheduler):
    """An excursion longer than the grace is real cooling and does reset it.

    Guards the other side of the debounce: 25 minutes below threshold is far
    slower than any artifact and well within the measured cooling rate, so the
    credit restarts at the end of the dip (-1530 s) → 1800 - 1530 = 270.
    """
    hist, now = _dense_history((-5000, 55), (-3000, 45), (-1500, 55), (0, 55))
    scheduler._chamber_history[PRINTER_ID] = hist

    with patch("backend.app.services.print_scheduler.time") as t:
        t.monotonic.return_value = now
        result = scheduler._chamber_soak_remaining(PRINTER_ID, TARGET, SOAK)

    assert result == SOAK - 1530


# ---------------------------------------------------------------------------
# Freshness: an old history is not evidence about the chamber right now
# ---------------------------------------------------------------------------


def test_stale_history_requires_full_soak(scheduler):
    """Hot history whose newest sample predates the max gap → full soak.

    The printer stopped reporting; at the measured cooling rate the chamber can
    cross the threshold inside such a window, so nothing may be credited.
    """
    hist, _ = _dense_history((-7200, 55), (-1800, 55))
    scheduler._chamber_history[PRINTER_ID] = hist

    with patch("backend.app.services.print_scheduler.time") as t:
        t.monotonic.return_value = NOW
        result = scheduler._chamber_soak_remaining(PRINTER_ID, TARGET, SOAK)

    assert result == SOAK


def test_fresh_history_within_max_gap_is_credited(scheduler):
    """Boundary partner: a newest sample inside the max gap still counts."""
    hist, _ = _dense_history((-7200, 55), (-30, 55))
    scheduler._chamber_history[PRINTER_ID] = hist

    with patch("backend.app.services.print_scheduler.time") as t:
        t.monotonic.return_value = NOW
        result = scheduler._chamber_soak_remaining(PRINTER_ID, TARGET, SOAK)

    assert result == 0


def test_currently_below_threshold_returns_full_soak(scheduler):
    """Most recent sample is below threshold → time_above ≈ 0 → full soak."""
    hist, now = _history(
        (-600, 55),
        (-300, 52),
        (0, 45),  # BELOW threshold right now
    )
    scheduler._chamber_history[PRINTER_ID] = hist

    with patch("backend.app.services.print_scheduler.time") as t:
        t.monotonic.return_value = now
        result = scheduler._chamber_soak_remaining(PRINTER_ID, TARGET, SOAK)

    assert result == SOAK


# ---------------------------------------------------------------------------
# Contiguity / gap handling in the no-dip branch
# ---------------------------------------------------------------------------


def test_disconnect_gap_credits_only_last_contiguous_run(scheduler):
    """Chamber above threshold both before AND after a big gap in samples.

    Simulates a printer that was hot, disconnected for 30 min, and came back
    still hot. We cannot claim it was at temperature during the disconnect —
    only the most recent contiguous run counts. Credit = 600 s (post-gap
    run), remaining = 1800 - 600 = 1200.
    """
    pre_gap, _ = _dense_history((-3000, 55), (-2000, 55))
    post_gap, now = _dense_history((-600, 55), (0, 55))
    hist = deque(list(pre_gap) + list(post_gap))
    scheduler._chamber_history[PRINTER_ID] = hist

    with patch("backend.app.services.print_scheduler.time") as t:
        t.monotonic.return_value = now
        result = scheduler._chamber_soak_remaining(PRINTER_ID, TARGET, SOAK)

    assert result == SOAK - 600


def test_sample_gap_at_cadence_threshold_still_contiguous(scheduler):
    """A gap exactly at the max-gap threshold does NOT break contiguity.

    The check is strictly greater-than, so a gap == threshold still credits
    across it. Guards against off-by-one drift in the contiguity heuristic.
    """
    hist, now = _history(
        (-1800, 55),
        (-1800 + int(_CHAMBER_SAMPLE_MAX_GAP_SECONDS), 55),  # gap = threshold exactly
        (0, 55),
    )
    # Fill densely from the second entry onwards so only the first-to-second
    # gap is at the threshold.
    dense_tail, _ = _dense_history(
        (-1800 + int(_CHAMBER_SAMPLE_MAX_GAP_SECONDS), 55),
        (0, 55),
    )
    hist = deque([hist[0]] + list(dense_tail))
    scheduler._chamber_history[PRINTER_ID] = hist

    with patch("backend.app.services.print_scheduler.time") as t:
        t.monotonic.return_value = now
        result = scheduler._chamber_soak_remaining(PRINTER_ID, TARGET, SOAK)

    assert result == 0


# ---------------------------------------------------------------------------
# Tolerance boundary
# ---------------------------------------------------------------------------


def test_tolerance_boundary_above_counts_as_above(scheduler):
    """Sample at target - tolerance + 0.1 is above threshold → credit."""
    threshold_plus = TARGET - 2.0 + 0.1  # 48.1°C — just above threshold
    hist, now = _dense_history((-SOAK, threshold_plus), (0, threshold_plus))
    scheduler._chamber_history[PRINTER_ID] = hist

    with patch("backend.app.services.print_scheduler.time") as t:
        t.monotonic.return_value = now
        result = scheduler._chamber_soak_remaining(PRINTER_ID, TARGET, SOAK)

    assert result == 0


def test_tolerance_boundary_at_threshold_counts_as_above(scheduler):
    """Sample exactly AT target - tolerance is NOT below (strictly less-than).

    Three contiguous samples 30 s apart: 55, 48.0, 55. The middle sample sits
    exactly at the threshold (48.0). If the at-threshold check counted as
    'below', last_below_ts would fire on the middle sample and remaining
    would be SOAK - 30 = 1770. Because the check is strict ``temp < threshold``
    (and 48.0 < 48.0 is False), no dip is found — the whole 60 s contiguous
    span is credited and remaining = SOAK - 60 = 1740.

    Distinguishing the two branches is the point: the OLD test compared
    against 0 no matter which branch fired.
    """
    at_threshold = TARGET - 2.0  # 48.0°C
    hist, now = _history((-60, 55), (-30, at_threshold), (0, 55))
    scheduler._chamber_history[PRINTER_ID] = hist

    with patch("backend.app.services.print_scheduler.time") as t:
        t.monotonic.return_value = now
        result = scheduler._chamber_soak_remaining(PRINTER_ID, TARGET, SOAK)

    assert result == SOAK - 60


# ---------------------------------------------------------------------------
# Result is always non-negative
# ---------------------------------------------------------------------------


def test_result_never_negative(scheduler):
    """Even if the contiguous run spans many times the soak duration, floors at 0."""
    hist, now = _dense_history((-7200, 55), (0, 52))
    scheduler._chamber_history[PRINTER_ID] = hist

    with patch("backend.app.services.print_scheduler.time") as t:
        t.monotonic.return_value = now
        result = scheduler._chamber_soak_remaining(PRINTER_ID, TARGET, SOAK)

    assert result == 0


# ---------------------------------------------------------------------------
# _sample_chamber_temps: recording, TTL, gating, eviction
# ---------------------------------------------------------------------------


def _status(*, connected=True, chamber=None, bed=None):
    """Build a PrinterStatus-shaped namespace. `chamber=None` → key absent."""
    temps: dict = {}
    if chamber is not None:
        temps["chamber"] = chamber
    if bed is not None:
        temps["bed"] = bed
    return SimpleNamespace(connected=connected, temperatures=temps)


def test_sample_chamber_temps_appends_current_reading(scheduler):
    """Each tick appends one (now, chamber_temp) sample per connected printer."""
    with (
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
    ):
        t.monotonic.return_value = NOW
        pm.get_all_statuses.return_value = {PRINTER_ID: _status(chamber=52.5)}
        scheduler._sample_chamber_temps()

    hist = scheduler._chamber_history[PRINTER_ID]
    assert list(hist) == [(NOW, 52.5)]


def test_sample_chamber_temps_prunes_entries_beyond_ttl(scheduler):
    """Samples older than _CHAMBER_HISTORY_TTL_SECONDS are popped from the deque."""
    old = NOW - _CHAMBER_HISTORY_TTL_SECONDS - 100
    recent = NOW - 30
    scheduler._chamber_history[PRINTER_ID] = deque([(old, 55.0), (recent, 55.0)])

    with (
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
    ):
        t.monotonic.return_value = NOW
        pm.get_all_statuses.return_value = {PRINTER_ID: _status(chamber=55.0)}
        scheduler._sample_chamber_temps()

    ts_values = [entry[0] for entry in scheduler._chamber_history[PRINTER_ID]]
    assert old not in ts_values
    assert recent in ts_values


def test_sample_chamber_temps_skips_absent_chamber_key(scheduler):
    """No 'chamber' key (e.g. printer without chamber sensor) → no sample recorded."""
    with (
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
    ):
        t.monotonic.return_value = NOW
        pm.get_all_statuses.return_value = {PRINTER_ID: _status(bed=60.0)}  # no chamber
        scheduler._sample_chamber_temps()

    assert PRINTER_ID not in scheduler._chamber_history


def test_sample_chamber_temps_skips_disconnected_printer(scheduler):
    """A registered but disconnected printer keeps stale temps → don't sample it."""
    with (
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
    ):
        t.monotonic.return_value = NOW
        pm.get_all_statuses.return_value = {PRINTER_ID: _status(connected=False, chamber=55.0)}
        scheduler._sample_chamber_temps()

    assert PRINTER_ID not in scheduler._chamber_history


def test_sample_chamber_temps_evicts_history_for_removed_printer(scheduler):
    """A printer_id present in _chamber_history but not in the manager → evicted."""
    scheduler._chamber_history[99] = deque([(NOW - 100, 55.0)])
    scheduler._chamber_history[PRINTER_ID] = deque([(NOW - 100, 55.0)])

    with (
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
    ):
        t.monotonic.return_value = NOW
        pm.get_all_statuses.return_value = {PRINTER_ID: _status(chamber=55.0)}
        scheduler._sample_chamber_temps()

    assert 99 not in scheduler._chamber_history
    assert PRINTER_ID in scheduler._chamber_history


def test_sample_chamber_temps_evicts_keep_warm_state_for_removed_printer(scheduler):
    """A printer_id in _keep_warm but not in the manager → evicted."""
    from backend.app.services.print_scheduler import _KeepWarmEntry

    scheduler._keep_warm[99] = _KeepWarmEntry(started=NOW - 100, held_target=100)
    scheduler._keep_warm[PRINTER_ID] = _KeepWarmEntry(started=NOW - 100, held_target=100)

    with (
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
    ):
        t.monotonic.return_value = NOW
        pm.get_all_statuses.return_value = {PRINTER_ID: _status(chamber=55.0)}
        scheduler._sample_chamber_temps()

    assert 99 not in scheduler._keep_warm
    assert PRINTER_ID in scheduler._keep_warm


def test_sample_chamber_temps_none_status_ignored(scheduler):
    """get_all_statuses() can return None entries — those must not crash sampling.

    The None-check must run BEFORE `status.connected` is dereferenced, or an
    unregistered / mid-shutdown entry will AttributeError the whole tick.
    """
    with (
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
    ):
        t.monotonic.return_value = NOW
        pm.get_all_statuses.return_value = {2: None, PRINTER_ID: _status(chamber=55.0)}
        scheduler._sample_chamber_temps()

    assert 2 not in scheduler._chamber_history
    assert PRINTER_ID in scheduler._chamber_history
