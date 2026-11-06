"""The REST status and the WebSocket push describe AMS units the same way.

The printer card is seeded from GET /printers/{id}/status and then
shallow-merged with every WebSocket push. The two serialisers were separate
copies, and each field one of them lacked showed up as a UI bug: ``exists``
(#2670), the stalled-countdown flag, and the drying status -- REST left out
``dry_status``, ``dry_sub_status`` and ``dry_sf_reason``, so a printer that
cannot dry showed an enabled Start button after every page load and poll.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.printer_manager import printer_state_to_dict

RAW = {
    "ams": [
        {
            "id": "0",
            "humidity": "4",
            "humidity_raw": "31",
            "temp": "24.5",
            "sn": "AMS0SN",
            "sw_ver": "00.00.06.40",
            "dry_time": 0,
            "dry_status": 0,
            "dry_sub_status": 0,
            "dry_sf_reason": [1, 8],
            "module_type": "ams",
            "tray": [
                {
                    "id": "0",
                    "tray_type": "PLA",
                    "tray_color": "FF0000FF",
                    "tray_sub_brands": "PLA Basic",
                    "tray_info_idx": "GFA00",
                    "remain": 80,
                    "k": 0.02,
                    "cali_idx": 3,
                    "tag_uid": "0000000000000000",
                    "state": 11,
                    "exists": True,
                },
                # The firmware's whole payload for a physically empty slot.
                {"id": "1"},
            ],
        },
        {
            "id": "128",
            "dry_time": 95,
            "dry_status": 2,
            "dry_sub_status": 1,
            "dry_countdown_stalled": True,
            "module_type": "n3s",
            "tray": [{"id": "0", "tray_type": "PETG", "tray_color": "00FF00FF", "remain": 50, "state": 11}],
        },
    ],
    "vt_tray": [
        {"id": "254", "tray_type": "PETG", "tray_color": "0000FFFF", "remain": 30, "k": 0.03, "cali_idx": 2},
        {"id": "255", "tray_type": "", "tag_uid": "0000000000000000", "state": 9},
    ],
}

DRYING_TARGETS = {128: {"filament": "PETG", "temp": 65}}


def _state() -> PrinterState:
    state = PrinterState()
    state.connected = True
    state.state = "IDLE"
    state.raw_data = json.loads(json.dumps(RAW))
    return state


def _without_nulls(value):
    """A null field and a missing one read the same on the card, which
    replaces these lists whole; REST fills its schema defaults, WS leaves them out."""
    if isinstance(value, dict):
        return {k: _without_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_without_nulls(v) for v in value]
    return value


async def _rest_status(async_client: AsyncClient, printer_factory) -> dict:
    printer = await printer_factory()
    with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
        mock_pm.get_status = MagicMock(return_value=_state())
        mock_pm.is_awaiting_plate_clear = MagicMock(return_value=False)
        mock_pm.get_drying_targets = MagicMock(return_value=DRYING_TARGETS)
        response = await async_client.get(f"/api/v1/printers/{printer.id}/status")
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rest_reports_the_drying_status(async_client: AsyncClient, printer_factory):
    ams = (await _rest_status(async_client, printer_factory))["ams"]

    assert ams[0]["dry_sf_reason"] == [1, 8]
    assert ams[1]["dry_status"] == 2
    assert ams[1]["dry_sub_status"] == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rest_and_websocket_ams_units_match(async_client: AsyncClient, printer_factory):
    rest = (await _rest_status(async_client, printer_factory))["ams"]
    ws = json.loads(json.dumps(printer_state_to_dict(_state(), drying_targets=DRYING_TARGETS)["ams"]))

    assert _without_nulls(rest) == _without_nulls(ws)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rest_and_websocket_external_spools_match(async_client: AsyncClient, printer_factory):
    rest = (await _rest_status(async_client, printer_factory))["vt_tray"]
    ws = json.loads(json.dumps(printer_state_to_dict(_state(), drying_targets=DRYING_TARGETS)["vt_tray"]))

    assert _without_nulls(rest) == _without_nulls(ws)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("24.5", 24.5), (31, 31.0), ("", None), ("--", None), (True, None), (None, None), ("nan", None), ("inf", None)],
)
def test_ams_temperature_is_a_finite_number_or_nothing(raw, expected):
    from backend.app.services.printer_manager import _ams_temp

    assert _ams_temp(raw) == expected


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rest_survives_the_shapes_websocket_already_tolerated(async_client: AsyncClient, printer_factory):
    """A non-dict AMS entry and a dict-shaped vt_tray used to 500 the REST route."""
    printer = await printer_factory()
    state = _state()
    state.raw_data = {
        "ams": ["garbage", {"id": "0", "temp": "--", "tray": []}],
        "vt_tray": {"id": "254", "tray_type": "PLA", "tray_color": "FFFFFFFF"},
    }
    with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
        mock_pm.get_status = MagicMock(return_value=state)
        mock_pm.is_awaiting_plate_clear = MagicMock(return_value=False)
        mock_pm.get_drying_targets = MagicMock(return_value={})
        response = await async_client.get(f"/api/v1/printers/{printer.id}/status")

    assert response.status_code == 200
    body = response.json()
    assert [unit["id"] for unit in body["ams"]] == [0]
    assert body["ams"][0]["temp"] is None
    assert [vt["id"] for vt in body["vt_tray"]] == [254]
