"""API routes for printer heater (nozzle / bed / chamber) sensor history."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.printer_sensor_history import PrinterSensorHistory
from backend.app.models.user import User

router = APIRouter(prefix="/printer-sensor-history", tags=["printer-sensor-history"])

VALID_KINDS = {"nozzle", "nozzle_2", "bed", "chamber"}


class HeaterHistoryPoint(BaseModel):
    recorded_at: datetime
    value: float | None
    target: float | None


class HeaterSeries(BaseModel):
    sensor_kind: str
    data: list[HeaterHistoryPoint]
    min_value: float | None
    max_value: float | None
    avg_value: float | None


class PrinterSensorHistoryResponse(BaseModel):
    printer_id: int
    series: list[HeaterSeries]


@router.get("/{printer_id}", response_model=PrinterSensorHistoryResponse)
async def get_printer_sensor_history(
    printer_id: int,
    hours: int = Query(default=24, ge=1, le=168, description="Hours of history (1-168)"),
    kinds: str | None = Query(
        default=None,
        description="Comma-separated list of sensor kinds (nozzle, nozzle_2, bed, chamber). All by default.",
    ),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTER_SENSOR_HISTORY_READ),
):
    """Return per-sensor heater history for a printer."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    if kinds:
        requested = {k.strip() for k in kinds.split(",") if k.strip()}
        kinds_to_fetch = sorted(requested & VALID_KINDS)
    else:
        kinds_to_fetch = sorted(VALID_KINDS)

    series_out: list[HeaterSeries] = []
    for kind in kinds_to_fetch:
        result = await db.execute(
            select(PrinterSensorHistory)
            .where(
                and_(
                    PrinterSensorHistory.printer_id == printer_id,
                    PrinterSensorHistory.sensor_kind == kind,
                    PrinterSensorHistory.recorded_at >= since,
                )
            )
            .order_by(PrinterSensorHistory.recorded_at)
        )
        records = result.scalars().all()

        stats_result = await db.execute(
            select(
                func.min(PrinterSensorHistory.value).label("min_v"),
                func.max(PrinterSensorHistory.value).label("max_v"),
                func.avg(PrinterSensorHistory.value).label("avg_v"),
            ).where(
                and_(
                    PrinterSensorHistory.printer_id == printer_id,
                    PrinterSensorHistory.sensor_kind == kind,
                    PrinterSensorHistory.recorded_at >= since,
                )
            )
        )
        stats = stats_result.one()

        series_out.append(
            HeaterSeries(
                sensor_kind=kind,
                data=[
                    HeaterHistoryPoint(
                        recorded_at=r.recorded_at,
                        value=r.value,
                        target=r.target,
                    )
                    for r in records
                ],
                min_value=stats.min_v,
                max_value=stats.max_v,
                avg_value=round(stats.avg_v, 1) if stats.avg_v is not None else None,
            )
        )

    return PrinterSensorHistoryResponse(printer_id=printer_id, series=series_out)


@router.delete("/{printer_id}")
async def delete_old_history(
    printer_id: int,
    days: int = Query(default=30, ge=1, le=365, description="Delete data older than X days"),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTER_SENSOR_HISTORY_READ),
):
    """Delete old printer sensor history for a printer."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    result = await db.execute(
        select(func.count(PrinterSensorHistory.id)).where(
            and_(
                PrinterSensorHistory.printer_id == printer_id,
                PrinterSensorHistory.recorded_at < cutoff,
            )
        )
    )
    count = result.scalar()

    await db.execute(
        PrinterSensorHistory.__table__.delete().where(
            and_(
                PrinterSensorHistory.printer_id == printer_id,
                PrinterSensorHistory.recorded_at < cutoff,
            )
        )
    )
    await db.commit()

    return {"deleted": count, "message": f"Deleted {count} records older than {days} days"}
