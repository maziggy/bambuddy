"""Reorder-point forecast for inventory SKUs (#2955).

This is the arithmetic ``frontend/src/components/ForecastPanel.tsx`` runs in the
browser, as pure functions, so something that is not a browser can decide when
a SKU has reached its reorder point or is about to break. ``test_stock_forecast_2955.py``
pins the two to the same numbers on the same inputs.

The pieces, in the order the panel applies them:

* Spools are grouped by SKU -- ``(material, subtype, brand, color_name)``.
* The daily rate comes from usage history when there are at least two distinct
  days of it (weighted by a 30-day half-life, so recent prints dominate), and
  from consumption since the spool baseline over the age of the oldest spool
  otherwise.
* ``reorder point = rate * lead time + safety stock``, where the safety stock
  is a statistical term (``Z_95 * sigma * sqrt(lead time)``) plus a margin the
  user sets in days or grams.
* A *stock break* is "the stock runs out before a replenishment can arrive"; a
  *reorder* is "the reorder point has been reached". They are exclusive: a SKU
  that is already breaking is not also reported as reorderable.

Nothing here touches the database. The caller supplies spools, their usage
history and the settings, and decides what to do with the answer.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone

# One-sided 95 % z-score, as in the panel.
Z_95 = 1.65

# Prints from this many days ago count half as much as one from today.
_HALF_LIFE_DAYS = 30.0
_LAMBDA = math.log(2) / _HALF_LIFE_DAYS

DEFAULT_SAFETY_MARGIN_VALUE = 14
DEFAULT_SAFETY_MARGIN_UNIT = "days"

# With no measured spread the panel assumes 20 % of the rate, and with no rate at
# all it assumes 5 g/day so a margin given in days still means something.
_FALLBACK_SIGMA_FRACTION = 0.2
_FALLBACK_RATE_G_PER_DAY = 5

_SECONDS_PER_DAY = 86400.0

SkuKey = tuple[str, str, str, str]


def sku_key(material: str, subtype: str | None, brand: str | None, color_name: str | None) -> SkuKey:
    """The grouping key. ``None`` and an empty string are the same SKU, as in the panel."""
    return (material, subtype or "", brand or "", color_name or "")


@dataclass(frozen=True)
class StockSpool:
    """The fields of a spool the forecast reads, whichever inventory mode it came from."""

    id: int
    material: str
    subtype: str | None
    brand: str | None
    color_name: str | None
    label_weight: float
    weight_used: float
    weight_used_baseline: float
    created_at: datetime | None
    # True when color_name is not a name the spool carries but the subtype standing in
    # for one (Spoolman mode does this so the list has no blank colours). The SKU key
    # keeps it, as the panel does; anything shown to a person should not.
    color_name_is_synthesized: bool = False

    @property
    def key(self) -> SkuKey:
        return sku_key(self.material, self.subtype, self.brand, self.color_name)

    @property
    def remaining_g(self) -> float:
        return max(0.0, self.label_weight - self.weight_used)

    @property
    def consumed_g(self) -> float:
        """Consumed since the baseline, so "Reset usage to 0" restarts the forecast (#1390)."""
        return max(0.0, self.weight_used - self.weight_used_baseline)


@dataclass(frozen=True)
class UsageRecord:
    """One consumption event: how much a print took from a spool, and when."""

    created_at: datetime
    weight_used: float


@dataclass(frozen=True)
class SkuSettings:
    """A SKU's user-set overrides. The defaults are the panel's for a SKU with no row."""

    lead_time_days: int = 0
    safety_margin_value: float = DEFAULT_SAFETY_MARGIN_VALUE
    safety_margin_unit: str = DEFAULT_SAFETY_MARGIN_UNIT
    alerts_snoozed: bool = False


@dataclass(frozen=True)
class SkuForecast:
    key: SkuKey
    remaining_g: float
    daily_rate_g: float | None
    effective_lead_time_days: int
    reorder_point_g: float
    days_remaining: int | None
    days_until_reorder_point: int | None
    stock_break_alert: bool
    reorder_alert: bool
    snoozed: bool


def _utc(moment: datetime) -> datetime:
    """A naive timestamp is UTC: that is what the database stores."""
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def history_rate(records: Sequence[UsageRecord], now: datetime) -> tuple[float, float] | None:
    """Daily consumption rate and its standard deviation from usage history.

    Records are summed per UTC calendar day, so concurrent multi-spool prints on
    one day count together. Each day after the first gives one observation: the
    grams printed that day over the gap since the previous day with any usage,
    weighted by ``exp(-lambda * age)``. Returns None with fewer than two
    distinct days -- there is no gap to measure -- and the caller falls back to
    :func:`delta_rate`.
    """
    if len(records) < 2:
        return None

    by_day: dict[date, float] = {}
    for record in records:
        day = _utc(record.created_at).date()
        by_day[day] = by_day.get(day, 0.0) + record.weight_used
    if len(by_day) < 2:
        return None

    now = _utc(now)
    days = sorted(by_day.items())
    observations: list[tuple[float, float]] = []  # (rate, weight)
    for i in range(1, len(days)):
        elapsed_days = max((days[i][0] - days[i - 1][0]).days, 1)
        midnight = datetime(days[i][0].year, days[i][0].month, days[i][0].day, tzinfo=timezone.utc)
        age_days = (now - midnight).total_seconds() / _SECONDS_PER_DAY
        observations.append((days[i][1] / elapsed_days, math.exp(-_LAMBDA * age_days)))

    total_weight = sum(w for _, w in observations)
    if total_weight == 0:
        return None
    mean = sum(r * w for r, w in observations) / total_weight
    variance = sum(w * (r - mean) ** 2 for r, w in observations) / total_weight
    return mean, math.sqrt(variance)


def delta_rate(spools: Sequence[StockSpool], now: datetime) -> float | None:
    """Daily rate from consumption since baseline over the age of the oldest spool.

    The fallback when there is not enough usage history, which is always the
    case in Spoolman mode: Spoolman owns the usage there and Bambuddy's own
    ``spool_usage_history`` table holds nothing for those spools. Returns None
    with nothing consumed, or with under a day of age to divide by.
    """
    total_used = sum(s.consumed_g for s in spools)
    if total_used == 0:
        return None
    now = _utc(now)
    oldest = now
    for spool in spools:
        if spool.created_at is not None:
            created = _utc(spool.created_at)
            if created < oldest:
                oldest = created
    age_days = (now - oldest).total_seconds() / _SECONDS_PER_DAY
    if age_days < 1:
        return None
    return total_used / age_days


def forecast_sku(
    spools: Sequence[StockSpool],
    history_by_spool: Mapping[int, Sequence[UsageRecord]],
    settings: SkuSettings,
    global_lead_time_days: int,
    now: datetime,
) -> SkuForecast:
    """Forecast one SKU from its spools. ``spools`` must be non-empty and share a key."""
    lead_time = max(global_lead_time_days, settings.lead_time_days)
    remaining = sum(s.remaining_g for s in spools)

    # Only history from spools that were never reset: a reset spool's earlier
    # events have no anchor and would inflate the rate. A spool with no
    # baseline is clean and keeps its records.
    history: list[UsageRecord] = []
    for spool in spools:
        if spool.weight_used_baseline == 0:
            history.extend(history_by_spool.get(spool.id, ()))

    rate: float | None = None
    std_dev: float | None = None
    measured = history_rate(history, now)
    if measured is not None:
        rate, std_dev = measured
    else:
        rate = delta_rate(spools, now)

    sigma = std_dev if std_dev is not None else (rate * _FALLBACK_SIGMA_FRACTION if rate is not None else 0.0)
    statistical_safety_g = Z_95 * sigma * math.sqrt(lead_time)
    if settings.safety_margin_unit == "g":
        margin_g = settings.safety_margin_value
    elif rate is not None:
        margin_g = rate * settings.safety_margin_value
    else:
        margin_g = settings.safety_margin_value * _FALLBACK_RATE_G_PER_DAY
    safety_stock_g = statistical_safety_g + margin_g
    reorder_point_g = rate * lead_time + safety_stock_g if rate is not None else 0.0

    days_remaining: int | None = None
    days_until_rop: int | None = None
    if rate is not None and rate > 0:
        days_remaining = math.floor(remaining / rate)
        days_until_rop = math.floor((remaining - reorder_point_g) / rate)

    stock_break = days_remaining is not None and lead_time > 0 and days_remaining <= lead_time
    reorder = not stock_break and days_until_rop is not None and days_until_rop <= 0

    return SkuForecast(
        key=spools[0].key,
        remaining_g=remaining,
        daily_rate_g=rate,
        effective_lead_time_days=lead_time,
        reorder_point_g=reorder_point_g,
        days_remaining=days_remaining,
        days_until_reorder_point=days_until_rop,
        stock_break_alert=stock_break,
        reorder_alert=reorder,
        snoozed=settings.alerts_snoozed,
    )


ForecastMap = dict[SkuKey, tuple[SkuForecast, StockSpool]]
"""Each SKU's forecast with the first spool of its group, which names it."""


def forecast_all(
    spools: Sequence[StockSpool],
    history_by_spool: Mapping[int, Sequence[UsageRecord]],
    sku_settings: Mapping[SkuKey, SkuSettings],
    global_lead_time_days: int,
    now: datetime,
) -> ForecastMap:
    """Forecast every SKU present in ``spools``.

    Returns each SKU's forecast with the first spool of its group, which is what
    a caller needs to name the SKU (material, subtype, brand, colour) without a
    second lookup. The caller passes only spools that count as stock -- archived
    ones excluded -- because what is archived differs by inventory mode.

    A colour-specific SKU with no settings row of its own falls back to the
    colourless row, which is where settings saved before colour became part of
    the key still live.
    """
    groups: dict[SkuKey, list[StockSpool]] = {}
    for spool in spools:
        groups.setdefault(spool.key, []).append(spool)

    out: ForecastMap = {}
    for key, members in groups.items():
        settings = sku_settings.get(key)
        if settings is None and key[3] != "":
            settings = sku_settings.get((key[0], key[1], key[2], ""))
        out[key] = (
            forecast_sku(members, history_by_spool, settings or SkuSettings(), global_lead_time_days, now),
            members[0],
        )
    return out
