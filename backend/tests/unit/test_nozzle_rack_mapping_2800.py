"""Nozzle-rack (H2C) dispatch mapping — #2800.

The H2C mounts one of six rack hotends on its right carriage. Dispatch has to
name the *physical* rack position, not the extruder index every other
dual-nozzle printer uses; get it wrong and the printer cleans and levels with
one nozzle, then prints with another several millimetres off the bed.

Nothing in the queue knew the rack position, so these jobs shipped with no
`nozzle_mapping` at all and the firmware picked for itself.
"""

import json
import zipfile

import pytest

from backend.app.services.bambu_mqtt import (
    _RACK_WIRE_SLOTS,
    BambuMQTTClient,
    resolve_rack_nozzle_mapping,
)
from backend.app.utils.printer_models import is_nozzle_rack_model
from backend.app.utils.threemf_tools import extract_slot_extruders_from_3mf


class TestIsNozzleRackModel:
    @pytest.mark.parametrize("model", ["H2C", "h2c", " H2C ", "O1C", "O1C2"])
    def test_h2c_spellings_and_codes(self, model):
        """The printer row may hold either the display name or the SSDP code."""
        assert is_nozzle_rack_model(model) is True

    @pytest.mark.parametrize("model", ["H2D", "H2D Pro", "H2S", "X2D", "P1S", "O1D", "N6", "", None])
    def test_everything_else_is_not_a_rack_model(self, model):
        """Other dual-nozzle printers must keep the plain extruder-index wire."""
        assert is_nozzle_rack_model(model) is False


class TestResolveRackNozzleMapping:
    def test_rack_slot_takes_the_live_rack_position(self):
        mapping = resolve_rack_nozzle_mapping([1], rack_nozzle_id=17)
        assert mapping is not None
        assert len(mapping) == _RACK_WIRE_SLOTS
        assert mapping[0] == 17
        assert set(mapping[1:]) == {-1}

    def test_the_fixed_hotend_takes_its_own_physical_id(self):
        """Both carriages are translated; neither extruder index reaches the wire.

        Sending the index for the fixed side (0) is what the printer rejected
        outright on hardware — it would not start the job at all.
        """
        mapping = resolve_rack_nozzle_mapping([0, 1], rack_nozzle_id=21)
        assert mapping[:2] == [1, 21]

    def test_unprinted_slots_stay_unset(self):
        mapping = resolve_rack_nozzle_mapping([1, -1, 1], rack_nozzle_id=16)
        assert mapping[:3] == [16, -1, 16]

    @pytest.mark.parametrize("rack_id", [None, 0, 1, 15, 22, 255])
    def test_no_usable_rack_position_omits_the_field(self, rack_id):
        """Mid-swap or stale state must fall back to the firmware's own pick.

        Guessing here is what prints in mid-air, so returning None (and
        omitting nozzle_mapping) is the intended failure mode.
        """
        assert resolve_rack_nozzle_mapping([1], rack_nozzle_id=rack_id) is None

    def test_job_that_never_uses_the_rack_is_left_alone(self):
        """BambuStudio omits nozzle_mapping for a fixed-hotend-only plate.

        Captured from the reporter's H2C: a plate sliced for the fixed side
        alone carries ams_mapping and no nozzle_mapping field at all, so
        naming a nozzle here would depart from what the printer expects.
        """
        assert resolve_rack_nozzle_mapping([0, 0], rack_nozzle_id=17) is None

    @pytest.mark.parametrize("unknown", [2, 3, 31])
    def test_a_carriage_the_h2c_does_not_have_omits_the_field(self, unknown):
        """A third index means the file was mapped for another machine.

        Forwarding it raw would name a physical nozzle by a number that does
        not identify one, which is the class of mistake #2800 was.
        """
        assert resolve_rack_nozzle_mapping([unknown, 1], rack_nozzle_id=17) is None

    @pytest.mark.parametrize(
        "bad_slots",
        [
            ["a", 1],  # non-numeric
            [{}, 1],  # nested object
            [[0], 1],  # nested list
            [0.5, 1],  # fractional
            [True, 1],  # bool would reach the wire as JSON `true`
            "1",  # not a list at all
        ],
    )
    def test_junk_input_returns_none_and_never_raises(self, bad_slots):
        """Nothing above this raises: `start_print` builds the MQTT command
        with no exception handler, and by then the queue item is already
        committed as `printing`. A bad value has to degrade to "firmware
        picks", not wedge the item in a state no print will leave."""
        assert resolve_rack_nozzle_mapping(bad_slots, rack_nozzle_id=17) is None

    @pytest.mark.parametrize("bad_rack", [[17], {"id": 17}, "17", 17.0, True])
    def test_junk_rack_position_returns_none_and_never_raises(self, bad_rack):
        assert resolve_rack_nozzle_mapping([1], rack_nozzle_id=bad_rack) is None

    def test_none_entries_read_as_unprinted(self):
        assert resolve_rack_nozzle_mapping([None, 1], rack_nozzle_id=17)[:2] == [-1, 17]

    def test_hardware_confirmed_mixed_nozzle_plate(self):
        """The exact job the reporter ran on an H2C, both ways round.

        Dispatched as [17, -1, -1, 1] the rack nozzle printed several
        millimetres above the bed; dispatched as [1, -1, -1, 17] the same
        sliced file printed correctly on both nozzles and completed. Native
        BambuStudio captures of mixed plates on the same machine carry
        [1, 17, ...] and [17, 1, ...] depending on filament slot order.
        """
        wire = resolve_rack_nozzle_mapping([0, -1, -1, 1], rack_nozzle_id=17)
        assert wire[:4] == [1, -1, -1, 17]
        assert set(wire[4:]) == {-1}

        swapped = resolve_rack_nozzle_mapping([1, 0], rack_nozzle_id=17)
        assert swapped[:2] == [17, 1]

    def test_more_slots_than_the_wire_carries(self):
        assert resolve_rack_nozzle_mapping([1] * (_RACK_WIRE_SLOTS + 1), rack_nozzle_id=17) is None

    def test_empty_mapping(self):
        assert resolve_rack_nozzle_mapping([], rack_nozzle_id=17) is None


class TestRackPositionFromMqtt:
    @pytest.fixture
    def client(self):
        return BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST-H2C",
            access_code="12345678",
            model="H2C",
        )

    def test_src_and_tar_are_captured(self, client):
        client._update_state({"device": {"nozzle": {"src_id": 16, "tar_id": 19}}})
        assert client.state.nozzle_rack_src_id == 16
        assert client.state.nozzle_rack_tar_id == 19

    def test_absent_key_does_not_clear_the_last_known_value(self, client):
        """The firmware only pushes these when they change."""
        client._update_state({"device": {"nozzle": {"src_id": 16, "tar_id": 19}}})
        client._update_state({"device": {"nozzle": {"info": []}}})
        assert client.state.nozzle_rack_tar_id == 19

    def test_unparseable_value_is_ignored(self, client):
        client._update_state({"device": {"nozzle": {"tar_id": 19}}})
        client._update_state({"device": {"nozzle": {"tar_id": "nonsense"}}})
        assert client.state.nozzle_rack_tar_id == 19

    def test_starts_unknown(self, client):
        assert client.state.nozzle_rack_src_id is None
        assert client.state.nozzle_rack_tar_id is None


class TestDispatch:
    """What actually reaches the wire."""

    def _client(self, model):
        from unittest.mock import MagicMock

        client = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="TEST-DISPATCH",
            access_code="12345678",
            model=model,
        )
        client._client = MagicMock()
        client.state.connected = True
        client._is_dual_nozzle = True
        return client

    def _print_cmd(self, client):
        return json.loads(client._client.publish.call_args[0][1])["print"]

    def test_rack_model_resolves_slot_extruders(self):
        client = self._client("H2C")
        client.state.nozzle_rack_tar_id = 18
        client.start_print("job.3mf", nozzle_slot_extruders=json.dumps([1, -1, 1]))
        cmd = self._print_cmd(client)
        assert cmd["nozzle_mapping"][:3] == [18, -1, 18]

    def test_src_id_used_when_tar_id_is_not_a_rack_position(self):
        """Between swaps the printer can report a settled src_id and nothing else."""
        client = self._client("H2C")
        client.state.nozzle_rack_src_id = 20
        client.state.nozzle_rack_tar_id = 0
        client.start_print("job.3mf", nozzle_slot_extruders=json.dumps([1]))
        assert self._print_cmd(client)["nozzle_mapping"][0] == 20

    def test_unknown_rack_position_omits_the_field(self):
        client = self._client("H2C")
        client.start_print("job.3mf", nozzle_slot_extruders=json.dumps([1]))
        assert "nozzle_mapping" not in self._print_cmd(client)

    def test_studio_capture_is_never_overridden(self):
        """A real capture is authoritative; the derived fallback must stand down."""
        client = self._client("H2C")
        client.state.nozzle_rack_tar_id = 18
        client.start_print(
            "job.3mf",
            nozzle_mapping=json.dumps([16, -1, -1, 1]),
            nozzle_slot_extruders=json.dumps([1, -1, 1]),
        )
        assert self._print_cmd(client)["nozzle_mapping"] == [16, -1, -1, 1]

    def test_other_dual_nozzle_models_are_untouched(self):
        """H2D has no rack: its extruder indices are already the wire values."""
        client = self._client("H2D")
        client.state.nozzle_rack_tar_id = 18
        client.start_print("job.3mf", nozzle_slot_extruders=json.dumps([0, 1]))
        assert "nozzle_mapping" not in self._print_cmd(client)

    def test_malformed_slot_extruders_is_logged_and_omitted(self, caplog):
        client = self._client("H2C")
        client.state.nozzle_rack_tar_id = 18
        with caplog.at_level("WARNING"):
            client.start_print("job.3mf", nozzle_slot_extruders="not json {")
        assert "nozzle_mapping" not in self._print_cmd(client)
        assert any("Invalid nozzle_slot_extruders" in rec.message for rec in caplog.records)

    def test_absent_slot_extruders_changes_nothing(self):
        client = self._client("H2C")
        client.state.nozzle_rack_tar_id = 18
        client.start_print("job.3mf")
        assert "nozzle_mapping" not in self._print_cmd(client)


def _write_dual_nozzle_3mf(path, group_by_slot):
    """Minimal 3MF carrying just what the nozzle extractor reads.

    physical_extruder_map is [1, 0] as Bambu ships it, so slicer group 0 comes
    out as MQTT extruder index 1 and group 1 as index 0. On the H2C index 1 is
    the rack carriage — confirmed on hardware in #2800, and the reason the
    rack-side fixture below slices its filaments into group 0.
    """
    filaments = "".join(f'<filament id="{slot}" group_id="{group}"/>' for slot, group in group_by_slot.items())
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "Metadata/project_settings.config",
            json.dumps(
                {
                    "physical_extruder_map": [1, 0],
                    "extruder_nozzle_stats": ["Standard#1", "Standard#1"],
                }
            ),
        )
        zf.writestr("Metadata/slice_info.config", f"<config><plate>{filaments}</plate></config>")
    return path


class TestSlotExtrudersFromFile:
    def test_derives_dense_per_slot_extruders(self, tmp_path):
        """Slots 1 and 3 print from the fixed hotend; slot 2 is unused."""
        source = _write_dual_nozzle_3mf(tmp_path / "job.3mf", {1: 1, 3: 1})
        assert extract_slot_extruders_from_3mf(source) == [0, -1, 0]

    def test_end_to_end_reaches_the_rack_position(self, tmp_path):
        """The reported failure: a two-slot job that must print from the rack."""
        source = _write_dual_nozzle_3mf(tmp_path / "job.3mf", {1: 0, 3: 0})
        assert extract_slot_extruders_from_3mf(source) == [1, -1, 1]
        wire = resolve_rack_nozzle_mapping(extract_slot_extruders_from_3mf(source), rack_nozzle_id=17)
        assert wire[:3] == [17, -1, 17]

    def test_both_extruders(self, tmp_path):
        """One slot per carriage — the mixed job that printed in mid-air."""
        source = _write_dual_nozzle_3mf(tmp_path / "job.3mf", {1: 0, 2: 1})
        assert extract_slot_extruders_from_3mf(source) == [1, 0]
        wire = resolve_rack_nozzle_mapping(extract_slot_extruders_from_3mf(source), rack_nozzle_id=17)
        assert wire[:2] == [17, 1]

    def test_single_nozzle_file_yields_nothing(self, tmp_path):
        path = tmp_path / "single.3mf"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("Metadata/project_settings.config", json.dumps({"physical_extruder_map": [0]}))
        assert extract_slot_extruders_from_3mf(path) is None

    def test_unreadable_file_is_not_fatal(self, tmp_path):
        path = tmp_path / "broken.3mf"
        path.write_bytes(b"not a zip")
        assert extract_slot_extruders_from_3mf(path) is None

    @pytest.mark.parametrize("slot_id", [50000000, 65, 0, -3])
    def test_out_of_range_slot_ids_are_rejected(self, tmp_path, slot_id):
        """Slot IDs are whatever the file claims, and this builds a dense list.

        Without a ceiling a corrupt or hostile 3MF declaring
        `filament id="50000000"` allocates a fifty-million-entry list on the
        dispatch path.
        """
        source = _write_dual_nozzle_3mf(tmp_path / f"s{abs(slot_id)}.3mf", {slot_id: 1})
        assert extract_slot_extruders_from_3mf(source) is None
