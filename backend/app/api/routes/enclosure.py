"""API routes for enclosure temp/humidity sensor history."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.enclosure_reading import EnclosureReading
from backend.app.services.homeassistant import homeassistant_service

router = APIRouter(prefix="/enclosure", tags=["enclosure"])


class EnclosureReadingPoint(BaseModel):
    recorded_at: datetime
    temp: float | None
    humidity: float | None


class EnclosureHistoryResponse(BaseModel):
    printer_id: int
    readings: list[EnclosureReadingPoint]
    current_temp: float | None
    current_humidity: float | None
    temp_unit: str
    humidity_unit: str


@router.get("/{printer_id}/history", response_model=EnclosureHistoryResponse)
async def get_enclosure_history(
    printer_id: int,
    hours: int = Query(default=24, ge=1, le=168),
    db: AsyncSession = Depends(get_db),
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    result = await db.execute(
        select(EnclosureReading)
        .where(
            and_(
                EnclosureReading.printer_id == printer_id,
                EnclosureReading.recorded_at >= since,
            )
        )
        .order_by(EnclosureReading.recorded_at)
    )
    rows = result.scalars().all()

    points = [
        EnclosureReadingPoint(
            recorded_at=r.recorded_at,
            temp=r.temp,
            humidity=r.humidity,
        )
        for r in rows
    ]

    cache = homeassistant_service.get_cached_enclosure(printer_id) or {}

    return EnclosureHistoryResponse(
        printer_id=printer_id,
        readings=points,
        current_temp=cache.get("temp"),
        current_humidity=cache.get("humidity"),
        temp_unit=cache.get("temp_unit", "°C"),
        humidity_unit=cache.get("humidity_unit", "%"),
    )
