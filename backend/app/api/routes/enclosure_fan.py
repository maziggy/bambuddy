"""API routes for enclosure fan run history."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.enclosure_fan_run import EnclosureFanRun
from backend.app.services.homeassistant import homeassistant_service

router = APIRouter(prefix="/enclosure-fan", tags=["enclosure-fan"])


class FanRunPoint(BaseModel):
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: float | None


class EnclosureFanHistoryResponse(BaseModel):
    printer_id: int
    runs: list[FanRunPoint]
    total_runtime_seconds: float
    run_count: int
    avg_duration_seconds: float | None
    longest_run_seconds: float | None
    is_on: bool | None


@router.get("/{printer_id}/history", response_model=EnclosureFanHistoryResponse)
async def get_fan_history(
    printer_id: int,
    hours: int = Query(default=24, ge=1, le=168),
    db: AsyncSession = Depends(get_db),
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    result = await db.execute(
        select(EnclosureFanRun)
        .where(
            and_(
                EnclosureFanRun.printer_id == printer_id,
                EnclosureFanRun.started_at >= since,
            )
        )
        .order_by(EnclosureFanRun.started_at)
    )
    runs = result.scalars().all()

    now = datetime.now(timezone.utc)
    points: list[FanRunPoint] = []
    total = 0.0
    longest = 0.0

    for run in runs:
        end = run.ended_at or now
        # Make started_at timezone-aware if it isn't
        start = run.started_at
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        dur = (end - start).total_seconds()
        total += dur
        if dur > longest:
            longest = dur
        points.append(
            FanRunPoint(
                started_at=run.started_at,
                ended_at=run.ended_at,
                duration_seconds=dur,
            )
        )

    count = len(points)
    enclosure_cache = homeassistant_service.get_cached_enclosure(printer_id)
    is_on = enclosure_cache.get("fan_on") if enclosure_cache else None

    return EnclosureFanHistoryResponse(
        printer_id=printer_id,
        runs=points,
        total_runtime_seconds=total,
        run_count=count,
        avg_duration_seconds=round(total / count, 1) if count > 0 else None,
        longest_run_seconds=longest if count > 0 else None,
        is_on=is_on,
    )
