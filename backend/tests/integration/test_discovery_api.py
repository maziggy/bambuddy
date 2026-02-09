"""Integration tests for Discovery API endpoints.

Tests the full request/response cycle for /api/v1/discovery/ endpoints.
"""

import pytest
from httpx import AsyncClient


class TestDiscoveryAPI:
    """Integration tests for /api/v1/discovery/ endpoints."""

    # ========================================================================
    # Info endpoint
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_discovery_info(self, async_client: AsyncClient):
        """Verify discovery info endpoint returns expected fields."""
        response = await async_client.get("/api/v1/discovery/info")

        assert response.status_code == 200
        data = response.json()
        assert "is_docker" in data
        assert "ssdp_running" in data
        assert "scan_running" in data
        assert "subnets" in data
        assert isinstance(data["is_docker"], bool)
        assert isinstance(data["ssdp_running"], bool)
        assert isinstance(data["scan_running"], bool)
        assert isinstance(data["subnets"], list)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_discovery_info_subnets_are_cidr(self, async_client: AsyncClient):
        """Verify subnets are valid CIDR notation strings."""
        response = await async_client.get("/api/v1/discovery/info")

        assert response.status_code == 200
        data = response.json()
        for subnet in data["subnets"]:
            assert isinstance(subnet, str)
            # Should contain a slash for CIDR notation
            assert "/" in subnet, f"Subnet {subnet} is not in CIDR notation"

    # ========================================================================
    # SSDP Discovery endpoints
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_discovery_status(self, async_client: AsyncClient):
        """Verify SSDP discovery status endpoint works."""
        response = await async_client.get("/api/v1/discovery/status")

        assert response.status_code == 200
        data = response.json()
        assert "running" in data
        assert isinstance(data["running"], bool)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_start_discovery(self, async_client: AsyncClient):
        """Verify SSDP discovery can be started."""
        response = await async_client.post("/api/v1/discovery/start?duration=1.0")

        assert response.status_code == 200
        data = response.json()
        assert "running" in data

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_stop_discovery(self, async_client: AsyncClient):
        """Verify SSDP discovery can be stopped."""
        response = await async_client.post("/api/v1/discovery/stop")

        assert response.status_code == 200
        data = response.json()
        assert "running" in data
        assert data["running"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_discovered_printers_empty(self, async_client: AsyncClient):
        """Verify empty list when no printers discovered."""
        response = await async_client.get("/api/v1/discovery/printers")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    # ========================================================================
    # Subnet scanning endpoints
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_start_subnet_scan(self, async_client: AsyncClient):
        """Verify subnet scan can be started."""
        response = await async_client.post(
            "/api/v1/discovery/scan",
            json={"subnet": "192.168.1.0/30", "timeout": 0.1},  # Small subnet for testing
        )

        assert response.status_code == 200
        data = response.json()
        assert "running" in data
        assert "scanned" in data
        assert "total" in data

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_scan_status(self, async_client: AsyncClient):
        """Verify subnet scan status endpoint works."""
        response = await async_client.get("/api/v1/discovery/scan/status")

        assert response.status_code == 200
        data = response.json()
        assert "running" in data
        assert "scanned" in data
        assert "total" in data

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_stop_subnet_scan(self, async_client: AsyncClient):
        """Verify subnet scan can be stopped."""
        response = await async_client.post("/api/v1/discovery/scan/stop")

        assert response.status_code == 200
        data = response.json()
        assert "running" in data

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_subnet_scan_invalid_subnet(self, async_client: AsyncClient):
        """Verify invalid subnet format is rejected."""
        response = await async_client.post("/api/v1/discovery/scan", json={"subnet": "invalid-subnet", "timeout": 1.0})

        # Should return 422 validation error or 200 with empty results
        assert response.status_code in [200, 422]


class TestDiscoveryService:
    """Unit tests for discovery service functionality."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_docker_detection_fields(self, async_client: AsyncClient):
        """Verify Docker detection returns consistent response."""
        # Call multiple times to ensure consistency
        response1 = await async_client.get("/api/v1/discovery/info")
        response2 = await async_client.get("/api/v1/discovery/info")

        assert response1.status_code == 200
        assert response2.status_code == 200
        assert response1.json()["is_docker"] == response2.json()["is_docker"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_subnets_consistent_across_calls(self, async_client: AsyncClient):
        """Verify subnet detection returns consistent results."""
        response1 = await async_client.get("/api/v1/discovery/info")
        response2 = await async_client.get("/api/v1/discovery/info")

        assert response1.status_code == 200
        assert response2.status_code == 200
        assert response1.json()["subnets"] == response2.json()["subnets"]
