"""Integration tests for enclosure fan run history API."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient


class TestEnclosureFanHistoryAPI:
    """Integration tests for GET /api/v1/enclosure-fan/{printer_id}/history."""

    @pytest.fixture
    async def fan_run_factory(self, db_session, printer_factory):
        """Factory to create test enclosure fan run records."""

        async def _create(printer_id=None, **kwargs):
            from backend.app.models.enclosure_fan_run import EnclosureFanRun

            if printer_id is None:
                printer = await printer_factory()
                printer_id = printer.id

            defaults = {
                "printer_id": printer_id,
                "started_at": datetime.now(timezone.utc) - timedelta(minutes=30),
                "ended_at": datetime.now(timezone.utc),
            }
            defaults.update(kwargs)
            run = EnclosureFanRun(**defaults)
            db_session.add(run)
            await db_session.commit()
            await db_session.refresh(run)
            return run

        return _create

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_fan_history_empty(self, async_client: AsyncClient, printer_factory):
        """Empty history returns zero counts and an empty runs list."""
        printer = await printer_factory()
        response = await async_client.get(f"/api/v1/enclosure-fan/{printer.id}/history")
        assert response.status_code == 200
        data = response.json()
        assert data["printer_id"] == printer.id
        assert data["runs"] == []
        assert data["run_count"] == 0
        assert data["total_runtime_seconds"] == 0
        assert data["avg_duration_seconds"] is None
        assert data["longest_run_seconds"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_fan_history_with_completed_run(self, async_client: AsyncClient, fan_run_factory):
        """A completed run (with ended_at) is reflected in stats."""
        started = datetime.now(timezone.utc) - timedelta(minutes=10)
        ended = datetime.now(timezone.utc)
        run = await fan_run_factory(started_at=started, ended_at=ended)

        response = await async_client.get(f"/api/v1/enclosure-fan/{run.printer_id}/history")
        assert response.status_code == 200
        data = response.json()
        assert data["run_count"] == 1
        assert len(data["runs"]) == 1
        assert data["runs"][0]["ended_at"] is not None
        # Duration should be approximately 600 seconds (10 minutes)
        assert abs(data["total_runtime_seconds"] - 600) < 5
        assert data["avg_duration_seconds"] is not None
        assert data["longest_run_seconds"] is not None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_fan_history_in_progress_run(self, async_client: AsyncClient, fan_run_factory):
        """An open run (no ended_at) counts duration up to the present."""
        run = await fan_run_factory(
            started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            ended_at=None,
        )

        response = await async_client.get(f"/api/v1/enclosure-fan/{run.printer_id}/history")
        assert response.status_code == 200
        data = response.json()
        assert data["run_count"] == 1
        assert data["runs"][0]["ended_at"] is None
        # At least ~5 minutes of runtime
        assert data["total_runtime_seconds"] >= 290

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_fan_history_hours_filter(self, async_client: AsyncClient, fan_run_factory, printer_factory):
        """Runs outside the requested window are excluded."""
        printer = await printer_factory()
        # Recent run (within 24h)
        await fan_run_factory(
            printer_id=printer.id,
            started_at=datetime.now(timezone.utc) - timedelta(hours=1),
            ended_at=datetime.now(timezone.utc),
        )
        # Old run (outside 24h)
        await fan_run_factory(
            printer_id=printer.id,
            started_at=datetime.now(timezone.utc) - timedelta(hours=48),
            ended_at=datetime.now(timezone.utc) - timedelta(hours=47),
        )

        response = await async_client.get(f"/api/v1/enclosure-fan/{printer.id}/history?hours=24")
        assert response.status_code == 200
        data = response.json()
        assert data["run_count"] == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_fan_history_max_hours(self, async_client: AsyncClient, printer_factory):
        """Query parameter is bounded to 168 hours (7 days)."""
        printer = await printer_factory()
        response = await async_client.get(f"/api/v1/enclosure-fan/{printer.id}/history?hours=9999")
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_fan_history_isolates_by_printer(
        self, async_client: AsyncClient, fan_run_factory, printer_factory
    ):
        """History for one printer does not include another printer's runs."""
        run = await fan_run_factory()
        other = await printer_factory()
        await fan_run_factory(printer_id=other.id)

        response = await async_client.get(f"/api/v1/enclosure-fan/{run.printer_id}/history")
        assert response.status_code == 200
        data = response.json()
        assert data["run_count"] == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_fan_history_aggregates_multiple_runs(
        self, async_client: AsyncClient, fan_run_factory, printer_factory
    ):
        """Multiple runs produce correct avg and longest stats."""
        printer = await printer_factory()
        now = datetime.now(timezone.utc)
        # 5-minute run
        await fan_run_factory(
            printer_id=printer.id,
            started_at=now - timedelta(hours=3, minutes=5),
            ended_at=now - timedelta(hours=3),
        )
        # 15-minute run
        await fan_run_factory(
            printer_id=printer.id,
            started_at=now - timedelta(hours=2, minutes=15),
            ended_at=now - timedelta(hours=2),
        )

        response = await async_client.get(f"/api/v1/enclosure-fan/{printer.id}/history")
        assert response.status_code == 200
        data = response.json()
        assert data["run_count"] == 2
        # Total: ~1200s; longest: ~900s; avg: ~600s
        assert abs(data["total_runtime_seconds"] - 1200) < 10
        assert abs(data["longest_run_seconds"] - 900) < 10
        assert abs(data["avg_duration_seconds"] - 600) < 10

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_fan_history_is_on_defaults_none(self, async_client: AsyncClient, printer_factory):
        """is_on is None when no HA cache is available."""
        printer = await printer_factory()
        response = await async_client.get(f"/api/v1/enclosure-fan/{printer.id}/history")
        assert response.status_code == 200
        assert response.json()["is_on"] is None
