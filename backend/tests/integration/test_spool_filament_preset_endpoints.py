"""Endpoints for the per-printer-model filament preset overrides.

    GET  /api/v1/inventory/spools/{id}/filament-presets
    PUT  /api/v1/inventory/spools/{id}/filament-presets
    GET  /api/v1/spoolman/inventory/spools/{id}/filament-presets
    PUT  /api/v1/spoolman/inventory/spools/{id}/filament-presets

Both PUTs replace the whole set, matching the K-profile endpoints beside them:
the spool form always holds the complete list, and an empty body is how the
user clears every override back to the spool's own preset.

The case worth having a test for is the duplicate: (model, diameter) is
UNIQUE, so a payload naming one twice has to be refused -- and refused
*before* the existing rows are deleted, or a rejected save takes the user's
overrides with it.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

SAMPLE_SPOOL = {
    "id": 7,
    "filament": {
        "id": 1,
        "name": "PLA Basic",
        "material": "PLA",
        "weight": 1000,
        "color_hex": "303030",
        "vendor": {"id": 1, "name": "Bambu"},
    },
    "remaining_weight": 800.0,
    "used_weight": 200.0,
    "location": None,
    "comment": None,
    "first_used": None,
    "last_used": None,
    "registered": "2024-01-01T00:00:00+00:00",
    "archived": False,
    "price": None,
    "extra": {},
}

INTERNAL = "/api/v1/inventory/spools"
SPOOLMAN = "/api/v1/spoolman/inventory/spools"


@pytest.fixture
async def spool(db_session):
    from backend.app.models.spool import Spool

    row = Spool(
        brand="Bambu",
        material="PLA",
        color_name="Charcoal",
        slicer_filament="GFSA00",
        slicer_filament_name="Bambu PLA Basic @BBL X1C",
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


@pytest.fixture
async def spoolman_settings(db_session):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
    await db_session.commit()


@pytest.fixture
def mock_spoolman_client():
    client = MagicMock()
    client.base_url = "http://localhost:7912"
    client.health_check = AsyncMock(return_value=True)
    client.get_spool = AsyncMock(return_value=SAMPLE_SPOOL)

    with patch(
        "backend.app.api.routes.spoolman_inventory._get_client",
        AsyncMock(return_value=client),
    ):
        yield client


def _preset(model, diameter="", code="GFSA09", name="Bambu PLA Basic @BBL H2C"):
    return {
        "printer_model": model,
        "nozzle_diameter": diameter,
        "slicer_filament": code,
        "slicer_filament_name": name,
    }


@pytest.mark.integration
class TestInternalInventory:
    @pytest.mark.asyncio
    async def test_empty_by_default(self, async_client: AsyncClient, spool):
        response = await async_client.get(f"{INTERNAL}/{spool.id}/filament-presets")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_put_then_get_round_trips(self, async_client: AsyncClient, spool):
        response = await async_client.put(
            f"{INTERNAL}/{spool.id}/filament-presets",
            json=[_preset("H2C"), _preset("A1 mini", "0.2", "GFSA21", "PLA @A1M 0.2 nozzle")],
        )
        assert response.status_code == 200, response.text

        rows = (await async_client.get(f"{INTERNAL}/{spool.id}/filament-presets")).json()
        assert len(rows) == 2
        by_model = {r["printer_model"]: r for r in rows}
        assert by_model["H2C"]["nozzle_diameter"] == ""
        assert by_model["H2C"]["slicer_filament"] == "GFSA09"
        assert by_model["A1 mini"]["nozzle_diameter"] == "0.2"
        assert by_model["A1 mini"]["slicer_filament"] == "GFSA21"
        assert all(r["spool_id"] == spool.id for r in rows)

    @pytest.mark.asyncio
    async def test_put_replaces_rather_than_appends(self, async_client: AsyncClient, spool):
        await async_client.put(f"{INTERNAL}/{spool.id}/filament-presets", json=[_preset("H2C")])
        await async_client.put(f"{INTERNAL}/{spool.id}/filament-presets", json=[_preset("X1C")])

        rows = (await async_client.get(f"{INTERNAL}/{spool.id}/filament-presets")).json()
        assert [r["printer_model"] for r in rows] == ["X1C"]

    @pytest.mark.asyncio
    async def test_empty_body_clears_every_override(self, async_client: AsyncClient, spool):
        await async_client.put(f"{INTERNAL}/{spool.id}/filament-presets", json=[_preset("H2C")])

        response = await async_client.put(f"{INTERNAL}/{spool.id}/filament-presets", json=[])
        assert response.status_code == 200
        assert (await async_client.get(f"{INTERNAL}/{spool.id}/filament-presets")).json() == []

    @pytest.mark.asyncio
    async def test_replacing_the_same_key_does_not_trip_the_unique_constraint(self, async_client: AsyncClient, spool):
        """Deletes and inserts land in one transaction, and SQLAlchemy is free
        to order the INSERTs first. Re-saving the same (model, diameter) with a
        new preset is the ordinary case -- the user changed their pick."""
        await async_client.put(f"{INTERNAL}/{spool.id}/filament-presets", json=[_preset("H2C")])

        response = await async_client.put(
            f"{INTERNAL}/{spool.id}/filament-presets",
            json=[_preset("H2C", "", "GFSA11", "Bambu PLA Matte @BBL H2C")],
        )
        assert response.status_code == 200, response.text

        rows = (await async_client.get(f"{INTERNAL}/{spool.id}/filament-presets")).json()
        assert len(rows) == 1
        assert rows[0]["slicer_filament"] == "GFSA11"

    @pytest.mark.asyncio
    async def test_duplicate_key_is_rejected_without_losing_the_stored_rows(self, async_client: AsyncClient, spool):
        await async_client.put(f"{INTERNAL}/{spool.id}/filament-presets", json=[_preset("H2C")])

        response = await async_client.put(
            f"{INTERNAL}/{spool.id}/filament-presets",
            json=[_preset("X1C", "", "GFSA01"), _preset("X1C", "", "GFSA02")],
        )
        assert response.status_code == 422

        # The rejected save must not have taken the existing override with it.
        rows = (await async_client.get(f"{INTERNAL}/{spool.id}/filament-presets")).json()
        assert [r["printer_model"] for r in rows] == ["H2C"]

    @pytest.mark.asyncio
    async def test_unknown_spool_is_404(self, async_client: AsyncClient):
        response = await async_client.put(f"{INTERNAL}/999999/filament-presets", json=[_preset("H2C")])
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_blank_model_is_rejected(self, async_client: AsyncClient, spool):
        """An empty printer_model would store a row the cascade can never
        match, since it refuses to resolve without a model."""
        response = await async_client.put(f"{INTERNAL}/{spool.id}/filament-presets", json=[_preset("")])
        assert response.status_code == 422


@pytest.mark.integration
class TestSpoolmanInventory:
    @pytest.mark.asyncio
    async def test_empty_by_default(self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client):
        response = await async_client.get(f"{SPOOLMAN}/7/filament-presets")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_put_then_get_round_trips(self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client):
        response = await async_client.put(
            f"{SPOOLMAN}/7/filament-presets",
            json=[_preset("H2C"), _preset("H2C", "0.2", "GFSA10", "PLA @H2C 0.2 nozzle")],
        )
        assert response.status_code == 200, response.text

        rows = (await async_client.get(f"{SPOOLMAN}/7/filament-presets")).json()
        assert len(rows) == 2
        assert all(r["spool_id"] == 7 for r in rows)
        assert {r["nozzle_diameter"] for r in rows} == {"", "0.2"}

    @pytest.mark.asyncio
    async def test_put_replaces_rather_than_appends(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        await async_client.put(f"{SPOOLMAN}/7/filament-presets", json=[_preset("H2C")])
        await async_client.put(f"{SPOOLMAN}/7/filament-presets", json=[_preset("X1C")])

        rows = (await async_client.get(f"{SPOOLMAN}/7/filament-presets")).json()
        assert [r["printer_model"] for r in rows] == ["X1C"]

    @pytest.mark.asyncio
    async def test_duplicate_key_is_rejected_without_losing_the_stored_rows(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        await async_client.put(f"{SPOOLMAN}/7/filament-presets", json=[_preset("H2C")])

        response = await async_client.put(
            f"{SPOOLMAN}/7/filament-presets",
            json=[_preset("X1C", "", "GFSA01"), _preset("X1C", "", "GFSA02")],
        )
        assert response.status_code == 422

        rows = (await async_client.get(f"{SPOOLMAN}/7/filament-presets")).json()
        assert [r["printer_model"] for r in rows] == ["H2C"]

    @pytest.mark.asyncio
    async def test_one_spools_overrides_do_not_leak_into_another(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        await async_client.put(f"{SPOOLMAN}/7/filament-presets", json=[_preset("H2C")])
        assert (await async_client.get(f"{SPOOLMAN}/8/filament-presets")).json() == []
