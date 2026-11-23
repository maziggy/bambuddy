"""The Statistics downloads and failure widget use the dashboard's calendar days."""

import csv
from datetime import datetime, timedelta
from io import BytesIO, StringIO

import pytest
from openpyxl import load_workbook

from backend.app.utils.local_time import utcnow_naive

ARCHIVES = "/api/v1/archives"
DAY = "2020-04-05"
ZONE = "Pacific/Chatham"


@pytest.fixture
async def history(archive_factory, printer_factory):
    printer = await printer_factory()
    # DST ends on 5 April: midnight in Chatham is 10:15 UTC on the 4th,
    # and the next midnight is 11:15 UTC on the 5th (a 25-hour day).
    for when, status in [
        (datetime(2020, 4, 4, 10, 14, 59, 999999), "completed"),
        (datetime(2020, 4, 4, 10, 15), "failed"),
        (datetime(2020, 4, 5, 11, 14, 59, 999999), "aborted"),
        (datetime(2020, 4, 5, 11, 15), "completed"),
        (utcnow_naive() - timedelta(days=45), "completed"),
        (utcnow_naive() - timedelta(days=2), "completed"),
    ]:
        await archive_factory(printer.id, created_at=when, status=status)


async def export_rows(client, fmt, params):
    response = await client.get(f"{ARCHIVES}/stats/export", params={"format": fmt, **params})
    assert response.status_code == 200, response.text
    if fmt == "csv":
        return list(csv.reader(StringIO(response.content.decode("utf-8-sig"))))
    workbook = load_workbook(BytesIO(response.content), read_only=True, data_only=True)
    try:
        return list(workbook["Statistics"].values)
    finally:
        workbook.close()


@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
@pytest.mark.parametrize(
    ("selection", "period", "total", "failed"),
    [
        ({"date_from": DAY, "date_to": DAY}, DAY, 2, 2),
        ({"date_from": "2020-04-04", "date_to": DAY}, f"2020-04-04 to {DAY}", 3, 2),
        ({"date_from": DAY}, f"From {DAY}", 5, 2),
        ({"date_to": DAY}, f"Through {DAY}", 3, 2),
        ({"all_time": "true"}, "All time", 6, 2),
    ],
)
async def test_selected_period(async_client, history, fmt, selection, period, total, failed):
    params = {**selection, "tz": ZONE}
    dashboard = await async_client.get(f"{ARCHIVES}/stats", params=params)
    assert dashboard.status_code == 200
    assert (dashboard.json()["total_prints"], dashboard.json()["failed_prints"]) == (total, failed)

    rows = await export_rows(async_client, fmt, params)
    summary = {row[0]: row[1] for row in rows if len(row) >= 2}
    assert (int(summary["Total Prints"]), int(summary["Failed Prints"])) == (total, failed)
    assert summary["Period"] == period
    trend = rows[next(i for i, row in enumerate(rows) if row[0] == "Week") + 1 :]
    assert sum(int(row[1]) for row in trend) == total
    assert sum(int(row[2]) for row in trend) == failed


@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
@pytest.mark.parametrize(("params", "days", "total"), [({}, 30, 1), ({"days": 60}, 60, 2)])
async def test_rolling_days(async_client, history, fmt, params, days, total):
    rows = await export_rows(async_client, fmt, params)
    summary = {row[0]: row[1] for row in rows if len(row) >= 2}
    assert int(summary["Period (days)"]) == days
    assert (int(summary["Total Prints"]), int(summary["Failed Prints"])) == (total, 0)
    widget = await async_client.get(f"{ARCHIVES}/analysis/failures", params=params)
    assert widget.status_code == 200
    assert (widget.json()["period_days"], widget.json()["total_prints"], widget.json()["failed_prints"]) == (
        days,
        total,
        0,
    )


async def test_widget_includes_old_failures_and_uses_local_weeks(async_client, history):
    async def analysis(**params):
        response = await async_client.get(f"{ARCHIVES}/analysis/failures", params={"tz": ZONE, **params})
        assert response.status_code == 200
        return response.json()

    selected = await analysis(date_from="2020-04-04", date_to="2020-04-11")
    assert (selected["total_prints"], selected["failed_prints"]) == (4, 2)
    assert [(w["week_start"], w["total_prints"], w["failed_prints"]) for w in selected["trend"]] == [
        ("2020-04-04", 1, 0),
        ("2020-04-05", 3, 2),
    ]
    all_time = await analysis(all_time="true")
    assert (all_time["total_prints"], all_time["failed_prints"]) == (6, 2)
    assert all_time["period_days"] is None
    assert (await analysis(date_to="9999-12-31"))["trend"] == all_time["trend"]


async def test_all_time_without_prints(async_client):
    params = {"all_time": "true"}
    response = await async_client.get(f"{ARCHIVES}/analysis/failures", params=params)
    assert response.status_code == 200
    assert response.json()["total_prints"] == 0
    assert response.json()["trend"] == []
    rows = await export_rows(async_client, "csv", params)
    assert rows[1:4] == [["Period", "All time"], ["Total Prints", "0"], ["Failed Prints", "0"]]
