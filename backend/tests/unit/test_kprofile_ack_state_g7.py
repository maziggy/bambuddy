"""A K-profile command's answer carries no printer status.

The printer echoes extrusion_cali_set / _del / _sel with the nozzle diameter
the command named. Only the extrusion_cali_get answer was kept out of the
status update (#2663), so the others could overwrite the installed nozzle's
diameter with whatever size the profile was written for.
"""

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient


@pytest.mark.parametrize("command", ["extrusion_cali_set", "extrusion_cali_del", "extrusion_cali_sel"])
def test_the_answer_leaves_the_nozzle_alone(command):
    client = BambuMQTTClient(ip_address="10.0.0.9", serial_number="SN-G7", access_code="x", model="X1C")
    client._process_message({"print": {"command": "push_status", "nozzle_diameter": "0.4"}})
    assert client.state.nozzles[0].nozzle_diameter == "0.4"

    client._process_message(
        {"print": {"command": command, "sequence_id": "9", "result": "success", "nozzle_diameter": "0.8"}}
    )

    assert client.state.nozzles[0].nozzle_diameter == "0.4"
