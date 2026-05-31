"""API routes for filament dryer / spool storage monitoring."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.storage_reading import StorageReading
from backend.app.models.storage_unit import StorageUnit
from backend.app.services.homeassistant import homeassistant_service

router = APIRouter(prefix="/storage", tags=["storage"])


# ── Schemas ───────────────────────────────────────────────────────────────────


class StorageUnitCreate(BaseModel):
    name: str
    unit_type: str = "storage"  # "dryer" or "storage"
    ha_temp_entity: str | None = None
    ha_humidity_entity: str | None = None
    notes: str | None = None


class StorageUnitUpdate(BaseModel):
    name: str | None = None
    unit_type: str | None = None
    ha_temp_entity: str | None = None
    ha_humidity_entity: str | None = None
    notes: str | None = None
    is_active: bool | None = None


class StorageReadingPoint(BaseModel):
    recorded_at: datetime
    temp: float | None
    humidity: float | None


class StorageUnitResponse(BaseModel):
    id: int
    name: str
    unit_type: str
    ha_temp_entity: str | None
    ha_humidity_entity: str | None
    notes: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    # Live reading from cache (None if no HA entities or not yet polled)
    current_temp: float | None = None
    current_humidity: float | None = None
    temp_unit: str = "°C"
    humidity_unit: str = "%"

    class Config:
        from_attributes = True


class StorageHistoryResponse(BaseModel):
    unit_id: int
    readings: list[StorageReadingPoint]
    current_temp: float | None
    current_humidity: float | None
    temp_unit: str
    humidity_unit: str


# ── Helpers ───────────────────────────────────────────────────────────────────


def _unit_to_response(unit: StorageUnit) -> StorageUnitResponse:
    cache = homeassistant_service.get_cached_storage(unit.id) or {}
    return StorageUnitResponse(
        id=unit.id,
        name=unit.name,
        unit_type=unit.unit_type,
        ha_temp_entity=unit.ha_temp_entity,
        ha_humidity_entity=unit.ha_humidity_entity,
        notes=unit.notes,
        is_active=unit.is_active,
        created_at=unit.created_at,
        updated_at=unit.updated_at,
        current_temp=cache.get("temp"),
        current_humidity=cache.get("humidity"),
        temp_unit=cache.get("temp_unit", "°C"),
        humidity_unit=cache.get("humidity_unit", "%"),
    )


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("/", response_model=list[StorageUnitResponse])
async def list_storage_units(
    db: AsyncSession = Depends(get_db),
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
):
    """List all storage units with their latest cached reading."""
    result = await db.execute(select(StorageUnit).order_by(StorageUnit.unit_type, StorageUnit.name))
    units = result.scalars().all()
    return [_unit_to_response(u) for u in units]


@router.post("/", response_model=StorageUnitResponse, status_code=201)
async def create_storage_unit(
    body: StorageUnitCreate,
    db: AsyncSession = Depends(get_db),
    _=RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Create a new storage unit."""
    if body.unit_type not in ("dryer", "storage"):
        raise HTTPException(400, "unit_type must be 'dryer' or 'storage'")
    unit = StorageUnit(
        name=body.name,
        unit_type=body.unit_type,
        ha_temp_entity=body.ha_temp_entity or None,
        ha_humidity_entity=body.ha_humidity_entity or None,
        notes=body.notes,
    )
    db.add(unit)
    await db.commit()
    await db.refresh(unit)
    return _unit_to_response(unit)


@router.get("/{unit_id}", response_model=StorageUnitResponse)
async def get_storage_unit(
    unit_id: int,
    db: AsyncSession = Depends(get_db),
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
):
    result = await db.execute(select(StorageUnit).where(StorageUnit.id == unit_id))
    unit = result.scalar_one_or_none()
    if not unit:
        raise HTTPException(404, "Storage unit not found")
    return _unit_to_response(unit)


@router.put("/{unit_id}", response_model=StorageUnitResponse)
async def update_storage_unit(
    unit_id: int,
    body: StorageUnitUpdate,
    db: AsyncSession = Depends(get_db),
    _=RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    result = await db.execute(select(StorageUnit).where(StorageUnit.id == unit_id))
    unit = result.scalar_one_or_none()
    if not unit:
        raise HTTPException(404, "Storage unit not found")

    if body.unit_type is not None and body.unit_type not in ("dryer", "storage"):
        raise HTTPException(400, "unit_type must be 'dryer' or 'storage'")

    if body.name is not None:
        unit.name = body.name
    if body.unit_type is not None:
        unit.unit_type = body.unit_type
    if body.ha_temp_entity is not None:
        unit.ha_temp_entity = body.ha_temp_entity or None
    if body.ha_humidity_entity is not None:
        unit.ha_humidity_entity = body.ha_humidity_entity or None
    if body.notes is not None:
        unit.notes = body.notes
    if body.is_active is not None:
        unit.is_active = body.is_active
        if not body.is_active:
            homeassistant_service.invalidate_storage_cache(unit_id)

    await db.commit()
    await db.refresh(unit)
    return _unit_to_response(unit)


@router.delete("/{unit_id}", status_code=204)
async def delete_storage_unit(
    unit_id: int,
    db: AsyncSession = Depends(get_db),
    _=RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    result = await db.execute(select(StorageUnit).where(StorageUnit.id == unit_id))
    unit = result.scalar_one_or_none()
    if not unit:
        raise HTTPException(404, "Storage unit not found")
    homeassistant_service.invalidate_storage_cache(unit_id)
    await db.delete(unit)
    await db.commit()


@router.get("/{unit_id}/history", response_model=StorageHistoryResponse)
async def get_storage_history(
    unit_id: int,
    hours: int = Query(default=24, ge=1, le=168),
    db: AsyncSession = Depends(get_db),
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
):
    """Return historical readings for a storage unit (up to 7 days)."""
    unit_result = await db.execute(select(StorageUnit).where(StorageUnit.id == unit_id))
    if not unit_result.scalar_one_or_none():
        raise HTTPException(404, "Storage unit not found")

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows_result = await db.execute(
        select(StorageReading)
        .where(
            and_(
                StorageReading.storage_unit_id == unit_id,
                StorageReading.recorded_at >= since,
            )
        )
        .order_by(StorageReading.recorded_at)
    )
    rows = rows_result.scalars().all()

    points = [StorageReadingPoint(recorded_at=r.recorded_at, temp=r.temp, humidity=r.humidity) for r in rows]

    cache = homeassistant_service.get_cached_storage(unit_id) or {}
    return StorageHistoryResponse(
        unit_id=unit_id,
        readings=points,
        current_temp=cache.get("temp"),
        current_humidity=cache.get("humidity"),
        temp_unit=cache.get("temp_unit", "°C"),
        humidity_unit=cache.get("humidity_unit", "%"),
    )
