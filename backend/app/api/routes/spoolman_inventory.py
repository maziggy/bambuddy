"""Spoolman inventory proxy endpoints.

Translates between Spoolman's data model and Bambuddy's internal
InventorySpool format so the frontend can use a single unified inventory UI
regardless of whether data comes from the local database or Spoolman.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.services.spoolman import SpoolmanClient, get_spoolman_client, init_spoolman_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/spoolman/inventory", tags=["spoolman-inventory"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _map_spoolman_spool(spool: dict) -> dict:
    """Convert a raw Spoolman spool dict to the InventorySpool-compatible format.

    Fields not supported by Spoolman (k_profiles, slicer_filament, …) are
    returned as None / empty so the frontend can still render them without
    errors.  The ``data_origin`` field is set to ``"spoolman"`` so UI code can
    distinguish these spools from local ones.
    """
    filament: dict = spool.get("filament") or {}
    vendor: dict = filament.get("vendor") or {}
    extra: dict = spool.get("extra") or {}

    # RFID tag stored as JSON-encoded string in Spoolman extra.tag
    raw_tag: str = (extra.get("tag") or "").strip('"').upper()
    tag_uid = raw_tag if len(raw_tag) == 16 else None
    tray_uuid = raw_tag if len(raw_tag) == 32 else None

    # Subtype = filament name with material prefix stripped
    material: str = (filament.get("material") or "").strip()
    filament_name: str = (filament.get("name") or "").strip()
    if material and filament_name.upper().startswith(material.upper()):
        subtype: str | None = filament_name[len(material) :].strip() or None
    else:
        subtype = filament_name or None

    # Colour: Spoolman stores RRGGBB, Bambuddy uses RRGGBBAA
    color_hex: str = (filament.get("color_hex") or "808080").upper().lstrip("#")
    rgba: str = (color_hex + "FF")[:8]

    label_weight: int = int(filament.get("weight") or 1000)
    used_weight: float = float(spool.get("used_weight") or 0)

    # Archived state – Spoolman uses a boolean ``archived`` field
    archived: bool = spool.get("archived", False)
    archived_at: str | None = None
    if archived:
        archived_at = spool.get("last_used") or spool.get("registered")
        if not archived_at:
            archived_at = datetime.now(timezone.utc).isoformat()

    created_at: str = spool.get("registered") or datetime.now(timezone.utc).isoformat()

    return {
        "id": spool["id"],
        "material": material,
        "subtype": subtype,
        "color_name": None,
        "rgba": rgba,
        "brand": vendor.get("name") or None,
        "label_weight": label_weight,
        "core_weight": 250,
        "core_weight_catalog_id": None,
        "weight_used": used_weight,
        "weight_locked": False,
        "last_scale_weight": None,
        "last_weighed_at": None,
        # slicer_filament_name carries the Spoolman filament name for display
        "slicer_filament": None,
        "slicer_filament_name": filament_name or None,
        "nozzle_temp_min": None,
        "nozzle_temp_max": None,
        "note": spool.get("comment") or None,
        "added_full": None,
        "last_used": spool.get("last_used"),
        "encode_time": spool.get("first_used"),
        "tag_uid": tag_uid,
        "tray_uuid": tray_uuid,
        "data_origin": "spoolman",
        "tag_type": "spoolman",
        "archived_at": archived_at,
        "created_at": created_at,
        "updated_at": created_at,
        "cost_per_kg": spool.get("price") or None,
        # spoolman_location is an extra field (not in the local InventorySpool
        # schema) used to display the Spoolman location text in the UI.
        "spoolman_location": spool.get("location") or None,
        "k_profiles": [],
    }


async def _get_client(db: AsyncSession) -> SpoolmanClient:
    """Return an authenticated Spoolman client or raise an HTTP error."""
    result = await db.execute(select(Settings))
    settings: dict[str, str] = {s.key: s.value for s in result.scalars().all()}

    enabled = settings.get("spoolman_enabled", "false").lower() == "true"
    url = settings.get("spoolman_url", "").strip()

    if not enabled:
        raise HTTPException(status_code=400, detail="Spoolman integration is not enabled")
    if not url:
        raise HTTPException(status_code=400, detail="Spoolman URL is not configured")

    client = await get_spoolman_client()
    if not client:
        client = await init_spoolman_client(url)

    if not await client.health_check():
        raise HTTPException(status_code=503, detail="Spoolman server is not reachable")

    return client


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class SpoolmanInventoryCreate(BaseModel):
    material: str
    subtype: str | None = None
    brand: str | None = None
    rgba: str | None = None
    label_weight: int = 1000
    core_weight: int = 250
    weight_used: float = 0.0
    note: str | None = None
    cost_per_kg: float | None = None


class SpoolmanInventoryUpdate(BaseModel):
    material: str | None = None
    subtype: str | None = None
    brand: str | None = None
    rgba: str | None = None
    label_weight: int | None = None
    core_weight: int | None = None
    weight_used: float | None = None
    note: str | None = None
    cost_per_kg: float | None = None


class SpoolmanInventoryBulkCreate(BaseModel):
    spool: SpoolmanInventoryCreate
    quantity: int = 1

    @field_validator("quantity")
    @classmethod
    def clamp_quantity(cls, v: int) -> int:
        return max(1, min(v, 50))


class SpoolWeightUpdate(BaseModel):
    weight_grams: float


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/spools")
async def list_spools(
    include_archived: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
) -> list[dict]:
    """Return all Spoolman spools in the InventorySpool format."""
    client = await _get_client(db)
    spools = await client.get_all_spools(allow_archived=include_archived)
    return [_map_spoolman_spool(s) for s in spools]


@router.get("/spools/{spool_id}")
async def get_spool(
    spool_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
) -> dict:
    """Return a single Spoolman spool in the InventorySpool format."""
    client = await _get_client(db)
    spool = await client.get_spool(spool_id)
    if not spool:
        raise HTTPException(status_code=404, detail="Spool not found in Spoolman")
    return _map_spoolman_spool(spool)


@router.post("/spools")
async def create_spool(
    data: SpoolmanInventoryCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> dict:
    """Create a new spool in Spoolman, auto-creating vendor and filament as needed."""
    client = await _get_client(db)

    color_hex = (data.rgba or "808080FF")[:6]
    filament_id = await client.find_or_create_filament(
        material=data.material,
        subtype=data.subtype or "",
        brand=data.brand,
        color_hex=color_hex,
        label_weight=data.label_weight,
    )
    if not filament_id:
        raise HTTPException(status_code=500, detail="Failed to find or create filament in Spoolman")

    remaining = max(0.0, data.label_weight - data.weight_used)
    spool = await client.create_spool(
        filament_id=filament_id,
        remaining_weight=remaining,
        comment=data.note or None,
    )
    if not spool:
        raise HTTPException(status_code=500, detail="Failed to create spool in Spoolman")

    return _map_spoolman_spool(spool)


@router.post("/spools/bulk")
async def bulk_create_spools(
    payload: SpoolmanInventoryBulkCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> list[dict]:
    """Create multiple identical spools in Spoolman."""
    client = await _get_client(db)
    data = payload.spool

    color_hex = (data.rgba or "808080FF")[:6]
    filament_id = await client.find_or_create_filament(
        material=data.material,
        subtype=data.subtype or "",
        brand=data.brand,
        color_hex=color_hex,
        label_weight=data.label_weight,
    )
    if not filament_id:
        raise HTTPException(status_code=500, detail="Failed to find or create filament in Spoolman")

    remaining = max(0.0, data.label_weight - data.weight_used)
    created: list[dict] = []
    for _ in range(payload.quantity):
        spool = await client.create_spool(
            filament_id=filament_id,
            remaining_weight=remaining,
            comment=data.note or None,
        )
        if spool:
            created.append(_map_spoolman_spool(spool))

    if not created:
        raise HTTPException(status_code=500, detail="Failed to create any spools in Spoolman")

    return created


@router.patch("/spools/{spool_id}")
async def update_spool(
    spool_id: int,
    data: SpoolmanInventoryUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> dict:
    """Update an existing Spoolman spool, re-linking the filament if metadata changed."""
    client = await _get_client(db)

    current = await client.get_spool(spool_id)
    if not current:
        raise HTTPException(status_code=404, detail="Spool not found in Spoolman")

    cur_filament: dict = current.get("filament") or {}
    cur_vendor: dict = cur_filament.get("vendor") or {}
    cur_mat: str = (cur_filament.get("material") or "").strip()
    cur_name: str = (cur_filament.get("name") or "").strip()
    if cur_mat and cur_name.upper().startswith(cur_mat.upper()):
        cur_subtype: str = cur_name[len(cur_mat) :].strip()
    else:
        cur_subtype = cur_name

    # Resolve final values: use request value if provided, else keep current
    material = data.material if data.material is not None else cur_mat
    subtype = data.subtype if data.subtype is not None else cur_subtype
    brand = data.brand if data.brand is not None else (cur_vendor.get("name") or None)
    cur_color = (cur_filament.get("color_hex") or "808080").upper().lstrip("#")
    rgba = data.rgba if data.rgba is not None else (cur_color + "FF")
    label_weight = data.label_weight if data.label_weight is not None else int(cur_filament.get("weight") or 1000)
    weight_used = data.weight_used if data.weight_used is not None else float(current.get("used_weight") or 0)
    note = data.note if data.note is not None else current.get("comment")

    color_hex = rgba[:6]
    filament_id = await client.find_or_create_filament(
        material=material,
        subtype=subtype or "",
        brand=brand,
        color_hex=color_hex,
        label_weight=label_weight,
    )
    if not filament_id:
        raise HTTPException(status_code=500, detail="Failed to find or create filament in Spoolman")

    remaining = max(0.0, label_weight - weight_used)
    updated = await client.update_spool_full(
        spool_id=spool_id,
        filament_id=filament_id,
        remaining_weight=remaining,
        comment=note or "",
        price=data.cost_per_kg,
    )
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update spool in Spoolman")

    return _map_spoolman_spool(updated)


@router.delete("/spools/{spool_id}")
async def delete_spool(
    spool_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> dict:
    """Permanently delete a spool from Spoolman."""
    client = await _get_client(db)
    success = await client.delete_spool(spool_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete spool from Spoolman")
    return {"status": "deleted"}


@router.post("/spools/{spool_id}/archive")
async def archive_spool(
    spool_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> dict:
    """Archive a spool in Spoolman (soft-delete)."""
    client = await _get_client(db)
    spool = await client.set_spool_archived(spool_id, archived=True)
    if not spool:
        raise HTTPException(status_code=500, detail="Failed to archive spool in Spoolman")
    return _map_spoolman_spool(spool)


@router.post("/spools/{spool_id}/restore")
async def restore_spool(
    spool_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> dict:
    """Restore an archived spool in Spoolman."""
    client = await _get_client(db)
    spool = await client.set_spool_archived(spool_id, archived=False)
    if not spool:
        raise HTTPException(status_code=500, detail="Failed to restore spool in Spoolman")
    return _map_spoolman_spool(spool)


@router.patch("/spools/{spool_id}/weight")
async def sync_spool_weight(
    spool_id: int,
    data: SpoolWeightUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> dict:
    """Update a spool's remaining weight from a measured gross weight.

    Computes remaining = gross_weight - core_weight (250 g default) and
    updates Spoolman accordingly.
    """
    client = await _get_client(db)

    current = await client.get_spool(spool_id)
    if not current:
        raise HTTPException(status_code=404, detail="Spool not found in Spoolman")

    core_weight = 250.0
    remaining = max(0.0, data.weight_grams - core_weight)

    updated = await client.update_spool_full(spool_id=spool_id, remaining_weight=remaining)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update spool weight in Spoolman")

    cur_filament = updated.get("filament") or {}
    label_weight = int(cur_filament.get("weight") or 1000)
    weight_used = max(0.0, label_weight - remaining)
    return {"status": "ok", "weight_used": weight_used}
