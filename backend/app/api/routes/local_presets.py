"""API routes for local slicer presets (imported from OrcaSlicer, etc.)."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.local_preset import LocalPreset
from backend.app.models.user import User
from backend.app.schemas.local_preset import (
    ImportResponse,
    LocalPresetCreate,
    LocalPresetDetail,
    LocalPresetResponse,
    LocalPresetsResponse,
    LocalPresetUpdate,
)
from backend.app.services.orca_profiles import (
    extract_core_fields,
    get_cache_status,
    import_orca_file,
    reclassify_presets,
    refresh_base_cache,
    resolve_preset,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/local-presets", tags=["Local Presets"])


@router.get("/", response_model=LocalPresetsResponse)
async def list_local_presets(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """List all local presets grouped by type."""
    result = await db.execute(select(LocalPreset).order_by(LocalPreset.name))
    presets = result.scalars().all()

    grouped = LocalPresetsResponse()
    for p in presets:
        resp = LocalPresetResponse.model_validate(p)
        if p.preset_type == "filament":
            grouped.filament.append(resp)
        elif p.preset_type == "printer":
            grouped.printer.append(resp)
        elif p.preset_type == "process":
            grouped.process.append(resp)

    return grouped


@router.get("/{preset_id}", response_model=LocalPresetDetail)
async def get_local_preset(
    preset_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Get full detail for a local preset including the setting JSON."""
    result = await db.execute(select(LocalPreset).where(LocalPreset.id == preset_id))
    preset = result.scalar_one_or_none()
    if not preset:
        raise HTTPException(404, "Local preset not found")

    data = LocalPresetResponse.model_validate(preset).model_dump()
    try:
        data["setting"] = json.loads(preset.setting)
    except Exception:
        data["setting"] = {}

    return LocalPresetDetail(**data)


@router.post("/import", response_model=ImportResponse)
async def import_presets(
    file: UploadFile,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Import presets from an OrcaSlicer export file (.json, .orca_filament, .bbscfg, .bbsflmt, .zip)."""
    if not file.filename:
        raise HTTPException(400, "No filename provided")

    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file")

    result = await import_orca_file(file.filename, content, db)
    return ImportResponse(**result)


@router.post("/", response_model=LocalPresetResponse)
async def create_local_preset(
    data: LocalPresetCreate,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Manually create a local preset."""
    if data.preset_type not in ("filament", "printer", "process"):
        raise HTTPException(400, "preset_type must be filament, printer, or process")

    # Extract core fields
    core = extract_core_fields(data.setting)

    preset = LocalPreset(
        name=data.name,
        preset_type=data.preset_type,
        source="manual",
        setting=json.dumps(data.setting),
        **core,
    )
    db.add(preset)
    await db.flush()
    await db.refresh(preset)
    return LocalPresetResponse.model_validate(preset)


@router.put("/{preset_id}", response_model=LocalPresetResponse)
async def update_local_preset(
    preset_id: int,
    data: LocalPresetUpdate,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Update a local preset's name or settings."""
    result = await db.execute(select(LocalPreset).where(LocalPreset.id == preset_id))
    preset = result.scalar_one_or_none()
    if not preset:
        raise HTTPException(404, "Local preset not found")

    if data.name is not None:
        preset.name = data.name

    if data.setting is not None:
        # Re-resolve and extract core fields
        resolved = await resolve_preset(data.setting, preset.preset_type, db)
        core = extract_core_fields(resolved)
        preset.setting = json.dumps(resolved)
        preset.filament_type = core.get("filament_type")
        preset.filament_vendor = core.get("filament_vendor")
        preset.nozzle_temp_min = core.get("nozzle_temp_min")
        preset.nozzle_temp_max = core.get("nozzle_temp_max")
        preset.pressure_advance = core.get("pressure_advance")
        preset.default_filament_colour = core.get("default_filament_colour")
        preset.filament_cost = core.get("filament_cost")
        preset.filament_density = core.get("filament_density")
        preset.compatible_printers = core.get("compatible_printers")

    await db.flush()
    await db.refresh(preset)
    return LocalPresetResponse.model_validate(preset)


@router.delete("/{preset_id}")
async def delete_local_preset(
    preset_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Delete a local preset."""
    result = await db.execute(select(LocalPreset).where(LocalPreset.id == preset_id))
    preset = result.scalar_one_or_none()
    if not preset:
        raise HTTPException(404, "Local preset not found")

    await db.delete(preset)
    return {"success": True}


@router.get("/base-cache/status")
async def base_cache_status(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Get the status of the OrcaSlicer base profile cache."""
    return await get_cache_status(db)


@router.post("/base-cache/refresh")
async def refresh_cache(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Force refresh all cached base profiles from GitHub."""
    return await refresh_base_cache(db)


@router.post("/reclassify")
async def reclassify(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Re-evaluate preset types for all local presets using the improved heuristic."""
    return await reclassify_presets(db)
