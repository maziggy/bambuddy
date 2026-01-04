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
