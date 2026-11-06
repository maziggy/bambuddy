"""A Stats date filter takes the local calendar day (DB-C12)."""

from datetime import datetime

import pytest
from httpx import AsyncClient

from backend.app.models.print_log import PrintLogEntry

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_a_print_at_half_past_one_in_berlin_counts_for_that_day(
    async_client: AsyncClient, printer_factory, db_session, monkeypatch
):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    printer = await printer_factory()
    # 01:30 on 16 October in Berlin is 23:30 on the 15th in UTC.
    db_session.add(
        PrintLogEntry(
            print_name="late night",
            printer_id=printer.id,
            status="completed",
            created_at=datetime(2026, 10, 15, 23, 30),
        )
    )
    await db_session.commit()

    sixteenth = await async_client.get("/api/v1/archives/slim?date_from=2026-10-16&date_to=2026-10-16")
    fifteenth = await async_client.get("/api/v1/archives/slim?date_from=2026-10-15&date_to=2026-10-15")

    assert [r["print_name"] for r in sixteenth.json()] == ["late night"]
    assert fifteenth.json() == []


async def test_the_statistics_totals_take_the_local_day(
    async_client: AsyncClient, printer_factory, db_session, monkeypatch
):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    printer = await printer_factory()
    db_session.add(
        PrintLogEntry(
            print_name="late night", printer_id=printer.id, status="failed", created_at=datetime(2026, 10, 15, 23, 30)
        )
    )
    await db_session.commit()

    stats = (await async_client.get("/api/v1/archives/stats?date_from=2026-10-16&date_to=2026-10-16")).json()
    failures = (
        await async_client.get("/api/v1/archives/analysis/failures?date_from=2026-10-16&date_to=2026-10-16")
    ).json()

    assert stats["total_prints"] == 1
    assert failures["total_prints"] == 1


async def test_the_archive_list_takes_the_local_day(
    async_client: AsyncClient, printer_factory, archive_factory, db_session, monkeypatch
):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    printer = await printer_factory()
    archive = await archive_factory(printer.id, print_name="late night")
    archive.created_at = datetime(2026, 10, 15, 23, 30)
    await db_session.commit()

    listed = (await async_client.get("/api/v1/archives/?date_from=2026-10-16&date_to=2026-10-16")).json()

    assert [a["id"] for a in listed] == [archive.id]


# The browser's own zone, sent with its dates: a browser away from the
# server's TZ (the Docker default is Europe/Berlin) shifted every day.
TOKYO_EARLY = datetime(2026, 10, 16, 23, 30)  # 08:30 on 17 October in Tokyo


async def _tokyo_morning_print(printer_factory, db_session, monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    printer = await printer_factory()
    db_session.add(PrintLogEntry(print_name="morning", printer_id=printer.id, status="failed", created_at=TOKYO_EARLY))
    await db_session.commit()
    return printer


@pytest.mark.parametrize(
    ("path", "count"),
    [
        ("/api/v1/archives/slim", lambda body: len(body)),
        ("/api/v1/archives/stats", lambda body: body["total_prints"]),
        ("/api/v1/archives/analysis/failures", lambda body: body["total_prints"]),
    ],
)
async def test_the_dates_are_days_in_the_zone_the_caller_names(
    async_client: AsyncClient, printer_factory, db_session, monkeypatch, path, count
):
    await _tokyo_morning_print(printer_factory, db_session, monkeypatch)

    tokyo_17th = (await async_client.get(f"{path}?date_from=2026-10-17&date_to=2026-10-17&tz=Asia/Tokyo")).json()
    server_17th = (await async_client.get(f"{path}?date_from=2026-10-17&date_to=2026-10-17")).json()

    assert count(tokyo_17th) == 1
    assert count(server_17th) == 0


async def test_an_unknown_zone_falls_back_to_the_server_s(
    async_client: AsyncClient, printer_factory, db_session, monkeypatch
):
    await _tokyo_morning_print(printer_factory, db_session, monkeypatch)

    response = await async_client.get("/api/v1/archives/slim?date_from=2026-10-16&date_to=2026-10-16&tz=Mars/Olympus")

    assert response.status_code == 200
    assert len(response.json()) == 1
