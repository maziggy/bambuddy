"""Integration tests for PATCH /spoolman/inventory/filaments/{filament_id}.

Covers:
- Option A (keep_existing_spools=True): stamps old filament weight onto spools that currently inherit
- Option B (keep_existing_spools=False): clears per-spool overrides in Spoolman so all inherit new value
- Name-only patch: no get_all_spools call
- Edge cases: disabled Spoolman, not found, invalid inputs
- Spool-level tare priority in sync_spool_weight (spoolman_inventory) and update_spool_weight (spoolbuddy)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

SAMPLE_FILAMENT = {
    "id": 7,
    "name": "PLA Basic",
    "material": "PLA",
    "color_hex": "FF0000",
    "color_name": "Red",
    "weight": 1000,
    "spool_weight": 250.0,
    "vendor": {"id": 3, "name": "Bambu Lab"},
}

SAMPLE_SPOOL_WITH_FILAMENT_7 = {
    "id": 42,
    "spool_weight": None,  # inheriting from filament
    "filament": {"id": 7, "name": "PLA Basic", "material": "PLA", "spool_weight": 250.0, "weight": 1000},
    "remaining_weight": 750.0,
    "used_weight": 250.0,
    "location": None,
    "comment": None,
    "archived": False,
    "extra": {},
}

SAMPLE_SPOOL_WITH_FILAMENT_99 = {
    "id": 55,
    "spool_weight": 196.0,  # has its own spool-level override
    "filament": {"id": 99, "name": "PETG HF", "material": "PETG", "spool_weight": 196.0, "weight": 1000},
    "remaining_weight": 500.0,
    "used_weight": 500.0,
    "location": None,
    "comment": None,
    "archived": False,
    "extra": {},
}

SPOOL_WITH_NULL_FILAMENT = {
    "id": 77,
    "spool_weight": None,
    "filament": None,
    "remaining_weight": 100.0,
    "used_weight": 900.0,
    "location": None,
    "comment": None,
    "archived": False,
    "extra": {},
}

SAMPLE_SPOOL_7_WITH_OVERRIDE = {
    "id": 43,
    "spool_weight": 300.0,  # has its own spool-level override
    "filament": {"id": 7, "name": "PLA Basic", "material": "PLA", "spool_weight": 250.0, "weight": 1000},
    "remaining_weight": 700.0,
    "used_weight": 300.0,
    "location": None,
    "comment": None,
    "archived": False,
    "extra": {},
}


@pytest.fixture
async def spoolman_settings(db_session):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
    await db_session.commit()


def make_mock_client(filament=None, all_spools=None, patched_filament=None):
    mock_client = MagicMock()
    mock_client.base_url = "http://localhost:7912"
    mock_client.get_filament = AsyncMock(return_value=filament or SAMPLE_FILAMENT)
    mock_client.patch_filament = AsyncMock(return_value=patched_filament or SAMPLE_FILAMENT)
    mock_client.get_all_spools = AsyncMock(return_value=all_spools if all_spools is not None else [SAMPLE_SPOOL_WITH_FILAMENT_7])
    mock_client.update_spool_full = AsyncMock(return_value={})
    return mock_client


# ---------------------------------------------------------------------------
# PATCH /filaments/{id} — core scenarios
# ---------------------------------------------------------------------------

class TestPatchFilamentOptionB:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_option_b_stamps_new_weight_on_all_affected_spools(
        self, async_client: AsyncClient, spoolman_settings
    ):
        """Option B: ALL affected spools (inheriting and overridden alike) get the new weight stamped."""
        mock_client = make_mock_client(all_spools=[SAMPLE_SPOOL_WITH_FILAMENT_7, SAMPLE_SPOOL_7_WITH_OVERRIDE])
        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/filaments/7",
                json={"spool_weight": 196.0, "keep_existing_spools": False},
            )

        assert response.status_code == 200
        mock_client.patch_filament.assert_called_once_with(7, {"spool_weight": 196.0})
        assert mock_client.update_spool_full.call_count == 2
        calls = {c.kwargs["spool_id"]: c.kwargs["spool_weight"] for c in mock_client.update_spool_full.call_args_list}
        assert calls[42] == pytest.approx(196.0)
        assert calls[43] == pytest.approx(196.0)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_option_b_stamps_inheriting_spool_with_new_weight(
        self, async_client: AsyncClient, spoolman_settings
    ):
        """Option B: a spool inheriting (spool_weight=None) gets the new weight explicitly stamped."""
        mock_client = make_mock_client(all_spools=[SAMPLE_SPOOL_WITH_FILAMENT_7])
        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/filaments/7",
                json={"spool_weight": 196.0, "keep_existing_spools": False},
            )

        assert response.status_code == 200
        mock_client.update_spool_full.assert_called_once_with(spool_id=42, spool_weight=pytest.approx(196.0))

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_option_b_only_stamps_affected_filament_spools(
        self, async_client: AsyncClient, spoolman_settings
    ):
        """Option B for filament 7 must not touch spools belonging to other filament types."""
        mock_client = make_mock_client(all_spools=[SAMPLE_SPOOL_WITH_FILAMENT_7, SAMPLE_SPOOL_WITH_FILAMENT_99])
        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/filaments/7",
                json={"spool_weight": 196.0, "keep_existing_spools": False},
            )

        assert response.status_code == 200
        # Only spool 42 (filament 7) should be stamped; spool 55 (filament 99) must not be touched
        mock_client.update_spool_full.assert_called_once_with(spool_id=42, spool_weight=pytest.approx(196.0))


class TestPatchFilamentOptionA:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_option_a_stamps_old_weight_on_inheriting_spools(
        self, async_client: AsyncClient, spoolman_settings
    ):
        """Option A: spools inheriting from filament (spool_weight=None) get old weight stamped on them."""
        mock_client = make_mock_client(all_spools=[SAMPLE_SPOOL_WITH_FILAMENT_7])
        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/filaments/7",
                json={"spool_weight": 196.0, "keep_existing_spools": True},
            )

        assert response.status_code == 200
        # old_weight = SAMPLE_FILAMENT["spool_weight"] = 250.0
        mock_client.update_spool_full.assert_called_once_with(spool_id=42, spool_weight=pytest.approx(250.0))

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_option_a_does_not_patch_spools_with_existing_override(
        self, async_client: AsyncClient, spoolman_settings
    ):
        """Option A: spools already having their own spool_weight are left unchanged."""
        mock_client = make_mock_client(all_spools=[SAMPLE_SPOOL_7_WITH_OVERRIDE])
        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/filaments/7",
                json={"spool_weight": 196.0, "keep_existing_spools": True},
            )

        assert response.status_code == 200
        mock_client.update_spool_full.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_option_a_mixed_spools_stamps_only_inheriting(
        self, async_client: AsyncClient, spoolman_settings
    ):
        """Option A: only inheriting spools (spool_weight=None) get old weight; overridden spools are skipped."""
        mock_client = make_mock_client(all_spools=[SAMPLE_SPOOL_WITH_FILAMENT_7, SAMPLE_SPOOL_7_WITH_OVERRIDE])
        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/filaments/7",
                json={"spool_weight": 196.0, "keep_existing_spools": True},
            )

        assert response.status_code == 200
        # Only spool 42 (inheriting) should be stamped; spool 43 (has override) must not be touched
        mock_client.update_spool_full.assert_called_once_with(spool_id=42, spool_weight=pytest.approx(250.0))

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_option_a_zero_spools_no_error(
        self, async_client: AsyncClient, spoolman_settings
    ):
        """Option A with zero spools for this filament: no error, no Spoolman calls."""
        mock_client = make_mock_client(all_spools=[])
        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/filaments/7",
                json={"spool_weight": 196.0, "keep_existing_spools": True},
            )

        assert response.status_code == 200
        mock_client.update_spool_full.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_option_a_filament_no_old_weight_skips_stamping(
        self, async_client: AsyncClient, spoolman_settings
    ):
        """Option A: if the filament has no old spool_weight, no stamping occurs."""
        mock_client = make_mock_client(filament={**SAMPLE_FILAMENT, "spool_weight": None})
        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/filaments/7",
                json={"spool_weight": 196.0, "keep_existing_spools": True},
            )

        assert response.status_code == 200
        mock_client.update_spool_full.assert_not_called()


class TestPatchFilamentNameOnly:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_name_only_patch_no_get_all_spools(
        self, async_client: AsyncClient, db_session, spoolman_settings
    ):
        """Patching name only must not call get_all_spools."""
        mock_client = make_mock_client()
        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/filaments/7",
                json={"name": "PLA Basic Renamed"},
            )

        assert response.status_code == 200
        mock_client.patch_filament.assert_called_once_with(7, {"name": "PLA Basic Renamed"})
        mock_client.get_all_spools.assert_not_called()


class TestPatchFilamentEdgeCases:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_null_filament_on_spool_skipped(
        self, async_client: AsyncClient, db_session, spoolman_settings
    ):
        """Spools with filament=null are skipped without error."""
        mock_client = make_mock_client(all_spools=[SPOOL_WITH_NULL_FILAMENT, SAMPLE_SPOOL_WITH_FILAMENT_7])
        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/filaments/7",
                json={"spool_weight": 196.0},
            )

        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spool_weight_zero_is_valid(
        self, async_client: AsyncClient, db_session, spoolman_settings
    ):
        """spool_weight=0 is valid (0g tare weight is legitimate)."""
        mock_client = make_mock_client()
        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/filaments/7",
                json={"spool_weight": 0},
            )

        assert response.status_code == 200
        mock_client.patch_filament.assert_called_once_with(7, {"spool_weight": 0})

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spool_weight_null_removes_weight(
        self, async_client: AsyncClient, db_session, spoolman_settings
    ):
        """spool_weight=null is forwarded to Spoolman as None."""
        mock_client = make_mock_client()
        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/filaments/7",
                json={"spool_weight": None},
            )

        assert response.status_code == 200
        mock_client.patch_filament.assert_called_once_with(7, {"spool_weight": None})


class TestPatchFilamentErrors:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_disabled_returns_400(self, async_client: AsyncClient, db_session):
        """When Spoolman is disabled, PATCH /filaments/{id} returns 400."""
        response = await async_client.patch(
            "/api/v1/spoolman/inventory/filaments/7",
            json={"spool_weight": 196.0},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_not_found_returns_404(
        self, async_client: AsyncClient, db_session, spoolman_settings
    ):
        """When get_filament raises SpoolmanNotFoundError, endpoint returns 404."""
        from backend.app.services.spoolman import SpoolmanNotFoundError

        mock_client = make_mock_client()
        mock_client.get_filament = AsyncMock(side_effect=SpoolmanNotFoundError("not found"))
        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/filaments/7",
                json={"spool_weight": 196.0},
            )

        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_invalid_id_returns_422(
        self, async_client: AsyncClient, db_session, spoolman_settings
    ):
        """filament_id=0 fails Path validation (gt=0) with 422."""
        response = await async_client.patch(
            "/api/v1/spoolman/inventory/filaments/0",
            json={"spool_weight": 196.0},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_negative_spool_weight_returns_422(
        self, async_client: AsyncClient, db_session, spoolman_settings
    ):
        """spool_weight=-1 fails Pydantic validation (ge=0.0) with 422."""
        mock_client = make_mock_client()
        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/filaments/7",
                json={"spool_weight": -1},
            )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Spool-level tare priority in sync_spool_weight (spoolman_inventory)
# ---------------------------------------------------------------------------

class TestSyncSpoolWeightPriority:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spool_level_spool_weight_takes_priority(
        self, async_client: AsyncClient, spoolman_settings
    ):
        """sync_spool_weight uses spool.spool_weight over filament.spool_weight for tare."""
        spool_data = {**SAMPLE_SPOOL_WITH_FILAMENT_7, "spool_weight": 100.0}
        mock_client = make_mock_client()
        mock_client.get_spool = AsyncMock(return_value=spool_data)
        mock_client.update_spool_full = AsyncMock(return_value=spool_data)

        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/spools/42/weight",
                json={"weight_grams": 600.0},
            )

        assert response.status_code == 200
        # remaining = 600 - 100 (spool-level tare) = 500
        update_call = mock_client.update_spool_full.call_args
        assert update_call.kwargs["remaining_weight"] == pytest.approx(500.0)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_filament_spool_weight_used_as_fallback(
        self, async_client: AsyncClient, spoolman_settings
    ):
        """sync_spool_weight falls back to filament.spool_weight when spool.spool_weight is None."""
        spool_data = {**SAMPLE_SPOOL_WITH_FILAMENT_7}  # spool_weight=None → filament fallback 250.0
        mock_client = make_mock_client()
        mock_client.get_spool = AsyncMock(return_value=spool_data)
        mock_client.update_spool_full = AsyncMock(return_value=spool_data)

        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/spools/42/weight",
                json={"weight_grams": 600.0},
            )

        assert response.status_code == 200
        # remaining = 600 - 250 (filament.spool_weight) = 350
        update_call = mock_client.update_spool_full.call_args
        assert update_call.kwargs["remaining_weight"] == pytest.approx(350.0)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spool_level_zero_not_treated_as_missing(
        self, async_client: AsyncClient, spoolman_settings
    ):
        """spool.spool_weight=0 is a valid 0g tare, not treated as missing."""
        spool_data = {**SAMPLE_SPOOL_WITH_FILAMENT_7, "spool_weight": 0}
        mock_client = make_mock_client()
        mock_client.get_spool = AsyncMock(return_value=spool_data)
        mock_client.update_spool_full = AsyncMock(return_value=spool_data)

        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/spools/42/weight",
                json={"weight_grams": 600.0},
            )

        assert response.status_code == 200
        # remaining = 600 - 0 = 600 (not 600 - 250 fallback)
        update_call = mock_client.update_spool_full.call_args
        assert update_call.kwargs["remaining_weight"] == pytest.approx(600.0)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_both_levels_none_uses_250g_fallback(
        self, async_client: AsyncClient, spoolman_settings
    ):
        """When both spool.spool_weight and filament.spool_weight are None, 250g fallback is used."""
        spool_data = {
            **SAMPLE_SPOOL_WITH_FILAMENT_7,
            "spool_weight": None,
            "filament": {**SAMPLE_SPOOL_WITH_FILAMENT_7["filament"], "spool_weight": None},
        }
        mock_client = make_mock_client()
        mock_client.get_spool = AsyncMock(return_value=spool_data)
        mock_client.update_spool_full = AsyncMock(return_value=spool_data)

        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/spools/42/weight",
                json={"weight_grams": 600.0},
            )

        assert response.status_code == 200
        # remaining = 600 - 250 (fallback) = 350
        update_call = mock_client.update_spool_full.call_args
        assert update_call.kwargs["remaining_weight"] == pytest.approx(350.0)


# ---------------------------------------------------------------------------
# Spool-level tare priority in update_spool_weight (spoolbuddy.py scale endpoint)
# ---------------------------------------------------------------------------

class TestUpdateSpoolWeightPriority:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spool_level_spool_weight_takes_priority(
        self, async_client: AsyncClient, spoolman_settings
    ):
        """update_spool_weight uses spool.spool_weight over filament.spool_weight for tare."""
        spool_data = {**SAMPLE_SPOOL_WITH_FILAMENT_7, "spool_weight": 100.0}
        mock_client = MagicMock()
        mock_client.get_spool = AsyncMock(return_value=spool_data)
        mock_client.update_spool = AsyncMock(return_value=None)

        with patch(
            "backend.app.api.routes.spoolbuddy._get_spoolman_client_or_none",
            AsyncMock(return_value=mock_client),
        ):
            response = await async_client.post(
                "/api/v1/spoolbuddy/scale/update-spool-weight",
                json={"spool_id": 42, "weight_grams": 600.0},
            )

        assert response.status_code == 200
        # remaining = 600 - 100 (spool-level tare) = 500
        mock_client.update_spool.assert_called_once_with(spool_id=42, remaining_weight=pytest.approx(500.0))

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_filament_spool_weight_used_as_fallback(
        self, async_client: AsyncClient, spoolman_settings
    ):
        """update_spool_weight falls back to filament.spool_weight when spool.spool_weight is None."""
        spool_data = {**SAMPLE_SPOOL_WITH_FILAMENT_7}  # spool_weight=None → filament fallback 250.0
        mock_client = MagicMock()
        mock_client.get_spool = AsyncMock(return_value=spool_data)
        mock_client.update_spool = AsyncMock(return_value=None)

        with patch(
            "backend.app.api.routes.spoolbuddy._get_spoolman_client_or_none",
            AsyncMock(return_value=mock_client),
        ):
            response = await async_client.post(
                "/api/v1/spoolbuddy/scale/update-spool-weight",
                json={"spool_id": 42, "weight_grams": 600.0},
            )

        assert response.status_code == 200
        # remaining = 600 - 250 (filament.spool_weight) = 350
        mock_client.update_spool.assert_called_once_with(spool_id=42, remaining_weight=pytest.approx(350.0))

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spool_level_zero_not_treated_as_missing(
        self, async_client: AsyncClient, spoolman_settings
    ):
        """spool.spool_weight=0 is a valid 0g tare, not treated as missing."""
        spool_data = {**SAMPLE_SPOOL_WITH_FILAMENT_7, "spool_weight": 0}
        mock_client = MagicMock()
        mock_client.get_spool = AsyncMock(return_value=spool_data)
        mock_client.update_spool = AsyncMock(return_value=None)

        with patch(
            "backend.app.api.routes.spoolbuddy._get_spoolman_client_or_none",
            AsyncMock(return_value=mock_client),
        ):
            response = await async_client.post(
                "/api/v1/spoolbuddy/scale/update-spool-weight",
                json={"spool_id": 42, "weight_grams": 600.0},
            )

        assert response.status_code == 200
        # remaining = 600 - 0 = 600 (not 600 - 250 fallback)
        mock_client.update_spool.assert_called_once_with(spool_id=42, remaining_weight=pytest.approx(600.0))

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_both_levels_none_uses_250g_fallback_and_warns(
        self, async_client: AsyncClient, spoolman_settings
    ):
        """When both spool.spool_weight and filament.spool_weight are None, 250g fallback is used with a warning."""
        spool_data = {
            **SAMPLE_SPOOL_WITH_FILAMENT_7,
            "spool_weight": None,
            "filament": {**SAMPLE_SPOOL_WITH_FILAMENT_7["filament"], "spool_weight": None},
        }
        mock_client = MagicMock()
        mock_client.get_spool = AsyncMock(return_value=spool_data)
        mock_client.update_spool = AsyncMock(return_value=None)

        with patch(
            "backend.app.api.routes.spoolbuddy._get_spoolman_client_or_none",
            AsyncMock(return_value=mock_client),
        ):
            response = await async_client.post(
                "/api/v1/spoolbuddy/scale/update-spool-weight",
                json={"spool_id": 42, "weight_grams": 600.0},
            )

        assert response.status_code == 200
        # remaining = 600 - 250 (fallback) = 350
        mock_client.update_spool.assert_called_once_with(spool_id=42, remaining_weight=pytest.approx(350.0))
        assert response.json().get("warnings")
