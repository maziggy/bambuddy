"""Which mapping entries a plate actually prints, and what the builder does
with the answer (#3087).

The dispatch-level behaviour is covered in
``backend/tests/integration/test_external_spool_use_ams_3087.py``. This is the
pure part: separating a padding ``-1`` from a slot that never resolved, which
is the distinction the MQTT command builder cannot make and the reason the
decision was put in the scheduler.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient
from backend.app.services.print_scheduler import (
    _consumed_mapping_entries,
    _is_external_tray,
    _might_be_dual_nozzle,
)


def _required(*slot_ids):
    """What `extract_filament_requirements` returns: one entry per filament the
    plate consumes, keyed by its project-wide slot_id. Anything with
    `used_g <= 0` is already dropped there, so everything here is printed."""
    return [{"slot_id": s, "type": "PLA", "used_grams": 10.0} for s in slot_ids]


class TestConsumedMappingEntries:
    def test_it_picks_out_the_slot_the_plate_prints(self):
        # The reporter's plate: seven project filaments, only #7 printed.
        assert _consumed_mapping_entries([-1, -1, -1, -1, -1, -1, 254], _required(7)) == [254]

    def test_padding_is_not_reported_as_unresolved(self):
        assert _consumed_mapping_entries([-1, 5, -1], _required(2)) == [5]

    def test_a_slot_the_plate_prints_reports_its_own_unresolved_entry(self):
        assert _consumed_mapping_entries([-1, -1, -1, -1, -1, -1, 254], _required(1, 7)) == [-1, 254]

    def test_several_printed_slots_come_back_in_slot_order(self):
        assert _consumed_mapping_entries([4, -1, 254], _required(1, 3)) == [4, 254]

    @pytest.mark.parametrize(
        "mapping,required",
        [
            (None, _required(1)),
            ([], _required(1)),
            ([254], None),
            ([254], []),
        ],
    )
    def test_it_declines_to_answer_without_both_halves(self, mapping, required):
        # No evidence, no decision — the caller then leaves use_ams alone.
        assert _consumed_mapping_entries(mapping, required) is None

    def test_a_requirement_the_mapping_is_too_short_for_declines(self):
        # The two were read at different times and disagree about the file.
        assert _consumed_mapping_entries([254], _required(7)) is None

    def test_a_junk_slot_id_declines(self):
        assert _consumed_mapping_entries([254], [{"slot_id": "7"}]) is None
        assert _consumed_mapping_entries([254], [{"slot_id": 0}]) is None
        assert _consumed_mapping_entries([254], [{}]) is None


class TestIsExternalTray:
    @pytest.mark.parametrize("tray_id", [254, 255, "254"])
    def test_the_spool_holder(self, tray_id):
        assert _is_external_tray(tray_id) is True

    @pytest.mark.parametrize("tray_id", [None, -1, 0, 5, 253, 128, "", "x", 1.5])
    def test_everything_else(self, tray_id):
        # 128-253 are AMS-HT units, -1/None unresolved, and junk is not a
        # licence to redirect a print to the spool holder.
        assert _is_external_tray(tray_id) is False


class TestMightBeDualNozzle:
    """Over-eager on purpose: a wrong yes only leaves a printer with the
    behaviour it already had, a wrong no rewrites which nozzle prints."""

    def _status(self, *, nozzles=(), **raw):
        return SimpleNamespace(nozzles=list(nozzles), raw_data=dict(raw))

    @pytest.mark.parametrize("model", ["H2D", "H2C", "X2D", "H2D Pro"])
    def test_the_model_name_is_enough(self, model):
        assert _might_be_dual_nozzle(model, self._status()) is True

    @pytest.mark.parametrize("model", ["P1S", "X1C", "A1", "P2S", "H2S", None, ""])
    def test_single_nozzle_models_pass(self, model):
        # H2S is the #1386 case: H2 family, one extruder.
        assert _might_be_dual_nozzle(model, self._status()) is False

    def test_two_external_feeds_give_it_away(self):
        # Only a two-extruder printer reports more than one vt_tray.
        assert _might_be_dual_nozzle("P1S", self._status(vt_tray=[{"id": "254"}, {"id": "255"}])) is True

    def test_one_external_feed_does_not(self):
        assert _might_be_dual_nozzle("P1S", self._status(vt_tray=[{"id": "254"}])) is False

    def test_a_second_nozzle_with_a_diameter_gives_it_away(self):
        nozzles = [SimpleNamespace(nozzle_diameter="0.4"), SimpleNamespace(nozzle_diameter="0.4")]
        assert _might_be_dual_nozzle("P1S", self._status(nozzles=nozzles)) is True

    def test_a_stub_second_nozzle_does_not(self):
        # The status model can carry placeholder NozzleInfo entries; only a
        # populated diameter means real hardware.
        nozzles = [SimpleNamespace(nozzle_diameter="0.4"), SimpleNamespace(nozzle_diameter="")]
        assert _might_be_dual_nozzle("P1S", self._status(nozzles=nozzles)) is False

    def test_an_extruder_map_gives_it_away(self):
        assert _might_be_dual_nozzle("P1S", self._status(ams_extruder_map={"0": 1})) is True

    def test_no_status_at_all_is_not_evidence_of_two(self):
        assert _might_be_dual_nozzle("P1S", None) is False

    def test_a_vt_tray_dict_is_not_counted_as_many_trays(self):
        # bambu_mqtt normalises vt_tray to a list, but a dict here would
        # otherwise count its keys and read as dual-nozzle.
        assert _might_be_dual_nozzle("P1S", self._status(vt_tray={"id": "254", "tray_type": "PLA"})) is False


class TestTheBuilderHonoursTheDecision:
    """The scheduler's answer has to survive the command builder, which has its
    own use_ams reconcile (#2589/#2595). It must not promote the flag back."""

    @pytest.fixture
    def mqtt_client(self):
        client = BambuMQTTClient(ip_address="192.168.1.100", serial_number="01P00A452600691", access_code="x")
        client.model = "P1S"
        client._client = MagicMock()
        client.state.connected = True
        return client

    def _sent(self, mqtt_client):
        return json.loads(mqtt_client._client.publish.call_args.args[1])["print"]

    def test_the_reporters_command_now_goes_out_printable(self, mqtt_client):
        mqtt_client.start_print("plate_4.3mf", ams_mapping=[-1] * 6 + [254], use_ams=False)
        cmd = self._sent(mqtt_client)

        assert cmd["use_ams"] is False
        # 254 is still never sent raw in the flat array — the firmware reads it
        # as AMS tray 0 — and ams_mapping2 still carries the spool holder.
        assert cmd["ams_mapping"] == [-1] * 7
        assert cmd["ams_mapping2"][6] == {"ams_id": 255, "slot_id": 0}
        assert cmd["ams_mapping2"][0] == {"ams_id": 255, "slot_id": 255}

    def test_the_builder_still_rejects_an_unresolved_mapping_as_external(self, mqtt_client):
        """The #2589 contract, unchanged: nothing here treats -1 as the spool."""
        mqtt_client.start_print("plate_4.3mf", ams_mapping=[-1] * 6 + [254], use_ams=True)

        assert self._sent(mqtt_client)["use_ams"] is True
