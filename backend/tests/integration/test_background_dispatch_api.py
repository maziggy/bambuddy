"""Integration tests for background dispatch API behavior."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


class TestBackgroundDispatchArchivesAPI:
    """Tests for the removed archive reprint dispatch endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reprint_route_returns_410_and_does_not_dispatch(self, async_client: AsyncClient):
        """Legacy direct reprint endpoint is gone; callers must create queue items."""
        with patch(
            "backend.app.services.background_dispatch.background_dispatch.dispatch_reprint_archive",
            new=AsyncMock(),
        ) as mock_dispatch:
            response = await async_client.post(
                "/api/v1/archives/123/reprint?printer_id=456",
                json={"plate_id": 2},
            )

        assert response.status_code == 410
        assert "POST /queue/" in response.json()["detail"]
        mock_dispatch.assert_not_awaited()


class TestBackgroundDispatchLibraryAPI:
    """Tests for the removed library print dispatch endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_library_print_route_returns_410_and_does_not_dispatch(self, async_client: AsyncClient):
        """Legacy direct library print endpoint is gone; callers must create queue items."""
        with patch(
            "backend.app.services.background_dispatch.background_dispatch.dispatch_print_library_file",
            new=AsyncMock(),
        ) as mock_dispatch:
            response = await async_client.post(
                "/api/v1/library/files/123/print?printer_id=456",
                json={"plate_id": 4},
            )

        assert response.status_code == 410
        assert "POST /queue/" in response.json()["detail"]
        mock_dispatch.assert_not_awaited()


class TestBackgroundDispatchCancelAPI:
    """Tests for /background-dispatch cancel endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cancel_job_returns_cancelled(self, async_client: AsyncClient):
        """Cancel endpoint returns cancelled for queued job."""
        with patch(
            "backend.app.services.background_dispatch.background_dispatch.cancel_job",
            new=AsyncMock(
                return_value={
                    "cancelled": True,
                    "pending": False,
                    "job_id": 9,
                    "source_name": "cube.gcode.3mf",
                    "printer_id": 1,
                    "printer_name": "Printer A",
                }
            ),
        ):
            response = await async_client.delete("/api/v1/background-dispatch/9")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cancelled"
        assert data["job_id"] == 9

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cancel_job_returns_cancelling_for_active_job(self, async_client: AsyncClient):
        """Cancel endpoint returns cancelling while active upload is being interrupted."""
        with patch(
            "backend.app.services.background_dispatch.background_dispatch.cancel_job",
            new=AsyncMock(
                return_value={
                    "cancelled": True,
                    "pending": True,
                    "job_id": 10,
                    "source_name": "cube.gcode.3mf",
                    "printer_id": 1,
                    "printer_name": "Printer A",
                }
            ),
        ):
            response = await async_client.delete("/api/v1/background-dispatch/10")

        assert response.status_code == 200
        assert response.json()["status"] == "cancelling"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cancel_job_returns_404_when_not_found(self, async_client: AsyncClient):
        """Cancel endpoint returns 404 for unknown job id."""
        with patch(
            "backend.app.services.background_dispatch.background_dispatch.cancel_job",
            new=AsyncMock(return_value={"cancelled": False, "reason": "not_found"}),
        ):
            response = await async_client.delete("/api/v1/background-dispatch/999")

        assert response.status_code == 404
        assert response.json()["detail"] == "Dispatch job not found"
