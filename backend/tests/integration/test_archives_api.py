"""Integration tests for Archives API endpoints.

Tests the full request/response cycle for /api/v1/archives/ endpoints.
"""

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
        assert item["quantity"] == 2
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
    async def test_slim_actual_time_null_for_failed(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify actual_time_seconds is null for non-completed prints."""
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
        assert item["actual_time_seconds"] is None

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
