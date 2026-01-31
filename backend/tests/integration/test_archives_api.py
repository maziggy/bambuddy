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
