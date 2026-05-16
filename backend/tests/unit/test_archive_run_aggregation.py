"""Tests for the PrintRun-based stats aggregation (#1378).

Statistics and per-archive aggregates now come from PrintLogEntry rows rather
than PrintArchive's runtime fields, so a reprint contributes new totals
instead of overwriting the source archive's first-run data.
"""

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient

from backend.app.models.print_log import PrintLogEntry


@pytest.mark.asyncio
@pytest.mark.integration
async def test_stats_count_reprints_independently(
    async_client: AsyncClient, archive_factory, printer_factory, db_session
):
    """A reprint adds to stats instead of overwriting the source archive."""
    printer = await printer_factory()
    archive = await archive_factory(
        printer.id,
        status="completed",
        filament_used_grams=100.0,
        cost=2.5,
        print_time_seconds=3600,
        with_run=False,
    )

    # First run — completed, 100g.
    db_session.add(
        PrintLogEntry(
            archive_id=archive.id,
            printer_id=archive.printer_id,
            status="completed",
            started_at=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 5, 1, 11, 0, tzinfo=timezone.utc),
            duration_seconds=3600,
            filament_used_grams=100.0,
            cost=2.5,
            created_at=datetime(2026, 5, 1, 11, 0, tzinfo=timezone.utc),
        )
    )
    # Reprint — failed at 10g.
    db_session.add(
        PrintLogEntry(
            archive_id=archive.id,
            printer_id=archive.printer_id,
            status="failed",
            started_at=datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 5, 5, 10, 5, tzinfo=timezone.utc),
            duration_seconds=300,
            filament_used_grams=10.0,
            cost=0.25,
            failure_reason="Cancelled by user",
            created_at=datetime(2026, 5, 5, 10, 5, tzinfo=timezone.utc),
        )
    )
    await db_session.commit()

    response = await async_client.get("/api/v1/archives/stats")
    assert response.status_code == 200
    body = response.json()

    # Both runs counted, not the single archive row.
    assert body["total_prints"] == 2
    assert body["successful_prints"] == 1
    assert body["failed_prints"] == 1

    # 100g + 10g — NOT 10g (which is what archives.filament_used_grams alone
    # would give if the archive's runtime fields were the source of truth).
    assert body["total_filament_grams"] == pytest.approx(110.0)
    assert body["total_cost"] == pytest.approx(2.75)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_archive_list_includes_run_aggregates(
    async_client: AsyncClient, archive_factory, printer_factory, db_session
):
    """List response carries run_count, last_run_at, total_filament_actual_grams."""
    printer = await printer_factory()
    archive = await archive_factory(
        printer.id,
        status="completed",
        filament_used_grams=100.0,
        with_run=False,
    )
    db_session.add_all(
        [
            PrintLogEntry(
                archive_id=archive.id,
                printer_id=archive.printer_id,
                status="completed",
                started_at=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
                completed_at=datetime(2026, 5, 1, 11, 0, tzinfo=timezone.utc),
                filament_used_grams=100.0,
                created_at=datetime(2026, 5, 1, 11, 0, tzinfo=timezone.utc),
            ),
            PrintLogEntry(
                archive_id=archive.id,
                printer_id=archive.printer_id,
                status="failed",
                started_at=datetime(2026, 5, 10, 10, 0, tzinfo=timezone.utc),
                completed_at=datetime(2026, 5, 10, 10, 5, tzinfo=timezone.utc),
                filament_used_grams=10.0,
                created_at=datetime(2026, 5, 10, 10, 5, tzinfo=timezone.utc),
            ),
        ]
    )
    await db_session.commit()

    response = await async_client.get("/api/v1/archives/")
    assert response.status_code == 200
    rows = response.json()
    row = next(r for r in rows if r["id"] == archive.id)

    assert row["run_count"] == 2
    assert row["successful_run_count"] == 1
    assert row["failed_run_count"] == 1
    assert row["total_filament_actual_grams"] == pytest.approx(110.0)
    assert row["last_run_at"] is not None  # max(started_at) populated


@pytest.mark.asyncio
@pytest.mark.integration
async def test_runs_endpoint_returns_runs_newest_first(
    async_client: AsyncClient, archive_factory, printer_factory, db_session
):
    """GET /archives/{id}/runs returns each PrintLogEntry for the archive."""
    printer = await printer_factory()
    archive = await archive_factory(
        printer.id,
        status="completed",
        with_run=False,
    )
    db_session.add_all(
        [
            PrintLogEntry(
                archive_id=archive.id,
                printer_id=archive.printer_id,
                status="completed",
                started_at=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
                completed_at=datetime(2026, 4, 1, 11, 0, tzinfo=timezone.utc),
                filament_used_grams=50.0,
            ),
            PrintLogEntry(
                archive_id=archive.id,
                printer_id=archive.printer_id,
                status="failed",
                started_at=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
                completed_at=datetime(2026, 5, 1, 10, 5, tzinfo=timezone.utc),
                filament_used_grams=5.0,
                failure_reason="Cancelled by user",
            ),
        ]
    )
    await db_session.commit()

    response = await async_client.get(f"/api/v1/archives/{archive.id}/runs")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    # Newest first
    assert body["items"][0]["status"] == "failed"
    assert body["items"][0]["failure_reason"] == "Cancelled by user"
    assert body["items"][1]["status"] == "completed"
    assert body["items"][1]["filament_used_grams"] == pytest.approx(50.0)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_purge_stats_also_deletes_linked_runs(
    async_client: AsyncClient, archive_factory, printer_factory, db_session
):
    """``DELETE /archives/{id}?purge_stats=true`` hard-deletes linked PrintLogEntry
    rows so their filament / cost / count contributions truly leave Quick Stats.
    Without this, ON DELETE SET NULL on the FK would orphan the runs and they'd
    keep showing up in the new aggregate-from-PrintLogEntry totals (#1378)."""
    from sqlalchemy import func, select

    printer = await printer_factory()
    keep = await archive_factory(printer.id, status="completed", filament_used_grams=50.0)
    purge = await archive_factory(printer.id, status="completed", filament_used_grams=100.0)

    # Extra runs on the archive about to be purged, to prove they all go.
    db_session.add_all(
        [
            PrintLogEntry(
                archive_id=purge.id,
                printer_id=purge.printer_id,
                status="failed",
                filament_used_grams=10.0,
            ),
            PrintLogEntry(
                archive_id=purge.id,
                printer_id=purge.printer_id,
                status="completed",
                filament_used_grams=100.0,
            ),
        ]
    )
    await db_session.commit()

    resp = await async_client.delete(f"/api/v1/archives/{purge.id}?purge_stats=true")
    assert resp.status_code == 200
    assert resp.json()["purged_from_stats"] is True

    remaining = await db_session.execute(
        select(func.count(PrintLogEntry.id)).where(PrintLogEntry.archive_id == purge.id)
    )
    assert remaining.scalar() == 0

    # The OTHER archive's auto-synthesized run is still there.
    keep_remaining = await db_session.execute(
        select(func.count(PrintLogEntry.id)).where(PrintLogEntry.archive_id == keep.id)
    )
    assert keep_remaining.scalar() == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_soft_delete_keeps_runs_for_stats(
    async_client: AsyncClient, archive_factory, printer_factory, db_session
):
    """Default soft-delete (without ``purge_stats=true``) keeps the archive's
    PrintLogEntry rows so the #1343 stats-preservation contract still holds —
    the archive disappears from listings, but its filament / time / cost stay
    in Quick Stats."""
    from sqlalchemy import func, select

    printer = await printer_factory()
    archive = await archive_factory(printer.id, status="completed", filament_used_grams=75.0)

    resp = await async_client.delete(f"/api/v1/archives/{archive.id}")
    assert resp.status_code == 200
    assert resp.json()["purged_from_stats"] is False

    # The run row is still there for stats aggregation.
    runs = await db_session.execute(select(func.count(PrintLogEntry.id)).where(PrintLogEntry.archive_id == archive.id))
    assert runs.scalar() == 1

    stats = (await async_client.get("/api/v1/archives/stats")).json()
    assert stats["total_prints"] >= 1
    assert stats["total_filament_grams"] >= 75.0
