"""Which nozzle does this AMS slot feed, and how wide is it?

Every path that configures a slot needs the same two facts: the extruder the
slot feeds, and that nozzle's diameter. Both the filament preset and the K
profile are stored per nozzle diameter, so getting the diameter wrong silently
selects the wrong preset *and* the wrong K value -- and before this module the
answer was worked out independently in seven places, each with ``nozzles[0]``
hard-coded as the diameter for every slot on the machine.

``nozzles[0]`` is correct on a single-nozzle printer and correct on a
dual-nozzle printer with the same size fitted both sides, which is why it has
survived. It is wrong the moment someone fits a 0.4 and a 0.2, which is exactly
the machine this feature exists for.

## Which array index belongs to which extruder

``PrinterState.nozzles`` is filled by two different MQTT parsers that use
opposite conventions, and this module is where that is resolved once:

* The **H2/X2 path** (``bambu_mqtt`` ~5100) writes ``nozzles[nozzle["id"]]``
  straight from ``device.nozzle.info``, i.e. indexed by physical nozzle id.
* The **legacy path** (~5013) writes left -> ``nozzles[0]``, right ->
  ``nozzles[1]``, which is the reverse of the extruder ids (extruder 0 is the
  RIGHT hotend).

**MEASURED 2026-08-27 on an H2D with 0.4 high flow LEFT and 0.6 high flow
RIGHT: ``nozzles[0]`` read 0.6 -- the right hotend, which is extruder 0.** So
the array is indexed by extruder id, and the H2 convention (physical nozzle id N
sits on extruder N) is the one that holds.

The legacy branch cannot govern a real dual-nozzle machine anyway: every model
in ``DUAL_NOZZLE_MODELS`` is H2-series or X2D, all of which report
``device.nozzle.info``, and ``left_nozzle_diameter`` appears nowhere in any
captured log or wire trace. On a single-nozzle printer both conventions agree
that index 0 is the only nozzle.

The distinction is invisible on a machine with matching nozzles, since both
conventions then return the same string -- which is why it went unnoticed for so
long, and why this is the single place to change if a future model contradicts
it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.app.utils.fts_routing import slot_extruder
from backend.app.utils.printer_models import is_dual_nozzle_model

logger = logging.getLogger(__name__)

# What a printer that has told us nothing is assumed to have fitted. Matches
# the default every call site used before this module existed.
DEFAULT_NOZZLE_DIAMETER = "0.4"


@dataclass(frozen=True)
class SlotNozzle:
    """The nozzle an AMS slot feeds."""

    # None when the printer has not said which extruder this slot feeds. Callers
    # that must have a number use ``extruder_or_default``; callers that store a
    # row keep the None so "unknown" is not written as "the right-hand nozzle".
    extruder: int | None
    diameter: str
    # "HH" (high flow), "HS" (standard), or None when the printer has not said.
    flow: str | None = None

    @property
    def extruder_or_default(self) -> int:
        """0 when unknown -- correct on a single-nozzle machine, a guess on a dual."""
        return 0 if self.extruder is None else self.extruder

    def flow_matches(self, stored_flow: str | None) -> bool:
        """Whether a stored K profile's flow type applies to this nozzle.

        Unknown on either side matches anything, and that is the load-bearing
        case rather than a nicety:

        * Every K profile stored before this existed has NULL here, so a strict
          comparison would stop applying all of them at once.
        * An X1C declares no flow on any calibration entry -- measured: all
          eight come back with ``nozzle_id: ''`` -- so profiles saved from one
          have nothing truthful to store. Treating "no answer" as "Standard"
          and then filtering on it would break the moment a high-flow nozzle is
          fitted to a machine whose table never mentioned flow.

        Once BOTH sides do declare one, they have to agree: a K value measured
        on a high-flow nozzle is not a fact about a standard one, the same way
        a 0.6 measurement says nothing about a 0.4.
        """
        if not stored_flow or not self.flow:
            return True
        return normalise_flow(stored_flow) == self.flow


def normalise_flow(raw: str | None) -> str | None:
    """The flow-type code in a nozzle id or type string, or None.

    Both spellings reduce to the same two letters, which is the whole point:
    a calibration entry files its nozzle as ``HH00-0.4`` / ``HS00-0.4`` while
    the fitted nozzle reports its type as ``HH01`` -- measured on an H2D, and
    the reason this compares two characters rather than four. The trailing
    digits are a hardware variant the calibration table normalises to ``00``.
    """
    text = (raw or "").strip().upper()
    return text[:2] if text[:2] in ("HH", "HS") else None


def nozzle_flow_for_extruder(state, extruder: int | None, model: str | None = None) -> str | None:
    """The flow type fitted to ``extruder``, or None when the printer is silent.

    Read from the same array as the diameter and indexed the same way. A
    printer that reports no nozzle type -- an X1C sends none at all -- yields
    None, which ``flow_matches`` treats as "applies to anything" rather than
    inventing Standard.
    """
    nozzles = getattr(state, "nozzles", None) or []
    if not nozzles:
        return None

    index = 0
    if extruder is not None and extruder > 0 and is_dual_nozzle_model(model):
        index = extruder

    for candidate in (index, 0):
        if candidate < len(nozzles):
            flow = normalise_flow(getattr(nozzles[candidate], "nozzle_type", ""))
            if flow:
                return flow
    return None


def nozzle_diameter_for_extruder(state, extruder: int | None, model: str | None = None) -> str:
    """The diameter fitted to ``extruder``, or the printer's only nozzle.

    Falls back to index 0, and then to 0.4, whenever the printer has not
    reported the entry -- an absent nozzle must not make this raise, since it is
    called on every assign.
    """
    nozzles = getattr(state, "nozzles", None) or []
    if not nozzles:
        return DEFAULT_NOZZLE_DIAMETER

    index = 0
    if extruder is not None and extruder > 0 and is_dual_nozzle_model(model):
        # Physical nozzle id N sits on extruder N -- see the module docstring
        # for why the legacy left/right convention cannot apply here.
        index = extruder

    for candidate in (index, 0):
        if candidate < len(nozzles):
            diameter = (getattr(nozzles[candidate], "nozzle_diameter", "") or "").strip()
            if diameter:
                return diameter
    return DEFAULT_NOZZLE_DIAMETER


def resolve_slot_nozzle(state, ams_id: int, tray_id: int, model: str | None = None) -> SlotNozzle:
    """The extruder an AMS slot feeds and that nozzle's diameter.

    ``state`` is the live ``PrinterState`` (or None when the printer is not
    connected, which yields the defaults rather than an error).
    """
    if state is None:
        return SlotNozzle(extruder=None, diameter=DEFAULT_NOZZLE_DIAMETER)

    extruder = slot_extruder(
        ams_id,
        tray_id,
        getattr(state, "ams_extruder_map", None),
        getattr(state, "ams_switch_inlet", None),
    )
    return SlotNozzle(
        extruder=extruder,
        diameter=nozzle_diameter_for_extruder(state, extruder, model),
        flow=nozzle_flow_for_extruder(state, extruder, model),
    )
