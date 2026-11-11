"""The H3C's nozzle temperature is the mounted hotend's (#3442).

The H3C swaps hotends on its one extruder. After a swap, ``nozzle_temper``
keeps reporting the hotend just put back in the rack, while the mounted one's
temperature is in ``device.extruder.info[0].temp`` (target << 16 | current),
which Bambu Studio reads over ``nozzle_temper`` (DevExtruderSystem ParseV2_0).
"""

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient
from backend.tests.unit.test_h3c_ams3h import _frame

pytestmark = pytest.mark.unit


def _encoded(target: int, current: int) -> int:
    return target * 65536 + current


def _h3c_frame(extruder_temp: int, nozzle_temper: float = 31.0, nozzle_target: float = 0.0) -> dict:
    frame = _frame()
    frame["device"]["extruder"]["info"][0]["temp"] = extruder_temp
    frame.update(nozzle_temper=nozzle_temper, nozzle_target_temper=nozzle_target)
    return frame


def _client(model: str) -> BambuMQTTClient:
    return BambuMQTTClient(ip_address="10.0.0.1", serial_number="SN", access_code="c", model=model)


class TestH3C:
    def test_the_mounted_hotend_is_shown_not_the_racked_one(self):
        client = _client("H3C")
        client._process_message({"print": _h3c_frame(_encoded(220, 215))})

        temps = client.state.temperatures
        assert (temps["nozzle"], temps["nozzle_target"], temps["nozzle_heating"]) == (215.0, 220.0, True)

    def test_a_delta_with_only_nozzle_temper_keeps_it(self):
        client = _client("H3C")
        client._process_message({"print": _h3c_frame(_encoded(220, 220))})
        client._process_message({"print": {"nozzle_temper": 40.0, "nozzle_target_temper": 0.0}})

        temps = client.state.temperatures
        assert (temps["nozzle"], temps["nozzle_target"]) == (220.0, 220.0)

    def test_a_cold_hotend_reads_direct(self):
        client = _client("H3C")
        client._process_message({"print": _h3c_frame(28)})

        temps = client.state.temperatures
        assert (temps["nozzle"], temps["nozzle_target"], temps["nozzle_heating"]) == (28.0, 0.0, False)

    def test_a_rack_printer_of_an_unknown_model_too(self):
        # Told apart by its AMS 3H and per-hotend inputs, like the tray ids.
        client = _client("")
        client._process_message({"print": _h3c_frame(_encoded(220, 215))})

        assert client.state.temperatures["nozzle"] == 215.0

    def test_an_h3c_without_ams_data_yet_ignores_nozzle_temper(self):
        client = _client("N10")
        client._process_message({"print": {"nozzle_temper": 40.0, "nozzle_target_temper": 0.0}})

        assert "nozzle" not in client.state.temperatures


class TestOtherPrinters:
    def test_single_nozzle_keeps_nozzle_temper(self):
        # A one-extruder report on a printer without a rack changes nothing.
        client = _client("P2S")
        client._process_message(
            {
                "print": {
                    "nozzle_temper": 200.0,
                    "nozzle_target_temper": 210.0,
                    "device": {"extruder": {"state": 1, "info": [{"id": 0, "temp": _encoded(250, 245)}]}},
                }
            }
        )

        temps = client.state.temperatures
        assert (temps["nozzle"], temps["nozzle_target"]) == (200.0, 210.0)

    def test_h2d_unchanged(self):
        client = _client("H2D")
        client._process_message(
            {
                "print": {
                    "nozzle_temper": 33.0,
                    "device": {
                        "extruder": {
                            "state": 2,
                            "info": [{"id": 0, "temp": _encoded(220, 210)}, {"id": 1, "temp": _encoded(250, 240)}],
                        }
                    },
                }
            }
        )

        temps = client.state.temperatures
        assert (temps["nozzle"], temps["nozzle_target"]) == (240.0, 250.0)
        assert (temps["nozzle_2"], temps["nozzle_2_target"]) == (210.0, 220.0)
