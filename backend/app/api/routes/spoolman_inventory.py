"""Spoolman inventory proxy endpoints.

Translates between Spoolman's data model and Bambuddy's internal
InventorySpool format so the frontend can use a single unified inventory UI
regardless of whether data comes from the local database or Spoolman.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.api.routes._spoolman_helpers import (
    NormalizedFilament,
    NormalizedVendorRef,
    _map_spoolman_spool,
    _safe_float,
    _safe_int,
    _safe_optional_float,
    assert_safe_spoolman_url,
)
from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.ams_label import AmsLabel
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.models.spoolman_k_profile import SpoolmanKProfile
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.models.user import User
from backend.app.schemas.spool import SpoolKProfileBase
from backend.app.schemas.spoolman import SpoolmanFilamentPatch, SpoolmanSlotAssignmentEnriched
from backend.app.services.printer_manager import printer_manager
from backend.app.services.spoolman import (
    SpoolmanClient,
    SpoolmanClientError,
    SpoolmanNotFoundError,
    SpoolmanUnavailableError,
    get_spoolman_client,
    init_spoolman_client,
)
from backend.app.utils.filament_ids import (
    GENERIC_FILAMENT_IDS,
    MATERIAL_TEMPS,
    normalize_slicer_filament,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/spoolman/inventory", tags=["spoolman-inventory"])


# Cache the last successful health-check timestamp to avoid a round-trip on
# every request.  A failed check clears the cache immediately.
_health_check_cache: dict[str, float] = {}
_HEALTH_CHECK_TTL = 30.0  # seconds


def _tag_cleared(val: str | None) -> bool:
    """Return True when a PATCH field explicitly removes a tag (null)."""
    return val is None


async def _get_client(db: AsyncSession) -> SpoolmanClient:
    """Return a validated Spoolman client (URL checked, health-checked) or raise an HTTP error."""
    result = await db.execute(select(Settings))
    settings: dict[str, str] = {s.key: s.value for s in result.scalars().all()}

    enabled = settings.get("spoolman_enabled", "false").lower() == "true"
    url = settings.get("spoolman_url", "").strip()

    if not enabled:
        raise HTTPException(status_code=400, detail="Spoolman integration is not enabled")
    if not url:
        raise HTTPException(status_code=400, detail="Spoolman URL is not configured")

    # SSRF guard: reject dangerous schemes, cloud-metadata IPs (169.254.169.254, 100.100.100.200,
    # fd00:ec2::254), multicast and unspecified addresses — loopback and RFC-1918 ranges are
    # intentionally permitted (Spoolman commonly runs on the same host or home LAN).
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
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Spoolman rejected the request",
                "upstream_status": exc.status_code,
                "upstream_body": getattr(exc, "response_text", ""),
            },
        ) from exc
    except SpoolmanUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Spoolman server is not reachable") from exc


def _raise_if_partial_failure(spools: list[dict], results: list, operation: str) -> None:
    """Raise HTTP 502 if any gather result is an exception, logging each failure."""
    failures = [(s["id"], r) for s, r in zip(spools, results, strict=True) if isinstance(r, BaseException)]
    if failures:
        logger.error(
            "Partial %s failure: %d/%d spools failed: %s",
            operation,
            len(failures),
            len(spools),
            [(sid, type(exc).__name__) for sid, exc in failures],
        )
        raise HTTPException(
            status_code=502,
            detail=f"{operation} partially applied: {len(spools) - len(failures)}/{len(spools)} spools updated",
        )


async def _apply_price_if_set(client: SpoolmanClient, spool: dict, cost_per_kg: float | None) -> tuple[dict, list[str]]:
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
    except HTTPException as exc:
        if exc.status_code >= 500:
            raise  # Propagate network/server errors — don't swallow Spoolman outages
        logger.warning(
            "Price update failed for spool %d; spool created without price (cost_per_kg=%s, status=%d)",
            spool["id"],
            cost_per_kg,
            exc.status_code,
        )
        return spool, [f"price_not_set: Spoolman rejected the price update (HTTP {exc.status_code})"]


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
    # When spoolman_filament_id is provided the caller has already chosen a filament from the
    # Spoolman catalog, so material (and other metadata) are optional — the backend skips
    # find_or_create_filament() and uses the supplied ID directly.
    spoolman_filament_id: int | None = Field(None, gt=0)
    material: str | None = Field(None, min_length=1, max_length=64)
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
    # BambuStudio slicer preset for this spool. Spoolman has no native field
    # for this, so we persist it under the bambu_slicer_filament[_name] keys
    # in the spool's extra dict and read it back in _map_spoolman_spool.
    slicer_filament: str | None = Field(None, max_length=128)
    slicer_filament_name: str | None = Field(None, max_length=255)

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
        # material is required only when the caller has not pre-selected a Spoolman filament
        if self.spoolman_filament_id is None and not self.material:
            raise ValueError("material is required when spoolman_filament_id is not provided")
        if self.weight_used > self.label_weight:
            raise ValueError("weight_used must not exceed label_weight")
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
    # BambuStudio slicer preset — persisted to Spoolman extra dict (see Create
    # schema). Pass an empty string to clear; null/omitted leaves unchanged.
    slicer_filament: str | None = Field(None, max_length=128)
    slicer_filament_name: str | None = Field(None, max_length=255)

    @field_validator("rgba")
    @classmethod
    def validate_rgba(cls, v: str | None) -> str | None:
        return _validate_rgba(v)

    @field_validator("storage_location")
    @classmethod
    def validate_storage_location(cls, v: str | None) -> str | None:
        return _validate_storage_location(v)

    @model_validator(mode="after")
    def validate_tag_fields(self) -> SpoolmanInventoryUpdate:
        # null = remove tag; non-null values rejected (use /tag endpoint to write tags)
        if self.tag_uid is not None:
            raise ValueError("tag_uid cannot be set via this endpoint; use PATCH /spools/{id}/tag to write tags")
        if self.tray_uuid is not None:
            raise ValueError("tray_uuid cannot be set via this endpoint; use PATCH /spools/{id}/tag to write tags")
        return self

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


class SpoolTagLinkRequest(BaseModel):
    # Minimum 8 hex chars = 4-byte NFC UID (Bambu Lab hardware tags use 4-byte UIDs).
    tag_uid: str | None = Field(None, min_length=8, max_length=30, pattern=r"^[0-9A-Fa-f]+$")
    tray_uuid: str | None = Field(None, min_length=32, max_length=32, pattern=r"^[0-9A-Fa-f]+$")

    @field_validator("tag_uid")
    @classmethod
    def tag_uid_not_all_zeros(cls, v: str | None) -> str | None:
        if v is not None and all(c in "0" for c in v):
            raise ValueError("tag_uid must not be all-zero bytes")
        return v

    @model_validator(mode="after")
    def at_least_one(self) -> SpoolTagLinkRequest:
        if not self.tag_uid and not self.tray_uuid:
            raise ValueError("tag_uid or tray_uuid is required")
        return self


class SpoolSlotAssignmentRequest(BaseModel):
    spoolman_spool_id: int = Field(..., gt=0)
    printer_id: int = Field(..., gt=0)
    # ams_id 0–7 for physical AMS units; 255 = external/virtual spool extruder slot
    ams_id: int = Field(..., ge=0, le=255)
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

    mapped: list[dict] = []
    spool_ids: list[int] = []
    for s in spools:
        try:
            m = _map_spoolman_spool(s)
            mapped.append(m)
            spool_ids.append(m["id"])
        except ValueError as exc:
            logger.warning("Skipping malformed Spoolman spool (id=%r): %s", s.get("id"), exc)

    if spool_ids:
        kp_result = await db.execute(select(SpoolmanKProfile).where(SpoolmanKProfile.spoolman_spool_id.in_(spool_ids)))
        kp_by_spool: dict[int, list[dict]] = {}
        for kp in kp_result.scalars().all():
            kp_by_spool.setdefault(kp.spoolman_spool_id, []).append(_k_profile_to_dict(kp))
        for m in mapped:
            m["k_profiles"] = kp_by_spool.get(m["id"], [])

    return mapped


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
        mapped = _map_spoolman_spool(spool)
    except ValueError as exc:
        logger.warning("Malformed Spoolman spool (id=%r): %s", spool_id, exc)
        raise HTTPException(status_code=502, detail="Spoolman returned malformed spool data") from exc

    kp_result = await db.execute(select(SpoolmanKProfile).where(SpoolmanKProfile.spoolman_spool_id == spool_id))
    mapped["k_profiles"] = [_k_profile_to_dict(kp) for kp in kp_result.scalars().all()]
    return mapped


async def _resolve_filament_id(data: SpoolmanInventoryCreate, client: SpoolmanClient) -> int:
    """Return the Spoolman filament ID for this spool creation request.

    If spoolman_filament_id is set the caller pre-selected a catalog entry,
    so find_or_create_filament() is skipped and the ID is used directly.
    """
    if data.spoolman_filament_id is not None:
        return data.spoolman_filament_id
    # Validator guarantees material is non-None when spoolman_filament_id is None
    assert data.material is not None  # noqa: S101
    color_hex = (data.rgba or "808080FF")[:6]
    async with _translate_spoolman_errors():
        return await client.find_or_create_filament(
            material=data.material,
            subtype=data.subtype or "",
            brand=data.brand,
            color_hex=color_hex,
            label_weight=data.label_weight,
            color_name=data.color_name,
        )


@router.post("/spools")
async def create_spool(
    data: SpoolmanInventoryCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> dict:
    """Create a new spool in Spoolman, auto-creating vendor and filament as needed."""
    client = await _get_client(db)
    filament_id = await _resolve_filament_id(data, client)

    remaining = max(0.0, data.label_weight - data.weight_used)
    try:
        async with _translate_spoolman_errors():
            spool = await client.create_spool(
                filament_id=filament_id,
                remaining_weight=remaining,
                comment=data.note or None,
                location=data.storage_location or None,
            )
    except HTTPException as exc:
        if exc.status_code == 404 and data.spoolman_filament_id is not None:
            raise HTTPException(
                status_code=404,
                detail=f"Filament {data.spoolman_filament_id} not found in Spoolman",
            ) from exc
        raise

    spool, price_warnings = await _apply_price_if_set(client, spool, data.cost_per_kg)

    # Persist slicer_filament under the spool's extra dict (mirror update_spool).
    if data.slicer_filament is not None or data.slicer_filament_name is not None:
        # Ensure extra fields are registered before write.
        if data.slicer_filament is not None:
            await client.ensure_extra_field("bambu_slicer_filament")
        if data.slicer_filament_name is not None:
            await client.ensure_extra_field("bambu_slicer_filament_name")
        new_extra: dict = {}
        if data.slicer_filament is not None:
            new_extra["bambu_slicer_filament"] = json.dumps(data.slicer_filament)
        if data.slicer_filament_name is not None:
            new_extra["bambu_slicer_filament_name"] = json.dumps(data.slicer_filament_name)
        if new_extra:
            try:
                async with _translate_spoolman_errors():
                    spool = await client.merge_spool_extra(spool["id"], new_extra)
            except HTTPException:
                # Best-effort — the spool already exists, log and continue.
                logger.warning(
                    "Failed to persist slicer_filament for spool %s",
                    spool.get("id"),
                )

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

    try:
        filament_id = await _resolve_filament_id(data, client)
    except HTTPException as exc:
        if exc.status_code == 404 and data.spoolman_filament_id is not None:
            raise HTTPException(
                status_code=404,
                detail=f"Filament {data.spoolman_filament_id} not found in Spoolman",
            ) from exc
        raise

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
            failures.append("spool creation failed")
            continue
        try:
            spool, price_warnings = await _apply_price_if_set(client, spool, data.cost_per_kg)
        except HTTPException as exc:
            logger.warning(
                "Bulk spool %d: price update failed (HTTP %d); spool not added to created list",
                spool.get("id", 0),
                exc.status_code,
            )
            failures.append("spool created but price update failed")
            continue
        if price_warnings:
            logger.warning("Bulk spool %s created without price: %s", spool.get("id"), price_warnings)
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
    storage_location = data.storage_location if storage_location_changed else None

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
    #
    # Spoolman PATCHes extra dicts by MERGING — popping "tag" from a re-fetched
    # dict and sending the rest doesn't clear the key (Spoolman keeps the old
    # value because the key wasn't in the payload). Explicitly set the tag to
    # a JSON-encoded empty string; read-side filters strip the quotes.
    async with client.extra_lock(spool_id):
        if tag_nulled:
            # Re-fetch inside the lock so we work with fresh extra data.
            async with _translate_spoolman_errors():
                fresh = await client.get_spool(spool_id)
            cur_extra = dict(fresh.get("extra") or {})
            cur_extra["tag"] = json.dumps("")
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

    # Persist BambuStudio slicer preset under the spool's extra dict.
    # Spoolman doesn't have a native field for this, so we round-trip via
    # extra and unpack in _map_spoolman_spool. Only writes when the request
    # explicitly set the field — passing null/omitting leaves the existing
    # extra entry untouched (write empty string to clear).
    sf_set = "slicer_filament" in data.model_fields_set
    sfn_set = "slicer_filament_name" in data.model_fields_set
    if sf_set or sfn_set:
        # Ensure extra fields are registered (Spoolman rejects PATCHes with
        # unknown keys with HTTP 400). Idempotent if startup already ran this.
        if sf_set:
            await client.ensure_extra_field("bambu_slicer_filament")
        if sfn_set:
            await client.ensure_extra_field("bambu_slicer_filament_name")
        new_extra: dict = {}
        if sf_set:
            new_extra["bambu_slicer_filament"] = json.dumps(data.slicer_filament or "")
        if sfn_set:
            new_extra["bambu_slicer_filament_name"] = json.dumps(data.slicer_filament_name or "")
        async with _translate_spoolman_errors():
            updated = await client.merge_spool_extra(spool_id, new_extra)

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

    Computes remaining = gross_weight - tare, where tare = spool.spool_weight
    if set, else filament.spool_weight; falls back to 250 g when both unset.
    """
    client = await _get_client(db)

    async with _translate_spoolman_errors():
        current = await client.get_spool(spool_id)

    cur_filament = current.get("filament") or {}
    spool_tare = current.get("spool_weight")
    raw_tare = spool_tare if spool_tare is not None else cur_filament.get("spool_weight")
    core_weight = _safe_float(raw_tare, 250.0)
    remaining = max(0.0, data.weight_grams - core_weight)

    async with _translate_spoolman_errors():
        updated = await client.update_spool_full(spool_id=spool_id, remaining_weight=remaining)

    upd_filament = updated.get("filament") or {}
    label_weight = _safe_int(upd_filament.get("weight"), 1000)
    weight_used = max(0.0, label_weight - remaining)
    return {"status": "ok", "weight_used": weight_used}


@router.patch("/spools/{spool_id}/tag")
async def link_tag_to_spoolman_spool(
    *,
    spool_id: int = Path(..., gt=0),
    data: SpoolTagLinkRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> dict:
    """Write an NFC tag UID or Bambu tray UUID into Spoolman's extra.tag for a spool.

    tray_uuid takes precedence over tag_uid when both are supplied.
    Returns 409 if another spool already carries the same tag.
    Uses extra_lock to serialise against concurrent extra-field writes.
    """
    client = await _get_client(db)
    tag = (data.tray_uuid or data.tag_uid).upper()
    tag_json = json.dumps(tag)

    async with client.extra_lock(spool_id):
        # Duplicate check: scan all spools for the same tag on a different spool.
        async with _translate_spoolman_errors():
            all_spools = await client.get_all_spools()
        for s in all_spools:
            s_tag = (s.get("extra") or {}).get("tag", "")
            if s_tag.strip('"').upper() == tag and s.get("id") != spool_id:
                raise HTTPException(
                    status_code=409,
                    detail=f"Tag is already assigned to spool {s['id']}",
                )

        # Re-fetch inside the lock so cur_extra reflects any concurrent update.
        async with _translate_spoolman_errors():
            current = await client.get_spool(spool_id)
        cur_extra = dict(current.get("extra") or {})
        cur_extra["tag"] = tag_json
        async with _translate_spoolman_errors():
            updated = await client.update_spool_full(spool_id=spool_id, extra=cur_extra)

    logger.info("Linked tag %s to Spoolman spool %s", tag, spool_id)
    return _map_spoolman_spool(updated)


@router.get("/slot-assignments/all", response_model=list[SpoolmanSlotAssignmentEnriched])
async def get_all_spoolman_slot_assignments(
    printer_id: int | None = Query(None, gt=0),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
) -> list[SpoolmanSlotAssignmentEnriched]:
    """Return all Spoolman slot assignments enriched with printer_name and ams_label.

    ``printer_name`` is null only when the printer relation is missing
    (cascade-deleted edge case). ``ams_label`` is null when no AmsLabel row
    matches the slot's MQTT serial (or the synthetic ``f"p{pid}a{ams_id}"``
    fallback key).
    """
    query = select(SpoolmanSlotAssignment).options(selectinload(SpoolmanSlotAssignment.printer))
    if printer_id is not None:
        query = query.where(SpoolmanSlotAssignment.printer_id == printer_id)
    result = await db.execute(query)
    slots = list(result.scalars().all())

    # Build (printer_id, ams_id) -> ams_serial map from live printer states.
    # Same pattern as inventory.py:765-806 for the local /assignments endpoint.
    printer_ids = {s.printer_id for s in slots}
    serial_map: dict[tuple[int, int], str] = {}
    all_statuses = printer_manager.get_all_statuses()
    for pid in printer_ids:
        state = all_statuses.get(pid)
        if not (state and state.raw_data):
            continue
        # Some printer firmware variants wrap the AMS list in an outer dict
        # (`{"ams": [...]}`). Mirror the defense used in sync_spoolman_ams_weights
        # (line 842-844) so a wrapped payload still resolves to a list.
        ams_raw = state.raw_data.get("ams", [])
        if isinstance(ams_raw, dict):
            ams_raw = ams_raw.get("ams", [])
        if not isinstance(ams_raw, list):
            continue
        for ams_unit in ams_raw:
            if not isinstance(ams_unit, dict):
                continue
            sn = str(ams_unit.get("sn") or ams_unit.get("serial_number") or "")
            if not sn:
                continue
            try:
                serial_map[(pid, int(ams_unit.get("id", 0)))] = sn
            except (ValueError, TypeError):
                continue

    # Add synthetic fallback key (f"p{pid}a{ams_id}") for slots without a serial.
    all_serials: set[str] = set(serial_map.values())
    for s in slots:
        if (s.printer_id, s.ams_id) not in serial_map:
            all_serials.add(f"p{s.printer_id}a{s.ams_id}")

    label_by_serial: dict[str, str] = {}
    if all_serials:
        lbl_result = await db.execute(select(AmsLabel).where(AmsLabel.ams_serial_number.in_(all_serials)))
        for lbl in lbl_result.scalars().all():
            label_by_serial[lbl.ams_serial_number] = lbl.label

    def _ams_label_for(pid: int, ams_id: int) -> str | None:
        sn = serial_map.get((pid, ams_id))
        if sn and sn in label_by_serial:
            return label_by_serial[sn]
        if not sn:
            return label_by_serial.get(f"p{pid}a{ams_id}")
        return None

    enriched: list[SpoolmanSlotAssignmentEnriched] = []
    for s in slots:
        if s.printer is None:
            # FK is ondelete=CASCADE so this should be unreachable in normal
            # operation; surface it loudly if a stale row ever appears.
            logger.warning(
                "Orphaned Spoolman slot assignment: printer_id=%d (ams=%d, tray=%d, spoolman_spool_id=%d) has no Printer row",
                s.printer_id,
                s.ams_id,
                s.tray_id,
                s.spoolman_spool_id,
            )
        enriched.append(
            SpoolmanSlotAssignmentEnriched(
                printer_id=s.printer_id,
                printer_name=s.printer.name if s.printer else None,
                ams_id=s.ams_id,
                tray_id=s.tray_id,
                spoolman_spool_id=s.spoolman_spool_id,
                ams_label=_ams_label_for(s.printer_id, s.ams_id),
            )
        )
    return enriched


@router.post("/sync-ams-weights")
async def sync_spoolman_ams_weights(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Sync remaining weight back to Spoolman for all slot-assigned spools.

    Reads live AMS remain% from connected printers, computes
    remaining = label_weight * remain% / 100, and PATCHes Spoolman.
    """
    client = await _get_client(db)

    # Fetch all non-archived Spoolman spools once for label_weight lookup
    async with _translate_spoolman_errors():
        raw_spools = await client.get_all_spools(allow_archived=False)
    spool_lookup: dict[int, dict] = {s["id"]: s for s in raw_spools if s.get("id") is not None}

    result = await db.execute(select(SpoolmanSlotAssignment))
    assignments = list(result.scalars().all())

    synced = 0
    skipped = 0

    def _find_tray(ams_data: list, ams_id: int, tray_id: int) -> dict | None:
        if not ams_data:
            return None
        for ams_unit in ams_data:
            if _safe_int(ams_unit.get("id"), -1) != ams_id:
                continue
            for tray in ams_unit.get("tray", []):
                if _safe_int(tray.get("id"), -1) == tray_id:
                    return tray
        return None

    for assignment in assignments:
        spool_dict = spool_lookup.get(assignment.spoolman_spool_id)
        if not spool_dict:
            logger.debug("Spoolman AMS sync: spool %d not found in Spoolman, skipping", assignment.spoolman_spool_id)
            skipped += 1
            continue

        label_weight = _safe_int((spool_dict.get("filament") or {}).get("weight"), 1000)
        if label_weight <= 0:
            logger.debug("Spoolman AMS sync: spool %d has no label_weight, skipping", assignment.spoolman_spool_id)
            skipped += 1
            continue

        state = printer_manager.get_status(assignment.printer_id)
        if not state or not state.raw_data:
            logger.info(
                "Spoolman AMS sync: printer %d not connected, skipping spool %d",
                assignment.printer_id,
                assignment.spoolman_spool_id,
            )
            skipped += 1
            continue

        ams_raw = state.raw_data.get("ams", [])
        if isinstance(ams_raw, dict):
            ams_raw = ams_raw.get("ams", [])
        tray = _find_tray(ams_raw, assignment.ams_id, assignment.tray_id)
        if not tray:
            logger.info(
                "Spoolman AMS sync: no tray data for spool %d (printer %d AMS%d-T%d)",
                assignment.spoolman_spool_id,
                assignment.printer_id,
                assignment.ams_id,
                assignment.tray_id,
            )
            skipped += 1
            continue

        remain_raw = tray.get("remain")
        if remain_raw is None:
            logger.debug(
                "Spoolman AMS sync: no remain value for spool %d (tray %d/%d), skipping",
                assignment.spoolman_spool_id,
                assignment.ams_id,
                assignment.tray_id,
            )
            skipped += 1
            continue

        try:
            remain_val = int(remain_raw)
        except (TypeError, ValueError):
            logger.debug(
                "Spoolman AMS sync: non-numeric remain=%r for spool %d, skipping",
                remain_raw,
                assignment.spoolman_spool_id,
            )
            skipped += 1
            continue

        if remain_val < 0 or remain_val > 100:
            logger.debug("Spoolman AMS sync: invalid remain=%s for spool %d", remain_raw, assignment.spoolman_spool_id)
            skipped += 1
            continue

        remaining = round(label_weight * remain_val / 100.0, 1)
        try:
            async with _translate_spoolman_errors():
                await client.update_spool_full(assignment.spoolman_spool_id, remaining_weight=remaining)
            logger.info(
                "Spoolman AMS sync: spool %d remaining set to %s g (remain=%d%%)",
                assignment.spoolman_spool_id,
                remaining,
                remain_val,
            )
            synced += 1
        except HTTPException as exc:
            if exc.status_code == 404:
                logger.warning(
                    "Spoolman AMS sync: spool %d not found in Spoolman (404), skipping",
                    assignment.spoolman_spool_id,
                )
            else:
                logger.warning(
                    "Spoolman AMS sync: failed to update spool %d (HTTP %d)",
                    assignment.spoolman_spool_id,
                    exc.status_code,
                )
            skipped += 1

    return {"synced": synced, "skipped": skipped}


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

    mapped = _map_spoolman_spool(spool)

    # Fetch K-profiles before the MQTT try block so we can use async DB access.
    kp_rows_result = await db.execute(
        select(SpoolmanKProfile).where(
            SpoolmanKProfile.spoolman_spool_id == body.spoolman_spool_id,
            SpoolmanKProfile.printer_id == body.printer_id,
        )
    )
    kp_rows = kp_rows_result.scalars().all()

    # Auto-configure AMS slot via MQTT (best-effort; slot assignment is already persisted)
    try:
        mqtt_client = printer_manager.get_client(body.printer_id)
        if mqtt_client:
            tray_type = mapped.get("material") or ""
            brand = mapped.get("brand") or ""
            subtype = mapped.get("subtype") or ""
            if brand:
                tray_sub_brands = f"{brand} {tray_type} {subtype}".strip()
            elif subtype:
                tray_sub_brands = f"{tray_type} {subtype}".strip()
            else:
                tray_sub_brands = tray_type

            tray_color = (mapped.get("rgba") or "808080FF").upper()
            if len(tray_color) == 6:
                tray_color = tray_color + "FF"

            material_upper = tray_type.upper().strip()
            tray_info_idx = (
                GENERIC_FILAMENT_IDS.get(material_upper)
                or GENERIC_FILAMENT_IDS.get(material_upper.split("-")[0].split(" ")[0])
                or ""
            )
            setting_id = ""

            temp_defaults = MATERIAL_TEMPS.get(material_upper, (200, 240))
            temp_min = mapped.get("nozzle_temp_min") or temp_defaults[0]
            temp_max = temp_defaults[1]

            # Pull printer state from printer_manager. The previous
            # `mqtt_client.printer_state` access via hasattr always returned
            # None (the attribute is `state`, not `printer_state`), so the
            # K-profile cascade silently skipped state.kprofiles, defaulted
            # nozzle_diameter to 0.4, and left slot_extruder unset.
            state = printer_manager.get_status(body.printer_id)
            nozzle_diameter = "0.4"
            if state and state.nozzles:
                nd = state.nozzles[0].nozzle_diameter
                if nd:
                    nozzle_diameter = nd

            slot_extruder = None
            if state and state.ams_extruder_map:
                if body.ams_id == 255:
                    # External slots: ext-L (tray 0) → extruder 1, ext-R (tray 1) → extruder 0
                    # tray_id 0→1, 1→0
                    slot_extruder = 1 - body.tray_id
                else:
                    slot_extruder = state.ams_extruder_map.get(str(body.ams_id))

            # Prefer exact extruder match, fall back to extruder-agnostic kp
            # for the same nozzle. Hard-skipping on mismatch silently dropped
            # valid stored profiles when the AMS-extruder mapping had shifted.
            exact_kp = None
            fallback_kp = None
            for kp in kp_rows:
                if kp.nozzle_diameter != nozzle_diameter or kp.cali_idx is None:
                    continue
                if slot_extruder is not None and kp.extruder is not None and kp.extruder == slot_extruder:
                    exact_kp = kp
                    break
                if fallback_kp is None:
                    fallback_kp = kp
            matching_kp = exact_kp or fallback_kp

            # Resolve the printer-side calibration entry by cali_idx so we
            # know the authoritative filament_id (the printer indexes its
            # calibration table by filament_id, not setting_id).
            printer_kp = None
            if matching_kp and state and state.kprofiles:
                for pkp in state.kprofiles:
                    if pkp.slot_id == matching_kp.cali_idx and pkp.nozzle_diameter == nozzle_diameter:
                        printer_kp = pkp
                        break
                if printer_kp is None:
                    logger.warning(
                        "Spoolman assign: cali_idx=%d not present in printer's "
                        "calibration table — stored kp may be stale.",
                        matching_kp.cali_idx,
                    )

            # Realign the slot's filament context (tray_info_idx + setting_id)
            # to the kp's calibration context. Without this, ams_filament_setting
            # declares the slot under generic PLA while extrusion_cali_sel points
            # the cali_idx at a different preset — the printer can't link them
            # and falls back to the default profile. P-prefix local presets are
            # valid for tray_info_idx; PFUS-prefix cloud-user presets are not
            # (the slicer rejects them).
            effective_tray_info_idx = tray_info_idx
            effective_setting_id = setting_id
            if printer_kp and printer_kp.filament_id:
                if not printer_kp.filament_id.startswith("PFUS"):
                    effective_tray_info_idx = printer_kp.filament_id
                if printer_kp.setting_id:
                    effective_setting_id = printer_kp.setting_id
            elif matching_kp and matching_kp.setting_id:
                derived = normalize_slicer_filament(matching_kp.setting_id)[0]
                if derived and not derived.startswith("PFUS"):
                    effective_tray_info_idx = derived
                effective_setting_id = matching_kp.setting_id
            if effective_tray_info_idx != tray_info_idx or effective_setting_id != setting_id:
                logger.info(
                    "Spoolman assign: realigning tray_info_idx %r → %r, setting_id %r → %r (kp_id=%s, source=%s)",
                    tray_info_idx,
                    effective_tray_info_idx,
                    setting_id,
                    effective_setting_id,
                    matching_kp.id if matching_kp else None,
                    "printer" if printer_kp else "stored",
                )

            mqtt_client.ams_set_filament_setting(
                ams_id=body.ams_id,
                tray_id=body.tray_id,
                tray_info_idx=effective_tray_info_idx,
                tray_type=tray_type,
                tray_sub_brands=tray_sub_brands,
                tray_color=tray_color,
                nozzle_temp_min=temp_min,
                nozzle_temp_max=temp_max,
                setting_id=effective_setting_id,
            )

            if matching_kp and matching_kp.cali_idx is not None:
                # Use printer-reported filament_id when available, otherwise
                # fall back to the realigned tray_info_idx so both commands
                # reference the same filament context.
                cali_filament_id = (
                    printer_kp.filament_id if printer_kp and printer_kp.filament_id else None
                ) or effective_tray_info_idx
                mqtt_client.extrusion_cali_sel(
                    ams_id=body.ams_id,
                    tray_id=body.tray_id,
                    cali_idx=matching_kp.cali_idx,
                    filament_id=cali_filament_id,
                    nozzle_diameter=nozzle_diameter,
                )
                logger.info(
                    "Spoolman assign: applied K-profile cali_idx=%d "
                    "(kp_id=%d, filament_id=%s) for spool %d on printer %d AMS%d-T%d",
                    matching_kp.cali_idx,
                    matching_kp.id,
                    cali_filament_id,
                    body.spoolman_spool_id,
                    body.printer_id,
                    body.ams_id,
                    body.tray_id,
                )
            else:
                # No stored K-profile: preserve the slot's current live cali_idx
                from backend.app.api.routes.inventory import _find_tray_in_ams_data

                live_tray = None
                if state and state.raw_data:
                    ams_raw = state.raw_data.get("ams", [])
                    if isinstance(ams_raw, dict):
                        ams_raw = ams_raw.get("ams", [])
                    live_tray = _find_tray_in_ams_data(ams_raw, body.ams_id, body.tray_id)
                live_cali_idx = (live_tray or {}).get("cali_idx")
                if live_cali_idx is not None and live_cali_idx >= 0:
                    mqtt_client.extrusion_cali_sel(
                        ams_id=body.ams_id,
                        tray_id=body.tray_id,
                        cali_idx=live_cali_idx,
                        filament_id=effective_tray_info_idx,
                        nozzle_diameter=nozzle_diameter,
                    )
                    logger.info(
                        "No stored K-profile for Spoolman spool %d — preserved live cali_idx=%d",
                        body.spoolman_spool_id,
                        live_cali_idx,
                    )

            logger.info(
                "Auto-configured AMS slot ams=%d tray=%d for Spoolman spool %d on printer %d",
                body.ams_id,
                body.tray_id,
                body.spoolman_spool_id,
                body.printer_id,
            )
    except Exception:
        logger.exception(
            "Failed to auto-configure AMS slot for Spoolman spool %d (printer=%d, ams=%d, tray=%d)",
            body.spoolman_spool_id,
            body.printer_id,
            body.ams_id,
            body.tray_id,
        )

    return mapped


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


def _k_profile_to_dict(p: SpoolmanKProfile) -> dict:
    """Manually map SpoolmanKProfile → SpoolKProfileResponse-compatible dict."""
    return {
        "id": p.id,
        "spool_id": p.spoolman_spool_id,
        "printer_id": p.printer_id,
        "extruder": p.extruder,
        "nozzle_diameter": p.nozzle_diameter,
        "nozzle_type": p.nozzle_type,
        "k_value": p.k_value,
        "name": p.name,
        "cali_idx": p.cali_idx,
        "setting_id": p.setting_id,
        "created_at": p.created_at,
    }


def _normalize_filament(raw: dict) -> NormalizedFilament | None:
    """Normalise a raw Spoolman filament dict for the frontend catalog picker.

    Returns None for entries with missing/zero IDs — those are malformed and
    must be filtered out before returning to the client.
    weight=0 is collapsed to None — 0g is not a valid filament weight.
    """
    filament_id = _safe_int(raw.get("id"), 0)
    if filament_id <= 0:
        logger.warning("Skipping Spoolman filament with missing or invalid id: %r", raw.get("name"))
        return None
    vendor = raw.get("vendor") or {}
    vendor_ref: NormalizedVendorRef | None = None
    if vendor:
        vendor_id = _safe_int(vendor.get("id"), 0)
        if vendor_id <= 0:
            logger.warning("Spoolman filament %d has vendor without valid id — vendor omitted", filament_id)
        else:
            vendor_ref = {"id": vendor_id, "name": str(vendor.get("name") or "").strip() or "Unknown"}
    return NormalizedFilament(
        id=filament_id,
        name=str(raw.get("name") or ""),
        material=raw.get("material") or None,
        color_hex=raw.get("color_hex") or None,
        color_name=raw.get("color_name") or None,
        weight=_safe_int(raw.get("weight"), 0) or None,  # 0g is not a valid weight
        spool_weight=_safe_optional_float(raw.get("spool_weight")),
        vendor=vendor_ref,
    )


@router.get("/filaments")
async def list_spoolman_filaments(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
) -> list[NormalizedFilament]:
    """Return all filaments from Spoolman, normalised for the frontend catalog picker."""
    client = await _get_client(db)
    async with _translate_spoolman_errors():
        raw_filaments = await client.get_filaments()
    if not isinstance(raw_filaments, list):
        logger.warning("Spoolman get_filaments() returned non-list type: %s", type(raw_filaments).__name__)
        return []
    return [f for raw in raw_filaments if (f := _normalize_filament(raw)) is not None]


@router.patch("/filaments/{filament_id}")
async def patch_spoolman_filament(
    *,
    filament_id: int = Path(..., gt=0),
    body: SpoolmanFilamentPatch = Body(...),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> NormalizedFilament:
    """Update a Spoolman filament's name and/or spool_weight.

    When spool_weight changes, Option A (keep_existing_spools=True) stamps the old
    weight onto spools currently inheriting it (spool.spool_weight is None) so their
    tare calculations are unaffected by the filament change.
    Option B (keep_existing_spools=False, the default): when spool_weight is a
    concrete value, stamps it onto every affected spool explicitly; when spool_weight
    is null, clears per-spool overrides so spools fall back to the filament value.
    """
    client = await _get_client(db)

    async with _translate_spoolman_errors():
        current = await client.get_filament(filament_id)

    patch_data = {k: v for k, v in body.model_dump(exclude_unset=True).items() if k != "keep_existing_spools"}
    if not patch_data:
        normalized = _normalize_filament(current)
        if normalized is None:
            raise HTTPException(status_code=404, detail="Filament not found")
        return normalized

    async with _translate_spoolman_errors():
        updated = await client.patch_filament(filament_id, patch_data)

    if "spool_weight" in body.model_fields_set:
        async with _translate_spoolman_errors():
            all_spools = await client.get_all_spools()
        affected_spools = [s for s in all_spools if (s.get("filament") or {}).get("id") == filament_id]

        if affected_spools:
            if body.keep_existing_spools:
                old_weight = _safe_optional_float(current.get("spool_weight"))
                if old_weight is not None:
                    spools_to_fix = [s for s in affected_spools if s.get("spool_weight") is None]
                    if spools_to_fix:
                        async with _translate_spoolman_errors():
                            results = await asyncio.gather(
                                *(
                                    client.update_spool_full(spool_id=s["id"], spool_weight=old_weight)
                                    for s in spools_to_fix
                                ),
                                return_exceptions=True,
                            )
                        _raise_if_partial_failure(spools_to_fix, results, "spool_weight stamp (option A)")
            else:
                new_weight = body.spool_weight
                if new_weight is not None:
                    # Stamp the new weight onto every spool of this filament type so
                    # each spool carries the value explicitly rather than inheriting.
                    async with _translate_spoolman_errors():
                        results = await asyncio.gather(
                            *(
                                client.update_spool_full(spool_id=s["id"], spool_weight=new_weight)
                                for s in affected_spools
                            ),
                            return_exceptions=True,
                        )
                    _raise_if_partial_failure(affected_spools, results, "spool_weight stamp (option B)")
                else:
                    # Filament weight is being cleared — remove any per-spool override
                    # so spools fall back to whatever the filament now provides.
                    spools_to_clear = [s for s in affected_spools if s.get("spool_weight") is not None]
                    if spools_to_clear:
                        async with _translate_spoolman_errors():
                            results = await asyncio.gather(
                                *(
                                    client.update_spool_full(spool_id=s["id"], clear_spool_weight=True)
                                    for s in spools_to_clear
                                ),
                                return_exceptions=True,
                            )
                        _raise_if_partial_failure(spools_to_clear, results, "spool_weight clear (option B null)")

    normalized = _normalize_filament(updated)
    if normalized is None:
        raise HTTPException(status_code=502, detail="Spoolman returned malformed filament data")
    return normalized


@router.get("/spools/{spool_id}/k-profiles")
async def get_spoolman_k_profiles(
    spool_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
) -> list[dict]:
    """Return all local K-value calibration profiles for a Spoolman spool."""
    await _get_client(db)
    result = await db.execute(select(SpoolmanKProfile).where(SpoolmanKProfile.spoolman_spool_id == spool_id))
    profiles = result.scalars().all()
    return [_k_profile_to_dict(p) for p in profiles]


@router.put("/spools/{spool_id}/k-profiles")
async def save_spoolman_k_profiles(
    spool_id: int = Path(..., gt=0),
    profiles: list[SpoolKProfileBase] = Body(...),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> list[dict]:
    """Replace all K-value calibration profiles for a Spoolman spool."""
    client = await _get_client(db)
    async with _translate_spoolman_errors():
        await client.get_spool(spool_id)

    saved: list[SpoolmanKProfile] = []
    try:
        await db.execute(delete(SpoolmanKProfile).where(SpoolmanKProfile.spoolman_spool_id == spool_id))
        for profile in profiles:
            obj = SpoolmanKProfile(
                spoolman_spool_id=spool_id,
                printer_id=profile.printer_id,
                extruder=profile.extruder,
                nozzle_diameter=profile.nozzle_diameter,
                nozzle_type=profile.nozzle_type,
                k_value=profile.k_value,
                name=profile.name,
                cali_idx=profile.cali_idx,
                setting_id=profile.setting_id,
            )
            db.add(obj)
            saved.append(obj)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(422, "Duplicate or invalid K-profile (check printer_id and nozzle uniqueness)") from exc
    except Exception as exc:
        await db.rollback()
        logger.error("K-profile save for spool %d failed: %s", spool_id, exc)
        raise HTTPException(500, "Failed to save K-profiles") from exc

    for obj in saved:
        await db.refresh(obj)

    return [_k_profile_to_dict(p) for p in saved]
