"""Integration tests for the Spoolman inventory proxy endpoints.

These tests verify that /api/v1/spoolman/inventory/spools/* correctly
translates between Spoolman's data model and Bambuddy's InventorySpool format.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
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


class TestSpoolmanInventorySlicerFilament:
    """slicer_filament persistence via Spoolman extra dict.

    Spoolman has no native slicer_filament field — Bambuddy persists the
    BambuStudio preset under bambu_slicer_filament[_name] keys in the
    spool's extra dict and unwraps them in _map_spoolman_spool. Without
    this round-trip the user's slicer-preset selection on the spool form
    is silently dropped (#1114).
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_persists_slicer_filament_to_extra(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """PATCH with slicer_filament writes bambu_slicer_filament to extra.

        Spoolman's PATCH MERGES extra keys, so we send via merge_spool_extra
        not update_spool_full. Values are JSON-encoded strings.
        """
        import json as _json

        mock_spoolman_client.ensure_extra_field = AsyncMock(return_value=True)

        response = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42",
            json={
                "slicer_filament": "PFUSf543b298f8ea66",
                "slicer_filament_name": "Devil Design PLA Basic @Bambu Lab H2D 0.4 nozzle (Custom)",
            },
        )
        assert response.status_code == 200
        # Field registration is idempotent — must be called for each key
        ensure_calls = [c.args[0] for c in mock_spoolman_client.ensure_extra_field.call_args_list]
        assert "bambu_slicer_filament" in ensure_calls
        assert "bambu_slicer_filament_name" in ensure_calls
        # Values must be JSON-encoded so read-side can json.loads + .strip('"')
        mock_spoolman_client.merge_spool_extra.assert_called_once_with(
            42,
            {
                "bambu_slicer_filament": _json.dumps("PFUSf543b298f8ea66"),
                "bambu_slicer_filament_name": _json.dumps("Devil Design PLA Basic @Bambu Lab H2D 0.4 nozzle (Custom)"),
            },
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_without_slicer_filament_skips_merge(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """PATCH without slicer_filament fields must not call merge_spool_extra.

        Avoids overwriting an existing preset with empty/null when the user
        just changed an unrelated field (e.g. note, weight).
        """
        response = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42",
            json={"note": "just changing the note"},
        )
        assert response.status_code == 200
        mock_spoolman_client.merge_spool_extra.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_clears_slicer_filament_with_empty_string(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """Empty-string slicer_filament writes the JSON-encoded "" sentinel.

        The read-side strip('"') resolves it to an empty string and falls
        back to filament.name — matches the user-facing "clear preset" flow.
        """
        import json as _json

        mock_spoolman_client.ensure_extra_field = AsyncMock(return_value=True)

        response = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42",
            json={"slicer_filament": "", "slicer_filament_name": ""},
        )
        assert response.status_code == 200
        mock_spoolman_client.merge_spool_extra.assert_called_once_with(
            42,
            {
                "bambu_slicer_filament": _json.dumps(""),
                "bambu_slicer_filament_name": _json.dumps(""),
            },
        )


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

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_omitting_storage_location_does_not_write_location_to_spoolman(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_spoolman_client,
    ):
        """PATCH without storage_location in the payload must not touch Spoolman's location field.

        Regression test for the round-trip bug: opening the edit modal and saving without
        changing the location would previously echo the current Spoolman value back
        (storage_location_changed=False branch used current.get("location") instead of None).
        """
        # Payload deliberately omits storage_location — simulates saving the modal
        # without touching that field.
        payload = {"note": "just updating the note"}
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json=payload)
        assert response.status_code == 200
        mock_spoolman_client.update_spool_full.assert_called_once()
        _, kwargs = mock_spoolman_client.update_spool_full.call_args
        # location must be None so update_spool_full skips writing the field entirely
        assert kwargs.get("location") is None
        # clear_location must also be False — we are not explicitly clearing it either
        assert kwargs.get("clear_location") is False


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
            # After B1 fix: non-null tag_uid on PATCH /spools/{id} is rejected (use /tag endpoint)
            ("A" * 30, 422),  # non-null → 422 (use /tag endpoint instead)
            ("DEADBEEF12345678", 422),  # non-null → 422 regardless of length
            ("A" * 31, 422),  # exceeds max_length — also 422
            ("A" * 32, 422),  # tray_uuid-length value — also 422
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
        """tag_uid on PATCH /spools/{id} — all non-null values are rejected (B1 fix; use /tag endpoint)."""
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
        """PATCH tag_uid=None clears tag without dropping unrelated extra keys.

        Spoolman PATCHes the extra dict by MERGING — popping a key from the
        dict and sending the rest doesn't actually clear it. The endpoint
        sets tag = json.dumps("") explicitly; read-side filters strip the
        wrapping quotes and treat the empty string as "no tag" (#1114).
        """
        import json as _json

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
        assert sent_extra.get("tag") == _json.dumps(""), (
            "tag must be set to JSON empty-string sentinel (Spoolman PATCH merges; "
            "popping the key would leave the previous value in place)"
        )
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

        import json as _json

        _, kwargs = mock_spoolman_client.update_spool_full.call_args
        sent_extra = kwargs.get("extra")
        assert sent_extra is not None
        # Tag is set to the JSON empty-string sentinel (not popped) — Spoolman
        # PATCH merges, so popping the key would leave the previous value.
        assert sent_extra.get("tag") == _json.dumps("")
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
    """Test that bulk create handles price-update failures per C1/C8 semantics."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_create_price_503_moves_spool_to_failures(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """When price update fails (503), the spool goes to failures — overall returns 207 if at least one succeeds."""
        from backend.app.services.spoolman import SpoolmanUnavailableError

        # First price update fails (SpoolmanUnavailableError → 503), second succeeds
        mock_spoolman_client.update_spool_full = AsyncMock(
            side_effect=[SpoolmanUnavailableError("price server down"), SAMPLE_SPOOLMAN_SPOOL]
        )
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
        # One spool succeeded, one failed (price 503) → 207 Partial
        assert resp.status_code == 207
        data = resp.json()
        assert len(data["created"]) == 1
        assert data["failed_count"] == 1
        # Both Spoolman creates were attempted
        assert mock_spoolman_client.create_spool.call_count == 2
        # Both price updates were attempted
        assert mock_spoolman_client.update_spool_full.call_count == 2


class TestSpoolTagLinkValidation:
    """NEW-B1: /spools/{id}/tag endpoint validates tag_uid length and content."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tag_uid_6_chars_rejected(self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client):
        """tag_uid with 6 hex chars is rejected — minimum is 8 chars (4-byte UID)."""
        resp = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42/tag",
            json={"tag_uid": "AABBCC"},  # 6 chars — below new minimum
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tag_uid_all_zeros_rejected(self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client):
        """tag_uid that is all-zero bytes is rejected as an unwritten/blank tag."""
        resp = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42/tag",
            json={"tag_uid": "00000000000000"},  # 14 zeros
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tag_uid_valid_14_chars_accepted(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """tag_uid with 14 valid hex chars (7-byte UID) is accepted."""
        # This tag is not in SAMPLE_SPOOLMAN_SPOOL so no duplicate conflict.
        resp = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42/tag",
            json={"tag_uid": "AABBCCDD112233"},  # 14 chars, valid, not all-zeros
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tag_uid_8_chars_accepted(self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client):
        """tag_uid with 8 hex chars (4-byte Bambu Lab NFC UID) is accepted after min_length fix."""
        resp = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42/tag",
            json={"tag_uid": "2728C17B"},  # 8 chars — real Bambu Lab 4-byte hardware UID
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tag_uid_8_zeros_rejected(self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client):
        """tag_uid with 8 zero chars is rejected — all-zeros validator applies at the new minimum."""
        resp = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42/tag",
            json={"tag_uid": "00000000"},  # 8 zeros — meets min_length but is a blank/unwritten tag
        )
        assert resp.status_code == 422


class TestLinkTagDuplicate:
    """NEW-I1: /spools/{id}/tag returns 409 when another spool already has the same tag."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_link_tag_returns_200_when_tag_not_on_another_spool(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """Linking a fresh tag to spool 42 returns 200 — no duplicate in Spoolman."""
        resp = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42/tag",
            json={"tag_uid": "AABBCCDD112233"},  # not in SAMPLE_SPOOLMAN_SPOOL
        )
        assert resp.status_code == 200
        mock_spoolman_client.update_spool_full.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_link_tag_returns_409_when_same_tag_on_different_spool(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """Linking spool 99 to a tag that spool 42 already carries must return 409."""
        # SAMPLE_SPOOLMAN_SPOOL (id=42) has extra.tag = '"AABBCCDDEEFF0011AABBCCDDEEFF0011"'.
        # Attempting to assign the same tag to spool 99 must be rejected.
        resp = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/99/tag",
            json={"tray_uuid": "AABBCCDDEEFF0011AABBCCDDEEFF0011"},  # 32-char tray UUID
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "42" in str(detail)


class TestSpoolmanInventoryUpdateCoreWeight:
    """core_weight is accepted for schema parity but not persisted — any value should be accepted."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_core_weight_other_than_250_accepted(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """PATCH with core_weight != 250 is accepted (field is ignored server-side, not rejected)."""
        resp = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42",
            json={"core_weight": 100},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_core_weight_250_explicitly_is_accepted(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """PATCH with core_weight=250 (the default) is valid and returns 200."""
        resp = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42",
            json={"core_weight": 250},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_without_core_weight_is_accepted(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """PATCH without core_weight (omitted) must not trigger the validator — returns 200."""
        resp = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42",
            json={"note": "no core_weight key"},
        )
        assert resp.status_code == 200


class TestUnlinkSpool:
    """POST /spoolman/spools/{id}/unlink clears Spoolman tag without re-entrant lock deadlock.

    Spoolman PATCHes the extra dict by MERGING — popping a key + sending the
    rest doesn't clear the popped key. The endpoint sends the JSON empty-string
    sentinel ('""') which the read-side filters strip. (#1114)

    The endpoint uses merge_spool_extra (not update_spool_full directly)
    because (a) merge_spool_extra owns the per-spool extra_lock for atomic
    read-modify-write semantics, and (b) wrapping it in another extra_lock
    would deadlock — asyncio.Lock is not re-entrant.
    """

    @pytest.fixture
    def mock_unlink_client(self):
        """Mock Spoolman client for the spoolman.py (non-inventory) route."""
        spool_with_tag = {
            **SAMPLE_SPOOLMAN_SPOOL,
            "extra": {"tag": '"AABBCCDDEEFF0011AABBCCDDEEFF0011"', "custom": "keep"},
        }
        mock_client = MagicMock()
        mock_client.base_url = "http://localhost:7912"
        mock_client.health_check = AsyncMock(return_value=True)
        mock_client.get_spool = AsyncMock(return_value=spool_with_tag)
        # merge_spool_extra returns the spool with the tag cleared (and custom
        # preserved) — that's what the read-side will see after the fix.
        mock_client.merge_spool_extra = AsyncMock(
            return_value={**spool_with_tag, "extra": {"tag": '""', "custom": "keep"}}
        )

        with (
            patch(
                "backend.app.api.routes.spoolman.get_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
            patch(
                "backend.app.api.routes.spoolman.init_spoolman_client",
                AsyncMock(return_value=mock_client),
            ),
        ):
            yield mock_client

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unlink_sets_tag_to_json_empty_string(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_unlink_client,
    ):
        """Unlink calls merge_spool_extra with the JSON-empty-string sentinel.

        Pre-fix the endpoint did `cur_extra.pop("tag")` then PATCHed the rest.
        Spoolman silently kept the previous tag because the key wasn't in the
        payload (PATCH merges). Now the endpoint sends `{"tag": '""'}` and
        the read-side .strip('"') resolves it to "" → spool drops out of
        get_linked_spools.
        """
        import json as _json

        resp = await async_client.post("/api/v1/spoolman/spools/42/unlink")
        assert resp.status_code == 200

        mock_unlink_client.merge_spool_extra.assert_called_once_with(42, {"tag": _json.dumps("")})

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unlink_preserves_other_extra_keys(
        self,
        async_client: AsyncClient,
        spoolman_settings,
        mock_unlink_client,
    ):
        """Unrelated extra keys must survive unlink.

        merge_spool_extra is responsible for the merge (read current → merge
        new fields → PATCH). The unlink endpoint only sends `{"tag": ...}`,
        so any other extra key on the spool is automatically preserved by
        merge_spool_extra's read-merge-write semantics.
        """
        resp = await async_client.post("/api/v1/spoolman/spools/42/unlink")
        assert resp.status_code == 200

        # The endpoint passes only the tag key — merge_spool_extra does the
        # rest. We don't assert anything about `custom` on the call args
        # because the route doesn't see / pass it.
        _, args, _ = mock_unlink_client.merge_spool_extra.mock_calls[0]
        sent_fields = args[1] if len(args) >= 2 else {}
        assert sent_fields == {"tag": '""'}, "unlink should only send the tag key — merge_spool_extra does the merge"


# ---------------------------------------------------------------------------
# B1: GET /spoolman/inventory/filaments
# B2: POST /spools with spoolman_filament_id bypasses find_or_create_filament
# ---------------------------------------------------------------------------

SAMPLE_FILAMENT_DICT = {
    "id": 7,
    "name": "PLA Basic",
    "material": "PLA",
    "color_hex": "FF0000",
    "color_name": "Red",
    "weight": 1000,
    "spool_weight": 196,
    "vendor": {"id": 3, "name": "Bambu Lab"},
}


class TestListSpoolmanFilaments:
    """Tests for GET /api/v1/spoolman/inventory/filaments (B1)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_filaments_disabled_returns_400(self, async_client: AsyncClient):
        """Without Spoolman enabled the endpoint returns 400."""
        resp = await async_client.get("/api/v1/spoolman/inventory/filaments")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_filaments_unreachable_returns_503(self, async_client: AsyncClient, spoolman_settings):
        """503 is returned when _get_client raises HTTPException(503)."""
        with patch(
            "backend.app.api.routes.spoolman_inventory._get_client",
            AsyncMock(side_effect=HTTPException(status_code=503, detail="Spoolman server is not reachable")),
        ):
            resp = await async_client.get("/api/v1/spoolman/inventory/filaments")
        assert resp.status_code == 503

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_filaments_success(self, async_client: AsyncClient, spoolman_settings):
        """Success path returns normalised filament list including spool_weight."""
        mock_client = MagicMock()
        mock_client.get_filaments = AsyncMock(return_value=[SAMPLE_FILAMENT_DICT])
        with patch(
            "backend.app.api.routes.spoolman_inventory._get_client",
            AsyncMock(return_value=mock_client),
        ):
            resp = await async_client.get("/api/v1/spoolman/inventory/filaments")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        entry = data[0]
        assert entry["id"] == 7
        assert entry["material"] == "PLA"
        assert entry["spool_weight"] == 196
        assert entry["vendor"]["name"] == "Bambu Lab"


class TestCreateSpoolWithFilamentId:
    """Tests for POST /api/v1/spoolman/inventory/spools with spoolman_filament_id (B2)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_with_filament_id_skips_find_or_create(self, async_client: AsyncClient, spoolman_settings):
        """When spoolman_filament_id is provided, find_or_create_filament must NOT be called."""
        mock_client = MagicMock()
        mock_client.find_or_create_filament = AsyncMock(return_value=7)
        mock_client.create_spool = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)
        mock_client.update_spool_full = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)
        with patch(
            "backend.app.api.routes.spoolman_inventory._get_client",
            AsyncMock(return_value=mock_client),
        ):
            resp = await async_client.post(
                "/api/v1/spoolman/inventory/spools",
                json={"spoolman_filament_id": 7},
            )

        assert resp.status_code == 200
        mock_client.find_or_create_filament.assert_not_called()
        mock_client.create_spool.assert_called_once()
        _, kwargs = mock_client.create_spool.call_args
        assert kwargs.get("filament_id") == 7

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_with_invalid_filament_id_returns_404(self, async_client: AsyncClient, spoolman_settings):
        """An invalid spoolman_filament_id (not in Spoolman) must return 404."""
        from backend.app.services.spoolman import SpoolmanNotFoundError

        mock_client = MagicMock()
        mock_client.create_spool = AsyncMock(side_effect=SpoolmanNotFoundError("filament not found"))
        with patch(
            "backend.app.api.routes.spoolman_inventory._get_client",
            AsyncMock(return_value=mock_client),
        ):
            resp = await async_client.post(
                "/api/v1/spoolman/inventory/spools",
                json={"spoolman_filament_id": 9999},
            )

        assert resp.status_code == 404
        assert "9999" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# WICHTIG-12: Additional edge-case tests
# ---------------------------------------------------------------------------


class TestBulkCreateWithFilamentId:
    """Bulk create with spoolman_filament_id skips find_or_create_filament."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_create_with_filament_id_skips_find_or_create(
        self, async_client: AsyncClient, spoolman_settings
    ):
        """Bulk POST with spoolman_filament_id must NOT call find_or_create_filament."""
        mock_client = MagicMock()
        mock_client.find_or_create_filament = AsyncMock(return_value=7)
        mock_client.create_spool = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)
        mock_client.update_spool_full = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)
        with patch(
            "backend.app.api.routes.spoolman_inventory._get_client",
            AsyncMock(return_value=mock_client),
        ):
            resp = await async_client.post(
                "/api/v1/spoolman/inventory/spools/bulk",
                json={"spool": {"spoolman_filament_id": 7}, "quantity": 2},
            )

        assert resp.status_code == 200
        mock_client.find_or_create_filament.assert_not_called()
        assert mock_client.create_spool.call_count == 2
        for call in mock_client.create_spool.call_args_list:
            _, kwargs = call
            assert kwargs.get("filament_id") == 7


class TestCreateSpoolValidation:
    """Validation edge cases for SpoolmanInventoryCreate."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_spool_filament_id_zero_returns_422(self, async_client: AsyncClient, spoolman_settings):
        """spoolman_filament_id=0 must fail Field(gt=0) validation → 422."""
        resp = await async_client.post(
            "/api/v1/spoolman/inventory/spools",
            json={"spoolman_filament_id": 0},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_spool_without_material_or_filament_id_returns_422(
        self, async_client: AsyncClient, spoolman_settings
    ):
        """Neither material nor spoolman_filament_id → model_validator must reject → 422."""
        resp = await async_client.post(
            "/api/v1/spoolman/inventory/spools",
            json={"label_weight": 1000},
        )
        assert resp.status_code == 422


class TestNormalizeFilament:
    """Unit-style tests for _normalize_filament helper (imported directly)."""

    def test_normalize_filament_null_vendor(self):
        from backend.app.api.routes.spoolman_inventory import _normalize_filament

        result = _normalize_filament({"id": 5, "name": "PLA", "vendor": None})
        assert result is not None
        assert result["vendor"] is None

    def test_normalize_filament_null_id_returns_none(self):
        from backend.app.api.routes.spoolman_inventory import _normalize_filament

        result = _normalize_filament({"id": None, "name": "PLA"})
        assert result is None

    def test_normalize_filament_zero_id_returns_none(self):
        from backend.app.api.routes.spoolman_inventory import _normalize_filament

        result = _normalize_filament({"id": 0, "name": "PLA"})
        assert result is None


# ---------------------------------------------------------------------------
# F1: TestTranslateSpoolmanErrors — 502/404/503 paths through _translate_spoolman_errors
# ---------------------------------------------------------------------------


class TestTranslateSpoolmanErrors:
    """F1: _translate_spoolman_errors() maps Spoolman exceptions to HTTP codes."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spoolman_not_found_returns_404(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """SpoolmanNotFoundError from get_spool → 404."""
        from backend.app.services.spoolman import SpoolmanNotFoundError

        mock_spoolman_client.get_spool.side_effect = SpoolmanNotFoundError("spool 999 not found")
        resp = await async_client.get("/api/v1/spoolman/inventory/spools/999")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spoolman_unavailable_returns_503(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """SpoolmanUnavailableError from get_spool → 503."""
        from backend.app.services.spoolman import SpoolmanUnavailableError

        mock_spoolman_client.get_spool.side_effect = SpoolmanUnavailableError("network error")
        resp = await async_client.get("/api/v1/spoolman/inventory/spools/42")
        assert resp.status_code == 503

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spoolman_client_error_returns_502_with_upstream_status(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """SpoolmanClientError from get_spool → 502 with upstream_status in body."""
        from backend.app.services.spoolman import SpoolmanClientError

        mock_spoolman_client.get_spool.side_effect = SpoolmanClientError("Spoolman rejected", 422, "filament not found")
        resp = await async_client.get("/api/v1/spoolman/inventory/spools/42")
        assert resp.status_code == 502
        body = resp.json()
        assert body["detail"]["upstream_status"] == 422
        assert body["detail"]["upstream_body"] == "filament not found"


# ---------------------------------------------------------------------------
# F2: _get_client health_check returns False → 503
# ---------------------------------------------------------------------------


class TestGetClientHealthCheckFalse:
    """F2: _get_client raises 503 when health_check() returns False."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_503_when_health_check_returns_false(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """health_check() → False should produce 503 on any inventory call."""
        import time

        import backend.app.api.routes.spoolman_inventory as inv_module

        mock_spoolman_client.health_check = AsyncMock(return_value=False)
        # Clear the TTL cache so health_check is actually called
        inv_module._health_check_cache.clear()
        resp = await async_client.get("/api/v1/spoolman/inventory/spools")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# F3: SpoolTagLinkRequest both fields null → 422
# ---------------------------------------------------------------------------


class TestSpoolTagLinkBothNull:
    """F3: /spools/{id}/tag with both tag_uid and tray_uuid null → 422."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_both_null_returns_422(self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client):
        """Sending {} (both fields absent) → at_least_one validator → 422."""
        resp = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42/tag",
            json={},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_both_explicitly_null_returns_422(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """Sending {tag_uid: null, tray_uuid: null} → at_least_one validator → 422."""
        resp = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42/tag",
            json={"tag_uid": None, "tray_uuid": None},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# F5: RBAC lists — missing endpoints
# ---------------------------------------------------------------------------


class TestSpoolmanInventoryAuthExtended:
    """F5: Additional endpoints in RBAC auth/403 parametrize lists."""

    @pytest.fixture
    async def auth_and_spoolman_settings(self, db_session):
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
            ("PATCH", "/api/v1/spoolman/inventory/spools/42/tag", {"tag_uid": "AABBCCDDEE112233"}),
            ("POST", "/api/v1/spoolman/inventory/sync-ams-weights", {"printer_id": 1, "ams_data": []}),
            ("PATCH", "/api/v1/spoolman/inventory/filaments/7", {"spool_weight": 196.0}),
        ],
    )
    async def test_extended_write_endpoints_require_auth(
        self,
        async_client: AsyncClient,
        auth_and_spoolman_settings,
        method: str,
        path: str,
        payload: dict | None,
    ):
        """Additional write endpoints return 401 when auth is enabled and no token is provided."""
        resp = await async_client.request(method, path, json=payload)
        assert resp.status_code == 401, f"{method} {path} should require auth but got {resp.status_code}: {resp.json()}"

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/v1/spoolman/inventory/filaments"),
        ],
    )
    async def test_extended_read_endpoints_require_auth(
        self,
        async_client: AsyncClient,
        auth_and_spoolman_settings,
        method: str,
        path: str,
    ):
        """Additional read endpoints return 401 when auth is enabled and no token is provided."""
        resp = await async_client.request(method, path)
        assert resp.status_code == 401, f"{method} {path} should require auth but got {resp.status_code}: {resp.json()}"


# ---------------------------------------------------------------------------
# F8: _normalize_filament negative ID returns None
# ---------------------------------------------------------------------------


class TestNormalizeFilamentNegativeId:
    """F8: _normalize_filament with negative id → None (was only checking == 0)."""

    def test_normalize_filament_negative_id_returns_none(self):
        from backend.app.api.routes.spoolman_inventory import _normalize_filament

        result = _normalize_filament({"id": -1, "name": "PLA"})
        assert result is None

    def test_normalize_filament_large_negative_id_returns_none(self):
        from backend.app.api.routes.spoolman_inventory import _normalize_filament

        result = _normalize_filament({"id": -999, "name": "PLA"})
        assert result is None


# ---------------------------------------------------------------------------
# F9: weight_used > label_weight cross-field validator integration test
# ---------------------------------------------------------------------------


class TestCreateSpoolWeightValidation:
    """F9: SpoolmanInventoryCreate.validate_weight_consistency cross-field validator."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_weight_used_exceeds_label_weight_returns_422(self, async_client: AsyncClient, spoolman_settings):
        """weight_used > label_weight → cross-field validator → 422."""
        resp = await async_client.post(
            "/api/v1/spoolman/inventory/spools",
            json={"material": "PLA", "label_weight": 500, "weight_used": 600},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_weight_used_equals_label_weight_accepted(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """weight_used == label_weight is exactly at the boundary → should pass (201)."""
        resp = await async_client.post(
            "/api/v1/spoolman/inventory/spools",
            json={"material": "PLA", "label_weight": 1000, "weight_used": 1000},
        )
        # 201 or 200 (spool created)
        assert resp.status_code in (200, 201)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_spool_with_non_default_core_weight_accepted(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """A3: core_weight != 250 must no longer be rejected → 201."""
        resp = await async_client.post(
            "/api/v1/spoolman/inventory/spools",
            json={"material": "PLA", "label_weight": 1000, "weight_used": 0, "core_weight": 196},
        )
        assert resp.status_code in (200, 201)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_spool_with_non_default_core_weight_accepted(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """A3: PATCH with core_weight != 250 must no longer return 422."""
        resp = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42",
            json={"core_weight": 300},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# P8-T1: /slot-assignments/all enriches with printer_name + ams_label
# ---------------------------------------------------------------------------


class TestGetAllSlotAssignmentsEnriched:
    """P8-T1: /slot-assignments/all enriches with printer_name + ams_label.

    Regression for InventoryPage LOCATION column showing '-' for Spoolman
    spools because the endpoint only returned 4 raw fields without the
    printer_name + ams_label needed by the UI.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_printer_name_for_existing_printer(
        self, async_client: AsyncClient, db_session, spoolman_settings
    ):
        """printer_name is enriched from the joined Printer relationship."""
        from backend.app.models.printer import Printer
        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

        db_session.add(
            Printer(
                id=1,
                name="Sully",
                model="X1C",
                serial_number="SN1",
                ip_address="1.2.3.4",
                access_code="",
            )
        )
        db_session.add(
            SpoolmanSlotAssignment(
                printer_id=1,
                ams_id=0,
                tray_id=2,
                spoolman_spool_id=216,
            )
        )
        await db_session.commit()

        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as mock_pm:
            mock_pm.get_all_statuses.return_value = {}
            resp = await async_client.get("/api/v1/spoolman/inventory/slot-assignments/all")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["printer_name"] == "Sully"
        assert data[0]["spoolman_spool_id"] == 216
        assert data[0]["ams_id"] == 0
        assert data[0]["tray_id"] == 2
        assert data[0]["ams_label"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_ams_label_when_label_configured(
        self, async_client: AsyncClient, db_session, spoolman_settings
    ):
        """ams_label is enriched from AmsLabel via printer MQTT serial map."""
        from backend.app.models.ams_label import AmsLabel
        from backend.app.models.printer import Printer
        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

        db_session.add(
            Printer(
                id=1,
                name="Sully",
                model="X1C",
                serial_number="SN1",
                ip_address="1.2.3.4",
                access_code="",
            )
        )
        db_session.add(AmsLabel(ams_serial_number="ABC123", label="Top Shelf"))
        db_session.add(
            SpoolmanSlotAssignment(
                printer_id=1,
                ams_id=0,
                tray_id=2,
                spoolman_spool_id=216,
            )
        )
        await db_session.commit()

        mock_state = MagicMock(raw_data={"ams": [{"id": 0, "sn": "ABC123"}]})
        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as mock_pm:
            mock_pm.get_all_statuses.return_value = {1: mock_state}
            resp = await async_client.get("/api/v1/spoolman/inventory/slot-assignments/all")

        assert resp.status_code == 200
        assert resp.json()[0]["ams_label"] == "Top Shelf"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_synthetic_ams_label_fallback(self, async_client: AsyncClient, db_session, spoolman_settings):
        """Falls back to synthetic 'p{pid}a{ams_id}' key when no MQTT serial available."""
        from backend.app.models.ams_label import AmsLabel
        from backend.app.models.printer import Printer
        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

        db_session.add(
            Printer(
                id=1,
                name="Sully",
                model="X1C",
                serial_number="SN1",
                ip_address="1.2.3.4",
                access_code="",
            )
        )
        db_session.add(AmsLabel(ams_serial_number="p1a0", label="Synthetic Label"))
        db_session.add(
            SpoolmanSlotAssignment(
                printer_id=1,
                ams_id=0,
                tray_id=2,
                spoolman_spool_id=216,
            )
        )
        await db_session.commit()

        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as mock_pm:
            mock_pm.get_all_statuses.return_value = {}  # No live state -> synthetic key
            resp = await async_client.get("/api/v1/spoolman/inventory/slot-assignments/all")

        assert resp.json()[0]["ams_label"] == "Synthetic Label"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_filter_by_printer_id_still_works(self, async_client: AsyncClient, db_session, spoolman_settings):
        """Regression: ?printer_id=N still filters and enriches."""
        from backend.app.models.printer import Printer
        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

        for pid in (1, 2):
            db_session.add(
                Printer(
                    id=pid,
                    name=f"P{pid}",
                    model="X1C",
                    serial_number=f"SN{pid}",
                    ip_address=f"1.2.3.{pid}",
                    access_code="",
                )
            )
            db_session.add(
                SpoolmanSlotAssignment(
                    printer_id=pid,
                    ams_id=0,
                    tray_id=0,
                    spoolman_spool_id=200 + pid,
                )
            )
        await db_session.commit()

        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as mock_pm:
            mock_pm.get_all_statuses.return_value = {}
            resp = await async_client.get("/api/v1/spoolman/inventory/slot-assignments/all?printer_id=1")

        data = resp.json()
        assert len(data) == 1
        assert data[0]["printer_id"] == 1
        assert data[0]["printer_name"] == "P1"
        assert data[0]["spoolman_spool_id"] == 201
