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
from backend.app.services.inventory_mode import spoolman_owns_assignments
from backend.app.services.spool_filament_preset import resolve_spool_preset, resolve_spoolman_preset


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


def _flow_applies(stored_flow: str | None, fitted_flow: str | None) -> bool:
    """``SlotNozzle.flow_matches`` for callers that hold only the two strings."""
    from backend.app.services.slot_nozzle import SlotNozzle

    return SlotNozzle(extruder=None, diameter="", flow=fitted_flow).flow_matches(stored_flow)


async def find_slot_kprofile_for_extruder(
    db: AsyncSession,
    printer_id: int,
    ams_id: int,
    tray_id: int,
    extruder: int,
    nozzle_diameter: str,
    printer_model: str | None = None,
    flow: str | None = None,
) -> SlotKProfile | None:
    """Stored profile for whatever is in this slot, calibrated for ``extruder``.

    Returns None when the slot holds no known spool, or when that spool has no
    profile for this nozzle — an operator who calibrated only one side should
    keep the binding they set by hand rather than have it swapped for a guess.

    Only the table the current inventory mode uses is consulted. Before #2812
    the inactive one was emptied on every mode toggle, so reading the built-in
    table first and stopping on a hit was safe -- there could be nothing in it
    to stop on. Nothing is emptied now, and a leftover built-in row would
    otherwise shadow the Spoolman assignment for the slot, returning that
    spool's profile or, on the deliberate stop below, no profile at all. That
    is the symptom #1556 reported from the other direction.
    """
    spoolman_mode = await spoolman_owns_assignments(db)

    assignment = (
        None
        if spoolman_mode
        else (
            await db.execute(
                select(SpoolAssignment).where(
                    SpoolAssignment.printer_id == printer_id,
                    SpoolAssignment.ams_id == ams_id,
                    SpoolAssignment.tray_id == tray_id,
                )
            )
        ).scalar_one_or_none()
    )

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
            .all()
        )
        # Flow is filtered here rather than in SQL: a stored NULL matches any
        # fitted nozzle (see SlotNozzle.flow_matches), which is not an equality
        # test and would need an OR IS NULL that reads worse than this.
        profile = next((p for p in profile if _flow_applies(p.nozzle_type, flow)), None)
        if profile is not None:
            spool = (await db.execute(select(Spool).where(Spool.id == assignment.spool_id))).scalar_one_or_none()
            # The preset this profile was calibrated under, through the
            # per-printer-model cascade: a spool can carry a different preset
            # per model, and extrusion_cali_sel has to name the one the printer
            # will actually see in the slot. Falls back to the spool's own
            # value when the caller cannot say which model this is.
            filament_id = spool.slicer_filament if spool else None
            if spool is not None and printer_model:
                filament_id, _ = await resolve_spool_preset(
                    db,
                    spool_id=spool.id,
                    printer_model=printer_model,
                    nozzle_diameter=nozzle_diameter,
                    fallback_filament=spool.slicer_filament,
                    fallback_name=spool.slicer_filament_name,
                )
            return SlotKProfile(
                cali_idx=profile.cali_idx,
                k_value=profile.k_value,
                name=profile.name,
                extruder=profile.extruder,
                filament_id=filament_id,
            )
        # A known spool with no profile for this nozzle is a deliberate stop:
        # falling through to Spoolman would answer for a different spool.
        return None

    if not spoolman_mode:
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
        .all()
    )
    sm_profile = next((p for p in sm_profile if _flow_applies(p.nozzle_type, flow)), None)
    if sm_profile is None:
        return None

    # A Spoolman K row carries no preset of its own, but the spool can still
    # have a per-model override stored locally -- that is the same table the
    # Spoolman assign path writes. Without a model to key on there is nothing
    # to resolve and the caller falls back to the tray's own tray_info_idx.
    sm_filament_id = None
    if printer_model:
        sm_filament_id, _ = await resolve_spoolman_preset(
            db,
            spoolman_spool_id=sm_assignment.spoolman_spool_id,
            printer_model=printer_model,
            nozzle_diameter=nozzle_diameter,
            fallback_filament=None,
            fallback_name=None,
        )
    return SlotKProfile(
        cali_idx=sm_profile.cali_idx,
        k_value=sm_profile.k_value,
        name=sm_profile.name,
        extruder=sm_profile.extruder,
        filament_id=sm_filament_id,
    )
