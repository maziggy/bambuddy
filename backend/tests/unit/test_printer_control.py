"""Unit tests for printer control endpoints (temperature, fan, jog, extrude, motion guards)."""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient


def _mock_client(state: str = "IDLE") -> MagicMock:
    mock = MagicMock()
    mock.state.state = state
    mock.send_gcode.return_value = True
    mock.move_axis.return_value = True
    mock.set_bed_temperature.return_value = True
    mock.set_nozzle_temperature.return_value = True
    mock.set_chamber_temperature.return_value = True
    mock.set_fan_speed.return_value = True
    return mock


class TestMotionGuard:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", ["RUNNING", "PAUSE"])
    async def test_bed_jog_blocked_while_printing(self, async_client: AsyncClient, printer_factory, state):
        printer = await printer_factory(name="P1", model="P1S")
        mock_client = _mock_client(state)
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/bed-jog?distance=10")
            assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_home_axes_blocked_while_printing(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P1", model="P1S")
        mock_client = _mock_client("RUNNING")
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/home-axes?axes=all")
            assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_jog_blocked_while_printing(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P1", model="P1S")
        mock_client = _mock_client("RUNNING")
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/jog?axis=X&distance=10")
            assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_extrude_blocked_while_printing(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P1", model="P1S")
        mock_client = _mock_client("PAUSE")
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/extrude?distance=5")
            assert response.status_code == 409


class TestTemperatureAPI:
    @pytest.mark.asyncio
    async def test_set_bed_temperature(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P1", model="P1S")
        mock_client = _mock_client()
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/temperature/bed?target=60")
            assert response.status_code == 200
            mock_client.set_bed_temperature.assert_called_once_with(60)

    @pytest.mark.asyncio
    async def test_set_nozzle_temperature_dual(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="H2D", model="H2D")
        mock_client = _mock_client()
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(
                f"/api/v1/printers/{printer.id}/temperature/nozzle?target=220&nozzle=1"
            )
            assert response.status_code == 200
            mock_client.set_nozzle_temperature.assert_called_once_with(220, nozzle=1)

    @pytest.mark.asyncio
    async def test_chamber_temp_rejected_on_a1(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="A1", model="A1")
        mock_client = _mock_client()
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/temperature/chamber?target=45")
            assert response.status_code == 400
            mock_client.set_chamber_temperature.assert_not_called()

    @pytest.mark.asyncio
    async def test_chamber_temp_allowed_on_x1(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="X1", model="X1C")
        mock_client = _mock_client()
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/temperature/chamber?target=45")
            assert response.status_code == 200
            mock_client.set_chamber_temperature.assert_called_once_with(45)


class TestFanAPI:
    @pytest.mark.asyncio
    async def test_fan_percent_to_255(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P1", model="P1S")
        mock_client = _mock_client()
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/fan?fan=1&percent=50")
            assert response.status_code == 200
            mock_client.set_fan_speed.assert_called_once_with(1, 128)

    @pytest.mark.asyncio
    async def test_fan_full_speed(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P1", model="P1S")
        mock_client = _mock_client()
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/fan?fan=2&percent=100")
            assert response.status_code == 200
            mock_client.set_fan_speed.assert_called_once_with(2, 255)


class TestJogAPI:
    @pytest.mark.asyncio
    async def test_jog_x_uses_move_axis(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P1", model="P1S")
        mock_client = _mock_client()
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/jog?axis=X&distance=10")
            assert response.status_code == 200
            mock_client.move_axis.assert_called_once_with("X", 10)

    @pytest.mark.asyncio
    async def test_jog_z_a1_inverts_sign(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="A1", model="A1 Mini")
        mock_client = _mock_client()
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/jog?axis=Z&distance=-10")
            assert response.status_code == 200
            sent_gcode = mock_client.send_gcode.call_args[0][0]
            assert "G1 Z10.00" in sent_gcode

    @pytest.mark.asyncio
    async def test_jog_z_x1_passes_through(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="X1", model="X1C")
        mock_client = _mock_client()
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/jog?axis=Z&distance=-10")
            assert response.status_code == 200
            sent_gcode = mock_client.send_gcode.call_args[0][0]
            assert "G1 Z-10.00" in sent_gcode


class TestExtrudeAPI:
    @pytest.mark.asyncio
    async def test_extrude_success(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P1", model="P1S")
        mock_client = _mock_client()
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/extrude?distance=5&speed=300")
            assert response.status_code == 200
            sent_gcode = mock_client.send_gcode.call_args[0][0]
            assert "G1 E5.00" in sent_gcode
            assert "F300" in sent_gcode

    @pytest.mark.asyncio
    async def test_extrude_too_large_rejected(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P1", model="P1S")
        mock_client = _mock_client()
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/extrude?distance=250")
            assert response.status_code == 400
