"""Spoolman inventory proxy endpoints.

Translates between Spoolman's data model and Bambuddy's internal
InventorySpool format so the frontend can use a single unified inventory UI
regardless of whether data comes from the local database or Spoolman.
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes._spoolman_helpers import (
    _map_spoolman_spool,
    _safe_float,
    _safe_int,
    assert_safe_spoolman_url,
)
from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.models.user import User
from backend.app.services.spoolman import (
    SpoolmanClient,
    SpoolmanClientError,
    SpoolmanNotFoundError,
    SpoolmanUnavailableError,
    get_spoolman_client,
    init_spoolman_client,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/spoolman/inventory", tags=["spoolman-inventory"])


# Cache the last successful health-check timestamp to avoid a round-trip on
# every request.  A failed check clears the cache immediately.
_health_check_cache: dict[str, float] = {}
_HEALTH_CHECK_TTL = 30.0  # seconds


def _tag_cleared(val: str | None) -> bool:
    """Return True when a PATCH field explicitly removes a tag (null or empty string)."""
    return val is None or val == ""


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

    # SSRF guard: reject dangerous schemes and bare private/loopback/link-local/multicast IPs.
    # Raises ValueError with a descriptive message on any violation.
    try:
        assert_safe_spoolman_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Re-use the cached client when URL is unchanged; reinitialise on URL change (cache invalidation).
    client = await get_spoolman_client()
    if not client or client.base_url != url.rstrip("/"):
        try:
            client = await init_spoolman_client(url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Only call health_check() when the cached result has expired.
    # Evict stale entries when URL changes (only one Spoolman URL is active at a time).
    if url not in _health_check_cache and _health_check_cache:
        _health_check_cache.clear()
    now = time.monotonic()
    last_ok = _health_check_cache.get(url, 0.0)
    if now - last_ok > _HEALTH_CHECK_TTL:
        if not await client.health_check():
            _health_check_cache.pop(url, None)
            raise HTTPException(status_code=503, detail="Spoolman server is not reachable")
        _health_check_cache[url] = now

    return client


@asynccontextmanager
async def _translate_spoolman_errors():
    """Translate Spoolman typed exceptions to HTTP errors for all inventory endpoints."""
    try:
        yield
    except SpoolmanNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Spool not found in Spoolman") from exc
    except SpoolmanClientError as exc:
        raise HTTPException(status_code=502, detail="Spoolman rejected the request") from exc
    except SpoolmanUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Spoolman server is not reachable") from exc


async def _apply_price_if_set(
    client: SpoolmanClient, spool: dict, cost_per_kg: float | None
) -> tuple[dict, list[str]]:
    """Patch the spool price; return (updated_spool, warnings).

    Returns the original spool and a non-empty warnings list when the price
    update fails, so the caller can return HTTP 207 instead of silently
    discarding the price.
    """
    if cost_per_kg is None:
        return spool, []
    try:
        async with _translate_spoolman_errors():
            updated = await client.update_spool_full(spool["id"], price=cost_per_kg)
        return updated, []
    except HTTPException:
        logger.warning(
            "Price update failed for spool %d; spool created without price (cost_per_kg=%s)",
            spool["id"],
            cost_per_kg,
        )
        return spool, ["price_not_set: Spoolman rejected the price update"]


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


_HEX_RE = re.compile(r"^[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")


def _validate_rgba(v: str | None) -> str | None:
    if v is None:
        return v
    clean = v.removeprefix("#")
    if not _HEX_RE.match(clean):
        raise ValueError("rgba must be a 6 or 8 character hex string (RRGGBB or RRGGBBAA)")
    return clean.upper()


def _validate_storage_location(v: str | None) -> str | None:
    if v is not None and any(c in v for c in ("\r", "\n", "\x00")):
        raise ValueError("storage_location must not contain control characters")
    return v


class SpoolmanInventoryCreate(BaseModel):
    material: str = Field(..., min_length=1, max_length=64)
    subtype: str | None = Field(None, max_length=64)
    brand: str | None = Field(None, max_length=128)
    color_name: str | None = Field(None, max_length=64)
    rgba: str | None = Field(None, max_length=8, description="6-digit hex (RRGGBB) or 8-digit (RRGGBBAA)")
    label_weight: int = Field(1000, ge=1, le=100_000)
    core_weight: int = Field(
        250, ge=0, le=10_000
    )  # Accepted for schema parity but not persisted to Spoolman (stored on filament type, not spool)
    weight_used: float = Field(0.0, ge=0.0, le=100_000.0)
    note: str | None = Field(None, max_length=1000)
    cost_per_kg: float | None = Field(None, ge=0.0, le=1_000_000.0)
    storage_location: str | None = Field(None, max_length=255)

    @field_validator("rgba")
    @classmethod
    def validate_rgba(cls, v: str | None) -> str | None:
        return _validate_rgba(v)

    @field_validator("storage_location")
    @classmethod
    def validate_storage_location(cls, v: str | None) -> str | None:
        return _validate_storage_location(v)

    @model_validator(mode="after")
    def validate_weight_consistency(self) -> SpoolmanInventoryCreate:
        if self.weight_used > self.label_weight:
            raise ValueError("weight_used must not exceed label_weight")
        if self.core_weight != 250:
            raise ValueError(
                "core_weight is not persisted in Spoolman (stored on the filament type, not the spool). "
                "Omit this field or leave it at the default (250 g)."
            )
        return self


class SpoolmanInventoryUpdate(BaseModel):
    material: str | None = Field(None, min_length=1, max_length=64)
    subtype: str | None = Field(None, max_length=64)
    brand: str | None = Field(None, max_length=128)
    color_name: str | None = Field(None, max_length=64)
    rgba: str | None = Field(None, max_length=8, description="6-digit hex (RRGGBB) or 8-digit (RRGGBBAA)")
    label_weight: int | None = Field(None, ge=1, le=100_000)
    core_weight: int | None = Field(
        None, ge=0, le=10_000
    )  # Accepted for schema parity but not persisted to Spoolman (stored on filament type, not spool)
    weight_used: float | None = Field(None, ge=0.0, le=100_000.0)
    note: str | None = Field(None, max_length=1000)
    cost_per_kg: float | None = Field(None, ge=0.0, le=1_000_000.0)
    tag_uid: str | None = Field(None, min_length=8, max_length=30, pattern=r"^[0-9A-Fa-f]+$")
    tray_uuid: str | None = Field(None, min_length=32, max_length=32, pattern=r"^[0-9A-Fa-f]+$")
    storage_location: str | None = Field(None, max_length=255)

    @field_validator("rgba")
    @classmethod
    def validate_rgba(cls, v: str | None) -> str | None:
        return _validate_rgba(v)

    @field_validator("storage_location")
    @classmethod
    def validate_storage_location(cls, v: str | None) -> str | None:
        return _validate_storage_location(v)

    @model_validator(mode="after")
    def validate_weight_consistency(self) -> SpoolmanInventoryUpdate:
        if self.weight_used is not None and self.label_weight is not None:
            if self.weight_used > self.label_weight:
                raise ValueError("weight_used must not exceed label_weight")
        return self


class SpoolmanInventoryBulkCreate(BaseModel):
    spool: SpoolmanInventoryCreate
    quantity: int = Field(1, ge=1, le=50)


class SpoolWeightUpdate(BaseModel):
    weight_grams: float = Field(..., ge=0.0, le=100_000.0)


class SpoolSlotAssignmentRequest(BaseModel):
    spoolman_spool_id: int = Field(..., gt=0)
    printer_id: int = Field(..., gt=0)
    ams_id: int = Field(..., ge=0, le=7)
    tray_id: int = Field(..., ge=0, le=3)


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
    async with _translate_spoolman_errors():
        spools = await client.get_all_spools(allow_archived=include_archived)
    result = []
    for s in spools:
        try:
            result.append(_map_spoolman_spool(s))
        except ValueError as exc:
            logger.warning("Skipping malformed Spoolman spool (id=%r): %s", s.get("id"), exc)
    return result


@router.get("/spools/{spool_id}")
async def get_spool(
    spool_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
) -> dict:
    """Return a single Spoolman spool in the InventorySpool format."""
    client = await _get_client(db)
    async with _translate_spoolman_errors():
        spool = await client.get_spool(spool_id)
    try:
        return _map_spoolman_spool(spool)
    except ValueError as exc:
        logger.warning("Malformed Spoolman spool (id=%r): %s", spool_id, exc)
        raise HTTPException(status_code=502, detail="Spoolman returned malformed spool data") from exc


@router.post("/spools")
async def create_spool(
    data: SpoolmanInventoryCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> dict:
    """Create a new spool in Spoolman, auto-creating vendor and filament as needed."""
    client = await _get_client(db)

    color_hex = (data.rgba or "808080FF")[:6]
    async with _translate_spoolman_errors():
        filament_id = await client.find_or_create_filament(
            material=data.material,
            subtype=data.subtype or "",
            brand=data.brand,
            color_hex=color_hex,
            label_weight=data.label_weight,
            color_name=data.color_name,
        )

    remaining = max(0.0, data.label_weight - data.weight_used)
    async with _translate_spoolman_errors():
        spool = await client.create_spool(
            filament_id=filament_id,
            remaining_weight=remaining,
            comment=data.note or None,
            location=data.storage_location or None,
        )

    spool, price_warnings = await _apply_price_if_set(client, spool, data.cost_per_kg)
    result = _map_spoolman_spool(spool)
    if price_warnings:
        return JSONResponse(status_code=207, content={**result, "warnings": price_warnings})
    return result


@router.post("/spools/bulk")
async def bulk_create_spools(
    payload: SpoolmanInventoryBulkCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> Response:
    """Create multiple identical spools in Spoolman."""
    client = await _get_client(db)
    data = payload.spool

    color_hex = (data.rgba or "808080FF")[:6]
    async with _translate_spoolman_errors():
        filament_id = await client.find_or_create_filament(
            material=data.material,
            subtype=data.subtype or "",
            brand=data.brand,
            color_hex=color_hex,
            label_weight=data.label_weight,
        )

    remaining = max(0.0, data.label_weight - data.weight_used)
    created: list[dict] = []
    failures: list[str] = []
    for _ in range(payload.quantity):
        try:
            spool = await client.create_spool(
                filament_id=filament_id,
                remaining_weight=remaining,
                comment=data.note or None,
                location=data.storage_location or None,
            )
        except (SpoolmanUnavailableError, SpoolmanClientError, SpoolmanNotFoundError) as exc:
            logger.warning("Bulk spool creation: one spool failed: %s", exc)
            failures.append(str(exc))
            continue
        spool, _ = await _apply_price_if_set(client, spool, data.cost_per_kg)
        created.append(_map_spoolman_spool(spool))

    if not created:
        raise HTTPException(status_code=500, detail="Failed to create any spools in Spoolman")

    if len(created) < payload.quantity:
        # Some spool creations failed — return 207 Multi-Status so the caller
        # can distinguish a full success from a partial one and show a useful message.
        return JSONResponse(
            status_code=207,
            content={
                "created": created,
                "requested_count": payload.quantity,
                "failed_count": payload.quantity - len(created),
                "failures": failures,
            },
        )

    return JSONResponse(status_code=200, content=created)


@router.patch("/spools/{spool_id}")
async def update_spool(
    *,
    spool_id: int = Path(..., gt=0),
    data: SpoolmanInventoryUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> dict:
    """Update an existing Spoolman spool, re-linking the filament if metadata changed."""
    client = await _get_client(db)

    async with _translate_spoolman_errors():
        current = await client.get_spool(spool_id)

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
    color_name = data.color_name if data.color_name is not None else (cur_filament.get("color_name") or None)
    cur_color = (cur_filament.get("color_hex") or "808080").upper().removeprefix("#")
    rgba = data.rgba if data.rgba is not None else (cur_color + "FF")
    label_weight = data.label_weight if data.label_weight is not None else int(cur_filament.get("weight") or 1000)
    weight_used = data.weight_used if data.weight_used is not None else float(current.get("used_weight") or 0)
    note = data.note if data.note is not None else current.get("comment")
    storage_location_changed = "storage_location" in data.model_fields_set
    storage_location = data.storage_location if storage_location_changed else current.get("location")

    color_hex = rgba[:6]
    async with _translate_spoolman_errors():
        filament_id = await client.find_or_create_filament(
            material=material,
            subtype=subtype or "",
            brand=brand,
            color_hex=color_hex,
            label_weight=label_weight,
            color_name=color_name,
        )
    if not filament_id:
        raise HTTPException(status_code=500, detail="Failed to find or create filament in Spoolman")

    remaining = max(0.0, label_weight - weight_used)

    # Tag removal: clear only the "tag" key so other custom Spoolman extra fields
    # set outside Bambuddy are preserved.
    tag_nulled = (
        ("tag_uid" in data.model_fields_set or "tray_uuid" in data.model_fields_set)
        and _tag_cleared(data.tag_uid)
        and _tag_cleared(data.tray_uuid)
    )

    # Serialise tag-clear + PATCH under the per-spool extra lock to prevent a
    # concurrent merge_spool_extra call (e.g. NFC write-back) from overwriting
    # the tag key between our read and our write.
    async with client.extra_lock(spool_id):
        if tag_nulled:
            # Re-fetch inside the lock so we work with fresh extra data.
            async with _translate_spoolman_errors():
                fresh = await client.get_spool(spool_id)
            cur_extra = dict(fresh.get("extra") or {})
            cur_extra.pop("tag", None)
            extra: dict | None = cur_extra
        else:
            extra = None

        async with _translate_spoolman_errors():
            updated = await client.update_spool_full(
                spool_id=spool_id,
                filament_id=filament_id,
                remaining_weight=remaining,
                comment=note or "",
                price=data.cost_per_kg,
                extra=extra,
                location=storage_location or None,
                clear_location=storage_location_changed and not storage_location,
            )

    return _map_spoolman_spool(updated)


@router.delete("/spools/{spool_id}")
async def delete_spool(
    spool_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> dict:
    """Permanently delete a spool from Spoolman."""
    client = await _get_client(db)
    async with _translate_spoolman_errors():
        await client.delete_spool(spool_id)
    return {"status": "deleted"}


@router.post("/spools/{spool_id}/archive")
async def archive_spool(
    spool_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> dict:
    """Archive a spool in Spoolman (soft-delete)."""
    client = await _get_client(db)
    async with _translate_spoolman_errors():
        spool = await client.set_spool_archived(spool_id, archived=True)
    try:
        return _map_spoolman_spool(spool)
    except ValueError as exc:
        logger.warning("Malformed Spoolman spool (id=%r): %s", spool_id, exc)
        raise HTTPException(status_code=502, detail="Spoolman returned malformed spool data") from exc


@router.post("/spools/{spool_id}/restore")
async def restore_spool(
    spool_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> dict:
    """Restore an archived spool in Spoolman."""
    client = await _get_client(db)
    async with _translate_spoolman_errors():
        spool = await client.set_spool_archived(spool_id, archived=False)
    try:
        return _map_spoolman_spool(spool)
    except ValueError as exc:
        logger.warning("Malformed Spoolman spool (id=%r): %s", spool_id, exc)
        raise HTTPException(status_code=502, detail="Spoolman returned malformed spool data") from exc


@router.patch("/spools/{spool_id}/weight")
async def sync_spool_weight(
    *,
    spool_id: int = Path(..., gt=0),
    data: SpoolWeightUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> dict:
    """Update a spool's remaining weight from a measured gross weight.

    Computes remaining = gross_weight - filament.spool_weight (empty-spool
    weight from Spoolman; falls back to 250 g when unset) and updates
    Spoolman accordingly.
    """
    client = await _get_client(db)

    async with _translate_spoolman_errors():
        current = await client.get_spool(spool_id)

    cur_filament = current.get("filament") or {}
    core_weight = _safe_float(cur_filament.get("spool_weight"), 250.0)
    remaining = max(0.0, data.weight_grams - core_weight)

    async with _translate_spoolman_errors():
        updated = await client.update_spool_full(spool_id=spool_id, remaining_weight=remaining)

    upd_filament = updated.get("filament") or {}
    label_weight = _safe_int(upd_filament.get("weight"), 1000)
    weight_used = max(0.0, label_weight - remaining)
    return {"status": "ok", "weight_used": weight_used}


@router.get("/slot-assignments/all")
async def get_all_spoolman_slot_assignments(
    printer_id: int | None = Query(None, gt=0),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
) -> list[dict]:
    """Return all Spoolman slot assignments, optionally filtered by printer.

    Each item is a raw assignment dict with keys ``printer_id``, ``ams_id``,
    ``tray_id``, and ``spoolman_spool_id`` — not an ``InventorySpool`` object.
    """
    query = select(SpoolmanSlotAssignment)
    if printer_id is not None:
        query = query.where(SpoolmanSlotAssignment.printer_id == printer_id)
    result = await db.execute(query)
    slots = result.scalars().all()
    return [
        {
            "printer_id": s.printer_id,
            "ams_id": s.ams_id,
            "tray_id": s.tray_id,
            "spoolman_spool_id": s.spoolman_spool_id,
        }
        for s in slots
    ]


@router.post("/slot-assignments")
async def assign_spoolman_slot(
    body: SpoolSlotAssignmentRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> dict:
    """Assign a Spoolman spool to a printer AMS slot (stored in local DB only).

    Raises 404 if the printer does not exist or the spool is not found in Spoolman.
    Spoolman's own ``spool.location`` field is NOT touched — it is user-managed.
    """

    client = await _get_client(db)
    result = await db.execute(select(Printer).where(Printer.id == body.printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")

    # Verify the Spoolman spool exists before committing to local DB.
    # This prevents ghost rows pointing at non-existent spool IDs.
    async with _translate_spoolman_errors():
        spool = await client.get_spool(body.spoolman_spool_id)

    # Spool confirmed in Spoolman — upsert into local slot-assignment table
    # assigned_at is intentionally not refreshed on re-assign (original timestamp preserved)
    try:
        await db.execute(
            text(
                "INSERT INTO spoolman_slot_assignments"
                " (printer_id, ams_id, tray_id, spoolman_spool_id)"
                " VALUES (:printer_id, :ams_id, :tray_id, :spool_id)"
                " ON CONFLICT(printer_id, ams_id, tray_id)"
                " DO UPDATE SET spoolman_spool_id = excluded.spoolman_spool_id"
            ),
            {
                "printer_id": body.printer_id,
                "ams_id": body.ams_id,
                "tray_id": body.tray_id,
                "spool_id": body.spoolman_spool_id,
            },
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.error("Failed to persist slot assignment: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to save slot assignment") from exc

    return _map_spoolman_spool(spool)


@router.delete("/slot-assignments/{spoolman_spool_id}")
async def unassign_spoolman_slot(
    spoolman_spool_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> dict:
    """Remove the local slot assignment for a Spoolman spool.

    Spoolman's own ``spool.location`` field is NOT touched — it is user-managed.
    """
    client = await _get_client(db)

    try:
        await db.execute(
            delete(SpoolmanSlotAssignment).where(SpoolmanSlotAssignment.spoolman_spool_id == spoolman_spool_id)
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.error("Failed to delete slot assignment: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to remove slot assignment") from exc

    # Fetch the spool from Spoolman to return in InventorySpool format.
    # If the spool no longer exists in Spoolman, the local unassignment still succeeded.
    try:
        async with _translate_spoolman_errors():
            spool = await client.get_spool(spoolman_spool_id)
        return _map_spoolman_spool(spool)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        # Spool no longer exists in Spoolman; unassignment still succeeded.
        return {"id": spoolman_spool_id}


@router.get("/slot-assignments")
async def get_spoolman_slot_assignment(
    printer_id: int = Query(..., gt=0),
    ams_id: int = Query(..., ge=0, le=7),
    tray_id: int = Query(..., ge=0, le=3),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
) -> dict | None:
    """Return the Spoolman spool assigned to a specific printer slot, or null if unassigned."""
    client = await _get_client(db)
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")

    slot_result = await db.execute(
        select(SpoolmanSlotAssignment).where(
            SpoolmanSlotAssignment.printer_id == printer_id,
            SpoolmanSlotAssignment.ams_id == ams_id,
            SpoolmanSlotAssignment.tray_id == tray_id,
        )
    )
    slot = slot_result.scalar_one_or_none()
    if not slot:
        return None

    try:
        async with _translate_spoolman_errors():
            spool = await client.get_spool(slot.spoolman_spool_id)
        return _map_spoolman_spool(spool)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        # Spool deleted in Spoolman — clean up stale assignment.
        # Include spoolman_spool_id in WHERE to avoid a TOCTOU race where a
        # concurrent re-assign changed the slot to a different spool between
        # the GET and this DELETE.
        try:
            await db.execute(
                delete(SpoolmanSlotAssignment).where(
                    SpoolmanSlotAssignment.id == slot.id,
                    SpoolmanSlotAssignment.spoolman_spool_id == slot.spoolman_spool_id,
                )
            )
            await db.commit()
        except Exception as cleanup_exc:
            await db.rollback()
            logger.warning(
                "Failed to remove stale slot assignment for spool %s: %s",
                slot.spoolman_spool_id,
                cleanup_exc,
            )
        return None
