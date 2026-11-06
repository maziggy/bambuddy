"""The electricity price energy is costed at, and the cost of energy over time (#1251).

The price is either the fixed ``energy_cost_per_kwh`` setting or, with
``energy_price_source = "homeassistant"``, a Home Assistant sensor. A sensor
reading is written back into ``energy_cost_per_kwh``, so that setting always
holds the last price known: the Settings page shows it, and it is what is used
whenever Home Assistant can't be reached.

Energy is costed at the price of the hour it was used. Every hourly energy
snapshot carries the price at that moment, and the energy a plug used between
two snapshots is costed at the price of the first. Each snapshot also keeps a
running total of the energy and cost to date, so the cost of any span is the
difference of two rows, like the energy itself. A print or a date range
then costs the sum of its hours, instead of all of it at whatever the price
happens to be when it ends, or today.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.smart_plug_energy_snapshot import SmartPlugEnergySnapshot
from backend.app.utils.local_time import to_naive_utc

logger = logging.getLogger(__name__)

DEFAULT_PRICE = 0.15
_HA_TIMEOUT = 5.0
PRICE_SOURCE_FIXED = "fixed"
PRICE_SOURCE_HOMEASSISTANT = "homeassistant"

# A sensor's unit, after the currency, scaled to per kWh. Day-ahead market
# sensors (Nord Pool, ENTSO-E) are often per MWh.
_ENERGY_UNIT_SCALE = {"kwh": 1.0, "mwh": 0.001, "wh": 1000.0}


async def stored_price(db: AsyncSession) -> float:
    """The ``energy_cost_per_kwh`` setting: the fixed price, or the last one read."""
    from backend.app.api.routes.settings import get_setting

    raw = await get_setting(db, "energy_cost_per_kwh")
    try:
        return float(raw) if raw else DEFAULT_PRICE
    except (TypeError, ValueError):
        return DEFAULT_PRICE


def price_from_state(state: dict | None) -> float | None:
    """A per-kWh price from a Home Assistant state, or None when it has none.

    Negative prices are paid out in some markets; they are costed as free,
    as a cost below zero is not something the rest of Bambuddy expects.
    """
    if not state:
        return None
    try:
        value = float(state.get("state"))
    except (TypeError, ValueError):
        return None
    unit = str((state.get("attributes") or {}).get("unit_of_measurement") or "")
    per = unit.rsplit("/", 1)[-1].strip().lower() if "/" in unit else ""
    value *= _ENERGY_UNIT_SCALE.get(per, 1.0)
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return max(0.0, value)


async def current_price(db: AsyncSession, *, remember: bool = False) -> float:
    """The electricity price now.

    With Home Assistant as the source, reads the sensor; falls back to the last
    price remembered when it can't be read. ``remember`` writes a new reading
    into ``energy_cost_per_kwh`` (the caller commits). Only the hourly loop and
    the settings save do, so the print paths never write a setting in the same
    transaction as the energy reading they exist to save.
    """
    from backend.app.api.routes.settings import get_homeassistant_settings, get_setting, set_setting

    fallback = await stored_price(db)
    if (await get_setting(db, "energy_price_source") or PRICE_SOURCE_FIXED) != PRICE_SOURCE_HOMEASSISTANT:
        return fallback
    entity_id = (await get_setting(db, "energy_price_ha_entity") or "").strip()
    if not entity_id:
        return fallback

    ha = await get_homeassistant_settings(db)
    if not ha["ha_enabled"] or not ha["ha_url"] or not ha["ha_token"]:
        return fallback

    price = price_from_state(await _read_state(ha["ha_url"], ha["ha_token"], entity_id))
    if price is None:
        logger.info("Electricity price sensor %s has no usable value; keeping %s", entity_id, fallback)
        return fallback
    if remember and price != fallback:
        await set_setting(db, "energy_cost_per_kwh", str(price))
    return price


async def _read_state(url: str, token: str, entity_id: str) -> dict | None:
    """One entity's state, or None. Its own short timeout: this runs at print
    start and end, which an unreachable Home Assistant must not hold up."""
    try:
        async with httpx.AsyncClient(timeout=_HA_TIMEOUT) as client:
            response = await client.get(
                f"{url.rstrip('/')}/api/states/{entity_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.debug("Could not read electricity price from %s: %s", entity_id, e)
        return None


def cost_of_readings(readings: list[tuple[float, float | None]], fallback_price: float) -> tuple[float, float]:
    """``(kwh, cost)`` over consecutive ``(lifetime_kwh, price)`` readings.

    Each step is costed at the price of the reading it starts from. A step
    where the counter went backwards (a device reset) counts as nothing.
    """
    kwh = 0.0
    cost = 0.0
    for (start, price), (end, _) in zip(readings, readings[1:], strict=False):
        delta = end - start
        if delta <= 0:
            continue
        kwh += delta
        cost += delta * (price if price is not None else fallback_price)
    return kwh, cost


def cost_at_average_price(kwh: float, costed_kwh: float, costed: float, fallback_price: float) -> float:
    """Cost ``kwh`` at the average price of the energy that could be costed.

    The energy figure shown and the energy summed hour by hour are the same
    unless a counter was reset along the way; this keeps cost and energy
    consistent either way.
    """
    if costed_kwh > 0:
        return kwh * (costed / costed_kwh)
    return kwh * fallback_price


async def print_energy_cost(
    db: AsyncSession,
    *,
    plug_id: int | None,
    start_at: datetime | None,
    start_kwh: float,
    start_price: float | None,
    end_kwh: float,
    end_plug_id: int,
    price_now: float,
) -> float:
    """What the energy a print used cost, hour by hour.

    Falls back to the price now for a print whose start predates #1251, or
    whose end reading came from a different plug than its start.
    """
    energy_used = end_kwh - start_kwh
    if start_at is None or plug_id is None or plug_id != end_plug_id:
        return energy_used * price_now

    result = await db.execute(
        select(SmartPlugEnergySnapshot.lifetime_kwh, SmartPlugEnergySnapshot.price_per_kwh)
        .where(
            SmartPlugEnergySnapshot.plug_id == plug_id,
            SmartPlugEnergySnapshot.recorded_at > to_naive_utc(start_at),
        )
        .order_by(SmartPlugEnergySnapshot.recorded_at, SmartPlugEnergySnapshot.id)
    )
    readings: list[tuple[float, float | None]] = [(start_kwh, start_price)]
    readings.extend((row[0], row[1]) for row in result.all())
    readings.append((end_kwh, None))
    costed_kwh, costed = cost_of_readings(readings, price_now)
    return cost_at_average_price(energy_used, costed_kwh, costed, price_now)


@dataclass
class SnapshotCost:
    """Energy and cost between two points of the snapshot history, summed over plugs.

    ``kwh``/``cost`` come from the running totals and are what is costed;
    ``last_lifetime_kwh`` is the sum of each plug's latest lifetime counter,
    which only the all-time figure needs.
    """

    kwh: float = 0.0
    cost: float = 0.0
    last_lifetime_kwh: float = 0.0


def _running_totals(row: SmartPlugEnergySnapshot, fallback_price: float) -> tuple[float, float]:
    """A snapshot's energy and cost to date. A row without them (none should be
    left after the upgrade backfill) counts its whole counter at its price."""
    kwh = row.kwh_to_date if row.kwh_to_date is not None else row.lifetime_kwh
    if row.cost_to_date is not None:
        return kwh, row.cost_to_date
    price = row.price_per_kwh if row.price_per_kwh is not None else fallback_price
    return kwh, row.lifetime_kwh * price


async def new_snapshot(
    db: AsyncSession, *, plug_id: int, recorded_at: datetime, lifetime_kwh: float, price: float
) -> SmartPlugEnergySnapshot:
    """The next snapshot row for a plug, its running totals carried forward.

    The energy since the previous snapshot is added at the previous snapshot's
    price, so each hour is costed at the price that held during it. A counter
    that went backwards (a device reset) adds nothing. The first snapshot of a
    plug counts what its counter had already reached at the price then.
    """
    s = SmartPlugEnergySnapshot
    prev = (
        await db.execute(select(s).where(s.plug_id == plug_id).order_by(s.recorded_at.desc(), s.id.desc()).limit(1))
    ).scalar_one_or_none()
    if prev is None:
        kwh_to_date, cost_to_date = lifetime_kwh, lifetime_kwh * price
    else:
        prev_kwh, prev_cost = _running_totals(prev, price)
        prev_price = prev.price_per_kwh if prev.price_per_kwh is not None else price
        step = max(0.0, lifetime_kwh - prev.lifetime_kwh)
        kwh_to_date, cost_to_date = prev_kwh + step, prev_cost + step * prev_price
    return s(
        plug_id=plug_id,
        recorded_at=recorded_at,
        lifetime_kwh=lifetime_kwh,
        price_per_kwh=price,
        kwh_to_date=kwh_to_date,
        cost_to_date=cost_to_date,
    )


async def _snapshot_at_or_before(
    db: AsyncSession, plug_id: int, moment: datetime | None
) -> SmartPlugEnergySnapshot | None:
    s = SmartPlugEnergySnapshot
    query = select(s).where(s.plug_id == plug_id)
    if moment is not None:
        query = query.where(s.recorded_at <= moment)
    return (await db.execute(query.order_by(s.recorded_at.desc(), s.id.desc()).limit(1))).scalar_one_or_none()


async def snapshot_cost(
    db: AsyncSession,
    *,
    fallback_price: float,
    dt_from: datetime | None = None,
    dt_to: datetime | None = None,
) -> SnapshotCost:
    """Energy and cost over the same span ``_sum_snapshot_deltas`` measures.

    Per plug: from the last snapshot at or before ``dt_from`` (or its first
    snapshot) to the last at or before ``dt_to``, as the difference of their
    running totals. Without ``dt_from``, from zero: the whole history. Two
    indexed lookups per plug, however long the history is.
    """
    from backend.app.models.smart_plug import SmartPlug

    s = SmartPlugEnergySnapshot
    dt_from = to_naive_utc(dt_from)
    dt_to = to_naive_utc(dt_to)
    result = SnapshotCost()
    for plug_id in (await db.execute(select(SmartPlug.id))).scalars().all():
        end = await _snapshot_at_or_before(db, plug_id, dt_to)
        if end is None:
            continue
        end_kwh, end_cost = _running_totals(end, fallback_price)
        result.last_lifetime_kwh += end.lifetime_kwh
        base_kwh = base_cost = 0.0
        if dt_from is not None:
            base = await _snapshot_at_or_before(db, plug_id, dt_from)
            if base is None:
                base = (
                    await db.execute(select(s).where(s.plug_id == plug_id).order_by(s.recorded_at, s.id).limit(1))
                ).scalar_one_or_none()
            if base is not None:
                base_kwh, base_cost = _running_totals(base, fallback_price)
        kwh, cost = end_kwh - base_kwh, end_cost - base_cost
        # Negative only across a reset in rows from before the upgrade, whose
        # totals are the raw counter; the energy figure clamps those to 0 too.
        if kwh > 0 and cost >= 0:
            result.kwh += kwh
            result.cost += cost
    return result


async def all_time_cost(db: AsyncSession, live_total_kwh: float, price_now: float) -> float:
    """What the plugs' lifetime energy cost, hour by hour where it can be.

    Energy since each plug's latest snapshot, and every plug with no snapshots
    (an MQTT plug), is costed at the price now.
    """
    hist = await snapshot_cost(db, fallback_price=price_now)
    tail = max(0.0, live_total_kwh - hist.last_lifetime_kwh)
    return cost_at_average_price(live_total_kwh, hist.kwh + tail, hist.cost + tail * price_now, price_now)
