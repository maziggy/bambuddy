"""Integration tests for Spoolman API endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


class TestSpoolmanAPI:
    """Integration tests for /api/v1/spoolman/ endpoints."""

    @pytest.fixture
    async def spoolman_settings(self, db_session):
        """Create Spoolman settings in the database (enabled with URL)."""
        from backend.app.models.settings import Settings

        # Both settings are required for Spoolman to work
        enabled_setting = Settings(key="spoolman_enabled", value="true")
        url_setting = Settings(key="spoolman_url", value="http://localhost:7912")
        db_session.add(enabled_setting)
        db_session.add(url_setting)
        await db_session.commit()
        return {"enabled": enabled_setting, "url": url_setting}

    @pytest.fixture
    async def spoolman_url_only(self, db_session):
        """Create only the URL setting (not enabled)."""
        from backend.app.models.settings import Settings

        setting = Settings(key="spoolman_url", value="http://localhost:7912")
        db_session.add(setting)
        await db_session.commit()
        return setting

    @pytest.fixture
    def mock_spoolman_client(self):
        """Mock the Spoolman client functions."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.base_url = "http://localhost:7912"
        mock_client.health_check = AsyncMock(return_value=True)
        mock_client.ensure_tag_extra_field = AsyncMock(return_value=True)
        mock_client.get_spools = AsyncMock(return_value=[])
        mock_client.get_filaments = AsyncMock(return_value=[])
        mock_client.create_spool = AsyncMock(return_value={"id": 1})
        mock_client.update_spool = AsyncMock(return_value={"id": 1})
        mock_client.close = AsyncMock()

        with (
            patch(
                "backend.app.api.routes.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.api.routes.spoolman.init_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.api.routes.spoolman.close_spoolman_client",
                AsyncMock(),
            ),
        ):
            yield mock_client

    @pytest.fixture
    def mock_spoolman_disconnected(self):
        """Mock the Spoolman client as disconnected (returns None)."""
        with (
            patch(
                "backend.app.api.routes.spoolman.get_spoolman_client",
                AsyncMock(return_value=None),
            ),
            patch(
                "backend.app.api.routes.spoolman.init_spoolman_client",
                AsyncMock(return_value=None),
            ),
        ):
            yield

    # =========================================================================
    # Status Endpoint Tests
    # =========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_status_not_configured(self, async_client: AsyncClient):
        """Verify status shows not enabled when no settings exist."""
        response = await async_client.get("/api/v1/spoolman/status")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False
        assert data["connected"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_status_url_only_not_enabled(self, async_client: AsyncClient, spoolman_url_only):
        """Verify status shows not enabled when only URL is set."""
        response = await async_client.get("/api/v1/spoolman/status")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False
        assert data["url"] == "http://localhost:7912"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_status_enabled_and_connected(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """Verify status shows enabled and connected when properly configured."""
        response = await async_client.get("/api/v1/spoolman/status")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert data["connected"] is True
        assert data["url"] == "http://localhost:7912"

    # =========================================================================
    # Connect/Disconnect Tests
    # =========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_connect_not_enabled(self, async_client: AsyncClient):
        """Verify connect fails when not enabled."""
        response = await async_client.post("/api/v1/spoolman/connect")
        assert response.status_code == 400
        assert "not enabled" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_connect_success(self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client):
        """Verify successful connection to Spoolman."""
        response = await async_client.post("/api/v1/spoolman/connect")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "connected" in data["message"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_disconnect(self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client):
        """Verify disconnect works."""
        response = await async_client.post("/api/v1/spoolman/disconnect")
        assert response.status_code == 200
        assert "disconnected" in response.json()["message"].lower()

    # =========================================================================
    # Spools Endpoint Tests
    # =========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_spools_not_enabled(self, async_client: AsyncClient):
        """Verify get spools fails when not enabled."""
        response = await async_client.get("/api/v1/spoolman/spools")
        assert response.status_code == 400
        assert "not enabled" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_spools_success(self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client):
        """Verify get spools returns data in expected format."""
        mock_spool = {
            "id": 1,
            "remaining_weight": 500,
            "used_weight": 500,
            "filament": {
                "id": 1,
                "name": "PLA Basic",
                "material": "PLA",
                "color_hex": "FF0000",
            },
            "first_used": "2024-01-01",
            "last_used": "2024-01-15",
            "location": "AMS1",
            "lot_nr": "LOT123",
            "comment": "Test spool",
            "extra": {"tag": '"ABC123"'},
        }
        mock_spoolman_client.get_spools = AsyncMock(return_value=[mock_spool])

        response = await async_client.get("/api/v1/spoolman/spools")
        assert response.status_code == 200
        data = response.json()
        assert "spools" in data
        assert isinstance(data["spools"], list)
        assert len(data["spools"]) == 1
        assert data["spools"][0]["id"] == 1

    # =========================================================================
    # Unlinked Spools Tests
    # =========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_unlinked_spools_not_enabled(self, async_client: AsyncClient):
        """Verify get unlinked spools fails when not enabled."""
        response = await async_client.get("/api/v1/spoolman/spools/unlinked")
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_unlinked_spools_success(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """Verify get unlinked spools returns spools without tags."""
        # Mock spool without extra.tag (unlinked)
        mock_spool = {
            "id": 1,
            "remaining_weight": 800,
            "used_weight": 200,
            "extra": {},  # No tag = unlinked
            "filament": {
                "id": 1,
                "name": "PLA Basic",
                "material": "PLA",
                "color_hex": "FF0000",
            },
            "location": "Shelf A",
        }
        mock_spoolman_client.get_spools = AsyncMock(return_value=[mock_spool])

        response = await async_client.get("/api/v1/spoolman/spools/unlinked")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == 1
        assert data[0]["filament_name"] == "PLA Basic"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_unlinked_spools_excludes_linked(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """Verify linked spools (with tag) are excluded."""
        # Mock spool with extra.tag (linked)
        mock_spool_linked = {
            "id": 1,
            "remaining_weight": 800,
            "used_weight": 200,
            "extra": {"tag": '"ABC123"'},  # Has tag = linked
            "filament": {"id": 1, "name": "PLA Red", "material": "PLA", "color_hex": "FF0000"},
        }

        # Mock spool without tag (unlinked)
        mock_spool_unlinked = {
            "id": 2,
            "remaining_weight": 900,
            "used_weight": 100,
            "extra": {},  # No tag = unlinked
            "filament": {"id": 2, "name": "PLA Blue", "material": "PLA", "color_hex": "0000FF"},
        }

        mock_spoolman_client.get_spools = AsyncMock(return_value=[mock_spool_linked, mock_spool_unlinked])

        response = await async_client.get("/api/v1/spoolman/spools/unlinked")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == 2  # Only unlinked spool

    # =========================================================================
    # Linked Spools Tests
    # =========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_linked_spools_not_enabled(self, async_client: AsyncClient):
        """Verify get linked spools fails when not enabled."""
        response = await async_client.get("/api/v1/spoolman/spools/linked")
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_linked_spools_success(self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client):
        """Verify get linked spools returns map of tag -> spool_id."""
        # Mock spool with extra.tag (linked)
        mock_spool = {
            "id": 42,
            "remaining_weight": 800,
            "extra": {"tag": '"A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4"'},
            "filament": {"id": 1, "name": "PLA Red", "material": "PLA"},
        }
        mock_spoolman_client.get_spools = AsyncMock(return_value=[mock_spool])

        response = await async_client.get("/api/v1/spoolman/spools/linked")
        assert response.status_code == 200
        data = response.json()
        assert "linked" in data
        assert isinstance(data["linked"], dict)
        # Tag should be uppercase and stripped of quotes
        assert "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4" in data["linked"]
        assert data["linked"]["A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4"] == 42

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_linked_spools_excludes_unlinked(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """Verify unlinked spools (without tag) are excluded."""
        # Mock spool with tag (linked)
        mock_spool_linked = {
            "id": 1,
            "extra": {"tag": '"ABC12345678901234567890123456789A"'},
            "filament": {"id": 1, "name": "PLA Red", "material": "PLA"},
        }
        # Mock spool without tag (unlinked)
        mock_spool_unlinked = {
            "id": 2,
            "extra": {},
            "filament": {"id": 2, "name": "PLA Blue", "material": "PLA"},
        }
        mock_spoolman_client.get_spools = AsyncMock(return_value=[mock_spool_linked, mock_spool_unlinked])

        response = await async_client.get("/api/v1/spoolman/spools/linked")
        assert response.status_code == 200
        data = response.json()
        assert len(data["linked"]) == 1  # Only linked spool

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_linked_spools_empty_tag_excluded(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """Verify spools with empty tag (JSON-encoded empty string) are excluded."""
        # Mock spool with empty JSON-encoded tag
        mock_spool = {
            "id": 1,
            "extra": {"tag": '""'},  # JSON-encoded empty string
            "filament": {"id": 1, "name": "PLA Red", "material": "PLA"},
        }
        mock_spoolman_client.get_spools = AsyncMock(return_value=[mock_spool])

        response = await async_client.get("/api/v1/spoolman/spools/linked")
        assert response.status_code == 200
        data = response.json()
        assert len(data["linked"]) == 0  # Empty tag should be excluded

    # =========================================================================
    # Link Spool Tests
    # =========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_link_spool_not_enabled(self, async_client: AsyncClient):
        """Verify link spool fails when not enabled."""
        response = await async_client.post(
            "/api/v1/spoolman/spools/1/link",
            json={"tray_uuid": "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_link_spool_invalid_uuid_length(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """Verify link spool fails with invalid UUID length."""
        response = await async_client.post(
            "/api/v1/spoolman/spools/1/link",
            json={"tray_uuid": "ABC123"},  # Too short
        )
        assert response.status_code == 400
        assert "32 hex characters" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_link_spool_invalid_uuid_format(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """Verify link spool fails with non-hex UUID."""
        response = await async_client.post(
            "/api/v1/spoolman/spools/1/link",
            json={"tray_uuid": "ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"},  # Not hex
        )
        assert response.status_code == 400
        assert "hex" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_link_spool_success(self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client):
        """Verify successfully linking a spool to AMS tray."""
        mock_spoolman_client.update_spool = AsyncMock(
            return_value={"id": 1, "extra": {"tag": '"A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4"'}}
        )

        response = await async_client.post(
            "/api/v1/spoolman/spools/1/link",
            json={"tray_uuid": "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "linked" in data["message"].lower()

        # Verify update_spool was called
        mock_spoolman_client.update_spool.assert_called_once()

    # =========================================================================
    # Sync Tests
    # =========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_sync_printer_not_enabled(self, async_client: AsyncClient, printer_factory):
        """Verify sync fails when Spoolman not enabled."""
        printer = await printer_factory()
        response = await async_client.post(f"/api/v1/spoolman/sync/{printer.id}")
        assert response.status_code == 400
        assert "not enabled" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_sync_printer_not_found(self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client):
        """Verify sync fails for non-existent printer."""
        response = await async_client.post("/api/v1/spoolman/sync/9999")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_sync_returns_result_structure(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
        printer_factory,
    ):
        """Verify sync returns proper result structure."""
        printer = await printer_factory()

        # Mock printer manager to return AMS data
        with patch("backend.app.api.routes.spoolman.printer_manager") as pm_mock:
            mock_state = MagicMock()
            mock_state.raw_data = {"ams": [{"id": 0, "tray": []}]}
            pm_mock.get_status = MagicMock(return_value=mock_state)

            response = await async_client.post(f"/api/v1/spoolman/sync/{printer.id}")
            assert response.status_code == 200
            data = response.json()
            # Verify SyncResult structure
            assert "success" in data
            assert "synced_count" in data
            assert "skipped_count" in data
            assert "skipped" in data
            assert "errors" in data
            assert isinstance(data["skipped"], list)
            assert isinstance(data["errors"], list)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_sync_printer_not_connected(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
        printer_factory,
    ):
        """Verify sync fails when printer is not connected (no status)."""
        printer = await printer_factory()

        with patch("backend.app.api.routes.spoolman.printer_manager") as pm_mock:
            pm_mock.get_status = MagicMock(return_value=None)

            response = await async_client.post(f"/api/v1/spoolman/sync/{printer.id}")
            assert response.status_code == 404
            assert "not connected" in response.json()["detail"].lower()

    # =========================================================================
    # Filaments Endpoint Tests
    # =========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_filaments_not_enabled(self, async_client: AsyncClient):
        """Verify get filaments fails when not enabled."""
        response = await async_client.get("/api/v1/spoolman/filaments")
        assert response.status_code == 400
        assert "not enabled" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_filaments_success(self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client):
        """Verify get filaments returns data in expected format."""
        mock_filament = {
            "id": 1,
            "name": "PLA Basic",
            "material": "PLA",
            "color_hex": "FF0000",
            "vendor_id": 1,
            "weight": 1000,
        }
        mock_spoolman_client.get_filaments = AsyncMock(return_value=[mock_filament])

        response = await async_client.get("/api/v1/spoolman/filaments")
        assert response.status_code == 200
        data = response.json()
        assert "filaments" in data
        assert isinstance(data["filaments"], list)
        assert len(data["filaments"]) == 1
        assert data["filaments"][0]["name"] == "PLA Basic"
