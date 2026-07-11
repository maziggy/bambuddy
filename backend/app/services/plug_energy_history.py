"""Derive Today / Yesterday from a smart plug's lifetime energy counter (#2539).

Most plugs report exactly one energy number, and it is a lifetime counter: a
Shelly's ``aenergy.total`` only ever climbs. Only Tasmota reports Today and
Yesterday itself. So for everything else, those two numbers have to be computed
from the difference between the counter now and the counter at a day boundary —
which is what the hourly ``smart_plug_energy_snapshots`` rows (#941) already
record.

    today     = live_total  - counter at the most recent local midnight
    yesterday = that midnight's counter - the previous midnight's counter

Two things this is careful about:

* **Local midnight, not UTC midnight.** With ``TZ=Europe/Berlin`` a UTC day
  boundary rolls "Today" over at 01:00 or 02:00 wall-clock, which matches
  neither what the user sees nor what the plug's own daily counter would do.

* **Counters reset.** A factory reset or some firmware updates zero a Shelly's
  ``aenergy.total``. The delta then goes negative, and a negative kWh reading is
  worse than an absent one — so we return None and let the UI show a blank
  rather than a number that is definitely wrong.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.smart_plug_energy_snapshot import SmartPlugEnergySnapshot
from backend.app.utils.local_time import local_day_start, to_naive_utc

logger = logging.getLogger(__name__)


async def _counter_at(db: AsyncSession, plug_id: int, boundary: datetime) -> float | None:
    """The plug's lifetime counter as of ``boundary`` — i.e. the last snapshot
    taken at or before it. None when the plug has no snapshot that far back,
    which is the normal state of a fresh install or a fresh upgrade.
    """
    result = await db.execute(
        select(SmartPlugEnergySnapshot.lifetime_kwh)
        .where(
            SmartPlugEnergySnapshot.plug_id == plug_id,
            SmartPlugEnergySnapshot.recorded_at <= to_naive_utc(boundary),
        )
        .order_by(SmartPlugEnergySnapshot.recorded_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def derive_today_yesterday(
    db: AsyncSession,
    plug_id: int,
    live_total_kwh: float,
    *,
    now_utc: datetime | None = None,
) -> tuple[float | None, float | None]:
    """Return ``(today_kwh, yesterday_kwh)`` derived from the lifetime counter.

    Either or both may be None while the snapshot history is still filling up:
    Today needs one snapshot from before this local midnight (so it is available
    within an hour of the first boundary the install lives through), Yesterday
    needs one from before the midnight before that.
    """
    now = now_utc or datetime.now(timezone.utc)
    midnight_today = local_day_start(now)
    midnight_yesterday = local_day_start(now, days_ago=1)

    base_today = await _counter_at(db, plug_id, midnight_today)
    if base_today is None:
        # No snapshot from before today began — nothing can be derived yet.
        return None, None

    today: float | None = live_total_kwh - base_today
    if today < 0:
        logger.info(
            "Plug %s: lifetime counter went backwards (%.3f < %.3f) — "
            "device counter was probably reset; reporting no value for today",
            plug_id,
            live_total_kwh,
            base_today,
        )
        today = None

    base_yesterday = await _counter_at(db, plug_id, midnight_yesterday)
    if base_yesterday is None:
        return today, None

    yesterday: float | None = base_today - base_yesterday
    if yesterday < 0:
        yesterday = None

    return today, yesterday


async def fill_derived_energy(db: AsyncSession, plug_id: int, energy: dict) -> dict:
    """Fill in Today / Yesterday on an energy dict that only has a lifetime total.

    A no-op for Tasmota, which reports both itself — a device that knows its own
    daily usage is more accurate than our hourly-snapshot arithmetic, so a value
    already present is never overwritten.
    """
    total = energy.get("total")
    if total is None:
        return energy
    if energy.get("today") is not None and energy.get("yesterday") is not None:
        return energy

    today, yesterday = await derive_today_yesterday(db, plug_id, float(total))
    if energy.get("today") is None and today is not None:
        energy["today"] = round(today, 3)
    if energy.get("yesterday") is None and yesterday is not None:
        energy["yesterday"] = round(yesterday, 3)
    return energy
