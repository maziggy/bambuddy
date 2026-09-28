"""Unit tests for the bed-jog and home-axes endpoints (#791).

Tests:
  POST /api/v1/printers/{printer_id}/bed-jog?distance=<mm>
  POST /api/v1/printers/{printer_id}/home-axes?axes=<z|xy|all>

``distance`` is a signed nozzle-bed gap and ``axes`` is accepted but always
homes everything — both endpoints once took a second parameter that made them
do something more clever, and both parameters are gone for the same reason
(#2579, #1052): on a machine with a nozzle and a plate, the clever version is
the one that ends with them touching.
"""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient


class TestBedJogAPI:
    @pytest.mark.asyncio
    async def test_bed_jog_not_found(self, async_client: AsyncClient):
        response = await async_client.post("/api/v1/printers/99999/bed-jog?distance=10")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_bed_jog_zero_distance_rejected(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P1")
        response = await async_client.post(f"/api/v1/printers/{printer.id}/bed-jog?distance=0")
        assert response.status_code == 400
        assert "distance" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_bed_jog_too_large_rejected(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P1")
        response = await async_client.post(f"/api/v1/printers/{printer.id}/bed-jog?distance=500")
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_bed_jog_not_connected(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="Disconnected")
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = None
            response = await async_client.post(f"/api/v1/printers/{printer.id}/bed-jog?distance=10")
            assert response.status_code == 400
            assert "not connected" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_bed_jog_send_failure(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P1")
        mock_client = MagicMock()
        mock_client.send_gcode.return_value = False
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/bed-jog?distance=10")
            assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_bed_jog_emits_bare_move_and_never_touches_m211(self, async_client: AsyncClient, printer_factory):
        """A jog must be a bare relative move — no M211 at all (#2579).

        Not because a bare move is clamped: the firmware ignores soft endstops
        on MQTT G-code whatever we send. But ``M211 S0`` disabled them
        *globally*, so Bambuddy was also taking away the protection on the
        printer's own touchscreen, and that part was ours to stop doing."""
        printer = await printer_factory(name="P1")
        mock_client = MagicMock()
        mock_client.send_gcode.return_value = True
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/bed-jog?distance=10")
            assert response.status_code == 200
            sent_gcode = mock_client.send_gcode.call_args[0][0]
            assert "M211" not in sent_gcode, f"must not touch M211, got: {sent_gcode!r}"
            assert sent_gcode.splitlines() == ["G91", "G1 Z10.00 F600", "G90"]

    @pytest.mark.asyncio
    async def test_bed_jog_never_touches_m211_even_with_stray_force(self, async_client: AsyncClient, printer_factory):
        """#2579 core regression: the endpoint must NEVER emit any M211. A stray
        ?force=true from an old client is ignored (FastAPI drops the unknown
        param) and the move stays a bare relative move — no M211 S0 (the disable
        that drove the nozzle into the bed) and no M211 S1 either.
        """
        printer = await printer_factory(name="H2C", model="H2C")
        mock_client = MagicMock()
        mock_client.send_gcode.return_value = True
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/bed-jog?distance=50&force=true")
            assert response.status_code == 200
            sent_gcode = mock_client.send_gcode.call_args[0][0]
            assert "M211" not in sent_gcode, f"must never touch M211, got: {sent_gcode!r}"
            assert "G1 Z50.00" in sent_gcode

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "model",
        [
            # bed-on-Z
            "X1C",
            "P1S",
            "H2D",
            "H2S",
            "H2C",
            "P2S",
            # bed-slingers — the Z axis carries the toolhead instead
            "A1",
            "A1 Mini",
            "A1MINI",
            "A1-MINI",
            "A2L",
            "N1",
            "N2S",
            "N9",
        ],
    )
    @pytest.mark.parametrize("distance", [-10, 10])
    async def test_bed_jog_sends_the_distance_unchanged_on_every_model(
        self, async_client: AsyncClient, printer_factory, model, distance
    ):
        """``distance`` is a nozzle-bed gap, and a gap is a gap on every printer.

        ``G1 Z+`` opens the nozzle-bed gap whether the bed drops away from the
        nozzle (X1 / P1 / H2) or the toolhead rises off the plate (A1 / A2L) —
        that is what the Z axis *means*, not a per-family convention. So one
        API call describes one physical outcome everywhere, and the route has
        no model branch to get wrong.

        It had one once. #1334 was a bed-slinger owner clicking an arrow
        labelled "move the plate up" and watching the nozzle dive, and the fix
        inverted the G-code sign on A1 models. That made a documented
        model-independent parameter mean the opposite thing on those printers:
        @AQU4R1U5 asked for 5 mm of clearance through the API and got 5 mm less.
        """
        printer = await printer_factory(name=f"Test-{model}", model=model)
        mock_client = MagicMock()
        mock_client.send_gcode.return_value = True
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/bed-jog?distance={distance}")
            assert response.status_code == 200
            sent_gcode = mock_client.send_gcode.call_args[0][0]
            assert f"G1 Z{distance:.2f} F600" in sent_gcode, f"{model}: got {sent_gcode!r}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("model", ["A1", "A1 Mini", "A2L", "N1", "N2S", "N9"])
    async def test_bed_jog_positive_is_the_safe_direction_on_bed_slingers(
        self, async_client: AsyncClient, printer_factory, model
    ):
        """The one that bit @AQU4R1U5: asking for clearance must never close the gap.

        Spelled out separately from the pass-through test above because this is
        the property that matters to anyone driving the API from a script — the
        sign of ``distance`` is the only thing standing between "lift the nozzle
        off my print" and a nozzle in the plate, and it must not depend on which
        printer is on the other end.
        """
        printer = await printer_factory(name=f"Test-{model}", model=model)
        mock_client = MagicMock()
        mock_client.send_gcode.return_value = True
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/bed-jog?distance=5")
            assert response.status_code == 200
            sent_gcode = mock_client.send_gcode.call_args[0][0]
            assert "G1 Z-" not in sent_gcode, f"{model}: clearance request closed the gap — {sent_gcode!r}"
            assert "G1 Z5.00" in sent_gcode


class TestHomeAxesAPI:
    @pytest.mark.asyncio
    async def test_home_axes_not_found(self, async_client: AsyncClient):
        response = await async_client.post("/api/v1/printers/99999/home-axes?axes=z")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_home_axes_invalid(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P1")
        response = await async_client.post(f"/api/v1/printers/{printer.id}/home-axes?axes=bogus")
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.parametrize("axes", ["z", "xy", "all"])
    async def test_home_axes_always_runs_full_home(self, async_client: AsyncClient, printer_factory, axes):
        # Regression for #1052: regardless of the axes argument, the endpoint must send a bare
        # `G28` so the printer's safe auto-home sequence (toolhead park → XY home → Z home) runs.
        # Sending `G28 Z` alone on H2C/H2D/H2S/X1 can crash the bed into the toolhead.
        printer = await printer_factory(name="P1")
        mock_client = MagicMock()
        mock_client.send_gcode.return_value = True
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/home-axes?axes={axes}")
            assert response.status_code == 200
            mock_client.send_gcode.assert_called_once_with("G28")

    @pytest.mark.asyncio
    async def test_home_axes_not_connected(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="D")
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = None
            response = await async_client.post(f"/api/v1/printers/{printer.id}/home-axes?axes=z")
            assert response.status_code == 400
