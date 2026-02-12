import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.catalog_defaults import DEFAULT_COLOR_CATALOG, DEFAULT_SPOOL_CATALOG
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.color_catalog import ColorCatalogEntry
from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spool_catalog import SpoolCatalogEntry
from backend.app.models.spool_k_profile import SpoolKProfile
from backend.app.models.user import User
from backend.app.schemas.spool import (
    SpoolAssignmentCreate,
    SpoolAssignmentResponse,
    SpoolCreate,
    SpoolKProfileBase,
    SpoolKProfileResponse,
    SpoolResponse,
    SpoolUpdate,
)
from backend.app.schemas.spool_usage import SpoolUsageHistoryResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/inventory", tags=["inventory"])

# Material temperature defaults (nozzle min/max)
MATERIAL_TEMPS: dict[str, tuple[int, int]] = {
    "PLA": (190, 230),
    "PETG": (220, 260),
    "ABS": (240, 270),
    "ASA": (240, 270),
    "TPU": (200, 240),
    "PA": (260, 290),
    "PC": (250, 280),
    "PVA": (190, 210),
    "PLA-CF": (210, 240),
    "PETG-CF": (240, 270),
    "PA-CF": (270, 300),
}

# FilamentColors.xyz API
FILAMENT_COLORS_API = "https://filamentcolors.xyz/api"


# ── Spool Catalog Schemas ──────────────────────────────────────────────────


class CatalogEntryResponse(BaseModel):
    id: int
    name: str
    weight: int
    is_default: bool

    class Config:
        from_attributes = True


class CatalogEntryCreate(BaseModel):
    name: str
    weight: int


class CatalogEntryUpdate(BaseModel):
    name: str
    weight: int


# ── Color Catalog Schemas ──────────────────────────────────────────────────


class ColorEntryResponse(BaseModel):
    id: int
    manufacturer: str
    color_name: str
    hex_color: str
    material: str | None
    is_default: bool

    class Config:
        from_attributes = True


class ColorEntryCreate(BaseModel):
    manufacturer: str
    color_name: str
    hex_color: str
    material: str | None = None


class ColorEntryUpdate(BaseModel):
    manufacturer: str
    color_name: str
    hex_color: str
    material: str | None = None


class ColorLookupResult(BaseModel):
    found: bool
    hex_color: str | None = None
    material: str | None = None


# ── Spool Catalog CRUD ─────────────────────────────────────────────────────


@router.get("/catalog", response_model=list[CatalogEntryResponse])
async def get_spool_catalog(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """Get all spool catalog entries."""
    result = await db.execute(select(SpoolCatalogEntry).order_by(SpoolCatalogEntry.name))
    return list(result.scalars().all())


@router.post("/catalog", response_model=CatalogEntryResponse)
async def add_catalog_entry(
    entry: CatalogEntryCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Add a new spool catalog entry."""
    row = SpoolCatalogEntry(name=entry.name, weight=entry.weight, is_default=False)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.put("/catalog/{entry_id}", response_model=CatalogEntryResponse)
async def update_catalog_entry(
    entry_id: int,
    entry: CatalogEntryUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Update a spool catalog entry."""
    result = await db.execute(select(SpoolCatalogEntry).where(SpoolCatalogEntry.id == entry_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Entry not found")
    row.name = entry.name
    row.weight = entry.weight
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/catalog/{entry_id}")
async def delete_catalog_entry(
    entry_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Delete a spool catalog entry."""
    result = await db.execute(select(SpoolCatalogEntry).where(SpoolCatalogEntry.id == entry_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Entry not found")
    await db.delete(row)
    await db.commit()
    return {"status": "deleted"}


@router.post("/catalog/reset")
async def reset_spool_catalog(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Reset spool catalog to defaults."""
    await db.execute(select(SpoolCatalogEntry))  # ensure table loaded
    # Delete all
    result = await db.execute(select(SpoolCatalogEntry))
    for row in result.scalars().all():
        await db.delete(row)
    # Re-seed defaults
    for name, weight in DEFAULT_SPOOL_CATALOG:
        db.add(SpoolCatalogEntry(name=name, weight=weight, is_default=True))
    await db.commit()
    return {"status": "reset"}


# ── Color Catalog CRUD ─────────────────────────────────────────────────────


@router.get("/colors", response_model=list[ColorEntryResponse])
async def get_color_catalog(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """Get all color catalog entries."""
    result = await db.execute(
        select(ColorCatalogEntry).order_by(
            ColorCatalogEntry.manufacturer, ColorCatalogEntry.material, ColorCatalogEntry.color_name
        )
    )
    return list(result.scalars().all())


@router.post("/colors", response_model=ColorEntryResponse)
async def add_color_entry(
    entry: ColorEntryCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Add a new color catalog entry."""
    row = ColorCatalogEntry(
        manufacturer=entry.manufacturer,
        color_name=entry.color_name,
        hex_color=entry.hex_color,
        material=entry.material,
        is_default=False,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.put("/colors/{entry_id}", response_model=ColorEntryResponse)
async def update_color_entry(
    entry_id: int,
    entry: ColorEntryUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Update a color catalog entry."""
    result = await db.execute(select(ColorCatalogEntry).where(ColorCatalogEntry.id == entry_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Entry not found")
    row.manufacturer = entry.manufacturer
    row.color_name = entry.color_name
    row.hex_color = entry.hex_color
    row.material = entry.material
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/colors/{entry_id}")
async def delete_color_entry(
    entry_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Delete a color catalog entry."""
    result = await db.execute(select(ColorCatalogEntry).where(ColorCatalogEntry.id == entry_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Entry not found")
    await db.delete(row)
    await db.commit()
    return {"status": "deleted"}


@router.post("/colors/reset")
async def reset_color_catalog(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Reset color catalog to defaults."""
    result = await db.execute(select(ColorCatalogEntry))
    for row in result.scalars().all():
        await db.delete(row)
    for manufacturer, color_name, hex_color, material in DEFAULT_COLOR_CATALOG:
        db.add(
            ColorCatalogEntry(
                manufacturer=manufacturer,
                color_name=color_name,
                hex_color=hex_color,
                material=material,
                is_default=True,
            )
        )
    await db.commit()
    return {"status": "reset"}


@router.get("/colors/lookup", response_model=ColorLookupResult)
async def lookup_color(
    manufacturer: str,
    color_name: str,
    material: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """Look up a color by manufacturer and color name."""
    query = select(ColorCatalogEntry).where(
        ColorCatalogEntry.manufacturer == manufacturer,
        ColorCatalogEntry.color_name == color_name,
    )
    if material:
        query = query.where(ColorCatalogEntry.material == material)
    query = query.limit(1)
    result = await db.execute(query)
    row = result.scalar_one_or_none()
    if row:
        return ColorLookupResult(found=True, hex_color=row.hex_color, material=row.material)
    return ColorLookupResult(found=False)


@router.get("/colors/search", response_model=list[ColorEntryResponse])
async def search_colors(
    manufacturer: str | None = None,
    material: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """Search colors by manufacturer and/or material."""
    query = select(ColorCatalogEntry)
    if manufacturer:
        query = query.where(func.lower(ColorCatalogEntry.manufacturer).contains(manufacturer.lower()))
    if material:
        query = query.where(func.lower(ColorCatalogEntry.material).contains(material.lower()))
    query = query.order_by(ColorCatalogEntry.manufacturer, ColorCatalogEntry.color_name).limit(100)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.post("/colors/sync")
async def sync_from_filamentcolors(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Sync colors from FilamentColors.xyz API with progress streaming."""

    async def generate():
        from backend.app.core.database import async_session

        added = 0
        skipped = 0
        total_fetched = 0
        total_available = 0

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                page = 1
                while True:
                    response = await client.get(
                        f"{FILAMENT_COLORS_API}/swatch/",
                        params={"page": page},
                    )
                    response.raise_for_status()
                    data = response.json()
                    total_available = data.get("count", total_available)
                    results = data.get("results", [])
                    if not results:
                        break

                    async with async_session() as db:
                        for swatch in results:
                            total_fetched += 1
                            manufacturer_data = swatch.get("manufacturer")
                            manufacturer_name = (
                                manufacturer_data.get("name", "") if isinstance(manufacturer_data, dict) else ""
                            )
                            filament_type_data = swatch.get("filament_type")
                            mat = filament_type_data.get("name", "") if isinstance(filament_type_data, dict) else None
                            color_name_val = swatch.get("color_name", "")
                            hex_color_val = swatch.get("hex_color", "")

                            if not manufacturer_name or not color_name_val or not hex_color_val:
                                skipped += 1
                                continue

                            if not hex_color_val.startswith("#"):
                                hex_color_val = f"#{hex_color_val}"

                            # Check if entry already exists
                            existing = await db.execute(
                                select(ColorCatalogEntry)
                                .where(
                                    ColorCatalogEntry.manufacturer == manufacturer_name,
                                    ColorCatalogEntry.color_name == color_name_val,
                                    ColorCatalogEntry.material == mat,
                                )
                                .limit(1)
                            )
                            if existing.scalar_one_or_none():
                                skipped += 1
                            else:
                                db.add(
                                    ColorCatalogEntry(
                                        manufacturer=manufacturer_name,
                                        color_name=color_name_val,
                                        hex_color=hex_color_val.upper(),
                                        material=mat,
                                        is_default=False,
                                    )
                                )
                                added += 1

                        await db.commit()

                    progress = {
                        "type": "progress",
                        "added": added,
                        "skipped": skipped,
                        "total_fetched": total_fetched,
                        "total_available": total_available,
                    }
                    yield f"data: {json.dumps(progress)}\n\n"

                    if not data.get("next") or total_fetched >= total_available:
                        break
                    page += 1

            result = {
                "type": "complete",
                "added": added,
                "skipped": skipped,
                "total_fetched": total_fetched,
                "total_available": total_available,
            }
            yield f"data: {json.dumps(result)}\n\n"

        except httpx.HTTPError as e:
            logger.error("HTTP error syncing from FilamentColors.xyz: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
        except Exception as e:
            logger.error("Error syncing from FilamentColors.xyz: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'error': 'Unexpected error during sync'})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Spool CRUD ───────────────────────────────────────────────────────────────


@router.get("/spools", response_model=list[SpoolResponse])
async def list_spools(
    include_archived: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """List all spools, excluding archived by default."""
    query = select(Spool).options(selectinload(Spool.k_profiles))
    if not include_archived:
        query = query.where(Spool.archived_at.is_(None))
    query = query.order_by(Spool.material, Spool.brand, Spool.color_name)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/spools/{spool_id}", response_model=SpoolResponse)
async def get_spool(
    spool_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """Get a single spool with k_profiles."""
    result = await db.execute(select(Spool).options(selectinload(Spool.k_profiles)).where(Spool.id == spool_id))
    spool = result.scalar_one_or_none()
    if not spool:
        raise HTTPException(404, "Spool not found")
    return spool


@router.post("/spools", response_model=SpoolResponse)
async def create_spool(
    spool_data: SpoolCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Create a new spool."""
    spool = Spool(**spool_data.model_dump())
    db.add(spool)
    await db.commit()
    await db.refresh(spool)
    result = await db.execute(select(Spool).options(selectinload(Spool.k_profiles)).where(Spool.id == spool.id))
    return result.scalar_one()


@router.patch("/spools/{spool_id}", response_model=SpoolResponse)
async def update_spool(
    spool_id: int,
    spool_data: SpoolUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Update a spool."""
    result = await db.execute(select(Spool).where(Spool.id == spool_id))
    spool = result.scalar_one_or_none()
    if not spool:
        raise HTTPException(404, "Spool not found")

    for field, value in spool_data.model_dump(exclude_unset=True).items():
        setattr(spool, field, value)

    await db.commit()
    result = await db.execute(select(Spool).options(selectinload(Spool.k_profiles)).where(Spool.id == spool_id))
    return result.scalar_one()


@router.delete("/spools/{spool_id}")
async def delete_spool(
    spool_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Hard delete a spool."""
    result = await db.execute(select(Spool).where(Spool.id == spool_id))
    spool = result.scalar_one_or_none()
    if not spool:
        raise HTTPException(404, "Spool not found")

    await db.delete(spool)
    await db.commit()
    return {"status": "deleted"}


@router.post("/spools/{spool_id}/archive", response_model=SpoolResponse)
async def archive_spool(
    spool_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Soft-delete a spool by setting archived_at."""
    from datetime import datetime, timezone

    result = await db.execute(select(Spool).where(Spool.id == spool_id))
    spool = result.scalar_one_or_none()
    if not spool:
        raise HTTPException(404, "Spool not found")

    spool.archived_at = datetime.now(timezone.utc)
    await db.commit()
    result = await db.execute(select(Spool).options(selectinload(Spool.k_profiles)).where(Spool.id == spool_id))
    return result.scalar_one()


@router.post("/spools/{spool_id}/restore", response_model=SpoolResponse)
async def restore_spool(
    spool_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Restore an archived spool."""
    result = await db.execute(select(Spool).where(Spool.id == spool_id))
    spool = result.scalar_one_or_none()
    if not spool:
        raise HTTPException(404, "Spool not found")

    spool.archived_at = None
    await db.commit()
    result = await db.execute(select(Spool).options(selectinload(Spool.k_profiles)).where(Spool.id == spool_id))
    return result.scalar_one()


# ── K-Profiles ───────────────────────────────────────────────────────────────


@router.get("/spools/{spool_id}/k-profiles", response_model=list[SpoolKProfileResponse])
async def list_k_profiles(
    spool_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """List K-profiles for a spool."""
    result = await db.execute(select(SpoolKProfile).where(SpoolKProfile.spool_id == spool_id))
    return list(result.scalars().all())


@router.put("/spools/{spool_id}/k-profiles", response_model=list[SpoolKProfileResponse])
async def replace_k_profiles(
    spool_id: int,
    profiles: list[SpoolKProfileBase],
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Replace all K-profiles for a spool (batch save)."""
    # Verify spool exists
    result = await db.execute(select(Spool).where(Spool.id == spool_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Spool not found")

    # Delete existing
    existing = await db.execute(select(SpoolKProfile).where(SpoolKProfile.spool_id == spool_id))
    for old in existing.scalars().all():
        await db.delete(old)

    # Create new
    new_profiles = []
    for p in profiles:
        kp = SpoolKProfile(spool_id=spool_id, **p.model_dump())
        db.add(kp)
        new_profiles.append(kp)

    await db.commit()
    for kp in new_profiles:
        await db.refresh(kp)
    return new_profiles


# ── Spool Assignments ────────────────────────────────────────────────────────


@router.get("/assignments", response_model=list[SpoolAssignmentResponse])
async def list_assignments(
    printer_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """List spool assignments, optionally filtered by printer."""
    query = select(SpoolAssignment).options(selectinload(SpoolAssignment.spool).selectinload(Spool.k_profiles))
    if printer_id is not None:
        query = query.where(SpoolAssignment.printer_id == printer_id)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.post("/assignments", response_model=SpoolAssignmentResponse)
async def assign_spool(
    data: SpoolAssignmentCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Assign a spool to an AMS slot and auto-configure via MQTT."""
    from backend.app.services.printer_manager import printer_manager

    # 1. Validate spool exists and is not archived
    result = await db.execute(select(Spool).options(selectinload(Spool.k_profiles)).where(Spool.id == data.spool_id))
    spool = result.scalar_one_or_none()
    if not spool:
        raise HTTPException(404, "Spool not found")
    if spool.archived_at:
        raise HTTPException(400, "Cannot assign an archived spool")

    # 2. Get current AMS tray state for fingerprint
    fingerprint_color = None
    fingerprint_type = None
    state = printer_manager.get_status(data.printer_id)
    if state and state.raw_data:
        tray = _find_tray_in_ams_data(
            state.raw_data.get("ams", {}).get("ams", []),
            data.ams_id,
            data.tray_id,
        )
        if tray:
            fingerprint_color = tray.get("tray_color", "")
            fingerprint_type = tray.get("tray_type", "")

    # 3. Upsert assignment (replace if same printer+ams+tray)
    existing = await db.execute(
        select(SpoolAssignment).where(
            SpoolAssignment.printer_id == data.printer_id,
            SpoolAssignment.ams_id == data.ams_id,
            SpoolAssignment.tray_id == data.tray_id,
        )
    )
    old = existing.scalar_one_or_none()
    if old:
        await db.delete(old)
        await db.flush()

    assignment = SpoolAssignment(
        spool_id=data.spool_id,
        printer_id=data.printer_id,
        ams_id=data.ams_id,
        tray_id=data.tray_id,
        fingerprint_color=fingerprint_color,
        fingerprint_type=fingerprint_type,
    )
    db.add(assignment)
    await db.commit()
    await db.refresh(assignment)

    # 4. Auto-configure AMS slot via MQTT
    configured = False
    try:
        client = printer_manager.get_client(data.printer_id)
        if client:
            # Build filament setting from spool data
            tray_type = spool.material
            tray_sub_brands = f"{spool.material} {spool.subtype}" if spool.subtype else spool.material
            tray_color = spool.rgba or "FFFFFFFF"
            tray_info_idx = spool.slicer_filament or ""
            setting_id = ""

            # Temperature: use spool overrides if set, else material defaults
            temp_min, temp_max = MATERIAL_TEMPS.get(spool.material.upper(), (200, 240))
            if spool.nozzle_temp_min is not None:
                temp_min = spool.nozzle_temp_min
            if spool.nozzle_temp_max is not None:
                temp_max = spool.nozzle_temp_max

            # a. Set filament setting
            client.ams_set_filament_setting(
                ams_id=data.ams_id,
                tray_id=data.tray_id,
                tray_info_idx=tray_info_idx,
                tray_type=tray_type,
                tray_sub_brands=tray_sub_brands,
                tray_color=tray_color,
                nozzle_temp_min=temp_min,
                nozzle_temp_max=temp_max,
                setting_id=setting_id,
            )

            # b. Look up K-profile for this spool + printer + nozzle
            nozzle_diameter = "0.4"
            if state and state.nozzles:
                nd = state.nozzles[0].nozzle_diameter
                if nd:
                    nozzle_diameter = nd

            matching_kp = None
            for kp in spool.k_profiles:
                if kp.printer_id == data.printer_id and kp.nozzle_diameter == nozzle_diameter:
                    matching_kp = kp
                    break

            if matching_kp and matching_kp.cali_idx is not None:
                client.extrusion_cali_sel(
                    ams_id=data.ams_id,
                    tray_id=data.tray_id,
                    cali_idx=matching_kp.cali_idx,
                    filament_id=tray_info_idx,
                    nozzle_diameter=nozzle_diameter,
                    setting_id=matching_kp.setting_id,
                )

            configured = True
            logger.info(
                "Auto-configured AMS slot ams=%d tray=%d for spool %d on printer %d",
                data.ams_id,
                data.tray_id,
                spool.id,
                data.printer_id,
            )
    except Exception as e:
        logger.warning("MQTT auto-configure failed for spool %d: %s", spool.id, e)

    # Return assignment with spool data
    result = await db.execute(
        select(SpoolAssignment)
        .options(selectinload(SpoolAssignment.spool).selectinload(Spool.k_profiles))
        .where(SpoolAssignment.id == assignment.id)
    )
    resp = result.scalar_one()
    response = SpoolAssignmentResponse.model_validate(resp)
    response.configured = configured
    return response


@router.delete("/assignments/{printer_id}/{ams_id}/{tray_id}")
async def unassign_spool(
    printer_id: int,
    ams_id: int,
    tray_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Unassign a spool from an AMS slot."""
    result = await db.execute(
        select(SpoolAssignment).where(
            SpoolAssignment.printer_id == printer_id,
            SpoolAssignment.ams_id == ams_id,
            SpoolAssignment.tray_id == tray_id,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(404, "Assignment not found")

    await db.delete(assignment)
    await db.commit()
    return {"status": "deleted"}


# ── Tag Linking ───────────────────────────────────────────────────────────────


class LinkTagRequest(BaseModel):
    tag_uid: str | None = None
    tray_uuid: str | None = None
    tag_type: str | None = None
    data_origin: str | None = "nfc_link"


@router.patch("/spools/{spool_id}/link-tag", response_model=SpoolResponse)
async def link_tag_to_spool(
    spool_id: int,
    data: LinkTagRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Link an RFID tag_uid/tray_uuid to an existing spool."""
    result = await db.execute(select(Spool).options(selectinload(Spool.k_profiles)).where(Spool.id == spool_id))
    spool = result.scalar_one_or_none()
    if not spool:
        raise HTTPException(404, "Spool not found")
    if spool.archived_at:
        raise HTTPException(400, "Cannot link tag to archived spool")

    # Check for conflicts: tag already linked to another active spool
    if data.tag_uid:
        conflict = await db.execute(
            select(Spool).where(
                Spool.tag_uid == data.tag_uid,
                Spool.id != spool_id,
                Spool.archived_at.is_(None),
            )
        )
        if conflict.scalar_one_or_none():
            raise HTTPException(409, "Tag UID already linked to another active spool")
        # Auto-clear from archived spools (tag recycling)
        archived_with_tag = await db.execute(
            select(Spool).where(
                Spool.tag_uid == data.tag_uid,
                Spool.id != spool_id,
                Spool.archived_at.is_not(None),
            )
        )
        for old_spool in archived_with_tag.scalars().all():
            old_spool.tag_uid = None

    if data.tray_uuid:
        conflict = await db.execute(
            select(Spool).where(
                Spool.tray_uuid == data.tray_uuid,
                Spool.id != spool_id,
                Spool.archived_at.is_(None),
            )
        )
        if conflict.scalar_one_or_none():
            raise HTTPException(409, "Tray UUID already linked to another active spool")
        archived_with_uuid = await db.execute(
            select(Spool).where(
                Spool.tray_uuid == data.tray_uuid,
                Spool.id != spool_id,
                Spool.archived_at.is_not(None),
            )
        )
        for old_spool in archived_with_uuid.scalars().all():
            old_spool.tray_uuid = None

    if data.tag_uid is not None:
        spool.tag_uid = data.tag_uid
    if data.tray_uuid is not None:
        spool.tray_uuid = data.tray_uuid
    if data.tag_type is not None:
        spool.tag_type = data.tag_type
    if data.data_origin is not None:
        spool.data_origin = data.data_origin

    await db.commit()
    result = await db.execute(select(Spool).options(selectinload(Spool.k_profiles)).where(Spool.id == spool_id))
    return result.scalar_one()


# ── Usage History ─────────────────────────────────────────────────────────────


@router.get("/spools/{spool_id}/usage", response_model=list[SpoolUsageHistoryResponse])
async def get_spool_usage_history(
    spool_id: int,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """Get usage history for a specific spool."""
    from backend.app.models.spool_usage_history import SpoolUsageHistory

    # Verify spool exists
    spool_result = await db.execute(select(Spool).where(Spool.id == spool_id))
    if not spool_result.scalar_one_or_none():
        raise HTTPException(404, "Spool not found")

    result = await db.execute(
        select(SpoolUsageHistory)
        .where(SpoolUsageHistory.spool_id == spool_id)
        .order_by(SpoolUsageHistory.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


@router.get("/usage", response_model=list[SpoolUsageHistoryResponse])
async def get_all_usage_history(
    limit: int = 100,
    printer_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """Get global usage history, optionally filtered by printer."""
    from backend.app.models.spool_usage_history import SpoolUsageHistory

    query = select(SpoolUsageHistory).order_by(SpoolUsageHistory.created_at.desc()).limit(limit)
    if printer_id is not None:
        query = query.where(SpoolUsageHistory.printer_id == printer_id)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.delete("/spools/{spool_id}/usage")
async def clear_spool_usage_history(
    spool_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Clear usage history for a spool."""
    from backend.app.models.spool_usage_history import SpoolUsageHistory

    result = await db.execute(select(SpoolUsageHistory).where(SpoolUsageHistory.spool_id == spool_id))
    for row in result.scalars().all():
        await db.delete(row)
    await db.commit()
    return {"status": "cleared"}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _find_tray_in_ams_data(ams_data: list, ams_id: int, tray_id: int) -> dict | None:
    """Find a specific tray in the AMS data structure."""
    if not ams_data:
        return None
    for ams_unit in ams_data:
        if int(ams_unit.get("id", -1)) != ams_id:
            continue
        for tray in ams_unit.get("tray", []):
            if int(tray.get("id", -1)) == tray_id:
                return tray
    return None
