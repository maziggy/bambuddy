"""Resolve an AMS slot's K value from the printer's calibration table.

H2-series trays carry no ``k`` field of their own — only ``cali_idx`` — so the
K value on the AMS slot card (#2854) is looked up from the printer's
calibration table in ``state.kprofiles``.

That table is not a clean per-nozzle numbering, and treating it as one is what
blanked every slot on a second AMS (#3044). Both of these happen:

* Two profiles can share a ``cali_idx`` and differ by extruder — measured on
  the maintainer's H2C, where one spool read 0.018 on the left nozzle and
  0.020 on the right. Resolving on ``cali_idx`` alone showed the wrong one.
* One profile can be what *both* extruders' slots point at. In the #3044
  capture an X2D's B1 and B3 carried exactly the K values of A4 and A1 — the
  same entries, tagged with one extruder. Demanding an extruder match left
  every slot on the right-hand AMS blank.

The two are told apart by whether the table distinguishes extruders *at all*:

1. a profile filed under the slot's own extruder wins outright;
2. if the slot's extruder appears nowhere in the table, its tagging carries no
   information about this slot, so match on ``cali_idx`` alone — taking the
   answer only when the candidates agree on one K value, with the diameters
   currently installed as the tie-break (which separates a live table from one
   left behind by a nozzle that has since been swapped out).

The condition on step 2 is what keeps the H2C case fixed. There extruder 0 does
hold profiles, so a right-hand slot pointing at an index only the left hotend
has is a real miss — the index means entry 16 *of the right nozzle's table*,
and the left's entry 16 is a different profile. Falling back there is how the
wrong K got shown in the first place.

BambuStudio is looser still: ``AMSItem.cpp`` fills the same card through
``CalibUtils::get_pa_k_n_value_by_cali_idx``, which scans the whole history for
a matching ``cali_idx`` and takes the first hit regardless of nozzle.

If neither step singles out one value the answer is ``None``. A blank space on
the card is a smaller error than confidently printing the other nozzle's
number.
"""

from collections.abc import Callable

from backend.app.utils.fts_routing import slot_extruder


def build_slot_k_resolver(state) -> Callable[[int | None, int, int], float | None]:
    """Return ``resolve(cali_idx, ams_id, tray_id) -> k value or None``.

    Built once per serialization pass and closed over the state, so the REST
    and WebSocket views of the same card cannot answer differently.
    """
    # (extruder, cali_idx) -> {nozzle_diameter: k}. The inner dict is what
    # detects the ambiguity: more than one entry means two nozzles' tables both
    # claim this index on this extruder.
    table: dict[tuple[int, int], dict[str, float]] = {}
    # cali_idx -> [(nozzle_diameter, k)], every extruder together. The fallback
    # for an index no profile claims on the slot's own extruder.
    shared: dict[int, list[tuple[str, float]]] = {}
    for kp in getattr(state, "kprofiles", None) or []:
        if kp.slot_id is None or not kp.k_value:
            continue
        try:
            k_value = float(kp.k_value)
        except (ValueError, TypeError):
            continue  # Skip K-profile entries with unparseable values
        try:
            extruder = int(kp.extruder_id or 0)
        except (ValueError, TypeError):
            extruder = 0
        nozzle = str(kp.nozzle_diameter or "")
        table.setdefault((extruder, kp.slot_id), {})[nozzle] = k_value
        shared.setdefault(kp.slot_id, []).append((nozzle, k_value))

    # Which extruders the table names at all. An extruder missing from this is
    # one the printer is not filing profiles under, which is what makes the
    # cali_idx-only fallback safe for it.
    extruders_filed = {extruder for extruder, _ in table}

    installed = {str(n.nozzle_diameter) for n in (getattr(state, "nozzles", None) or []) if n.nozzle_diameter}

    def _agreed(candidates: list[tuple[str, float]]) -> float | None:
        """The one K these candidates describe, or None if they disagree.

        Values rather than entries: two nozzles listing the same number is not
        an ambiguity, it is the shared profile the fallback exists for.
        """
        values = {k for _, k in candidates}
        if len(values) == 1:
            return values.pop()
        live = {k for nozzle, k in candidates if nozzle in installed}
        return live.pop() if len(live) == 1 else None

    def resolve(cali_idx: int | None, ams_id: int, tray_id: int) -> float | None:
        if cali_idx is None:
            return None
        extruder = slot_extruder(ams_id, tray_id, state.ams_extruder_map, state.ams_switch_inlet)
        # Single-nozzle printers report everything under extruder 0, and that
        # is also the right default when the routing is simply unknown.
        own = extruder if extruder is not None else 0
        by_nozzle = table.get((own, cali_idx))
        if by_nozzle:
            if len(by_nozzle) == 1:
                return next(iter(by_nozzle.values()))
            live = [k for nozzle, k in by_nozzle.items() if nozzle in installed]
            return live[0] if len(live) == 1 else None
        if own in extruders_filed:
            return None
        candidates = shared.get(cali_idx)
        return _agreed(candidates) if candidates else None

    return resolve
