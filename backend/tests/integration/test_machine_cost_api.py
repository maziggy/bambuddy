"""Integration tests for machine cost feature across API endpoints.

Tests that price/lifespan_hours fields work through the printer API,
and that machine_cost is computed correctly in archive responses.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient


class TestPrinterMachineCostFields:
    """Test price and lifespan_hours fields on printer CRUD."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_printer_with_price_and_lifespan(self, async_client: AsyncClient):
        """Printer can be created with price and lifespan_hours."""
        response = await async_client.post(
            "/api/v1/printers/",
            json={
                "name": "Cost Test Printer",
                "serial_number": "00M09ACOST00001",
                "ip_address": "192.168.1.200",
                "access_code": "12345678",
                "model": "X1C",
                "price": 1500.0,
                "lifespan_hours": 5000,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["price"] == 1500.0
        assert data["lifespan_hours"] == 5000

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_printer_without_cost_fields(self, async_client: AsyncClient):
        """Printer created without price/lifespan has null values."""
        response = await async_client.post(
            "/api/v1/printers/",
            json={
                "name": "No Cost Printer",
                "serial_number": "00M09ACOST00002",
                "ip_address": "192.168.1.201",
                "access_code": "12345678",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["price"] is None
        assert data["lifespan_hours"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_printer_price_and_lifespan(self, async_client: AsyncClient, printer_factory, db_session):
        """Price and lifespan can be updated on an existing printer."""
        printer = await printer_factory(name="Update Cost Printer")

        response = await async_client.patch(
            f"/api/v1/printers/{printer.id}",
            json={"price": 2000.0, "lifespan_hours": 8000},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["price"] == 2000.0
        assert data["lifespan_hours"] == 8000

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_clear_printer_price_and_lifespan(self, async_client: AsyncClient, printer_factory, db_session):
        """Price and lifespan can be cleared (set to null)."""
        printer = await printer_factory(name="Clear Cost Printer", price=1500.0, lifespan_hours=5000)

        response = await async_client.patch(
            f"/api/v1/printers/{printer.id}",
            json={"price": None, "lifespan_hours": None},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["price"] is None
        assert data["lifespan_hours"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_printers_includes_cost_fields(self, async_client: AsyncClient, printer_factory, db_session):
        """List endpoint includes price and lifespan_hours."""
        await printer_factory(name="Listed Printer", price=999.0, lifespan_hours=3000)

        response = await async_client.get("/api/v1/printers/")

        assert response.status_code == 200
        data = response.json()
        printer_data = next(p for p in data if p["name"] == "Listed Printer")
        assert printer_data["price"] == 999.0
        assert printer_data["lifespan_hours"] == 3000


class TestArchiveMachineCost:
    """Test machine_cost computation in archive endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_includes_machine_cost(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Archive response includes computed machine_cost when printer has price/lifespan."""
        printer = await printer_factory(name="Costed Printer", price=1500.0, lifespan_hours=5000)
        now = datetime.now(timezone.utc)
        archive = await archive_factory(
            printer.id,
            print_name="Machine Cost Print",
            status="completed",
            started_at=now - timedelta(hours=2),
            completed_at=now,
            print_time_seconds=7200,
        )

        response = await async_client.get(f"/api/v1/archives/{archive.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["machine_cost"] is not None
        # (1500 / 5000) * 2 = 0.6
        assert data["machine_cost"] == 0.6

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_machine_cost_null_without_printer_price(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Machine cost is null when printer has no price configured."""
        printer = await printer_factory(name="No Price Printer")
        archive = await archive_factory(
            printer.id,
            print_name="No Cost Print",
            print_time_seconds=3600,
        )

        response = await async_client.get(f"/api/v1/archives/{archive.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["machine_cost"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_machine_cost_uses_slicer_estimate_fallback(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Machine cost falls back to slicer estimate when no actual duration."""
        printer = await printer_factory(name="Estimate Printer", price=1000.0, lifespan_hours=2000)
        archive = await archive_factory(
            printer.id,
            print_name="Estimate Print",
            status="completed",
            print_time_seconds=3600,  # 1 hour slicer estimate
        )

        response = await async_client.get(f"/api/v1/archives/{archive.id}")

        assert response.status_code == 200
        data = response.json()
        # (1000 / 2000) * 1 = 0.5
        assert data["machine_cost"] == 0.5

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_list_includes_machine_cost(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """List archives endpoint includes machine_cost in each response."""
        printer = await printer_factory(name="List Cost Printer", price=1200.0, lifespan_hours=4000)
        await archive_factory(
            printer.id,
            print_name="Listed Archive",
            print_time_seconds=7200,  # 2 hours
        )

        response = await async_client.get("/api/v1/archives/")

        assert response.status_code == 200
        data = response.json()
        archive_data = next(a for a in data if a["print_name"] == "Listed Archive")
        # (1200 / 4000) * 2 = 0.6
        assert archive_data["machine_cost"] == 0.6

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_slim_endpoint_includes_machine_cost(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Slim endpoint includes machine_cost field."""
        printer = await printer_factory(name="Slim Cost Printer", price=1000.0, lifespan_hours=2000)
        await archive_factory(
            printer.id,
            print_name="Slim Archive",
            print_time_seconds=3600,  # 1 hour
        )

        response = await async_client.get("/api/v1/archives/slim")

        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        archive_data = next(a for a in data if a["print_name"] == "Slim Archive")
        # (1000 / 2000) * 1 = 0.5
        assert archive_data["machine_cost"] == 0.5

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_stats_endpoint_includes_total_machine_cost(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Stats endpoint includes total_machine_cost."""
        printer = await printer_factory(name="Stats Cost Printer", price=1500.0, lifespan_hours=5000)
        await archive_factory(
            printer.id,
            print_name="Stats Archive 1",
            print_time_seconds=3600,  # 1 hour
        )
        await archive_factory(
            printer.id,
            print_name="Stats Archive 2",
            print_time_seconds=7200,  # 2 hours
        )

        response = await async_client.get("/api/v1/archives/stats")

        assert response.status_code == 200
        data = response.json()
        assert "total_machine_cost" in data
        # (1500/5000)*1 + (1500/5000)*2 = 0.3 + 0.6 = 0.9
        assert data["total_machine_cost"] == 0.9


class TestArchiveMachineCostTimeConversion:
    """Test that seconds-to-hours conversion works correctly through the API."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_30_minute_print_via_api(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """A 30-minute print correctly converts to 0.5 hours for cost calculation."""
        printer = await printer_factory(name="30min Printer", price=1000.0, lifespan_hours=5000)
        archive = await archive_factory(
            printer.id,
            print_name="30min Print",
            print_time_seconds=1800,  # 30 minutes
        )

        response = await async_client.get(f"/api/v1/archives/{archive.id}")

        assert response.status_code == 200
        data = response.json()
        # cost_per_hour = 1000/5000 = 0.2, duration = 0.5h → 0.1
        assert data["machine_cost"] == 0.1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_90_second_print_via_api(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """A 90-second print converts to 0.025 hours for cost calculation."""
        printer = await printer_factory(name="90sec Printer", price=3000.0, lifespan_hours=1000)
        archive = await archive_factory(
            printer.id,
            print_name="90sec Print",
            print_time_seconds=90,  # 1.5 minutes
        )

        response = await async_client.get(f"/api/v1/archives/{archive.id}")

        assert response.status_code == 200
        data = response.json()
        # cost_per_hour = 3000/1000 = 3.0, duration = 90/3600 = 0.025h → 0.08
        assert data["machine_cost"] == 0.08

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_actual_duration_minutes_via_api(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Actual duration of 45 minutes converts correctly through the API."""
        printer = await printer_factory(name="45min Printer", price=2000.0, lifespan_hours=5000)
        now = datetime.now(timezone.utc)
        archive = await archive_factory(
            printer.id,
            print_name="45min Print",
            status="completed",
            started_at=now - timedelta(minutes=45),
            completed_at=now,
            print_time_seconds=3600,  # slicer says 1h, but actual was 45min
        )

        response = await async_client.get(f"/api/v1/archives/{archive.id}")

        assert response.status_code == 200
        data = response.json()
        # Uses actual 45min (0.75h), not slicer 1h
        # cost_per_hour = 2000/5000 = 0.4, duration = 0.75h → 0.3
        assert data["machine_cost"] == 0.3

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_slim_endpoint_sub_hour_conversion(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Slim endpoint also correctly converts sub-hour durations."""
        printer = await printer_factory(name="Slim Sub-hour Printer", price=1200.0, lifespan_hours=4000)
        await archive_factory(
            printer.id,
            print_name="Slim 15min Print",
            print_time_seconds=900,  # 15 minutes
        )

        response = await async_client.get("/api/v1/archives/slim")

        assert response.status_code == 200
        data = response.json()
        archive_data = next(a for a in data if a["print_name"] == "Slim 15min Print")
        # cost_per_hour = 1200/4000 = 0.3, duration = 0.25h → 0.075 → 0.07 (banker's rounding)
        expected = round(0.3 * 0.25, 2)
        assert archive_data["machine_cost"] == expected

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_stats_total_with_sub_hour_prints(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Stats endpoint correctly sums machine costs for sub-hour prints."""
        printer = await printer_factory(name="Stats Sub-hour Printer", price=1000.0, lifespan_hours=5000)
        # 30 min print: cost = 0.2 * 0.5 = 0.1
        await archive_factory(
            printer.id,
            print_name="Stats 30min",
            print_time_seconds=1800,
        )
        # 15 min print: cost = 0.2 * 0.25 = 0.05
        await archive_factory(
            printer.id,
            print_name="Stats 15min",
            print_time_seconds=900,
        )

        response = await async_client.get("/api/v1/archives/stats")

        assert response.status_code == 200
        data = response.json()
        # 0.1 + 0.05 = 0.15
        assert data["total_machine_cost"] == 0.15


class TestArchiveComparisonMachineCost:
    """Test machine_cost in archive comparison endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_comparison_includes_machine_cost_field(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Compare endpoint includes machine_cost in comparison fields."""
        printer = await printer_factory(name="Compare Cost Printer", price=1000.0, lifespan_hours=2000)
        archive1 = await archive_factory(
            printer.id,
            print_name="Compare Archive 1",
            print_time_seconds=3600,
        )
        archive2 = await archive_factory(
            printer.id,
            print_name="Compare Archive 2",
            print_time_seconds=7200,
        )

        response = await async_client.get(f"/api/v1/archives/compare?archive_ids={archive1.id},{archive2.id}")

        assert response.status_code == 200
        data = response.json()

        # Find machine_cost in comparison fields
        mc_field = next((f for f in data["comparison"] if f["field"] == "machine_cost"), None)
        assert mc_field is not None
        assert mc_field["label"] == "Machine Cost"
        # archive1: (1000/2000)*1=0.5, archive2: (1000/2000)*2=1.0
        assert mc_field["values"] == [0.5, 1.0]
        assert mc_field["has_difference"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_comparison_machine_cost_no_difference(
        self, async_client: AsyncClient, printer_factory, archive_factory, db_session
    ):
        """Compare endpoint shows no difference when machine costs are equal."""
        printer = await printer_factory(name="Equal Cost Printer", price=1000.0, lifespan_hours=2000)
        archive1 = await archive_factory(
            printer.id,
            print_name="Equal Archive 1",
            print_time_seconds=3600,
        )
        archive2 = await archive_factory(
            printer.id,
            print_name="Equal Archive 2",
            print_time_seconds=3600,
        )

        response = await async_client.get(f"/api/v1/archives/compare?archive_ids={archive1.id},{archive2.id}")

        assert response.status_code == 200
        data = response.json()

        mc_field = next((f for f in data["comparison"] if f["field"] == "machine_cost"), None)
        assert mc_field is not None
        assert mc_field["has_difference"] is False
