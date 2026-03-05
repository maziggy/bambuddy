"""Integration tests for AMS Labels API endpoints."""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from backend.app.models.ams_label import AmsLabel


class TestAmsLabelsAPI:
    """Integration tests for /api/v1/printers/{printer_id}/ams-labels endpoints."""

    def _mock_printer_state(self, ams_units=None):
        """Create a mock printer state with AMS data."""
        state = MagicMock()
        state.connected = True
        state.raw_data = {
            "ams": ams_units
            or [
                {"id": "0", "sn": "AMS_SERIAL_0"},
                {"id": "1", "sn": "AMS_SERIAL_1"},
            ],
        }
        return state

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_labels_empty(self, async_client: AsyncClient, printer_factory):
        """Returns empty dict when no labels are saved."""
        printer = await printer_factory()
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_status.return_value = self._mock_printer_state()
            response = await async_client.get(f"/api/v1/printers/{printer.id}/ams-labels")
        assert response.status_code == 200
        assert response.json() == {}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_save_label_with_serial(self, async_client: AsyncClient, printer_factory):
        """Save a label keyed by AMS serial number."""
        printer = await printer_factory()
        response = await async_client.put(
            f"/api/v1/printers/{printer.id}/ams-labels/0",
            json={"label": "Workshop AMS", "ams_serial": "AMS_SERIAL_0"},
        )
        assert response.status_code == 200
        assert response.json() == {"ams_id": 0, "label": "Workshop AMS"}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_save_label_without_serial_uses_synthetic_key(
        self, async_client: AsyncClient, printer_factory, db_session
    ):
        """When no serial is provided, a synthetic key p{printer_id}a{ams_id} is used."""
        printer = await printer_factory()
        response = await async_client.put(
            f"/api/v1/printers/{printer.id}/ams-labels/2",
            json={"label": "Old Firmware AMS"},
        )
        assert response.status_code == 200

        # Verify the synthetic key was stored
        from sqlalchemy import select

        result = await db_session.execute(select(AmsLabel).where(AmsLabel.ams_serial_number == f"p{printer.id}a2"))
        label = result.scalar_one_or_none()
        assert label is not None
        assert label.label == "Old Firmware AMS"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_save_label_whitespace_serial_uses_synthetic_key(
        self, async_client: AsyncClient, printer_factory, db_session
    ):
        """Whitespace-only serial falls back to synthetic key."""
        printer = await printer_factory()
        response = await async_client.put(
            f"/api/v1/printers/{printer.id}/ams-labels/0",
            json={"label": "Whitespace Test", "ams_serial": "   "},
        )
        assert response.status_code == 200

        from sqlalchemy import select

        result = await db_session.execute(select(AmsLabel).where(AmsLabel.ams_serial_number == f"p{printer.id}a0"))
        label = result.scalar_one_or_none()
        assert label is not None
        assert label.label == "Whitespace Test"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_save_label_updates_existing(self, async_client: AsyncClient, printer_factory):
        """Saving a label with the same serial updates the existing record."""
        printer = await printer_factory()
        await async_client.put(
            f"/api/v1/printers/{printer.id}/ams-labels/0",
            json={"label": "Original Name", "ams_serial": "SN123"},
        )
        response = await async_client.put(
            f"/api/v1/printers/{printer.id}/ams-labels/0",
            json={"label": "Updated Name", "ams_serial": "SN123"},
        )
        assert response.status_code == 200
        assert response.json()["label"] == "Updated Name"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_save_label_printer_not_found(self, async_client: AsyncClient):
        """Returns 404 when printer does not exist."""
        response = await async_client.put(
            "/api/v1/printers/99999/ams-labels/0",
            json={"label": "Ghost Printer"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_save_label_validation_empty_label(self, async_client: AsyncClient, printer_factory):
        """Rejects empty label."""
        printer = await printer_factory()
        response = await async_client.put(
            f"/api/v1/printers/{printer.id}/ams-labels/0",
            json={"label": ""},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_labels_resolves_serial_to_ams_id(self, async_client: AsyncClient, printer_factory):
        """GET returns labels keyed by ams_id, resolved from live printer state."""
        printer = await printer_factory()

        # Save a label with a known serial
        await async_client.put(
            f"/api/v1/printers/{printer.id}/ams-labels/0",
            json={"label": "Silk Colours", "ams_serial": "AMS_SERIAL_0"},
        )

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_status.return_value = self._mock_printer_state()
            response = await async_client.get(f"/api/v1/printers/{printer.id}/ams-labels")

        assert response.status_code == 200
        data = response.json()
        assert data.get("0") == "Silk Colours"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_labels_no_printer_state(self, async_client: AsyncClient, printer_factory):
        """GET returns empty when printer has no live state."""
        printer = await printer_factory()
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_status.return_value = None
            response = await async_client.get(f"/api/v1/printers/{printer.id}/ams-labels")
        assert response.status_code == 200
        assert response.json() == {}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_label(self, async_client: AsyncClient, printer_factory, db_session):
        """Delete removes the label from the database."""
        printer = await printer_factory()
        await async_client.put(
            f"/api/v1/printers/{printer.id}/ams-labels/0",
            json={"label": "To Delete", "ams_serial": "DEL_SN"},
        )

        response = await async_client.delete(f"/api/v1/printers/{printer.id}/ams-labels/0?ams_serial=DEL_SN")
        assert response.status_code == 200
        assert response.json() == {"success": True}

        # Verify it's gone
        from sqlalchemy import select

        result = await db_session.execute(select(AmsLabel).where(AmsLabel.ams_serial_number == "DEL_SN"))
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_nonexistent_label_succeeds(self, async_client: AsyncClient, printer_factory):
        """Delete returns success even if no label exists (idempotent)."""
        printer = await printer_factory()
        response = await async_client.delete(f"/api/v1/printers/{printer.id}/ams-labels/0?ams_serial=NONEXISTENT")
        assert response.status_code == 200
        assert response.json() == {"success": True}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_label_whitespace_serial_uses_synthetic_key(
        self, async_client: AsyncClient, printer_factory, db_session
    ):
        """Delete with whitespace serial falls back to synthetic key."""
        printer = await printer_factory()
        # Save with synthetic key
        await async_client.put(
            f"/api/v1/printers/{printer.id}/ams-labels/0",
            json={"label": "Synthetic Label"},
        )

        response = await async_client.delete(f"/api/v1/printers/{printer.id}/ams-labels/0?ams_serial=%20%20")
        assert response.status_code == 200

        from sqlalchemy import select

        result = await db_session.execute(select(AmsLabel).where(AmsLabel.ams_serial_number == f"p{printer.id}a0"))
        assert result.scalar_one_or_none() is None
