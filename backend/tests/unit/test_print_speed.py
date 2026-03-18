"""Unit tests for the print speed control endpoint.

Tests POST /api/v1/printers/{printer_id}/print-speed?mode=N
where mode is 1=silent, 2=standard, 3=sport, 4=ludicrous.
"""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient


class TestPrintSpeedAPI:
    """Tests for the print speed control endpoint."""

    @pytest.mark.asyncio
    async def test_print_speed_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent printer."""
        response = await async_client.post("/api/v1/printers/99999/print-speed?mode=2")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_print_speed_not_connected(self, async_client: AsyncClient, printer_factory):
        """Verify error when printer is not connected."""
        printer = await printer_factory(name="Disconnected Printer")

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = None

            response = await async_client.post(f"/api/v1/printers/{printer.id}/print-speed?mode=2")

            assert response.status_code == 400
            assert "not connected" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_print_speed_failure(self, async_client: AsyncClient, printer_factory):
        """Verify 500 when client fails to set speed."""
        printer = await printer_factory(name="Test Printer")

        mock_client = MagicMock()
        mock_client.set_print_speed.return_value = False

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/print-speed?mode=2")

            assert response.status_code == 500
            assert "failed" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "mode, expected_name",
        [
            (1, "Silent"),
            (2, "Standard"),
            (3, "Sport"),
            (4, "Ludicrous"),
        ],
    )
    async def test_print_speed_success(self, async_client: AsyncClient, printer_factory, mode, expected_name):
        """Verify successful speed change for each mode (1-4)."""
        printer = await printer_factory(name="Test Printer")

        mock_client = MagicMock()
        mock_client.set_print_speed.return_value = True

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/print-speed?mode={mode}")

            assert response.status_code == 200
            result = response.json()
            assert result["success"] is True
            assert expected_name in result["message"]
            mock_client.set_print_speed.assert_called_once_with(mode)
