"""H2C per-nozzle print time (#1908).

Recent H2C firmware reports how long each nozzle has printed as ``p_t`` (seconds)
on its ``device.nozzle.info`` entry. It is carried as ``print_time_s`` through
the MQTT state, the WebSocket status push and the REST status, and stays None
where the firmware does not report it.
"""

import pytest

from backend.app.schemas.printer import NozzleRackSlot
from backend.app.services.bambu_mqtt import BambuMQTTClient, PrinterState, nozzle_print_time_s
from backend.app.services.printer_manager import printer_state_to_dict


def _nozzle(nozzle_id: int, **extra) -> dict:
    return {"id": nozzle_id, "type": "HS01", "diameter": 0.4, "tm": 350, "wear": 0, "stat": 184, **extra}


def _ingested(*nozzles: dict) -> BambuMQTTClient:
    client = BambuMQTTClient(ip_address="10.0.0.1", serial_number="H2C", access_code="c", model="H2C")
    client._process_message({"print": {"device": {"nozzle": {"info": list(nozzles)}}}})
    return client


class TestParse:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (0, 0),
            (7265, 7265),
            (7265.9, 7265),
            (None, None),
            (-1, None),
            ("7265", None),
            (True, None),
        ],
    )
    def test_only_a_non_negative_number_is_a_print_time(self, raw, expected):
        assert nozzle_print_time_s(raw) == expected

    def test_each_nozzle_keeps_its_own_print_time(self):
        rack = _ingested(_nozzle(0, p_t=3600), _nozzle(16, p_t=90061), _nozzle(17)).state.nozzle_rack
        assert {n["id"]: n["print_time_s"] for n in rack} == {0: 3600, 16: 90061, 17: None}

    def test_firmware_without_p_t_leaves_it_unset(self):
        (nozzle,) = _ingested(_nozzle(0)).state.nozzle_rack
        assert nozzle["print_time_s"] is None


class TestSerialise:
    def test_the_websocket_push_carries_it(self):
        state = PrinterState(connected=True, state="IDLE")
        state.nozzle_rack = _ingested(_nozzle(16, p_t=7200)).state.nozzle_rack
        (slot,) = printer_state_to_dict(state)["nozzle_rack"]
        assert slot["print_time_s"] == 7200

    def test_the_push_and_the_rest_schema_have_the_same_fields(self):
        # The frontend shallow-merges pushes over the REST status, so a field
        # the push leaves out goes stale on the first push.
        state = PrinterState(connected=True, state="IDLE")
        state.nozzle_rack = _ingested(_nozzle(16, p_t=7200)).state.nozzle_rack
        (slot,) = printer_state_to_dict(state)["nozzle_rack"]
        assert set(slot) == set(NozzleRackSlot.model_fields)
        assert NozzleRackSlot(**slot).print_time_s == 7200


class TestRestStatus:
    @pytest.mark.asyncio
    async def test_the_status_route_carries_it(self, async_client, printer_factory):
        # What the page has before the first push arrives.
        from unittest.mock import MagicMock, patch

        printer = await printer_factory()
        state = _ingested(_nozzle(0, p_t=3600), _nozzle(16)).state
        state.connected = True
        state.state = "IDLE"

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_status = MagicMock(return_value=state)
            mock_pm.is_awaiting_plate_clear = MagicMock(return_value=False)
            response = await async_client.get(f"/api/v1/printers/{printer.id}/status")

        assert response.status_code == 200
        assert {n["id"]: n["print_time_s"] for n in response.json()["nozzle_rack"]} == {0: 3600, 16: None}
