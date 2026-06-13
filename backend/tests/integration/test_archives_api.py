"""Integration tests for Archives API endpoints.

Tests the full request/response cycle for /api/v1/archives/ endpoints.
"""

from pathlib import Path

import pytest
from httpx import AsyncClient


class TestArchivesAPI:
    """Integration tests for /api/v1/archives/ endpoints."""

    # ========================================================================
    # List endpoints
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_archives_empty(self, async_client: AsyncClient):
        """Verify empty list is returned when no archives exist."""
        response = await async_client.get("/api/v1/archives/")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_archives_with_data(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify list returns existing archives."""
        printer = await printer_factory()
        await archive_factory(printer.id, print_name="Test Archive")

        response = await async_client.get("/api/v1/archives/")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert any(a["print_name"] == "Test Archive" for a in data)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_archives_pagination(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify pagination works correctly."""
        printer = await printer_factory()
        # Create 5 archives
        for i in range(5):
            await archive_factory(printer.id, print_name=f"Archive {i}")

        # Get first page with limit 2
        response = await async_client.get("/api/v1/archives/?limit=2&offset=0")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 2

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_archives_filter_by_printer(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify filtering by printer_id works."""
        printer1 = await printer_factory(name="Printer 1", serial_number="00M09A000000001")
        printer2 = await printer_factory(name="Printer 2", serial_number="00M09A000000002")
        await archive_factory(printer1.id, print_name="Printer 1 Archive")
        await archive_factory(printer2.id, print_name="Printer 2 Archive")

        response = await async_client.get(f"/api/v1/archives/?printer_id={printer1.id}")

        assert response.status_code == 200
        data = response.json()
        assert all(a["printer_id"] == printer1.id for a in data)

    # ========================================================================
    # Get single endpoint
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_archive(self, async_client: AsyncClient, archive_factory, printer_factory, db_session):
        """Verify single archive can be retrieved."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, print_name="Get Test Archive")

        response = await async_client.get(f"/api/v1/archives/{archive.id}")

        assert response.status_code == 200
        result = response.json()
        assert result["id"] == archive.id
        assert result["print_name"] == "Get Test Archive"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_archive_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent archive."""
        response = await async_client.get("/api/v1/archives/9999")

        assert response.status_code == 404

    # ========================================================================
    # Update endpoints
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_archive_name(self, async_client: AsyncClient, archive_factory, printer_factory, db_session):
        """Verify archive name can be updated."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, print_name="Original Name")

        response = await async_client.patch(f"/api/v1/archives/{archive.id}", json={"print_name": "Updated Name"})

        assert response.status_code == 200
        assert response.json()["print_name"] == "Updated Name"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_archive_notes(self, async_client: AsyncClient, archive_factory, printer_factory, db_session):
        """Verify archive notes can be updated."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id)

        response = await async_client.patch(f"/api/v1/archives/{archive.id}", json={"notes": "Great print!"})

        assert response.status_code == 200
        assert response.json()["notes"] == "Great print!"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_archive_favorite(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify archive favorite status can be updated."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id)

        response = await async_client.patch(f"/api/v1/archives/{archive.id}", json={"is_favorite": True})

        assert response.status_code == 200
        assert response.json()["is_favorite"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_archive_external_url(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify archive external_url can be updated."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id)

        response = await async_client.patch(
            f"/api/v1/archives/{archive.id}", json={"external_url": "https://printables.com/model/12345"}
        )

        assert response.status_code == 200
        assert response.json()["external_url"] == "https://printables.com/model/12345"

        # Verify it can be cleared
        response = await async_client.patch(f"/api/v1/archives/{archive.id}", json={"external_url": None})

        assert response.status_code == 200
        assert response.json()["external_url"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_archive_failure_reason_mirrors_to_print_log_entry(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """#1444: PATCH /archives/{id} with failure_reason must mirror to the
        latest PrintLogEntry so the Stats page Failure Analysis widget
        (which reads PrintLogEntry.failure_reason) reflects the user's
        reclassification instead of showing "Unknown" forever.
        """
        from sqlalchemy import select

        from backend.app.models.print_log import PrintLogEntry

        printer = await printer_factory()
        # archive_factory auto-creates a matching PrintLogEntry (failure_reason
        # carried from the archive, which is NULL here — same shape as the bug
        # repro: print completed → log entry written with NULL → user goes to
        # classify the failure afterwards).
        archive = await archive_factory(printer.id, print_name="Failed Print", status="failed", run_status="failed")

        response = await async_client.patch(
            f"/api/v1/archives/{archive.id}",
            json={"failure_reason": "Adhesion failure"},
        )
        assert response.status_code == 200

        result = await db_session.execute(select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id))
        mirrored = result.scalar_one()
        assert mirrored.failure_reason == "Adhesion failure"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_archive_status_mirrors_to_print_log_entry(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """#1444: PATCH /archives/{id} with status must mirror to the latest
        PrintLogEntry so stats that filter on PrintLogEntry.status see the
        user's reclassification.
        """
        from sqlalchemy import select

        from backend.app.models.print_log import PrintLogEntry

        printer = await printer_factory()
        archive = await archive_factory(printer.id, run_status="completed")

        response = await async_client.patch(
            f"/api/v1/archives/{archive.id}",
            json={"status": "failed"},
        )
        assert response.status_code == 200

        result = await db_session.execute(select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id))
        mirrored = result.scalar_one()
        assert mirrored.status == "failed"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_archive_failure_reason_only_touches_latest_entry(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """#1444: For an archive with multiple runs (reprints), only the
        latest PrintLogEntry should receive the reclassification. Earlier
        runs were classified at their own time and must not be retroactively
        overwritten.
        """
        from backend.app.models.print_log import PrintLogEntry

        printer = await printer_factory()
        # First run — created by the factory's auto-run with its own reason.
        archive = await archive_factory(printer.id, status="failed", run_status="failed")
        from sqlalchemy import select

        first_run = (
            await db_session.execute(select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id))
        ).scalar_one()
        first_run.failure_reason = "Filament tangle"
        await db_session.commit()

        # Second run — the reprint that just finished with NULL classification.
        latest_run = PrintLogEntry(archive_id=archive.id, status="failed", failure_reason=None)
        db_session.add(latest_run)
        await db_session.commit()

        response = await async_client.patch(
            f"/api/v1/archives/{archive.id}",
            json={"failure_reason": "Adhesion failure"},
        )
        assert response.status_code == 200

        await db_session.refresh(first_run)
        await db_session.refresh(latest_run)
        assert first_run.failure_reason == "Filament tangle"
        assert latest_run.failure_reason == "Adhesion failure"

    # ========================================================================
    # Delete endpoints
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_archive(self, async_client: AsyncClient, archive_factory, printer_factory, db_session):
        """Verify archive can be deleted."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id)
        archive_id = archive.id

        response = await async_client.delete(f"/api/v1/archives/{archive_id}")

        assert response.status_code == 200

        # Verify deleted
        response = await async_client.get(f"/api/v1/archives/{archive_id}")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_nonexistent_archive(self, async_client: AsyncClient):
        """Verify deleting non-existent archive returns 404."""
        response = await async_client.delete("/api/v1/archives/9999")

        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_archive_blocked_when_related_queue_item_printing(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """#1734: archive delete must 409 when a related queue item is currently
        mid-print — deleting the archive would strip the dispatcher's metadata
        trail (filament / plate / ams_mapping) out from under the running print.
        Both soft and hard delete are gated by the same precondition.
        """
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        archive = await archive_factory(printer.id)
        db_session.add(PrintQueueItem(printer_id=printer.id, archive_id=archive.id, status="printing", position=1))
        await db_session.commit()

        soft = await async_client.delete(f"/api/v1/archives/{archive.id}")
        assert soft.status_code == 409
        assert "printing" in soft.json()["detail"].lower()

        hard = await async_client.delete(f"/api/v1/archives/{archive.id}?purge_stats=true")
        assert hard.status_code == 409

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_delete_impact_reports_counts(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """#1734: the delete-impact pre-flight endpoint reports the total
        number of related queue items AND how many are currently printing,
        so the frontend can both warn the user before they confirm AND
        disable the confirm button when the printing count is non-zero.
        """
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        archive = await archive_factory(printer.id)
        # Build a mixed-status set the way a Send All upload + later in-flight
        # dispatch looks at the wire (#1733).
        db_session.add_all(
            [
                PrintQueueItem(printer_id=printer.id, archive_id=archive.id, status="pending", position=1),
                PrintQueueItem(printer_id=printer.id, archive_id=archive.id, status="pending", position=2),
                PrintQueueItem(printer_id=printer.id, archive_id=archive.id, status="printing", position=3),
            ]
        )
        # An unrelated archive's queue rows must not bleed into the count.
        other = await archive_factory(printer.id)
        db_session.add(PrintQueueItem(printer_id=printer.id, archive_id=other.id, status="pending", position=4))
        await db_session.commit()

        resp = await async_client.get(f"/api/v1/archives/{archive.id}/delete-impact")
        assert resp.status_code == 200
        body = resp.json()
        assert body["related_queue_items"] == 3
        assert body["currently_printing"] == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_soft_delete_preserves_stats_contribution(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """#1343: deleting an archive without ``purge_stats`` keeps its
        contribution in Quick Stats. The row vanishes from listings but the
        filament / time / cost totals stay intact.
        """
        printer = await printer_factory()
        await archive_factory(
            printer.id,
            status="completed",
            print_time_seconds=3600,
            filament_used_grams=50.0,
            cost=1.50,
        )
        archive_to_delete = await archive_factory(
            printer.id,
            status="completed",
            print_time_seconds=7200,
            filament_used_grams=100.0,
            cost=3.00,
        )

        # Pre-delete: stats include both archives.
        pre = (await async_client.get("/api/v1/archives/stats")).json()
        assert pre["total_prints"] == 2
        assert pre["total_filament_grams"] == 150.0
        assert pre["total_cost"] == 4.50

        # Soft delete (default — no purge_stats param).
        resp = await async_client.delete(f"/api/v1/archives/{archive_to_delete.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["purged_from_stats"] is False

        # Listing hides the deleted archive…
        listing = (await async_client.get("/api/v1/archives/")).json()
        assert all(a["id"] != archive_to_delete.id for a in listing)

        # …but stats still reflect both prints (the whole point of #1343).
        post = (await async_client.get("/api/v1/archives/stats")).json()
        assert post["total_prints"] == 2
        assert post["total_filament_grams"] == 150.0
        assert post["total_cost"] == 4.50

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_soft_delete_clears_thumbnail_path_on_linked_log_entries(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """#1348 follow-up: soft-deleting an archive removes its files from disk;
        the cached thumbnail_path on linked PrintLogEntry rows must be NULLed
        in the same transaction so the print-log view doesn't 404-storm on the
        now-deleted thumbnail file."""
        from sqlalchemy import select

        from backend.app.models.print_log import PrintLogEntry

        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            status="completed",
            thumbnail_path="archives/test/test_print/thumbnail.png",
        )
        # The factory's auto-PrintLogEntry doesn't copy thumbnail_path; set it
        # manually to mirror what the production write_log_entry path stores.
        run_query = await db_session.execute(select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id))
        run = run_query.scalar_one()
        run.thumbnail_path = "archives/test/test_print/thumbnail.png"
        await db_session.commit()
        assert run.thumbnail_path is not None

        resp = await async_client.delete(f"/api/v1/archives/{archive.id}")
        assert resp.status_code == 200
        assert resp.json()["purged_from_stats"] is False

        await db_session.refresh(run)
        assert run.thumbnail_path is None, "soft-delete must NULL thumbnail_path on linked log entry"
        # The log entry itself survives the soft delete (its filament/cost
        # contribution still needs to flow into stats per #1343).
        assert run.id is not None
        assert run.archive_id == archive.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_hard_delete_clears_thumbnail_path_before_fk_cascade(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """#1348 follow-up: the auto-purge sweeper (and any caller of
        ArchiveService.delete_archive) hard-deletes the archive row but leaves
        PrintLogEntry rows alive via ON DELETE SET NULL. The eager
        thumbnail_path clear must run inside delete_archive so even orphaned
        log entries don't surface stale paths."""
        from sqlalchemy import select

        from backend.app.models.print_log import PrintLogEntry
        from backend.app.services.archive import ArchiveService

        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            status="completed",
            thumbnail_path="archives/test/test_print/thumbnail.png",
        )
        run_query = await db_session.execute(select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id))
        run = run_query.scalar_one()
        run.thumbnail_path = "archives/test/test_print/thumbnail.png"
        await db_session.commit()
        run_id = run.id

        service = ArchiveService(db_session)
        assert await service.delete_archive(archive.id) is True

        # Log entry survives the hard-delete (the FK is ON DELETE SET NULL
        # in production; SQLite test config doesn't enable foreign_keys=ON
        # by default so archive_id may still be set, but the row itself
        # remains for audit). The thumbnail_path was cleared eagerly by
        # _null_print_log_thumbnail_paths before db.delete(archive).
        refetch = await db_session.execute(select(PrintLogEntry).where(PrintLogEntry.id == run_id))
        survivor = refetch.scalar_one()
        assert survivor.thumbnail_path is None, (
            "delete_archive must NULL thumbnail_path before removing the archive row"
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_print_log_thumbnail_route_lazy_nulls_missing_file(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """#1348 follow-up: GET /print-log/{id}/thumbnail self-heals when the
        thumbnail_path on a log entry points at a missing file (failed print
        whose thumbnail was never written, or a stale path that escaped the
        delete-time cleanup)."""
        from sqlalchemy import select

        from backend.app.models.print_log import PrintLogEntry

        printer = await printer_factory()
        archive = await archive_factory(printer.id, status="failed")
        run_query = await db_session.execute(select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id))
        run = run_query.scalar_one()
        # Path points at a file that never existed (failed-print case where
        # archive.thumbnail_path was set but the extractor never produced one).
        run.thumbnail_path = "archives/missing/never_written/thumbnail.png"
        await db_session.commit()

        # Auth is disabled in the integration test config, so the stream-token
        # guard is bypassed — the route runs the lazy-NULL branch directly.
        resp = await async_client.get(f"/api/v1/print-log/{run.id}/thumbnail")
        assert resp.status_code == 404

        await db_session.refresh(run)
        assert run.thumbnail_path is None, "missing file must self-heal to NULL"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_purge_stats_drops_archive_from_quick_stats(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """#1343: deleting with ``?purge_stats=true`` hard-deletes the row,
        dropping its contribution from Quick Stats (the original behaviour,
        now opt-in)."""
        printer = await printer_factory()
        keep = await archive_factory(printer.id, status="completed", filament_used_grams=50.0)
        purge = await archive_factory(printer.id, status="completed", filament_used_grams=100.0)

        resp = await async_client.delete(f"/api/v1/archives/{purge.id}?purge_stats=true")
        assert resp.status_code == 200
        assert resp.json()["purged_from_stats"] is True

        stats = (await async_client.get("/api/v1/archives/stats")).json()
        assert stats["total_prints"] == 1
        assert stats["total_filament_grams"] == 50.0

        # The kept archive is still listed.
        listing = (await async_client.get("/api/v1/archives/")).json()
        assert [a["id"] for a in listing] == [keep.id]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_soft_deleted_archive_404_on_detail(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """A soft-deleted archive must 404 on GET — a stale bookmark or
        direct URL should not expose a row the user has already removed."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id)
        await async_client.delete(f"/api/v1/archives/{archive.id}")
        resp = await async_client.get(f"/api/v1/archives/{archive.id}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_soft_deleted_archive_hidden_from_search(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Search must skip soft-deleted archives. Uses the LIKE fallback by
        querying a single-character pattern that the SQLite FTS5 rejects, so
        the test covers the fallback path that the production FTS path also
        respects."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, print_name="UniqueSoftDeleteCandidate")
        await async_client.delete(f"/api/v1/archives/{archive.id}")
        resp = await async_client.get("/api/v1/archives/search?q=UniqueSoftDeleteCandidate")
        assert resp.status_code == 200
        assert resp.json() == []

    # ========================================================================
    # Statistics endpoints
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_archive_stats(self, async_client: AsyncClient, archive_factory, printer_factory, db_session):
        """Verify archive statistics can be retrieved."""
        printer = await printer_factory()
        await archive_factory(
            printer.id,
            status="completed",
            print_time_seconds=3600,
            filament_used_grams=50.0,
        )
        await archive_factory(
            printer.id,
            status="completed",
            print_time_seconds=7200,
            filament_used_grams=100.0,
        )

        response = await async_client.get("/api/v1/archives/stats")

        assert response.status_code == 200
        result = response.json()
        # Check for actual stats fields
        assert "total_prints" in result
        assert "successful_prints" in result


class TestNo3MFWarning:
    """`GET /archives/no-3mf-warning` — install step 4 reactive nudge.

    The connection diagnostic's external_storage check only catches the
    printer-side variant of the setting (newer firmware). For older slicers
    where the toggle lives only in BambuStudio, the printer never reports
    it. The fallback path in main.py creates the archive with
    extra_data.no_3mf_available=True; this endpoint exposes that as a
    boolean so the frontend can surface a one-time banner.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_true_when_recent_fallback_exists(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        printer = await printer_factory()
        await archive_factory(printer.id, extra_data={"no_3mf_available": True})

        response = await async_client.get("/api/v1/archives/no-3mf-warning")

        assert response.status_code == 200
        assert response.json() == {"has_fallback": True}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_false_when_no_archives(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/archives/no-3mf-warning")

        assert response.status_code == 200
        assert response.json() == {"has_fallback": False}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_false_when_only_normal_archives(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        printer = await printer_factory()
        # extra_data has other keys but no_3mf_available is absent — normal
        # archives must not trigger the nudge.
        await archive_factory(printer.id, extra_data={"makerworld_url": "https://example"})
        await archive_factory(printer.id, extra_data=None)

        response = await async_client.get("/api/v1/archives/no-3mf-warning")

        assert response.status_code == 200
        assert response.json() == {"has_fallback": False}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ignores_archives_older_than_30_days(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        from datetime import datetime, timedelta, timezone

        from backend.app.models.archive import PrintArchive

        printer = await printer_factory()
        archive = await archive_factory(printer.id, extra_data={"no_3mf_available": True})
        # Backdate past the 30-day window — old fallbacks are forgiven.
        archive.created_at = datetime.now(timezone.utc) - timedelta(days=45)
        await db_session.commit()

        response = await async_client.get("/api/v1/archives/no-3mf-warning")

        assert response.status_code == 200
        assert response.json() == {"has_fallback": False}
        # Sanity: row really is in the DB, we just don't surface it.
        assert (await db_session.get(PrintArchive, archive.id)) is not None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ignores_soft_deleted_fallbacks(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        from datetime import datetime, timezone

        printer = await printer_factory()
        archive = await archive_factory(printer.id, extra_data={"no_3mf_available": True})
        archive.deleted_at = datetime.now(timezone.utc)
        await db_session.commit()

        response = await async_client.get("/api/v1/archives/no-3mf-warning")

        assert response.status_code == 200
        # Soft-deleted fallbacks have been actioned (user clearing the
        # evidence). Stop nudging.
        assert response.json() == {"has_fallback": False}


class TestPrintLogEntryDelete:
    """#1687: per-row delete on the Print Log page.

    Pin the route's three contracts: (1) deleting a row drops its filament
    / cost / count contribution from /archives/stats in the same response
    cycle; (2) the matching archive (if any) is untouched; (3) missing IDs
    return 404 rather than 200-silently.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_print_log_entry_drops_from_stats(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        from sqlalchemy import select

        from backend.app.models.print_log import PrintLogEntry

        printer = await printer_factory()
        keep = await archive_factory(printer.id, status="completed", filament_used_grams=50.0)
        drop = await archive_factory(printer.id, status="completed", filament_used_grams=125.0)

        drop_run = (
            await db_session.execute(select(PrintLogEntry).where(PrintLogEntry.archive_id == drop.id))
        ).scalar_one()

        resp = await async_client.delete(f"/api/v1/print-log/{drop_run.id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        assert resp.json()["id"] == drop_run.id

        # The linked archive survives — the row was a stats row, not the archive.
        listing = (await async_client.get("/api/v1/archives/")).json()
        assert {a["id"] for a in listing} == {keep.id, drop.id}

        # /stats no longer counts the dropped run's filament contribution.
        stats = (await async_client.get("/api/v1/archives/stats")).json()
        assert stats["total_prints"] == 1
        assert stats["total_filament_grams"] == 50.0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_print_log_entry_404_when_missing(self, async_client: AsyncClient):
        resp = await async_client.delete("/api/v1/print-log/999999")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_print_log_entry_does_not_clear_others(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Deleting one row must not touch siblings — guard against an accidental
        ``delete(PrintLogEntry)`` without a ``where`` clause (cf. clear_print_log
        which intentionally drops everything)."""
        from sqlalchemy import select

        from backend.app.models.print_log import PrintLogEntry

        printer = await printer_factory()
        a = await archive_factory(printer.id, status="completed", filament_used_grams=10.0)
        b = await archive_factory(printer.id, status="completed", filament_used_grams=20.0)
        c = await archive_factory(printer.id, status="completed", filament_used_grams=30.0)

        runs = {r.archive_id: r for r in (await db_session.execute(select(PrintLogEntry))).scalars().all()}

        resp = await async_client.delete(f"/api/v1/print-log/{runs[b.id].id}")
        assert resp.status_code == 200

        survivors = (await db_session.execute(select(PrintLogEntry.archive_id))).scalars().all()
        assert set(survivors) == {a.id, c.id}


class TestPrintLogEntryUpdate:
    """Tests for ``PATCH /print-log/{entry_id}`` (#1687 part 4).

    Pin the route's contracts: (1) GET serialiser actually surfaces
    ``failure_reason`` (previously it was silently dropped from the response
    even when set in the DB); (2) PATCH persists ``failure_reason`` and
    ``status``; (3) unknown vocabulary returns 400 rather than getting stored
    as raw garbage; (4) missing IDs return 404.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_surfaces_failure_reason(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Pre-fix the GET endpoint built PrintLogEntrySchema without
        ``failure_reason`` even though the column was populated, so the Print
        Log table couldn't render what the Failure Analysis widget already
        groups by. Regression guard for the silent-drop bug.
        """
        from sqlalchemy import select

        from backend.app.models.print_log import PrintLogEntry

        printer = await printer_factory()
        archive = await archive_factory(printer.id, status="failed")
        entry = (
            await db_session.execute(select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id))
        ).scalar_one()
        entry.failure_reason = "spaghettiDetached"
        await db_session.commit()

        body = (await async_client.get("/api/v1/print-log/")).json()
        match = next(item for item in body["items"] if item["id"] == entry.id)
        assert match["failure_reason"] == "spaghettiDetached"
        # archive_id should also flow through so the frontend can tell orphan
        # entries apart from archive-linked ones.
        assert match["archive_id"] == archive.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_sets_failure_reason(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        from sqlalchemy import select

        from backend.app.models.print_log import PrintLogEntry

        printer = await printer_factory()
        archive = await archive_factory(printer.id, status="failed")
        entry = (
            await db_session.execute(select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id))
        ).scalar_one()
        assert entry.failure_reason is None

        resp = await async_client.patch(
            f"/api/v1/print-log/{entry.id}",
            json={"failure_reason": "cloggedNozzle"},
        )
        assert resp.status_code == 200
        assert resp.json()["failure_reason"] == "cloggedNozzle"

        await db_session.refresh(entry)
        assert entry.failure_reason == "cloggedNozzle"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_can_clear_failure_reason(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Empty-string failure_reason stores back as NULL (the column's
        nullable=True intent is preserved end-to-end)."""
        from sqlalchemy import select

        from backend.app.models.print_log import PrintLogEntry

        printer = await printer_factory()
        archive = await archive_factory(printer.id, status="failed")
        entry = (
            await db_session.execute(select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id))
        ).scalar_one()
        entry.failure_reason = "warping"
        await db_session.commit()

        resp = await async_client.patch(
            f"/api/v1/print-log/{entry.id}",
            json={"failure_reason": ""},
        )
        assert resp.status_code == 200
        assert resp.json()["failure_reason"] is None

        await db_session.refresh(entry)
        assert entry.failure_reason is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_rejects_unknown_failure_reason(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Unknown values must 400 — otherwise the UI would render raw garbage
        because the i18n layer maps the value back through the canonical
        vocabulary."""
        from sqlalchemy import select

        from backend.app.models.print_log import PrintLogEntry

        printer = await printer_factory()
        archive = await archive_factory(printer.id, status="failed")
        entry = (
            await db_session.execute(select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id))
        ).scalar_one()

        resp = await async_client.patch(
            f"/api/v1/print-log/{entry.id}",
            json={"failure_reason": "completely-made-up"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_updates_status(self, async_client: AsyncClient, archive_factory, printer_factory, db_session):
        from sqlalchemy import select

        from backend.app.models.print_log import PrintLogEntry

        printer = await printer_factory()
        archive = await archive_factory(printer.id, status="completed")
        entry = (
            await db_session.execute(select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id))
        ).scalar_one()
        entry.status = "completed"
        await db_session.commit()

        resp = await async_client.patch(
            f"/api/v1/print-log/{entry.id}",
            json={"status": "failed", "failure_reason": "layerShift"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"
        assert resp.json()["failure_reason"] == "layerShift"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_rejects_unknown_status(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        from sqlalchemy import select

        from backend.app.models.print_log import PrintLogEntry

        printer = await printer_factory()
        archive = await archive_factory(printer.id, status="failed")
        entry = (
            await db_session.execute(select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id))
        ).scalar_one()

        resp = await async_client.patch(
            f"/api/v1/print-log/{entry.id}",
            json={"status": "bogus-status"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_404_when_missing(self, async_client: AsyncClient):
        resp = await async_client.patch(
            "/api/v1/print-log/999999",
            json={"failure_reason": "cloggedNozzle"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_works_on_orphan_entry(self, async_client: AsyncClient, printer_factory, db_session):
        """Orphan log entries (no archive_id) are the actual reason this
        endpoint exists — the Archive Edit modal can't reach them. Make sure
        the PATCH works for those rows specifically."""
        from backend.app.models.print_log import PrintLogEntry

        printer = await printer_factory()
        orphan = PrintLogEntry(
            archive_id=None,
            print_name="failed-before-archive-created",
            printer_id=printer.id,
            status="failed",
            failure_reason=None,
        )
        db_session.add(orphan)
        await db_session.commit()
        await db_session.refresh(orphan)
        assert orphan.archive_id is None

        resp = await async_client.patch(
            f"/api/v1/print-log/{orphan.id}",
            json={"failure_reason": "powerFailure"},
        )
        assert resp.status_code == 200
        assert resp.json()["failure_reason"] == "powerFailure"
        assert resp.json()["archive_id"] is None


class TestArchivesSlimAPI:
    """Integration tests for /api/v1/archives/slim endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_slim_empty(self, async_client: AsyncClient):
        """Verify empty list when no archives exist."""
        response = await async_client.get("/api/v1/archives/slim")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_slim_returns_only_expected_fields(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify response contains only slim fields, not full archive data."""
        printer = await printer_factory()
        await archive_factory(
            printer.id,
            print_name="Slim Test",
            status="completed",
            filament_type="PLA",
            filament_color="#FF0000",
            filament_used_grams=50.0,
            print_time_seconds=3600,
            cost=1.50,
            quantity=2,
        )

        response = await async_client.get("/api/v1/archives/slim")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        item = data[0]

        # Expected fields present
        assert item["printer_id"] == printer.id
        assert item["print_name"] == "Slim Test"
        assert item["status"] == "completed"
        assert item["filament_type"] == "PLA"
        assert item["filament_color"] == "#FF0000"
        assert item["filament_used_grams"] == 50.0
        assert item["print_time_seconds"] == 3600
        assert item["cost"] == 1.50
        # quantity is per-event semantics now (each PrintLogEntry = one run);
        # the archive's quantity field is no longer surfaced through this
        # endpoint after the #1390 per-event migration.
        assert item["quantity"] == 1
        assert "created_at" in item

        # Full archive fields must NOT be present
        assert "id" not in item
        assert "filename" not in item
        assert "file_path" not in item
        assert "file_size" not in item
        assert "extra_data" not in item
        assert "notes" not in item
        assert "tags" not in item
        assert "photos" not in item
        assert "thumbnail_path" not in item
        assert "content_hash" not in item
        assert "duplicates" not in item
        assert "duplicate_count" not in item

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_slim_computes_actual_time(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify actual_time_seconds is computed from started_at/completed_at."""
        from datetime import datetime, timezone

        printer = await printer_factory()
        started = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        completed = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)  # 2 hours = 7200s
        await archive_factory(
            printer.id,
            status="completed",
            started_at=started,
            completed_at=completed,
        )

        response = await async_client.get("/api/v1/archives/slim")

        assert response.status_code == 200
        item = response.json()[0]
        assert item["actual_time_seconds"] == 7200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_slim_actual_time_for_failed_includes_elapsed(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Failed prints report measured elapsed time so Printer Stats By Time
        matches Quick Stats Print Time (#1390). Previously this returned null
        and the frontend fell back to the slicer estimate, double-counting the
        unfinished portion of the print."""
        from datetime import datetime, timezone

        printer = await printer_factory()
        await archive_factory(
            printer.id,
            status="failed",
            started_at=datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            completed_at=datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
        )

        response = await async_client.get("/api/v1/archives/slim")

        assert response.status_code == 200
        item = response.json()[0]
        assert item["actual_time_seconds"] == 3600

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_slim_date_filtering(self, async_client: AsyncClient, archive_factory, printer_factory, db_session):
        """Verify date_from and date_to filters work."""
        from datetime import datetime, timezone

        printer = await printer_factory()
        await archive_factory(
            printer.id,
            print_name="Old Print",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        await archive_factory(
            printer.id,
            print_name="New Print",
            created_at=datetime(2024, 6, 15, tzinfo=timezone.utc),
        )

        # Filter to only June 2024
        response = await async_client.get("/api/v1/archives/slim?date_from=2024-06-01&date_to=2024-06-30")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["print_name"] == "New Print"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_slim_pagination(self, async_client: AsyncClient, archive_factory, printer_factory, db_session):
        """Verify limit and offset work."""
        printer = await printer_factory()
        for i in range(5):
            await archive_factory(printer.id, print_name=f"Print {i}")

        response = await async_client.get("/api/v1/archives/slim?limit=2&offset=0")

        assert response.status_code == 200
        assert len(response.json()) == 2

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_slim_counts_reprints_as_separate_rows(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Reprints add events even though the archive row is overwritten (#1390).

        Before the per-event migration, /archives/slim returned one row per
        archive — so an archive that had been reprinted three times appeared
        once and undercounted Filament Used / Cost / Time. The endpoint must
        now return one row per logged event.
        """
        from backend.app.models.print_log import PrintLogEntry

        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            print_name="Reprinted Model",
            filament_used_grams=50.0,
            cost=1.50,
        )
        # archive_factory synthesizes one event; add two more to simulate
        # the same archive being reprinted twice more.
        for _ in range(2):
            db_session.add(
                PrintLogEntry(
                    archive_id=archive.id,
                    printer_id=archive.printer_id,
                    status="completed",
                    filament_type=archive.filament_type,
                    filament_used_grams=archive.filament_used_grams,
                    cost=archive.cost,
                    print_name=archive.print_name,
                )
            )
        await db_session.commit()

        response = await async_client.get("/api/v1/archives/slim")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3, "Each reprint must contribute one row"
        total_filament = sum(item["filament_used_grams"] or 0 for item in data)
        assert total_filament == 150.0, "Sum across events must reflect all three runs"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_slim_includes_orphan_events(self, async_client: AsyncClient, printer_factory, db_session):
        """Events whose archive was hard-deleted still appear (#1390).

        After ON DELETE SET NULL the event row survives with archive_id=NULL.
        The slim endpoint must keep counting it so Quick Stats and the
        archive-iterating widgets stay aligned.
        """
        from backend.app.models.print_log import PrintLogEntry

        printer = await printer_factory()
        db_session.add(
            PrintLogEntry(
                archive_id=None,
                printer_id=printer.id,
                status="completed",
                filament_type="PETG",
                filament_used_grams=25.0,
                cost=0.75,
                print_name="Orphaned Print",
            )
        )
        await db_session.commit()

        response = await async_client.get("/api/v1/archives/slim")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["print_name"] == "Orphaned Print"
        assert data[0]["filament_used_grams"] == 25.0
        # print_time_seconds (sliced estimate) comes from the archive table,
        # which orphans no longer have — must surface as null gracefully.
        assert data[0]["print_time_seconds"] is None


class TestFailureAnalysisAPI:
    """Per-event failure analysis (#1390)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_failure_analysis_counts_reprints_and_orphans(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Failure analysis aggregates per event, not per archive.

        Verifies the dual fix for #1390: a reprint that adds a second failed
        event must count twice, and an orphan failed event (archive deleted)
        must still appear in the totals.
        """
        from backend.app.models.print_log import PrintLogEntry

        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            print_name="Failing Model",
            status="failed",
            failure_reason="filament_runout",
        )
        # Add a second failed event for the same archive (a reprint that also
        # failed) and one orphan failed event (archive was deleted).
        db_session.add(
            PrintLogEntry(
                archive_id=archive.id,
                printer_id=printer.id,
                status="failed",
                failure_reason="filament_runout",
                filament_type=archive.filament_type,
                print_name=archive.print_name,
            )
        )
        db_session.add(
            PrintLogEntry(
                archive_id=None,
                printer_id=printer.id,
                status="failed",
                failure_reason="bed_adhesion",
                filament_type="PETG",
                print_name="Orphaned Failed Print",
            )
        )
        await db_session.commit()

        response = await async_client.get("/api/v1/archives/analysis/failures")

        assert response.status_code == 200
        result = response.json()
        assert result["total_prints"] == 3
        assert result["failed_prints"] == 3
        assert result["failures_by_reason"]["filament_runout"] == 2
        assert result["failures_by_reason"]["bed_adhesion"] == 1


class TestArchiveDataIntegrity:
    """Tests for archive data integrity."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_linked_to_printer(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify archive is properly linked to printer."""
        printer = await printer_factory(name="My Printer")
        archive = await archive_factory(printer.id)

        response = await async_client.get(f"/api/v1/archives/{archive.id}")

        assert response.status_code == 200
        result = response.json()
        assert result["printer_id"] == printer.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_stores_print_data(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify archive stores all print data correctly."""
        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            print_name="Test Print",
            filename="test.3mf",
            status="completed",
            filament_type="PLA",
            filament_used_grams=75.5,
            print_time_seconds=5400,
        )

        response = await async_client.get(f"/api/v1/archives/{archive.id}")

        assert response.status_code == 200
        result = response.json()
        assert result["print_name"] == "Test Print"
        assert result["filename"] == "test.3mf"
        assert result["status"] == "completed"
        assert result["filament_type"] == "PLA"
        assert result["filament_used_grams"] == 75.5
        assert result["print_time_seconds"] == 5400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_update_persists(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """CRITICAL: Verify archive updates persist."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, notes="Original notes")

        # Update
        await async_client.patch(f"/api/v1/archives/{archive.id}", json={"notes": "Updated notes", "is_favorite": True})

        # Verify persistence
        response = await async_client.get(f"/api/v1/archives/{archive.id}")
        result = response.json()
        assert result["notes"] == "Updated notes"
        assert result["is_favorite"] is True


class TestArchiveF3DEndpoints:
    """Tests for F3D (Fusion 360 design file) attachment endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_response_includes_f3d_path(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify f3d_path is included in archive response."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, f3d_path="archives/test/design.f3d")

        response = await async_client.get(f"/api/v1/archives/{archive.id}")

        assert response.status_code == 200
        result = response.json()
        assert "f3d_path" in result
        assert result["f3d_path"] == "archives/test/design.f3d"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_response_f3d_path_null_when_not_set(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify f3d_path is null when no F3D file attached."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id)

        response = await async_client.get(f"/api/v1/archives/{archive.id}")

        assert response.status_code == 200
        result = response.json()
        assert "f3d_path" in result
        assert result["f3d_path"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_f3d_to_nonexistent_archive(self, async_client: AsyncClient):
        """Verify 404 when uploading F3D to non-existent archive."""
        # Create a minimal file-like upload
        files = {"file": ("design.f3d", b"fake f3d content", "application/octet-stream")}
        response = await async_client.post("/api/v1/archives/9999/f3d", files=files)

        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_download_f3d_not_found_when_no_file(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify 404 when downloading F3D from archive without F3D file."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id)

        response = await async_client.get(f"/api/v1/archives/{archive.id}/f3d")

        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_download_f3d_nonexistent_archive(self, async_client: AsyncClient):
        """Verify 404 when downloading F3D from non-existent archive."""
        response = await async_client.get("/api/v1/archives/9999/f3d")

        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_f3d_nonexistent_archive(self, async_client: AsyncClient):
        """Verify 404 when deleting F3D from non-existent archive."""
        response = await async_client.delete("/api/v1/archives/9999/f3d")

        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_f3d_when_no_file(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify 404 when deleting F3D from archive without F3D file."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id)

        response = await async_client.delete(f"/api/v1/archives/{archive.id}/f3d")

        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_archives_includes_f3d_path(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify f3d_path is included in archive list responses."""
        printer = await printer_factory()
        await archive_factory(printer.id, print_name="With F3D", f3d_path="archives/test/design.f3d")
        await archive_factory(printer.id, print_name="Without F3D")

        response = await async_client.get("/api/v1/archives/")

        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2

        with_f3d = next((a for a in data if a["print_name"] == "With F3D"), None)
        without_f3d = next((a for a in data if a["print_name"] == "Without F3D"), None)

        assert with_f3d is not None
        assert with_f3d["f3d_path"] == "archives/test/design.f3d"
        assert without_f3d is not None
        assert without_f3d["f3d_path"] is None

    # ========================================================================
    # Multi-Plate 3MF endpoints (Issue #93)
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_archive_plates_not_found(self, async_client: AsyncClient):
        """Verify 404 when fetching plates for non-existent archive."""
        response = await async_client.get("/api/v1/archives/999999/plates")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_plate_thumbnail_not_found(self, async_client: AsyncClient):
        """Verify 404 when fetching plate thumbnail for non-existent archive."""
        response = await async_client.get("/api/v1/archives/999999/plate-thumbnail/1")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_filament_requirements_not_found(self, async_client: AsyncClient):
        """Verify filament-requirements returns 404 for non-existent archive."""
        response = await async_client.get("/api/v1/archives/999999/filament-requirements")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_filament_requirements_with_plate_id_not_found(self, async_client: AsyncClient):
        """Verify filament-requirements with plate_id returns 404 for non-existent archive."""
        response = await async_client.get("/api/v1/archives/999999/filament-requirements?plate_id=1")
        assert response.status_code == 404

    # ========================================================================
    # Tag Management endpoints (Issue #183)
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_tags_empty(self, async_client: AsyncClient):
        """Verify empty list when no tags exist."""
        response = await async_client.get("/api/v1/archives/tags")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_tags_with_data(self, async_client: AsyncClient, archive_factory, printer_factory, db_session):
        """Verify tags are returned with counts."""
        printer = await printer_factory()
        await archive_factory(printer.id, print_name="Archive 1", tags="functional, test")
        await archive_factory(printer.id, print_name="Archive 2", tags="functional, calibration")
        await archive_factory(printer.id, print_name="Archive 3", tags="test")

        response = await async_client.get("/api/v1/archives/tags")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

        # Convert to dict for easier lookup
        tags_dict = {t["name"]: t["count"] for t in data}
        assert tags_dict.get("functional") == 2
        assert tags_dict.get("test") == 2
        assert tags_dict.get("calibration") == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_tags_sorted_by_count(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify tags are sorted by count descending, then by name."""
        printer = await printer_factory()
        await archive_factory(printer.id, tags="alpha")
        await archive_factory(printer.id, tags="beta, alpha")
        await archive_factory(printer.id, tags="gamma, beta, alpha")

        response = await async_client.get("/api/v1/archives/tags")
        assert response.status_code == 200
        data = response.json()

        # alpha=3, beta=2, gamma=1
        assert data[0]["name"] == "alpha"
        assert data[0]["count"] == 3
        assert data[1]["name"] == "beta"
        assert data[1]["count"] == 2
        assert data[2]["name"] == "gamma"
        assert data[2]["count"] == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rename_tag(self, async_client: AsyncClient, archive_factory, printer_factory, db_session):
        """Verify renaming a tag updates all archives."""
        printer = await printer_factory()
        a1 = await archive_factory(printer.id, print_name="Archive 1", tags="old-tag, other")
        a2 = await archive_factory(printer.id, print_name="Archive 2", tags="old-tag")
        await archive_factory(printer.id, print_name="Archive 3", tags="different")

        response = await async_client.put("/api/v1/archives/tags/old-tag", json={"new_name": "new-tag"})
        assert response.status_code == 200
        data = response.json()
        assert data["affected"] == 2

        # Verify the archives were updated
        response = await async_client.get(f"/api/v1/archives/{a1.id}")
        assert "new-tag" in response.json()["tags"]
        assert "old-tag" not in response.json()["tags"]

        response = await async_client.get(f"/api/v1/archives/{a2.id}")
        assert response.json()["tags"] == "new-tag"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rename_tag_no_change(self, async_client: AsyncClient):
        """Verify renaming to same name returns 0 affected."""
        response = await async_client.put("/api/v1/archives/tags/some-tag", json={"new_name": "some-tag"})
        assert response.status_code == 200
        assert response.json()["affected"] == 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rename_tag_empty_name_error(self, async_client: AsyncClient):
        """Verify renaming to empty name returns error."""
        response = await async_client.put("/api/v1/archives/tags/some-tag", json={"new_name": ""})
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_tag(self, async_client: AsyncClient, archive_factory, printer_factory, db_session):
        """Verify deleting a tag removes it from all archives."""
        printer = await printer_factory()
        a1 = await archive_factory(printer.id, print_name="Archive 1", tags="delete-me, keep")
        a2 = await archive_factory(printer.id, print_name="Archive 2", tags="delete-me")
        await archive_factory(printer.id, print_name="Archive 3", tags="different")

        response = await async_client.delete("/api/v1/archives/tags/delete-me")
        assert response.status_code == 200
        data = response.json()
        assert data["affected"] == 2

        # Verify the archives were updated
        response = await async_client.get(f"/api/v1/archives/{a1.id}")
        assert response.json()["tags"] == "keep"

        response = await async_client.get(f"/api/v1/archives/{a2.id}")
        # Should be None or empty when last tag is removed
        assert response.json()["tags"] is None or response.json()["tags"] == ""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_tag_not_found(self, async_client: AsyncClient):
        """Verify deleting non-existent tag returns 0 affected."""
        response = await async_client.delete("/api/v1/archives/tags/nonexistent-tag")
        assert response.status_code == 200
        assert response.json()["affected"] == 0


class TestUploadSourceThreeMF:
    """Regression for #1531: source-3MF upload on fallback archives."""

    @staticmethod
    def _minimal_3mf_bytes() -> bytes:
        """Smallest valid .3mf — the upload path enforces a zip header check."""
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("[Content_Types].xml", "<types/>")
        return buf.getvalue()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_fallback_archive_source_upload_lands_under_base_dir(
        self, async_client: AsyncClient, archive_factory, printer_factory, monkeypatch, tmp_path
    ):
        """Fallback archive (file_path='') must accept a source upload and store it inside base_dir.

        Pre-fix, ``Path(base_dir) / ''`` collapsed to ``base_dir`` and the
        ``.parent`` walked out of the data volume, sending the file to
        ``/app/source/...`` and crashing on ``relative_to``.
        """
        from backend.app.core.config import settings as app_settings

        monkeypatch.setattr(app_settings, "base_dir", tmp_path)

        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            print_name="Cloud Print",
            file_path="",  # fallback archive — no source 3MF was archived
            filename="Cloud Print.3mf",
        )

        files = {"file": ("cloud_print.3mf", self._minimal_3mf_bytes(), "application/octet-stream")}
        response = await async_client.post(f"/api/v1/archives/{archive.id}/source", files=files)

        assert response.status_code == 200, response.text
        payload = response.json()
        rel = payload["source_3mf_path"]
        # Stored as a relative path inside base_dir.
        assert not rel.startswith("/"), f"source_3mf_path should be relative, got {rel!r}"
        # File physically landed under base_dir (NOT escaped to /app/source/).
        assert (tmp_path / rel).is_file()
        # Deterministic fallback location keyed off archive id.
        assert rel == f"archive/no_source/{archive.id}/cloud_print.3mf"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_normal_archive_source_upload_unchanged(
        self, async_client: AsyncClient, archive_factory, printer_factory, monkeypatch, tmp_path
    ):
        """Normal archive (file_path set) still nests the source under <archive>/source/."""
        from backend.app.core.config import settings as app_settings

        monkeypatch.setattr(app_settings, "base_dir", tmp_path)

        printer = await printer_factory()
        # archive_factory's default file_path is "archives/test/test_print.gcode.3mf".
        archive = await archive_factory(printer.id, print_name="Real Print")

        files = {"file": ("real_print.3mf", self._minimal_3mf_bytes(), "application/octet-stream")}
        response = await async_client.post(f"/api/v1/archives/{archive.id}/source", files=files)

        assert response.status_code == 200, response.text
        rel = response.json()["source_3mf_path"]
        assert rel == "archives/test/source/real_print.3mf"
        assert (tmp_path / rel).is_file()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_symlinked_data_dir_upload_succeeds(
        self, async_client: AsyncClient, archive_factory, printer_factory, monkeypatch, tmp_path
    ):
        """Regression: DATA_DIR that's a symlink to the real storage must not break the upload.

        Common on TrueNAS / Synology / QNAP storage pools, and any
        ``-v /symlinked/host/path:/app/data`` mount. The helper resolves
        only for the containment check and returns literal paths so the
        caller's ``relative_to(settings.base_dir)`` doesn't trip over a
        canonical-vs-symlink mismatch.
        """
        from backend.app.core.config import settings as app_settings

        real_dir = tmp_path / "real_storage"
        real_dir.mkdir()
        symlink_dir = tmp_path / "data_via_symlink"
        symlink_dir.symlink_to(real_dir)
        monkeypatch.setattr(app_settings, "base_dir", symlink_dir)

        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            print_name="Symlinked Print",
            file_path="archives/X1C/print.gcode.3mf",
            filename="print.gcode.3mf",
        )

        files = {"file": ("print.3mf", self._minimal_3mf_bytes(), "application/octet-stream")}
        response = await async_client.post(f"/api/v1/archives/{archive.id}/source", files=files)

        assert response.status_code == 200, response.text
        rel = response.json()["source_3mf_path"]
        assert rel == "archives/X1C/source/print.3mf"
        # Reachable via both the symlink and the canonical path.
        assert (symlink_dir / rel).is_file()
        assert (real_dir / rel).is_file()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_absolute_file_path_rejected_with_clear_500(
        self, async_client: AsyncClient, archive_factory, printer_factory, monkeypatch, tmp_path
    ):
        """A row whose file_path is absolute (corrupted by old import / manual edit)
        must fail with the explicit "outside the data directory" message, not silently
        write outside base_dir."""
        from backend.app.core.config import settings as app_settings

        monkeypatch.setattr(app_settings, "base_dir", tmp_path)

        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            print_name="Corrupt Path",
            file_path="/tmp/totally_outside.gcode.3mf",
            filename="totally_outside.gcode.3mf",
        )

        files = {"file": ("totally_outside.3mf", self._minimal_3mf_bytes(), "application/octet-stream")}
        response = await async_client.post(f"/api/v1/archives/{archive.id}/source", files=files)

        assert response.status_code == 500
        assert "outside the data directory" in response.json()["detail"]
        # Did not write anything under the bogus /tmp/source/ either.
        assert not (Path("/tmp") / "source").exists() or not (Path("/tmp") / "source" / "totally_outside.3mf").exists()
