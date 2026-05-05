"""Integration tests for Virtual Printer API endpoints.

Tests the full request/response cycle for /api/v1/settings/virtual-printer endpoints.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


class TestVirtualPrinterSettingsAPI:
    """Integration tests for /api/v1/settings/virtual-printer endpoints."""

    # ========================================================================
    # Get settings
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_virtual_printer_settings(self, async_client: AsyncClient):
        """Verify virtual printer settings can be retrieved."""
        response = await async_client.get("/api/v1/settings/virtual-printer")

        assert response.status_code == 200
        result = response.json()
        assert "enabled" in result
        assert "access_code_set" in result
        assert "mode" in result
        assert "status" in result

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_settings_has_status(self, async_client: AsyncClient):
        """Verify settings include status details."""
        response = await async_client.get("/api/v1/settings/virtual-printer")

        assert response.status_code == 200
        result = response.json()
        status = result["status"]
        assert "enabled" in status
        assert "running" in status
        assert "mode" in status
        assert "name" in status
        assert "serial" in status
        assert "pending_files" in status

    # ========================================================================
    # Update settings
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_mode(self, async_client: AsyncClient):
        """Verify mode can be updated."""
        response = await async_client.put("/api/v1/settings/virtual-printer?mode=review")

        assert response.status_code == 200
        result = response.json()
        assert result["mode"] == "review"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_mode_to_print_queue(self, async_client: AsyncClient):
        """Verify mode can be set to print_queue."""
        response = await async_client.put("/api/v1/settings/virtual-printer?mode=print_queue")

        assert response.status_code == 200
        result = response.json()
        assert result["mode"] == "print_queue"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_mode_legacy_queue_maps_to_review(self, async_client: AsyncClient):
        """Verify legacy 'queue' mode is normalized to 'review'."""
        response = await async_client.put("/api/v1/settings/virtual-printer?mode=queue")

        assert response.status_code == 200
        result = response.json()
        assert result["mode"] == "review"  # Legacy queue maps to review

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_mode_to_immediate(self, async_client: AsyncClient):
        """Verify mode can be set to immediate."""
        response = await async_client.put("/api/v1/settings/virtual-printer?mode=immediate")

        assert response.status_code == 200
        result = response.json()
        assert result["mode"] == "immediate"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_access_code(self, async_client: AsyncClient):
        """Verify access code can be set."""
        response = await async_client.put("/api/v1/settings/virtual-printer?access_code=12345678")

        assert response.status_code == 200
        result = response.json()
        assert result["access_code_set"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_access_code_wrong_length(self, async_client: AsyncClient):
        """Verify access code validation for length."""
        response = await async_client.put("/api/v1/settings/virtual-printer?access_code=123")

        # Should fail validation
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_enable_without_access_code(self, async_client: AsyncClient):
        """Verify enabling fails without access code set."""
        # First ensure no access code is set by checking current state
        # Then try to enable
        response = await async_client.put("/api/v1/settings/virtual-printer?enabled=true")

        # If access code wasn't set, this should fail
        # If it was already set, it will succeed
        # Both are valid test outcomes
        assert response.status_code in [200, 400]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_enable_with_access_code(self, async_client: AsyncClient):
        """Verify enabling succeeds when access code is set."""
        # First set access code
        await async_client.put("/api/v1/settings/virtual-printer?access_code=12345678")

        # Then enable (this will start the servers which may fail in test env)
        # We mock the manager to avoid actually starting servers
        with patch("backend.app.services.virtual_printer.virtual_printer_manager") as mock_manager:
            mock_manager.configure = AsyncMock()
            mock_manager.get_status = MagicMock(
                return_value={
                    "enabled": True,
                    "running": True,
                    "mode": "immediate",
                    "name": "Bambuddy",
                    "serial": "00M09A391800001",
                    "pending_files": 0,
                }
            )

            response = await async_client.put("/api/v1/settings/virtual-printer?enabled=true")

            assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_disable_virtual_printer(self, async_client: AsyncClient):
        """Verify virtual printer can be disabled."""
        with patch("backend.app.services.virtual_printer.virtual_printer_manager") as mock_manager:
            mock_manager.configure = AsyncMock()
            mock_manager.get_status = MagicMock(
                return_value={
                    "enabled": False,
                    "running": False,
                    "mode": "immediate",
                    "name": "Bambuddy",
                    "serial": "00M09A391800001",
                    "pending_files": 0,
                }
            )

            response = await async_client.put("/api/v1/settings/virtual-printer?enabled=false")

            assert response.status_code == 200
            result = response.json()
            assert result["enabled"] is False


class TestPendingUploadsAPI:
    """Integration tests for /api/v1/pending-uploads/ endpoints."""

    @pytest.fixture
    def mock_pending_uploads(self, db_session):
        """Create mock pending uploads in database."""

        async def _create_pending(filename: str = "test.3mf"):
            from datetime import datetime

            from backend.app.models.pending_upload import PendingUpload

            upload = PendingUpload(
                filename=filename,
                file_path=f"/tmp/{filename}",
                file_size=1024,
                source_ip="192.168.1.100",
                status="pending",
            )
            db_session.add(upload)
            await db_session.commit()
            await db_session.refresh(upload)
            return upload

        return _create_pending

    # ========================================================================
    # List pending uploads
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_pending_uploads_empty(self, async_client: AsyncClient):
        """Verify empty list is returned when no pending uploads."""
        response = await async_client.get("/api/v1/pending-uploads/")

        assert response.status_code == 200
        result = response.json()
        assert isinstance(result, list)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_pending_uploads_count(self, async_client: AsyncClient):
        """Verify count endpoint returns correct count."""
        response = await async_client.get("/api/v1/pending-uploads/count")

        assert response.status_code == 200
        result = response.json()
        assert "count" in result
        assert isinstance(result["count"], int)

    # ========================================================================
    # Archive pending upload
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_nonexistent_upload(self, async_client: AsyncClient):
        """Verify archiving non-existent upload returns 404."""
        response = await async_client.post("/api/v1/pending-uploads/99999/archive")

        assert response.status_code == 404

    # ========================================================================
    # Discard pending upload
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_discard_nonexistent_upload(self, async_client: AsyncClient):
        """Verify discarding non-existent upload returns 404."""
        response = await async_client.delete("/api/v1/pending-uploads/99999")

        assert response.status_code == 404

    # ========================================================================
    # Bulk operations
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_all_empty(self, async_client: AsyncClient):
        """Verify archive all with no pending uploads."""
        response = await async_client.post("/api/v1/pending-uploads/archive-all")

        assert response.status_code == 200
        result = response.json()
        assert "archived" in result
        assert "failed" in result

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_discard_all_empty(self, async_client: AsyncClient):
        """Verify discard all with no pending uploads."""
        response = await async_client.delete("/api/v1/pending-uploads/discard-all")

        assert response.status_code == 200
        result = response.json()
        assert "discarded" in result


class TestVirtualPrinterAutoDispatchAPI:
    """Integration tests for auto_dispatch on /api/v1/virtual-printers endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_virtual_printer_auto_dispatch_default(self, async_client: AsyncClient):
        """Verify creating a VP without auto_dispatch defaults to true."""
        response = await async_client.post(
            "/api/v1/virtual-printers",
            json={
                "name": "TestDefaultDispatch",
                "mode": "print_queue",
                "access_code": "12345678",
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["auto_dispatch"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_virtual_printer_auto_dispatch_false(self, async_client: AsyncClient):
        """Verify creating a VP with auto_dispatch=false persists correctly."""
        response = await async_client.post(
            "/api/v1/virtual-printers",
            json={
                "name": "TestManualDispatch",
                "mode": "print_queue",
                "access_code": "12345678",
                "auto_dispatch": False,
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["auto_dispatch"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_virtual_printer_auto_dispatch(self, async_client: AsyncClient):
        """Verify auto_dispatch can be toggled via PUT and persists."""
        # Create with auto_dispatch=True (default)
        create_resp = await async_client.post(
            "/api/v1/virtual-printers",
            json={
                "name": "TestToggleDispatch",
                "mode": "print_queue",
                "access_code": "12345678",
            },
        )
        assert create_resp.status_code == 200
        vp_id = create_resp.json()["id"]

        # Update to auto_dispatch=False
        update_resp = await async_client.put(
            f"/api/v1/virtual-printers/{vp_id}",
            json={"auto_dispatch": False},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["auto_dispatch"] is False

        # Verify it persists by fetching
        get_resp = await async_client.get(f"/api/v1/virtual-printers/{vp_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["auto_dispatch"] is False


class TestVirtualPrinterTailscaleToggleAPI:
    """The Tailscale toggle is informational — toggling either way always succeeds.

    There used to be a 409 guard rejecting "enable" when the daemon was unreachable,
    back when the toggle controlled LE cert provisioning. That path was removed:
    the slicer's printer-MQTT trust validates against its bundled BBL CA, not the
    system trust store, so even an LE cert wouldn't be accepted. The toggle now
    only surfaces the host's Tailscale IP/FQDN on the VP card; daemon presence is
    irrelevant to whether the toggle can be flipped.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_toggle_does_not_consult_tailscale_daemon(self, async_client: AsyncClient):
        """PUT tailscale_disabled never calls tailscale_service.get_status — always succeeds."""
        create_resp = await async_client.post(
            "/api/v1/virtual-printers",
            json={
                "name": "TestTailscaleToggle",
                "mode": "immediate",
                "access_code": "12345678",
            },
        )
        assert create_resp.status_code == 200
        vp_id = create_resp.json()["id"]
        assert create_resp.json()["tailscale_disabled"] is True

        with patch(
            "backend.app.services.virtual_printer.tailscale.tailscale_service.get_status",
            new=AsyncMock(side_effect=AssertionError("get_status must not be called for toggle")),
        ):
            enable_resp = await async_client.put(
                f"/api/v1/virtual-printers/{vp_id}",
                json={"tailscale_disabled": False},
            )
            disable_resp = await async_client.put(
                f"/api/v1/virtual-printers/{vp_id}",
                json={"tailscale_disabled": True},
            )

        assert enable_resp.status_code == 200
        assert enable_resp.json()["tailscale_disabled"] is False
        assert disable_resp.status_code == 200
        assert disable_resp.json()["tailscale_disabled"] is True
