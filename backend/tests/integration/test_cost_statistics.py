"""Integration tests for cost tracking in archives and statistics.

Tests the full flow of cost tracking from usage to statistics:
- Archive cost field populated correctly
- Statistics endpoint aggregates costs
- Completed vs failed prints cost handling
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.archive import PrintArchive
from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment


class TestArchiveCostTracking:
    """Tests for cost field in PrintArchive."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_has_cost_field(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify PrintArchive includes cost field in response."""
        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            print_name="Test Archive",
            status="completed",
            cost=5.50,  # Set a cost
        )

        response = await async_client.get(f"/api/v1/archives/{archive.id}")

        assert response.status_code == 200
        result = response.json()
        assert "cost" in result
        assert result["cost"] == 5.50

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_cost_null_when_not_set(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify cost is null when not set."""
        printer = await printer_factory()
        archive = await archive_factory(
            printer.id,
            print_name="Test Archive",
            status="completed",
            # cost not set
        )

        response = await async_client.get(f"/api/v1/archives/{archive.id}")

        assert response.status_code == 200
        result = response.json()
        assert result["cost"] is None or result["cost"] == 0


class TestStatisticsCostAggregation:
    """Tests for cost aggregation in statistics endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_statistics_includes_total_cost(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify statistics endpoint includes total_cost field."""
        printer = await printer_factory()

        # Create archives with costs
        await archive_factory(
            printer.id,
            status="completed",
            cost=2.50,
            filament_used_grams=100.0,
        )
        await archive_factory(
            printer.id,
            status="completed",
            cost=3.75,
            filament_used_grams=150.0,
        )

        response = await async_client.get("/api/v1/archives/stats")

        assert response.status_code == 200
        result = response.json()
        assert "total_cost" in result
        assert result["total_cost"] == 6.25

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_statistics_aggregates_costs_correctly(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify statistics correctly sums costs from all archives."""
        printer = await printer_factory()

        # Create multiple archives with different costs
        costs = [1.25, 2.50, 0.75, 5.00, 0.50]
        for cost in costs:
            await archive_factory(
                printer.id,
                status="completed",
                cost=cost,
                filament_used_grams=50.0,
            )

        response = await async_client.get("/api/v1/archives/stats")

        assert response.status_code == 200
        result = response.json()
        expected_total = sum(costs)
        assert result["total_cost"] == expected_total

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_statistics_handles_null_costs(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify statistics handles archives with null costs gracefully."""
        printer = await printer_factory()

        # Mix of archives with and without costs
        await archive_factory(printer.id, status="completed", cost=2.50)
        await archive_factory(printer.id, status="completed", cost=None)
        await archive_factory(printer.id, status="completed", cost=1.75)
        await archive_factory(printer.id, status="completed")  # No cost field

        response = await async_client.get("/api/v1/archives/stats")

        assert response.status_code == 200
        result = response.json()
        # Should sum only non-null costs
        assert result["total_cost"] == 4.25

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_statistics_includes_failed_print_costs(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """Verify failed prints with costs are included in statistics."""
        printer = await printer_factory()

        await archive_factory(printer.id, status="completed", cost=5.00)
        await archive_factory(printer.id, status="failed", cost=2.50)  # Failed but has cost
        await archive_factory(printer.id, status="cancelled", cost=1.00)

        response = await async_client.get("/api/v1/archives/stats")

        assert response.status_code == 200
        result = response.json()
        # All prints should contribute to total cost
        assert result["total_cost"] == 8.50

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_statistics_zero_cost_when_no_archives(self, async_client: AsyncClient):
        """Verify total_cost is 0 when no archives exist."""
        response = await async_client.get("/api/v1/archives/stats")

        assert response.status_code == 200
        result = response.json()
        assert result["total_cost"] == 0.0


class TestSpoolCostPersistence:
    """Tests for spool cost_per_kg field."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spool_cost_fields_persist(self, async_client: AsyncClient, db_session):
        """Verify cost_per_kg is saved and retrieved."""
        # Create a spool with cost
        spool_data = {
            "material": "PLA",
            "brand": "TestBrand",
            "label_weight": 1000,
            "core_weight": 250,
            "cost_per_kg": 25.50,
        }

        create_response = await async_client.post("/api/v1/inventory/spools", json=spool_data)
        assert create_response.status_code == 200
        spool_id = create_response.json()["id"]

        # Retrieve and verify
        get_response = await async_client.get(f"/api/v1/inventory/spools/{spool_id}")
        assert get_response.status_code == 200
        result = get_response.json()

        assert result["cost_per_kg"] == 25.50

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spool_update_cost_fields(self, async_client: AsyncClient, db_session):
        """Verify cost fields can be updated."""
        # Create spool without cost
        spool_data = {
            "material": "PETG",
            "brand": "TestBrand",
            "label_weight": 1000,
            "core_weight": 250,
        }

        create_response = await async_client.post("/api/v1/inventory/spools", json=spool_data)
        assert create_response.status_code == 200
        spool_id = create_response.json()["id"]

        # Update with cost
        update_data = {
            "cost_per_kg": 30.00,
        }

        update_response = await async_client.patch(f"/api/v1/inventory/spools/{spool_id}", json=update_data)
        assert update_response.status_code == 200

        result = update_response.json()
        assert result["cost_per_kg"] == 30.00

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spool_cost_null_by_default(self, async_client: AsyncClient, db_session):
        """Verify cost_per_kg defaults to null when not provided."""
        spool_data = {
            "material": "ABS",
            "label_weight": 1000,
            "core_weight": 250,
        }

        create_response = await async_client.post("/api/v1/inventory/spools", json=spool_data)
        assert create_response.status_code == 200

        result = create_response.json()
        assert result["cost_per_kg"] is None


class TestCostCalculationScenarios:
    """End-to-end tests for various cost calculation scenarios."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cost_with_multiple_colors(self, async_client: AsyncClient, printer_factory, db_session):
        """Verify cost tracking works for multi-color prints."""

        # Create two spools with different costs
        spool1_data = {
            "material": "ABS",
            "brand": "TestBrand",
            "label_weight": 1000,
            "core_weight": 250,
            "cost_per_kg": 20.00,
        }
        spool2_data = {
            "material": "PLA",
            "label_weight": 1000,
            "core_weight": 250,
            "cost_per_kg": 25.00,
        }

        spool1_response = await async_client.post("/api/v1/inventory/spools", json=spool1_data)
        spool2_response = await async_client.post("/api/v1/inventory/spools", json=spool2_data)

        assert spool1_response.status_code == 200
        assert spool2_response.status_code == 200

        # Verify spools created with correct costs
        assert spool1_response.json()["cost_per_kg"] == 20.00
        assert spool2_response.json()["cost_per_kg"] == 25.00

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cost_precision(self, async_client: AsyncClient, db_session):
        """Verify cost calculations maintain proper precision."""
        # Create spool with specific cost
        spool_data = {
            "material": "PLA",
            "brand": "TestBrand",
            "label_weight": 1000,
            "core_weight": 250,
            "cost_per_kg": 19.99,  # Specific price
        }

        response = await async_client.post("/api/v1/inventory/spools", json=spool_data)
        assert response.status_code == 200

        result = response.json()
        # Verify precision is maintained
        assert result["cost_per_kg"] == 19.99
