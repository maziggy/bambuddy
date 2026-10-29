"""The stock forecast's fallback rate counts grams over the time they were used (#3407).

Spoolman keeps per-spool totals only, so the rate there was the grams used
since each spool's baseline over the days since the oldest spool was
registered. Shelf time before a spool's first use counted as use, and a spool
used up inside that span but last used before the 90-day window was left out
whole while its days stayed in. The reporter's SKU of three spools, all
registered 120 days ago, came out at 9.2 g/d.

Now each spool's grams are spread over its own first to last use, and what
falls inside the last 90 days is divided by those days. The panel computes the
same (ForecastPanelDeltaRate.test.tsx pins the same numbers).
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services.stock_forecast import SkuSettings, StockSpool, UsageRecord, delta_rate, forecast_all

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


def _ago(days: float) -> datetime:
    return (NOW - timedelta(days=days)).replace(tzinfo=None)


def _spool(spool_id, used, *, registered, first=None, last=None, archived=False) -> StockSpool:
    """In Spoolman's terms: archiving is dated by the last use, as the mapping does."""
    return StockSpool(
        id=spool_id,
        material="PLA",
        subtype="Silk",
        brand="eSun",
        color_name="Red",
        label_weight=1000,
        weight_used=used,
        weight_used_baseline=0,
        created_at=_ago(registered),
        archived_at=_ago(last) if archived else None,
        first_used_at=_ago(first) if first is not None else None,
        last_used_at=_ago(last) if last is not None else None,
    )


# The reporter's table: A 07-01..07-19, B 08-28..10-07 (both used up), C from 10-07.
A = _spool(1, 1000, registered=120, first=119, last=101, archived=True)
B = _spool(2, 1000, registered=120, first=61, last=21, archived=True)
C = _spool(3, 100, registered=120, first=21, last=2)


def _rate(spools):
    forecast, _ = forecast_all(spools, {}, {}, 0, NOW)[C.key]
    return forecast.daily_rate_g


def test_the_reporters_sku():
    """1100 g in the last 90 days. A was used before them, so the SKU was in
    use for all 90 (the gap between A and B is real zero use)."""
    assert _rate([A, B, C]) == pytest.approx(1100 / 90)


def test_shelf_time_before_the_first_use_is_not_use():
    """Registered 120 days ago, first printed with 21 days ago."""
    assert _rate([C]) == pytest.approx(100 / 21)


def test_a_spool_used_across_the_windows_start_counts_its_share():
    """Used evenly over 100 days, 90 of them inside the window."""
    spool = _spool(1, 1000, registered=150, first=100, last=0)
    assert _rate([spool]) == pytest.approx(900 / 90)


def test_without_first_use_the_span_starts_at_creation():
    """The built-in inventory records no first use and no usage history here:
    the creation date stands in, as before."""
    spool = _spool(1, 300, registered=20)
    assert _rate([spool]) == pytest.approx(15)


def test_a_built_in_spools_first_use_is_its_earliest_record():
    """One day of history is not enough for the trend, but says when use began."""
    spool = _spool(1, 300, registered=120)
    history = {1: [UsageRecord(created_at=_ago(30), weight_used=100)]}
    forecast, _ = forecast_all([spool], history, {}, 0, NOW)[spool.key]
    assert forecast.daily_rate_g == pytest.approx(300 / 30)


def test_a_spool_used_up_within_a_day_counts_at_its_end():
    spool = _spool(1, 200, registered=60, first=10.2, last=10)
    assert delta_rate([spool], NOW) == pytest.approx(200 / 10.2)


def test_a_spool_of_one_print_is_not_dropped():
    """First and last use the same moment: a span of nothing, all of it at once."""
    spool = _spool(1, 200, registered=60, first=10, last=10)
    assert delta_rate([spool], NOW) == pytest.approx(200 / 10)


def test_a_point_before_the_window_adds_nothing():
    """A built-in spool archived lately but last used long before: its grams
    are older than the window, though it counts as recently used up."""
    stock = _spool(1, 90, registered=200, first=200, last=0)
    old = _spool(2, 1000, registered=100, last=100)
    old = StockSpool(**{**old.__dict__, "archived_at": _ago(10), "first_used_at": None})
    assert delta_rate([stock, old], NOW) == pytest.approx(90 * 90 / 200 / 90)


def test_nothing_consumed_is_no_rate():
    assert delta_rate([_spool(1, 0, registered=50)], NOW) is None


def test_nothing_used_in_the_window_is_a_rate_of_zero():
    """Used, but not lately: zero, not unknown."""
    stock = _spool(1, 500, registered=300, first=200, last=150)
    assert _rate([stock]) == 0


def test_settings_do_not_change_the_rate():
    forecast, _ = forecast_all([A, B, C], {}, {C.key: SkuSettings(lead_time_days=7)}, 0, NOW)[C.key]
    assert forecast.daily_rate_g == pytest.approx(1100 / 90)


def test_a_spool_archived_long_ago_and_never_used_does_not_stretch_the_window():
    never = StockSpool(**{**_spool(9, 0, registered=400).__dict__, "archived_at": _ago(400)})
    assert _rate([never, C]) == pytest.approx(100 / 21)


def test_a_built_in_spools_records_count_whatever_its_creation_date():
    """Built-in ids are never handed on, so the reused-id guard is Spoolman's."""
    spool = _spool(1, 300, registered=10)
    history = {1: [UsageRecord(created_at=_ago(d), weight_used=100) for d in (30, 20)]}
    forecast, _ = forecast_all([spool], history, {}, 0, NOW)[spool.key]
    assert forecast.daily_rate_g == pytest.approx(10)
