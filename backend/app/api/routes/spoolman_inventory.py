"""Spoolman inventory proxy endpoints.

Translates between Spoolman's data model and Bambuddy's internal
InventorySpool format so the frontend can use a single unified inventory UI
regardless of whether data comes from the local database or Spoolman.
"""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes._spoolman_helpers import _map_spoolman_spool
from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.services.spoolman import SpoolmanClient, get_spoolman_client, init_spoolman_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/spoolman/inventory", tags=["spoolman-inventory"])


# Cache the last successful health-check timestamp to avoid a round-trip on
# every request.  A failed check clears the cache immediately.
_health_check_cache: dict[str, float] = {}
_HEALTH_CHECK_TTL = 30.0  # seconds


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

    # Reject non-http(s) schemes to prevent SSRF via file://, ftp://, etc.
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Spoolman URL must use http or https")

    # Re-use the existing client when the URL is unchanged; reinitialise only
    # when the URL was changed in settings (TOCTOU guard).
    client = await get_spoolman_client()
    if not client or client.base_url != url.rstrip("/"):
        client = await init_spoolman_client(url)

    # Only call health_check() when the cached result has expired.
    now = time.monotonic()
    last_ok = _health_check_cache.get(url, 0.0)
    if now - last_ok > _HEALTH_CHECK_TTL:
        if not await client.health_check():
            _health_check_cache.pop(url, None)
            raise HTTPException(status_code=503, detail="Spoolman server is not reachable")
        _health_check_cache[url] = now

    return client


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


_HEX_RE = re.compile(r"^[0-9A-Fa-f]{6,8}$")


def _validate_rgba(v: str | None) -> str | None:
    if v is None:
        return v
    clean = v.lstrip("#")
    if not _HEX_RE.match(clean):
        raise ValueError("rgba must be a 6 or 8 character hex string (RRGGBB or RRGGBBAA)")
    return clean.upper()


class SpoolmanInventoryCreate(BaseModel):
    material: str = Field(..., min_length=1, max_length=64)
    subtype: str | None = Field(None, max_length=64)
    brand: str | None = Field(None, max_length=128)
    rgba: str | None = Field(None, max_length=9)
    label_weight: int = Field(1000, ge=1, le=100_000)
    core_weight: int = Field(250, ge=0, le=10_000)
    weight_used: float = Field(0.0, ge=0.0, le=100_000.0)
    note: str | None = Field(None, max_length=1000)
    cost_per_kg: float | None = Field(None, ge=0.0, le=1_000_000.0)
    storage_location: str | None = Field(None, max_length=255)

    @field_validator("rgba")
    @classmethod
    def validate_rgba(cls, v: str | None) -> str | None:
        return _validate_rgba(v)


class SpoolmanInventoryUpdate(BaseModel):
    material: str | None = Field(None, min_length=1, max_length=64)
    subtype: str | None = Field(None, max_length=64)
    brand: str | None = Field(None, max_length=128)
    rgba: str | None = Field(None, max_length=9)
    label_weight: int | None = Field(None, ge=1, le=100_000)
    core_weight: int | None = Field(None, ge=0, le=10_000)
    weight_used: float | None = Field(None, ge=0.0, le=100_000.0)
    note: str | None = Field(None, max_length=1000)
    cost_per_kg: float | None = Field(None, ge=0.0, le=1_000_000.0)
    tag_uid: str | None = Field(None, max_length=64)
    tray_uuid: str | None = Field(None, max_length=64)
    storage_location: str | None = Field(None, max_length=255)

    @field_validator("rgba")
    @classmethod
    def validate_rgba(cls, v: str | None) -> str | None:
        return _validate_rgba(v)


class SpoolmanInventoryBulkCreate(BaseModel):
    spool: SpoolmanInventoryCreate
    quantity: int = 1

    @field_validator("quantity")
    @classmethod
    def clamp_quantity(cls, v: int) -> int:
        return max(1, min(v, 50))


class SpoolWeightUpdate(BaseModel):
    weight_grams: float = Field(..., ge=0.0, le=100_000.0)


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
        location=data.storage_location or None,
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
            location=data.storage_location or None,
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
    storage_location_changed = "storage_location" in data.model_fields_set
    storage_location = data.storage_location if storage_location_changed else current.get("location")

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

    # When the caller explicitly sets tag_uid/tray_uuid to null (i.e. tag removal),
    # clear the extra.tag field stored in Spoolman.
    tag_nulled = (
        ("tag_uid" in data.model_fields_set or "tray_uuid" in data.model_fields_set)
        and data.tag_uid is None
        and data.tray_uuid is None
    )
    extra = {} if tag_nulled else None

    updated = await client.update_spool_full(
        spool_id=spool_id,
        filament_id=filament_id,
        remaining_weight=remaining,
        comment=note or "",
        price=data.cost_per_kg,
        extra=extra,
        location=storage_location or None,
        clear_location=storage_location_changed and storage_location is None,
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
