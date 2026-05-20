"""Unit tests for temperature and fan control API endpoints."""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient


class TestControlLimitsAPI:
    @pytest.mark.asyncio
    async def test_control_limits_not_found(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/printers/99999/control-limits")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_control_limits_x1c(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="X1C", model="X1C")
        response = await async_client.get(f"/api/v1/printers/{printer.id}/control-limits")
        assert response.status_code == 200
        data = response.json()
        assert data["bed_max"] == 120
        assert data["nozzle_max"] == 300
        assert 3 in data["fans"]


class TestBedTemperatureAPI:
    @pytest.mark.asyncio
    async def test_bed_temp_not_connected(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="Test", model="A1")
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = None
            response = await async_client.post(f"/api/v1/printers/{printer.id}/bed-temperature?target=60")
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_bed_temp_out_of_range(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="A1", model="A1")
        response = await async_client.post(f"/api/v1/printers/{printer.id}/bed-temperature?target=150")
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_bed_temp_success(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="A1", model="A1")
        mock_client = MagicMock()
        mock_client.set_bed_temperature.return_value = True
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/bed-temperature?target=60")
        assert response.status_code == 200
        mock_client.set_bed_temperature.assert_called_once_with(60)


class TestNozzleTemperatureAPI:
    @pytest.mark.asyncio
    async def test_nozzle_temp_second_nozzle_rejected_on_a1(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="A1", model="A1", nozzle_count=1)
        response = await async_client.post(
            f"/api/v1/printers/{printer.id}/nozzle-temperature?target=200&nozzle=1"
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_nozzle_temp_success(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="H2D", model="H2D", nozzle_count=2)
        mock_client = MagicMock()
        mock_client.set_nozzle_temperature.return_value = True
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(
                f"/api/v1/printers/{printer.id}/nozzle-temperature?target=220&nozzle=0"
            )
        assert response.status_code == 200
        mock_client.set_nozzle_temperature.assert_called_once_with(220, nozzle=0)


class TestFanSpeedAPI:
    @pytest.mark.asyncio
    async def test_fan_speed_invalid_fan_on_a1(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="A1", model="A1")
        response = await async_client.post(f"/api/v1/printers/{printer.id}/fan-speed?fan=3&speed_percent=50")
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_fan_speed_success(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="X1C", model="X1C")
        mock_client = MagicMock()
        mock_client.set_fan_speed.return_value = True
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/fan-speed?fan=1&speed_percent=80")
        assert response.status_code == 200
        mock_client.set_fan_speed.assert_called_once_with(1, 204)
