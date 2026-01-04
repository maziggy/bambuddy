"""Integration tests for AMS History API endpoints."""

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient


class TestAMSHistoryAPI:
    """Integration tests for /api/v1/ams-history endpoints."""

    @pytest.fixture
    async def ams_history_factory(self, db_session, printer_factory):
        """Factory to create test AMS history records."""

        async def _create_history(printer_id=None, ams_id=0, **kwargs):
            from backend.app.models.ams_history import AMSSensorHistory

            if printer_id is None:
                printer = await printer_factory()
                printer_id = printer.id

            defaults = {
                "printer_id": printer_id,
                "ams_id": ams_id,
                "humidity": 45.0,
                "humidity_raw": 4500,
                "temperature": 25.0,
                "recorded_at": datetime.now(),
            }
            defaults.update(kwargs)

            history = AMSSensorHistory(**defaults)
            db_session.add(history)
            await db_session.commit()
            await db_session.refresh(history)
            return history

        return _create_history

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_ams_history_empty(self, async_client: AsyncClient, printer_factory, db_session):
        """Verify empty history returns empty data array."""
        printer = await printer_factory()
        response = await async_client.get(f"/api/v1/ams-history/{printer.id}/0")
        assert response.status_code == 200
        data = response.json()
        assert data["printer_id"] == printer.id
        assert data["ams_id"] == 0
        assert data["data"] == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_ams_history_with_data(self, async_client: AsyncClient, ams_history_factory, db_session):
        """Verify history returns recorded data."""
        # Create history records
        history = await ams_history_factory()
        printer_id = history.printer_id

        response = await async_client.get(f"/api/v1/ams-history/{printer_id}/0")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) >= 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_ams_history_with_stats(
        self, async_client: AsyncClient, ams_history_factory, printer_factory, db_session
    ):
        """Verify history includes statistics."""
        printer = await printer_factory()
        # Create multiple records with different values
        await ams_history_factory(printer_id=printer.id, humidity=40.0, temperature=24.0)
        await ams_history_factory(printer_id=printer.id, humidity=50.0, temperature=26.0)
        await ams_history_factory(printer_id=printer.id, humidity=45.0, temperature=25.0)

        response = await async_client.get(f"/api/v1/ams-history/{printer.id}/0")
        assert response.status_code == 200
        data = response.json()

        # Check statistics
        assert data["min_humidity"] == 40.0
        assert data["max_humidity"] == 50.0
        assert data["min_temperature"] == 24.0
        assert data["max_temperature"] == 26.0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_ams_history_with_hours_filter(
        self, async_client: AsyncClient, ams_history_factory, printer_factory, db_session
    ):
        """Verify hours parameter filters data."""
        printer = await printer_factory()
        # Create a recent record
        await ams_history_factory(printer_id=printer.id, recorded_at=datetime.now())
        # Create an old record (outside default 24h)
        await ams_history_factory(printer_id=printer.id, recorded_at=datetime.now() - timedelta(hours=48))

        # Request only last 24 hours (default)
        response = await async_client.get(f"/api/v1/ams-history/{printer.id}/0")
        assert response.status_code == 200
        data = response.json()
        # Should only get the recent record
        assert len(data["data"]) == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_ams_history_custom_hours(self, async_client: AsyncClient, printer_factory, db_session):
        """Verify custom hours parameter works."""
        printer = await printer_factory()
        response = await async_client.get(f"/api/v1/ams-history/{printer.id}/0", params={"hours": 48})
        assert response.status_code == 200
        data = response.json()
        assert data["printer_id"] == printer.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_ams_history_different_ams_units(
        self, async_client: AsyncClient, ams_history_factory, printer_factory, db_session
    ):
        """Verify filtering by AMS unit ID."""
        printer = await printer_factory()
        await ams_history_factory(printer_id=printer.id, ams_id=0, humidity=40.0)
        await ams_history_factory(printer_id=printer.id, ams_id=1, humidity=50.0)

        # Get AMS unit 0
        response = await async_client.get(f"/api/v1/ams-history/{printer.id}/0")
        assert response.status_code == 200
        data0 = response.json()
        assert len(data0["data"]) == 1
        assert data0["data"][0]["humidity"] == 40.0

        # Get AMS unit 1
        response = await async_client.get(f"/api/v1/ams-history/{printer.id}/1")
        assert response.status_code == 200
        data1 = response.json()
        assert len(data1["data"]) == 1
        assert data1["data"][0]["humidity"] == 50.0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_old_history(
        self, async_client: AsyncClient, ams_history_factory, printer_factory, db_session
    ):
        """Verify old history can be deleted."""
        printer = await printer_factory()
        # Create an old record
        await ams_history_factory(printer_id=printer.id, recorded_at=datetime.now() - timedelta(days=60))

        # Delete records older than 30 days
        response = await async_client.delete(f"/api/v1/ams-history/{printer.id}", params={"days": 30})
        assert response.status_code == 200
        data = response.json()
        assert data["deleted"] >= 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_old_history_no_records(self, async_client: AsyncClient, printer_factory, db_session):
        """Verify delete with no old records returns 0."""
        printer = await printer_factory()
        response = await async_client.delete(f"/api/v1/ams-history/{printer.id}", params={"days": 30})
        assert response.status_code == 200
        data = response.json()
        assert data["deleted"] == 0
