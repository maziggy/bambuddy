"""Two API answers that did not match what they were asked (F).

``GET /archives/export?date_to=2026-10-15`` read the date as midnight and left
out everything printed that day; every other date filter in Bambuddy treats a
date-only end as the whole day. And ``POST /printers/{id}/print-speed`` with a
mode other than 1-4 reached the printer client, which refused it, and answered
500 "Failed to set print speed" instead of saying the request was invalid.
"""

import csv
import io
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _exported_names(async_client: AsyncClient, **params) -> list[str]:
    response = await async_client.get("/api/v1/archives/export", params={"format": "csv", **params})
    assert response.status_code == 200, response.text
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    return sorted(row["Print Name"] for row in rows)


@pytest.fixture
async def three_days(archive_factory, printer_factory, db_session):
    printer = await printer_factory()
    for name, when in [
        ("day before", datetime(2026, 10, 14, 23, 0)),
        ("that morning", datetime(2026, 10, 15, 9, 30)),
        ("that night", datetime(2026, 10, 15, 23, 59, 30)),
        ("day after", datetime(2026, 10, 16, 0, 0, 1)),
    ]:
        archive = await archive_factory(printer.id, print_name=name)
        archive.created_at = when
    await db_session.commit()


async def test_a_date_only_end_includes_that_whole_day(async_client: AsyncClient, three_days):
    names = await _exported_names(async_client, date_from="2026-10-15", date_to="2026-10-15")

    assert names == ["that morning", "that night"]


async def test_an_end_with_a_time_keeps_its_exact_meaning(async_client: AsyncClient, three_days):
    names = await _exported_names(async_client, date_to="2026-10-15T12:00:00")

    assert names == ["day before", "that morning"]


async def test_a_bound_with_an_offset_is_read_in_utc(async_client: AsyncClient, three_days):
    """11:00+02:00 is 09:00 UTC, the time the archives are stored in."""
    names = await _exported_names(async_client, date_from="2026-10-15T11:00:00+02:00")

    assert names == ["day after", "that morning", "that night"]


async def test_an_unreadable_end_is_still_a_400(async_client: AsyncClient):
    response = await async_client.get("/api/v1/archives/export", params={"date_to": "15.10.2026"})

    assert response.status_code == 400


@pytest.mark.parametrize("mode", [0, 5, -1])
async def test_a_print_speed_mode_outside_1_to_4_is_refused_as_invalid(
    async_client: AsyncClient, printer_factory, mode
):
    printer = await printer_factory()
    client = MagicMock()

    with patch("backend.app.api.routes.printers.printer_manager") as pm:
        pm.get_client.return_value = client
        response = await async_client.post(f"/api/v1/printers/{printer.id}/print-speed", params={"mode": mode})

    assert response.status_code == 422, response.text
    client.set_print_speed.assert_not_called()


async def test_an_end_with_an_offset_is_read_in_utc(async_client: AsyncClient, three_days):
    """01:00+02:00 on the 16th is 23:00 UTC on the 15th: before "that night"."""
    names = await _exported_names(async_client, date_from="2026-10-15", date_to="2026-10-16T01:00:00+02:00")

    assert names == ["that morning"]
