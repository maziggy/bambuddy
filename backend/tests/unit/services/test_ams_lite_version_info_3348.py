"""The AMS Lite gets its serial number and firmware from get_version (#3348).

Like every AMS, the AMS Lite reports both only in the printer's get_version
answer, as a module of its own: "ams_f1/<id>". That prefix was not recognised,
so the module was skipped and the unit showed neither. The module names and
fields below are from real A1 and A2L captures (ha-bambulab mock data). The
A2L reports its AMS Lite as unit 16, which is stored as 6.
"""

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient


@pytest.fixture
def client():
    return BambuMQTTClient(ip_address="192.168.1.100", serial_number="TESTAMSLITE", access_code="12345678")


def _version(name, sw_ver="00.00.07.94", sn="AMSLITE0001"):
    return {
        "command": "get_version",
        "module": [
            {"name": "ota", "sw_ver": "01.08.01.00"},
            {"name": name, "sw_ver": sw_ver, "hw_ver": "AMS_F102", "sn": sn},
        ],
    }


def _push(unit_id):
    return {"ams": [{"id": str(unit_id), "tray": [{"id": "0"}, {"id": "1"}, {"id": "2"}, {"id": "3"}]}]}


def _unit(client, unit_id):
    return next(u for u in client.state.raw_data["ams"] if int(u["id"]) == unit_id)


@pytest.mark.parametrize(("module", "pushed_id", "stored_id"), [("ams_f1/0", 0, 0), ("ams_f1/16", 16, 6)])
class TestAmsLite:
    def test_version_after_the_ams_push(self, client, module, pushed_id, stored_id):
        client._handle_ams_data(_push(pushed_id))

        client._handle_version_info(_version(module))

        unit = _unit(client, stored_id)
        assert (unit["sn"], unit["sw_ver"], unit["module_type"]) == ("AMSLITE0001", "00.00.07.94", "ams_f1")

    def test_version_before_the_first_ams_push(self, client, module, pushed_id, stored_id):
        client._handle_version_info(_version(module))

        client._handle_ams_data(_push(pushed_id))

        unit = _unit(client, stored_id)
        assert (unit["sn"], unit["sw_ver"]) == ("AMSLITE0001", "00.00.07.94")

    def test_no_missing_version_warning(self, client, module, pushed_id, stored_id, caplog):
        client._handle_ams_data(_push(pushed_id))

        client._handle_version_info(_version(module))

        assert "not available in version info" not in caplog.text


def test_other_ams_modules_are_unchanged(client):
    client._handle_ams_data(
        {"ams": [{"id": "0", "tray": []}, {"id": "128", "tray": []}]},
    )

    client._handle_version_info(
        {
            "module": [
                {"name": "n3f/0", "sw_ver": "03.00.21.29", "sn": "N3F0001"},
                {"name": "n3s/128", "sw_ver": "03.00.21.30", "sn": "N3S0001"},
            ]
        }
    )

    assert (_unit(client, 0)["sn"], _unit(client, 0)["module_type"]) == ("N3F0001", "n3f")
    assert (_unit(client, 128)["sn"], _unit(client, 128)["module_type"]) == ("N3S0001", "n3s")
