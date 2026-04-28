"""Integration tests for POST /api/v1/spoolman/inventory/sync-ams-weights.

Covers:
  - happy path: synced count incremented, update_spool_full called with correct weight
  - printer offline: assignment skipped
  - spool missing from Spoolman: assignment skipped
  - invalid remain value: assignment skipped
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

SAMPLE_SPOOL = {
    "id": 42,
    "filament": {
        "id": 1,
        "name": "PLA Basic",
        "material": "PLA",
        "weight": 1000,
        "color_hex": "FF0000",
        "vendor": {"id": 1, "name": "BrandX"},
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


@pytest.fixture
async def sync_settings(db_session):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
    await db_session.commit()


@pytest.fixture
async def test_printer(db_session):
    from backend.app.models.printer import Printer

    printer = Printer(
        name="Sync Printer",
        serial_number="SYNCTEST001",
        ip_address="192.168.1.50",
        access_code="12345678",
    )
    db_session.add(printer)
    await db_session.commit()
    await db_session.refresh(printer)
    return printer


@pytest.fixture
async def slot_assignment(db_session, test_printer):
    from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

    assignment = SpoolmanSlotAssignment(
        printer_id=test_printer.id,
        ams_id=0,
        tray_id=0,
        spoolman_spool_id=42,
    )
    db_session.add(assignment)
    await db_session.commit()
    return assignment


def _make_spoolman_client(spools=None):
    client = MagicMock()
    client.base_url = "http://localhost:7912"
    client.health_check = AsyncMock(return_value=True)
    client.get_all_spools = AsyncMock(return_value=[SAMPLE_SPOOL] if spools is None else spools)
    client.update_spool_full = AsyncMock(return_value=SAMPLE_SPOOL)
    return client


def _make_printer_state(remain=75):
    state = MagicMock()
    state.raw_data = {
        "ams": [
            {
                "id": 0,
                "tray": [{"id": 0, "remain": remain}],
            }
        ]
    }
    return state


class TestSyncSpoolmanAmsWeights:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_happy_path_synced_count(
        self, async_client: AsyncClient, sync_settings, test_printer, slot_assignment
    ):
        """POST /sync-ams-weights syncs one spool, returns synced=1, skipped=0."""
        spoolman_client = _make_spoolman_client()
        printer_state = _make_printer_state(remain=75)

        with (
            patch(
                "backend.app.api.routes.spoolman_inventory._get_client",
                AsyncMock(return_value=spoolman_client),
            ),
            patch(
                "backend.app.api.routes.spoolman_inventory.printer_manager"
            ) as pm_mock,
        ):
            pm_mock.get_status = MagicMock(return_value=printer_state)

            response = await async_client.post("/api/v1/spoolman/inventory/sync-ams-weights")

        assert response.status_code == 200
        body = response.json()
        assert body["synced"] == 1
        assert body["skipped"] == 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_weight_calculated_correctly(
        self, async_client: AsyncClient, sync_settings, test_printer, slot_assignment
    ):
        """Remaining weight = round(label_weight * remain / 100, 1)."""
        spoolman_client = _make_spoolman_client()
        printer_state = _make_printer_state(remain=75)

        with (
            patch(
                "backend.app.api.routes.spoolman_inventory._get_client",
                AsyncMock(return_value=spoolman_client),
            ),
            patch(
                "backend.app.api.routes.spoolman_inventory.printer_manager"
            ) as pm_mock,
        ):
            pm_mock.get_status = MagicMock(return_value=printer_state)

            await async_client.post("/api/v1/spoolman/inventory/sync-ams-weights")

        spoolman_client.update_spool_full.assert_called_once_with(42, remaining_weight=750.0)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_printer_offline_skipped(
        self, async_client: AsyncClient, sync_settings, test_printer, slot_assignment
    ):
        """Spools whose printer is offline are counted as skipped, not synced."""
        spoolman_client = _make_spoolman_client()

        with (
            patch(
                "backend.app.api.routes.spoolman_inventory._get_client",
                AsyncMock(return_value=spoolman_client),
            ),
            patch(
                "backend.app.api.routes.spoolman_inventory.printer_manager"
            ) as pm_mock,
        ):
            pm_mock.get_status = MagicMock(return_value=None)

            response = await async_client.post("/api/v1/spoolman/inventory/sync-ams-weights")

        assert response.status_code == 200
        body = response.json()
        assert body["synced"] == 0
        assert body["skipped"] == 1
        spoolman_client.update_spool_full.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_spool_full_error_counted_as_skipped(
        self, async_client: AsyncClient, sync_settings, test_printer, slot_assignment
    ):
        """update_spool_full raising HTTPException counts as skipped, not synced."""
        from fastapi import HTTPException

        spoolman_client = _make_spoolman_client()
        spoolman_client.update_spool_full = AsyncMock(side_effect=HTTPException(status_code=503))
        printer_state = _make_printer_state(remain=50)

        with (
            patch(
                "backend.app.api.routes.spoolman_inventory._get_client",
                AsyncMock(return_value=spoolman_client),
            ),
            patch(
                "backend.app.api.routes.spoolman_inventory.printer_manager"
            ) as pm_mock,
        ):
            pm_mock.get_status = MagicMock(return_value=printer_state)

            response = await async_client.post("/api/v1/spoolman/inventory/sync-ams-weights")

        assert response.status_code == 200
        body = response.json()
        assert body["synced"] == 0
        assert body["skipped"] == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_invalid_remain_value_skipped(
        self, async_client: AsyncClient, sync_settings, test_printer, slot_assignment
    ):
        """Non-numeric remain value in AMS data is counted as skipped."""
        spoolman_client = _make_spoolman_client()
        printer_state = _make_printer_state(remain="notanumber")

        with (
            patch(
                "backend.app.api.routes.spoolman_inventory._get_client",
                AsyncMock(return_value=spoolman_client),
            ),
            patch(
                "backend.app.api.routes.spoolman_inventory.printer_manager"
            ) as pm_mock,
        ):
            pm_mock.get_status = MagicMock(return_value=printer_state)

            response = await async_client.post("/api/v1/spoolman/inventory/sync-ams-weights")

        assert response.status_code == 200
        body = response.json()
        assert body["synced"] == 0
        assert body["skipped"] == 1
        spoolman_client.update_spool_full.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spool_missing_from_spoolman_skipped(
        self, async_client: AsyncClient, sync_settings, test_printer, slot_assignment
    ):
        """Spools not present in Spoolman are counted as skipped."""
        spoolman_client = _make_spoolman_client(spools=[])  # empty — spool 42 is gone
        printer_state = _make_printer_state(remain=50)

        with (
            patch(
                "backend.app.api.routes.spoolman_inventory._get_client",
                AsyncMock(return_value=spoolman_client),
            ),
            patch(
                "backend.app.api.routes.spoolman_inventory.printer_manager"
            ) as pm_mock,
        ):
            pm_mock.get_status = MagicMock(return_value=printer_state)

            response = await async_client.post("/api/v1/spoolman/inventory/sync-ams-weights")

        assert response.status_code == 200
        body = response.json()
        assert body["synced"] == 0
        assert body["skipped"] == 1
