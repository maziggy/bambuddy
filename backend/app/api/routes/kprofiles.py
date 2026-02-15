"""API routes for K-profile (pressure advance) management."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.kprofile_note import KProfileNote as KProfileNoteModel
from backend.app.models.printer import Printer
from backend.app.models.user import User
from backend.app.schemas.kprofile import (
    KProfile,
    KProfileCreate,
    KProfileDelete,
    KProfileNote,
    KProfileNoteResponse,
    KProfilesResponse,
)
from backend.app.services.printer_manager import printer_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/printers/{printer_id}/kprofiles", tags=["kprofiles"])


@router.get("/", response_model=KProfilesResponse)
async def get_kprofiles(
    printer_id: int,
    nozzle_diameter: str = "0.4",
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.KPROFILES_READ),
):
    """Get K-profiles from a printer.

    Args:
        printer_id: ID of the printer
        nozzle_diameter: Filter by nozzle diameter (default: "0.4")
    """
    # Check printer exists
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    # Get MQTT client for printer
    client = printer_manager.get_client(printer_id)
    if not client or not client.state.connected:
        raise HTTPException(400, "Printer not connected")

    # Request K-profiles from printer
    profiles = await client.get_kprofiles(nozzle_diameter=nozzle_diameter)

    # Convert from MQTT dataclass to Pydantic schema
    return KProfilesResponse(
        profiles=[
            KProfile(
                slot_id=p.slot_id,
                extruder_id=p.extruder_id,
                nozzle_id=p.nozzle_id,
                nozzle_diameter=p.nozzle_diameter,
                filament_id=p.filament_id,
                name=p.name,
                k_value=p.k_value,
                n_coef=p.n_coef,
                ams_id=p.ams_id,
                tray_id=p.tray_id,
                setting_id=p.setting_id,
            )
            for p in profiles
        ],
        nozzle_diameter=nozzle_diameter,
    )


@router.post("/", response_model=dict)
async def set_kprofile(
    printer_id: int,
    profile: KProfileCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.KPROFILES_UPDATE),
):
    """Create or update a K-profile on the printer.

    For H2D edits (slot_id > 0), this performs an in-place edit using cali_idx.
    For other printers or new profiles, this adds a new profile.

    Args:
        printer_id: ID of the printer
        profile: K-profile data to set
    """
    is_edit = profile.slot_id > 0
    operation = "edit" if is_edit else "add"

    logger.info(
        f"[API] set_kprofile ({operation}): printer={printer_id}, slot_id={profile.slot_id}, "
        f"extruder_id={profile.extruder_id}, nozzle_id={profile.nozzle_id}, "
        f"name={profile.name}, filament_id={profile.filament_id}, k_value={profile.k_value}"
    )

    # Check printer exists
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    # Get MQTT client for printer
    client = printer_manager.get_client(printer_id)
    if not client or not client.state.connected:
        raise HTTPException(400, "Printer not connected")

    # Detect H2D by serial number prefix
    is_h2d = printer.serial_number.startswith("094")

    if is_edit and is_h2d:
        # H2D in-place edit: use cali_idx with slot_id=0 and empty setting_id
        logger.info("[API] H2D in-place edit: cali_idx=%s", profile.slot_id)
        success = client.set_kprofile(
            filament_id=profile.filament_id,
            name=profile.name,
            k_value=profile.k_value,
            nozzle_diameter=profile.nozzle_diameter,
            nozzle_id=profile.nozzle_id,
            extruder_id=profile.extruder_id,
            setting_id=None,
            slot_id=0,
            cali_idx=profile.slot_id,  # Pass the original slot for in-place edit
        )
    elif is_edit:
        # Non-H2D edit: use delete + add approach
        logger.info("[API] Edit: deleting existing profile slot_id=%s", profile.slot_id)
        delete_success = client.delete_kprofile(
            cali_idx=profile.slot_id,
            filament_id=profile.filament_id,
            nozzle_id=profile.nozzle_id,
            nozzle_diameter=profile.nozzle_diameter,
            extruder_id=profile.extruder_id,
            setting_id=profile.setting_id,
        )
        if not delete_success:
            raise HTTPException(500, "Failed to delete existing K-profile for edit")

        # Wait for printer to process the delete before adding
        await asyncio.sleep(0.5)
        logger.info("[API] Edit: delete complete, now adding updated profile")

        success = client.set_kprofile(
            filament_id=profile.filament_id,
            name=profile.name,
            k_value=profile.k_value,
            nozzle_diameter=profile.nozzle_diameter,
            nozzle_id=profile.nozzle_id,
            extruder_id=profile.extruder_id,
            setting_id=None,  # Generate new setting_id for add
            slot_id=0,  # Always 0 for add (new profile)
        )
    else:
        # New profile: add with slot_id=0
        success = client.set_kprofile(
            filament_id=profile.filament_id,
            name=profile.name,
            k_value=profile.k_value,
            nozzle_diameter=profile.nozzle_diameter,
            nozzle_id=profile.nozzle_id,
            extruder_id=profile.extruder_id,
            setting_id=None,  # Generate new setting_id for add
            slot_id=0,  # Always 0 for add (new profile)
        )

    if not success:
        raise HTTPException(500, "Failed to send K-profile command")

    message = "K-profile updated successfully" if is_edit else "K-profile added successfully"
    return {"success": True, "message": message}


@router.post("/batch", response_model=dict)
async def set_kprofiles_batch(
    printer_id: int,
    profiles: list[KProfileCreate],
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.KPROFILES_UPDATE),
):
    """Create multiple K-profiles in a single command (for dual-nozzle).

    This sends all profiles in one MQTT command, which is more reliable
    for dual-nozzle printers that may not handle sequential commands well.

    Args:
        printer_id: ID of the printer
        profiles: List of K-profiles to set
    """
    if not profiles:
        raise HTTPException(400, "No profiles provided")

    logger.info("[API] set_kprofiles_batch: printer=%s, %s profiles", printer_id, len(profiles))
    for p in profiles:
        logger.info("  - extruder_id=%s, name=%s, k_value=%s", p.extruder_id, p.name, p.k_value)

    # Check printer exists
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    # Get MQTT client for printer
    client = printer_manager.get_client(printer_id)
    if not client or not client.state.connected:
        raise HTTPException(400, "Printer not connected")

    # Build list of profile dicts for batch command
    profile_dicts = [
        {
            "filament_id": p.filament_id,
            "name": p.name,
            "k_value": p.k_value,
            "nozzle_id": p.nozzle_id,
            "extruder_id": p.extruder_id,
            "setting_id": p.setting_id,
            "slot_id": p.slot_id,
        }
        for p in profiles
    ]

    # Get nozzle_diameter from first profile (all should have same)
    nozzle_diameter = profiles[0].nozzle_diameter

    success = client.set_kprofiles_batch(profile_dicts, nozzle_diameter)

    if not success:
        raise HTTPException(500, "Failed to send K-profiles batch command")

    return {"success": True, "message": f"Added {len(profiles)} K-profiles"}


@router.delete("/", response_model=dict)
async def delete_kprofile(
    printer_id: int,
    profile: KProfileDelete,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.KPROFILES_DELETE),
):
    """Delete a K-profile from the printer.

    Args:
        printer_id: ID of the printer
        profile: K-profile identification data for deletion
    """
    # Check printer exists
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    # Get MQTT client for printer
    client = printer_manager.get_client(printer_id)
    if not client or not client.state.connected:
        raise HTTPException(400, "Printer not connected")

    # Send the delete command to printer
    logger.info(
        f"[API] delete_kprofile: printer={printer_id}, slot_id={profile.slot_id}, "
        f"setting_id={profile.setting_id}, filament_id={profile.filament_id}"
    )
    success = client.delete_kprofile(
        cali_idx=profile.slot_id,
        filament_id=profile.filament_id,
        nozzle_id=profile.nozzle_id,
        nozzle_diameter=profile.nozzle_diameter,
        extruder_id=profile.extruder_id,
        setting_id=profile.setting_id,
    )

    if not success:
        raise HTTPException(500, "Failed to send K-profile delete command")

    # Wait for printer to process the delete before frontend refetches
    await asyncio.sleep(0.5)

    return {"success": True, "message": "K-profile deleted successfully"}


@router.get("/notes", response_model=KProfileNoteResponse)
async def get_kprofile_notes(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.KPROFILES_READ),
):
    """Get all K-profile notes for a printer.

    Notes are stored locally since printers don't support notes.

    Args:
        printer_id: ID of the printer
    """
    # Check printer exists
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    # Get all notes for this printer
    result = await db.execute(select(KProfileNoteModel).where(KProfileNoteModel.printer_id == printer_id))
    notes = result.scalars().all()

    # Return as a dictionary mapping setting_id -> note
    return KProfileNoteResponse(notes={note.setting_id: note.note for note in notes})


@router.put("/notes", response_model=dict)
async def set_kprofile_note(
    printer_id: int,
    note_data: KProfileNote,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.KPROFILES_UPDATE),
):
    """Set or update a note for a K-profile.

    Args:
        printer_id: ID of the printer
        note_data: The note data (setting_id and note content)
    """
    # Check printer exists
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    # Find existing note or create new one
    result = await db.execute(
        select(KProfileNoteModel).where(
            KProfileNoteModel.printer_id == printer_id,
            KProfileNoteModel.setting_id == note_data.setting_id,
        )
    )
    existing_note = result.scalar_one_or_none()

    if note_data.note.strip():
        # Save or update note
        if existing_note:
            existing_note.note = note_data.note
        else:
            new_note = KProfileNoteModel(
                printer_id=printer_id,
                setting_id=note_data.setting_id,
                note=note_data.note,
            )
            db.add(new_note)
        await db.commit()
        return {"success": True, "message": "Note saved"}
    else:
        # Delete note if empty
        if existing_note:
            await db.delete(existing_note)
            await db.commit()
        return {"success": True, "message": "Note deleted"}


@router.delete("/notes/{setting_id}", response_model=dict)
async def delete_kprofile_note(
    printer_id: int,
    setting_id: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.KPROFILES_DELETE),
):
    """Delete a note for a K-profile.

    Args:
        printer_id: ID of the printer
        setting_id: The setting_id of the K-profile
    """
    # Check printer exists
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    # Find and delete the note
    result = await db.execute(
        select(KProfileNoteModel).where(
            KProfileNoteModel.printer_id == printer_id,
            KProfileNoteModel.setting_id == setting_id,
        )
    )
    existing_note = result.scalar_one_or_none()

    if existing_note:
        await db.delete(existing_note)
        await db.commit()

    return {"success": True, "message": "Note deleted"}
