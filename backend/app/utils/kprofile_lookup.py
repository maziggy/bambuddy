"""Resolve an AMS slot's K value from the printer's calibration table.

H2-series trays carry no ``k`` field of their own — only ``cali_idx`` — so the
K value on the AMS slot card (#2854) is looked up from the printer's
calibration table in ``state.kprofiles``. That table is not flat: the printer
numbers it **per nozzle**, so entry 16 exists under every nozzle it holds
profiles for and means a different profile on each.

``state.kprofiles`` is the union across nozzle diameters (see
``BambuMQTTClient._store_kprofiles``), which is what the assign paths need but
makes ``cali_idx`` alone ambiguous. Resolution here is therefore:

1. the slot's own extruder, which separates the two nozzles of a dual-nozzle
   machine outright;
2. failing that, the diameters currently installed, which separates a live
   table from one left behind by a nozzle that has since been swapped out.

If both fail to single out one profile the answer is ``None``. A blank space on
the card is a smaller error than confidently printing the other nozzle's number.
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
        table.setdefault((extruder, kp.slot_id), {})[str(kp.nozzle_diameter or "")] = k_value

    installed = {str(n.nozzle_diameter) for n in (getattr(state, "nozzles", None) or []) if n.nozzle_diameter}

    def resolve(cali_idx: int | None, ams_id: int, tray_id: int) -> float | None:
        if cali_idx is None:
            return None
        extruder = slot_extruder(ams_id, tray_id, state.ams_extruder_map, state.ams_switch_inlet)
        # Single-nozzle printers report everything under extruder 0, and that
        # is also the right default when the routing is simply unknown.
        by_nozzle = table.get((extruder if extruder is not None else 0, cali_idx))
        if not by_nozzle:
            return None
        if len(by_nozzle) == 1:
            return next(iter(by_nozzle.values()))
        live = [k for nozzle, k in by_nozzle.items() if nozzle in installed]
        return live[0] if len(live) == 1 else None

    return resolve
