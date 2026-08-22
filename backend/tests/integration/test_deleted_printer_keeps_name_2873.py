"""Statistics keeps naming a printer that was deleted with its history (#2873).

Deleting a printer while keeping its prints leaves the log rows pointing at an
id nothing resolves any more, so every per-printer breakdown fell back to
"Printer 1" for machines the reporter knew as "Ultron". Each run recorded the
name it printed on, so that is what the aggregates report now.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient

from backend.app.models.print_log import PrintLogEntry


@pytest.mark.asyncio
@pytest.mark.integration
async def test_stats_name_history_of_a_deleted_printer(async_client: AsyncClient, db_session):
    base = datetime(2026, 7, 15, 10, 0, 0)
    db_session.add_all(
        [
            PrintLogEntry(
                printer_id=71,
                printer_name="Ultron",
                status="completed",
                started_at=base,
                completed_at=base + timedelta(hours=1),
                duration_seconds=3600,
            ),
            PrintLogEntry(
                printer_id=71,
                printer_name="Ultron",
                status="failed",
                started_at=base + timedelta(hours=2),
                completed_at=base + timedelta(hours=3),
                duration_seconds=3600,
            ),
        ]
    )
    await db_session.commit()

    stats = (await async_client.get("/api/v1/archives/stats")).json()

    assert stats["prints_by_printer"]["71"] == 2
    assert stats["printer_names"]["71"] == "Ultron"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_stats_reports_the_last_name_the_printer_ran_under(async_client: AsyncClient, db_session):
    """A printer renamed before it was deleted is remembered by its last name."""
    base = datetime(2026, 7, 15, 10, 0, 0)
    db_session.add_all(
        [
            PrintLogEntry(printer_id=71, printer_name="Ultron", status="completed", started_at=base),
            PrintLogEntry(
                printer_id=71,
                printer_name="Ultron Mk II",
                status="completed",
                started_at=base + timedelta(days=1),
            ),
            # A run logged before names were recorded must not blank the label.
            PrintLogEntry(printer_id=71, printer_name=None, status="completed", started_at=base + timedelta(days=2)),
        ]
    )
    await db_session.commit()

    stats = (await async_client.get("/api/v1/archives/stats")).json()

    assert stats["printer_names"]["71"] == "Ultron Mk II"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_stats_names_do_not_override_a_live_printer(async_client: AsyncClient, db_session, printer_factory):
    """Renaming a printer that still exists shows up straight away.

    The client prefers the live record, so the historical name only has to be
    present, not authoritative.
    """
    printer = await printer_factory(name="Renamed Later")
    db_session.add(PrintLogEntry(printer_id=printer.id, printer_name="Original Name", status="completed"))
    await db_session.commit()

    stats = (await async_client.get("/api/v1/archives/stats")).json()
    printers = (await async_client.get("/api/v1/printers/")).json()

    assert stats["printer_names"][str(printer.id)] == "Original Name"
    assert [p["name"] for p in printers if p["id"] == printer.id] == ["Renamed Later"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_failure_analysis_names_a_deleted_printer(async_client: AsyncClient, db_session, printer_factory):
    """Failures by printer reads the recorded name once the printer is gone."""
    live = await printer_factory(name="Still Here")
    db_session.add_all(
        [
            PrintLogEntry(printer_id=71, printer_name="Ultron", status="failed"),
            PrintLogEntry(printer_id=live.id, printer_name="Older Name", status="failed"),
        ]
    )
    await db_session.commit()

    analysis = (await async_client.get("/api/v1/archives/analysis/failures")).json()

    assert analysis["failures_by_printer"]["Ultron"] == 1
    assert analysis["failures_by_printer"]["Still Here"] == 1
    assert "Printer 71" not in analysis["failures_by_printer"]
