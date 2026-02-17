"""RFID tag matching and auto-assignment for spool inventory."""

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment

logger = logging.getLogger(__name__)

# Zero-value constants for tag validation
ZERO_TAG_UID = "0000000000000000"
ZERO_TRAY_UUID = "00000000000000000000000000000000"


def is_valid_tag(tag_uid: str, tray_uuid: str) -> bool:
    """Check if a tag/UUID pair contains a non-zero, non-empty value."""
    uid_valid = bool(tag_uid) and tag_uid != ZERO_TAG_UID and tag_uid != "0" * len(tag_uid)
    uuid_valid = bool(tray_uuid) and tray_uuid != ZERO_TRAY_UUID and tray_uuid != "0" * len(tray_uuid)
    return uid_valid or uuid_valid


def is_bambu_tag(tag_uid: str, tray_uuid: str, tray_info_idx: str) -> bool:
    """Check if an AMS tray contains a Bambu Lab RFID spool (has valid UUID or slicer preset)."""
    uuid_valid = bool(tray_uuid) and tray_uuid != ZERO_TRAY_UUID and tray_uuid != "0" * len(tray_uuid)
    has_preset = bool(tray_info_idx)
    return uuid_valid or (is_valid_tag(tag_uid, tray_uuid) and has_preset)


async def create_spool_from_tray(db: AsyncSession, tray_data: dict) -> Spool:
    """Create a new Spool inventory entry from AMS tray MQTT data.

    Extracts material, subtype, color, temps, and tag info from the tray dict.
    Looks up core_weight from the spool catalog if a Bambu Lab entry matches.
    """
    from backend.app.models.color_catalog import ColorCatalogEntry
    from backend.app.models.spool_catalog import SpoolCatalogEntry

    tray_type = tray_data.get("tray_type", "")  # "PLA"
    tray_sub_brands = tray_data.get("tray_sub_brands", "")  # "PLA Basic"
    tray_color = tray_data.get("tray_color", "FFFFFFFF")  # RRGGBBAA
    tray_id_name = tray_data.get("tray_id_name", "")  # Color name e.g. "Jade White"
    tag_uid = tray_data.get("tag_uid", "")
    tray_uuid = tray_data.get("tray_uuid", "")
    tray_info_idx = tray_data.get("tray_info_idx", "")
    nozzle_min = tray_data.get("nozzle_temp_min", 0)
    nozzle_max = tray_data.get("nozzle_temp_max", 0)
    label_weight = int(tray_data.get("tray_weight", 1000))

    # Parse material and subtype from tray_sub_brands ("PLA Basic" → material="PLA", subtype="Basic")
    material = tray_type or "PLA"
    subtype = None
    if tray_sub_brands and " " in tray_sub_brands:
        parts = tray_sub_brands.split(" ", 1)
        if parts[0].upper() == material.upper():
            subtype = parts[1]
        else:
            # tray_sub_brands is the full material name (e.g. "PETG-HF")
            material = tray_sub_brands
    elif tray_sub_brands and tray_sub_brands.upper() != material.upper():
        material = tray_sub_brands

    # Resolve color name from tray_id_name code, hex catalog, or raw tray_id_name
    from backend.app.core.bambu_colors import resolve_bambu_color_name

    rgba = tray_color if tray_color else None
    color_name = None

    # 1. Try Bambu color code mapping (e.g. "A06-D0" → "Titan Gray")
    if tray_id_name:
        color_name = resolve_bambu_color_name(tray_id_name)
        logger.info("Color resolve: tray_id_name=%r → resolved=%r", tray_id_name, color_name)
        # If not a known code, use tray_id_name directly (it may be a readable name)
        if not color_name and "-" not in tray_id_name:
            color_name = tray_id_name
    else:
        logger.info("Color resolve: tray_id_name is empty, rgba=%r", rgba)

    # 2. Try color catalog lookup by hex color
    if not color_name and rgba and len(rgba) >= 6:
        hex_prefix = f"#{rgba[:6].upper()}"
        cat_result = await db.execute(
            select(ColorCatalogEntry)
            .where(func.upper(ColorCatalogEntry.hex_color) == hex_prefix)
            .where(func.upper(ColorCatalogEntry.manufacturer) == "BAMBU LAB")
            .limit(1)
        )
        entry = cat_result.scalar_one_or_none()
        if entry:
            color_name = entry.color_name

    # Look up core weight from spool catalog
    core_weight = 250  # Default for Bambu Lab plastic spools
    cat_result = await db.execute(select(SpoolCatalogEntry).where(SpoolCatalogEntry.name.ilike("Bambu Lab%")).limit(10))
    for entry in cat_result.scalars().all():
        # Pick the best match (prefer exact, fallback to first Bambu Lab entry)
        core_weight = entry.weight
        break

    # Resolve slicer filament name from builtin table
    slicer_filament_name = None
    if tray_info_idx:
        try:
            from backend.app.api.routes.cloud import _BUILTIN_FILAMENT_NAMES

            slicer_filament_name = _BUILTIN_FILAMENT_NAMES.get(tray_info_idx)
        except Exception:
            pass
        # Fallback: use tray_sub_brands as the display name
        if not slicer_filament_name and tray_sub_brands:
            slicer_filament_name = tray_sub_brands

    # Calculate initial weight_used from AMS remain percentage
    remain_raw = tray_data.get("remain")
    try:
        remain_pct = int(remain_raw) if remain_raw is not None else 100
    except (TypeError, ValueError):
        remain_pct = 100
    # Clamp to valid range: negative means unknown, >100 is invalid
    if remain_pct < 0 or remain_pct > 100:
        remain_pct = 100  # Unknown → assume full
    weight_used = round(label_weight * (100 - remain_pct) / 100.0, 1)

    spool = Spool(
        material=material,
        subtype=subtype,
        color_name=color_name,
        rgba=rgba,
        brand="Bambu Lab",
        label_weight=label_weight,
        core_weight=core_weight,
        weight_used=weight_used,
        slicer_filament=tray_info_idx or None,
        slicer_filament_name=slicer_filament_name,
        nozzle_temp_min=int(nozzle_min) if nozzle_min else None,
        nozzle_temp_max=int(nozzle_max) if nozzle_max else None,
        tag_uid=tag_uid if tag_uid and tag_uid != ZERO_TAG_UID else None,
        tray_uuid=tray_uuid if tray_uuid and tray_uuid != ZERO_TRAY_UUID else None,
        data_origin="rfid_auto",
        tag_type="bambulab",
    )
    db.add(spool)
    await db.flush()

    logger.info(
        "Auto-created spool %d from AMS tray data: %s %s %s (tag=%s uuid=%s)",
        spool.id,
        material,
        subtype or "",
        color_name or "",
        tag_uid,
        tray_uuid,
    )
    return spool


async def get_spool_by_tag(db: AsyncSession, tag_uid: str, tray_uuid: str) -> Spool | None:
    """Look up an active spool by RFID tag UID or Bambu Lab tray UUID.

    Prefers tray_uuid match over tag_uid (more reliable).
    """
    # Try tray_uuid first (Bambu Lab spools — more reliable)
    if tray_uuid and tray_uuid != ZERO_TRAY_UUID and tray_uuid != "0" * len(tray_uuid):
        result = await db.execute(
            select(Spool)
            .options(selectinload(Spool.k_profiles))
            .where(Spool.tray_uuid == tray_uuid, Spool.archived_at.is_(None))
            .limit(1)
        )
        spool = result.scalar_one_or_none()
        if spool:
            return spool

    # Fall back to tag_uid
    if tag_uid and tag_uid != ZERO_TAG_UID and tag_uid != "0" * len(tag_uid):
        result = await db.execute(
            select(Spool)
            .options(selectinload(Spool.k_profiles))
            .where(Spool.tag_uid == tag_uid, Spool.archived_at.is_(None))
            .limit(1)
        )
        spool = result.scalar_one_or_none()
        if spool:
            return spool

    return None


async def auto_assign_spool(
    printer_id: int,
    ams_id: int,
    tray_id: int,
    spool: Spool,
    printer_manager,
    db: AsyncSession,
    tray_info_idx: str = "",
) -> SpoolAssignment:
    """Create a SpoolAssignment and auto-configure the AMS slot via MQTT.

    For BL spools (RFID-detected), only K-profile commands are sent.
    ams_set_filament_setting is NOT sent because the firmware already has
    filament configuration from the RFID tag, and sending it would destroy
    the RFID-detected state (eye → pen icon in BambuStudio).
    """
    # Get current tray state for fingerprint
    fingerprint_color = None
    fingerprint_type = None
    tray = None
    state = printer_manager.get_status(printer_id)
    if state and state.raw_data:
        from backend.app.api.routes.inventory import _find_tray_in_ams_data

        ams = state.raw_data.get("ams", [])
        if isinstance(ams, dict):
            ams = ams.get("ams", [])
        tray = _find_tray_in_ams_data(
            ams,
            ams_id,
            tray_id,
        )
        if tray:
            fingerprint_color = tray.get("tray_color", "")
            fingerprint_type = tray.get("tray_type", "")

    # Upsert: remove old assignment for this slot
    existing = await db.execute(
        select(SpoolAssignment).where(
            SpoolAssignment.printer_id == printer_id,
            SpoolAssignment.ams_id == ams_id,
            SpoolAssignment.tray_id == tray_id,
        )
    )
    old = existing.scalar_one_or_none()
    if old:
        await db.delete(old)
        await db.flush()

    assignment = SpoolAssignment(
        spool_id=spool.id,
        printer_id=printer_id,
        ams_id=ams_id,
        tray_id=tray_id,
        fingerprint_color=fingerprint_color,
        fingerprint_type=fingerprint_type,
    )
    db.add(assignment)
    await db.flush()

    # Apply K-profile via MQTT (if available)
    # NOTE: Do NOT send ams_set_filament_setting here. This function is only
    # called for BL spools (RFID-detected). The firmware already has the filament
    # configuration from the RFID tag. Sending ams_set_filament_setting would
    # destroy the RFID-detected state (eye → pen icon in BambuStudio/OrcaSlicer).
    try:
        client = printer_manager.get_client(printer_id)
        if client:
            # Apply K-profile if available
            nozzle_diameter = "0.4"
            if state and state.nozzles:
                nd = state.nozzles[0].nozzle_diameter
                if nd:
                    nozzle_diameter = nd

            matching_kp = None
            for kp in spool.k_profiles:
                if kp.printer_id == printer_id and kp.nozzle_diameter == nozzle_diameter:
                    matching_kp = kp
                    break

            if matching_kp and matching_kp.cali_idx is not None:
                # The filament_id in extrusion_cali_sel must match the filament preset
                # under which the K-profile was calibrated. Use spool.slicer_filament
                # (the preset assigned in inventory), falling back to tray's RFID value.
                cali_filament_id = spool.slicer_filament or tray_info_idx or ""
                client.extrusion_cali_sel(
                    ams_id=ams_id,
                    tray_id=tray_id,
                    cali_idx=matching_kp.cali_idx,
                    filament_id=cali_filament_id,
                    nozzle_diameter=nozzle_diameter,
                )

                # NOTE: Do NOT send extrusion_cali_set here. extrusion_cali_sel already
                # selected the correct profile by cali_idx. Sending extrusion_cali_set
                # with the same cali_idx would MODIFY the existing profile's metadata
                # (extruder_id, nozzle_id, name), corrupting it.

                logger.info(
                    "Applied K-profile cali_idx=%d for spool %d on printer %d AMS%d-T%d",
                    matching_kp.cali_idx,
                    spool.id,
                    printer_id,
                    ams_id,
                    tray_id,
                )

            logger.info(
                "Auto-assigned spool %d to printer %d AMS%d-T%d (RFID match)",
                spool.id,
                printer_id,
                ams_id,
                tray_id,
            )
    except Exception as e:
        logger.warning("K-profile apply failed for spool %d (RFID match): %s", spool.id, e)

    return assignment
