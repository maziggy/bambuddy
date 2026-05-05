import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.auth import RequirePermissionIfAuthEnabled, require_auth_if_enabled
from backend.app.core.catalog_defaults import DEFAULT_COLOR_CATALOG, DEFAULT_SPOOL_CATALOG
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.core.websocket import ws_manager
from backend.app.models.ams_label import AmsLabel
from backend.app.models.color_catalog import ColorCatalogEntry
from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spool_catalog import SpoolCatalogEntry
from backend.app.models.spool_k_profile import SpoolKProfile
from backend.app.models.user import User
from backend.app.schemas.spool import (
    SpoolAssignmentCreate,
    SpoolAssignmentResponse,
    SpoolBulkCreate,
    SpoolCreate,
    SpoolKProfileBase,
    SpoolKProfileResponse,
    SpoolResponse,
    SpoolUpdate,
    normalize_effect_type,
    normalize_extra_colors,
)
from backend.app.schemas.spool_usage import SpoolUsageHistoryResponse
from backend.app.utils.filament_ids import filament_id_to_setting_id, normalize_slicer_filament
from backend.app.utils.tag_normalization import normalize_tag_uid, normalize_tray_uuid

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

# Generic Bambu filament IDs by material — fallback when no specific
# preset is resolvable. Keep aligned with the inline table in
# apply_spool_to_slot_via_mqtt below; both paths must produce the same
# value for a given material.
_GENERIC_FILAMENT_IDS: dict[str, str] = {
    "PLA": "GFL99",
    "PETG": "GFG99",
    "ABS": "GFB99",
    "ASA": "GFB98",
    "PC": "GFC99",
    "PA": "GFN99",
    "NYLON": "GFN99",
    "TPU": "GFU99",
    "PVA": "GFS99",
    "HIPS": "GFS98",
    "PLA-CF": "GFL98",
    "PETG-CF": "GFG98",
    "PA-CF": "GFN98",
    "PETG HF": "GFG96",
}


async def apply_spool_to_slot_via_mqtt(
    *,
    db: AsyncSession,
    current_user: User | None,
    spool: Spool,
    printer_id: int,
    ams_id: int,
    tray_id: int,
    current_tray_info_idx: str = "",
    current_tray_type: str = "",
) -> bool:
    """Publish ams_filament_setting + extrusion_cali_sel for a spool on a slot.

    Shared by `assign_spool` (initial assign for a loaded slot) and
    `on_ams_change` (re-fire when a SpoolBuddy-pre-assigned slot transitions
    empty → loaded). Returns True when MQTT commands were published, False if
    no client was available or setup failed mid-way.

    `current_tray_info_idx` / `current_tray_type` describe the live tray state
    used as fallback hints when the spool's slicer_filament can't be resolved.
    Caller should not pass these for the empty-slot re-fire path (they'll be
    the freshly-loaded values, which is the intended fallback).
    """
    from backend.app.services.printer_manager import printer_manager

    client = printer_manager.get_client(printer_id)
    if client is None:
        return False

    state = printer_manager.get_status(printer_id)

    tray_type = spool.material
    tray_sub_brands = (
        f"{spool.brand} {spool.material} {spool.subtype}".strip()
        if spool.brand
        else f"{spool.material} {spool.subtype}"
        if spool.subtype
        else spool.material
    )
    tray_color = spool.rgba or "FFFFFFFF"

    _generic_id_values = set(_GENERIC_FILAMENT_IDS.values())

    tray_info_idx = ""
    setting_id = ""
    sf = spool.slicer_filament or ""

    if sf:
        base_sf = sf.split("_")[0] if "_" in sf else sf
        if base_sf.startswith("GFS") or base_sf.startswith("PFUS"):
            setting_id = base_sf
            try:
                from backend.app.api.routes.cloud import build_authenticated_cloud

                cloud = await build_authenticated_cloud(db, current_user)
                if cloud is not None and cloud.is_authenticated:
                    try:
                        detail = await cloud.get_setting_detail(base_sf)
                        if detail.get("filament_id"):
                            tray_info_idx = detail["filament_id"]
                            cloud_name = detail.get("name", "")
                            if cloud_name:
                                tray_sub_brands = cloud_name.replace(r"@.*$", "").split("@")[0].strip()
                        elif detail.get("base_id"):
                            bid = detail["base_id"].split("_")[0]
                            if bid.startswith("GFS") and len(bid) >= 5:
                                tray_info_idx = f"GF{bid[3:]}"
                            else:
                                tray_info_idx = bid
                    finally:
                        await cloud.close()
                elif cloud is not None:
                    await cloud.close()
            except Exception as e:
                logger.warning("Spool assign: cloud lookup failed for %r: %s", sf, e)

            if not tray_info_idx:
                tray_info_idx, setting_id = normalize_slicer_filament(sf)
        elif base_sf.startswith("GF"):
            tray_info_idx, setting_id = normalize_slicer_filament(sf)
        else:
            try:
                local_id = int(sf)
                from backend.app.models.local_preset import LocalPreset as LP

                lp_result = await db.execute(select(LP).where(LP.id == local_id, LP.preset_type == "filament"))
                lp = lp_result.scalar_one_or_none()
                if lp:
                    mat = (spool.material or lp.filament_type or "").upper().strip()
                    tray_info_idx = (
                        _GENERIC_FILAMENT_IDS.get(mat)
                        or _GENERIC_FILAMENT_IDS.get(mat.split("-")[0].split(" ")[0])
                        or ""
                    )
                    if lp.name:
                        tray_sub_brands = lp.name.split("@")[0].strip()
            except (ValueError, TypeError):
                tray_info_idx, setting_id = normalize_slicer_filament(sf)

    if tray_info_idx and spool.slicer_filament_name:
        from backend.app.api.routes.cloud import _BUILTIN_FILAMENT_NAMES

        expected_name = _BUILTIN_FILAMENT_NAMES.get(tray_info_idx, "")
        if expected_name and expected_name != spool.slicer_filament_name:
            for fid, fname in _BUILTIN_FILAMENT_NAMES.items():
                if fname == spool.slicer_filament_name:
                    tray_info_idx = fid
                    setting_id = filament_id_to_setting_id(fid)
                    break

    if not tray_info_idx:
        if (
            current_tray_info_idx
            and current_tray_info_idx not in _generic_id_values
            and current_tray_type
            and current_tray_type.upper() == tray_type.upper()
        ):
            tray_info_idx = current_tray_info_idx
        elif tray_type:
            material = tray_type.upper().strip()
            generic = (
                _GENERIC_FILAMENT_IDS.get(material)
                or _GENERIC_FILAMENT_IDS.get(material.split("-")[0].split(" ")[0])
                or ""
            )
            if generic:
                tray_info_idx = generic

    temp_min, temp_max = MATERIAL_TEMPS.get((spool.material or "").upper(), (200, 240))
    if spool.nozzle_temp_min is not None:
        temp_min = spool.nozzle_temp_min
    if spool.nozzle_temp_max is not None:
        temp_max = spool.nozzle_temp_max

    nozzle_diameter = "0.4"
    if state and state.nozzles:
        nd = state.nozzles[0].nozzle_diameter
        if nd:
            nozzle_diameter = nd

    slot_extruder = None
    if state and state.ams_extruder_map:
        if ams_id == 255:
            slot_extruder = 1 - tray_id  # ext-L (tray 0) → extruder 1, ext-R (tray 1) → extruder 0
        else:
            slot_extruder = state.ams_extruder_map.get(str(ams_id))

    # Prefer exact extruder match, fall back to extruder-agnostic kp for the
    # same nozzle. Hard-skipping on mismatch silently drops valid stored
    # profiles when the AMS-extruder mapping has shifted.
    exact_kp = None
    fallback_kp = None
    for kp in spool.k_profiles:
        if kp.printer_id != printer_id or kp.nozzle_diameter != nozzle_diameter:
            continue
        if slot_extruder is not None and kp.extruder is not None and kp.extruder == slot_extruder:
            exact_kp = kp
            break
        if fallback_kp is None:
            fallback_kp = kp
    matching_kp = exact_kp or fallback_kp

    # Resolve the printer-side calibration entry by looking up the cali_idx
    # in state.kprofiles. The printer keys its calibration table by
    # (filament_id, cali_idx) — for the cali_idx to stick, the slot's
    # filament_id must match the kp's. PFUS-prefix cloud user presets are
    # rejected by the slicer in tray_info_idx; the printer-reported
    # filament_id is typically a P-prefix local preset which is valid.
    printer_kp = None
    if matching_kp and matching_kp.cali_idx is not None and state and getattr(state, "kprofiles", None):
        for pkp in state.kprofiles:
            if pkp.slot_id == matching_kp.cali_idx and pkp.nozzle_diameter == nozzle_diameter:
                printer_kp = pkp
                break

    effective_tray_info_idx = tray_info_idx
    effective_setting_id = setting_id
    if printer_kp and printer_kp.filament_id:
        effective_tray_info_idx = printer_kp.filament_id
    target_setting_id = (printer_kp.setting_id if printer_kp else None) or (
        matching_kp.setting_id if matching_kp else None
    )
    if target_setting_id:
        effective_setting_id = target_setting_id
    if effective_tray_info_idx != tray_info_idx or effective_setting_id != setting_id:
        logger.info(
            "Spool assign: realigning tray_info_idx %r → %r, setting_id %r → %r (source=%s)",
            tray_info_idx,
            effective_tray_info_idx,
            setting_id,
            effective_setting_id,
            "printer" if printer_kp else "stored",
        )

    client.ams_set_filament_setting(
        ams_id=ams_id,
        tray_id=tray_id,
        tray_info_idx=effective_tray_info_idx,
        tray_type=tray_type,
        tray_sub_brands=tray_sub_brands,
        tray_color=tray_color,
        nozzle_temp_min=temp_min,
        nozzle_temp_max=temp_max,
        setting_id=effective_setting_id,
    )

    if matching_kp and matching_kp.cali_idx is not None:
        # filament_id for cali_sel must match the preset under which the kp
        # was registered. Priority: live printer kp > stored kp.setting_id >
        # spool.slicer_filament > realigned tray_info_idx.
        if printer_kp and printer_kp.filament_id:
            cali_filament_id = printer_kp.filament_id
        elif matching_kp.setting_id:
            cali_filament_id = normalize_slicer_filament(matching_kp.setting_id)[0] or matching_kp.setting_id
        else:
            cali_filament_id = spool.slicer_filament or effective_tray_info_idx
        client.extrusion_cali_sel(
            ams_id=ams_id,
            tray_id=tray_id,
            cali_idx=matching_kp.cali_idx,
            filament_id=cali_filament_id,
            nozzle_diameter=nozzle_diameter,
        )

    # Persist slot preset mapping for UI display (preset_name on hover card).
    try:
        from backend.app.models.slot_preset import SlotPresetMapping

        preset_name = spool.slicer_filament_name or tray_sub_brands or tray_type
        preset_source = "cloud"
        if sf:
            base_sf_mapping = sf.split("_")[0] if "_" in sf else sf
            try:
                int(base_sf_mapping)
                preset_id_to_save = f"local_{base_sf_mapping}"
                preset_source = "local"
            except (ValueError, TypeError):
                preset_id_to_save = filament_id_to_setting_id(tray_info_idx) if tray_info_idx else setting_id
        else:
            preset_id_to_save = filament_id_to_setting_id(tray_info_idx) if tray_info_idx else ""

        if preset_id_to_save:
            existing_mapping = await db.execute(
                select(SlotPresetMapping).where(
                    SlotPresetMapping.printer_id == printer_id,
                    SlotPresetMapping.ams_id == ams_id,
                    SlotPresetMapping.tray_id == tray_id,
                )
            )
            mapping = existing_mapping.scalar_one_or_none()
            if mapping:
                mapping.preset_id = preset_id_to_save
                mapping.preset_name = preset_name
                mapping.preset_source = preset_source
            else:
                mapping = SlotPresetMapping(
                    printer_id=printer_id,
                    ams_id=ams_id,
                    tray_id=tray_id,
                    preset_id=preset_id_to_save,
                    preset_name=preset_name,
                    preset_source=preset_source,
                )
                db.add(mapping)
            await db.commit()
    except Exception as e:
        logger.warning("Failed to save slot preset mapping for spool %d: %s", spool.id, e)

    logger.info(
        "Auto-configured AMS slot ams=%d tray=%d for spool %d on printer %d",
        ams_id,
        tray_id,
        spool.id,
        printer_id,
    )
    return True


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


class BulkDeleteIdsRequest(BaseModel):
    ids: list[int]


# ── Color Catalog Schemas ──────────────────────────────────────────────────


class ColorEntryResponse(BaseModel):
    id: int
    manufacturer: str
    color_name: str
    hex_color: str
    material: str | None
    is_default: bool
    extra_colors: str | None = None
    effect_type: str | None = None

    class Config:
        from_attributes = True


_HEX_COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$"


class ColorEntryCreate(BaseModel):
    manufacturer: str
    color_name: str
    hex_color: str = Field(..., pattern=_HEX_COLOR_PATTERN)
    material: str | None = None
    extra_colors: str | None = None
    effect_type: str | None = None

    @field_validator("extra_colors")
    @classmethod
    def _validate_extra_colors(cls, v: str | None) -> str | None:
        return normalize_extra_colors(v)

    @field_validator("effect_type")
    @classmethod
    def _validate_effect_type(cls, v: str | None) -> str | None:
        return normalize_effect_type(v)


class ColorEntryUpdate(BaseModel):
    manufacturer: str
    color_name: str
    hex_color: str = Field(..., pattern=_HEX_COLOR_PATTERN)
    material: str | None = None
    extra_colors: str | None = None
    effect_type: str | None = None

    @field_validator("extra_colors")
    @classmethod
    def _validate_extra_colors(cls, v: str | None) -> str | None:
        return normalize_extra_colors(v)

    @field_validator("effect_type")
    @classmethod
    def _validate_effect_type(cls, v: str | None) -> str | None:
        return normalize_effect_type(v)


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


@router.post("/catalog/bulk-delete")
async def bulk_delete_catalog_entries(
    data: BulkDeleteIdsRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Delete multiple spool catalog entries by ID."""
    if not data.ids:
        return {"deleted": 0}
    result = await db.execute(select(SpoolCatalogEntry).where(SpoolCatalogEntry.id.in_(data.ids)))
    rows = result.scalars().all()
    for row in rows:
        await db.delete(row)
    await db.commit()
    return {"deleted": len(rows)}


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


@router.get("/colors/map")
async def get_color_name_map(
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_auth_if_enabled),
):
    """Compact {hex: name} map for frontend color-name resolution.

    Not gated on INVENTORY_READ — every page that renders a spool color needs
    this, including read-only views available to users without inventory access.
    Normalized to lowercase 6-char hex without '#'. When multiple catalog entries
    share the same hex (different materials or manufacturers), Bambu Lab wins,
    then default entries, then the first encountered.
    """
    result = await db.execute(
        select(
            ColorCatalogEntry.hex_color,
            ColorCatalogEntry.color_name,
            ColorCatalogEntry.manufacturer,
            ColorCatalogEntry.is_default,
        )
    )
    mapping: dict[str, tuple[str, int]] = {}  # hex → (name, priority); higher priority wins
    for hex_color, color_name, manufacturer, is_default in result.all():
        if not hex_color or not color_name:
            continue
        key = hex_color.lstrip("#").lower()[:6]
        if len(key) != 6:
            continue
        priority = 0
        if manufacturer and manufacturer.strip().lower() == "bambu lab":
            priority += 2
        if is_default:
            priority += 1
        existing = mapping.get(key)
        if existing is None or priority > existing[1]:
            mapping[key] = (color_name, priority)
    return {"colors": {k: v[0] for k, v in mapping.items()}}


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
        extra_colors=entry.extra_colors,
        effect_type=entry.effect_type,
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
    row.extra_colors = entry.extra_colors
    row.effect_type = entry.effect_type
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


@router.post("/colors/bulk-delete")
async def bulk_delete_color_entries(
    data: BulkDeleteIdsRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Delete multiple color catalog entries by ID."""
    if not data.ids:
        return {"deleted": 0}
    result = await db.execute(select(ColorCatalogEntry).where(ColorCatalogEntry.id.in_(data.ids)))
    rows = result.scalars().all()
    for row in rows:
        await db.delete(row)
    await db.commit()
    return {"deleted": len(rows)}


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
    await ws_manager.broadcast({"type": "inventory_changed"})
    return result.scalar_one()


@router.post("/spools/bulk", response_model=list[SpoolResponse])
async def bulk_create_spools(
    data: SpoolBulkCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Create multiple identical spools."""
    spools = []
    for _ in range(data.quantity):
        spool = Spool(**data.spool.model_dump())
        db.add(spool)
        spools.append(spool)
    await db.commit()
    ids = [s.id for s in spools]
    result = await db.execute(select(Spool).options(selectinload(Spool.k_profiles)).where(Spool.id.in_(ids)))
    await ws_manager.broadcast({"type": "inventory_changed"})
    return list(result.scalars().all())


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

    update_data = spool_data.model_dump(exclude_unset=True)
    # Auto-lock weight when user explicitly sets weight_used
    if "weight_used" in update_data and "weight_locked" not in update_data:
        update_data["weight_locked"] = True

    for field, value in update_data.items():
        setattr(spool, field, value)

    await db.commit()
    result = await db.execute(select(Spool).options(selectinload(Spool.k_profiles)).where(Spool.id == spool_id))
    await ws_manager.broadcast({"type": "inventory_changed"})
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
    await ws_manager.broadcast({"type": "inventory_changed"})
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
    await ws_manager.broadcast({"type": "inventory_changed"})
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
    await ws_manager.broadcast({"type": "inventory_changed"})
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
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_VIEW_ASSIGNMENTS),
):
    """List spool assignments, optionally filtered by printer."""
    from backend.app.services.printer_manager import printer_manager

    query = select(SpoolAssignment).options(
        selectinload(SpoolAssignment.spool).selectinload(Spool.k_profiles),
        selectinload(SpoolAssignment.printer),
    )
    if printer_id is not None:
        query = query.where(SpoolAssignment.printer_id == printer_id)
    result = await db.execute(query)
    assignments = list(result.scalars().all())

    # Build (printer_id, ams_id) -> ams_serial map from live printer states.
    # Fetch all statuses in one call rather than one get_status() call per printer.
    serial_map: dict[tuple[int, int], str] = {}
    seen_printer_ids: set[int] = {a.printer_id for a in assignments}
    all_statuses = printer_manager.get_all_statuses()
    for pid in seen_printer_ids:
        state = all_statuses.get(pid)
        if state and state.raw_data:
            for ams_unit in state.raw_data.get("ams", []):
                sn = str(ams_unit.get("sn") or ams_unit.get("serial_number") or "")
                if sn:
                    try:
                        serial_map[(pid, int(ams_unit.get("id", 0)))] = sn
                    except (ValueError, TypeError):
                        continue

    # Fetch all relevant AMS labels keyed by serial number
    all_serials = set(serial_map.values())
    # Also include synthetic fallback keys for assignments without a known serial
    synthetic_keys: dict[str, tuple[int, int]] = {}
    for a in assignments:
        if (a.printer_id, a.ams_id) not in serial_map:
            synthetic = f"p{a.printer_id}a{a.ams_id}"
            synthetic_keys[synthetic] = (a.printer_id, a.ams_id)
            all_serials.add(synthetic)

    label_by_serial: dict[str, str] = {}
    if all_serials:
        lbl_result = await db.execute(select(AmsLabel).where(AmsLabel.ams_serial_number.in_(all_serials)))
        for lbl in lbl_result.scalars().all():
            label_by_serial[lbl.ams_serial_number] = lbl.label

    # Build response objects, attaching ams_label where available
    responses: list[SpoolAssignmentResponse] = []
    for a in assignments:
        resp = SpoolAssignmentResponse.model_validate(a)
        sn = serial_map.get((a.printer_id, a.ams_id))
        if sn and sn in label_by_serial:
            resp.ams_label = label_by_serial[sn]
        elif not sn:
            synthetic = f"p{a.printer_id}a{a.ams_id}"
            resp.ams_label = label_by_serial.get(synthetic)
        responses.append(resp)

    return responses


@router.post("/assignments", response_model=SpoolAssignmentResponse)
async def assign_spool(
    data: SpoolAssignmentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
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

    # 2. Get current AMS tray state for fingerprint + existing filament ID
    fingerprint_color = None
    fingerprint_type = None
    current_tray_info_idx = ""
    state = printer_manager.get_status(data.printer_id)
    if state and state.raw_data:
        if data.ams_id == 255:
            # External slot: look up tray from vt_tray by global ID
            vt_tray = state.raw_data.get("vt_tray") or []
            ext_id = data.tray_id + 254  # 0→254, 1→255
            for vt in vt_tray:
                if isinstance(vt, dict) and int(vt.get("id", 254)) == ext_id:
                    fingerprint_color = vt.get("tray_color", "")
                    fingerprint_type = vt.get("tray_type", "")
                    current_tray_info_idx = vt.get("tray_info_idx", "")
                    break
        else:
            ams_data = state.raw_data.get("ams", {})
            ams_list = (
                ams_data.get("ams", [])
                if isinstance(ams_data, dict)
                else ams_data
                if isinstance(ams_data, list)
                else []
            )
            tray = _find_tray_in_ams_data(
                ams_list,
                data.ams_id,
                data.tray_id,
            )
            if tray:
                fingerprint_color = tray.get("tray_color", "")
                fingerprint_type = tray.get("tray_type", "")
                current_tray_info_idx = tray.get("tray_info_idx", "")

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

    # 4. Auto-configure AMS slot via MQTT.
    #
    # Skip the publish entirely when the target slot is empty: Bambu firmware
    # silently drops ams_filament_setting / extrusion_cali_sel for unloaded
    # slots (there is no filament context for the cali_idx to attach to). The
    # SpoolAssignment row is preserved with an empty fingerprint_type, which
    # acts as the "pending config" marker — when the spool is physically
    # inserted later, on_ams_change re-fires the full configuration. This is
    # the SpoolBuddy primary workflow: weigh-then-assign before insertion.
    slot_is_empty = not (fingerprint_type and fingerprint_type.strip())
    configured = False
    pending_config = slot_is_empty
    if not slot_is_empty:
        try:
            configured = await apply_spool_to_slot_via_mqtt(
                db=db,
                current_user=current_user,
                spool=spool,
                printer_id=data.printer_id,
                ams_id=data.ams_id,
                tray_id=data.tray_id,
                current_tray_info_idx=current_tray_info_idx,
                current_tray_type=fingerprint_type or "",
            )
        except Exception as e:
            logger.warning("MQTT auto-configure failed for spool %d: %s", spool.id, e)

    # Return assignment with spool data
    result = await db.execute(
        select(SpoolAssignment)
        .options(
            selectinload(SpoolAssignment.spool).selectinload(Spool.k_profiles),
            selectinload(SpoolAssignment.printer),
        )
        .where(SpoolAssignment.id == assignment.id)
    )
    resp = result.scalar_one()
    response = SpoolAssignmentResponse.model_validate(resp)
    response.configured = configured
    response.pending_config = pending_config

    if pending_config:
        logger.info(
            "Pre-configured assignment: spool %d → printer %d AMS%d-T%d (slot empty, will configure on insert)",
            spool.id,
            data.printer_id,
            data.ams_id,
            data.tray_id,
        )

    await ws_manager.broadcast(
        {
            "type": "spool_assignment_changed",
            "printer_id": data.printer_id,
            "ams_id": data.ams_id,
            "tray_id": data.tray_id,
        }
    )

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

    await ws_manager.broadcast(
        {
            "type": "spool_assignment_changed",
            "printer_id": printer_id,
            "ams_id": ams_id,
            "tray_id": tray_id,
        }
    )

    return {"status": "deleted"}


# ── Tag Linking ───────────────────────────────────────────────────────────────


class LinkTagRequest(BaseModel):
    tag_uid: str | None = None
    tray_uuid: str | None = None
    tag_type: str | None = None
    data_origin: str | None = "nfc_link"


def _validate_tag_input(
    raw_value: str | None, normalized_value: str | None, field_name: str, exact_len: int | None = None
) -> None:
    if raw_value is None:
        return
    raw = str(raw_value).strip()
    if not raw:
        return
    if normalized_value is None:
        raise HTTPException(422, f"{field_name} must contain hexadecimal characters")
    if len(normalized_value) % 2 != 0:
        raise HTTPException(422, f"{field_name} must have an even number of hex characters")
    if exact_len is not None and len(normalized_value) != exact_len:
        raise HTTPException(422, f"{field_name} must be exactly {exact_len} hex characters")


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

    normalized_tag_uid = (normalize_tag_uid(data.tag_uid) or None) if data.tag_uid is not None else None
    normalized_tray_uuid = (normalize_tray_uuid(data.tray_uuid) or None) if data.tray_uuid is not None else None

    _validate_tag_input(data.tag_uid, normalized_tag_uid, "tag_uid")
    _validate_tag_input(data.tray_uuid, normalized_tray_uuid, "tray_uuid", exact_len=32)

    # Check for conflicts: tag already linked to another active spool
    if normalized_tag_uid:
        conflict = await db.execute(
            select(Spool).where(
                func.upper(Spool.tag_uid) == normalized_tag_uid,
                Spool.id != spool_id,
                Spool.archived_at.is_(None),
            )
        )
        if conflict.scalar_one_or_none():
            raise HTTPException(409, "Tag UID already linked to another active spool")
        # Auto-clear from archived spools (tag recycling)
        archived_with_tag = await db.execute(
            select(Spool).where(
                func.upper(Spool.tag_uid) == normalized_tag_uid,
                Spool.id != spool_id,
                Spool.archived_at.is_not(None),
            )
        )
        for old_spool in archived_with_tag.scalars().all():
            old_spool.tag_uid = None

    if normalized_tray_uuid:
        conflict = await db.execute(
            select(Spool).where(
                func.upper(Spool.tray_uuid) == normalized_tray_uuid,
                Spool.id != spool_id,
                Spool.archived_at.is_(None),
            )
        )
        if conflict.scalar_one_or_none():
            raise HTTPException(409, "Tray UUID already linked to another active spool")
        archived_with_uuid = await db.execute(
            select(Spool).where(
                func.upper(Spool.tray_uuid) == normalized_tray_uuid,
                Spool.id != spool_id,
                Spool.archived_at.is_not(None),
            )
        )
        for old_spool in archived_with_uuid.scalars().all():
            old_spool.tray_uuid = None

    if data.tag_uid is not None:
        spool.tag_uid = normalized_tag_uid
    if data.tray_uuid is not None:
        spool.tray_uuid = normalized_tray_uuid
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


# ── AMS Weight Sync ──────────────────────────────────────────────────────────


@router.post("/sync-ams-weights")
async def sync_weights_from_ams(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Force-sync spool weight_used from live AMS remain% data.

    Overwrites the database weight_used for every assigned spool using the
    current AMS remain% from connected printers.  This is a manual recovery
    tool — it bypasses the normal "only increase" guard.
    """
    from backend.app.services.printer_manager import printer_manager

    result = await db.execute(select(SpoolAssignment).options(selectinload(SpoolAssignment.spool)))
    assignments = list(result.scalars().all())
    logger.info("AMS weight sync: found %d assignments", len(assignments))

    synced = 0
    skipped = 0

    for assignment in assignments:
        spool = assignment.spool
        if not spool:
            logger.debug("AMS weight sync: assignment %d has no spool", assignment.id)
            skipped += 1
            continue

        if spool.weight_locked:
            logger.debug("AMS weight sync: spool %d is weight-locked, skipping", spool.id)
            skipped += 1
            continue

        state = printer_manager.get_status(assignment.printer_id)
        if not state or not state.raw_data:
            logger.info(
                "AMS weight sync: printer %d not connected, skipping spool %d",
                assignment.printer_id,
                spool.id,
            )
            skipped += 1
            continue

        ams_raw = state.raw_data.get("ams", [])
        if isinstance(ams_raw, dict):
            ams_raw = ams_raw.get("ams", [])
        tray = _find_tray_in_ams_data(ams_raw, assignment.ams_id, assignment.tray_id)
        if not tray:
            logger.info(
                "AMS weight sync: no tray data for spool %d (printer %d AMS%d-T%d)",
                spool.id,
                assignment.printer_id,
                assignment.ams_id,
                assignment.tray_id,
            )
            skipped += 1
            continue

        remain_raw = tray.get("remain")
        if remain_raw is None:
            logger.debug("AMS weight sync: no remain value for spool %d", spool.id)
            skipped += 1
            continue

        try:
            remain_val = int(remain_raw)
        except (TypeError, ValueError):
            skipped += 1
            continue

        if remain_val < 0 or remain_val > 100:
            logger.debug("AMS weight sync: invalid remain=%s for spool %d", remain_raw, spool.id)
            skipped += 1
            continue

        lw = spool.label_weight or 1000
        new_used = round(lw * (100 - remain_val) / 100.0, 1)
        old_used = spool.weight_used or 0

        if round(old_used, 1) != new_used:
            logger.info(
                "AMS weight sync: spool %d weight_used %s -> %s (remain=%d%%)",
                spool.id,
                old_used,
                new_used,
                remain_val,
            )
            spool.weight_used = new_used
            synced += 1
        else:
            skipped += 1

    await db.commit()
    return {"synced": synced, "skipped": skipped}


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
