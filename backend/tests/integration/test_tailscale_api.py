"""Integration tests for GET /api/v1/virtual-printers/tailscale-status."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


class TestTailscaleStatusAPI:
    """Tests for the tailscale-status endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tailscale_status_available(self, async_client: AsyncClient):
        """Returns 200 with available=true when Tailscale is connected."""
        from backend.app.services.virtual_printer.tailscale import TailscaleStatus

        mock_status = TailscaleStatus(
            available=True,
            hostname="myhost",
            tailnet_name="example.ts.net",
            fqdn="myhost.example.ts.net",
            tailscale_ips=["100.1.2.3"],
        )

        with patch("backend.app.api.routes.virtual_printers.tailscale_service") as mock_svc:
            mock_svc.get_status = AsyncMock(return_value=mock_status)
            response = await async_client.get("/api/v1/virtual-printers/tailscale-status")

        assert response.status_code == 200
        data = response.json()
        assert data["available"] is True
        assert data["fqdn"] == "myhost.example.ts.net"
        assert data["hostname"] == "myhost"
        assert data["tailnet_name"] == "example.ts.net"
        assert "100.1.2.3" in data["tailscale_ips"]
        assert data["error"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tailscale_status_unavailable(self, async_client: AsyncClient):
        """Returns 200 with available=false and error message when Tailscale is absent."""
        from backend.app.services.virtual_printer.tailscale import TailscaleStatus

        mock_status = TailscaleStatus(
            available=False,
            hostname="",
            tailnet_name="",
            fqdn="",
            error="tailscale binary not found",
        )

        with patch("backend.app.api.routes.virtual_printers.tailscale_service") as mock_svc:
            mock_svc.get_status = AsyncMock(return_value=mock_status)
            response = await async_client.get("/api/v1/virtual-printers/tailscale-status")

        assert response.status_code == 200
        data = response.json()
        assert data["available"] is False
        assert data["fqdn"] == ""
        assert data["error"] == "tailscale binary not found"
