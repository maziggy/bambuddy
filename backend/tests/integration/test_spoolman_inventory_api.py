"""Integration tests for the Spoolman inventory proxy endpoints.

These tests verify that /api/v1/spoolman/inventory/spools/* correctly
translates between Spoolman's data model and Bambuddy's InventorySpool format.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

SAMPLE_SPOOLMAN_SPOOL = {
    "id": 42,
    "filament": {
        "id": 7,
        "name": "PLA Basic",
        "material": "PLA",
        "color_hex": "FF0000",
        "weight": 1000,
        "vendor": {"id": 3, "name": "Bambu Lab"},
    },
    "remaining_weight": 750.0,
    "used_weight": 250.0,
    "location": "Printer1 - AMS A1",
    "comment": "test note",
    "first_used": "2024-01-01T00:00:00+00:00",
    "last_used": "2024-02-01T00:00:00+00:00",
    "registered": "2024-01-01T00:00:00+00:00",
    "archived": False,
    "price": None,
    "extra": {"tag": '"AABBCCDDEEFF0011AABBCCDDEEFF0011"'},
}


@pytest.fixture
async def spoolman_settings(db_session):
    """Create Spoolman settings in the database (enabled with URL)."""
    from backend.app.models.settings import Settings

    enabled_setting = Settings(key="spoolman_enabled", value="true")
    url_setting = Settings(key="spoolman_url", value="http://localhost:7912")
    db_session.add(enabled_setting)
    db_session.add(url_setting)
    await db_session.commit()
    return {"enabled": enabled_setting, "url": url_setting}


@pytest.fixture
def mock_spoolman_client():
    """Mock the Spoolman client with a sample spool."""
    mock_client = MagicMock()
    mock_client.base_url = "http://localhost:7912"
    mock_client.health_check = AsyncMock(return_value=True)
    mock_client.get_all_spools = AsyncMock(return_value=[SAMPLE_SPOOLMAN_SPOOL])
    mock_client.get_spool = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)
    mock_client.create_spool = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)
    mock_client.delete_spool = AsyncMock(return_value=True)
    mock_client.set_spool_archived = AsyncMock(
        side_effect=lambda spool_id, archived: {**SAMPLE_SPOOLMAN_SPOOL, "archived": archived}
    )
    mock_client.update_spool_full = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)
    mock_client.merge_spool_extra = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)
    mock_client.find_or_create_filament = AsyncMock(return_value=7)

    with (
        patch(
            "backend.app.api.routes.spoolman_inventory.get_spoolman_client",
            AsyncMock(return_value=mock_client),
        ),
        patch(
            "backend.app.api.routes.spoolman_inventory.init_spoolman_client",
            AsyncMock(return_value=mock_client),
        ),
    ):
        yield mock_client


class TestSpoolmanInventoryMapping:
    """Tests for the Spoolman → InventorySpool data mapping."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_spools_returns_inventory_format(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """GET /spoolman/inventory/spools returns spools in InventorySpool format."""
        response = await async_client.get("/api/v1/spoolman/inventory/spools")

        assert response.status_code == 200
        spools = response.json()
        assert isinstance(spools, list)
        assert len(spools) == 1

        spool = spools[0]
        assert spool["id"] == 42
        assert spool["material"] == "PLA"
        assert spool["subtype"] == "Basic"
        assert spool["brand"] == "Bambu Lab"
        assert spool["label_weight"] == 1000
        assert spool["weight_used"] == 250.0
        assert spool["note"] == "test note"
        assert spool["data_origin"] == "spoolman"
        assert spool["tag_type"] == "spoolman"
        # RRGGBB + FF alpha
        assert spool["rgba"] == "FF0000FF"
        # Spoolman location mapped to storage_location
        assert spool["storage_location"] == "Printer1 - AMS A1"
        # RFID tag: 32-char → tray_uuid
        assert spool["tray_uuid"] == "AABBCCDDEEFF0011AABBCCDDEEFF0011"
        assert spool["tag_uid"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_single_spool(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """GET /spoolman/inventory/spools/{id} returns a single spool."""
        response = await async_client.get("/api/v1/spoolman/inventory/spools/42")

        assert response.status_code == 200
        spool = response.json()
        assert spool["id"] == 42
        assert spool["material"] == "PLA"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_includes_archived_when_requested(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """GET /spoolman/inventory/spools?include_archived=true calls Spoolman with allow_archived."""
        await async_client.get("/api/v1/spoolman/inventory/spools?include_archived=true")
        mock_spoolman_client.get_all_spools.assert_called_once_with(allow_archived=True)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archived_spool_has_archived_at(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """An archived Spoolman spool maps to archived_at != None."""
        archived_spool = {
            **SAMPLE_SPOOLMAN_SPOOL,
            "archived": True,
        }
        mock_spoolman_client.get_all_spools.return_value = [archived_spool]

        response = await async_client.get("/api/v1/spoolman/inventory/spools?include_archived=true")
        spool = response.json()[0]
        assert spool["archived_at"] is not None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tag_uid_16char_maps_correctly(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """A 16-char tag maps to tag_uid, not tray_uuid."""
        spool_with_short_tag = {
            **SAMPLE_SPOOLMAN_SPOOL,
            "extra": {"tag": '"AABBCCDDEEFF0011"'},
        }
        mock_spoolman_client.get_all_spools.return_value = [spool_with_short_tag]

        response = await async_client.get("/api/v1/spoolman/inventory/spools")
        spool = response.json()[0]
        assert spool["tag_uid"] == "AABBCCDDEEFF0011"
        assert spool["tray_uuid"] is None


class TestSpoolmanInventoryCRUD:
    """Tests for create, update, delete, archive, restore operations."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_not_enabled_returns_400(self, async_client: AsyncClient):
        """All endpoints return 400 when Spoolman is not enabled."""
        response = await async_client.get("/api/v1/spoolman/inventory/spools")
        assert response.status_code == 400
        assert "not enabled" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_spool(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """POST /spoolman/inventory/spools creates a spool via Spoolman."""
        payload = {
            "material": "PLA",
            "subtype": "Basic",
            "brand": "Bambu Lab",
            "rgba": "FF0000FF",
            "label_weight": 1000,
            "weight_used": 0,
        }
        response = await async_client.post("/api/v1/spoolman/inventory/spools", json=payload)

        assert response.status_code == 200
        mock_spoolman_client.find_or_create_filament.assert_called_once()
        mock_spoolman_client.create_spool.assert_called_once()
        data = response.json()
        assert data["material"] == "PLA"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_create_spools(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """POST /spoolman/inventory/spools/bulk creates multiple spools."""
        payload = {
            "spool": {"material": "PETG", "label_weight": 1000, "weight_used": 0},
            "quantity": 3,
        }
        response = await async_client.post("/api/v1/spoolman/inventory/spools/bulk", json=payload)

        assert response.status_code == 200
        assert mock_spoolman_client.create_spool.call_count == 3

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_create_quantity_out_of_range_returns_422(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """Bulk create quantity outside 1-50 is rejected with 422 (not silently clamped)."""
        payload = {
            "spool": {"material": "ABS", "label_weight": 1000, "weight_used": 0},
            "quantity": 999,
        }
        response = await async_client.post("/api/v1/spoolman/inventory/spools/bulk", json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_create_quantity_zero_returns_422(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """Bulk create quantity of 0 is rejected with 422."""
        payload = {
            "spool": {"material": "ABS", "label_weight": 1000, "weight_used": 0},
            "quantity": 0,
        }
        response = await async_client.post("/api/v1/spoolman/inventory/spools/bulk", json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_spool(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """PATCH /spoolman/inventory/spools/{id} updates a spool."""
        payload = {"note": "updated note", "weight_used": 100.0}
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json=payload)

        assert response.status_code == 200
        mock_spoolman_client.update_spool_full.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_spool_not_found(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """PATCH returns 404 when Spoolman spool does not exist."""
        from backend.app.services.spoolman import SpoolmanNotFoundError

        mock_spoolman_client.get_spool.side_effect = SpoolmanNotFoundError("spool not found")
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/999", json={"note": "x"})
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_spool(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """DELETE /spoolman/inventory/spools/{id} deletes a spool."""
        response = await async_client.delete("/api/v1/spoolman/inventory/spools/42")

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"
        mock_spoolman_client.delete_spool.assert_called_once_with(42)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_spool_failure(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """DELETE returns 500 when Spoolman deletion fails."""
        mock_spoolman_client.delete_spool.return_value = False
        response = await async_client.delete("/api/v1/spoolman/inventory/spools/42")
        assert response.status_code == 500

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_spool(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """POST /spoolman/inventory/spools/{id}/archive archives a spool."""
        response = await async_client.post("/api/v1/spoolman/inventory/spools/42/archive")

        assert response.status_code == 200
        mock_spoolman_client.set_spool_archived.assert_called_once_with(42, archived=True)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_restore_spool(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """POST /spoolman/inventory/spools/{id}/restore restores an archived spool."""
        response = await async_client.post("/api/v1/spoolman/inventory/spools/42/restore")

        assert response.status_code == 200
        mock_spoolman_client.set_spool_archived.assert_called_once_with(42, archived=False)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_sync_weight(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """PATCH /spoolman/inventory/spools/{id}/weight updates remaining weight."""
        payload = {"weight_grams": 850.0}
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42/weight", json=payload)

        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "ok"
        # remaining = 850 - 250 core = 600; weight_used = 1000 - 600 = 400
        assert result["weight_used"] == 400.0
        mock_spoolman_client.update_spool_full.assert_called_once_with(spool_id=42, remaining_weight=600.0)


class TestSpoolmanInventoryInputValidation:
    """Tests for input validation added as security hardening."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_rejects_material_too_long(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """material longer than 64 chars is rejected with 422."""
        payload = {"material": "A" * 65, "label_weight": 1000, "weight_used": 0}
        response = await async_client.post("/api/v1/spoolman/inventory/spools", json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_rejects_note_too_long(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """note longer than 1000 chars is rejected with 422."""
        payload = {
            "material": "PLA",
            "label_weight": 1000,
            "weight_used": 0,
            "note": "x" * 1001,
        }
        response = await async_client.post("/api/v1/spoolman/inventory/spools", json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_rejects_negative_weight_used(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """Negative weight_used is rejected with 422."""
        payload = {"material": "PLA", "label_weight": 1000, "weight_used": -1.0}
        response = await async_client.post("/api/v1/spoolman/inventory/spools", json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_rejects_zero_label_weight(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """label_weight of 0 is rejected (minimum is 1)."""
        payload = {"material": "PLA", "label_weight": 0, "weight_used": 0}
        response = await async_client.post("/api/v1/spoolman/inventory/spools", json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_rejects_invalid_rgba(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """Non-hex rgba string is rejected with 422."""
        payload = {"material": "PLA", "label_weight": 1000, "weight_used": 0, "rgba": "GGGGGGFF"}
        response = await async_client.post("/api/v1/spoolman/inventory/spools", json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_accepts_valid_6char_rgba(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """A valid 6-char hex rgba is accepted."""
        payload = {"material": "PLA", "label_weight": 1000, "weight_used": 0, "rgba": "FF0000"}
        response = await async_client.post("/api/v1/spoolman/inventory/spools", json=payload)
        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_weight_update_rejects_negative_grams(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """Negative weight_grams on weight sync endpoint is rejected with 422."""
        response = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42/weight",
            json={"weight_grams": -50.0},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_rejects_tag_uid_too_long(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """tag_uid longer than 64 chars is rejected with 422."""
        payload = {"tag_uid": "A" * 65}
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_rejects_tray_uuid_too_long(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """tray_uuid longer than 64 chars is rejected with 422."""
        payload = {"tray_uuid": "B" * 65}
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_invalid_spoolman_url_scheme_returns_400(
        self,
        async_client: AsyncClient,
        db_session,
        mock_spoolman_client,
    ):
        """A spoolman_url with a non-http(s) scheme is rejected."""
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="spoolman_enabled", value="true"))
        db_session.add(Settings(key="spoolman_url", value="ftp://evil.internal/"))
        await db_session.commit()

        response = await async_client.get("/api/v1/spoolman/inventory/spools")
        assert response.status_code == 400
        assert "http" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        "evil_url",
        [
            "file:///etc/passwd",
            "gopher://127.0.0.1:70/",
            "dict://internal.corp/",
            "http://169.254.169.254/latest/meta-data/",  # AWS IMDS (link-local)
            "http://[::1]:7912/",  # IPv6 loopback
            "http://0.0.0.0/",  # unspecified
            "javascript:alert(1)",
            "http://224.0.0.1/",  # IPv4 multicast
            "http://[ff02::1]/",  # IPv6 multicast
            "http://127.1.2.3/",  # 127.x.x.x loopback range
            "http://[::ffff:169.254.169.254]/",  # IPv4-mapped IPv6 IMDS bypass
        ],
    )
    async def test_ssrf_blocked_schemes_and_addresses(
        self,
        async_client: AsyncClient,
        db_session,
        mock_spoolman_client,
        evil_url: str,
    ):
        """SSRF: any Spoolman URL that is not http(s) must be rejected with 400."""
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="spoolman_enabled", value="true"))
        db_session.add(Settings(key="spoolman_url", value=evil_url))
        await db_session.commit()

        response = await async_client.get("/api/v1/spoolman/inventory/spools")
        assert response.status_code == 400, (
            f"Expected 400 for SSRF URL {evil_url!r} but got {response.status_code}: {response.json()}"
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_rejects_storage_location_too_long(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """storage_location longer than 255 chars is rejected with 422."""
        payload = {
            "material": "PLA",
            "label_weight": 1000,
            "weight_used": 0,
            "storage_location": "x" * 256,
        }
        response = await async_client.post("/api/v1/spoolman/inventory/spools", json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_rejects_storage_location_too_long(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """storage_location longer than 255 chars on PATCH is rejected with 422."""
        payload = {"storage_location": "y" * 256}
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json=payload)
        assert response.status_code == 422


class TestStorageLocationPassthrough:
    """Tests that storage_location is correctly passed to and from Spoolman."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_spools_maps_spoolman_location_to_storage_location(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """Spoolman's location field is exposed as storage_location in the response."""
        response = await async_client.get("/api/v1/spoolman/inventory/spools")
        spool = response.json()[0]
        assert spool["storage_location"] == "Printer1 - AMS A1"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_spools_null_location_gives_null_storage_location(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """A Spoolman spool with no location gives null storage_location."""
        spool_no_loc = {**SAMPLE_SPOOLMAN_SPOOL, "location": None}
        mock_spoolman_client.get_all_spools.return_value = [spool_no_loc]
        response = await async_client.get("/api/v1/spoolman/inventory/spools")
        spool = response.json()[0]
        assert spool["storage_location"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_passes_storage_location_to_spoolman(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """storage_location is forwarded as location when creating a Spoolman spool."""
        payload = {
            "material": "PLA",
            "label_weight": 1000,
            "weight_used": 0,
            "storage_location": "Shelf B",
        }
        response = await async_client.post("/api/v1/spoolman/inventory/spools", json=payload)
        assert response.status_code == 200
        mock_spoolman_client.create_spool.assert_called_once()
        _, kwargs = mock_spoolman_client.create_spool.call_args
        assert kwargs.get("location") == "Shelf B"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_passes_storage_location_to_spoolman(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """storage_location is forwarded as location when updating a Spoolman spool."""
        payload = {"storage_location": "Drawer 3"}
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json=payload)
        assert response.status_code == 200
        mock_spoolman_client.update_spool_full.assert_called_once()
        _, kwargs = mock_spoolman_client.update_spool_full.call_args
        assert kwargs.get("location") == "Drawer 3"
        assert kwargs.get("clear_location") is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_clears_storage_location_when_null_sent(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """Explicitly sending null storage_location clears the Spoolman location."""
        payload = {"storage_location": None}
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json=payload)
        assert response.status_code == 200
        _, kwargs = mock_spoolman_client.update_spool_full.call_args
        assert kwargs.get("clear_location") is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_clears_storage_location_when_empty_string_sent(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """Sending an empty string for storage_location also clears the Spoolman location."""
        payload = {"storage_location": ""}
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json=payload)
        assert response.status_code == 200
        _, kwargs = mock_spoolman_client.update_spool_full.call_args
        assert kwargs.get("clear_location") is True


class TestSpoolmanInventoryAuth:
    """Write/delete endpoints require INVENTORY_UPDATE when auth is enabled."""

    @pytest.fixture
    async def auth_and_spoolman_settings(self, db_session):
        """Enable both Spoolman and auth."""
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="spoolman_enabled", value="true"))
        db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
        db_session.add(Settings(key="auth_enabled", value="true"))
        await db_session.commit()

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        "method,path,payload",
        [
            ("POST", "/api/v1/spoolman/inventory/spools", {"material": "PLA", "label_weight": 1000, "weight_used": 0}),
            (
                "POST",
                "/api/v1/spoolman/inventory/spools/bulk",
                {"spool": {"material": "PLA", "label_weight": 1000, "weight_used": 0}, "quantity": 1},
            ),
            ("PATCH", "/api/v1/spoolman/inventory/spools/42", {"note": "x"}),
            ("DELETE", "/api/v1/spoolman/inventory/spools/42", None),
            ("POST", "/api/v1/spoolman/inventory/spools/42/archive", None),
            ("POST", "/api/v1/spoolman/inventory/spools/42/restore", None),
            ("PATCH", "/api/v1/spoolman/inventory/spools/42/weight", {"weight_grams": 100.0}),
        ],
    )
    async def test_write_endpoints_require_auth(
        self,
        async_client: AsyncClient,
        auth_and_spoolman_settings,
        method: str,
        path: str,
        payload: dict | None,
    ):
        """All write/delete endpoints return 401 when auth is enabled and no token is provided."""
        response = await async_client.request(method, path, json=payload)
        assert response.status_code == 401, (
            f"{method} {path} should require auth but got {response.status_code}: {response.json()}"
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/v1/spoolman/inventory/spools"),
            ("GET", "/api/v1/spoolman/inventory/spools/42"),
        ],
    )
    async def test_read_endpoints_require_auth(
        self,
        async_client: AsyncClient,
        auth_and_spoolman_settings,
        method: str,
        path: str,
    ):
        """Read endpoints also require auth when auth is enabled."""
        response = await async_client.request(method, path)
        assert response.status_code == 401, (
            f"{method} {path} should require auth but got {response.status_code}: {response.json()}"
        )

    @pytest.fixture
    async def viewer_token(self, db_session):
        """Create a Viewer-group user (INVENTORY_READ only, no INVENTORY_UPDATE)."""
        from backend.app.core.auth import create_access_token, get_password_hash
        from backend.app.models.group import Group
        from backend.app.models.settings import Settings
        from backend.app.models.user import User
        from sqlalchemy import select

        db_session.add(Settings(key="spoolman_enabled", value="true"))
        db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
        db_session.add(Settings(key="auth_enabled", value="true"))
        await db_session.commit()

        viewer_group = (
            await db_session.execute(select(Group).where(Group.name == "Viewers"))
        ).scalar_one()
        viewer = User(
            username="sm_inv_viewer",
            password_hash=get_password_hash("pw"),
            is_active=True,
        )
        viewer.groups.append(viewer_group)
        db_session.add(viewer)
        await db_session.commit()
        return create_access_token(data={"sub": viewer.username})

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        "method,path,payload",
        [
            ("POST", "/api/v1/spoolman/inventory/spools", {"material": "PLA", "label_weight": 1000, "weight_used": 0}),
            (
                "POST",
                "/api/v1/spoolman/inventory/spools/bulk",
                {"spool": {"material": "PLA", "label_weight": 1000, "weight_used": 0}, "quantity": 1},
            ),
            ("PATCH", "/api/v1/spoolman/inventory/spools/42", {"note": "x"}),
            ("DELETE", "/api/v1/spoolman/inventory/spools/42", None),
            ("POST", "/api/v1/spoolman/inventory/spools/42/archive", None),
            ("POST", "/api/v1/spoolman/inventory/spools/42/restore", None),
            ("PATCH", "/api/v1/spoolman/inventory/spools/42/weight", {"weight_grams": 100.0}),
        ],
    )
    async def test_write_endpoints_return_403_for_viewer(
        self,
        async_client: AsyncClient,
        viewer_token,
        method: str,
        path: str,
        payload: dict | None,
    ):
        """Viewer-group users (INVENTORY_READ, no INVENTORY_UPDATE) get 403 on write endpoints."""
        response = await async_client.request(
            method,
            path,
            json=payload,
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert response.status_code == 403, (
            f"{method} {path} should return 403 for read-only user "
            f"but got {response.status_code}: {response.json()}"
        )
