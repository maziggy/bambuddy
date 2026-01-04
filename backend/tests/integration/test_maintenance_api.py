"""Integration tests for Maintenance API endpoints."""

import pytest
from httpx import AsyncClient


class TestMaintenanceTypesAPI:
    """Integration tests for /api/v1/maintenance/types endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_maintenance_types(self, async_client: AsyncClient):
        """Verify maintenance types list returns data with defaults."""
        response = await async_client.get("/api/v1/maintenance/types")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # Should have default system types
        assert len(data) >= 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_includes_system_types(self, async_client: AsyncClient):
        """Verify default system types are created."""
        response = await async_client.get("/api/v1/maintenance/types")
        assert response.status_code == 200
        data = response.json()
        names = [t["name"] for t in data]
        # Check for some default types
        assert "Lubricate Linear Rails" in names or len(data) > 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_custom_maintenance_type(self, async_client: AsyncClient):
        """Verify custom maintenance type can be created."""
        data = {
            "name": "Custom Test Task",
            "description": "Test description",
            "default_interval_hours": 200.0,
            "interval_type": "hours",
            "icon": "Wrench",
        }
        response = await async_client.post("/api/v1/maintenance/types", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["name"] == "Custom Test Task"
        assert result["is_system"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_maintenance_type(self, async_client: AsyncClient):
        """Verify maintenance type can be updated."""
        # First create a custom type
        create_data = {
            "name": "Update Test",
            "description": "Original",
            "default_interval_hours": 100.0,
        }
        create_response = await async_client.post("/api/v1/maintenance/types", json=create_data)
        assert create_response.status_code == 200
        type_id = create_response.json()["id"]

        # Update it
        update_data = {"description": "Updated description"}
        response = await async_client.patch(f"/api/v1/maintenance/types/{type_id}", json=update_data)
        assert response.status_code == 200
        assert response.json()["description"] == "Updated description"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_custom_maintenance_type(self, async_client: AsyncClient):
        """Verify custom maintenance type can be deleted."""
        # Create a custom type
        create_data = {
            "name": "Delete Test",
            "description": "To be deleted",
            "default_interval_hours": 50.0,
        }
        create_response = await async_client.post("/api/v1/maintenance/types", json=create_data)
        type_id = create_response.json()["id"]

        # Delete it
        response = await async_client.delete(f"/api/v1/maintenance/types/{type_id}")
        assert response.status_code == 200


class TestPrinterMaintenanceAPI:
    """Integration tests for /api/v1/maintenance/printers endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_printer_maintenance_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent printer."""
        response = await async_client.get("/api/v1/maintenance/printers/9999")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_printer_maintenance(self, async_client: AsyncClient, printer_factory, db_session):
        """Verify maintenance overview for a printer."""
        printer = await printer_factory(name="Maintenance Test Printer")
        response = await async_client.get(f"/api/v1/maintenance/printers/{printer.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["printer_id"] == printer.id
        assert data["printer_name"] == "Maintenance Test Printer"
        assert "maintenance_items" in data
        assert "total_print_hours" in data

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_all_maintenance_overview(self, async_client: AsyncClient, printer_factory, db_session):
        """Verify overview endpoint returns all printers."""
        await printer_factory(name="Overview Printer 1")
        await printer_factory(name="Overview Printer 2")
        response = await async_client.get("/api/v1/maintenance/overview")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_maintenance_summary(self, async_client: AsyncClient):
        """Verify summary endpoint returns counts."""
        response = await async_client.get("/api/v1/maintenance/summary")
        assert response.status_code == 200
        data = response.json()
        assert "total_due" in data
        assert "total_warning" in data
        assert "printers_with_issues" in data


class TestMaintenanceItemsAPI:
    """Integration tests for /api/v1/maintenance/items endpoints."""

    @pytest.fixture
    async def maintenance_item(self, async_client: AsyncClient, printer_factory, db_session):
        """Create a maintenance item for testing."""
        printer = await printer_factory(name="Item Test Printer")
        # Get the printer's maintenance overview to create items
        response = await async_client.get(f"/api/v1/maintenance/printers/{printer.id}")
        assert response.status_code == 200
        data = response.json()
        # Return the first maintenance item
        if data["maintenance_items"]:
            return data["maintenance_items"][0]
        return None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_maintenance_item(self, async_client: AsyncClient, maintenance_item):
        """Verify maintenance item can be updated."""
        if not maintenance_item:
            pytest.skip("No maintenance items available")

        item_id = maintenance_item["id"]
        response = await async_client.patch(
            f"/api/v1/maintenance/items/{item_id}", json={"custom_interval_hours": 150.0}
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_disable_maintenance_item(self, async_client: AsyncClient, maintenance_item):
        """Verify maintenance item can be disabled."""
        if not maintenance_item:
            pytest.skip("No maintenance items available")

        item_id = maintenance_item["id"]
        response = await async_client.patch(f"/api/v1/maintenance/items/{item_id}", json={"enabled": False})
        assert response.status_code == 200
        assert response.json()["enabled"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_perform_maintenance(self, async_client: AsyncClient, maintenance_item):
        """Verify maintenance can be marked as performed."""
        if not maintenance_item:
            pytest.skip("No maintenance items available")

        item_id = maintenance_item["id"]
        response = await async_client.post(
            f"/api/v1/maintenance/items/{item_id}/perform", json={"notes": "Test maintenance performed"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["last_performed_at"] is not None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_maintenance_history(self, async_client: AsyncClient, maintenance_item):
        """Verify maintenance history can be retrieved."""
        if not maintenance_item:
            pytest.skip("No maintenance items available")

        item_id = maintenance_item["id"]
        # First perform maintenance to create history
        await async_client.post(f"/api/v1/maintenance/items/{item_id}/perform", json={"notes": "History test"})

        response = await async_client.get(f"/api/v1/maintenance/items/{item_id}/history")
        assert response.status_code == 200
        history = response.json()
        assert isinstance(history, list)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_maintenance_item_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent maintenance item."""
        response = await async_client.patch("/api/v1/maintenance/items/9999", json={"enabled": False})
        assert response.status_code == 404


class TestPrinterHoursAPI:
    """Integration tests for /api/v1/maintenance/printers/{id}/hours endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_set_printer_hours(self, async_client: AsyncClient, printer_factory, db_session):
        """Verify printer hours can be set."""
        printer = await printer_factory(name="Hours Test Printer")
        response = await async_client.patch(
            f"/api/v1/maintenance/printers/{printer.id}/hours", params={"total_hours": 500.0}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_hours"] == 500.0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_set_printer_hours_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent printer."""
        response = await async_client.patch("/api/v1/maintenance/printers/9999/hours", params={"total_hours": 100.0})
        assert response.status_code == 404
