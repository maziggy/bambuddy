"""Integration tests for GitHub Backup API endpoints."""

import pytest
from httpx import AsyncClient


class TestGitHubBackupConfigAPI:
    """Integration tests for /api/v1/github-backup endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_config_no_config(self, async_client: AsyncClient):
        """Verify getting config when none exists returns null."""
        response = await async_client.get("/api/v1/github-backup/config")
        assert response.status_code == 200
        assert response.json() is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_config(self, async_client: AsyncClient):
        """Verify GitHub backup config can be created."""
        data = {
            "repository_url": "https://github.com/test/repo",
            "access_token": "ghp_testtoken123",
            "branch": "main",
            "schedule_enabled": False,
            "schedule_type": "daily",
            "backup_kprofiles": True,
            "backup_cloud_profiles": True,
            "backup_settings": False,
            "enabled": True,
        }
        response = await async_client.post("/api/v1/github-backup/config", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["repository_url"] == "https://github.com/test/repo"
        assert result["branch"] == "main"
        assert result["has_token"] is True
        assert result["enabled"] is True
        # Token should not be exposed in response
        assert "access_token" not in result

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_config_after_create(self, async_client: AsyncClient):
        """Verify getting config after creation returns the config."""
        # Create config first
        data = {
            "repository_url": "https://github.com/test/getrepo",
            "access_token": "ghp_testtoken456",
            "branch": "develop",
            "schedule_enabled": True,
            "schedule_type": "weekly",
            "backup_kprofiles": True,
            "backup_cloud_profiles": False,
            "backup_settings": True,
            "enabled": True,
        }
        await async_client.post("/api/v1/github-backup/config", json=data)

        # Get config
        response = await async_client.get("/api/v1/github-backup/config")
        assert response.status_code == 200
        result = response.json()
        assert result is not None
        assert result["repository_url"] == "https://github.com/test/getrepo"
        assert result["branch"] == "develop"
        assert result["schedule_type"] == "weekly"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_config_partial(self, async_client: AsyncClient):
        """Verify partial update of GitHub backup config."""
        # Create config first
        create_data = {
            "repository_url": "https://github.com/test/update",
            "access_token": "ghp_token",
            "branch": "main",
            "schedule_enabled": False,
            "schedule_type": "daily",
            "backup_kprofiles": True,
            "backup_cloud_profiles": True,
            "backup_settings": False,
            "enabled": True,
        }
        await async_client.post("/api/v1/github-backup/config", json=create_data)

        # Partial update
        update_data = {
            "branch": "develop",
            "schedule_enabled": True,
        }
        response = await async_client.patch("/api/v1/github-backup/config", json=update_data)
        assert response.status_code == 200
        result = response.json()
        assert result["branch"] == "develop"
        assert result["schedule_enabled"] is True
        # Original values should be preserved
        assert result["repository_url"] == "https://github.com/test/update"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_config(self, async_client: AsyncClient):
        """Verify GitHub backup config can be deleted."""
        # Create config first
        create_data = {
            "repository_url": "https://github.com/test/delete",
            "access_token": "ghp_deletetoken",
            "branch": "main",
            "schedule_enabled": False,
            "schedule_type": "daily",
            "backup_kprofiles": True,
            "backup_cloud_profiles": True,
            "backup_settings": False,
            "enabled": True,
        }
        await async_client.post("/api/v1/github-backup/config", json=create_data)

        # Delete
        response = await async_client.delete("/api/v1/github-backup/config")
        assert response.status_code == 200

        # Verify it's deleted
        get_response = await async_client.get("/api/v1/github-backup/config")
        assert get_response.status_code == 200
        assert get_response.json() is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_config_not_found(self, async_client: AsyncClient):
        """Verify deleting non-existent config returns 404."""
        # Make sure no config exists
        await async_client.delete("/api/v1/github-backup/config")

        # Try to delete again
        response = await async_client.delete("/api/v1/github-backup/config")
        assert response.status_code == 404


class TestGitHubBackupStatusAPI:
    """Integration tests for /api/v1/github-backup/status endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_status_no_config(self, async_client: AsyncClient):
        """Verify status when no config exists."""
        # Ensure no config
        await async_client.delete("/api/v1/github-backup/config")

        response = await async_client.get("/api/v1/github-backup/status")
        assert response.status_code == 200
        result = response.json()
        assert result["configured"] is False
        assert result["enabled"] is False
        assert result["is_running"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_status_with_config(self, async_client: AsyncClient):
        """Verify status when config exists."""
        # Create config
        create_data = {
            "repository_url": "https://github.com/test/status",
            "access_token": "ghp_statustoken",
            "branch": "main",
            "schedule_enabled": True,
            "schedule_type": "hourly",
            "backup_kprofiles": True,
            "backup_cloud_profiles": True,
            "backup_settings": False,
            "enabled": True,
        }
        await async_client.post("/api/v1/github-backup/config", json=create_data)

        response = await async_client.get("/api/v1/github-backup/status")
        assert response.status_code == 200
        result = response.json()
        assert result["configured"] is True
        assert result["enabled"] is True
        assert result["is_running"] is False
        assert result["next_scheduled_run"] is not None


class TestGitHubBackupLogsAPI:
    """Integration tests for /api/v1/github-backup/logs endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_logs_no_config(self, async_client: AsyncClient):
        """Verify getting logs when no config exists returns empty list."""
        # Ensure no config
        await async_client.delete("/api/v1/github-backup/config")

        response = await async_client.get("/api/v1/github-backup/logs")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_logs_with_config(self, async_client: AsyncClient):
        """Verify getting logs with config."""
        # Create config
        create_data = {
            "repository_url": "https://github.com/test/logs",
            "access_token": "ghp_logstoken",
            "branch": "main",
            "schedule_enabled": False,
            "schedule_type": "daily",
            "backup_kprofiles": True,
            "backup_cloud_profiles": True,
            "backup_settings": False,
            "enabled": True,
        }
        await async_client.post("/api/v1/github-backup/config", json=create_data)

        response = await async_client.get("/api/v1/github-backup/logs")
        assert response.status_code == 200
        # No backups run yet, so empty list
        assert response.json() == []


class TestGitHubBackupTriggerAPI:
    """Integration tests for /api/v1/github-backup/run endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_trigger_no_config(self, async_client: AsyncClient):
        """Verify triggering backup without config returns 404."""
        # Ensure no config
        await async_client.delete("/api/v1/github-backup/config")

        response = await async_client.post("/api/v1/github-backup/run")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_trigger_disabled_config(self, async_client: AsyncClient):
        """Verify triggering backup with disabled config returns 400."""
        # Create disabled config
        create_data = {
            "repository_url": "https://github.com/test/trigger",
            "access_token": "ghp_triggertoken",
            "branch": "main",
            "schedule_enabled": False,
            "schedule_type": "daily",
            "backup_kprofiles": True,
            "backup_cloud_profiles": True,
            "backup_settings": False,
            "enabled": False,  # Disabled
        }
        await async_client.post("/api/v1/github-backup/config", json=create_data)

        response = await async_client.post("/api/v1/github-backup/run")
        assert response.status_code == 400
        assert "disabled" in response.json()["detail"].lower()
