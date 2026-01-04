"""API routes for AMS sensor history."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import get_db
from backend.app.models.ams_history import AMSSensorHistory

router = APIRouter(prefix="/ams-history", tags=["ams-history"])


class AMSHistoryPoint(BaseModel):
    recorded_at: datetime
    humidity: float | None
    humidity_raw: float | None
    temperature: float | None


class AMSHistoryResponse(BaseModel):
    printer_id: int
    ams_id: int
    data: list[AMSHistoryPoint]
    min_humidity: float | None
    max_humidity: float | None
    avg_humidity: float | None
    min_temperature: float | None
    max_temperature: float | None
    avg_temperature: float | None


@router.get("/{printer_id}/{ams_id}", response_model=AMSHistoryResponse)
async def get_ams_history(
    printer_id: int,
    ams_id: int,
    hours: int = Query(default=24, ge=1, le=168, description="Hours of history (1-168)"),
    db: AsyncSession = Depends(get_db),
):
    """Get AMS sensor history for a specific printer and AMS unit."""
    since = datetime.now() - timedelta(hours=hours)

    # Get data points
    result = await db.execute(
        select(AMSSensorHistory)
        .where(
            and_(
                AMSSensorHistory.printer_id == printer_id,
                AMSSensorHistory.ams_id == ams_id,
                AMSSensorHistory.recorded_at >= since,
            )
        )
        .order_by(AMSSensorHistory.recorded_at)
    )
    records = result.scalars().all()

    # Calculate stats
    stats_result = await db.execute(
        select(
            func.min(AMSSensorHistory.humidity).label("min_humidity"),
            func.max(AMSSensorHistory.humidity).label("max_humidity"),
            func.avg(AMSSensorHistory.humidity).label("avg_humidity"),
            func.min(AMSSensorHistory.temperature).label("min_temp"),
            func.max(AMSSensorHistory.temperature).label("max_temp"),
            func.avg(AMSSensorHistory.temperature).label("avg_temp"),
        ).where(
            and_(
                AMSSensorHistory.printer_id == printer_id,
                AMSSensorHistory.ams_id == ams_id,
                AMSSensorHistory.recorded_at >= since,
            )
        )
    )
    stats = stats_result.one()

    return AMSHistoryResponse(
        printer_id=printer_id,
        ams_id=ams_id,
        data=[
            AMSHistoryPoint(
                recorded_at=r.recorded_at,
                humidity=r.humidity,
                humidity_raw=r.humidity_raw,
                temperature=r.temperature,
            )
            for r in records
        ],
        min_humidity=stats.min_humidity,
        max_humidity=stats.max_humidity,
        avg_humidity=round(stats.avg_humidity, 1) if stats.avg_humidity else None,
        min_temperature=stats.min_temp,
        max_temperature=stats.max_temp,
        avg_temperature=round(stats.avg_temp, 1) if stats.avg_temp else None,
    )


@router.delete("/{printer_id}")
async def delete_old_history(
    printer_id: int,
    days: int = Query(default=30, ge=1, le=365, description="Delete data older than X days"),
    db: AsyncSession = Depends(get_db),
):
    """Delete old AMS history data for a printer."""
    cutoff = datetime.now() - timedelta(days=days)

    result = await db.execute(
        select(func.count(AMSSensorHistory.id)).where(
            and_(
                AMSSensorHistory.printer_id == printer_id,
                AMSSensorHistory.recorded_at < cutoff,
            )
        )
    )
    count = result.scalar()

    await db.execute(
        AMSSensorHistory.__table__.delete().where(
            and_(
                AMSSensorHistory.printer_id == printer_id,
                AMSSensorHistory.recorded_at < cutoff,
            )
        )
    )
    await db.commit()

    return {"deleted": count, "message": f"Deleted {count} records older than {days} days"}
