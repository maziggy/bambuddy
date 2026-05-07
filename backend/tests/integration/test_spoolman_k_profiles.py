"""Integration tests for Spoolman K-profile endpoints.

Covers:
  GET  /api/v1/spoolman/inventory/spools/{id}/k-profiles
  PUT  /api/v1/spoolman/inventory/spools/{id}/k-profiles
  GET  /api/v1/spoolman/inventory/spools/{id}  — k_profiles enrichment
  GET  /api/v1/spoolman/inventory/spools       — k_profiles enrichment (batch)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

SAMPLE_SPOOL = {
    "id": 7,
    "filament": {
        "id": 1,
        "name": "PETG CF",
        "material": "PETG",
        "weight": 1000,
        "color_hex": "000000",
        "vendor": {"id": 1, "name": "BrandX"},
    },
    "remaining_weight": 600.0,
    "used_weight": 400.0,
    "location": None,
    "comment": None,
    "first_used": None,
    "last_used": None,
    "registered": "2024-01-01T00:00:00+00:00",
    "archived": False,
    "price": None,
    "extra": {},
}


@pytest.fixture
async def kp_settings(db_session):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
    await db_session.commit()


@pytest.fixture
async def test_printer(db_session):
    from backend.app.models.printer import Printer

    printer = Printer(
        name="KP Printer",
        serial_number="KPTEST001",
        ip_address="192.168.1.77",
        access_code="12345678",
    )
    db_session.add(printer)
    await db_session.commit()
    await db_session.refresh(printer)
    return printer


@pytest.fixture
def mock_spoolman_client():
    client = MagicMock()
    client.base_url = "http://localhost:7912"
    client.health_check = AsyncMock(return_value=True)
    client.get_spool = AsyncMock(return_value=SAMPLE_SPOOL)
    client.get_all_spools = AsyncMock(return_value=[SAMPLE_SPOOL])

    with patch(
        "backend.app.api.routes.spoolman_inventory._get_client",
        AsyncMock(return_value=client),
    ):
        yield client


class TestGetSpoolmanKProfiles:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_empty_list_when_none(self, async_client: AsyncClient, kp_settings, mock_spoolman_client):
        """GET /spools/7/k-profiles returns [] when no profiles exist."""
        response = await async_client.get("/api/v1/spoolman/inventory/spools/7/k-profiles")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_existing_profiles(
        self, async_client: AsyncClient, kp_settings, mock_spoolman_client, test_printer, db_session
    ):
        """GET /spools/7/k-profiles returns saved profiles."""
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile

        kp = SpoolmanKProfile(
            spoolman_spool_id=7,
            printer_id=test_printer.id,
            extruder=0,
            nozzle_diameter="0.4",
            k_value=0.025,
            cali_idx=3,
        )
        db_session.add(kp)
        await db_session.commit()

        response = await async_client.get("/api/v1/spoolman/inventory/spools/7/k-profiles")
        assert response.status_code == 200
        profiles = response.json()
        assert len(profiles) == 1
        assert profiles[0]["spool_id"] == 7
        assert profiles[0]["printer_id"] == test_printer.id
        assert profiles[0]["k_value"] == pytest.approx(0.025)
        assert profiles[0]["cali_idx"] == 3


class TestSaveSpoolmanKProfiles:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_put_creates_profiles(
        self, async_client: AsyncClient, kp_settings, mock_spoolman_client, test_printer
    ):
        """PUT /spools/7/k-profiles saves profiles and returns them."""
        response = await async_client.put(
            "/api/v1/spoolman/inventory/spools/7/k-profiles",
            json=[
                {
                    "printer_id": test_printer.id,
                    "extruder": 0,
                    "nozzle_diameter": "0.4",
                    "k_value": 0.02,
                    "cali_idx": 1,
                }
            ],
        )
        assert response.status_code == 200
        saved = response.json()
        assert len(saved) == 1
        assert saved[0]["spool_id"] == 7
        assert saved[0]["k_value"] == pytest.approx(0.02)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_put_replaces_existing_profiles(
        self, async_client: AsyncClient, kp_settings, mock_spoolman_client, test_printer, db_session
    ):
        """PUT /spools/7/k-profiles with new data deletes old rows first."""
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile

        old = SpoolmanKProfile(
            spoolman_spool_id=7,
            printer_id=test_printer.id,
            extruder=0,
            nozzle_diameter="0.4",
            k_value=0.99,
            cali_idx=99,
        )
        db_session.add(old)
        await db_session.commit()

        response = await async_client.put(
            "/api/v1/spoolman/inventory/spools/7/k-profiles",
            json=[
                {
                    "printer_id": test_printer.id,
                    "extruder": 0,
                    "nozzle_diameter": "0.4",
                    "k_value": 0.03,
                    "cali_idx": 7,
                }
            ],
        )
        assert response.status_code == 200
        saved = response.json()
        assert len(saved) == 1
        assert saved[0]["k_value"] == pytest.approx(0.03)
        assert saved[0]["cali_idx"] == 7

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_put_empty_clears_profiles(
        self, async_client: AsyncClient, kp_settings, mock_spoolman_client, test_printer, db_session
    ):
        """PUT /spools/7/k-profiles with [] clears all existing profiles."""
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile

        kp = SpoolmanKProfile(
            spoolman_spool_id=7,
            printer_id=test_printer.id,
            extruder=0,
            nozzle_diameter="0.4",
            k_value=0.02,
            cali_idx=1,
        )
        db_session.add(kp)
        await db_session.commit()

        response = await async_client.put(
            "/api/v1/spoolman/inventory/spools/7/k-profiles",
            json=[],
        )
        assert response.status_code == 200
        assert response.json() == []

        # Verify gone in DB
        get_resp = await async_client.get("/api/v1/spoolman/inventory/spools/7/k-profiles")
        assert get_resp.json() == []


class TestSpoolKProfileEnrichment:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_spool_includes_k_profiles(
        self, async_client: AsyncClient, kp_settings, mock_spoolman_client, test_printer, db_session
    ):
        """GET /spools/7 includes k_profiles from local DB."""
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile

        kp = SpoolmanKProfile(
            spoolman_spool_id=7,
            printer_id=test_printer.id,
            extruder=0,
            nozzle_diameter="0.6",
            k_value=0.018,
            cali_idx=2,
        )
        db_session.add(kp)
        await db_session.commit()

        response = await async_client.get("/api/v1/spoolman/inventory/spools/7")
        assert response.status_code == 200
        body = response.json()
        assert "k_profiles" in body
        assert len(body["k_profiles"]) == 1
        assert body["k_profiles"][0]["k_value"] == pytest.approx(0.018)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_spools_includes_k_profiles(
        self, async_client: AsyncClient, kp_settings, mock_spoolman_client, test_printer, db_session
    ):
        """GET /spools includes k_profiles for each spool from local DB."""
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile

        kp = SpoolmanKProfile(
            spoolman_spool_id=7,
            printer_id=test_printer.id,
            extruder=0,
            nozzle_diameter="0.4",
            k_value=0.021,
            cali_idx=4,
        )
        db_session.add(kp)
        await db_session.commit()

        response = await async_client.get("/api/v1/spoolman/inventory/spools")
        assert response.status_code == 200
        spools = response.json()
        assert len(spools) == 1
        assert "k_profiles" in spools[0]
        assert len(spools[0]["k_profiles"]) == 1
        assert spools[0]["k_profiles"][0]["cali_idx"] == 4


class TestPutSpoolmanKProfilesValidation:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_put_k_profiles_duplicate_raises_422(
        self, async_client: AsyncClient, kp_settings, mock_spoolman_client, test_printer
    ):
        """Two profiles with identical (printer_id, extruder, nozzle_diameter) → UNIQUE violation → 422."""
        profiles = [
            {"printer_id": test_printer.id, "extruder": 0, "nozzle_diameter": "0.4", "k_value": 0.02},
            {"printer_id": test_printer.id, "extruder": 0, "nozzle_diameter": "0.4", "k_value": 0.03},
        ]
        response = await async_client.put(
            "/api/v1/spoolman/inventory/spools/7/k-profiles",
            json=profiles,
        )
        assert response.status_code == 422
