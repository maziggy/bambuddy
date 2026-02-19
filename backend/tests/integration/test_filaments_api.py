"""Integration tests for Filaments API endpoints."""

import pytest
from httpx import AsyncClient


class TestFilamentsAPI:
    """Integration tests for /api/v1/filament-catalog/ (material types) endpoints."""

    @pytest.fixture
    async def filament_factory(self, db_session):
        """Factory to create test filaments."""

        async def _create_filament(**kwargs):
            from backend.app.models.filament import Filament

            defaults = {
                "name": "Test PLA",
                "type": "PLA",
                "color": "Red",
                "color_hex": "#FF0000",
                "brand": "Generic",
                "cost_per_kg": 25.0,
            }
            defaults.update(kwargs)

            filament = Filament(**defaults)
            db_session.add(filament)
            await db_session.commit()
            await db_session.refresh(filament)
            return filament

        return _create_filament

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_filaments_empty(self, async_client: AsyncClient):
        """Verify empty list when no filaments exist."""
        response = await async_client.get("/api/v1/filament-catalog/")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_filaments_with_data(self, async_client: AsyncClient, filament_factory, db_session):
        """Verify list returns existing filaments."""
        await filament_factory(name="Test Filament")
        response = await async_client.get("/api/v1/filament-catalog/")
        assert response.status_code == 200
        data = response.json()
        assert any(f["name"] == "Test Filament" for f in data)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_filament(self, async_client: AsyncClient):
        """Verify filament can be created."""
        data = {
            "name": "New PETG",
            "type": "PETG",
            "color": "Blue",
            "color_hex": "#0000FF",
            "brand": "Bambu",
            "cost_per_kg": 30.0,
        }
        response = await async_client.post("/api/v1/filament-catalog/", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["name"] == "New PETG"
        assert result["type"] == "PETG"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_filament(self, async_client: AsyncClient, filament_factory, db_session):
        """Verify single filament can be retrieved."""
        filament = await filament_factory(name="Get Test")
        response = await async_client.get(f"/api/v1/filament-catalog/{filament.id}")
        assert response.status_code == 200
        assert response.json()["name"] == "Get Test"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_filament_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent filament."""
        response = await async_client.get("/api/v1/filament-catalog/9999")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_filament(self, async_client: AsyncClient, filament_factory, db_session):
        """Verify filament can be updated."""
        filament = await filament_factory(name="Original")
        response = await async_client.patch(
            f"/api/v1/filament-catalog/{filament.id}", json={"name": "Updated", "cost_per_kg": 35.0}
        )
        assert response.status_code == 200
        result = response.json()
        assert result["name"] == "Updated"
        assert result["cost_per_kg"] == 35.0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_filament(self, async_client: AsyncClient, filament_factory, db_session):
        """Verify filament can be deleted."""
        filament = await filament_factory()
        response = await async_client.delete(f"/api/v1/filament-catalog/{filament.id}")
        assert response.status_code == 200
        # Verify deleted
        response = await async_client.get(f"/api/v1/filament-catalog/{filament.id}")
        assert response.status_code == 404
