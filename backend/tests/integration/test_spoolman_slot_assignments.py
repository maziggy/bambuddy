"""Integration tests for Spoolman slot-assignment endpoints.

Tests for:
  POST   /api/v1/spoolman/inventory/slot-assignments
  DELETE /api/v1/spoolman/inventory/slot-assignments/{spoolman_spool_id}
  GET    /api/v1/spoolman/inventory/slot-assignments?printer_id=&ams_id=&tray_id=
  GET    /api/v1/spoolman/inventory/slot-assignments/all[?printer_id=]

Slot assignments are now stored in the local ``spoolman_slot_assignments`` table.
Spoolman's ``spool.location`` field is NOT touched by any of these endpoints.
"""

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
        name="Test Printer",
        serial_number="SLOTTEST001",
        ip_address="192.168.1.100",
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


class TestAssignSpoolmanSlot:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_assign_inserts_local_row(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_client
    ):
        """POST /slot-assignments creates a row visible via the /all endpoint."""
        response = await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={
                "spoolman_spool_id": 10,
                "printer_id": test_printer.id,
                "ams_id": 0,
                "tray_id": 0,
            },
        )

        assert response.status_code == 200
        all_resp = await async_client.get(
            "/api/v1/spoolman/inventory/slot-assignments/all",
            params={"printer_id": test_printer.id},
        )
        assert all_resp.status_code == 200
        rows = all_resp.json()
        assert len(rows) == 1
        assert rows[0]["spoolman_spool_id"] == 10
        assert rows[0]["ams_id"] == 0
        assert rows[0]["tray_id"] == 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_assign_does_not_call_update_spool(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_client
    ):
        """POST /slot-assignments must NOT write to Spoolman's location field."""
        response = await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={
                "spoolman_spool_id": 10,
                "printer_id": test_printer.id,
                "ams_id": 0,
                "tray_id": 0,
            },
        )

        assert response.status_code == 200
        mock_client.update_spool.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_assign_returns_inventory_spool(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_client
    ):
        """POST /slot-assignments response is mapped to InventorySpool format."""
        response = await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={
                "spoolman_spool_id": 10,
                "printer_id": test_printer.id,
                "ams_id": 0,
                "tray_id": 0,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == 10
        assert body["material"] == "PLA"
        assert body["data_origin"] == "spoolman"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_assign_upserts_on_conflict(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_client
    ):
        """POST /slot-assignments twice for the same slot replaces the old spool ID."""
        # First assign spool 99
        await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={
                "spoolman_spool_id": 99,
                "printer_id": test_printer.id,
                "ams_id": 0,
                "tray_id": 0,
            },
        )
        # Re-assign spool 10 to the same slot
        response = await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={
                "spoolman_spool_id": 10,
                "printer_id": test_printer.id,
                "ams_id": 0,
                "tray_id": 0,
            },
        )
        assert response.status_code == 200

        # The /all endpoint must report exactly one row for this slot with spool_id=10
        all_resp = await async_client.get(
            "/api/v1/spoolman/inventory/slot-assignments/all",
            params={"printer_id": test_printer.id},
        )
        assert all_resp.status_code == 200
        rows = all_resp.json()
        matched = [r for r in rows if r["ams_id"] == 0 and r["tray_id"] == 0]
        assert len(matched) == 1
        assert matched[0]["spoolman_spool_id"] == 10

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_assign_printer_not_found(
        self, async_client: AsyncClient, slot_settings, mock_client
    ):
        """POST /slot-assignments with unknown printer_id returns 404."""
        response = await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={
                "spoolman_spool_id": 10,
                "printer_id": 99999,
                "ams_id": 0,
                "tray_id": 0,
            },
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_assign_invalid_spool_id(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_client
    ):
        """POST /slot-assignments with spool_id=0 returns 422 (gt=0 validation)."""
        response = await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={
                "spoolman_spool_id": 0,
                "printer_id": test_printer.id,
                "ams_id": 0,
                "tray_id": 0,
            },
        )

        assert response.status_code == 422


class TestUnassignSpoolmanSlot:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unassign_deletes_local_row(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_client
    ):
        """DELETE /slot-assignments/{id} removes the row so /all no longer lists it."""
        # First assign spool 10
        await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={
                "spoolman_spool_id": 10,
                "printer_id": test_printer.id,
                "ams_id": 0,
                "tray_id": 0,
            },
        )
        # Then unassign
        response = await async_client.delete(
            "/api/v1/spoolman/inventory/slot-assignments/10"
        )
        assert response.status_code == 200

        # The /all endpoint must now return an empty list for this printer
        all_resp = await async_client.get(
            "/api/v1/spoolman/inventory/slot-assignments/all",
            params={"printer_id": test_printer.id},
        )
        assert all_resp.status_code == 200
        assert all_resp.json() == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unassign_does_not_call_update_spool(
        self, async_client: AsyncClient, slot_settings, mock_client
    ):
        """DELETE /slot-assignments/{id} must NOT touch Spoolman's location field."""
        response = await async_client.delete(
            "/api/v1/spoolman/inventory/slot-assignments/10"
        )

        assert response.status_code == 200
        mock_client.update_spool.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unassign_returns_inventory_spool(
        self, async_client: AsyncClient, slot_settings, mock_client
    ):
        """DELETE /slot-assignments/{id} returns the spool in InventorySpool format."""
        response = await async_client.delete(
            "/api/v1/spoolman/inventory/slot-assignments/10"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == 10
        assert body["data_origin"] == "spoolman"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unassign_invalid_id(
        self, async_client: AsyncClient, slot_settings, mock_client
    ):
        """DELETE /slot-assignments/0 returns 422 (gt=0 path validation)."""
        response = await async_client.delete(
            "/api/v1/spoolman/inventory/slot-assignments/0"
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unassign_succeeds_when_spool_deleted_in_spoolman(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_client, db_session
    ):
        """DELETE /slot-assignments/{id} returns 200 even when the spool no longer exists in Spoolman.

        The local row must be removed regardless — the caller should not see an error just
        because Spoolman has already discarded the spool.
        """
        from sqlalchemy import select

        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
        from backend.app.services.spoolman import SpoolmanNotFoundError

        # Create the assignment first
        await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={"spoolman_spool_id": 10, "printer_id": test_printer.id, "ams_id": 0, "tray_id": 0},
        )

        # Spool 10 has since been deleted from Spoolman
        mock_client.get_spool = AsyncMock(side_effect=SpoolmanNotFoundError("spool 10 not found"))

        response = await async_client.delete(
            "/api/v1/spoolman/inventory/slot-assignments/10"
        )

        assert response.status_code == 200
        assert response.json().get("id") == 10

        # Local row must be gone
        result = await db_session.execute(
            select(SpoolmanSlotAssignment).where(
                SpoolmanSlotAssignment.printer_id == test_printer.id,
                SpoolmanSlotAssignment.ams_id == 0,
                SpoolmanSlotAssignment.tray_id == 0,
            )
        )
        assert result.scalar_one_or_none() is None


class TestGetSpoolmanSlotAssignment:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_returns_matched_spool(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_client
    ):
        """GET /slot-assignments returns the spool whose ID is in the local table."""
        # First assign so the row exists
        await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={"spoolman_spool_id": 10, "printer_id": test_printer.id, "ams_id": 0, "tray_id": 0},
        )
        mock_client.get_spool.reset_mock()

        response = await async_client.get(
            "/api/v1/spoolman/inventory/slot-assignments",
            params={"printer_id": test_printer.id, "ams_id": 0, "tray_id": 0},
        )

        assert response.status_code == 200
        body = response.json()
        assert body is not None
        assert body["id"] == 10
        mock_client.get_spool.assert_awaited_once_with(10)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_returns_null_when_no_assignment(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_client
    ):
        """GET /slot-assignments returns null when no local row exists for the slot."""
        response = await async_client.get(
            "/api/v1/spoolman/inventory/slot-assignments",
            params={"printer_id": test_printer.id, "ams_id": 1, "tray_id": 0},
        )

        assert response.status_code == 200
        assert response.json() is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_printer_not_found(
        self, async_client: AsyncClient, slot_settings, mock_client
    ):
        """GET /slot-assignments with unknown printer_id returns 404."""
        response = await async_client.get(
            "/api/v1/spoolman/inventory/slot-assignments",
            params={"printer_id": 99999, "ams_id": 0, "tray_id": 0},
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_missing_params(
        self, async_client: AsyncClient, slot_settings, mock_client
    ):
        """GET /slot-assignments without required params returns 422."""
        response = await async_client.get(
            "/api/v1/spoolman/inventory/slot-assignments"
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_returns_null_and_cleans_stale_when_spool_deleted_in_spoolman(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_client, db_session
    ):
        """GET /slot-assignments returns null and removes the stale row when Spoolman returns 404."""
        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
        from backend.app.services.spoolman import SpoolmanNotFoundError

        # Assign spool 10 first
        await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={"spoolman_spool_id": 10, "printer_id": test_printer.id, "ams_id": 0, "tray_id": 0},
        )

        # Simulate spool 10 being deleted from Spoolman (404 via SpoolmanNotFoundError)
        mock_client.get_spool = AsyncMock(side_effect=SpoolmanNotFoundError("spool 10 not found"))

        response = await async_client.get(
            "/api/v1/spoolman/inventory/slot-assignments",
            params={"printer_id": test_printer.id, "ams_id": 0, "tray_id": 0},
        )

        assert response.status_code == 200
        assert response.json() is None

        # Stale row must have been removed
        await db_session.refresh(test_printer)  # ensure session is fresh
        result = await db_session.execute(
            select(SpoolmanSlotAssignment).where(
                SpoolmanSlotAssignment.printer_id == test_printer.id,
                SpoolmanSlotAssignment.ams_id == 0,
                SpoolmanSlotAssignment.tray_id == 0,
            )
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_propagates_503_from_spoolman(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_client
    ):
        """GET /slot-assignments propagates a 503 from Spoolman instead of silently returning null."""
        from backend.app.services.spoolman import SpoolmanUnavailableError

        # Assign spool 10 first so a local row exists
        await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={"spoolman_spool_id": 10, "printer_id": test_printer.id, "ams_id": 0, "tray_id": 0},
        )

        # Simulate Spoolman being unreachable
        mock_client.get_spool = AsyncMock(side_effect=SpoolmanUnavailableError("timeout"))

        response = await async_client.get(
            "/api/v1/spoolman/inventory/slot-assignments",
            params={"printer_id": test_printer.id, "ams_id": 0, "tray_id": 0},
        )

        assert response.status_code == 503


class TestGetAllSpoolmanSlotAssignments:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_all_returns_empty_list(
        self, async_client: AsyncClient, slot_settings, mock_client
    ):
        """GET /slot-assignments/all returns [] when no assignments exist."""
        response = await async_client.get(
            "/api/v1/spoolman/inventory/slot-assignments/all"
        )

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_all_returns_all_rows(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_client
    ):
        """GET /slot-assignments/all returns all existing assignments."""
        await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={"spoolman_spool_id": 10, "printer_id": test_printer.id, "ams_id": 0, "tray_id": 0},
        )
        await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={"spoolman_spool_id": 20, "printer_id": test_printer.id, "ams_id": 0, "tray_id": 1},
        )

        response = await async_client.get("/api/v1/spoolman/inventory/slot-assignments/all")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2
        spool_ids = {r["spoolman_spool_id"] for r in body}
        assert spool_ids == {10, 20}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_all_filters_by_printer(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_client, db_session
    ):
        """GET /slot-assignments/all?printer_id=X only returns that printer's rows."""
        from backend.app.models.printer import Printer

        # Create a second printer via DB directly (no Spoolman mock needed for printer creation)
        other = Printer(
            name="Other Printer",
            serial_number="SLOTTEST002",
            ip_address="192.168.1.101",
            access_code="87654321",
        )
        db_session.add(other)
        await db_session.commit()
        await db_session.refresh(other)

        # Assign via API for test_printer
        await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={"spoolman_spool_id": 10, "printer_id": test_printer.id, "ams_id": 0, "tray_id": 0},
        )
        # Assign via API for other printer
        await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={"spoolman_spool_id": 99, "printer_id": other.id, "ams_id": 0, "tray_id": 0},
        )

        response = await async_client.get(
            "/api/v1/spoolman/inventory/slot-assignments/all",
            params={"printer_id": test_printer.id},
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["spoolman_spool_id"] == 10
        assert body[0]["printer_id"] == test_printer.id


class TestCascadeDeletePrinter:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_printer_removes_slot_assignments(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_client, db_session
    ):
        """DELETE /printers/{id} removes all slot assignments for that printer.

        SQLite does not enforce FK cascades automatically. The delete_printer
        endpoint must explicitly delete SpoolmanSlotAssignment rows so no
        orphaned rows survive after the printer record is gone.
        """
        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

        # Assign two spools to different AMS slots on the test printer
        for tray_id, spool_id in [(0, 10), (1, 20)]:
            await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments",
                json={
                    "spoolman_spool_id": spool_id,
                    "printer_id": test_printer.id,
                    "ams_id": 0,
                    "tray_id": tray_id,
                },
            )

        # Verify both rows exist
        pre = await db_session.execute(
            select(SpoolmanSlotAssignment).where(SpoolmanSlotAssignment.printer_id == test_printer.id)
        )
        assert len(pre.scalars().all()) == 2

        # Delete the printer
        del_resp = await async_client.delete(f"/api/v1/printers/{test_printer.id}")
        assert del_resp.status_code == 200

        # All slot assignment rows for the deleted printer must be gone
        post = await db_session.execute(
            select(SpoolmanSlotAssignment).where(SpoolmanSlotAssignment.printer_id == test_printer.id)
        )
        assert post.scalars().all() == []
