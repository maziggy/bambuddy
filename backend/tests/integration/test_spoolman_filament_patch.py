"""Integration tests for PATCH /spoolman/inventory/filaments/{filament_id}.

Covers:
- Option A (keep_existing_spools=True): inserts override rows for existing spools
- Option B (keep_existing_spools=False): deletes override rows for affected spools
- Name-only patch: no get_all_spools call, no override changes
- Edge cases: disabled Spoolman, not found, invalid inputs
- Override lookup in update_spool_weight (spoolbuddy) and sync_spool_weight (spoolman_inventory)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

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
    "filament": None,
    "remaining_weight": 100.0,
    "used_weight": 900.0,
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
    return mock_client


# ---------------------------------------------------------------------------
# PATCH /filaments/{id} — core scenarios
# ---------------------------------------------------------------------------

class TestPatchFilamentOptionB:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_option_b_calls_spoolman_and_clears_overrides(
        self, async_client: AsyncClient, db_session, spoolman_settings
    ):
        """Option B: patch_filament called; override rows for affected spools deleted."""
        from backend.app.models.spoolman_spool_weight_override import SpoolmanSpoolWeightOverride

        # Pre-insert an override row for spool 42 (filament 7)
        db_session.add(SpoolmanSpoolWeightOverride(spoolman_spool_id=42, core_weight=250))
        await db_session.commit()

        mock_client = make_mock_client()
        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/filaments/7",
                json={"spool_weight": 196.0, "keep_existing_spools": False},
            )

        assert response.status_code == 200
        mock_client.patch_filament.assert_called_once_with(7, {"spool_weight": 196.0})

        # Override row should be deleted
        result = await db_session.execute(
            select(SpoolmanSpoolWeightOverride).where(SpoolmanSpoolWeightOverride.spoolman_spool_id == 42)
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_option_b_only_deletes_affected_filament_overrides(
        self, async_client: AsyncClient, db_session, spoolman_settings
    ):
        """Option B for filament 7 must not delete overrides for filament 99 spools."""
        from backend.app.models.spoolman_spool_weight_override import SpoolmanSpoolWeightOverride

        db_session.add(SpoolmanSpoolWeightOverride(spoolman_spool_id=42, core_weight=250))  # filament 7
        db_session.add(SpoolmanSpoolWeightOverride(spoolman_spool_id=55, core_weight=196))  # filament 99
        await db_session.commit()

        mock_client = make_mock_client(
            all_spools=[SAMPLE_SPOOL_WITH_FILAMENT_7, SAMPLE_SPOOL_WITH_FILAMENT_99]
        )
        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/filaments/7",
                json={"spool_weight": 196.0, "keep_existing_spools": False},
            )

        assert response.status_code == 200

        # Filament 7's override should be gone
        r7 = await db_session.execute(
            select(SpoolmanSpoolWeightOverride).where(SpoolmanSpoolWeightOverride.spoolman_spool_id == 42)
        )
        assert r7.scalar_one_or_none() is None

        # Filament 99's override must remain
        r99 = await db_session.execute(
            select(SpoolmanSpoolWeightOverride).where(SpoolmanSpoolWeightOverride.spoolman_spool_id == 55)
        )
        assert r99.scalar_one_or_none() is not None


class TestPatchFilamentOptionA:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_option_a_writes_override_rows(
        self, async_client: AsyncClient, db_session, spoolman_settings
    ):
        """Option A: override row written for each existing spool of the filament."""
        from backend.app.models.spoolman_spool_weight_override import SpoolmanSpoolWeightOverride

        mock_client = make_mock_client()
        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/filaments/7",
                json={"spool_weight": 196.0, "keep_existing_spools": True},
            )

        assert response.status_code == 200

        result = await db_session.execute(
            select(SpoolmanSpoolWeightOverride).where(SpoolmanSpoolWeightOverride.spoolman_spool_id == 42)
        )
        row = result.scalar_one_or_none()
        assert row is not None
        assert row.core_weight == 250  # old weight preserved

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_option_a_does_not_overwrite_existing_overrides(
        self, async_client: AsyncClient, db_session, spoolman_settings
    ):
        """Option A ON CONFLICT DO NOTHING: existing override stays unchanged."""
        from backend.app.models.spoolman_spool_weight_override import SpoolmanSpoolWeightOverride

        # Pre-existing override with custom weight
        db_session.add(SpoolmanSpoolWeightOverride(spoolman_spool_id=42, core_weight=200))
        await db_session.commit()

        mock_client = make_mock_client()
        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/filaments/7",
                json={"spool_weight": 196.0, "keep_existing_spools": True},
            )

        assert response.status_code == 200

        result = await db_session.execute(
            select(SpoolmanSpoolWeightOverride).where(SpoolmanSpoolWeightOverride.spoolman_spool_id == 42)
        )
        row = result.scalar_one_or_none()
        assert row is not None
        assert row.core_weight == 200  # not overwritten

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_option_a_zero_spools_no_error(
        self, async_client: AsyncClient, db_session, spoolman_settings
    ):
        """Option A with zero spools for this filament: no error, no rows inserted."""
        from backend.app.models.spoolman_spool_weight_override import SpoolmanSpoolWeightOverride

        mock_client = make_mock_client(all_spools=[])
        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/filaments/7",
                json={"spool_weight": 196.0, "keep_existing_spools": True},
            )

        assert response.status_code == 200
        result = await db_session.execute(select(SpoolmanSpoolWeightOverride))
        assert result.scalars().all() == []


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
# Override lookup in weight calculation endpoints
# ---------------------------------------------------------------------------

class TestOverrideLookupInWeightCalc:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_sync_spool_weight_uses_override_when_present(
        self, async_client: AsyncClient, db_session, spoolman_settings
    ):
        """sync_spool_weight uses override row instead of Spoolman filament spool_weight."""
        from backend.app.models.spoolman_spool_weight_override import SpoolmanSpoolWeightOverride

        # Override: spool 42 uses 100g tare
        db_session.add(SpoolmanSpoolWeightOverride(spoolman_spool_id=42, core_weight=100))
        await db_session.commit()

        spool_data = {**SAMPLE_SPOOL_WITH_FILAMENT_7, "remaining_weight": 900.0}
        mock_client = make_mock_client()
        mock_client.get_spool = AsyncMock(return_value=spool_data)
        mock_client.update_spool_full = AsyncMock(return_value=spool_data)

        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/spools/42/weight",
                json={"weight_grams": 600.0},
            )

        assert response.status_code == 200
        # remaining = 600 - 100 (override) = 500; weight_used = 1000 - 500 = 500
        update_call = mock_client.update_spool_full.call_args
        assert update_call.kwargs["remaining_weight"] == pytest.approx(500.0)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_sync_spool_weight_uses_spoolman_when_no_override(
        self, async_client: AsyncClient, db_session, spoolman_settings
    ):
        """sync_spool_weight uses Spoolman spool_weight when no override exists."""
        spool_data = {**SAMPLE_SPOOL_WITH_FILAMENT_7, "remaining_weight": 500.0}
        mock_client = make_mock_client()
        mock_client.get_spool = AsyncMock(return_value=spool_data)
        mock_client.update_spool_full = AsyncMock(return_value=spool_data)

        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=mock_client)):
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/spools/42/weight",
                json={"weight_grams": 600.0},
            )

        assert response.status_code == 200
        # remaining = 600 - 250 (spoolman spool_weight) = 350
        update_call = mock_client.update_spool_full.call_args
        assert update_call.kwargs["remaining_weight"] == pytest.approx(350.0)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_sync_spool_weight_zero_spool_weight_not_treated_as_missing(
        self, async_client: AsyncClient, db_session, spoolman_settings
    ):
        """spool_weight=0 is a valid tare, not treated as missing (falsy-bug fix in spoolbuddy.py is separate)."""
        spool_data = {
            **SAMPLE_SPOOL_WITH_FILAMENT_7,
            "filament": {**SAMPLE_SPOOL_WITH_FILAMENT_7["filament"], "spool_weight": 0},
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
        # remaining = 600 - 0 = 600 (not 600 - 250 fallback)
        update_call = mock_client.update_spool_full.call_args
        assert update_call.kwargs["remaining_weight"] == pytest.approx(600.0)


# ---------------------------------------------------------------------------
# Override lookup in update_spool_weight (spoolbuddy.py scale endpoint)
# ---------------------------------------------------------------------------

class TestUpdateSpoolWeightOverrideLookup:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_spool_weight_uses_override_when_present(
        self, async_client: AsyncClient, db_session, spoolman_settings
    ):
        """update_spool_weight uses override row instead of Spoolman filament spool_weight."""
        from backend.app.models.spoolman_spool_weight_override import SpoolmanSpoolWeightOverride

        # Override: spool 42 uses 100g tare
        db_session.add(SpoolmanSpoolWeightOverride(spoolman_spool_id=42, core_weight=100))
        await db_session.commit()

        spool_data = {**SAMPLE_SPOOL_WITH_FILAMENT_7}
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
        # remaining = 600 - 100 (override) = 500
        mock_client.update_spool.assert_called_once_with(spool_id=42, remaining_weight=pytest.approx(500.0))

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_spool_weight_uses_spoolman_when_no_override(
        self, async_client: AsyncClient, db_session, spoolman_settings
    ):
        """update_spool_weight uses Spoolman spool_weight when no override exists."""
        spool_data = {**SAMPLE_SPOOL_WITH_FILAMENT_7}
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
        # remaining = 600 - 250 (spoolman spool_weight) = 350
        mock_client.update_spool.assert_called_once_with(spool_id=42, remaining_weight=pytest.approx(350.0))

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_spool_weight_zero_spool_weight_not_treated_as_missing(
        self, async_client: AsyncClient, db_session, spoolman_settings
    ):
        """spool_weight=0 is valid — the falsy-bug fix (was: if not raw_spool_weight) ensures 0g tare is used."""
        spool_data = {
            **SAMPLE_SPOOL_WITH_FILAMENT_7,
            "filament": {**SAMPLE_SPOOL_WITH_FILAMENT_7["filament"], "spool_weight": 0},
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
        # remaining = 600 - 0 = 600 (not 600 - 250 fallback from falsy-bug)
        mock_client.update_spool.assert_called_once_with(spool_id=42, remaining_weight=pytest.approx(600.0))
