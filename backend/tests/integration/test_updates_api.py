"""Integration tests for Updates API endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


class TestUpdatesAPI:
    @pytest.mark.asyncio
    async def test_get_version(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/updates/version")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_apply_update_docker_rejection(self, async_client: AsyncClient):
        with patch("backend.app.api.routes.updates._is_docker_environment", return_value=True):
            response = await async_client.post("/api/v1/updates/apply")
        result = response.json()
        assert result["success"] is False
        assert result["is_docker"] is True

    @pytest.mark.asyncio
    async def test_apply_update_non_docker(self, async_client: AsyncClient):
        """Test non-Docker path - mock _perform_update to prevent side effects."""
        with (
            patch("backend.app.api.routes.updates._is_docker_environment", return_value=False),
            patch("backend.app.api.routes.updates._perform_update", new_callable=AsyncMock),
        ):
            response = await async_client.post("/api/v1/updates/apply")
        assert response.json()["success"] is True

    def test_is_docker_with_dockerenv(self):
        from backend.app.api.routes.updates import _is_docker_environment

        with patch("os.path.exists", return_value=True):
            assert _is_docker_environment() is True

    def test_parse_version(self):
        from backend.app.api.routes.updates import parse_version

        assert parse_version("0.1.5")[:3] == (0, 1, 5)

    def test_is_newer_version(self):
        from backend.app.api.routes.updates import is_newer_version

        assert is_newer_version("0.1.5", "0.1.5b7") is True
