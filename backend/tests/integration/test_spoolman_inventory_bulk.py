"""Bulk Spoolman inventory endpoint coverage for the batch-edit feature (#1795).

Endpoints under test:
- POST /api/v1/spoolman/inventory/spools/bulk-update
- POST /api/v1/spoolman/inventory/spools/bulk-delete
- POST /api/v1/spoolman/inventory/spools/bulk-archive
- POST /api/v1/spoolman/inventory/spools/bulk-restore
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import AsyncClient

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
    "extra": {},
}


@pytest.fixture
async def spoolman_settings(db_session):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
    await db_session.commit()


@pytest.fixture
def mock_spoolman_client():
    mock = MagicMock()
    mock.base_url = "http://localhost:7912"
    mock.health_check = AsyncMock(return_value=True)
    mock.get_spool = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)
    mock.delete_spool = AsyncMock(return_value=True)
    mock.set_spool_archived = AsyncMock(
        side_effect=lambda spool_id, archived: {**SAMPLE_SPOOLMAN_SPOOL, "archived": archived}
    )
    mock.update_spool_full = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)
    mock.merge_spool_extra = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)
    mock.is_filament_shared = AsyncMock(return_value=False)
    mock.patch_filament = AsyncMock(return_value={"id": 7})
    mock.find_or_create_filament = AsyncMock(return_value=7)
    mock.find_or_create_vendor = AsyncMock(return_value=3)
    mock.ensure_extra_field = AsyncMock(return_value=True)
    mock.get_distinct_locations = AsyncMock(return_value=[])

    class _Lock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    mock.extra_lock = lambda spool_id: _Lock()

    with (
        patch(
            "backend.app.api.routes.spoolman_inventory.get_spoolman_client",
            AsyncMock(return_value=mock),
        ),
        patch(
            "backend.app.api.routes.spoolman_inventory.init_spoolman_client",
            AsyncMock(return_value=mock),
        ),
    ):
        yield mock


class TestSpoolmanBulkUpdate:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_calls_per_spool_update_for_each_id(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        resp = await async_client.post(
            "/api/v1/spoolman/inventory/spools/bulk-update",
            json={"ids": [42, 43, 44], "update": {"note": "From bulk edit"}},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["updated"] == 3
        assert body["errors"] == []
        # update_spool route loops through each, which calls update_spool_full once per ID
        assert mock_spoolman_client.update_spool_full.await_count == 3

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_collects_per_spool_errors_without_aborting_batch(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        # First two succeed, third raises
        mock_spoolman_client.update_spool_full.side_effect = [
            SAMPLE_SPOOLMAN_SPOOL,
            SAMPLE_SPOOLMAN_SPOOL,
            HTTPException(status_code=404, detail="Spool 999 not found"),
        ]

        resp = await async_client.post(
            "/api/v1/spoolman/inventory/spools/bulk-update",
            json={"ids": [42, 43, 999], "update": {"note": "Batched"}},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["updated"] == 2
        assert len(body["errors"]) == 1
        assert body["errors"][0]["id"] == 999
        assert body["errors"][0]["status"] == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_empty_update_rejected(self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client):
        resp = await async_client.post(
            "/api/v1/spoolman/inventory/spools/bulk-update",
            json={"ids": [42], "update": {}},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_empty_ids_rejected(self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client):
        resp = await async_client.post(
            "/api/v1/spoolman/inventory/spools/bulk-update",
            json={"ids": [], "update": {"note": "X"}},
        )
        assert resp.status_code == 422


class TestSpoolmanBulkDelete:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_deletes_listed_spools(self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client):
        resp = await async_client.post(
            "/api/v1/spoolman/inventory/spools/bulk-delete",
            json={"ids": [42, 43, 44]},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted"] == 3
        assert body["errors"] == []
        assert mock_spoolman_client.delete_spool.await_count == 3


class TestSpoolmanBulkArchiveRestore:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_archive_calls_per_spool(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        resp = await async_client.post(
            "/api/v1/spoolman/inventory/spools/bulk-archive",
            json={"ids": [42, 43]},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["archived"] == 2
        # set_spool_archived(spool_id, archived=True) called for each id
        assert mock_spoolman_client.set_spool_archived.await_count == 2
        for call in mock_spoolman_client.set_spool_archived.call_args_list:
            assert call.kwargs.get("archived") is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_restore_calls_per_spool(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        resp = await async_client.post(
            "/api/v1/spoolman/inventory/spools/bulk-restore",
            json={"ids": [42, 43]},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["restored"] == 2
        assert mock_spoolman_client.set_spool_archived.await_count == 2
        for call in mock_spoolman_client.set_spool_archived.call_args_list:
            assert call.kwargs.get("archived") is False
