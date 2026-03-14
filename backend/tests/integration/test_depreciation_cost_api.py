"""Integration tests for printer depreciation cost across API endpoints.

Tests the actual endpoint code paths: archive_to_response, get_archive_stats,
rescan_archive, recalculate_all_costs, and printer create/update with
depreciation fields.
"""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient


class TestPrinterDepreciationFields:
    """Printer create/update/get with purchase_price and lifespan_hours."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_printer_with_depreciation(self, async_client: AsyncClient):
        """POST /printers/ accepts purchase_price and lifespan_hours."""
        data = {
            "name": "Dep Printer",
            "serial_number": "00M09A900000001",
            "ip_address": "192.168.1.200",
            "access_code": "12345678",
            "model": "X1C",
            "purchase_price": 600.0,
            "lifespan_hours": 3000.0,
        }
        response = await async_client.post("/api/v1/printers/", json=data)

        assert response.status_code == 200
        result = response.json()
        assert result["purchase_price"] == 600.0
        assert result["lifespan_hours"] == 3000.0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_printer_without_depreciation(self, async_client: AsyncClient):
        """POST /printers/ works without depreciation fields (null)."""
        data = {
            "name": "No Dep Printer",
            "serial_number": "00M09A900000002",
            "ip_address": "192.168.1.201",
            "access_code": "12345678",
        }
        response = await async_client.post("/api/v1/printers/", json=data)

        assert response.status_code == 200
        result = response.json()
        assert result["purchase_price"] is None
        assert result["lifespan_hours"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_printer_depreciation_fields(self, async_client: AsyncClient, printer_factory):
        """PATCH /printers/{id} updates purchase_price and lifespan_hours."""
        printer = await printer_factory()

        response = await async_client.patch(
            f"/api/v1/printers/{printer.id}",
            json={"purchase_price": 1500.0, "lifespan_hours": 5000.0},
        )

        assert response.status_code == 200
        result = response.json()
        assert result["purchase_price"] == 1500.0
        assert result["lifespan_hours"] == 5000.0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_printer_includes_depreciation(self, async_client: AsyncClient, printer_factory):
        """GET /printers/{id} response includes depreciation fields."""
        printer = await printer_factory(purchase_price=600.0, lifespan_hours=3000.0)

        response = await async_client.get(f"/api/v1/printers/{printer.id}")

        assert response.status_code == 200
        result = response.json()
        assert result["purchase_price"] == 600.0
        assert result["lifespan_hours"] == 3000.0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_printers_includes_depreciation(self, async_client: AsyncClient, printer_factory):
        """GET /printers/ list response includes depreciation fields."""
        await printer_factory(purchase_price=600.0, lifespan_hours=3000.0)

        response = await async_client.get("/api/v1/printers/")

        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert data[0]["purchase_price"] == 600.0
        assert data[0]["lifespan_hours"] == 3000.0


class TestArchiveDepreciationResponse:
    """Archive responses include depreciation_cost."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_response_includes_depreciation_cost(
        self, async_client: AsyncClient, printer_factory, archive_factory
    ):
        """GET /archives/{id} includes depreciation_cost in response."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, depreciation_cost=0.40)

        response = await async_client.get(f"/api/v1/archives/{archive.id}")

        assert response.status_code == 200
        result = response.json()
        assert result["depreciation_cost"] == 0.40

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_response_depreciation_null(
        self, async_client: AsyncClient, printer_factory, archive_factory
    ):
        """GET /archives/{id} returns null depreciation_cost when not set."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id)

        response = await async_client.get(f"/api/v1/archives/{archive.id}")

        assert response.status_code == 200
        result = response.json()
        assert result["depreciation_cost"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_list_includes_depreciation_cost(
        self, async_client: AsyncClient, printer_factory, archive_factory
    ):
        """GET /archives/ list includes depreciation_cost on each archive."""
        printer = await printer_factory()
        await archive_factory(printer.id, depreciation_cost=0.25)

        response = await async_client.get("/api/v1/archives/")

        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert data[0]["depreciation_cost"] == 0.25


class TestArchiveStatsDepreciation:
    """Stats endpoint aggregates depreciation costs."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_stats_includes_total_depreciation_cost(
        self, async_client: AsyncClient, printer_factory, archive_factory
    ):
        """GET /archives/stats includes total_depreciation_cost."""
        printer = await printer_factory()
        await archive_factory(printer.id, depreciation_cost=0.40)
        await archive_factory(printer.id, depreciation_cost=0.60)

        response = await async_client.get("/api/v1/archives/stats")

        assert response.status_code == 200
        result = response.json()
        assert "total_depreciation_cost" in result
        assert result["total_depreciation_cost"] == 1.0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_stats_depreciation_zero_when_none(self, async_client: AsyncClient, printer_factory, archive_factory):
        """total_depreciation_cost is 0 when no archives have depreciation."""
        printer = await printer_factory()
        await archive_factory(printer.id)  # No depreciation_cost

        response = await async_client.get("/api/v1/archives/stats")

        assert response.status_code == 200
        result = response.json()
        assert result["total_depreciation_cost"] == 0.0


class TestRecalculateCostsDepreciation:
    """POST /recalculate-costs updates depreciation_cost on archives."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_recalculate_sets_depreciation(self, async_client: AsyncClient, printer_factory, archive_factory):
        """recalculate-costs computes depreciation from printer's price/lifespan."""
        printer = await printer_factory(purchase_price=600.0, lifespan_hours=3000.0)
        archive = await archive_factory(printer.id, print_time_seconds=7200)  # 2 hours

        response = await async_client.post("/api/v1/archives/recalculate-costs")

        assert response.status_code == 200

        # Verify the archive now has depreciation_cost
        get_response = await async_client.get(f"/api/v1/archives/{archive.id}")
        result = get_response.json()
        assert result["depreciation_cost"] == 0.40  # $600 / 3000h * 2h

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_recalculate_clears_depreciation_when_no_price(
        self, async_client: AsyncClient, printer_factory, archive_factory
    ):
        """recalculate-costs sets depreciation to null when printer has no price."""
        printer = await printer_factory()  # No purchase_price
        # Archive with stale depreciation from before printer was edited
        archive = await archive_factory(printer.id, depreciation_cost=0.50, print_time_seconds=7200)

        response = await async_client.post("/api/v1/archives/recalculate-costs")

        assert response.status_code == 200

        get_response = await async_client.get(f"/api/v1/archives/{archive.id}")
        result = get_response.json()
        assert result["depreciation_cost"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_recalculate_updates_depreciation_on_price_change(
        self, async_client: AsyncClient, printer_factory, archive_factory
    ):
        """After updating printer price, recalculate reflects the new value."""
        printer = await printer_factory(purchase_price=600.0, lifespan_hours=3000.0)
        archive = await archive_factory(printer.id, print_time_seconds=3600)  # 1 hour

        # First recalculate
        await async_client.post("/api/v1/archives/recalculate-costs")
        get1 = await async_client.get(f"/api/v1/archives/{archive.id}")
        assert get1.json()["depreciation_cost"] == 0.20  # $600 / 3000h * 1h

        # Update printer price
        await async_client.patch(
            f"/api/v1/printers/{printer.id}",
            json={"purchase_price": 1200.0},
        )

        # Recalculate again
        await async_client.post("/api/v1/archives/recalculate-costs")
        get2 = await async_client.get(f"/api/v1/archives/{archive.id}")
        assert get2.json()["depreciation_cost"] == 0.40  # $1200 / 3000h * 1h

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_recalculate_skips_archive_without_print_time(
        self, async_client: AsyncClient, printer_factory, archive_factory
    ):
        """Archives without print_time_seconds get null depreciation."""
        printer = await printer_factory(purchase_price=600.0, lifespan_hours=3000.0)
        archive = await archive_factory(printer.id, print_time_seconds=None)

        await async_client.post("/api/v1/archives/recalculate-costs")

        get_response = await async_client.get(f"/api/v1/archives/{archive.id}")
        assert get_response.json()["depreciation_cost"] is None
