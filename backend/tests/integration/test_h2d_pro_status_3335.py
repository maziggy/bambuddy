"""An H2D Pro reports its chamber in status (#3335).

The status route drops the chamber readings of models without a chamber
sensor and reports whether the chamber heater can be set. An H2D Pro, stored
as "H2D Pro", was treated as having neither.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.bambu_mqtt import PrinterState

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.mark.parametrize("model", ["H2D Pro", "X1E"])
async def test_the_chamber_is_reported_and_can_be_heated(async_client, printer_factory, model):
    printer = await printer_factory(model=model)
    state = PrinterState()
    state.connected = True
    state.state = "IDLE"
    state.temperatures = {"nozzle": 25.0, "bed": 25.0, "chamber": 31.0, "chamber_target": 0.0}

    with patch("backend.app.api.routes.printers.printer_manager") as manager:
        manager.get_status = MagicMock(return_value=state)
        manager.is_awaiting_plate_clear = MagicMock(return_value=False)
        response = await async_client.get(f"/api/v1/printers/{printer.id}/status")

    assert response.status_code == 200
    body = response.json()
    assert body["temperatures"]["chamber"] == 31.0
    assert body["supports_chamber_heater"] is True
