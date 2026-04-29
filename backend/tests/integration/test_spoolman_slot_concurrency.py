"""T-Gap 3: Concurrency test for POST /slot-assignments upsert+cleanup race."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

SAMPLE_SPOOL = {
    "id": 10,
    "filament": {
        "id": 1,
        "name": "PLA Basic",
        "material": "PLA",
        "color_hex": "FF0000",
        "weight": 1000,
        "vendor": {"id": 1, "name": "Test Brand"},
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
async def slot_settings(db_session):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
    await db_session.commit()


@pytest.fixture
async def test_printer(db_session):
    from backend.app.models.printer import Printer

    printer = Printer(
        name="Concurrency Test Printer",
        serial_number="CONCTEST001",
        ip_address="192.168.1.99",
        access_code="12345678",
    )
    db_session.add(printer)
    await db_session.commit()
    await db_session.refresh(printer)
    return printer


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.base_url = "http://localhost:7912"
    client.health_check = AsyncMock(return_value=True)
    client.get_spool = AsyncMock(return_value=SAMPLE_SPOOL)

    with patch(
        "backend.app.api.routes.spoolman_inventory._get_client",
        AsyncMock(return_value=client),
    ):
        yield client


class TestSlotAssignmentConcurrency:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_concurrent_assign_same_slot_idempotent(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_client, db_session
    ):
        """Concurrent POST requests for the same slot must not produce duplicate rows."""
        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

        payload = {
            "spoolman_spool_id": 10,
            "printer_id": test_printer.id,
            "ams_id": 0,
            "tray_id": 0,
        }

        async def assign():
            return await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments",
                json=payload,
            )

        responses = await asyncio.gather(assign(), assign(), assign())
        for resp in responses:
            assert resp.status_code == 200

        # Exactly one row for this (printer, ams, tray) combination
        result = await db_session.execute(
            select(SpoolmanSlotAssignment).where(
                SpoolmanSlotAssignment.printer_id == test_printer.id,
                SpoolmanSlotAssignment.ams_id == 0,
                SpoolmanSlotAssignment.tray_id == 0,
            )
        )
        rows = result.scalars().all()
        assert len(rows) == 1
        assert rows[0].spoolman_spool_id == 10

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reassign_slot_updates_spool_id(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_client, db_session
    ):
        """Re-assigning a slot to a different spool updates the existing row."""
        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

        base = {"printer_id": test_printer.id, "ams_id": 1, "tray_id": 2}

        resp1 = await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={**base, "spoolman_spool_id": 10},
        )
        assert resp1.status_code == 200

        # Re-assign same slot to a different spool
        mock_client.get_spool.return_value = {**SAMPLE_SPOOL, "id": 20}
        resp2 = await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={**base, "spoolman_spool_id": 20},
        )
        assert resp2.status_code == 200

        # Only one row; spool_id updated to 20
        result = await db_session.execute(
            select(SpoolmanSlotAssignment).where(
                SpoolmanSlotAssignment.printer_id == test_printer.id,
                SpoolmanSlotAssignment.ams_id == 1,
                SpoolmanSlotAssignment.tray_id == 2,
            )
        )
        rows = result.scalars().all()
        assert len(rows) == 1
        assert rows[0].spoolman_spool_id == 20
