"""Integration tests for removed direct print API behavior."""

import pytest
from httpx import AsyncClient


class TestLegacyArchivePrintAPI:
    """Tests for the removed archive reprint dispatch endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reprint_route_returns_410_and_does_not_dispatch(self, async_client: AsyncClient):
        """Legacy direct reprint endpoint is gone; callers must create queue items."""
        response = await async_client.post(
            "/api/v1/archives/123/reprint?printer_id=456",
            json={"plate_id": 2},
        )

        assert response.status_code == 410
        assert "POST /queue/" in response.json()["detail"]


class TestLegacyLibraryPrintAPI:
    """Tests for the removed library print dispatch endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_library_print_route_returns_410_and_does_not_dispatch(self, async_client: AsyncClient):
        """Legacy direct library print endpoint is gone; callers must create queue items."""
        response = await async_client.post(
            "/api/v1/library/files/123/print?printer_id=456",
            json={"plate_id": 4},
        )

        assert response.status_code == 410
        assert "POST /queue/" in response.json()["detail"]
