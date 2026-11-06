"""When a credential's last-used time is written again (#3360)."""

from datetime import datetime, timedelta, timezone

from backend.app.services.last_used import LastUsedThrottle

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def _throttle():
    return LastUsedThrottle(timedelta(minutes=1))


def test_never_used_is_due():
    assert _throttle().due("k", None, NOW)


def test_used_within_the_minute_is_not_due():
    assert not _throttle().due("k", NOW - timedelta(seconds=30), NOW)


def test_used_over_a_minute_ago_is_due():
    assert _throttle().due("k", NOW - timedelta(minutes=2), NOW)


def test_a_naive_stored_time_is_read_as_utc():
    assert not _throttle().due("k", (NOW - timedelta(seconds=30)).replace(tzinfo=None), NOW)


def test_a_time_in_the_future_is_due():
    """A clock that ran ahead would otherwise freeze the time until it got there."""
    assert _throttle().due("k", NOW + timedelta(hours=3), NOW)


def test_an_attempt_counts_when_the_stored_time_did_not_move():
    """A failed write leaves the stored time where it was."""
    throttle = _throttle()
    stale = NOW - timedelta(hours=1)

    assert throttle.due("k", stale, NOW)
    assert not throttle.due("k", stale, NOW + timedelta(seconds=10))
    assert throttle.due("k", stale, NOW + timedelta(minutes=2))


def test_attempts_are_per_credential():
    throttle = _throttle()
    assert throttle.due("a", None, NOW)
    assert throttle.due("b", None, NOW)
