"""Expand printer reservations into concrete windows and test for conflicts (#2785).

Pure functions over ``PrinterReservation`` rows (or anything shaped like one),
so the scheduler and the API share one reading of "is this printer reserved
between A and B". All instants in and out are naive UTC, matching the DB
columns; the weekly pattern itself is read in the server's local zone.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.utils.local_time import local_zone

MINUTES_PER_DAY = 24 * 60

# Prefix of the waiting_reason the scheduler writes when it stages a job
# because of a reservation. The manual_start branch keeps a reason with this
# prefix instead of clearing it, the same way it keeps the unmatched-filament
# one: it is why the job waits for the user.
RESERVATION_HOLD_PREFIX = "Reserved: "


class ReservationLike(Protocol):
    id: int
    label: str
    printer_ids: list[int]
    days_mask: int
    all_day: bool
    start_minute: int
    end_minute: int
    valid_from: date | None
    valid_until: date | None
    created_by_id: int | None


@dataclass(frozen=True)
class Occurrence:
    reservation_id: int
    label: str
    printer_ids: tuple[int, ...]
    created_by_id: int | None
    # Naive UTC.
    start: datetime
    end: datetime


def _to_local(dt: datetime, tz: tzinfo) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def _to_naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _wall(day: date, minute: int, tz: tzinfo) -> datetime:
    return datetime.combine(day, time(minute // 60, minute % 60), tzinfo=tz)


def window_on(res: ReservationLike, day: date, tz: tzinfo) -> tuple[datetime, datetime] | None:
    """The reservation's window starting on local ``day``, as aware datetimes, or None."""
    if not (res.days_mask >> day.weekday()) & 1:
        return None
    if res.valid_from and day < res.valid_from:
        return None
    if res.valid_until and day > res.valid_until:
        return None
    if res.all_day:
        return _wall(day, 0, tz), _wall(day + timedelta(days=1), 0, tz)
    start = _wall(day, res.start_minute, tz)
    # end <= start runs past midnight; equal means a full 24 hours.
    end_day = day if res.end_minute > res.start_minute else day + timedelta(days=1)
    return start, _wall(end_day, res.end_minute, tz)


def occurrences(
    reservations: Iterable[ReservationLike],
    start: datetime,
    end: datetime,
    tz: tzinfo | None = None,
) -> list[Occurrence]:
    """Every reservation window overlapping ``[start, end)``, sorted by start."""
    tz = tz or local_zone()
    local_start = _to_local(start, tz)
    local_end = _to_local(end, tz)
    out: list[Occurrence] = []
    for res in reservations:
        if not res.printer_ids:
            continue
        # A window can start the day before and run past midnight into range.
        day = local_start.date() - timedelta(days=1)
        while day <= local_end.date():
            window = window_on(res, day, tz)
            if window and window[0] < local_end and window[1] > local_start:
                out.append(
                    Occurrence(
                        reservation_id=res.id,
                        label=res.label or "",
                        printer_ids=tuple(res.printer_ids),
                        created_by_id=res.created_by_id,
                        start=_to_naive_utc(window[0]),
                        end=_to_naive_utc(window[1]),
                    )
                )
            day += timedelta(days=1)
    out.sort(key=lambda o: (o.start, o.reservation_id))
    return out


def find_conflict(
    reservations: Iterable[ReservationLike],
    printer_id: int,
    start: datetime,
    end: datetime,
    *,
    owner_id: int | None = None,
    tz: tzinfo | None = None,
) -> Occurrence | None:
    """The first reservation window on ``printer_id`` that overlaps ``[start, end]``.

    A zero-length range (unknown print time) still conflicts with a window it
    falls inside. Reservations owned by ``owner_id`` never conflict: whoever
    reserved the printer is who it is being kept free for.
    """
    relevant = [
        r
        for r in reservations
        if printer_id in (r.printer_ids or []) and not (owner_id is not None and r.created_by_id == owner_id)
    ]
    if not relevant:
        return None
    # Widen a zero-length range by a second so "now" inside a window matches.
    probe_end = end if end > start else start + timedelta(seconds=1)
    hits = occurrences(relevant, start, probe_end, tz)
    return hits[0] if hits else None


def describe_hold(occ: Occurrence, *, printer_name: str, starts_now: bool, tz: tzinfo | None = None) -> str:
    """The waiting_reason for a job staged because of ``occ``."""
    tz = tz or local_zone()
    what = f"{printer_name} ({occ.label})" if occ.label else printer_name
    local_start = _to_local(occ.start, tz)
    local_end = _to_local(occ.end, tz)
    if starts_now:
        return f"{RESERVATION_HOLD_PREFIX}{what} until {local_end:%a %H:%M} — start it by hand to override"
    return (
        f"{RESERVATION_HOLD_PREFIX}{what} from {local_start:%a %H:%M}, before this print would finish "
        "— start it by hand to override"
    )


def is_reservation_hold_reason(reason: str | None) -> bool:
    return bool(reason) and reason.startswith(RESERVATION_HOLD_PREFIX)


async def load_reservations(db: AsyncSession) -> list:
    """All reservation rows. The table is small: a handful per printer at most."""
    from backend.app.models.printer_reservation import PrinterReservation

    result = await db.execute(select(PrinterReservation).order_by(PrinterReservation.id))
    return list(result.scalars().all())


async def drop_printer_from_reservations(db: AsyncSession, printer_id: int) -> None:
    """Remove a deleted printer from every reservation; delete ones left empty.

    Does not commit: runs inside the printer delete's transaction.
    """
    for row in await load_reservations(db):
        ids = list(row.printer_ids or [])
        if printer_id not in ids:
            continue
        remaining = [pid for pid in ids if pid != printer_id]
        if remaining:
            # A new list, so the JSON column registers the change.
            row.printer_ids = remaining
        else:
            await db.delete(row)
