"""Find the stored K-profile for an AMS slot on a particular nozzle.

K-profiles are per-nozzle: Bambuddy already keeps one row per
``(spool, printer, extruder)`` in ``spool_k_profile`` (and the Spoolman mirror
in ``spoolman_k_profile``), each with its own ``cali_idx`` and K value. On the
maintainer's H2C, one black PLA reads 0.018 on the left hotend and 0.020 on the
right, stored as calibration indices 16 and 15.

A tray, by contrast, holds exactly **one** ``cali_idx``. So whenever a slot's
nozzle changes — which on a Filament Track Switch machine happens every time an
AMS is moved between the switch's two inlets — the stored counterpart for the
new nozzle has to be looked up and re-selected. This module is that lookup.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spool_k_profile import SpoolKProfile
from backend.app.models.spoolman_k_profile import SpoolmanKProfile
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment


@dataclass(frozen=True)
class SlotKProfile:
    """A stored calibration profile, flattened across the two inventory backends."""

    cali_idx: int | None
    k_value: float | None
    name: str | None
    extruder: int
    # The preset the profile was calibrated under. ``extrusion_cali_sel`` must
    # carry this rather than the tray's RFID value, or the firmware mislinks it.
    filament_id: str | None


async def find_slot_kprofile_for_extruder(
    db: AsyncSession,
    printer_id: int,
    ams_id: int,
    tray_id: int,
    extruder: int,
    nozzle_diameter: str,
) -> SlotKProfile | None:
    """Stored profile for whatever is in this slot, calibrated for ``extruder``.

    Returns None when the slot holds no known spool, or when that spool has no
    profile for this nozzle — an operator who calibrated only one side should
    keep the binding they set by hand rather than have it swapped for a guess.

    Local spools take priority over Spoolman, matching the rest of the
    K-profile cascade.
    """
    assignment = (
        await db.execute(
            select(SpoolAssignment).where(
                SpoolAssignment.printer_id == printer_id,
                SpoolAssignment.ams_id == ams_id,
                SpoolAssignment.tray_id == tray_id,
            )
        )
    ).scalar_one_or_none()

    if assignment is not None:
        profile = (
            (
                await db.execute(
                    select(SpoolKProfile).where(
                        SpoolKProfile.spool_id == assignment.spool_id,
                        SpoolKProfile.printer_id == printer_id,
                        SpoolKProfile.extruder == extruder,
                        SpoolKProfile.nozzle_diameter == nozzle_diameter,
                    )
                )
            )
            .scalars()
            .first()
        )
        if profile is not None:
            spool = (await db.execute(select(Spool).where(Spool.id == assignment.spool_id))).scalar_one_or_none()
            return SlotKProfile(
                cali_idx=profile.cali_idx,
                k_value=profile.k_value,
                name=profile.name,
                extruder=profile.extruder,
                filament_id=(spool.slicer_filament if spool else None),
            )
        # A known spool with no profile for this nozzle is a deliberate stop:
        # falling through to Spoolman would answer for a different spool.
        return None

    sm_assignment = (
        await db.execute(
            select(SpoolmanSlotAssignment).where(
                SpoolmanSlotAssignment.printer_id == printer_id,
                SpoolmanSlotAssignment.ams_id == ams_id,
                SpoolmanSlotAssignment.tray_id == tray_id,
            )
        )
    ).scalar_one_or_none()
    if sm_assignment is None:
        return None

    sm_profile = (
        (
            await db.execute(
                select(SpoolmanKProfile).where(
                    SpoolmanKProfile.spoolman_spool_id == sm_assignment.spoolman_spool_id,
                    SpoolmanKProfile.printer_id == printer_id,
                    SpoolmanKProfile.extruder == extruder,
                    SpoolmanKProfile.nozzle_diameter == nozzle_diameter,
                )
            )
        )
        .scalars()
        .first()
    )
    if sm_profile is None:
        return None

    # Spoolman rows carry no slicer preset; the caller falls back to the tray's
    # own tray_info_idx for filament_id.
    return SlotKProfile(
        cali_idx=sm_profile.cali_idx,
        k_value=sm_profile.k_value,
        name=sm_profile.name,
        extruder=sm_profile.extruder,
        filament_id=None,
    )
