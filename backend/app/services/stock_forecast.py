"""Reorder-point forecast for inventory SKUs (#2955).

This is the arithmetic ``frontend/src/components/ForecastPanel.tsx`` runs in the
browser, as pure functions, so something that is not a browser can decide when
a SKU has reached its reorder point or is about to break. ``test_stock_forecast_2955.py``
pins the two to the same numbers on the same inputs.

The pieces, in the order the panel applies them:

* Spools are grouped by SKU -- ``(material, subtype, brand, color_name)``.
  Archived spools are not stock, but one archived in the last
  ``ARCHIVED_RATE_WINDOW_DAYS`` still counts towards how fast the SKU is used:
  used-up spools are archived, and leaving out what they took makes a SKU in
  weekly use look untouched (#3399). A SKU with no stock left is not forecast.
* The daily rate comes from usage history when there are at least two distinct
  days of it (weighted by a 30-day half-life, so recent prints dominate), and
  otherwise from the consumption since each spool's baseline, spread over the
  time the spool was in use and counted over the last
  ``ARCHIVED_RATE_WINDOW_DAYS`` (#3407).
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
from datetime import date, datetime, timedelta, timezone

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

# An archived spool counts towards the rate for this long after it was archived.
# Older ones would stretch the delta rate's age over years and dilute it.
ARCHIVED_RATE_WINDOW_DAYS = 90

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
    # When the spool was archived (Spoolman: its last use); None while it is stock.
    archived_at: datetime | None = None
    # When it was first and last used. Spoolman records both; the built-in
    # inventory only the last, and its first use is its earliest usage record.
    first_used_at: datetime | None = None
    last_used_at: datetime | None = None
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


def archived_counts(archived_at: datetime, now: datetime) -> bool:
    """Whether a spool archived then still counts towards its SKU's rate."""
    return (_utc(now) - _utc(archived_at)).total_seconds() <= ARCHIVED_RATE_WINDOW_DAYS * _SECONDS_PER_DAY


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


def own_records(spool: StockSpool, records: Sequence[UsageRecord]) -> list[UsageRecord]:
    """The records of this spool, not of an earlier one that had its id.

    Spoolman can give a deleted spool's id to a new one -- deleted in
    Spoolman's own UI, or a Spoolman instance replaced -- and the usage rows
    Bambuddy recorded under it stay. A row from before the spool was created
    is not its own (#3407). Built-in ids are never handed on.
    """
    if spool.created_at is None:
        return list(records)
    created = _utc(spool.created_at)
    return [r for r in records if _utc(r.created_at) >= created]


def delta_rate(
    spools: Sequence[StockSpool],
    now: datetime,
    first_use: Mapping[int, datetime] | None = None,
    used_before: bool = False,
) -> float | None:
    """Daily rate from each spool's consumption since its baseline.

    The fallback when there is not enough usage history: a spool's total is
    all there is, so it is spread evenly over the time the spool was in use,
    from its first use (``first_used_at``, else ``first_use[id]``, else its
    creation) to its last (else its archiving, else now). What falls inside the
    last ``ARCHIVED_RATE_WINDOW_DAYS`` is divided by the days of that window,
    which starts no earlier than the SKU's first use; ``used_before`` says the
    SKU has spools archived before the window, so it was in use all along. The
    grams and the days
    then cover the same span: before #3407 the days ran from the oldest
    spool's creation, so shelf time counted as use, and a spool used up
    inside them but last used before the window was left out whole.

    Returns None with nothing consumed, or with under a day to divide by.
    """
    now = _utc(now)
    window_start = now - timedelta(days=ARCHIVED_RATE_WINDOW_DAYS)
    spans: list[tuple[float, datetime, datetime]] = []
    for spool in spools:
        consumed = spool.consumed_g
        if consumed <= 0:
            continue
        end = spool.last_used_at or spool.archived_at
        end = min(_utc(end), now) if end is not None else now
        start = spool.first_used_at or (first_use or {}).get(spool.id) or spool.created_at
        start = min(_utc(start), end) if start is not None else end
        spans.append((consumed, start, end))
    if not spans:
        return None

    observed_from = window_start if used_before else max(window_start, min(start for _, start, _ in spans))
    days = (now - observed_from).total_seconds() / _SECONDS_PER_DAY
    if days < 1:
        return None
    grams = 0.0
    for consumed, start, end in spans:
        length = (end - start).total_seconds()
        if length < _SECONDS_PER_DAY:
            # Used up within a day: all of it at its end.
            grams += consumed if end >= observed_from else 0.0
            continue
        inside = (end - max(start, observed_from)).total_seconds()
        if inside > 0:
            grams += consumed * inside / length
    return grams / days


def forecast_sku(
    spools: Sequence[StockSpool],
    history_by_spool: Mapping[int, Sequence[UsageRecord]],
    settings: SkuSettings,
    global_lead_time_days: int,
    now: datetime,
    used_up: Sequence[StockSpool] = (),
    used_before: bool = False,
) -> SkuForecast:
    """Forecast one SKU from its spools. ``spools`` (its stock) must be non-empty
    and share a key; ``used_up`` are its recently archived spools, which count
    towards the rate only; ``used_before`` says it has spools archived longer
    ago than that."""
    lead_time = max(global_lead_time_days, settings.lead_time_days)
    remaining = sum(s.remaining_g for s in spools)
    rate_spools = [*spools, *used_up]
    own = {spool.id: list(history_by_spool.get(spool.id, ())) for spool in rate_spools}

    # Only history from spools that were never reset: a reset spool's earlier
    # events have no anchor and would inflate the rate. A spool with no
    # baseline is clean and keeps its records.
    history: list[UsageRecord] = []
    for spool in rate_spools:
        if spool.weight_used_baseline == 0:
            history.extend(own[spool.id])

    rate: float | None = None
    std_dev: float | None = None
    measured = history_rate(history, now)
    if measured is not None:
        rate, std_dev = measured
    else:
        # A built-in spool's first use is its earliest record, kept or not:
        # a reset drops records from the rate, not from when use began.
        first_use = {spool.id: min(r.created_at for r in own[spool.id]) for spool in rate_spools if own[spool.id]}
        rate = delta_rate(rate_spools, now, first_use, used_before)

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
    """Forecast every SKU that has stock in ``spools``.

    Returns each SKU's forecast with the first stock spool of its group, which is
    what a caller needs to name the SKU (material, subtype, brand, colour)
    without a second lookup. A spool with ``archived_at`` set is not stock; the
    caller sets it however its inventory mode records archiving. One archived
    within ``ARCHIVED_RATE_WINDOW_DAYS`` counts towards its SKU's rate.

    A colour-specific SKU with no settings row of its own falls back to the
    colourless row, which is where settings saved before colour became part of
    the key still live.
    """
    groups: dict[SkuKey, list[StockSpool]] = {}
    used_up: dict[SkuKey, list[StockSpool]] = {}
    used_before: set[SkuKey] = set()
    for spool in spools:
        if spool.archived_at is None:
            groups.setdefault(spool.key, []).append(spool)
        elif archived_counts(spool.archived_at, now):
            used_up.setdefault(spool.key, []).append(spool)
        elif spool.weight_used > 0:
            # Only a spool that was used says the SKU was in use before the window.
            used_before.add(spool.key)

    out: ForecastMap = {}
    for key, members in groups.items():
        settings = sku_settings.get(key)
        if settings is None and key[3] != "":
            settings = sku_settings.get((key[0], key[1], key[2], ""))
        out[key] = (
            forecast_sku(
                members,
                history_by_spool,
                settings or SkuSettings(),
                global_lead_time_days,
                now,
                used_up=used_up.get(key, ()),
                used_before=key in used_before,
            ),
            members[0],
        )
    return out
