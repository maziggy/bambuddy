"""H2D active tray: the extruder block of a frame is read before its AMS block.

An H2D reports tray_now as a bare slot (0-3) and names the AMS in each
extruder's ``snow``. Both often arrive in one frame, but the AMS block was
handled first, so tray_now was resolved against the previous frame's snow --
and on a disagreement that stale snow won. Now a frame's snow is read first,
and a snow from an earlier frame no longer overrides the slot the printer just
reported: the AMS-to-nozzle map places it instead.
"""

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient


def _frame(*, tray_now=None, snow_right=None, state=0x0000):
    print_data: dict = {}
    if snow_right is not None:
        print_data["device"] = {
            "extruder": {
                "info": [{"id": 0, "snow": snow_right}, {"id": 1, "snow": 0xFFFF}],
                "state": state,
            }
        }
    if tray_now is not None:
        print_data["ams"] = {"tray_now": str(tray_now)}
    return {"print": print_data}


@pytest.fixture
def h2d():
    """H2D, right nozzle active, AMS 0 on the right nozzle, AMS-HT 128 on the left."""
    client = BambuMQTTClient(ip_address="10.0.0.2", serial_number="TEST_H2D_G11", access_code="x")
    client._process_message(
        {
            "print": {
                "device": {"extruder": {"info": [{"id": 0, "snow": 0xFFFF}, {"id": 1, "snow": 0xFFFF}], "state": 0}},
                "ams": {
                    "ams": [
                        {"id": 0, "info": "2003", "tray": [{"id": i} for i in range(4)]},
                        {"id": 128, "info": "2104", "tray": [{"id": 0}]},
                    ],
                    "tray_now": "255",
                },
            }
        }
    )
    assert client._is_dual_nozzle
    return client


def test_a_frame_carrying_both_uses_its_own_snow(h2d):
    h2d._process_message(_frame(tray_now=0, snow_right=0x0000))  # AMS 0 slot 0
    assert h2d.state.tray_now == 0

    h2d._process_message(_frame(tray_now=2, snow_right=0x0002))  # swap to AMS 0 slot 2

    assert h2d.state.tray_now == 2


def test_a_stale_snow_does_not_override_the_reported_slot(h2d):
    h2d._process_message(_frame(tray_now=0, snow_right=0x0000))

    h2d._process_message(_frame(tray_now=2))  # no extruder block in this frame

    # One AMS on the active nozzle: the map places slot 2 there.
    assert h2d.state.tray_now == 2


def test_a_fresh_snow_still_wins_a_disagreement(h2d):
    """The extruder's own report of its slot is the newer, complete answer."""
    h2d._process_message(_frame(tray_now=0, snow_right=0x0003))

    assert h2d.state.tray_now == 3


def test_extruder_info_without_snow_is_not_a_fresh_snow(h2d):
    """A temperature-only update of the extruder block names no tray."""
    h2d._process_message(_frame(tray_now=0, snow_right=0x0000))

    h2d._process_message(
        {
            "print": {
                "device": {"extruder": {"info": [{"id": 0, "temp": 200}, {"id": 1, "temp": 30}], "state": 0}},
                "ams": {"tray_now": "2"},
            }
        }
    )

    assert h2d.state.tray_now == 2


def test_a_nozzle_switch_in_the_same_frame_uses_the_new_nozzle(h2d):
    """Left nozzle (bit 8) feeds from the AMS-HT 128, whose one tray is slot 0."""
    h2d._process_message(
        {
            "print": {
                "device": {
                    "extruder": {"info": [{"id": 0, "snow": 0x0002}, {"id": 1, "snow": 128 << 8}], "state": 0x100}
                },
                "ams": {"tray_now": "0"},
            }
        }
    )

    assert h2d.state.active_extruder == 1
    assert h2d.state.tray_now == 128


@pytest.mark.parametrize(
    "info", [[{"id": 0, "snow": "x2"}, {"id": 1, "snow": 65535}], ["junk", {"id": "x", "snow": 1}]]
)
def test_malformed_extruder_data_does_not_lose_the_frame(h2d, info):
    h2d._process_message(
        {
            "print": {
                "device": {"extruder": {"info": info, "state": 0}},
                "ams": {"tray_now": "1"},
                "gcode_state": "RUNNING",
            }
        }
    )

    assert h2d.state.state == "RUNNING"
    assert h2d.state.tray_now == 1
