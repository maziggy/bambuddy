"""Scheduled (delayed) manual AMS drying runs (#2638)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.printer import Printer
from backend.app.models.scheduled_drying import ScheduledDrying
from backend.app.models.user import User
from backend.app.schemas.scheduled_drying import ScheduledDryingCreate, ScheduledDryingResponse
from backend.app.services import drying_preflight
from backend.app.services.printer_manager import printer_manager
from backend.app.utils.local_time import utcnow_naive

router = APIRouter(prefix="/scheduled-dryings", tags=["scheduled-dryings"])

ACTIVE_STATUSES = ("pending", "running")
# Failed rows are listed too. A run can only fail at dispatch (the firmware
# turns out too old, say), which is exactly the case a schedule-time check on
# an offline printer cannot catch, so without this the run just disappears and
# only the backend log knows why. The client dismisses the row to clear it.
LISTED_STATUSES = (*ACTIVE_STATUSES, "failed")


@router.post("", response_model=ScheduledDryingResponse)
async def create_scheduled_drying(
    payload: ScheduledDryingCreate,
    user: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Printer).where(Printer.id == payload.printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    # Fail fast in the UI rather than hours later with nobody watching. An
    # offline printer is still schedulable: only its model is judged here.
    state = printer_manager.get_status(payload.printer_id)
    unsupported = drying_preflight.check_drying_supported(
        printer.model,
        state.firmware_version if state else None,
        require_firmware=state is not None,
    )
    if unsupported:
        raise HTTPException(400, unsupported)

    if payload.start_after is not None and payload.start_after <= utcnow_naive():
        raise HTTPException(400, "start_after must be in the future")

    row = ScheduledDrying(
        printer_id=payload.printer_id,
        ams_id=payload.ams_id,
        temp=payload.temp,
        duration_hours=payload.duration_hours,
        filament=payload.filament,
        rotate_tray=payload.rotate_tray,
        start_after=payload.start_after,
        created_by_id=user.id if user else None,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.get("", response_model=list[ScheduledDryingResponse])
async def list_scheduled_dryings(
    printer_id: int | None = None,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
    db: AsyncSession = Depends(get_db),
):
    query = select(ScheduledDrying).where(ScheduledDrying.status.in_(LISTED_STATUSES))
    if printer_id is not None:
        query = query.where(ScheduledDrying.printer_id == printer_id)
    result = await db.execute(query.order_by(ScheduledDrying.start_after.asc().nullsfirst(), ScheduledDrying.id.asc()))
    return list(result.scalars().all())


@router.delete("/{scheduled_drying_id}")
async def cancel_scheduled_drying(
    scheduled_drying_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ScheduledDrying).where(ScheduledDrying.id == scheduled_drying_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Scheduled drying not found")
    if row.status == "failed":
        # Terminal and now acknowledged; drop it so it stops being listed.
        await db.delete(row)
        await db.commit()
        return {"status": "dismissed", "id": scheduled_drying_id}
    if row.status not in ACTIVE_STATUSES:
        raise HTTPException(400, "Only pending, running or failed dryings can be cancelled")

    if row.status == "running":
        # Best effort; cancellation proceeds even if the printer is offline.
        printer_manager.send_drying_command(row.printer_id, row.ams_id, 0, 0, mode=0)

    row.status = "cancelled"
    row.completed_at = utcnow_naive()
    await db.commit()
    return {"status": "cancelled", "id": row.id}
