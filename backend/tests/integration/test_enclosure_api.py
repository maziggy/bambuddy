"""Integration tests for enclosure temp/humidity history API."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient


class TestEnclosureHistoryAPI:
    """Integration tests for GET /api/v1/enclosure/{printer_id}/history."""

    @pytest.fixture
    async def reading_factory(self, db_session, printer_factory):
        """Factory to create test enclosure reading records."""

        async def _create(printer_id=None, **kwargs):
            from backend.app.models.enclosure_reading import EnclosureReading

            if printer_id is None:
                printer = await printer_factory()
                printer_id = printer.id

            defaults = {
                "printer_id": printer_id,
                "temp": 24.0,
                "humidity": 55.0,
                "recorded_at": datetime.now(timezone.utc),
            }
            defaults.update(kwargs)
            reading = EnclosureReading(**defaults)
            db_session.add(reading)
            await db_session.commit()
            await db_session.refresh(reading)
            return reading

        return _create

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_history_empty(self, async_client: AsyncClient, printer_factory):
        """Empty history returns an empty readings list."""
        printer = await printer_factory()
        response = await async_client.get(f"/api/v1/enclosure/{printer.id}/history")
        assert response.status_code == 200
        data = response.json()
        assert data["printer_id"] == printer.id
        assert data["readings"] == []
        assert data["current_temp"] is None
        assert data["current_humidity"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_history_with_readings(self, async_client: AsyncClient, reading_factory):
        """History returns recorded readings."""
        reading = await reading_factory(temp=25.5, humidity=60.0)
        response = await async_client.get(f"/api/v1/enclosure/{reading.printer_id}/history")
        assert response.status_code == 200
        data = response.json()
        assert len(data["readings"]) == 1
        assert data["readings"][0]["temp"] == 25.5
        assert data["readings"][0]["humidity"] == 60.0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_history_hours_filter(self, async_client: AsyncClient, reading_factory, printer_factory):
        """Readings outside the requested window are excluded."""
        printer = await printer_factory()
        await reading_factory(printer_id=printer.id, recorded_at=datetime.now(timezone.utc))
        await reading_factory(
            printer_id=printer.id,
            recorded_at=datetime.now(timezone.utc) - timedelta(hours=48),
        )
        response = await async_client.get(f"/api/v1/enclosure/{printer.id}/history?hours=24")
        assert response.status_code == 200
        assert len(response.json()["readings"]) == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_history_max_hours(self, async_client: AsyncClient, printer_factory):
        """Query parameter is bounded to 168 hours (7 days)."""
        printer = await printer_factory()
        response = await async_client.get(f"/api/v1/enclosure/{printer.id}/history?hours=9999")
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_history_isolates_by_printer(self, async_client: AsyncClient, reading_factory, printer_factory):
        """History for one printer does not include another printer's readings."""
        r1 = await reading_factory()
        other = await printer_factory()
        await reading_factory(printer_id=other.id)

        response = await async_client.get(f"/api/v1/enclosure/{r1.printer_id}/history")
        assert response.status_code == 200
        data = response.json()
        for reading in data["readings"]:
            assert reading["temp"] is not None or reading["humidity"] is not None
        assert len(data["readings"]) == 1
