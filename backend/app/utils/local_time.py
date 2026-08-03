"""Local-timezone helpers.

Bambuddy has no timezone *setting* — it takes the container's ``TZ`` env var,
the same value the support package reports. Anything that has to reason about a
calendar day ("today", "yesterday", "run the backup at 03:00") needs this,
because a day boundary computed in UTC rolls over at 01:00 or 02:00 wall-clock
for most of Europe, which is neither what the user sees nor what their smart
plug's own daily counter does.

Lived in ``services/local_backup`` until #2539, when the smart-plug energy
history needed the same day boundary and reaching into another service's private
helper stopped being defensible.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)


def local_zone() -> tzinfo:
    """Resolve the local timezone from the ``TZ`` env var.

    Falls back to UTC when ``TZ`` is unset or unrecognised, so a missing value
    degrades to the legacy behaviour rather than crashing.

    On Windows the embedded Python in our installer doesn't carry an IANA tz
    database, so ``ZoneInfo(...)`` — including ``ZoneInfo("UTC")`` — raises
    ``ZoneInfoNotFoundError`` unless the ``tzdata`` PyPI package is installed.
    requirements.txt pins ``tzdata`` on win32, but to stay resilient on installs
    that haven't refreshed deps we fall through to the stdlib
    ``datetime.timezone.utc`` as a last resort; it satisfies every
    ``astimezone`` / ``str()`` call site without needing the IANA DB.
    """
    tz_name = os.environ.get("TZ", "").strip()
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            logger.warning("Unrecognised TZ env value %r, falling back to UTC", tz_name)
    try:
        return ZoneInfo("UTC")
    except ZoneInfoNotFoundError:
        return timezone.utc


def utcnow_naive() -> datetime:
    """Current UTC time, tzinfo stripped.

    Bambuddy's ``DateTime`` columns are naive and hold UTC; only the few that
    genuinely need an offset are declared ``DateTime(timezone=True)``. SQLite
    silently tolerates an aware value written to a naive column (its bind
    processor reads the fields and drops the offset), which is why aware writes
    survived here for so long — but **asyncpg rejects them outright** with
    ``DataError: invalid input for query argument``, so on Postgres the write
    raises. Use this for anything destined for a naive column.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(dt: datetime | None) -> datetime | None:
    """Normalise a datetime to naive UTC for binding against a naive column.

    Accepts naive (assumed already UTC) or aware; returns None unchanged.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def local_day_start(now_utc: datetime, *, days_ago: int = 0) -> datetime:
    """Return midnight local time, ``days_ago`` days back, as a UTC instant.

    ``days_ago=0`` is the midnight that began the current local day; ``1`` is the
    one before it. Subtracting whole days from the *local* wall clock rather than
    from the UTC instant is what keeps this correct across a DST transition, where
    a calendar day is 23 or 25 hours long, not 24.

    ``fold=0`` resolves the ambiguous wall-clock hour at DST fall-back to the
    earlier instance. The spring-forward gap cannot bite here: the synthesized
    time is always midnight, and no timezone in the IANA database skips it.
    """
    tz = local_zone()
    local_now = now_utc.astimezone(tz)
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0, fold=0)
    if days_ago:
        # Step back in local days, then re-pin to midnight: (midnight - 24h) can
        # land at 23:00 or 01:00 of the previous day across a DST change.
        local_midnight = (local_midnight - timedelta(days=days_ago)).replace(
            hour=0, minute=0, second=0, microsecond=0, fold=0
        )
    return local_midnight.astimezone(timezone.utc)


def next_local_hour(now_utc: datetime) -> datetime:
    """Return the next top-of-the-hour *local* time, as a UTC instant.

    Aligning to the local hour rather than the UTC hour is deliberate: it
    guarantees a tick lands exactly on local midnight in every timezone,
    including the half- and quarter-hour offsets (India, Nepal, Chatham) where
    local midnight is not on a UTC hour boundary at all.
    """
    tz = local_zone()
    local_now = now_utc.astimezone(tz)
    local_next = (local_now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0, fold=0)
    return local_next.astimezone(timezone.utc)
