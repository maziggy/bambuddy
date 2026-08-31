"""Which nozzle an AMS slot feeds.

Every path that configures a slot needs the extruder and that nozzle's
diameter, and both the filament preset and the K profile are stored per
diameter -- so a wrong answer here silently selects the wrong preset AND the
wrong K value. Seven call sites used to work it out independently, each reading
``nozzles[0]`` for every slot on the machine.

``nozzles[0]`` is right on a single-nozzle printer, and right on a dual-nozzle
printer with matching nozzles, which is why it survived. These pin the case it
is wrong for: two different sizes fitted, which is the machine the per-nozzle
feature exists for.
"""

from __future__ import annotations

from backend.app.services.slot_nozzle import (
    DEFAULT_NOZZLE_DIAMETER,
    SlotNozzle,
    normalise_flow,
    nozzle_diameter_for_extruder,
    nozzle_flow_for_extruder,
    resolve_slot_nozzle,
)


class _Nozzle:
    def __init__(self, diameter: str):
        self.nozzle_diameter = diameter
        self.nozzle_type = ""


class _State:
    def __init__(self, diameters, ams_extruder_map=None, ams_switch_inlet=None, types=()):
        self.nozzles = [_Nozzle(d) for d in diameters]
        for nozzle, nozzle_type in zip(self.nozzles, types, strict=False):
            nozzle.nozzle_type = nozzle_type
        self.ams_extruder_map = ams_extruder_map
        self.ams_switch_inlet = ams_switch_inlet


# extruder 0 is the RIGHT hotend, 1 the left.
MIXED = ["0.4", "0.2"]


class TestDualNozzleMixedDiameters:
    """The case the old shortcut got wrong."""

    def test_each_hotend_reports_its_own_diameter(self):
        state = _State(MIXED)
        assert nozzle_diameter_for_extruder(state, 0, "H2C") == "0.4"
        assert nozzle_diameter_for_extruder(state, 1, "H2C") == "0.2"

    def test_the_slots_extruder_decides_which_one(self):
        # AMS 0 is bound to the left hotend, AMS 1 to the right.
        state = _State(MIXED, ams_extruder_map={"0": 1, "1": 0})

        left = resolve_slot_nozzle(state, 0, 0, "H2C")
        right = resolve_slot_nozzle(state, 1, 0, "H2C")
        assert (left.extruder, left.diameter) == (1, "0.2")
        assert (right.extruder, right.diameter) == (0, "0.4")

    def test_external_slots_name_their_side_by_tray_id(self):
        # Ext-L is tray 0 -> extruder 1, Ext-R is tray 1 -> extruder 0.
        state = _State(MIXED)
        assert resolve_slot_nozzle(state, 255, 0, "H2D").diameter == "0.2"
        assert resolve_slot_nozzle(state, 255, 1, "H2D").diameter == "0.4"

    def test_an_fts_inlet_answers_when_there_is_no_extruder_map(self):
        state = _State(MIXED, ams_switch_inlet={"0": "A"})
        resolved = resolve_slot_nozzle(state, 0, 0, "H2C")
        assert resolved.extruder is not None
        assert resolved.diameter in {"0.4", "0.2"}


class TestSingleNozzle:
    """Must behave exactly as the old shortcut did."""

    def test_index_zero_whatever_the_map_says(self):
        # A single-nozzle model has one entry; an extruder id from a stale map
        # must not index past it.
        state = _State(["0.6"], ams_extruder_map={"0": 1})
        assert nozzle_diameter_for_extruder(state, 1, "X1C") == "0.6"
        assert resolve_slot_nozzle(state, 0, 0, "X1C").diameter == "0.6"

    def test_no_map_means_unknown_extruder_not_zero(self):
        # "I don't know" and "the right-hand nozzle" are different answers; the
        # caller's own default of 0 is correct on a single-nozzle machine, but
        # storing it as a fact is what bound a left K-profile to a right slot.
        state = _State(["0.4"])
        resolved = resolve_slot_nozzle(state, 0, 0, "X1C")
        assert resolved.extruder is None
        assert resolved.extruder_or_default == 0


class TestMissingHardware:
    """Called on every assign, so it must never raise."""

    def test_no_state_at_all(self):
        resolved = resolve_slot_nozzle(None, 0, 0, "H2C")
        assert (resolved.extruder, resolved.diameter) == (None, DEFAULT_NOZZLE_DIAMETER)

    def test_printer_has_reported_no_nozzles(self):
        assert nozzle_diameter_for_extruder(_State([]), 0, "H2C") == DEFAULT_NOZZLE_DIAMETER

    def test_the_second_hotend_is_absent_from_the_report(self):
        # H2C parks a nozzle back in its rack and the entry can go missing;
        # falling back to the other hotend beats returning nothing.
        assert nozzle_diameter_for_extruder(_State(["0.4"]), 1, "H2C") == "0.4"

    def test_a_blank_diameter_is_not_a_diameter(self):
        # NozzleInfo starts life with an empty string before MQTT fills it in.
        # Falling back to the OTHER hotend's size would invent a fact about a
        # different nozzle, so a blank primary takes the default instead --
        # which is what every call site did before this module existed.
        assert nozzle_diameter_for_extruder(_State(["", "0.6"]), 0, "H2C") == DEFAULT_NOZZLE_DIAMETER
        assert nozzle_diameter_for_extruder(_State(["", ""]), 0, "H2C") == DEFAULT_NOZZLE_DIAMETER


class TestMatchingNozzles:
    """The common fleet: both hotends the same size."""

    def test_both_conventions_agree_so_the_answer_cannot_be_wrong(self):
        state = _State(["0.4", "0.4"], ams_extruder_map={"0": 1})
        assert nozzle_diameter_for_extruder(state, 0, "H2C") == "0.4"
        assert nozzle_diameter_for_extruder(state, 1, "H2C") == "0.4"


class TestFlowType:
    """High Flow vs Standard. The same filament reads a different K through
    each, and a printer files them as separate calibration entries."""

    def test_the_fitted_nozzles_flow_is_read_per_hotend(self):
        # Measured spelling: a fitted nozzle reports HH01/HS01, while a
        # calibration entry says HH00-0.4 -- two characters is the comparison.
        state = _State(MIXED, types=("HH01", "HS01"))
        assert nozzle_flow_for_extruder(state, 0, "H2C") == "HH"
        assert nozzle_flow_for_extruder(state, 1, "H2C") == "HS"

    def test_a_printer_that_declares_no_flow_answers_none(self):
        # An X1C sends no flow on any profile, and legacy printers put the
        # nozzle MATERIAL in this field. Neither is a flow type.
        assert nozzle_flow_for_extruder(_State(["0.4"], types=("",)), 0, "X1C") is None
        assert nozzle_flow_for_extruder(_State(["0.4"], types=("hardened_steel",)), 0, "X1C") is None

    def test_normalise_accepts_both_spellings(self):
        assert normalise_flow("HH00-0.4") == "HH"
        assert normalise_flow("HH01") == "HH"
        assert normalise_flow("hs00-0.4") == "HS"
        assert normalise_flow("") is None
        assert normalise_flow(None) is None


class TestFlowMatching:
    """Which stored profiles apply to the nozzle now fitted."""

    def test_the_flows_must_agree_once_both_are_known(self):
        high = SlotNozzle(extruder=0, diameter="0.4", flow="HH")
        assert high.flow_matches("HH00") is True
        assert high.flow_matches("HS00") is False

    def test_a_profile_with_no_stored_flow_still_applies(self):
        # Every profile saved before flow was recorded has NULL here -- a
        # strict comparison would stop applying all of them at once.
        high = SlotNozzle(extruder=0, diameter="0.4", flow="HH")
        assert high.flow_matches(None) is True
        assert high.flow_matches("") is True

    def test_a_printer_with_no_fitted_flow_applies_everything(self):
        # The X1C case: filtering on an invented Standard would drop every
        # profile the moment a high-flow nozzle was fitted.
        unknown = SlotNozzle(extruder=0, diameter="0.4", flow=None)
        assert unknown.flow_matches("HH00") is True
        assert unknown.flow_matches("HS00") is True

    def test_resolve_carries_the_flow_with_the_diameter(self):
        state = _State(MIXED, ams_extruder_map={"0": 1, "1": 0}, types=("HH01", "HS01"))
        left = resolve_slot_nozzle(state, 0, 0, "H2C")
        assert (left.extruder, left.diameter, left.flow) == (1, "0.2", "HS")
