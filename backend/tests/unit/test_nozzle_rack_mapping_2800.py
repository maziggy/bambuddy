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
        # Extruder index 0 is the rack carriage: measured 2026-08-14 from
        # ams_extruder_map plus a BambuStudio dispatch that completed.
        mapping = resolve_rack_nozzle_mapping([0], rack_nozzle_id=17)
        assert mapping is not None
        assert len(mapping) == _RACK_WIRE_SLOTS
        assert mapping[0] == 17
        assert set(mapping[1:]) == {-1}

    def test_the_wire_is_padded_to_a_fixed_length(self):
        """Studio's own dispatch is 32 entries whatever the plate's slot count.

        This was briefly changed to the plate's slot count on the strength of
        one 3-entry capture, which turned out to be a calibration job; the real
        project print on the same machine dispatched 32 ([16, 1, 18, -1 x29])
        and completed. The length is Studio's business, not the file's.
        """
        for slots in ([0], [1, 0], [0, 1, 1, -1]):
            assert len(resolve_rack_nozzle_mapping(slots, rack_nozzle_id=16)) == _RACK_WIRE_SLOTS

    def test_the_fixed_hotend_takes_its_own_physical_id(self):
        """Both carriages are translated; neither extruder index reaches the wire.

        Sending an extruder index for the fixed side is what the printer
        rejected outright on hardware — it would not start the job at all.
        """
        mapping = resolve_rack_nozzle_mapping([1, 0], rack_nozzle_id=21)
        assert mapping[:2] == [1, 21]

    def test_unprinted_slots_stay_unset(self):
        mapping = resolve_rack_nozzle_mapping([0, -1, 0], rack_nozzle_id=16)
        assert mapping[:3] == [16, -1, 16]

    @pytest.mark.parametrize("rack_id", [None, 0, 1, 15, 22, 255])
    def test_no_usable_rack_position_omits_the_field(self, rack_id):
        """Mid-swap or stale state must fall back to the firmware's own pick.

        Guessing here is what prints in mid-air, so returning None (and
        omitting nozzle_mapping) is the intended failure mode.
        """
        assert resolve_rack_nozzle_mapping([0], rack_nozzle_id=rack_id) is None

    def test_job_that_never_uses_the_rack_is_left_alone(self):
        """BambuStudio omits nozzle_mapping for a fixed-hotend-only plate.

        Captured from the reporter's H2C: a plate sliced for the fixed side
        alone carries ams_mapping and no nozzle_mapping field at all, so
        naming a nozzle here would depart from what the printer expects.
        """
        assert resolve_rack_nozzle_mapping([1, 1], rack_nozzle_id=17) is None

    @pytest.mark.parametrize("unknown", [2, 3, 31])
    def test_a_carriage_the_h2c_does_not_have_omits_the_field(self, unknown):
        """A third index means the file was mapped for another machine.

        Forwarding it raw would name a physical nozzle by a number that does
        not identify one, which is the class of mistake #2800 was.
        """
        assert resolve_rack_nozzle_mapping([unknown, 0], rack_nozzle_id=17) is None

    @pytest.mark.parametrize(
        "bad_slots",
        [
            ["a", 0],  # non-numeric
            [{}, 0],  # nested object
            [[0], 0],  # nested list
            [0.5, 0],  # fractional
            [True, 0],  # bool would reach the wire as JSON `true`
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
        assert resolve_rack_nozzle_mapping([0], rack_nozzle_id=bad_rack) is None

    def test_none_entries_read_as_unprinted(self):
        assert resolve_rack_nozzle_mapping([None, 0], rack_nozzle_id=17)[:2] == [-1, 17]

    def test_hardware_confirmed_mixed_nozzle_plate(self):
        """The exact job the reporter ran on an H2C, both ways round.

        Dispatched as [17, -1, -1, 1] the rack nozzle printed several
        millimetres above the bed; dispatched as [1, -1, -1, 17] the same
        sliced file printed correctly on both nozzles and completed. Native
        BambuStudio captures of mixed plates on the same machine carry
        [1, 17, ...] and [17, 1, ...] depending on filament slot order.
        """
        wire = resolve_rack_nozzle_mapping([1, -1, -1, 0], rack_nozzle_id=17)
        assert wire[:4] == [1, -1, -1, 17]
        assert set(wire[4:]) == {-1}

        swapped = resolve_rack_nozzle_mapping([0, 1], rack_nozzle_id=17)
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
        client.start_print("job.3mf", nozzle_slot_extruders=json.dumps([0, -1, 0]))
        cmd = self._print_cmd(client)
        assert cmd["nozzle_mapping"][:3] == [18, -1, 18]

    def test_src_id_used_when_tar_id_is_not_a_rack_position(self):
        """Between swaps the printer can report a settled src_id and nothing else."""
        client = self._client("H2C")
        client.state.nozzle_rack_src_id = 20
        client.state.nozzle_rack_tar_id = 0
        client.start_print("job.3mf", nozzle_slot_extruders=json.dumps([0]))
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
    the fixed hotend and index 0 the rack carriage — measured 2026-08-14, which
    is why the rack-side fixtures below slice their filaments into group 1.
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
        """The reported failure: a two-slot job that must print from the rack.

        `physical_extruder_map` is [1, 0], so it is the file's group 1 that
        lands on extruder 0 -- the rack carriage.
        """
        source = _write_dual_nozzle_3mf(tmp_path / "job.3mf", {1: 1, 3: 1})
        assert extract_slot_extruders_from_3mf(source) == [0, -1, 0]
        wire = resolve_rack_nozzle_mapping(extract_slot_extruders_from_3mf(source), rack_nozzle_id=17)
        assert wire[:3] == [17, -1, 17]

    def test_both_extruders(self, tmp_path):
        """One slot per carriage — the mixed job that printed in mid-air."""
        source = _write_dual_nozzle_3mf(tmp_path / "job.3mf", {1: 0, 2: 1})
        assert extract_slot_extruders_from_3mf(source) == [1, 0]
        wire = resolve_rack_nozzle_mapping(extract_slot_extruders_from_3mf(source), rack_nozzle_id=17)
        assert wire[:2] == [1, 17]

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


def _write_h2c_3mf(path, plates):
    """3MF in the shape BambuStudio writes for a nozzle-rack machine.

    ``plates`` is a list of ``(index, {slot: group}, {group: 1-based extruder})``.
    The per-plate ``<nozzle>`` elements are the group-to-extruder table; the
    fixture above deliberately omits them, because H2D files carry group ids
    that already are extruder indices and both shapes have to keep working.
    """
    body = ""
    for index, filaments, nozzles in plates:
        elems = "".join(f'<filament id="{slot}" group_id="{group}"/>' for slot, group in filaments.items())
        elems += "".join(f'<nozzle id="{group}" extruder_id="{ext}"/>' for group, ext in nozzles.items())
        body += f'<plate><metadata key="index" value="{index}"/>{elems}</plate>'
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "Metadata/project_settings.config",
            json.dumps(
                {
                    "physical_extruder_map": [1, 0],
                    # One nozzle on the fixed carriage, four racked, as the
                    # reporter's H2C reports itself.
                    "extruder_nozzle_stats": ["High Flow#1", "High Flow#4"],
                }
            ),
        )
        zf.writestr("Metadata/slice_info.config", f"<config>{body}</config>")
    return path


class TestGroupsBeyondTheExtruderCount:
    """A rack carriage hosts six hotends, so groups outnumber extruders.

    The H2C's `extruder_max_nozzle_count` is ['1', '6'], and the slicer emits
    one filament group per nozzle it wants rather than one per carriage. Group
    ids therefore run past the end of `physical_extruder_map`, which has an
    entry per *extruder*. Treating the group id as an index into it silently
    dropped those filaments, and a dropped filament densifies to -1 -- the same
    value that means "this plate does not print the slot".
    """

    def test_a_plate_wanting_two_rack_nozzles_is_left_to_the_firmware(self, tmp_path):
        """The maintainer's own plate, first prints on a new H2C.

        Three filaments in groups 2, 0 and 1, where the file's nozzle table
        puts groups 1 AND 2 on extruder 2. Two groups on one extruder is a
        rack: the plate wants a different hotend from it per group, which is
        the whole point of the six-nozzle carriage. Which physical slot each
        group takes is the slicer's choice against the live rack -- both rack
        groups here carry identical nozzle_diameter and volume_type, and
        BambuStudio still dispatched them to 16 and 18 (captured 17:20 on
        2026-08-13; that print completed).

        Nothing derivable from the file reproduces that, and the two attempts
        that answered anyway both failed on hardware: dropping the unplaceable
        filament dispatched [-1, 16, 1, ...] and the printer refused to start
        (HMS 0500-4047), and placing it dispatched [1, 16, 1] which printed in
        mid-air. So the mapping is withheld entirely.
        """
        source = _write_h2c_3mf(
            tmp_path / "benchy.3mf",
            [(1, {1: 2, 2: 0, 3: 1}, {0: 1, 1: 2, 2: 2})],
        )
        assert extract_slot_extruders_from_3mf(source, plate_id=1) is None

    def test_one_rack_group_is_still_answered(self, tmp_path):
        """The refusal is about naming *several* rack positions, not the rack.

        A plate with one group per carriage needs only the position the printer
        reports as live, which is knowable -- that is the #2800 case and it
        must keep working.
        """
        source = _write_h2c_3mf(
            tmp_path / "one-each.3mf",
            [(1, {1: 0, 2: 1}, {0: 1, 1: 2})],
        )
        assert extract_slot_extruders_from_3mf(source, plate_id=1) == [1, 0]

    def test_a_group_the_file_never_places_omits_the_whole_mapping(self, tmp_path):
        """Refusing beats answering for the slots that did resolve.

        A partial mapping is not a smaller answer, it is a wrong one: the gap
        reaches the printer as -1, contradicting the ams_mapping entry for the
        same slot. Omitting costs only the firmware's own nozzle pick.
        """
        source = _write_h2c_3mf(
            tmp_path / "orphan.3mf",
            [(1, {1: 5, 2: 0}, {0: 1, 1: 2})],
        )
        assert extract_slot_extruders_from_3mf(source, plate_id=1) is None

    def test_a_slot_the_plate_genuinely_skips_still_reads_minus_one(self, tmp_path):
        """-1 keeps its meaning where it is earned rather than inferred."""
        source = _write_h2c_3mf(
            tmp_path / "gap.3mf",
            [(1, {1: 0, 3: 1}, {0: 1, 1: 2})],
        )
        assert extract_slot_extruders_from_3mf(source, plate_id=1) == [1, -1, 0]

    def test_filaments_grouped_and_ungrouped_in_one_plate_omit_the_mapping(self, tmp_path):
        """Half an answer has the same failure mode as a dropped group."""
        path = tmp_path / "mixed.3mf"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(
                "Metadata/project_settings.config",
                json.dumps(
                    {
                        "physical_extruder_map": [1, 0],
                        "extruder_nozzle_stats": ["High Flow#1", "High Flow#4"],
                    }
                ),
            )
            zf.writestr(
                "Metadata/slice_info.config",
                '<config><plate><metadata key="index" value="1"/>'
                '<filament id="1" group_id="0"/><filament id="2"/>'
                '<nozzle id="0" extruder_id="1"/></plate></config>',
            )
        assert extract_slot_extruders_from_3mf(path, plate_id=1) is None

    def test_files_without_a_nozzle_table_keep_the_group_as_the_index(self, tmp_path):
        """Every H2D slice in the wild carries groups 0 and 1 and no table."""
        source = _write_dual_nozzle_3mf(tmp_path / "h2d.3mf", {1: 0, 2: 1})
        assert extract_slot_extruders_from_3mf(source, plate_id=1) == [1, 0]


class TestPlateScoping:
    """A 3MF holds every plate in the project, not just the one being printed."""

    def test_each_plate_answers_for_itself(self, tmp_path):
        source = _write_h2c_3mf(
            tmp_path / "two.3mf",
            [
                (1, {1: 0, 2: 1}, {0: 1, 1: 2}),
                (2, {1: 1, 2: 0}, {0: 1, 1: 2}),
            ],
        )
        assert extract_slot_extruders_from_3mf(source, plate_id=1) == [1, 0]
        assert extract_slot_extruders_from_3mf(source, plate_id=2) == [0, 1]

    def test_an_unknown_plate_falls_back_to_the_whole_file(self, tmp_path):
        """Not every file indexes its plates; this must not become a hard stop."""
        source = _write_h2c_3mf(tmp_path / "one.3mf", [(1, {1: 0, 2: 1}, {0: 1, 1: 2})])
        assert extract_slot_extruders_from_3mf(source, plate_id=7) == [1, 0]

    def test_plates_that_disagree_about_a_group_fall_back_to_the_index(self, tmp_path):
        """Reading every plate at once can only be done on the old terms.

        With no plate asked for, two plates naming different extruders for one
        group leave no table worth trusting, so the group id is read as the
        extruder index exactly as it was before the table existed.
        """
        source = _write_h2c_3mf(
            tmp_path / "conflict.3mf",
            [
                (1, {1: 0}, {0: 1}),
                (2, {2: 0}, {0: 2}),
            ],
        )
        assert extract_slot_extruders_from_3mf(source, plate_id=None) == [1, 1]
