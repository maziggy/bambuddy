import json
import logging

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.auth import (
    RequireAnyPermissionIfAuthEnabled,
    RequirePermissionIfAuthEnabled,
    require_auth_if_enabled,
)
from backend.app.core.catalog_defaults import DEFAULT_COLOR_CATALOG, DEFAULT_SPOOL_CATALOG
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.core.websocket import ws_manager
from backend.app.models.ams_label import AmsLabel
from backend.app.models.color_catalog import ColorCatalogEntry
from backend.app.models.location import Location
from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spool_catalog import SpoolCatalogEntry
from backend.app.models.spool_k_profile import SpoolKProfile
from backend.app.models.user import User
from backend.app.schemas.location import LocationCreate, LocationResponse, LocationUpdate
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
from backend.app.services.location_service import (
    DUPLICATE_LOCATION_NAME,
    assign_location_name,
    count_internal_spools_at_location,
    get_location_by_id,
    get_location_by_name,
    location_name_key,
    prepare_internal_spool_payload,
    rename_location as rename_location_record,
)
from backend.app.services.slicer_filament_resolver import resolve_slicer_filament
from backend.app.services.spool_csv import (
    MAX_CSV_IMPORT_BYTES,
    ImportPreview,
    ImportResult,
    parse_and_validate,
    serialize,
)
from backend.app.services.spoolman import SpoolmanClient, get_spoolman_client, init_spoolman_client
from backend.app.utils.filament_ids import (
    GENERIC_FILAMENT_IDS,
    MATERIAL_TEMPS,
    filament_id_to_setting_id,
    normalize_slicer_filament,
)
from backend.app.utils.tag_normalization import normalize_tag_uid, normalize_tray_uuid

logger = logging.getLogger(__name__)

_GENERIC_ID_VALUES = set(GENERIC_FILAMENT_IDS.values())

router = APIRouter(prefix="/inventory", tags=["inventory"])

# Bounded read size for the CSV import body so a chunked upload with no
# Content-Length can't stream past the cap into memory before we notice.
_CSV_UPLOAD_CHUNK_BYTES = 64 * 1024

# FilamentColors.xyz API
FILAMENT_COLORS_API = "https://filamentcolors.xyz/api"


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

    _generic_id_values = _GENERIC_ID_VALUES
    _known_materials = set(MATERIAL_TEMPS.keys()) | set(GENERIC_FILAMENT_IDS.keys())

    # slicer_filament → (tray_info_idx, setting_id) resolution is shared with
    # the Spoolman-mode route via this helper (#1713). The helper handles
    # GFS/PFUS/PFCN cloud lookup, GF normalize, integer LocalPreset id,
    # the builtin-name realignment, AND the defensive PFUS/PFCN/material-name
    # sanitization. When it returns an empty tray_info_idx the local
    # current-tray-state + generic-material fallback below rescues the slot.
    tray_info_idx, setting_id, sub_brand_override = await resolve_slicer_filament(
        db=db,
        current_user=current_user,
        slicer_filament=spool.slicer_filament,
        slicer_filament_name=spool.slicer_filament_name,
        material=spool.material,
    )
    if sub_brand_override:
        tray_sub_brands = sub_brand_override

    if not tray_info_idx:
        if (
            current_tray_info_idx
            and current_tray_info_idx not in _generic_id_values
            and not current_tray_info_idx.startswith("PFUS")
            and not current_tray_info_idx.startswith("PFCN")
            and current_tray_info_idx.upper() not in _known_materials
            and current_tray_type
            and current_tray_type.upper() == tray_type.upper()
        ):
            tray_info_idx = current_tray_info_idx
        elif tray_type:
            material = tray_type.upper().strip()
            generic = (
                GENERIC_FILAMENT_IDS.get(material)
                or GENERIC_FILAMENT_IDS.get(material.split("-")[0].split(" ")[0])
                or ""
            )
            if generic:
                tray_info_idx = generic

    # Ensure setting_id is always derivable from tray_info_idx. The local-preset
    # path above sets tray_info_idx to a generic ID (e.g. "GFL99") but leaves
    # setting_id empty — without this fallback the slicer gets a half-configured
    # slot (filament id without setting id) and shows empty fields in the slot
    # detail modal.
    if tray_info_idx and not setting_id:
        setting_id = filament_id_to_setting_id(tray_info_idx)

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
    else:
        # No stored K-profile for this spool — always reset the slot to Default
        # K (cali_idx=-1). The live cali_idx on the slot belongs to whatever
        # filament was there before, so preserving it would apply the wrong
        # filament's calibration to the new spool. Default K is the firmware's
        # documented "no specific profile" value (see BambuClient.extrusion_cali_sel
        # docstring).
        cali_filament_id = spool.slicer_filament or effective_tray_info_idx
        client.extrusion_cali_sel(
            ams_id=ams_id,
            tray_id=tray_id,
            cali_idx=-1,
            filament_id=cali_filament_id,
            nozzle_diameter=nozzle_diameter,
        )
        logger.info(
            "No stored K-profile for spool %d — reset slot to Default K (cali_idx=-1)",
            spool.id,
        )

    # Persist slot preset mapping for UI display (preset_name on hover card).
    # Shared with the RFID auto-assign path — both must keep this row in sync
    # with the currently-assigned spool, otherwise the slot card surfaces the
    # previous spool's preset name (the PrintersPage display chain consults
    # slot_preset_mappings.preset_name first).
    from backend.app.services.slot_preset_writer import upsert_slot_preset_for_spool

    await upsert_slot_preset_for_spool(
        db=db,
        spool=spool,
        printer_id=printer_id,
        ams_id=ams_id,
        tray_id=tray_id,
        tray_info_idx=tray_info_idx,
        tray_sub_brands=tray_sub_brands,
        tray_type=tray_type,
        setting_id=setting_id,
    )

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


class ColorByMaterialResult(BaseModel):
    color_name: str | None = None


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


# ── Storage Locations (#1004) ───────────────────────────────────────────────


async def _load_settings_map(db: AsyncSession) -> dict[str, str]:
    result = await db.execute(select(Settings))
    return {s.key: s.value for s in result.scalars().all()}


def _spoolman_is_enabled(settings: dict[str, str]) -> bool:
    return settings.get("spoolman_enabled", "false").lower() == "true"


async def _ensure_spoolman_client(settings: dict[str, str]) -> SpoolmanClient | None:
    if not _spoolman_is_enabled(settings):
        return None
    url = settings.get("spoolman_url", "").strip()
    if not url:
        return None
    from backend.app.api.routes._spoolman_helpers import assert_safe_spoolman_url

    try:
        assert_safe_spoolman_url(url)
    except ValueError:
        return None
    client = await get_spoolman_client()
    if not client or client.base_url != url.rstrip("/"):
        client = await init_spoolman_client(url)
    return client


async def _spool_counts_for_locations(
    db: AsyncSession,
    locations: list[Location],
    settings: dict[str, str],
) -> dict[int, int]:
    if _spoolman_is_enabled(settings):
        client = await _ensure_spoolman_client(settings)
        if client:
            try:
                spools = await client.get_all_spools(allow_archived=False)
            except Exception:
                logger.warning("Failed to fetch Spoolman spools for location counts", exc_info=True)
            else:
                # Use the canonical key helper so this matches what the
                # migration backfill, Location.name_key, and every other
                # codepath store as the case-insensitive lookup key. Plain
                # str.lower() drifts for non-ASCII (Turkish ı/İ, German ß)
                # and caused mismatched delete-block counts in Spoolman mode.
                by_key: dict[str, int] = {}
                for spool in spools:
                    raw = spool.get("location")
                    if not raw or not isinstance(raw, str) or not raw.strip():
                        continue
                    try:
                        key = location_name_key(raw)
                    except ValueError:
                        continue
                    by_key[key] = by_key.get(key, 0) + 1
                return {loc.id: by_key.get(loc.name_key, 0) for loc in locations}

    counts: dict[int, int] = {}
    for loc in locations:
        counts[loc.id] = await count_internal_spools_at_location(db, loc.id)
    return counts


def _location_to_response(location: Location, spool_count: int) -> LocationResponse:
    return LocationResponse(
        id=location.id,
        name=location.name,
        identifier=location.identifier,
        spool_count=spool_count,
        created_at=location.created_at,
        updated_at=location.updated_at,
    )


@router.get("/locations", response_model=list[LocationResponse])
async def list_locations(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """List all storage locations with spool counts."""
    settings = await _load_settings_map(db)
    result = await db.execute(select(Location).order_by(Location.name))
    locations = list(result.scalars().all())
    counts = await _spool_counts_for_locations(db, locations, settings)
    return [_location_to_response(loc, counts.get(loc.id, 0)) for loc in locations]


@router.post("/locations", response_model=LocationResponse, status_code=201)
async def create_location(
    data: LocationCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Create a storage location."""
    existing = await get_location_by_name(db, data.name)
    if existing:
        raise HTTPException(status_code=409, detail=DUPLICATE_LOCATION_NAME)
    location = Location(identifier=data.identifier)
    assign_location_name(location, data.name)
    db.add(location)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_LOCATION_NAME) from exc
    await db.refresh(location)
    await ws_manager.broadcast({"type": "inventory_changed"})
    return _location_to_response(location, 0)


@router.patch("/locations/{location_id}", response_model=LocationResponse)
async def update_location(
    location_id: int,
    data: LocationUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Update a storage location (rename propagates to assigned spools)."""
    location = await get_location_by_id(db, location_id)
    if not location:
        raise HTTPException(status_code=404, detail="Location not found")

    old_name = location.name
    if data.identifier is not None:
        location.identifier = data.identifier or None

    if data.name is not None and data.name != old_name:
        try:
            await rename_location_record(db, location, data.name)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        # Cascade to Spoolman BEFORE the local commit so a Spoolman failure
        # rolls back the local rename instead of leaving the catalog and
        # Spoolman's per-spool `location` field permanently diverged. Without
        # this ordering, a partial failure makes the next location-sync recreate
        # the old name as a duplicate catalog row (#1505 review blocker).
        settings = await _load_settings_map(db)
        client = await _ensure_spoolman_client(settings)
        if client:
            try:
                await client.rename_location(old_name, location.name)
            except Exception as exc:
                logger.warning(
                    "Spoolman location rename failed for %s -> %s: %s",
                    old_name,
                    location.name,
                    exc,
                )
                await db.rollback()
                raise HTTPException(
                    status_code=502,
                    detail="Spoolman rename failed; local rename rolled back",
                ) from exc

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_LOCATION_NAME) from exc
    await db.refresh(location)
    settings = await _load_settings_map(db)
    counts = await _spool_counts_for_locations(db, [location], settings)
    await ws_manager.broadcast({"type": "inventory_changed"})
    return _location_to_response(location, counts.get(location.id, 0))


@router.delete("/locations/{location_id}")
async def delete_location(
    location_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Delete a storage location when no spools are assigned."""
    location = await get_location_by_id(db, location_id)
    if not location:
        raise HTTPException(status_code=404, detail="Location not found")

    settings = await _load_settings_map(db)
    counts = await _spool_counts_for_locations(db, [location], settings)
    if counts.get(location.id, 0) > 0:
        raise HTTPException(status_code=409, detail="Location has spools assigned and cannot be deleted")

    await db.delete(location)
    await db.commit()
    await ws_manager.broadcast({"type": "inventory_changed"})
    return {"status": "deleted"}


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


@router.get("/colors/by-material", response_model=ColorByMaterialResult)
async def get_color_by_material(
    hex: str,
    material: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_auth_if_enabled),
):
    """Disambiguated hex→name lookup that respects material context.

    ``/colors/map`` collapses every catalog entry sharing a hex to a single
    name with "Bambu Lab > is_default > first" priority — that loses, e.g.,
    "PLA Matte Charcoal" (#000000) behind "PLA Basic Black" (also #000000).
    This endpoint preserves the material context so the queue scheduler's
    Filament Override label can show the actually-sliced sub-brand colour
    instead of the generic bucket. #1718.

    Returns ``color_name=None`` when the hex isn't in the catalog at all.
    When the hex IS in the catalog but no entry matches the requested
    material (or none was supplied), falls back to the same priority order
    as ``/colors/map`` so callers without a material hint don't regress.

    Not gated on INVENTORY_READ for the same reason ``/colors/map`` isn't —
    every queue / archive view that renders a sliced filament colour needs
    this, including read-only roles.
    """
    key = hex.lstrip("#").lower()[:6]
    if len(key) != 6:
        return ColorByMaterialResult(color_name=None)

    material_norm = (material or "").strip().lower()

    # Catalog rows are stored as ``#RRGGBB`` (verified at write time and
    # against production); lookup uses lower-cased hex equality so mixed-case
    # writes from older imports still match.
    result = await db.execute(
        select(
            ColorCatalogEntry.color_name,
            ColorCatalogEntry.manufacturer,
            ColorCatalogEntry.material,
            ColorCatalogEntry.is_default,
        ).where(func.lower(ColorCatalogEntry.hex_color) == f"#{key}")
    )
    candidates = [(name, mfg, mat, is_default) for name, mfg, mat, is_default in result.all() if name]
    if not candidates:
        return ColorByMaterialResult(color_name=None)

    if material_norm:
        for name, _mfg, mat, _is_default in candidates:
            if mat and mat.strip().lower() == material_norm:
                return ColorByMaterialResult(color_name=name)

    # Same priority order as ``/colors/map`` so a caller passing no (or an
    # unrecognised) material gets the existing answer, not a degraded one.
    best_name: str | None = None
    best_priority = -1
    for name, mfg, _mat, is_default in candidates:
        priority = 0
        if mfg and mfg.strip().lower() == "bambu lab":
            priority += 2
        if is_default:
            priority += 1
        if priority > best_priority:
            best_name = name
            best_priority = priority
    return ColorByMaterialResult(color_name=best_name)


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
            # Identify honestly as Bambuddy rather than leaking httpx's
            # default "python-httpx/x.y" UA — consistent with every other
            # outbound client (bambu_cloud, makerworld, firmware_check).
            async with httpx.AsyncClient(
                timeout=120.0,
                headers={"User-Agent": "Bambuddy/1.0 (+https://github.com/maziggy/bambuddy)"},
            ) as client:
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


# ── CSV import / export (#1576) ──────────────────────────────────────────────
# Declared before the dynamic `/spools/{spool_id}` route below so the literal
# `export` / `import` segments match here instead of being parsed as an int id.


@router.get("/spools/export")
async def export_spools_csv(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """Export the active inventory as CSV (same schema the importer accepts)."""
    from datetime import datetime, timezone

    query = select(Spool).where(Spool.archived_at.is_(None)).order_by(Spool.material, Spool.brand, Spool.color_name)
    result = await db.execute(query)
    spools = list(result.scalars().all())
    content = serialize(spools)
    # Date-stamp the filename so repeat exports don't overwrite each other in
    # the browser's default download folder.
    filename = f"bambuddy_inventory_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/spools/import", response_model=ImportPreview | ImportResult)
async def import_spools_csv(
    file: UploadFile = File(...),
    dry_run: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Import spools from a CSV file.

    With ``dry_run=true`` returns an ImportPreview (per-row valid/error/skipped,
    colours resolved) and writes nothing — the UI shows this before the user
    confirms. With ``dry_run=false`` it validates the same way and then persists
    only the valid rows in a single transaction (invalid rows are skipped, the
    user fixes the CSV and re-uploads), returning an ImportResult summary.
    """

    def _too_large() -> HTTPException:
        return HTTPException(
            status_code=413,
            detail={
                "code": "csv_import_too_large",
                "message": f"CSV file exceeds the {MAX_CSV_IMPORT_BYTES // (1024 * 1024)} MB limit.",
            },
        )

    # Reject by declared size first (fast path when Content-Length is set), then
    # read in bounded chunks and bail the moment the accumulated body crosses the
    # cap — file.size is None for chunked uploads, so the loop is what actually
    # keeps an oversized stream from filling memory.
    if file.size is not None and file.size > MAX_CSV_IMPORT_BYTES:
        raise _too_large()
    raw = bytearray()
    while chunk := await file.read(_CSV_UPLOAD_CHUNK_BYTES):
        raw.extend(chunk)
        if len(raw) > MAX_CSV_IMPORT_BYTES:
            raise _too_large()
    preview = await parse_and_validate(bytes(raw), db)

    if dry_run:
        return preview

    created = 0
    for row in preview.rows:
        if row.status == "valid" and row.spool is not None:
            db.add(Spool(**row.spool))
            created += 1

    if created:
        await db.commit()
        await ws_manager.broadcast({"type": "inventory_changed"})

    return ImportResult(
        created=created,
        skipped=preview.skipped_count,
        errors=preview.error_count,
        error_rows=[r for r in preview.rows if r.status == "error"],
    )


@router.get("/spools/by-tag", response_model=SpoolResponse)
async def get_spool_by_tag(
    tray_uuid: str | None = None,
    tag_uid: str | None = None,
    include_archived: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequireAnyPermissionIfAuthEnabled(Permission.INVENTORY_READ, Permission.INVENTORY_UPDATE),
):
    """Find a single spool by its NFC ``tray_uuid`` and/or ``tag_uid``.

    Lets NFC inventory integrations dedupe a scan without listing the whole
    inventory. ``tray_uuid`` is the primary identifier (it matches the value the
    AMS reports over MQTT), so it is tried first; ``tag_uid`` is the fallback.
    At least one identifier must be supplied. Returns 404 when nothing matches.

    Accepts ``inventory:read`` OR ``inventory:update`` so a Manage-Inventory API
    key (which has ``inventory:update`` via ``can_manage_inventory``) can read a
    spool back without widening the global ``INVENTORY_READ`` scope mapping (#1663).
    """
    normalized_tray_uuid = normalize_tray_uuid(tray_uuid) or None
    normalized_tag_uid = normalize_tag_uid(tag_uid) or None

    if not normalized_tray_uuid and not normalized_tag_uid:
        raise HTTPException(400, "Provide tray_uuid and/or tag_uid")

    base_query = select(Spool).options(selectinload(Spool.k_profiles))
    if not include_archived:
        base_query = base_query.where(Spool.archived_at.is_(None))

    for column, value in (
        (Spool.tray_uuid, normalized_tray_uuid),
        (Spool.tag_uid, normalized_tag_uid),
    ):
        if not value:
            continue
        result = await db.execute(base_query.where(func.upper(column) == value).order_by(Spool.id))
        spool = result.scalars().first()
        if spool:
            return spool

    raise HTTPException(404, "Spool not found")


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
    try:
        payload = await prepare_internal_spool_payload(db, spool_data.model_dump(), set(spool_data.model_fields_set))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    spool = Spool(**payload)
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
    fields_set = set(data.spool.model_fields_set)
    try:
        payload = await prepare_internal_spool_payload(db, data.spool.model_dump(), fields_set)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for _ in range(data.quantity):
        spool = Spool(**payload)
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
    try:
        update_data = await prepare_internal_spool_payload(db, update_data, set(spool_data.model_fields_set))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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


@router.post("/spools/{spool_id}/reset-consumed-counter", response_model=SpoolResponse)
async def reset_spool_consumed_counter(
    spool_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Zero the displayed "Total Consumed" counter without touching remaining.

    Stamps `weight_used_baseline = weight_used` so the Inventory page's
    `weight_used - baseline` display reads 0, while `label_weight -
    weight_used` (remaining) is unchanged. weight_locked is also left
    alone — the spool keeps receiving AMS auto-sync updates. Matches
    Spoolman's split between used_weight and remaining_weight (#1390).

    The earlier name `/reset-usage` was misleading: callers reasonably
    expected `weight_used` itself to drop to 0 and were surprised when
    the response showed it unchanged. The current name describes what
    the endpoint actually does — reset the "Total Consumed" counter
    widget, not the lifetime weight_used field.
    """
    result = await db.execute(select(Spool).where(Spool.id == spool_id))
    spool = result.scalar_one_or_none()
    if not spool:
        raise HTTPException(404, "Spool not found")

    spool.weight_used_baseline = spool.weight_used or 0
    await db.commit()
    result = await db.execute(select(Spool).options(selectinload(Spool.k_profiles)).where(Spool.id == spool_id))
    await ws_manager.broadcast({"type": "inventory_changed"})
    return result.scalar_one()


@router.post("/spools/reset-consumed-counter-bulk")
async def bulk_reset_spool_consumed_counter(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Bulk-stamp baseline = weight_used across the given spool IDs.

    Caller passes an explicit list of IDs — no "reset all" shortcut, since
    a typo on a wildcard would wipe the entire inventory's tracking.
    Same semantics as the per-spool endpoint: remaining is preserved,
    weight_locked is left alone.
    """
    spool_ids = payload.get("spool_ids")
    if not isinstance(spool_ids, list) or not spool_ids:
        raise HTTPException(400, "spool_ids must be a non-empty list")
    if not all(isinstance(sid, int) for sid in spool_ids):
        raise HTTPException(400, "spool_ids must contain integers")

    result = await db.execute(select(Spool).where(Spool.id.in_(spool_ids)))
    spools = list(result.scalars().all())
    for spool in spools:
        spool.weight_used_baseline = spool.weight_used or 0
    await db.commit()
    await ws_manager.broadcast({"type": "inventory_changed"})
    return {"reset": len(spools)}


class BulkUpdateRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=500)
    update: SpoolUpdate


class BulkIdsRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=500)


@router.post("/spools/bulk-update")
async def bulk_update_spools(
    payload: BulkUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Apply the same partial update to every listed spool.

    Per-spool errors are collected and returned alongside the success count so
    a single bad ID doesn't abort the whole batch. Unknown IDs are reported
    in the ``not_found`` list.
    """
    update_data = payload.update.model_dump(exclude_unset=True)
    fields_set = set(payload.update.model_fields_set)
    if not update_data:
        raise HTTPException(status_code=400, detail="update must include at least one field")
    try:
        prepared = await prepare_internal_spool_payload(db, update_data, fields_set)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Auto-lock weight when the user explicitly sets weight_used — mirrors the
    # per-spool PATCH behaviour so bulk edits don't desync the lock state.
    if "weight_used" in prepared and "weight_locked" not in prepared:
        prepared["weight_locked"] = True

    result = await db.execute(select(Spool).where(Spool.id.in_(payload.ids)))
    spools = {s.id: s for s in result.scalars().all()}
    not_found = [sid for sid in payload.ids if sid not in spools]
    updated_ids: list[int] = []
    for sid, spool in spools.items():
        for field, value in prepared.items():
            setattr(spool, field, value)
        updated_ids.append(sid)
    await db.commit()
    if updated_ids:
        await ws_manager.broadcast({"type": "inventory_changed"})
    return {"updated": len(updated_ids), "not_found": not_found}


@router.post("/spools/bulk-delete")
async def bulk_delete_spools(
    payload: BulkIdsRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Hard-delete every listed spool. Unknown IDs are returned in not_found."""
    result = await db.execute(select(Spool).where(Spool.id.in_(payload.ids)))
    spools = list(result.scalars().all())
    found_ids = {s.id for s in spools}
    not_found = [sid for sid in payload.ids if sid not in found_ids]
    for spool in spools:
        await db.delete(spool)
    await db.commit()
    if spools:
        await ws_manager.broadcast({"type": "inventory_changed"})
    return {"deleted": len(spools), "not_found": not_found}


@router.post("/spools/bulk-archive")
async def bulk_archive_spools(
    payload: BulkIdsRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Soft-archive every listed spool (sets archived_at). Already-archived spools are left alone and counted in already_archived."""
    from datetime import datetime, timezone

    result = await db.execute(select(Spool).where(Spool.id.in_(payload.ids)))
    spools = list(result.scalars().all())
    found_ids = {s.id for s in spools}
    not_found = [sid for sid in payload.ids if sid not in found_ids]
    archived: list[int] = []
    already: list[int] = []
    now = datetime.now(timezone.utc)
    for spool in spools:
        if spool.archived_at is not None:
            already.append(spool.id)
            continue
        spool.archived_at = now
        archived.append(spool.id)
    await db.commit()
    if archived:
        await ws_manager.broadcast({"type": "inventory_changed"})
    return {"archived": len(archived), "already_archived": already, "not_found": not_found}


@router.post("/spools/bulk-restore")
async def bulk_restore_spools(
    payload: BulkIdsRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Restore every listed archived spool. Non-archived rows are no-ops counted in already_active."""
    result = await db.execute(select(Spool).where(Spool.id.in_(payload.ids)))
    spools = list(result.scalars().all())
    found_ids = {s.id for s in spools}
    not_found = [sid for sid in payload.ids if sid not in found_ids]
    restored: list[int] = []
    already: list[int] = []
    for spool in spools:
        if spool.archived_at is None:
            already.append(spool.id)
            continue
        spool.archived_at = None
        restored.append(spool.id)
    await db.commit()
    if restored:
        await ws_manager.broadcast({"type": "inventory_changed"})
    return {"restored": len(restored), "already_active": already, "not_found": not_found}


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

    # 2. Get current AMS tray state for fingerprint + existing filament ID.
    # tray_state: Bambu firmware reports 11=loaded, 9=empty, 10=spool present
    # but filament not in feeder. Captured here so the empty-slot heuristic
    # below can prefer it over tray_type — a manual "Reset slot" clears
    # tray_type to "" while leaving state at 11 (filament still physically
    # present), which would otherwise mislead the heuristic into the
    # pending-config branch and skip MQTT forever (#1228 follow-up).
    fingerprint_color = None
    fingerprint_type = None
    current_tray_info_idx = ""
    tray_state: int | None = None
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
                    raw_state = vt.get("state")
                    if isinstance(raw_state, int):
                        tray_state = raw_state
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
                raw_state = tray.get("state")
                if isinstance(raw_state, int):
                    tray_state = raw_state

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
    # Only suppress the publish when the firmware's *explicit* empty signal
    # (state ∈ {9, 10}) is set — "no spool" / "spool present but no feed".
    # Every other state, including state=3 (the default idle on A1 Mini BMCU /
    # P1S Standard AMS for both loaded and unconfigured slots) and missing
    # state (older firmwares), is treated as the user's assertion that a
    # spool is in the slot and we attempt the MQTT push.
    #
    # The pre-existing "skip when slot looks empty" guard read state=3 +
    # tray_type="" as "empty" and skipped MQTT. On these firmwares that
    # combination is the post-"Reset Slot" state with the spool still
    # physically inserted — there is NO AMS signal that distinguishes it
    # from a truly-empty slot, so the guard created a deadlock: MQTT never
    # fired, the AMS never reported any change (because nothing changed
    # physically), and on_ams_change replay therefore never re-fired the
    # config either. Reporter (#1322 follow-up by @RosdasHH) verified
    # empirically that removing the guard makes the slot configure
    # correctly because Bambu firmware DOES accept the push for a
    # physically-loaded slot, even when tray_type is "" and state is 3.
    #
    # Trade-off for the truly-empty slot case: firmware drops the push
    # silently (per Bambu's documented behavior), the SpoolAssignment row
    # still has empty fingerprint_type because nothing in the assign path
    # updates that column, and on_ams_change at main.py:1031-1054 still
    # fires the deferred config when a spool eventually appears. So the
    # SpoolBuddy weigh-then-assign-before-insert workflow continues to
    # work — just without the optimization of skipping a no-op MQTT call.
    #
    # state ∈ {9, 10} stays as an explicit short-circuit so we don't churn
    # a doomed MQTT push when the firmware has positively confirmed "no
    # spool" — and to keep the on_ams_change replay path as the single
    # source of truth for those slots.
    slot_is_definitely_empty = tray_state == 9 or tray_state == 10
    configured = False
    if not slot_is_definitely_empty:
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
    # pending_config is the "config not landed yet" UI marker. True when the
    # firmware said empty, OR when MQTT couldn't actually publish (printer
    # offline, no client, transient failure). on_ams_change replay re-fires
    # the config in either case once the AMS reports a non-empty fingerprint.
    pending_config = slot_is_definitely_empty or not configured

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


# ── Filament SKU Settings (reorder forecasting) ───────────────────────────────


class FilamentSkuSettingsResponse(BaseModel):
    id: int
    material: str
    subtype: str | None
    brand: str | None
    lead_time_days: int
    safety_margin_value: int
    safety_margin_unit: str
    alerts_snoozed: bool = False

    class Config:
        from_attributes = True


class FilamentSkuSettingsUpsert(BaseModel):
    material: str
    subtype: str | None = None
    brand: str | None = None
    lead_time_days: int = 0
    safety_margin_value: int = 14
    safety_margin_unit: str = "days"
    alerts_snoozed: bool = False


@router.get("/sku-settings", response_model=list[FilamentSkuSettingsResponse])
async def list_sku_settings(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequireAnyPermissionIfAuthEnabled(Permission.INVENTORY_READ, Permission.INVENTORY_FORECAST_READ),
):
    """List all filament SKU reorder settings."""
    from backend.app.models.filament_sku_settings import FilamentSkuSettings

    result = await db.execute(
        select(FilamentSkuSettings).order_by(FilamentSkuSettings.material, FilamentSkuSettings.brand)
    )
    return list(result.scalars().all())


@router.post("/sku-settings", response_model=FilamentSkuSettingsResponse)
async def upsert_sku_settings(
    data: FilamentSkuSettingsUpsert,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequireAnyPermissionIfAuthEnabled(
        Permission.INVENTORY_FORECAST_WRITE, Permission.INVENTORY_UPDATE
    ),
):
    """Create or update reorder settings for a filament SKU (material/subtype/brand)."""
    from backend.app.models.filament_sku_settings import FilamentSkuSettings

    result = await db.execute(
        select(FilamentSkuSettings).where(
            FilamentSkuSettings.material == data.material,
            FilamentSkuSettings.subtype == data.subtype,
            FilamentSkuSettings.brand == data.brand,
        )
    )
    row = result.scalar_one_or_none()
    if row:
        row.lead_time_days = data.lead_time_days
        row.safety_margin_value = data.safety_margin_value
        row.safety_margin_unit = data.safety_margin_unit
        row.alerts_snoozed = data.alerts_snoozed
    else:
        row = FilamentSkuSettings(
            material=data.material,
            subtype=data.subtype,
            brand=data.brand,
            lead_time_days=data.lead_time_days,
            safety_margin_value=data.safety_margin_value,
            safety_margin_unit=data.safety_margin_unit,
            alerts_snoozed=data.alerts_snoozed,
        )
        db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


# ── Shopping List ─────────────────────────────────────────────────────────────


class ShoppingListItemResponse(BaseModel):
    id: int
    material: str
    subtype: str | None
    brand: str | None
    quantity_spools: int
    note: str | None
    status: str
    purchased_at: str | None
    added_at: str

    class Config:
        from_attributes = True


class ShoppingListItemCreate(BaseModel):
    material: str
    subtype: str | None = None
    brand: str | None = None
    quantity_spools: int = 1
    note: str | None = None


class ShoppingListItemStatusUpdate(BaseModel):
    status: str  # pending | purchased | received


@router.get("/shopping-list", response_model=list[ShoppingListItemResponse])
async def get_shopping_list(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequireAnyPermissionIfAuthEnabled(Permission.INVENTORY_READ, Permission.INVENTORY_FORECAST_READ),
):
    """Get the filament shopping list."""
    from backend.app.models.shopping_list import ShoppingListItem

    result = await db.execute(select(ShoppingListItem).order_by(ShoppingListItem.added_at.desc()))
    items = result.scalars().all()
    return [
        ShoppingListItemResponse(
            id=i.id,
            material=i.material,
            subtype=i.subtype,
            brand=i.brand,
            quantity_spools=i.quantity_spools,
            note=i.note,
            status=i.status or "pending",
            purchased_at=i.purchased_at.isoformat() if i.purchased_at else None,
            added_at=i.added_at.isoformat() if i.added_at else "",
        )
        for i in items
    ]


@router.post("/shopping-list", response_model=ShoppingListItemResponse)
async def add_to_shopping_list(
    data: ShoppingListItemCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequireAnyPermissionIfAuthEnabled(
        Permission.INVENTORY_FORECAST_WRITE, Permission.INVENTORY_UPDATE
    ),
):
    """Add a filament SKU to the shopping list."""
    from backend.app.models.shopping_list import ShoppingListItem

    item = ShoppingListItem(
        material=data.material,
        subtype=data.subtype,
        brand=data.brand,
        quantity_spools=data.quantity_spools,
        note=data.note,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return ShoppingListItemResponse(
        id=item.id,
        material=item.material,
        subtype=item.subtype,
        brand=item.brand,
        quantity_spools=item.quantity_spools,
        note=item.note,
        status=item.status or "pending",
        purchased_at=item.purchased_at.isoformat() if item.purchased_at else None,
        added_at=item.added_at.isoformat() if item.added_at else "",
    )


@router.patch("/shopping-list/{item_id}/status", response_model=ShoppingListItemResponse)
async def update_shopping_list_status(
    item_id: int,
    data: ShoppingListItemStatusUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequireAnyPermissionIfAuthEnabled(
        Permission.INVENTORY_FORECAST_WRITE, Permission.INVENTORY_UPDATE
    ),
):
    """Update the purchase status of a shopping list item."""
    from datetime import datetime, timezone

    from backend.app.models.shopping_list import ShoppingListItem

    if data.status not in ("pending", "purchased", "received"):
        raise HTTPException(400, "Invalid status")

    result = await db.execute(select(ShoppingListItem).where(ShoppingListItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Item not found")

    item.status = data.status
    if data.status in ("purchased", "received") and item.purchased_at is None:
        item.purchased_at = datetime.now(timezone.utc)
    elif data.status == "pending":
        item.purchased_at = None

    await db.commit()
    await db.refresh(item)
    return ShoppingListItemResponse(
        id=item.id,
        material=item.material,
        subtype=item.subtype,
        brand=item.brand,
        quantity_spools=item.quantity_spools,
        note=item.note,
        status=item.status or "pending",
        purchased_at=item.purchased_at.isoformat() if item.purchased_at else None,
        added_at=item.added_at.isoformat() if item.added_at else "",
    )


@router.delete("/shopping-list/{item_id}")
async def remove_from_shopping_list(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequireAnyPermissionIfAuthEnabled(
        Permission.INVENTORY_FORECAST_WRITE, Permission.INVENTORY_UPDATE
    ),
):
    """Remove a single item from the shopping list."""
    from backend.app.models.shopping_list import ShoppingListItem

    result = await db.execute(select(ShoppingListItem).where(ShoppingListItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Item not found")
    await db.delete(item)
    await db.commit()
    return {"status": "deleted"}


@router.delete("/shopping-list")
async def clear_shopping_list(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequireAnyPermissionIfAuthEnabled(
        Permission.INVENTORY_FORECAST_WRITE, Permission.INVENTORY_UPDATE
    ),
):
    """Clear all items from the shopping list."""
    from backend.app.models.shopping_list import ShoppingListItem

    result = await db.execute(delete(ShoppingListItem).returning(ShoppingListItem.id))
    deleted = len(result.fetchall())
    await db.commit()
    return {"deleted": deleted}


class CreateSpoolFromSlotRequest(BaseModel):
    printer_id: int
    ams_id: int
    tray_id: int


@router.post("/spools/from-slot", response_model=SpoolResponse)
async def create_spool_from_slot(
    req: CreateSpoolFromSlotRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Explicit user action: create an inventory spool from an AMS slot's current tray data.

    Used by the "+ Add to inventory" affordance when auto_add_unknown_rfid is disabled —
    the user looked at the slot and chose to register it. Also assigns the new spool
    to the slot in the same call.
    """
    from backend.app.services.printer_manager import printer_manager
    from backend.app.services.spool_tag_matcher import auto_assign_spool, create_spool_from_tray

    state = printer_manager.get_status(req.printer_id)
    if not state or not state.raw_data:
        raise HTTPException(status_code=404, detail="Printer not connected or no state available")

    ams_data = state.raw_data.get("ams")
    ams_units: list[dict] = []
    if isinstance(ams_data, list):
        ams_units = ams_data
    elif isinstance(ams_data, dict):
        if "ams" in ams_data and isinstance(ams_data["ams"], list):
            ams_units = ams_data["ams"]
        elif "tray" in ams_data:
            ams_units = [{"id": 0, "tray": ams_data.get("tray", [])}]

    tray: dict | None = None
    for unit in ams_units:
        if not isinstance(unit, dict):
            continue
        if int(unit.get("id", -1)) != req.ams_id:
            continue
        for t in unit.get("tray", []):
            if isinstance(t, dict) and int(t.get("id", -1)) == req.tray_id:
                tray = t
                break
        if tray:
            break

    if not tray or not tray.get("tray_type"):
        raise HTTPException(status_code=400, detail="Slot is empty or has no readable tray data")

    spool = await create_spool_from_tray(db, tray)
    await auto_assign_spool(
        req.printer_id,
        req.ams_id,
        req.tray_id,
        spool,
        printer_manager,
        db,
        tray_info_idx=tray.get("tray_info_idx", ""),
    )
    await db.commit()
    await ws_manager.broadcast({"type": "inventory_changed"})
    await ws_manager.broadcast(
        {
            "type": "spool_auto_assigned",
            "printer_id": req.printer_id,
            "ams_id": req.ams_id,
            "tray_id": req.tray_id,
            "spool_id": spool.id,
        }
    )
    result = await db.execute(select(Spool).options(selectinload(Spool.k_profiles)).where(Spool.id == spool.id))
    return result.scalar_one()
