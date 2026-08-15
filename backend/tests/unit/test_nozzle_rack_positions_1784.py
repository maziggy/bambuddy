"""Choosing which rack position each filament group prints from (#1784).

An H2C's rack carriage hosts six hotends. Which one a filament group takes is
the operator's choice and is stated nowhere in the 3MF -- established by
dispatching one plate twice from BambuStudio with different picks and diffing
the two files: `group_id` values, the toolchange stream, the `NOZZLE_CHANGE`
markers and `project_settings.config` were all identical, and only extrusion
floats differed in the last digit. The pick lives solely in the dispatched
`nozzle_mapping`.

The numbers pinned here are measured on the maintainer's own H2C
(`31B8BP610600650`), not invented:

- 2026-08-14 09:32, picking R1 and R2 -> `nozzle_mapping [16, 1, 17, -1 x29]`
- 2026-08-13 17:20, same plate picking R1 and R3 -> `[16, 1, 18, -1 x29]`
- 2026-08-14 09:02 rack telemetry -> `IDs: [16, 1, 21, 19, 18, 0, 20]`, i.e.
  both carriages present and rack id 17 the lone gap, because its nozzle was
  mounted at the time.
"""

import json
import zipfile

import pytest

from backend.app.services.bambu_mqtt import (
    RACK_POSITIONS,
    _rack_by_position,
    rack_position_to_nozzle_id,
    resolve_rack_plan_mapping,
)
from backend.app.utils.threemf_tools import extract_rack_plan_from_3mf

# The plate this whole feature was built against: three PLA filaments, groups
# 2/0/1, with groups 1 and 2 both on the rack carriage (slicer extruder 2).
BENCHY_FILAMENTS = {
    1: {"group": 2, "color": "#DE4343"},
    2: {"group": 0, "color": "#F4EE2A"},
    3: {"group": 1, "color": "#0078BF"},
}
BENCHY_NOZZLES = {0: 1, 1: 2, 2: 2}

# The machine half of the file, for the tests that hand-write slice_info.
_RACK_SETTINGS = json.dumps(
    {
        "physical_extruder_map": ["1", "0"],
        "extruder_max_nozzle_count": ["1", "6"],
        "extruder_nozzle_stats": ["High Flow#1", "High Flow#6"],
    }
)


def _write_rack_3mf(path, filaments, nozzles, *, plate_index=1, max_nozzles=("1", "6"), diameter="0.40"):
    """A 3MF in the shape BambuStudio writes for a nozzle-rack machine."""
    elems = "".join(
        f'<filament id="{slot}" group_id="{f["group"]}" color="{f["color"]}" '
        f'nozzle_diameter="{f.get("diameter", diameter)}" '
        f'volume_type="{f.get("volume_type", "High Flow")}"/>'
        for slot, f in filaments.items()
    )
    elems += "".join(f'<nozzle id="{group}" extruder_id="{ext}"/>' for group, ext in nozzles.items())
    body = f'<plate><metadata key="index" value="{plate_index}"/>{elems}</plate>'
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "Metadata/project_settings.config",
            json.dumps(
                {
                    "physical_extruder_map": ["1", "0"],
                    "extruder_max_nozzle_count": list(max_nozzles),
                    "extruder_nozzle_stats": ["High Flow#1", "High Flow#6"],
                }
            ),
        )
        zf.writestr("Metadata/slice_info.config", f"<config>{body}</config>")
    return path


def _rack(present=(1, 2, 3, 4, 5, 6), *, diameters=None, types=None, colors=None, carriage=None):
    """Live rack telemetry: a nozzle at each named 1-based position.

    Positions left out are absent from the payload entirely, which is how the
    firmware reports both an empty position and one whose nozzle is currently
    mounted (#943).
    """
    diameters = diameters or {}
    types = types or {}
    colors = colors or {}
    slots = [
        {
            "id": 15 + position,
            "diameter": diameters.get(position, "0.4"),
            "type": types.get(position, "HH01"),
            "filament_color": colors.get(position, ""),
        }
        for position in present
    ]
    # The fixed carriage is always reported; the rack carriage only when it
    # actually holds a nozzle.
    slots.append({"id": 1, "diameter": "0.4", "type": "HH01", "filament_color": ""})
    if carriage:
        slots.append({"id": 0, **carriage})
    return slots


@pytest.fixture
def benchy(tmp_path):
    return _write_rack_3mf(tmp_path / "benchy.3mf", BENCHY_FILAMENTS, BENCHY_NOZZLES)


class TestRackPositionNumbering:
    """Rack position n is physical nozzle id 15 + n."""

    def test_the_six_positions_map_to_the_ids_the_printer_uses(self):
        assert [rack_position_to_nozzle_id(p) for p in RACK_POSITIONS] == [16, 17, 18, 19, 20, 21]

    @pytest.mark.parametrize("position", [0, -1, 7, 16])
    def test_a_position_outside_the_rack_names_no_nozzle(self, position):
        assert rack_position_to_nozzle_id(position) is None

    def test_a_bool_is_not_a_position(self):
        """`True` is an int in Python and would otherwise resolve to R1."""
        assert rack_position_to_nozzle_id(True) is None


class TestReadingThePlan:
    def test_the_maintainers_plate_reads_as_two_rack_groups_and_one_fixed(self, benchy):
        plan = extract_rack_plan_from_3mf(benchy, plate_id=1)

        assert plan.slot_groups == [2, 0, 1]
        assert plan.rack_group_ids == [1, 2]
        assert plan.groups[0].on_rack is False
        assert plan.groups[1].on_rack is True
        assert plan.groups[2].nozzle_diameter == "0.40"
        assert plan.groups[2].volume_type == "High Flow"

    def test_the_rack_carriage_is_read_from_the_file_not_assumed(self, tmp_path):
        """`extruder_max_nozzle_count` names the rack: the one addressing >1.

        A fourth independent confirmation of which extruder index is the rack,
        and the only one that comes from the file itself rather than telemetry.
        """
        source = _write_rack_3mf(tmp_path / "flipped.3mf", BENCHY_FILAMENTS, BENCHY_NOZZLES, max_nozzles=("6", "1"))
        plan = extract_rack_plan_from_3mf(source, plate_id=1)

        # Groups 1 and 2 sit on slicer extruder 2 -> index 1, which is now the
        # single-nozzle carriage; group 0 is on index 0, which is now the rack.
        # Exactly inverted from the same file read with ('1', '6').
        assert plan.rack_group_ids == [0]
        assert plan.groups[1].on_rack is False
        assert plan.groups[2].on_rack is False

    def test_a_machine_with_no_multi_nozzle_carriage_has_no_plan(self, tmp_path):
        source = _write_rack_3mf(tmp_path / "h2d.3mf", BENCHY_FILAMENTS, BENCHY_NOZZLES, max_nozzles=("1", "1"))
        assert extract_rack_plan_from_3mf(source, plate_id=1) is None

    def test_an_ungrouped_filament_makes_the_plan_partial_so_there_is_none(self, tmp_path):
        """A partial plan dispatches the ungrouped slot as unprinted.

        Which contradicts an ams_mapping that does name a tray for it, and the
        firmware rejects the contradiction outright as HMS 0500-4047.
        """
        path = _write_rack_3mf(tmp_path / "partial.3mf", BENCHY_FILAMENTS, BENCHY_NOZZLES)
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("Metadata/project_settings.config", _RACK_SETTINGS)
            zf.writestr(
                "Metadata/slice_info.config",
                '<config><plate><metadata key="index" value="1"/>'
                '<filament id="1" group_id="2" nozzle_diameter="0.40" volume_type="High Flow"/>'
                '<filament id="2"/>'
                '<nozzle id="2" extruder_id="2"/></plate></config>',
            )
        assert extract_rack_plan_from_3mf(path, plate_id=1) is None

    def test_two_filaments_may_share_a_group_and_differ_in_colour(self, tmp_path):
        """Colour is a hint for auto-assignment, not part of the group identity.

        Rejecting the file over it would be wrong: a group is one hotend, and a
        hotend can print more than one colour in sequence.
        """
        source = _write_rack_3mf(
            tmp_path / "shared.3mf",
            {1: {"group": 1, "color": "#FF0000"}, 2: {"group": 1, "color": "#00FF00"}},
            {1: 2},
        )
        plan = extract_rack_plan_from_3mf(source, plate_id=1)

        assert plan.slot_groups == [1, 1]
        assert plan.rack_group_ids == [1]

    def test_filaments_in_one_group_wanting_different_nozzles_is_unresolvable(self, tmp_path):
        """One group is one hotend, so no single position can serve both."""
        source = _write_rack_3mf(
            tmp_path / "contradiction.3mf",
            {
                1: {"group": 1, "color": "#FF0000"},
                2: {"group": 1, "color": "#00FF00", "diameter": "0.60"},
            },
            {1: 2},
        )
        assert extract_rack_plan_from_3mf(source, plate_id=1) is None

    def test_an_unreadable_file_yields_no_plan_rather_than_raising(self, tmp_path):
        broken = tmp_path / "broken.3mf"
        broken.write_bytes(b"not a zip")
        assert extract_rack_plan_from_3mf(broken, plate_id=1) is None

    def test_a_missing_file_yields_no_plan(self, tmp_path):
        assert extract_rack_plan_from_3mf(tmp_path / "absent.3mf", plate_id=1) is None


class TestResolvingAgainstTheLiveRack:
    """The measured dispatches, reproduced from a plan plus a pick."""

    def test_picking_r1_and_r2_reproduces_the_dispatch_of_2026_08_14(self, benchy):
        plan = extract_rack_plan_from_3mf(benchy, plate_id=1)
        wire, error = resolve_rack_plan_mapping(plan.slot_groups, plan.group_dicts(), {2: 1, 1: 2}, _rack())

        assert error is None
        assert wire[:3] == [16, 1, 17]
        assert wire[3:] == [-1] * 29
        assert len(wire) == 32

    def test_picking_r1_and_r3_reproduces_the_dispatch_of_2026_08_13(self, benchy):
        plan = extract_rack_plan_from_3mf(benchy, plate_id=1)
        wire, error = resolve_rack_plan_mapping(plan.slot_groups, plan.group_dicts(), {2: 1, 1: 3}, _rack())

        assert error is None
        assert wire[:3] == [16, 1, 18]

    def test_the_fixed_group_always_lands_on_physical_nozzle_1(self, benchy):
        """Whichever slot it occupies -- confirmed in both captures."""
        plan = extract_rack_plan_from_3mf(benchy, plate_id=1)
        wire, _ = resolve_rack_plan_mapping(plan.slot_groups, plan.group_dicts(), {2: 4, 1: 5}, _rack())

        # Slot 2 is group 0, the fixed carriage.
        assert wire[1] == 1


class TestAutoAssignment:
    def test_an_unpicked_plate_is_assigned_by_colour(self, benchy):
        """Preferring the position already loaded with the group's own colour
        means the operator does not have to move filament to get what they
        asked for. Here that reproduces BambuStudio's own pick exactly.
        """
        plan = extract_rack_plan_from_3mf(benchy, plate_id=1)
        rack = _rack(colors={1: "DE4343FF", 2: "0078BFFF", 4: "FFFFFFFF"})
        wire, error = resolve_rack_plan_mapping(plan.slot_groups, plan.group_dicts(), {}, rack)

        assert error is None
        assert wire[:3] == [16, 1, 17]

    def test_without_a_colour_match_it_takes_the_lowest_free_position(self, benchy):
        plan = extract_rack_plan_from_3mf(benchy, plate_id=1)
        wire, error = resolve_rack_plan_mapping(plan.slot_groups, plan.group_dicts(), {}, _rack())

        assert error is None
        # Groups are assigned lowest-id first: group 1 takes R1, group 2 takes R2.
        assert wire[:3] == [17, 1, 16]

    def test_an_explicit_pick_is_never_stolen_by_an_auto_assignment(self, benchy):
        """The half-picked case: one group named, the other filled in around it."""
        plan = extract_rack_plan_from_3mf(benchy, plate_id=1)
        wire, error = resolve_rack_plan_mapping(plan.slot_groups, plan.group_dicts(), {1: 1}, _rack())

        assert error is None
        assert wire[2] == 16  # group 1, as picked
        assert wire[0] == 17  # group 2, assigned around it

    def test_only_eligible_positions_are_assigned(self, benchy):
        """A 0.2 nozzle cannot lay down a 0.4 extrusion."""
        plan = extract_rack_plan_from_3mf(benchy, plate_id=1)
        rack = _rack(present=(1, 2, 3), diameters={1: "0.2", 2: "0.6"})
        wire, error = resolve_rack_plan_mapping(plan.slot_groups, plan.group_dicts(), {}, rack)

        assert wire is None
        assert "no free rack position" in error

    def test_flow_type_is_matched_as_well_as_diameter(self, benchy):
        plan = extract_rack_plan_from_3mf(benchy, plate_id=1)
        rack = _rack(present=(1, 2), types={1: "HS", 2: "HS"})
        _, error = resolve_rack_plan_mapping(plan.slot_groups, plan.group_dicts(), {}, rack)

        assert "no free rack position" in error

    def test_a_printer_reporting_no_nozzle_type_is_not_ruled_out(self, benchy):
        """Compared only when both sides state it, so a terse firmware still works."""
        plan = extract_rack_plan_from_3mf(benchy, plate_id=1)
        rack = _rack(present=(1, 2), types={1: "", 2: ""})
        wire, error = resolve_rack_plan_mapping(plan.slot_groups, plan.group_dicts(), {}, rack)

        assert error is None
        assert wire[:3] == [17, 1, 16]

    def test_a_padded_diameter_matches_an_unpadded_one(self, benchy):
        """The 3MF writes "0.40" and the printer reports "0.4"."""
        plan = extract_rack_plan_from_3mf(benchy, plate_id=1)
        wire, error = resolve_rack_plan_mapping(
            plan.slot_groups, plan.group_dicts(), {}, _rack(diameters={1: "0.400", 2: "0.4"})
        )

        assert error is None
        assert wire is not None


class TestTheMountedNozzle:
    """#943: a mounted rack nozzle is omitted from telemetry, not blanked."""

    def test_the_lone_gap_is_recovered_from_the_carriage(self):
        """Measured 09:02: IDs [16, 1, 21, 19, 18, 0, 20] -- id 17 the only gap."""
        slots = _rack(present=(1, 3, 4, 5, 6), carriage={"diameter": "0.4", "type": "HH01", "filament_color": ""})
        by_position = _rack_by_position(slots)

        assert set(by_position) == {1, 2, 3, 4, 5, 6}
        assert by_position[2]["id"] == 0  # the carriage's nozzle, standing in for R2

    def test_the_mounted_nozzle_can_be_picked(self, benchy):
        """It is the likeliest pick of all -- the last print left it there."""
        plan = extract_rack_plan_from_3mf(benchy, plate_id=1)
        slots = _rack(present=(1, 3, 4, 5, 6), carriage={"diameter": "0.4", "type": "HH01", "filament_color": ""})
        wire, error = resolve_rack_plan_mapping(plan.slot_groups, plan.group_dicts(), {2: 1, 1: 2}, slots)

        assert error is None
        assert wire[:3] == [16, 1, 17]

    def test_two_gaps_are_ambiguous_and_stay_absent(self):
        """Four nozzles in six positions looks identical to two mounted ones,
        and only one can be mounted at a time. Guessing would offer a position
        that holds nothing.
        """
        slots = _rack(present=(1, 4, 5, 6), carriage={"diameter": "0.4", "type": "HH01"})
        by_position = _rack_by_position(slots)

        assert set(by_position) == {1, 4, 5, 6}

    def test_an_empty_carriage_fills_no_gap(self):
        slots = _rack(present=(1, 3, 4, 5, 6), carriage={"diameter": "", "type": ""})
        assert set(_rack_by_position(slots)) == {1, 3, 4, 5, 6}


class TestRefusals:
    """Every one of these stops a print rather than guessing a hotend.

    A wrong physical id levels with one nozzle and prints with another, several
    millimetres off the bed -- the failure this whole area exists to prevent.
    """

    def test_a_position_holding_nothing_is_refused_with_a_reason(self, benchy):
        plan = extract_rack_plan_from_3mf(benchy, plate_id=1)
        _, error = resolve_rack_plan_mapping(plan.slot_groups, plan.group_dicts(), {2: 1, 1: 3}, _rack(present=(1, 2)))

        assert error == "the printer reports nothing at rack position 3"

    def test_the_wrong_nozzle_names_what_it_holds_and_what_is_needed(self, benchy):
        plan = extract_rack_plan_from_3mf(benchy, plate_id=1)
        _, error = resolve_rack_plan_mapping(
            plan.slot_groups, plan.group_dicts(), {2: 1, 1: 5}, _rack(diameters={5: "0.6"})
        )

        assert "rack position 5 holds a 0.6" in error
        assert "needs 0.40 High Flow" in error

    def test_two_groups_cannot_share_one_position(self, benchy):
        """They are different hotends by definition -- that is what a group is."""
        plan = extract_rack_plan_from_3mf(benchy, plate_id=1)
        _, error = resolve_rack_plan_mapping(plan.slot_groups, plan.group_dicts(), {2: 1, 1: 1}, _rack())

        assert error == "rack position 1 is picked for more than one filament group"

    def test_a_position_beyond_the_rack_is_refused(self, benchy):
        plan = extract_rack_plan_from_3mf(benchy, plate_id=1)
        _, error = resolve_rack_plan_mapping(plan.slot_groups, plan.group_dicts(), {2: 1, 1: 9}, _rack())

        assert error == "rack position 9 does not exist"

    def test_a_plate_with_more_slots_than_the_wire_carries_is_refused(self):
        groups = {0: {"on_rack": False, "nozzle_diameter": "0.4", "volume_type": "High Flow"}}
        _, error = resolve_rack_plan_mapping([0] * 33, groups, {}, _rack())

        assert "33 filament slots" in error

    def test_an_empty_plate_is_refused(self):
        _, error = resolve_rack_plan_mapping([], {}, {}, _rack())
        assert error == "the plate lists no filament slots"

    def test_a_slot_naming_an_undescribed_group_is_refused(self):
        groups = {0: {"on_rack": False, "nozzle_diameter": "0.4", "volume_type": "High Flow"}}
        _, error = resolve_rack_plan_mapping([0, 7], groups, {}, _rack())

        assert error == "filament slot 2 names group 7, which the plate does not describe"

    def test_a_plate_assigning_nothing_is_refused(self):
        """All -1 would tell the printer to print with no nozzle at all."""
        _, error = resolve_rack_plan_mapping([-1, -1], {}, {}, _rack())
        assert error == "the plate assigns no filament to a nozzle"


class TestFixedOnlyPlates:
    def test_a_plate_using_only_the_fixed_hotend_needs_no_rack_position(self, tmp_path):
        source = _write_rack_3mf(tmp_path / "fixed.3mf", {1: {"group": 0, "color": "#FFFFFF"}}, {0: 1})
        plan = extract_rack_plan_from_3mf(source, plate_id=1)
        wire, error = resolve_rack_plan_mapping(plan.slot_groups, plan.group_dicts(), {}, _rack())

        assert error is None
        assert wire[0] == 1
        assert wire[1:] == [-1] * 31

    def test_it_resolves_even_with_an_entirely_empty_rack(self, tmp_path):
        """Nothing is being asked of the rack, so its contents cannot matter."""
        source = _write_rack_3mf(tmp_path / "fixed.3mf", {1: {"group": 0, "color": "#FFFFFF"}}, {0: 1})
        plan = extract_rack_plan_from_3mf(source, plate_id=1)
        wire, error = resolve_rack_plan_mapping(plan.slot_groups, plan.group_dicts(), {}, _rack(present=()))

        assert error is None
        assert wire[0] == 1


class TestPlateScoping:
    def test_the_dispatched_plate_is_the_one_read(self, tmp_path):
        """A multi-plate file may assign the same slot differently per plate."""
        path = tmp_path / "multi.3mf"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("Metadata/project_settings.config", _RACK_SETTINGS)
            zf.writestr(
                "Metadata/slice_info.config",
                "<config>"
                '<plate><metadata key="index" value="1"/>'
                '<filament id="1" group_id="0" nozzle_diameter="0.40" volume_type="High Flow"/>'
                '<nozzle id="0" extruder_id="1"/></plate>'
                '<plate><metadata key="index" value="2"/>'
                '<filament id="1" group_id="1" nozzle_diameter="0.40" volume_type="High Flow"/>'
                '<nozzle id="1" extruder_id="2"/></plate>'
                "</config>",
            )

        assert extract_rack_plan_from_3mf(path, plate_id=1).rack_group_ids == []
        assert extract_rack_plan_from_3mf(path, plate_id=2).rack_group_ids == [1]


class TestEveryRequirementsPathIsAnnotated:
    """The three filament-requirements paths must all carry the group data.

    They each build their filament list differently — the archive route and the
    library route parse `slice_info.config` themselves rather than calling
    `extract_filament_requirements` — so annotating only one of them shipped a
    picker that never appeared in the print dialog. `annotate_rack_groups` is
    the single implementation; these pin that all three reach it.
    """

    def test_the_shared_annotator_tags_a_route_built_filament_list(self, benchy):
        from backend.app.services.filament_requirements import annotate_rack_groups

        # The shape the archive/library routes build, with no group keys.
        filaments = [{"slot_id": 1}, {"slot_id": 2}, {"slot_id": 3}]
        annotate_rack_groups(filaments, benchy, 1)

        assert [f["group_id"] for f in filaments] == [2, 0, 1]
        assert filaments[0]["group"]["on_rack"] is True
        assert filaments[1]["group"]["on_rack"] is False

    def test_a_slot_the_plate_does_not_print_is_left_untagged(self, benchy):
        from backend.app.services.filament_requirements import annotate_rack_groups

        filaments = [{"slot_id": 9}]
        annotate_rack_groups(filaments, benchy, 1)

        assert "group_id" not in filaments[0]

    def test_a_non_rack_file_leaves_every_filament_untouched(self, tmp_path):
        from backend.app.services.filament_requirements import annotate_rack_groups

        source = _write_rack_3mf(tmp_path / "h2d.3mf", BENCHY_FILAMENTS, BENCHY_NOZZLES, max_nozzles=("1", "1"))
        filaments = [{"slot_id": 1}]
        annotate_rack_groups(filaments, source, 1)

        assert filaments == [{"slot_id": 1}]

    @pytest.mark.parametrize("module", ["archives", "library"])
    def test_both_routes_import_the_annotator(self, module):
        """A guard against the two hand-rolled routes drifting out again."""
        import importlib

        route = importlib.import_module(f"backend.app.api.routes.{module}")
        assert hasattr(route, "annotate_rack_groups")
