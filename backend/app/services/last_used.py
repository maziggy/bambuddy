"""When a credential's last-used time is due to be written again (#3360).

API keys and long-lived tokens record when they were last used. The time is
shown to people, so writing it at most once an interval is enough, and writing
it on every request (each poll of a kiosk) costs a write and a commit per
request. The attempt is remembered in this process too: when writes fail (a
read-only replica after a failover, a locked SQLite database) the stored time
does not move, and without that every request would try again, and on SQLite
wait out the busy timeout first.
"""

from datetime import datetime, timedelta, timezone


class LastUsedThrottle:
    def __init__(self, interval: timedelta):
        self._interval = interval
        # Keyed by the credential's stored hash, which is unique to it and,
        # unlike a row id, never handed to another credential.
        self._attempts: dict[str, datetime] = {}

    def _recent(self, then: datetime | None, now: datetime) -> bool:
        if then is None:
            return False
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        # A time in the future (a clock that ran ahead, a restored backup) is
        # not recent: it would otherwise stop updating until the clock got there.
        return timedelta(0) <= now - then < self._interval

    def due(self, key: str, stored: datetime | None, now: datetime) -> bool:
        """Whether to write the time now, and if so, counts this as the attempt."""
        if self._recent(stored, now) or self._recent(self._attempts.get(key), now):
            return False
        self._attempts[key] = now
        return True
