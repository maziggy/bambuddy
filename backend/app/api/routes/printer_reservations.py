"""Per-printer time-based reservations (#2785).

A reservation blocks out one or more printers on a weekly pattern so the
queue timeline shows who they are being kept free for, and a reservation-aware
virtual printer holds auto-dispatched jobs instead of starting them into one.

Everyone who can see the queue sees every reservation — that is the point of
them. Creating needs ``queue:create``; editing and deleting follow the queue's
own/all split, with the reservation's creator as its owner.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import (
    ApiKeyActor,
    RequestActor,
    RequestPrinterScope,
    RequireAnyPermissionIfAuthEnabled,
    RequirePermissionIfAuthEnabled,
    require_ownership_permission,
)
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.core.printer_scope import PrinterScope, ensure_printers_exist
from backend.app.models.printer import Printer
from backend.app.models.printer_reservation import PrinterReservation
from backend.app.models.user import User
from backend.app.schemas.print_queue import NaiveUTCDatetime
from backend.app.schemas.printer_reservation import (
    PrinterReservationCreate,
    PrinterReservationResponse,
    PrinterReservationUpdate,
    ReservationOccurrenceResponse,
)
from backend.app.services import printer_reservations as svc
from backend.app.utils.local_time import utcnow_naive

router = APIRouter(prefix="/printer-reservations", tags=["printer-reservations"])

# Longest range the occurrences endpoint expands in one call.
MAX_OCCURRENCE_RANGE = timedelta(days=62)

_READ = (Permission.QUEUE_READ_ALL, Permission.QUEUE_READ_OWN, Permission.QUEUE_READ)


async def _existing_printer_ids(db: AsyncSession) -> set[int]:
    return set((await db.execute(select(Printer.id))).scalars().all())


async def _usernames(db: AsyncSession, user_ids: set[int]) -> dict[int, str]:
    if not user_ids:
        return {}
    rows = await db.execute(select(User.id, User.username).where(User.id.in_(user_ids)))
    return dict(rows.tuples().all())


def _visible_ids(ids: list[int] | None, existing: set[int], scope: PrinterScope) -> list[int]:
    return [pid for pid in (ids or []) if pid in existing and scope.allows(pid)]


def _ensure_in_scope(printer_ids: list[int], scope: PrinterScope) -> None:
    for pid in printer_ids:
        scope.ensure(pid)


async def _response(db: AsyncSession, row: PrinterReservation, scope: PrinterScope) -> PrinterReservationResponse:
    existing = await _existing_printer_ids(db)
    names = await _usernames(db, {row.created_by_id} if row.created_by_id else set())
    resp = PrinterReservationResponse.model_validate(row)
    resp.printer_ids = _visible_ids(row.printer_ids, existing, scope)
    resp.created_by_username = names.get(row.created_by_id) if row.created_by_id else None
    return resp


async def _load_for_write(
    db: AsyncSession, reservation_id: int, auth_result: tuple[User | ApiKeyActor | None, bool], scope: PrinterScope
) -> PrinterReservation:
    user, can_modify_all = auth_result
    row = (
        await db.execute(select(PrinterReservation).where(PrinterReservation.id == reservation_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Reservation not found")
    if not can_modify_all:
        user_id = getattr(user, "id", None)
        if user_id is None or row.created_by_id != user_id:
            raise HTTPException(403, "You can only change your own reservations")
    # Never let someone edit printers they cannot see out from under others.
    _ensure_in_scope(row.printer_ids or [], scope)
    return row


def _apply(row: PrinterReservation, body: PrinterReservationCreate | PrinterReservationUpdate) -> None:
    row.label = body.label.strip()
    row.printer_ids = list(body.printer_ids)
    row.days_mask = body.days_mask
    row.all_day = body.all_day
    row.start_minute = body.start_minute
    row.end_minute = body.end_minute
    row.valid_from = body.valid_from
    row.valid_until = body.valid_until


@router.get("", response_model=list[PrinterReservationResponse])
async def list_reservations(
    _: User | None = RequireAnyPermissionIfAuthEnabled(*_READ),
    printer_scope: PrinterScope = RequestPrinterScope,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    rows = await svc.load_reservations(db)
    existing = await _existing_printer_ids(db)
    names = await _usernames(db, {r.created_by_id for r in rows if r.created_by_id})
    out: list[PrinterReservationResponse] = []
    for row in rows:
        ids = _visible_ids(row.printer_ids, existing, printer_scope)
        if not ids:
            continue
        resp = PrinterReservationResponse.model_validate(row)
        resp.printer_ids = ids
        resp.created_by_username = names.get(row.created_by_id) if row.created_by_id else None
        out.append(resp)
    return out


@router.get("/occurrences", response_model=list[ReservationOccurrenceResponse])
async def list_occurrences(
    start: NaiveUTCDatetime = Query(None, description="Range start (UTC). Defaults to now."),
    end: NaiveUTCDatetime = Query(None, description="Range end (UTC). Defaults to start + 7 days."),
    _: User | None = RequireAnyPermissionIfAuthEnabled(*_READ),
    printer_scope: PrinterScope = RequestPrinterScope,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Concrete reservation windows in ``[start, end)``, for the queue timeline."""
    range_start: datetime = start or utcnow_naive()
    range_end: datetime = end or range_start + timedelta(days=7)
    if range_end <= range_start:
        raise HTTPException(400, "end must be after start")
    if range_end - range_start > MAX_OCCURRENCE_RANGE:
        raise HTTPException(400, f"Range may span at most {MAX_OCCURRENCE_RANGE.days} days")

    rows = await svc.load_reservations(db)
    existing = await _existing_printer_ids(db)
    names = await _usernames(db, {r.created_by_id for r in rows if r.created_by_id})
    out: list[ReservationOccurrenceResponse] = []
    for occ in svc.occurrences(rows, range_start, range_end):
        ids = _visible_ids(list(occ.printer_ids), existing, printer_scope)
        if not ids:
            continue
        out.append(
            ReservationOccurrenceResponse(
                reservation_id=occ.reservation_id,
                label=occ.label,
                printer_ids=ids,
                created_by_id=occ.created_by_id,
                created_by_username=names.get(occ.created_by_id) if occ.created_by_id else None,
                start=occ.start,
                end=occ.end,
            )
        )
    return out


@router.post("", response_model=PrinterReservationResponse)
async def create_reservation(
    body: PrinterReservationCreate,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.QUEUE_CREATE),
    actor: User | ApiKeyActor | None = RequestActor,
    printer_scope: PrinterScope = RequestPrinterScope,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    _ensure_in_scope(body.printer_ids, printer_scope)
    await ensure_printers_exist(db, body.printer_ids)
    row = PrinterReservation(created_by_id=getattr(actor, "id", None))
    _apply(row, body)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return await _response(db, row, printer_scope)


@router.put("/{reservation_id}", response_model=PrinterReservationResponse)
async def update_reservation(
    reservation_id: int,
    body: PrinterReservationUpdate,
    auth_result: tuple[User | ApiKeyActor | None, bool] = Depends(
        require_ownership_permission(Permission.QUEUE_UPDATE_ALL, Permission.QUEUE_UPDATE_OWN)
    ),
    printer_scope: PrinterScope = RequestPrinterScope,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    row = await _load_for_write(db, reservation_id, auth_result, printer_scope)
    _ensure_in_scope(body.printer_ids, printer_scope)
    await ensure_printers_exist(db, body.printer_ids)
    _apply(row, body)
    await db.commit()
    await db.refresh(row)
    return await _response(db, row, printer_scope)


@router.delete("/{reservation_id}")
async def delete_reservation(
    reservation_id: int,
    auth_result: tuple[User | ApiKeyActor | None, bool] = Depends(
        require_ownership_permission(Permission.QUEUE_DELETE_ALL, Permission.QUEUE_DELETE_OWN)
    ),
    printer_scope: PrinterScope = RequestPrinterScope,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    row = await _load_for_write(db, reservation_id, auth_result, printer_scope)
    await db.delete(row)
    await db.commit()
    return {"status": "deleted", "id": reservation_id}
