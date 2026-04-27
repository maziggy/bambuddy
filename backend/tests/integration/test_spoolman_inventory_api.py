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
    async def test_malformed_spool_skipped_in_list(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """A spool with an invalid id (e.g. 0) is silently skipped; others still appear."""
        bad_spool = {**SAMPLE_SPOOLMAN_SPOOL, "id": 0}
        mock_spoolman_client.get_all_spools.return_value = [bad_spool, SAMPLE_SPOOLMAN_SPOOL]

        response = await async_client.get("/api/v1/spoolman/inventory/spools")
        assert response.status_code == 200
        spools = response.json()
        # bad_spool is dropped; the valid one survives
        assert len(spools) == 1
        assert spools[0]["id"] == 42

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_spools_returns_503_when_spoolman_unavailable(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """GET /spoolman/inventory/spools returns 503 when Spoolman is unreachable (H10)."""
        from backend.app.services.spoolman import SpoolmanUnavailableError

        mock_spoolman_client.get_all_spools.side_effect = SpoolmanUnavailableError("down")

        response = await async_client.get("/api/v1/spoolman/inventory/spools")
        assert response.status_code == 503

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
        """DELETE returns 503 when Spoolman is unreachable."""
        from backend.app.services.spoolman import SpoolmanUnavailableError

        mock_spoolman_client.delete_spool.side_effect = SpoolmanUnavailableError("unreachable")
        response = await async_client.delete("/api/v1/spoolman/inventory/spools/42")
        assert response.status_code == 503

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_spool_not_found(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """DELETE returns 404 when Spoolman reports the spool does not exist."""
        from backend.app.services.spoolman import SpoolmanNotFoundError

        mock_spoolman_client.delete_spool.side_effect = SpoolmanNotFoundError("gone")
        response = await async_client.delete("/api/v1/spoolman/inventory/spools/42")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_spool_not_found(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """POST /archive returns 404 when Spoolman reports the spool does not exist."""
        from backend.app.services.spoolman import SpoolmanNotFoundError

        mock_spoolman_client.set_spool_archived.side_effect = SpoolmanNotFoundError("gone")
        response = await async_client.post("/api/v1/spoolman/inventory/spools/42/archive")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_restore_spool_not_found(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """POST /restore returns 404 when Spoolman reports the spool does not exist."""
        from backend.app.services.spoolman import SpoolmanNotFoundError

        mock_spoolman_client.set_spool_archived.side_effect = SpoolmanNotFoundError("gone")
        response = await async_client.post("/api/v1/spoolman/inventory/spools/42/restore")
        assert response.status_code == 404

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

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_spool_returns_404_on_not_found(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """PATCH returns 404 when update_spool_full raises SpoolmanNotFoundError (I2)."""
        from backend.app.services.spoolman import SpoolmanNotFoundError

        mock_spoolman_client.update_spool_full.side_effect = SpoolmanNotFoundError("gone")
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json={"note": "x"})
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_spool_returns_503_on_unavailable(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """PATCH returns 503 when update_spool_full raises SpoolmanUnavailableError (I2)."""
        from backend.app.services.spoolman import SpoolmanUnavailableError

        mock_spoolman_client.update_spool_full.side_effect = SpoolmanUnavailableError("down")
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json={"note": "x"})
        assert response.status_code == 503

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_sync_weight_returns_404_on_not_found(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """PATCH /weight returns 404 when update_spool_full raises SpoolmanNotFoundError (I2)."""
        from backend.app.services.spoolman import SpoolmanNotFoundError

        mock_spoolman_client.update_spool_full.side_effect = SpoolmanNotFoundError("gone")
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42/weight", json={"weight_grams": 500.0})
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_sync_weight_returns_503_on_unavailable(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """PATCH /weight returns 503 when update_spool_full raises SpoolmanUnavailableError (I2)."""
        from backend.app.services.spoolman import SpoolmanUnavailableError

        mock_spoolman_client.update_spool_full.side_effect = SpoolmanUnavailableError("down")
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42/weight", json={"weight_grams": 500.0})
        assert response.status_code == 503


class TestSpoolmanInventoryCostPerKg:
    """Tests for the two-step cost_per_kg create path (PT-C2)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_spool_with_cost_per_kg_calls_price_update(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """POST with cost_per_kg calls update_spool_full with price= after creation."""
        from unittest.mock import AsyncMock

        mock_spoolman_client.update_spool_full = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)

        payload = {
            "material": "PLA",
            "brand": "Bambu Lab",
            "label_weight": 1000,
            "cost_per_kg": 24.99,
        }
        resp = await async_client.post("/api/v1/spoolman/inventory/spools", json=payload)
        assert resp.status_code == 200
        # update_spool_full must have been called with price=24.99
        calls = [
            c
            for c in mock_spoolman_client.update_spool_full.call_args_list
            if c.kwargs.get("price") == 24.99 or (c.args and 24.99 in c.args)
        ]
        assert len(calls) >= 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_spool_without_cost_per_kg_skips_price_update(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """POST without cost_per_kg does not call update_spool_full."""
        from unittest.mock import AsyncMock

        mock_spoolman_client.update_spool_full = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)

        payload = {"material": "PLA", "brand": "Bambu Lab", "label_weight": 1000}
        resp = await async_client.post("/api/v1/spoolman/inventory/spools", json=payload)
        assert resp.status_code == 200
        mock_spoolman_client.update_spool_full.assert_not_called()


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
        """tag_uid longer than 30 chars is rejected with 422 (NFC UID max 10 bytes = 20 hex chars, capped at 30)."""
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
        """tray_uuid longer than 32 chars is rejected with 422."""
        payload = {"tray_uuid": "B" * 65}
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("uuid_len", [16, 31])
    async def test_update_rejects_tray_uuid_too_short(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
        uuid_len: int,
    ):
        """tray_uuid shorter than 32 chars is rejected (min_length=max_length=32)."""
        payload = {"tray_uuid": "A" * uuid_len}
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_rejects_rgba_nine_chars(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """rgba must be max 8 hex chars; 9-char value is rejected with 422."""
        payload = {"rgba": "FF0000FFA"}  # 9 chars
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tag_uid_below_min_length_rejected(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """tag_uid shorter than 8 hex chars is rejected with 422 (PT-I5)."""
        payload = {"tag_uid": "AABBCC"}  # 6 chars, below min_length=8
        resp = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json=payload)
        assert resp.status_code == 422

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
            "javascript:alert(1)",
            "http://169.254.169.254/latest/meta-data/",  # AWS IMDS
            "http://100.100.100.200/",  # Alibaba Cloud metadata
            "http://[fd00:ec2::254]/",  # AWS IMDS IPv6
            "http://0.0.0.0/",  # unspecified
            "http://224.0.0.1/",  # IPv4 multicast
            "http://[ff02::1]/",  # IPv6 multicast
            "http://[::ffff:169.254.169.254]/",  # IPv4-mapped IPv6 IMDS bypass
            "http://2130706433/",  # decimal-encoded 127.0.0.1
            "http://0x7f000001/",  # hex-encoded 127.0.0.1
        ],
    )
    async def test_ssrf_blocked_schemes_and_addresses(
        self,
        async_client: AsyncClient,
        db_session,
        mock_spoolman_client,
        evil_url: str,
    ):
        """SSRF: dangerous schemes, cloud metadata IPs, multicast, unspecified,
        and numeric-encoded IPs must be rejected with 400. Loopback and
        RFC-1918 private ranges are allowed — they are legitimate Spoolman
        topologies for self-hosted Bambuddy deployments."""
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
    @pytest.mark.parametrize(
        "lan_url",
        [
            "http://127.0.0.1:7912/",  # loopback
            "http://[::1]:7912/",  # IPv6 loopback
            "http://192.168.1.50:7912/",  # RFC-1918 /16
            "http://10.0.0.5:7912/",  # RFC-1918 /8
            "http://172.20.0.3:7912/",  # RFC-1918 /12
        ],
    )
    async def test_ssrf_allows_lan_spoolman_topologies(
        self,
        async_client: AsyncClient,
        db_session,
        mock_spoolman_client,
        lan_url: str,
    ):
        """Regression: Bambuddy's normal deployment is LAN-local Spoolman.
        Loopback and RFC-1918 private addresses must NOT be rejected as SSRF."""
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="spoolman_enabled", value="true"))
        db_session.add(Settings(key="spoolman_url", value=lan_url))
        await db_session.commit()

        response = await async_client.get("/api/v1/spoolman/inventory/spools")
        assert response.status_code != 400, f"LAN URL {lan_url!r} was incorrectly blocked as SSRF: {response.json()}"

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


class TestColorNamePassthrough:
    """color_name is forwarded to find_or_create_filament on create and update (B6 / T5)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_passes_color_name_to_filament(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """color_name from the create payload is forwarded to find_or_create_filament."""
        payload = {
            "material": "PLA",
            "label_weight": 1000,
            "weight_used": 0,
            "color_name": "Bambu Green",
        }
        response = await async_client.post("/api/v1/spoolman/inventory/spools", json=payload)
        assert response.status_code == 200
        mock_spoolman_client.find_or_create_filament.assert_called_once()
        _, kwargs = mock_spoolman_client.find_or_create_filament.call_args
        assert kwargs.get("color_name") == "Bambu Green"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_passes_color_name_to_filament(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """color_name from the update payload is forwarded to find_or_create_filament."""
        payload = {"color_name": "Jade White"}
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json=payload)
        assert response.status_code == 200
        mock_spoolman_client.find_or_create_filament.assert_called_once()
        _, kwargs = mock_spoolman_client.find_or_create_filament.call_args
        assert kwargs.get("color_name") == "Jade White"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_omits_color_name_when_not_provided(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """When color_name is not in the PATCH payload, the existing filament color_name is used."""
        payload = {"note": "no color_name here"}
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json=payload)
        assert response.status_code == 200
        _, kwargs = mock_spoolman_client.find_or_create_filament.call_args
        # color_name falls back to current filament's color_name (which is None in test fixture)
        assert kwargs.get("color_name") is None


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
        from sqlalchemy import select

        from backend.app.core.auth import create_access_token, get_password_hash
        from backend.app.models.group import Group
        from backend.app.models.settings import Settings
        from backend.app.models.user import User

        db_session.add(Settings(key="spoolman_enabled", value="true"))
        db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
        db_session.add(Settings(key="auth_enabled", value="true"))
        await db_session.commit()

        viewer_group = (await db_session.execute(select(Group).where(Group.name == "Viewers"))).scalar_one()
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
            f"{method} {path} should return 403 for read-only user but got {response.status_code}: {response.json()}"
        )
        # Error body must mention the permission string so a "banned-user middleware"
        # regression (generic 403 with no permission context) doesn't pass silently.
        detail = response.json().get("detail", "")
        assert "inventory:update" in detail, f"Expected 'inventory:update' in 403 detail but got: {detail!r}"


# ---------------------------------------------------------------------------
# Additional regression tests for second-round review items
# ---------------------------------------------------------------------------


class TestSpoolmanInventorySecurityExtras:
    """Additional security/validation tests added in second review round."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_rejects_double_hash_rgba(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """SEC-3: rgba like '##FF0000' (double hash) must be rejected with 422."""
        payload = {"material": "PLA", "label_weight": 1000, "weight_used": 0, "rgba": "##FF0000"}
        response = await async_client.post("/api/v1/spoolman/inventory/spools", json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("spool_id", [0, -1])
    async def test_path_param_non_positive_spool_id_returns_422(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
        spool_id: int,
    ):
        """SEC-5: /spools/0 and /spools/-1 must be rejected with 422 (Path gt=0)."""
        response = await async_client.get(f"/api/v1/spoolman/inventory/spools/{spool_id}")
        assert response.status_code == 422, f"Expected 422 for spool_id={spool_id} but got {response.status_code}"

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        "tag_uid,expected_status",
        [
            ("A" * 30, 200),  # exactly at NFC UID cap — valid
            ("DEADBEEF12345678", 200),  # 16-char backward compat — valid
            ("A" * 31, 422),  # one over limit — rejected by Pydantic max_length=30
            ("A" * 32, 422),  # tray_uuid-length value rejected in tag_uid field
        ],
    )
    async def test_tag_uid_length_boundary(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
        tag_uid: str,
        expected_status: int,
    ):
        """tag_uid boundary — 30 chars valid (NFC UID max), 31+ rejected."""
        payload = {"tag_uid": tag_uid}
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json=payload)
        assert response.status_code == expected_status, (
            f"tag_uid len={len(tag_uid)}: expected {expected_status} but got {response.status_code}"
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_create_partial_failure_returns_207(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """I9: bulk create with quantity=3 where middle call fails → 207 Multi-Status."""
        from backend.app.services.spoolman import SpoolmanUnavailableError

        results = [SAMPLE_SPOOLMAN_SPOOL, SpoolmanUnavailableError("Spoolman down"), SAMPLE_SPOOLMAN_SPOOL]
        mock_spoolman_client.create_spool.side_effect = results

        payload = {
            "spool": {"material": "PLA", "label_weight": 1000, "weight_used": 0},
            "quantity": 3,
        }
        response = await async_client.post("/api/v1/spoolman/inventory/spools/bulk", json=payload)
        assert response.status_code == 207, (
            f"Expected 207 Multi-Status for partial failure but got {response.status_code}"
        )
        body = response.json()
        assert isinstance(body, dict)
        assert body["requested_count"] == 3
        assert body["failed_count"] == 1
        assert len(body["created"]) == 2


class TestTagClearPreservesExtraKeys:
    """Regression test: clearing tag_uid must not wipe unrelated Spoolman extra fields."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tag_clear_preserves_custom_extra_key(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """PATCH tag_uid='' must preserve unrelated keys in Spoolman extra dict."""
        spool_with_extra = {
            **SAMPLE_SPOOLMAN_SPOOL,
            "extra": {"tag": '"AABBCCDDEEFF0011AABBCCDDEEFF0011"', "custom_key": "keep_me"},
        }
        mock_spoolman_client.get_spool = AsyncMock(return_value=spool_with_extra)
        mock_spoolman_client.update_spool_full = AsyncMock(return_value=spool_with_extra)

        response = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42",
            json={"tag_uid": None},
        )
        assert response.status_code == 200

        mock_spoolman_client.update_spool_full.assert_called_once()
        _, kwargs = mock_spoolman_client.update_spool_full.call_args
        sent_extra = kwargs.get("extra")
        assert sent_extra is not None, "extra must be sent when tag is cleared"
        assert "tag" not in sent_extra, "tag key must be removed when tag_uid is cleared"
        assert sent_extra.get("custom_key") == "keep_me", "unrelated extra keys must survive"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tag_clear_refetches_spool_inside_lock(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """B7: tag-clear does a fresh get_spool() re-fetch inside the lock, not the stale one.

        Simulates a write that changes extra between the initial get_spool (used for
        other field resolution) and the lock acquisition.  The extra sent to
        update_spool_full must come from the second (in-lock) fetch, not the first.
        """
        stale_extra = {"tag": '"AABBCCDD"', "custom_key": "stale_value"}
        fresh_extra = {"tag": '"AABBCCDD"', "custom_key": "fresh_value"}

        stale_spool = {**SAMPLE_SPOOLMAN_SPOOL, "extra": stale_extra}
        fresh_spool = {**SAMPLE_SPOOLMAN_SPOOL, "extra": fresh_extra}

        # First call returns stale; second call (inside lock) returns fresh
        mock_spoolman_client.get_spool = AsyncMock(side_effect=[stale_spool, fresh_spool])
        mock_spoolman_client.update_spool_full = AsyncMock(return_value=fresh_spool)

        response = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42",
            json={"tag_uid": None, "tray_uuid": None},
        )
        assert response.status_code == 200

        # get_spool called twice: once for field resolution, once for fresh extra fetch
        assert mock_spoolman_client.get_spool.call_count == 2

        _, kwargs = mock_spoolman_client.update_spool_full.call_args
        sent_extra = kwargs.get("extra")
        assert sent_extra is not None
        assert "tag" not in sent_extra
        # custom_key must come from the fresh re-fetch, not the stale first fetch
        assert sent_extra.get("custom_key") == "fresh_value"


class TestSpoolmanInventorySSRFSpoolBuddyPath:
    """SSRF tests for _get_spoolman_client_or_none (nfc/* and scale/ endpoints)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        "evil_url",
        [
            "file:///etc/passwd",
            "http://169.254.169.254/latest/meta-data/",  # AWS IMDS
            "http://0.0.0.0/",  # unspecified
            "http://[::ffff:169.254.169.254]/",  # IPv4-mapped IMDS bypass
        ],
    )
    async def test_nfc_tag_scanned_with_ssrf_url_ignores_spoolman(
        self,
        async_client: AsyncClient,
        db_session,
        evil_url: str,
    ):
        """SSRF: _get_spoolman_client_or_none silently disables Spoolman for unsafe URLs
        on the SpoolBuddy NFC path (tag-scanned broadcasts unknown_tag, not 400)."""
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="spoolman_enabled", value="true"))
        db_session.add(Settings(key="spoolman_url", value=evil_url))
        await db_session.commit()

        from unittest.mock import AsyncMock, patch

        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                "/api/v1/spoolbuddy/nfc/tag-scanned",
                json={"device_id": "sb-ssrf", "tag_uid": "AABBCCDD"},
            )

        # Must not crash or proxy the SSRF URL — unknown_tag is the safe degraded response
        assert resp.status_code == 200
        if mock_ws.broadcast.called:
            msg = mock_ws.broadcast.call_args[0][0]
            assert msg["type"] == "spoolbuddy_unknown_tag"

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        "evil_url",
        [
            "http://169.254.169.254/latest/meta-data/",  # AWS IMDS
            "http://[::ffff:169.254.169.254]/",  # IPv4-mapped IMDS bypass
        ],
    )
    async def test_nfc_write_result_with_ssrf_url_degrades_gracefully(
        self,
        async_client: AsyncClient,
        db_session,
        evil_url: str,
    ):
        """SSRF: write-result with unsafe Spoolman URL must not proxy to the evil host.

        write-result calls Spoolman to write-back the tag UID when data_origin='spoolman'.
        With an SSRF URL, _get_spoolman_client_or_none returns None so the call is skipped
        and the route returns 502 (tag written but link not persisted — not a server crash).
        """
        import json as _json

        from backend.app.models.settings import Settings
        from backend.app.models.spoolbuddy_device import SpoolBuddyDevice

        db_session.add(Settings(key="spoolman_enabled", value="true"))
        db_session.add(Settings(key="spoolman_url", value=evil_url))
        # Register the device so the route doesn't 404 before reaching the SSRF guard.
        db_session.add(
            SpoolBuddyDevice(
                device_id="sb-ssrf-wr",
                hostname="sb-ssrf-wr.local",
                ip_address="127.0.0.1",
                pending_command="write_tag",
                pending_write_payload=_json.dumps({"spool_id": 99, "ndef_data_hex": "DEAD", "data_origin": "spoolman"}),
            )
        )
        await db_session.commit()

        from unittest.mock import AsyncMock, patch

        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                "/api/v1/spoolbuddy/nfc/write-result",
                json={
                    "device_id": "sb-ssrf-wr",
                    "spool_id": 99,
                    "tag_uid": "AABBCCDD",
                    "success": True,
                },
            )

        # 502 = tag written to NFC but Spoolman link not persisted (SSRF guard blocked it).
        # Must not be 500 (crash) and must not have proxied to the evil host.
        assert resp.status_code == 502

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        "evil_url",
        [
            "http://169.254.169.254/latest/meta-data/",  # AWS IMDS
        ],
    )
    async def test_scale_update_weight_with_ssrf_url_degrades_gracefully(
        self,
        async_client: AsyncClient,
        db_session,
        evil_url: str,
    ):
        """SSRF: scale weight update with unsafe Spoolman URL must not proxy to the evil host."""
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="spoolman_enabled", value="true"))
        db_session.add(Settings(key="spoolman_url", value=evil_url))
        await db_session.commit()

        from unittest.mock import AsyncMock, patch

        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                "/api/v1/spoolbuddy/scale/update-spool-weight",
                json={"device_id": "sb-ssrf-scale", "spool_id": 1, "weight_grams": 500.0},
            )

        # Must not crash or proxy to an SSRF host
        assert resp.status_code in (200, 404, 422)


class TestMergeSpoolExtraPreservesKeys:
    """Unit-level test for merge_spool_extra key preservation (via mocked Spoolman)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_merge_preserves_unrelated_extra_keys(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """merge_spool_extra must deep-merge rather than overwrite the extra dict.

        Seed extra={"custom_key": "keep_me", "tag": "old"}.
        After merging {"tag": "new"}, the PATCH payload must still contain custom_key.
        """
        from unittest.mock import AsyncMock, patch

        existing_spool = {
            **SAMPLE_SPOOLMAN_SPOOL,
            "extra": {"custom_key": "keep_me", "tag": '"old"'},
        }
        updated_spool = {**existing_spool, "extra": {"custom_key": "keep_me", "tag": '"new"'}}

        mock_client = mock_spoolman_client
        mock_client.get_spool = AsyncMock(return_value=existing_spool)
        mock_client.update_spool_full = AsyncMock(return_value=updated_spool)

        # Call merge_spool_extra directly through the service
        from backend.app.services.spoolman import SpoolmanClient

        client = SpoolmanClient.__new__(SpoolmanClient)
        client.base_url = "http://localhost:7912"
        client.api_url = "http://localhost:7912/api/v1"
        client._extra_locks = {}

        async def _mock_get(spool_id):
            return existing_spool

        async def _mock_update(spool_id, **kwargs):
            # Capture what was actually sent
            _mock_update.captured_extra = kwargs.get("extra")
            return updated_spool

        _mock_update.captured_extra = None
        client.get_spool = _mock_get
        client.update_spool_full = _mock_update

        result = await client.merge_spool_extra(42, {"tag": '"new"'})

        # The merged extra must include the unrelated key
        assert _mock_update.captured_extra is not None
        assert _mock_update.captured_extra.get("custom_key") == "keep_me"
        assert _mock_update.captured_extra.get("tag") == '"new"'
        assert result is not None


class TestGetClientValueError:
    """Test the ValueError branch in _get_client when init_spoolman_client fails (Gap 5)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_400_when_init_spoolman_client_raises_value_error(
        self, async_client: AsyncClient, spoolman_settings
    ):
        """If init_spoolman_client raises ValueError after SSRF check passes, return HTTP 400."""
        with (
            patch(
                "backend.app.api.routes.spoolman_inventory.get_spoolman_client",
                AsyncMock(return_value=None),
            ),
            patch(
                "backend.app.api.routes.spoolman_inventory.init_spoolman_client",
                AsyncMock(side_effect=ValueError("unsupported scheme")),
            ),
        ):
            resp = await async_client.get("/api/v1/spoolman/inventory/spools")
        assert resp.status_code == 400
        assert "unsupported scheme" in resp.json()["detail"]


class TestBulkCreateWithPriceFailure:
    """Test that bulk create succeeds even when price update fails (Gap 6)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_create_succeeds_when_price_update_fails(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """Bulk create returns 200 even if update_spool_full (price) raises SpoolmanUnavailableError."""
        from backend.app.services.spoolman import SpoolmanUnavailableError

        mock_spoolman_client.update_spool_full = AsyncMock(side_effect=SpoolmanUnavailableError("price server down"))
        mock_spoolman_client.create_spool = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)

        payload = {
            "spool": {
                "material": "PLA",
                "brand": "Bambu Lab",
                "label_weight": 1000,
                "cost_per_kg": 19.99,
            },
            "quantity": 2,
        }
        resp = await async_client.post("/api/v1/spoolman/inventory/spools/bulk", json=payload)
        # Price update failure must not abort the bulk create
        assert resp.status_code in (200, 207)
        # Both spools must have been created
        assert mock_spoolman_client.create_spool.call_count == 2
        # Price update was attempted for each
        assert mock_spoolman_client.update_spool_full.call_count == 2
