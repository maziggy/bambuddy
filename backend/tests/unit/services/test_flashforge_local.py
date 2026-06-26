import json
from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.flashforge_local import (
    FlashForgeLocalClient,
    is_flashforge_model,
    probe_flashforge_connection,
)


def _detail_payload() -> dict:
    return {
        "code": 0,
        "detail": {
            "model": "Creator 5 Pro",
            "name": "Creator 5 Pro",
            "status": "printing",
            "printFileName": "colored_cow.gcode.3mf",
            "printProgress": 0.25,
            "estimatedTime": 1234,
            "printDuration": 456,
            "printLayer": 12,
            "targetPrintLayer": 100,
            "firmwareVersion": "1.9.3",
            "camera": 1,
            "cameraStreamUrl": "http://192.168.0.211:8080/?action=stream",
            "nozzleTemps": [120, 121, 180, 209],
            "nozzleTargetTemps": [120, 120, 130, 210],
            "platTemp": 59,
            "platTargetTemp": 60,
            "chamberTemp": 29,
            "coolingFanSpeed": 70,
            "chamberFanSpeed": 0,
            "lightStatus": "open",
            "doorStatus": "close",
            "matlStationInfo": {
                "slotCnt": 4,
                "slotInfos": [
                    {"slotId": 1, "hasFilament": True, "materialName": "PLA", "materialColor": "#FCEBD7"},
                    {"slotId": 2, "hasFilament": True, "materialName": "PLA", "materialColor": "#FFFFFF"},
                ],
            },
        },
    }


def test_is_flashforge_model():
    assert is_flashforge_model("Creator 5 Pro")
    assert is_flashforge_model("FlashForge Creator 5 Pro")
    assert not is_flashforge_model("Bambu Lab P1S")
    assert not is_flashforge_model(None)


def test_apply_detail_maps_creator_5_pro_status():
    client = FlashForgeLocalClient("192.168.0.211", "SN123", "code", model="Creator 5 Pro")

    client._apply_detail(_detail_payload()["detail"])

    assert client.state.connected is True
    assert client.state.state == "RUNNING"
    assert client.state.current_print == "colored_cow.gcode.3mf"
    assert client.state.gcode_file == "colored_cow.gcode.3mf"
    assert client.state.progress == 25
    assert client.state.remaining_time == 1234
    assert client.state.layer_num == 12
    assert client.state.total_layers == 100
    assert client.state.firmware_version == "1.9.3"
    assert client.state.ipcam is True
    assert client.state.temperatures == {
        "nozzle": 209,
        "nozzle_target": 210,
        "bed": 59,
        "bed_target": 60,
        "chamber": 29,
    }
    assert client.state.cooling_fan_speed == 70
    assert client.state.chamber_light is True
    assert client.state.door_open is False
    assert client.state.raw_data["vendor"] == "flashforge"
    assert client.state.raw_data["ams"][0]["module_type"] == "flashforge_ifs"
    assert client.state.raw_data["ams"][0]["tray"][0]["tray_type"] == "PLA"
    assert client.state.raw_data["ams"][0]["tray"][0]["tray_color"] == "FCEBD7FF"


@pytest.mark.asyncio
async def test_flashforge_connection_probe_uses_detail_endpoint():
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps(_detail_payload()).encode()

    with patch("backend.app.services.flashforge_local.urlopen", return_value=response) as urlopen_mock:
        result = await probe_flashforge_connection("192.168.0.211", "SN123", "code")

    assert result == {"success": True, "state": "RUNNING", "model": "Creator 5 Pro"}
    request = urlopen_mock.call_args.args[0]
    assert request.full_url == "http://192.168.0.211:8898/detail"
    assert json.loads(request.data.decode()) == {"serialNumber": "SN123", "checkCode": "code"}
