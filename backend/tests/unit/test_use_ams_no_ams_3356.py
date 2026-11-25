"""A printer with no AMS gets use_ams=False (#3356).

A P1S with no AMS and nothing set on its spool holder: the matcher had nothing
to match, so the print went out with no mapping and use_ams=True, which the
firmware refuses with 07FF_8012 "Failed to get AMS mapping table". The
mapping-based reconcile (#2595) never ran without a mapping. The printer's own
push says it has no AMS (``"ams": []``, from the reporter's support package),
so the command builder sends use_ams=False, as the slicer does.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient


@pytest.fixture
def client():
    client = BambuMQTTClient(ip_address="192.168.1.100", serial_number="01P00A000000001", access_code="12345678")
    client.model = "P1S"
    client._client = MagicMock()
    client.state.connected = True
    return client


def _use_ams(client) -> bool:
    assert client._client.publish.called, "start_print did not publish"
    return json.loads(client._client.publish.call_args.args[1])["print"]["use_ams"]


def _no_ams(client):
    client._handle_ams_data({"ams": []})


def test_no_ams_and_no_mapping(client):
    _no_ams(client)

    assert client.start_print("case_body.3mf", ams_mapping=None, use_ams=True)

    assert _use_ams(client) is False


@pytest.mark.parametrize("mapping", [[254], [-1], [-1, 254]])
def test_no_ams_with_a_mapping(client, mapping):
    _no_ams(client)

    assert client.start_print("case_body.3mf", ams_mapping=mapping, use_ams=True)

    assert _use_ams(client) is False


def test_not_heard_from_the_ams_yet(client):
    """No AMS push handled yet (just reconnected): the AMS may be there."""
    assert "ams" not in client.state.raw_data

    assert client.start_print("case_body.3mf", ams_mapping=None, use_ams=True)

    assert _use_ams(client) is True


def test_with_an_ams(client):
    client._handle_ams_data({"ams": [{"id": "0", "tray": [{"id": "0"}, {"id": "1"}, {"id": "2"}, {"id": "3"}]}]})

    assert client.start_print("case_body.3mf", ams_mapping=None, use_ams=True)

    assert _use_ams(client) is True


def test_dual_nozzle_is_left_alone(client):
    """On an H2D use_ams is nozzle routing, not an AMS on/off flag."""
    client.model = "H2D"
    _no_ams(client)

    assert client.start_print("case_body.3mf", ams_mapping=None, use_ams=True)

    assert _use_ams(client) is True


# The AMS blocks below are shaped like Martin's X1C and H2C captures: the unit
# list and ams_exist_bits arrive together in every push.
def _unit(unit_id, trays=4):
    return {"id": str(unit_id), "tray": [{"id": str(t), "tray_type": "PLA"} for t in range(trays)]}


def _push(units, bits):
    return {"ams": units, "ams_exist_bits": bits, "tray_now": "255", "power_on_flag": False}


def _unit_ids(client):
    return sorted((u["id"] for u in client.state.raw_data["ams"]), key=int)


class TestAnUnpluggedAms:
    def test_the_x1c_unplug_and_replug(self, client):
        client.model = "X1C"
        client._handle_ams_data(_push([_unit(1)], "2"))

        client._handle_ams_data(_push([], "0"))
        assert _unit_ids(client) == []

        client._handle_ams_data(_push([_unit(1)], "2"))
        assert _unit_ids(client) == ["1"]

    def test_printing_from_the_spool_holder_after_the_unplug(self, client):
        """No restart needed: the no-AMS rule sees the unplug."""
        client._handle_ams_data(_push([_unit(0)], "1"))
        client._handle_ams_data(_push([], "0"))

        assert client.start_print("case_body.3mf", ams_mapping=None, use_ams=True)

        assert _use_ams(client) is False

    def test_a_unit_whose_bit_is_cleared_goes(self, client):
        client.model = "H2C"
        client._handle_ams_data(_push([_unit(0), _unit(1), _unit(2), _unit(128, 1)], "17"))

        client._handle_ams_data(_push([_unit(0), _unit(2), _unit(128, 1)], "15"))

        assert _unit_ids(client) == ["0", "2", "128"]

    def test_a_partial_list_keeps_the_units_it_leaves_out(self, client):
        client.model = "H2C"
        client._handle_ams_data(_push([_unit(0), _unit(1), _unit(2), _unit(128, 1)], "17"))

        client._handle_ams_data(_push([_unit(0)], "17"))

        assert _unit_ids(client) == ["0", "1", "2", "128"]

    def test_an_ams_ht_next_to_other_units_is_kept(self, client):
        """Its bit is not read: measured once, on one printer."""
        client.model = "H2C"
        client._handle_ams_data(_push([_unit(0), _unit(128, 1)], "11"))

        client._handle_ams_data(_push([_unit(0)], "1"))

        assert _unit_ids(client) == ["0", "128"]

    def test_the_last_ams_ht_goes_with_everything_else(self, client):
        client._handle_ams_data(_push([_unit(128, 1)], "10"))

        client._handle_ams_data(_push([], "0"))

        assert _unit_ids(client) == []

    def test_a_push_without_bits_changes_nothing(self, client):
        client._handle_ams_data(_push([_unit(0)], "1"))

        client._handle_ams_data({"ams": []})

        assert _unit_ids(client) == ["0"]

    def test_no_bits_next_to_a_listed_unit_keeps_an_ams_ht(self, client):
        """All-zero bits while units are still listed is the shutdown pattern
        (#765), not a report that the AMS HT is gone."""
        client._handle_ams_data(_push([_unit(0), _unit(128, 1)], "11"))

        client._handle_ams_data(_push([_unit(0)], "0"))

        assert _unit_ids(client) == ["0", "128"]
