"""Spoolman integration API routes."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.models.user import User
from backend.app.services.printer_manager import printer_manager
from backend.app.services.spoolman import (
    SpoolmanNotFoundError,
    SpoolmanUnavailableError,
    close_spoolman_client,
    get_spoolman_client,
    init_spoolman_client,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/spoolman", tags=["spoolman"])


class SpoolmanStatus(BaseModel):
    """Spoolman connection status."""

    enabled: bool
    connected: bool
    url: str | None


class SkippedSpool(BaseModel):
    """Information about a skipped spool during sync."""

    location: str  # e.g., "AMS A1" or "External Spool"
    reason: str  # e.g., "Not a Bambu Lab spool", "Empty tray"
    filament_type: str | None = None  # e.g., "PLA", "PETG"
    color: str | None = None  # Hex color


class SyncResult(BaseModel):
    """Result of a Spoolman sync operation."""

    success: bool
    synced_count: int
    skipped_count: int = 0
    skipped: list[SkippedSpool] = []
    errors: list[str]


async def get_spoolman_settings(db: AsyncSession) -> dict:
    """Get Spoolman settings from database.

    Returns:
        Dict with keys: enabled, url, sync_mode, disable_weight_sync
    """
    settings = {
        "enabled": False,
        "url": "",
        "sync_mode": "auto",
        "disable_weight_sync": False,
    }

    result = await db.execute(select(Settings))
    for setting in result.scalars().all():
        if setting.key == "spoolman_enabled":
            settings["enabled"] = setting.value.lower() == "true"
        elif setting.key == "spoolman_url":
            settings["url"] = setting.value
        elif setting.key == "spoolman_sync_mode":
            settings["sync_mode"] = setting.value
        elif setting.key == "spoolman_disable_weight_sync":
            settings["disable_weight_sync"] = setting.value.lower() == "true"

    return settings


@router.get("/status", response_model=SpoolmanStatus)
async def get_spoolman_status(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FILAMENTS_READ),
):
    """Get Spoolman integration status."""
    sm = await get_spoolman_settings(db)
    enabled, url = sm["enabled"], sm["url"]

    client = await get_spoolman_client()
    connected = False
    if client:
        connected = await client.health_check()

    return SpoolmanStatus(
        enabled=enabled,
        connected=connected,
        url=url if url else None,
    )


@router.post("/connect")
async def connect_spoolman(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Connect to Spoolman server using configured URL."""
    sm = await get_spoolman_settings(db)
    enabled, url = sm["enabled"], sm["url"]

    if not enabled:
        raise HTTPException(status_code=400, detail="Spoolman integration is not enabled")

    if not url:
        raise HTTPException(status_code=400, detail="Spoolman URL is not configured")

    try:
        client = await init_spoolman_client(url)
        connected = await client.health_check()

        if not connected:
            raise HTTPException(
                status_code=503,
                detail=f"Could not connect to Spoolman at {url}",
            )

        # Ensure the 'tag' extra field exists for RFID/UUID storage
        await client.ensure_tag_extra_field()

        return {"success": True, "message": f"Connected to Spoolman at {url}"}
    except Exception as e:
        logger.error("Failed to connect to Spoolman: %s", e)
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/disconnect")
async def disconnect_spoolman(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Disconnect from Spoolman server."""
    await close_spoolman_client()
    return {"success": True, "message": "Disconnected from Spoolman"}


@router.post("/sync/{printer_id}", response_model=SyncResult)
async def sync_printer_ams(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FILAMENTS_UPDATE),
):
    """Sync AMS data from a specific printer to Spoolman."""
    # Check if Spoolman is enabled and connected
    sm = await get_spoolman_settings(db)
    enabled, url, disable_weight_sync = sm["enabled"], sm["url"], sm["disable_weight_sync"]
    if not enabled:
        raise HTTPException(status_code=400, detail="Spoolman integration is not enabled")

    client = await get_spoolman_client()
    if not client:
        # Try to connect
        if url:
            client = await init_spoolman_client(url)
        else:
            raise HTTPException(status_code=400, detail="Spoolman URL is not configured")

    if not await client.health_check():
        raise HTTPException(status_code=503, detail="Spoolman is not reachable")

    # Get printer info
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")

    # Get current printer state with AMS data
    state = printer_manager.get_status(printer_id)
    if not state:
        raise HTTPException(status_code=404, detail="Printer not connected")

    if not state.raw_data:
        raise HTTPException(status_code=400, detail="No AMS data available")

    ams_data = state.raw_data.get("ams")
    if not ams_data:
        raise HTTPException(
            status_code=400,
            detail="No AMS data in printer state. Try triggering a slot re-read on the printer.",
        )

    # Sync each AMS tray to Spoolman
    synced = 0
    skipped: list[SkippedSpool] = []
    errors = []

    # Handle different AMS data structures
    # Traditional AMS: list of {"id": N, "tray": [...]} dicts
    # H2D/newer printers: dict with different structure
    ams_units = []
    if isinstance(ams_data, list):
        ams_units = ams_data
    elif isinstance(ams_data, dict):
        # H2D format: check for "ams" key containing list, or "tray" key directly
        if "ams" in ams_data and isinstance(ams_data["ams"], list):
            ams_units = ams_data["ams"]
        elif "tray" in ams_data:
            # Single AMS unit format - wrap in list
            ams_units = [{"id": 0, "tray": ams_data.get("tray", [])}]
        else:
            logger.info("AMS dict keys for debugging: %s", list(ams_data.keys()))

    if not ams_units:
        raise HTTPException(
            status_code=400,
            detail=(
                "AMS data format not supported. Keys: "
                f"{list(ams_data.keys()) if isinstance(ams_data, dict) else type(ams_data).__name__}"
            ),
        )

    # OPTIMIZATION: Fetch all spools once before processing trays
    # This eliminates redundant API calls (one per tray) when syncing multiple trays
    logger.debug("[Printer %s] Fetching spools cache for sync...", printer.name)
    try:
        cached_spools = await client.get_spools()
        logger.debug("[Printer %s] Cached %d spools for batch sync", printer.name, len(cached_spools))
    except Exception as e:
        logger.error("[Printer %s] Failed to fetch spools cache after retries: %s", printer.name, e)
        raise HTTPException(
            status_code=503,
            detail=f"Failed to connect to Spoolman after multiple retries: {str(e)}",
        )

    # Load inventory weights as fallback (when AMS MQTT data lacks remain values)
    inv_weights: dict[tuple[int, int], float] = {}
    try:
        assign_result = await db.execute(
            select(SpoolAssignment)
            .options(selectinload(SpoolAssignment.spool))
            .where(SpoolAssignment.printer_id == printer_id)
        )
        for assignment in assign_result.scalars().all():
            spool = assignment.spool
            if spool and spool.label_weight > 0:
                remaining = max(0.0, spool.label_weight - (spool.weight_used or 0))
                inv_weights[(assignment.ams_id, assignment.tray_id)] = remaining
    except Exception as e:
        logger.debug("Could not load inventory weights for printer %s: %s", printer_id, e)

    # Load existing Spoolman slot assignments for the no-RFID fallback path
    spoolman_slot_map: dict[tuple[int, int], int] = {}
    try:
        slot_result = await db.execute(
            select(SpoolmanSlotAssignment).where(SpoolmanSlotAssignment.printer_id == printer_id)
        )
        for slot in slot_result.scalars().all():
            spoolman_slot_map[(slot.ams_id, slot.tray_id)] = slot.spoolman_spool_id
    except Exception as e:
        logger.warning("Could not load Spoolman slot assignments for printer %s: %s", printer_id, e)

    slot_changes: list[tuple[int, int, int]] = []  # (ams_id, tray_id, spoolman_spool_id)
    empty_slots: list[tuple[int, int]] = []  # (ams_id, tray_id) now empty

    for ams_unit in ams_units:
        if not isinstance(ams_unit, dict):
            continue

        ams_id = int(ams_unit.get("id", 0))
        trays = ams_unit.get("tray", [])

        for tray_data in trays:
            if not isinstance(tray_data, dict):
                continue

            tray_id_raw = int(tray_data.get("id", 0))
            tray = client.parse_ams_tray(ams_id, tray_data)
            if not tray:
                empty_slots.append((ams_id, tray_id_raw))
                continue

            spool_tag = (
                tray.tray_uuid
                if tray.tray_uuid and tray.tray_uuid != "00000000000000000000000000000000"
                else tray.tag_uid
            )

            hint = spoolman_slot_map.get((ams_id, tray.tray_id)) if not spool_tag else None

            try:
                inv_remaining = inv_weights.get((ams_id, tray.tray_id))
                sync_result = await client.sync_ams_tray(
                    tray,
                    printer.name,
                    disable_weight_sync=disable_weight_sync,
                    cached_spools=cached_spools,
                    inventory_remaining=inv_remaining,
                    spoolman_spool_id_hint=hint,
                )
                if sync_result:
                    synced += 1
                    if sync_result.get("id"):
                        slot_changes.append((ams_id, tray.tray_id, sync_result["id"]))
                        spool_exists = any(s.get("id") == sync_result["id"] for s in cached_spools)
                        if not spool_exists:
                            cached_spools.append(sync_result)
                            logger.debug("Added newly created spool %s to cache", sync_result["id"])
                    logger.info(
                        "Synced %s from %s AMS %s tray %s", tray.tray_sub_brands, printer.name, ams_id, tray.tray_id
                    )
                elif spool_tag:
                    errors.append(f"Spool not found in Spoolman: AMS {ams_id}:{tray.tray_id}")
                elif not hint:
                    skipped.append(
                        SkippedSpool(
                            location=f"AMS {ams_id} T{tray.tray_id}",
                            reason="No RFID tag and no slot assignment",
                            filament_type=tray.tray_type or None,
                            color=tray.tray_color[:6] if tray.tray_color else None,
                        )
                    )
            except Exception as e:
                error_msg = f"Error syncing AMS {ams_id} tray {tray.tray_id}: {e}"
                logger.error(error_msg)
                errors.append(error_msg)

    # Persist slot assignment changes to the local table
    if slot_changes or empty_slots:
        try:
            for ams_id, tray_id, spool_id in slot_changes:
                await db.execute(
                    text(
                        "INSERT INTO spoolman_slot_assignments"
                        " (printer_id, ams_id, tray_id, spoolman_spool_id)"
                        " VALUES (:printer_id, :ams_id, :tray_id, :spool_id)"
                        " ON CONFLICT(printer_id, ams_id, tray_id)"
                        " DO UPDATE SET spoolman_spool_id = excluded.spoolman_spool_id"
                    ),
                    {"printer_id": printer_id, "ams_id": ams_id, "tray_id": tray_id, "spool_id": spool_id},
                )
            for ams_id, tray_id in empty_slots:
                await db.execute(
                    delete(SpoolmanSlotAssignment).where(
                        SpoolmanSlotAssignment.printer_id == printer_id,
                        SpoolmanSlotAssignment.ams_id == ams_id,
                        SpoolmanSlotAssignment.tray_id == tray_id,
                    )
                )
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.error("Error persisting Spoolman slot assignments for printer %s: %s", printer_id, e)
            errors.append(f"Failed to persist slot assignments: {type(e).__name__}")

    return SyncResult(
        success=len(errors) == 0,
        synced_count=synced,
        skipped_count=len(skipped),
        skipped=skipped,
        errors=errors,
    )


@router.post("/sync-all", response_model=SyncResult)
async def sync_all_printers(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FILAMENTS_UPDATE),
):
    """Sync AMS data from all connected printers to Spoolman."""
    # Check if Spoolman is enabled
    sm = await get_spoolman_settings(db)
    enabled, url, disable_weight_sync = sm["enabled"], sm["url"], sm["disable_weight_sync"]
    if not enabled:
        raise HTTPException(status_code=400, detail="Spoolman integration is not enabled")

    client = await get_spoolman_client()
    if not client:
        if url:
            client = await init_spoolman_client(url)
        else:
            raise HTTPException(status_code=400, detail="Spoolman URL is not configured")

    if not await client.health_check():
        raise HTTPException(status_code=503, detail="Spoolman is not reachable")

    # Get all active printers
    result = await db.execute(select(Printer).where(Printer.is_active.is_(True)))
    printers = result.scalars().all()

    total_synced = 0
    all_skipped: list[SkippedSpool] = []
    all_errors = []

    # OPTIMIZATION: Fetch all spools once before processing ALL printers/trays
    # This eliminates redundant API calls across all printers
    logger.debug("Fetching spools cache for sync-all operation...")
    try:
        cached_spools = await client.get_spools()
        logger.debug("Cached %d spools for batch sync across %d printers", len(cached_spools), len(printers))
    except Exception as e:
        logger.error("Failed to fetch spools cache after retries: %s", e)
        raise HTTPException(
            status_code=503,
            detail=f"Failed to connect to Spoolman after multiple retries: {str(e)}",
        )

    # Load inventory assignments for weight fallback (when AMS MQTT data lacks remain values)
    # Key: (printer_id, ams_id, tray_id) → remaining_weight in grams
    inventory_weights: dict[tuple[int, int, int], float] = {}
    try:
        assign_result = await db.execute(select(SpoolAssignment).options(selectinload(SpoolAssignment.spool)))
        for assignment in assign_result.scalars().all():
            spool = assignment.spool
            if spool and spool.label_weight > 0:
                remaining = max(0.0, spool.label_weight - (spool.weight_used or 0))
                inventory_weights[(assignment.printer_id, assignment.ams_id, assignment.tray_id)] = remaining
    except Exception as e:
        logger.debug("Could not load inventory assignments for weight fallback: %s", e)

    # Load all Spoolman slot assignments for the no-RFID fallback
    # Key: (printer_id, ams_id, tray_id) → spoolman_spool_id
    all_slot_map: dict[tuple[int, int, int], int] = {}
    try:
        slot_result = await db.execute(select(SpoolmanSlotAssignment))
        for slot in slot_result.scalars().all():
            all_slot_map[(slot.printer_id, slot.ams_id, slot.tray_id)] = slot.spoolman_spool_id
    except Exception as e:
        logger.warning("Could not load Spoolman slot assignments: %s", e)

    # Collect slot changes across all printers for a single DB write at the end
    all_slot_changes: list[tuple[int, int, int, int]] = []  # (printer_id, ams_id, tray_id, spool_id)
    all_empty_slots: list[tuple[int, int, int]] = []  # (printer_id, ams_id, tray_id)

    for printer in printers:
        state = printer_manager.get_status(printer.id)
        if not state or not state.raw_data:
            continue

        ams_data = state.raw_data.get("ams")
        if not ams_data:
            continue

        # Handle different AMS data structures
        # Traditional AMS: list of {"id": N, "tray": [...]} dicts
        # H2D/newer printers: dict with different structure
        ams_units = []
        if isinstance(ams_data, list):
            ams_units = ams_data
        elif isinstance(ams_data, dict):
            # H2D format: check for "ams" key containing list, or "tray" key directly
            if "ams" in ams_data and isinstance(ams_data["ams"], list):
                ams_units = ams_data["ams"]
            elif "tray" in ams_data:
                # Single AMS unit format - wrap in list
                ams_units = [{"id": 0, "tray": ams_data.get("tray", [])}]
            else:
                logger.debug("Printer %s AMS dict keys: %s", printer.name, list(ams_data.keys()))

        if not ams_units:
            logger.debug("Printer %s has no AMS units to sync (type: %s)", printer.name, type(ams_data).__name__)
            continue

        for ams_unit in ams_units:
            if not isinstance(ams_unit, dict):
                logger.debug("Skipping non-dict AMS unit: %s", type(ams_unit))
                continue

            ams_id = int(ams_unit.get("id", 0))
            trays = ams_unit.get("tray", [])

            for tray_data in trays:
                if not isinstance(tray_data, dict):
                    continue

                tray_id_raw = int(tray_data.get("id", 0))
                tray = client.parse_ams_tray(ams_id, tray_data)
                if not tray:
                    all_empty_slots.append((printer.id, ams_id, tray_id_raw))
                    continue

                spool_tag = (
                    tray.tray_uuid
                    if tray.tray_uuid and tray.tray_uuid != "00000000000000000000000000000000"
                    else tray.tag_uid
                )

                hint = all_slot_map.get((printer.id, ams_id, tray.tray_id)) if not spool_tag else None

                try:
                    inv_remaining = inventory_weights.get((printer.id, ams_id, tray.tray_id))
                    sync_result = await client.sync_ams_tray(
                        tray,
                        printer.name,
                        disable_weight_sync=disable_weight_sync,
                        cached_spools=cached_spools,
                        inventory_remaining=inv_remaining,
                        spoolman_spool_id_hint=hint,
                    )
                    if sync_result:
                        total_synced += 1
                        if sync_result.get("id"):
                            all_slot_changes.append((printer.id, ams_id, tray.tray_id, sync_result["id"]))
                            spool_exists = any(s.get("id") == sync_result["id"] for s in cached_spools)
                            if not spool_exists:
                                cached_spools.append(sync_result)
                                logger.debug("Added newly created spool %s to cache", sync_result["id"])
                    elif spool_tag:
                        all_errors.append(
                            f"Spool not found in Spoolman: {printer.name} AMS {ams_id}:{tray.tray_id}"
                        )
                    elif not hint:
                        all_skipped.append(
                            SkippedSpool(
                                location=f"{printer.name} AMS {ams_id} T{tray.tray_id}",
                                reason="No RFID tag and no slot assignment",
                                filament_type=tray.tray_type or None,
                                color=tray.tray_color[:6] if tray.tray_color else None,
                            )
                        )
                except Exception as e:
                    all_errors.append(f"{printer.name} AMS {ams_id}:{tray.tray_id}: {e}")

    # Persist slot assignment changes across all printers
    if all_slot_changes or all_empty_slots:
        try:
            for p_id, ams_id, tray_id, spool_id in all_slot_changes:
                await db.execute(
                    text(
                        "INSERT INTO spoolman_slot_assignments"
                        " (printer_id, ams_id, tray_id, spoolman_spool_id)"
                        " VALUES (:printer_id, :ams_id, :tray_id, :spool_id)"
                        " ON CONFLICT(printer_id, ams_id, tray_id)"
                        " DO UPDATE SET spoolman_spool_id = excluded.spoolman_spool_id"
                    ),
                    {"printer_id": p_id, "ams_id": ams_id, "tray_id": tray_id, "spool_id": spool_id},
                )
            for p_id, ams_id, tray_id in all_empty_slots:
                await db.execute(
                    delete(SpoolmanSlotAssignment).where(
                        SpoolmanSlotAssignment.printer_id == p_id,
                        SpoolmanSlotAssignment.ams_id == ams_id,
                        SpoolmanSlotAssignment.tray_id == tray_id,
                    )
                )
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.error("Error persisting Spoolman slot assignments: %s", e)
            all_errors.append(f"Failed to persist slot assignments: {type(e).__name__}")

    return SyncResult(
        success=len(all_errors) == 0,
        synced_count=total_synced,
        skipped_count=len(all_skipped),
        skipped=all_skipped,
        errors=all_errors,
    )


@router.get("/spools")
async def get_spools(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FILAMENTS_READ),
):
    """Get all spools from Spoolman."""
    sm = await get_spoolman_settings(db)
    enabled, url = sm["enabled"], sm["url"]
    if not enabled:
        raise HTTPException(status_code=400, detail="Spoolman integration is not enabled")

    client = await get_spoolman_client()
    if not client:
        if url:
            client = await init_spoolman_client(url)
        else:
            raise HTTPException(status_code=400, detail="Spoolman URL is not configured")

    if not await client.health_check():
        raise HTTPException(status_code=503, detail="Spoolman is not reachable")

    spools = await client.get_spools()
    return {"spools": spools}


@router.get("/filaments")
async def get_filaments(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FILAMENTS_READ),
):
    """Get all filaments from Spoolman."""
    sm = await get_spoolman_settings(db)
    enabled, url = sm["enabled"], sm["url"]
    if not enabled:
        raise HTTPException(status_code=400, detail="Spoolman integration is not enabled")

    client = await get_spoolman_client()
    if not client:
        if url:
            client = await init_spoolman_client(url)
        else:
            raise HTTPException(status_code=400, detail="Spoolman URL is not configured")

    if not await client.health_check():
        raise HTTPException(status_code=503, detail="Spoolman is not reachable")

    filaments = await client.get_filaments()
    return {"filaments": filaments}


class UnlinkedSpool(BaseModel):
    """A Spoolman spool that is not linked to any AMS tray."""

    id: int
    filament_name: str | None
    filament_vendor: str | None
    filament_material: str | None
    filament_color_hex: str | None
    remaining_weight: float | None
    location: str | None


@router.get("/spools/unlinked", response_model=list[UnlinkedSpool])
async def get_unlinked_spools(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FILAMENTS_READ),
):
    """Get all Spoolman spools that don't have a tag (not linked to AMS)."""
    sm = await get_spoolman_settings(db)
    enabled, url = sm["enabled"], sm["url"]
    if not enabled:
        raise HTTPException(status_code=400, detail="Spoolman integration is not enabled")

    client = await get_spoolman_client()
    if not client:
        if url:
            client = await init_spoolman_client(url)
        else:
            raise HTTPException(status_code=400, detail="Spoolman URL is not configured")

    if not await client.health_check():
        raise HTTPException(status_code=503, detail="Spoolman is not reachable")

    spools = await client.get_spools()
    unlinked = []

    for spool in spools:
        # Check if spool has a tag in extra field
        extra = spool.get("extra", {}) or {}
        tag = extra.get("tag", "")
        # Remove quotes if present (JSON encoded string) and check if empty
        clean_tag = tag.strip('"') if tag else ""
        if not clean_tag:
            filament = spool.get("filament", {}) or {}
            unlinked.append(
                UnlinkedSpool(
                    id=spool["id"],
                    filament_name=filament.get("name"),
                    filament_vendor=(filament.get("vendor") or {}).get("name"),
                    filament_material=filament.get("material"),
                    filament_color_hex=filament.get("color_hex"),
                    remaining_weight=spool.get("remaining_weight"),
                    location=spool.get("location"),
                )
            )

    return unlinked


@router.get("/spools/linked")
async def get_linked_spools(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FILAMENTS_READ),
):
    """Get a map of tag -> spool_id for all Spoolman spools that have a tag assigned."""
    sm = await get_spoolman_settings(db)
    enabled, url = sm["enabled"], sm["url"]
    if not enabled:
        raise HTTPException(status_code=400, detail="Spoolman integration is not enabled")

    client = await get_spoolman_client()
    if not client:
        if url:
            client = await init_spoolman_client(url)
        else:
            raise HTTPException(status_code=400, detail="Spoolman URL is not configured")

    if not await client.health_check():
        raise HTTPException(status_code=503, detail="Spoolman is not reachable")

    spools = await client.get_spools()
    linked: dict[str, dict] = {}

    for spool in spools:
        # Check if spool has a tag in extra field
        extra = spool.get("extra", {}) or {}
        tag = extra.get("tag", "")
        if tag:
            # Remove quotes if present (JSON encoded string)
            clean_tag = tag.strip('"').upper()
            if clean_tag:
                filament = spool.get("filament") or {}
                linked[clean_tag] = {
                    "id": spool["id"],
                    "remaining_weight": spool.get("remaining_weight"),
                    "filament_weight": filament.get("weight"),
                }

    return {"linked": linked}


class LinkSpoolRequest(BaseModel):
    """Request to link a Spoolman spool to an AMS tag (tray_uuid or tag_uid)."""

    spool_tag: str | None = None
    tray_uuid: str | None = None
    tag_uid: str | None = None
    printer_id: int | None = None
    ams_id: int | None = None
    tray_id: int | None = None


@router.post("/spools/{spool_id}/link")
async def link_spool(
    spool_id: int,
    request: LinkSpoolRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FILAMENTS_UPDATE),
):
    """Link a Spoolman spool to an AMS tag by setting Spoolman extra.tag."""
    sm = await get_spoolman_settings(db)
    enabled, url = sm["enabled"], sm["url"]
    if not enabled:
        raise HTTPException(status_code=400, detail="Spoolman integration is not enabled")

    client = await get_spoolman_client()
    if not client:
        if url:
            client = await init_spoolman_client(url)
        else:
            raise HTTPException(status_code=400, detail="Spoolman URL is not configured")

    if not await client.health_check():
        raise HTTPException(status_code=503, detail="Spoolman is not reachable")

    # Resolve and validate spool tag (supports tray_uuid=32 hex and tag_uid=16 hex)
    spool_tag = (request.spool_tag or request.tray_uuid or request.tag_uid or "").strip()
    if not spool_tag:
        raise HTTPException(status_code=400, detail="Missing spool tag (tray_uuid or tag_uid)")
    if len(spool_tag) not in (16, 32):
        raise HTTPException(status_code=400, detail="Invalid spool tag format (must be 16 or 32 hex characters)")
    try:
        int(spool_tag, 16)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid spool tag format (must be hex)")

    if set(spool_tag) == {"0"}:
        raise HTTPException(status_code=400, detail="Invalid spool tag format (all-zero tag is not linkable)")

    spool_tag = spool_tag.upper()

    # Validate printer context when provided, but do NOT write spool.location —
    # that field is user-managed in Spoolman. Slot assignment is stored locally.
    printer_context: tuple[int, int, int] | None = None
    if request.printer_id is not None and request.ams_id is not None and request.tray_id is not None:
        printer_result = await db.execute(select(Printer).where(Printer.id == request.printer_id))
        if not printer_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Printer not found")
        printer_context = (request.printer_id, request.ams_id, request.tray_id)

    try:
        await client.merge_spool_extra(spool_id, {"tag": json.dumps(spool_tag)})
    except SpoolmanNotFoundError:
        raise HTTPException(status_code=404, detail="Spool not found in Spoolman")
    except SpoolmanUnavailableError:
        raise HTTPException(status_code=503, detail="Spoolman is not reachable")

    # Upsert slot assignment locally when printer context was supplied
    if printer_context:
        p_id, a_id, t_id = printer_context
        try:
            await db.execute(
                text(
                    "INSERT INTO spoolman_slot_assignments"
                    " (printer_id, ams_id, tray_id, spoolman_spool_id)"
                    " VALUES (:printer_id, :ams_id, :tray_id, :spool_id)"
                    " ON CONFLICT(printer_id, ams_id, tray_id)"
                    " DO UPDATE SET spoolman_spool_id = excluded.spoolman_spool_id"
                ),
                {"printer_id": p_id, "ams_id": a_id, "tray_id": t_id, "spool_id": spool_id},
            )
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.error(
                "Linked spool %s in Spoolman but failed to persist local slot assignment "
                "(printer=%s ams=%s tray=%s): %s",
                spool_id, p_id, a_id, t_id, e,
            )
            raise HTTPException(
                status_code=500,
                detail=(
                    "Spool linked in Spoolman but the local slot assignment could not be saved. "
                    "Please re-open the link dialog to retry."
                ),
            ) from e

    logger.info("Linked Spoolman spool %s to tag %s", spool_id, spool_tag)
    return {"success": True, "message": f"Spool {spool_id} linked to AMS tag"}


@router.post("/spools/{spool_id}/unlink")
async def unlink_spool(
    spool_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FILAMENTS_UPDATE),
):
    """Unlink a Spoolman spool from AMS by clearing Spoolman extra.tag."""
    sm = await get_spoolman_settings(db)
    enabled, url = sm["enabled"], sm["url"]
    if not enabled:
        raise HTTPException(status_code=400, detail="Spoolman integration is not enabled")

    client = await get_spoolman_client()
    if not client:
        if url:
            client = await init_spoolman_client(url)
        else:
            raise HTTPException(status_code=400, detail="Spoolman URL is not configured")

    if not await client.health_check():
        raise HTTPException(status_code=503, detail="Spoolman is not reachable")

    try:
        await client.merge_spool_extra(spool_id, {"tag": json.dumps("")})
    except SpoolmanNotFoundError:
        raise HTTPException(status_code=404, detail="Spool not found in Spoolman")
    except SpoolmanUnavailableError:
        raise HTTPException(status_code=503, detail="Spoolman is not reachable")

    # Remove local slot assignment for this spool (all slots — a spool can only be in one at a time)
    try:
        await db.execute(
            delete(SpoolmanSlotAssignment).where(SpoolmanSlotAssignment.spoolman_spool_id == spool_id)
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("DB error removing slot assignment for spool %s", spool_id)
        raise HTTPException(status_code=500, detail="Failed to remove local slot assignment")

    logger.info("Unlinked Spoolman spool %s", spool_id)
    return {"success": True, "message": f"Spool {spool_id} unlinked from AMS"}
