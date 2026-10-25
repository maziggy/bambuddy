"""Load and Unload work on an AMS-HT slot.

An AMS-HT tray is numbered by its unit id (128-135), as the printer reports it
in tray_now. The printer card sent unit*4+slot (512 for the first AMS-HT),
which the load route refused, and the commands turned 128 into ams_id 32. They
now send what BambuStudio sends: ams_id = the unit id, slot_id 0, target = the
unit id.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient


def _client(model: str = "O1C2") -> BambuMQTTClient:
    client = BambuMQTTClient(ip_address="10.0.0.1", serial_number="H2C", access_code="c", model=model)
    client._client = MagicMock()
    client.state.connected = True
    return client


def _sent(client) -> dict:
    return json.loads(client._client.publish.call_args[0][1])["print"]


@pytest.mark.parametrize("unit", [128, 131, 135])
def test_load_addresses_the_unit(unit):
    client = _client()

    assert client.ams_load_filament(unit) is True

    sent = _sent(client)
    assert (sent["ams_id"], sent["slot_id"], sent["target"]) == (unit, 0, unit)


def test_a_regular_slot_is_unchanged():
    client = _client()

    assert client.ams_load_filament(6) is True

    sent = _sent(client)
    assert (sent["ams_id"], sent["slot_id"], sent["target"]) == (1, 2, 6)


def test_unload_of_a_loaded_ams_ht_addresses_its_unit():
    """tray_now 128 named ams_id 32, a unit the printer does not have."""
    client = _client(model="X1C")
    client.state.tray_now = 128

    assert client.ams_unload_filament() is True

    assert _sent(client)["ams_id"] == 128
