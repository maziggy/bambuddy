"""Which nozzle an AMS slot feeds, with or without a Filament Track Switch.

K-profiles are per-nozzle, and the printer's calibration tables are numbered
per-nozzle too: ``cali_idx: 16`` means "entry 16 of whichever nozzle feeds this
tray". Without a switch that is unambiguous, because each AMS is wired to one
extruder and says so in its ``info`` bits. With a switch installed every AMS
reports 0xE instead and is bound to a switch *inlet*, so the answer has to come
from the inlet binding.

Every caller that resolves a slot to an extruder should go through
``slot_extruder`` here. Three separate copies of that logic used to end in
``else 0``, which on a switch machine silently filed every profile under the
right-hand nozzle regardless of where the slot actually was.

Kept as a leaf module with no imports of its own so the routes, the MQTT layer
and the scheduler can all share one answer.
"""

# Which extruder each switch outlet terminates at. Measured on the maintainer's
# H2C, 2026-08-16: Out-A is the left hotend, Out-B is the right one.
#
# We would rather read this than assert it, but it is not in the telemetry:
# ``fila_switch.out`` reported ``[1, 1]`` unchanged across a 90-second capture,
# i.e. both outlets claiming the same extruder, which cannot describe real
# wiring. Whatever that field means, it is not outlet-to-nozzle.
#
# The inlet-to-outlet pairing then comes from the switch's un-crossed rest
# position: In-A -> Out-A, In-B -> Out-B. The switch does cross the two during a
# filament change, so this describes where a slot sits between prints, which is
# what a manual "configure this slot" task needs. It is deliberately one table
# to change if a machine turns up with its outlet tubes swapped.
FTS_INLET_EXTRUDER: dict[str, int] = {
    "A": 1,  # left / deputy
    "B": 0,  # right / main
}


def extruder_for_inlet(inlet: str | None) -> int | None:
    """Extruder fed by switch inlet ``"A"`` or ``"B"``; None for anything else."""
    if not inlet:
        return None
    return FTS_INLET_EXTRUDER.get(inlet.upper())


def slot_extruder(
    ams_id: int,
    tray_id: int,
    ams_extruder_map: dict | None,
    ams_switch_inlet: dict | None = None,
) -> int | None:
    """Resolve one AMS slot to the extruder it feeds, or None if unknowable.

    Returns None rather than guessing. A single-nozzle printer has no map and no
    switch, and there the caller's own default of extruder 0 is correct — but on
    a dual-nozzle machine "I don't know" and "the right-hand nozzle" are very
    different answers, and conflating them is what bound a left-nozzle K-profile
    to a slot sitting on the right.

    ``ams_id`` 255 is the external spool holder, where the tray id names the
    side directly: tray 0 is Ext-L (extruder 1) and tray 1 is Ext-R (extruder 0).
    """
    if ams_id == 255:
        return 1 - tray_id if tray_id in (0, 1) else None

    # A real extruder id always wins. BambuStudio treats a non-0xE value as
    # authoritative too, so an AMS wired straight to one nozzle keeps that
    # binding even on a machine that has a switch fitted for its other units.
    if ams_extruder_map:
        mapped = ams_extruder_map.get(str(ams_id))
        if mapped is not None:
            return int(mapped)

    if ams_switch_inlet:
        return extruder_for_inlet(ams_switch_inlet.get(str(ams_id)))

    return None
