"""The backend forecast against ForecastPanel.tsx, on the same inputs (#2955).

``stock_forecast`` is the panel's arithmetic moved out of the browser so the
stock alerts have something to run on. The two exist side by side until the
panel reads the backend's values, so these tests pin them: ``EXPECTED`` was
produced by running the panel's own functions (``computeHistoryRate``,
``computeDeltaRate`` and the block that turns them into a reorder point and the
two alert flags, copied verbatim from ``ForecastPanel.tsx``) under Node with
``Date.now()`` fixed to ``NOW`` and ``TZ=UTC``, on the scenarios below. A change
to either side that moves a number shows up here.

``TZ=UTC`` matters for exactly one thing: the panel parses a timezone-less
``created_at`` as browser-local time, the backend reads it as UTC, and the delta
rate divides by an age in days, so the two differ by the browser's offset. The
database stores UTC, so UTC is what the timestamps mean.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services.stock_forecast import (
    SkuSettings,
    StockSpool,
    UsageRecord,
    delta_rate,
    forecast_all,
    forecast_sku,
    history_rate,
    sku_key,
)

NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)


def _ago(days: float) -> datetime:
    """A naive UTC timestamp ``days`` before NOW, the shape the database returns."""
    return (NOW - timedelta(days=days)).replace(tzinfo=None)


def _spool(spool_id: int, used: float, age_days: float, baseline: float = 0) -> StockSpool:
    return StockSpool(
        id=spool_id,
        material="PLA",
        subtype="Basic",
        brand="Bambu Lab",
        color_name="Black",
        label_weight=1000,
        weight_used=used,
        weight_used_baseline=baseline,
        created_at=_ago(age_days),
    )


def _history(*events: tuple[int, float, float]) -> dict[int, list[UsageRecord]]:
    """(spool id, days ago, grams) triples, grouped by spool the way the caller does."""
    out: dict[int, list[UsageRecord]] = {}
    for spool_id, ago, grams in events:
        out.setdefault(spool_id, []).append(UsageRecord(created_at=_ago(ago), weight_used=grams))
    return out


# name -> (spools, history, settings, global lead time). The same scenarios, in the
# same order, as the Node run that produced EXPECTED.
SCENARIOS = {
    # History rate, no settings row, lead time from the global setting.
    "A": (
        [_spool(1, 600, 60), _spool(2, 0, 10)],
        _history((1, 40, 120), (1, 25, 90), (1, 12, 200), (1, 3, 150)),
        None,
        7,
    ),
    # No history: the delta rate, with the margin given in days.
    "B": ([_spool(1, 300, 20)], {}, SkuSettings(lead_time_days=14, safety_margin_value=10), 0),
    # A reset spool's history is left out; the clean spool's is kept.
    "C": (
        [_spool(1, 700, 50, baseline=200), _spool(2, 300, 30)],
        _history((1, 20, 400), (1, 10, 50), (2, 20, 100), (2, 6, 100), (2, 1, 100)),
        SkuSettings(lead_time_days=5),
        0,
    ),
    # Stock break from the delta rate: 100 g left, 30 days of lead time.
    "D": ([_spool(1, 900, 30)], {}, SkuSettings(lead_time_days=30), 0),
    # Margin in grams.
    "E": (
        [_spool(1, 500, 90)],
        _history((1, 30, 80), (1, 20, 60), (1, 10, 100)),
        SkuSettings(lead_time_days=10, safety_margin_value=150, safety_margin_unit="g"),
        0,
    ),
    # Nothing consumed: no rate, so no dates and no alerts.
    "F": ([_spool(1, 0, 40)], {}, None, 7),
    # Younger than a day: the delta rate is not measurable. The global lead time wins over the SKU's.
    "G": ([_spool(1, 200, 0.5)], {}, SkuSettings(lead_time_days=3), 9),
    # Under the reorder point with more than the lead time of stock left: a reorder.
    "H": (
        [_spool(1, 850, 100)],
        _history((1, 50, 200), (1, 40, 200), (1, 20, 200)),
        SkuSettings(lead_time_days=6, safety_margin_value=3),
        0,
    ),
    # The same history with less left: a stock break, not also a reorder.
    "I": (
        [_spool(1, 930, 100)],
        _history((1, 50, 200), (1, 40, 200), (1, 20, 200)),
        SkuSettings(lead_time_days=6, safety_margin_value=3),
        0,
    ),
    # Two spools of one SKU: the delta rate divides by the age of the oldest.
    "J": ([_spool(1, 400, 400), _spool(2, 100, 5)], {}, SkuSettings(lead_time_days=10), 0),
}

EXPECTED = {
    "A": {
        "remaining_g": 1400,
        "daily_rate_g": 13.57710201710711,
        "effective_lead_time_days": 7,
        "reorder_point_g": 304.32790456992615,
        "days_remaining": 103,
        "days_until_reorder_point": 80,
        "stock_break_alert": False,
        "reorder_alert": False,
    },
    "B": {
        "remaining_g": 700,
        "daily_rate_g": 15,
        "effective_lead_time_days": 14,
        "reorder_point_g": 378.521204064531,
        "days_remaining": 46,
        "days_until_reorder_point": 21,
        "stock_break_alert": False,
        "reorder_alert": False,
    },
    "C": {
        "remaining_g": 1000,
        "daily_rate_g": 13.942344991570476,
        "effective_lead_time_days": 5,
        "reorder_point_g": 288.583334452561,
        "days_remaining": 71,
        "days_until_reorder_point": 51,
        "stock_break_alert": False,
        "reorder_alert": False,
    },
    "D": {
        "remaining_g": 100,
        "daily_rate_g": 30,
        "effective_lead_time_days": 30,
        "reorder_point_g": 1374.2245331930114,
        "days_remaining": 3,
        "days_until_reorder_point": -43,
        "stock_break_alert": True,
        "reorder_alert": False,
    },
    "E": {
        "remaining_g": 500,
        "daily_rate_g": 8.230026663902231,
        "effective_lead_time_days": 10,
        "reorder_point_g": 242.666532290416,
        "days_remaining": 60,
        "days_until_reorder_point": 31,
        "stock_break_alert": False,
        "reorder_alert": False,
    },
    "F": {
        "remaining_g": 1000,
        "daily_rate_g": None,
        "effective_lead_time_days": 7,
        "reorder_point_g": 0,
        "days_remaining": None,
        "days_until_reorder_point": None,
        "stock_break_alert": False,
        "reorder_alert": False,
    },
    "G": {
        "remaining_g": 800,
        "daily_rate_g": None,
        "effective_lead_time_days": 9,
        "reorder_point_g": 0,
        "days_remaining": None,
        "days_until_reorder_point": None,
        "stock_break_alert": False,
        "reorder_alert": False,
    },
    "H": {
        "remaining_g": 150,
        "daily_rate_g": 13.864882095643093,
        "effective_lead_time_days": 6,
        "reorder_point_g": 144.46457585381498,
        "days_remaining": 10,
        "days_until_reorder_point": 0,
        "stock_break_alert": False,
        "reorder_alert": True,
    },
    "I": {
        "remaining_g": 70,
        "daily_rate_g": 13.864882095643093,
        "effective_lead_time_days": 6,
        "reorder_point_g": 144.46457585381498,
        "days_remaining": 5,
        "days_until_reorder_point": -6,
        "stock_break_alert": True,
        "reorder_alert": False,
    },
    "J": {
        "remaining_g": 1500,
        "daily_rate_g": 1.25,
        "effective_lead_time_days": 10,
        "reorder_point_g": 31.304439534819455,
        "days_remaining": 1200,
        "days_until_reorder_point": 1174,
        "stock_break_alert": False,
        "reorder_alert": False,
    },
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_backend_matches_the_panels_numbers(name):
    spools, history, settings, global_lead = SCENARIOS[name]
    got = forecast_sku(spools, history, settings or SkuSettings(), global_lead, NOW)
    expected = EXPECTED[name]

    for field, want in expected.items():
        have = getattr(got, field)
        if isinstance(want, float):
            assert have == pytest.approx(want, rel=1e-9), field
        else:
            assert have == want, field


def test_the_scenarios_cover_both_alerts_and_neither():
    """The pin means little if no scenario ever alerts."""
    flags = {(e["reorder_alert"], e["stock_break_alert"]) for e in EXPECTED.values()}
    assert flags == {(False, False), (True, False), (False, True)}
