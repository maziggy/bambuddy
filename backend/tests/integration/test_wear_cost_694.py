"""Printer wear cost per printing hour (#694).

Each printer can carry an optional wear cost per printing hour. Every run's
wear is its logged duration at that rate, written to the print log entry; the
archive keeps the first run's wear, like energy (#1378). Totals that add up
filament and energy cost add wear too, and billing keeps charging filament only.
"""

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient

from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import PrintLogEntry
from backend.app.services.print_log import record_archive_wear, wear_cost_for_run, write_log_entry

START = datetime(2026, 10, 1, 8, 0, 0)


class TestWearCostForRun:
    def test_duration_at_the_hourly_rate(self):
        assert wear_cost_for_run(5400, 0.2) == 0.3

    @pytest.mark.parametrize("rate", [None, 0, 0.0, -1.0])
    def test_no_rate_means_no_wear(self, rate):
        assert wear_cost_for_run(3600, rate) is None

    @pytest.mark.parametrize("duration", [None, 0, -60])
    def test_no_measured_duration_means_no_wear(self, duration):
        # A reconciled run logs 0 seconds: its real end time is unknown.
        assert wear_cost_for_run(duration, 0.2) is None

    def test_a_value_that_is_not_a_number_is_ignored(self):
        assert wear_cost_for_run(3600, "0.2") is None


async def _archive(db_session, **kwargs) -> PrintArchive:
    archive = PrintArchive(
        filename="cube.3mf", file_path="test/cube.3mf", file_size=1000, print_name="Cube", status="completed", **kwargs
    )
    db_session.add(archive)
    await db_session.commit()
    await db_session.refresh(archive)
    return archive


@pytest.mark.asyncio
@pytest.mark.integration
class TestRecordingWear:
    async def test_log_entry_gets_the_runs_wear(self, db_session):
        entry = await write_log_entry(
            db_session,
            status="completed",
            started_at=START,
            completed_at=START + timedelta(hours=2),
            wear_cost_per_hour=0.25,
        )
        assert entry.wear_cost == 0.5

    async def test_reconciled_run_has_no_wear(self, db_session):
        entry = await write_log_entry(
            db_session,
            status="aborted",
            started_at=START,
            completed_at=START + timedelta(days=2),
            wear_cost_per_hour=0.25,
            reconciled=True,
        )
        assert entry.wear_cost is None

    async def test_a_failed_run_still_wears_the_printer(self, db_session):
        entry = await write_log_entry(
            db_session,
            status="failed",
            started_at=START,
            completed_at=START + timedelta(minutes=30),
            wear_cost_per_hour=1.0,
        )
        assert entry.wear_cost == 0.5

    async def test_first_run_sets_the_archives_wear(self, db_session):
        archive = await _archive(db_session)
        entry = await write_log_entry(
            db_session,
            archive_id=archive.id,
            status="completed",
            started_at=START,
            completed_at=START + timedelta(hours=1),
            wear_cost_per_hour=0.4,
        )
        await record_archive_wear(db_session, archive, entry)
        assert archive.wear_cost == 0.4

    async def test_a_reprint_leaves_the_archives_wear_alone(self, db_session):
        archive = await _archive(db_session, wear_cost=0.4)
        db_session.add(PrintLogEntry(archive_id=archive.id, status="completed", wear_cost=0.4))
        await db_session.commit()

        entry = await write_log_entry(
            db_session,
            archive_id=archive.id,
            status="completed",
            started_at=START,
            completed_at=START + timedelta(hours=3),
            wear_cost_per_hour=0.4,
        )
        await record_archive_wear(db_session, archive, entry)
        assert entry.wear_cost == 1.2
        assert archive.wear_cost == 0.4

    async def test_a_printer_without_a_rate_leaves_the_archive_empty(self, db_session):
        archive = await _archive(db_session)
        entry = await write_log_entry(
            db_session,
            archive_id=archive.id,
            status="completed",
            started_at=START,
            completed_at=START + timedelta(hours=1),
        )
        await record_archive_wear(db_session, archive, entry)
        assert archive.wear_cost is None


@pytest.mark.asyncio
@pytest.mark.integration
class TestPrinterSetting:
    async def test_set_read_and_clear(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory()

        response = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"wear_cost_per_hour": 0.35})
        assert response.status_code == 200
        assert response.json()["wear_cost_per_hour"] == 0.35

        listed = (await async_client.get("/api/v1/printers/")).json()
        assert next(p for p in listed if p["id"] == printer.id)["wear_cost_per_hour"] == 0.35

        response = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"wear_cost_per_hour": None})
        assert response.status_code == 200
        assert response.json()["wear_cost_per_hour"] is None

    async def test_negative_rate_is_rejected(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory()
        response = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"wear_cost_per_hour": -1})
        assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.integration
class TestTotals:
    async def test_stats_total(self, async_client: AsyncClient, db_session):
        db_session.add(PrintLogEntry(printer_id=1, status="completed", duration_seconds=3600, wear_cost=0.3))
        db_session.add(PrintLogEntry(printer_id=1, status="failed", duration_seconds=1800, wear_cost=0.15))
        db_session.add(PrintLogEntry(printer_id=1, status="completed", duration_seconds=3600))
        await db_session.commit()

        stats = (await async_client.get("/api/v1/archives/stats")).json()
        assert stats["total_wear_cost"] == pytest.approx(0.45)
        # Filament cost stays filament only.
        assert stats["total_cost"] == 0

    async def test_print_log_returns_and_sorts_by_wear(self, async_client: AsyncClient, db_session):
        db_session.add(PrintLogEntry(print_name="cheap", status="completed", wear_cost=0.1))
        db_session.add(PrintLogEntry(print_name="dear", status="completed", wear_cost=2.0))
        await db_session.commit()

        response = await async_client.get("/api/v1/print-log/", params={"sort_by": "wear_cost", "sort_dir": "desc"})
        assert response.status_code == 200
        items = response.json()["items"]
        assert [i["print_name"] for i in items[:2]] == ["dear", "cheap"]
        assert items[0]["wear_cost"] == 2.0

    async def test_archive_response_carries_wear(self, async_client: AsyncClient, db_session):
        archive = await _archive(db_session, wear_cost=0.75)
        response = await async_client.get(f"/api/v1/archives/{archive.id}")
        assert response.status_code == 200
        assert response.json()["wear_cost"] == 0.75

    async def test_project_totals_include_wear(self, async_client: AsyncClient, db_session):
        from backend.app.models.project import Project

        project = Project(name="Wear project")
        db_session.add(project)
        await db_session.commit()
        await db_session.refresh(project)

        archive = await _archive(db_session, project_id=project.id)
        db_session.add(PrintLogEntry(archive_id=archive.id, status="completed", cost=1.0, wear_cost=0.5))
        db_session.add(PrintLogEntry(archive_id=archive.id, status="completed", cost=1.0, wear_cost=0.25))
        await db_session.commit()

        stats = (await async_client.get(f"/api/v1/projects/{project.id}")).json()["stats"]
        assert stats["total_wear_cost"] == pytest.approx(0.75)
        assert stats["estimated_cost"] == pytest.approx(2.0)

    async def test_batch_cost_includes_wear(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        archive = await archive_factory(printer.id)
        order = (
            await async_client.post(
                "/api/v1/queue/batches",
                json={"name": "Order", "archive_id": archive.id, "plates": [{"plate_id": 1, "quantity_target": 2}]},
            )
        ).json()
        item = (
            await async_client.post(
                "/api/v1/queue/",
                json={"printer_id": printer.id, "archive_id": archive.id, "batch_id": order["id"], "plate_id": 1},
            )
        ).json()
        queued = await db_session.get(PrintQueueItem, item["id"])
        queued.status = "completed"
        db_session.add(
            PrintLogEntry(
                archive_id=archive.id,
                queue_item_id=item["id"],
                status="completed",
                cost=2.0,
                energy_cost=0.5,
                wear_cost=0.25,
            )
        )
        await db_session.commit()

        result = (await async_client.get(f"/api/v1/queue/batches/{order['id']}")).json()
        assert result["actual_cost"] == pytest.approx(2.75)
