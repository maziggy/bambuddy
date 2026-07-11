"""P1-series AMS drying is screen-only — the API must refuse it (#2533).

Bambu's P1 manual states that "P1S connected AMS drying functions may only be
controlled from the P1S screen". The firmware still answers
``ams_filament_drying`` with ``result: success`` and then ignores it, which is
exactly what the reporter saw: three commands accepted on an idle P1S with an
AMS 2 Pro, and the unit never left ``dry_status: 0``.

So a command we can't fulfil must be refused rather than acked, and that has to
hold for stop as well as start — a cycle a P1S user started at the printer can
only be ended there.
"""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient


@pytest.fixture
def mqtt_send():
    """Watch the MQTT command so we can assert nothing was published."""
    with patch(
        "backend.app.services.printer_manager.printer_manager.send_drying_command",
        new=MagicMock(return_value=True),
    ) as m:
        yield m


@pytest.fixture
def live_state():
    """A connected printer on firmware new enough that only the model gates drying."""
    state = MagicMock()
    state.firmware_version = "01.10.00.00"
    state.raw_data = {"ams": [{"id": 0, "module_type": "n3f", "tray": []}]}
    with patch(
        "backend.app.services.printer_manager.printer_manager.get_status",
        new=MagicMock(return_value=state),
    ) as m:
        yield m


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("model", ["P1S", "P1P"])
@pytest.mark.parametrize("action", ["start", "stop"])
async def test_screen_only_model_refuses_drying(
    async_client: AsyncClient, printer_factory, mqtt_send, live_state, model, action
):
    printer = await printer_factory(model=model)

    response = await async_client.post(f"/api/v1/printers/{printer.id}/drying/{action}?ams_id=0")

    assert response.status_code == 400
    assert "screen" in response.json()["detail"].lower()
    # And nothing went out on the wire — an ack the printer would drop is worse
    # than a refusal, because it leaves the user believing drying is running.
    mqtt_send.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("action", ["start", "stop"])
async def test_commandable_model_still_dries(async_client: AsyncClient, printer_factory, mqtt_send, live_state, action):
    printer = await printer_factory(model="X1C")

    response = await async_client.post(f"/api/v1/printers/{printer.id}/drying/{action}?ams_id=0")

    assert response.status_code == 200
    mqtt_send.assert_called_once()
