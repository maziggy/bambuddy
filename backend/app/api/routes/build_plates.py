"""Build plate catalog and per-printer installed plate (#1306).

The catalog is global: one list of plates per install. Built-in rows are seeded
on startup (see ``seed_build_plates``) and can be switched off but not deleted;
users add their own alongside them.

Permission model:

* Reading the catalog needs :attr:`Permission.PRINTERS_READ` — the printer
  cards and print dialog show plate names to anyone who can see printers.
* Editing the catalog is a settings change (:attr:`Permission.SETTINGS_UPDATE`).
* Swapping the plate on a printer is a physical, at-the-machine action, so it
  is gated like the other printer controls (:attr:`Permission.PRINTERS_CONTROL`)
  rather than needing rights to edit the printer's configuration.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.build_plate import BuildPlate
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.schemas.build_plate import (
    BedTypeOption,
    BuildPlateCreate,
    BuildPlateResponse,
    BuildPlateUpdate,
    InstalledPlateUpdate,
)
from backend.app.schemas.printer import PrinterResponse
from backend.app.utils.bed_types import BED_TYPES, bed_type_label

logger = logging.getLogger(__name__)

router = APIRouter(tags=["build-plates"])


async def _installed_map(db: AsyncSession) -> dict[int, list[int]]:
    rows = await db.execute(
        select(Printer.id, Printer.installed_plate_id).where(Printer.installed_plate_id.is_not(None))
    )
    out: dict[int, list[int]] = {}
    for printer_id, plate_id in rows.all():
        if plate_id is not None:
            out.setdefault(plate_id, []).append(printer_id)
    return out


async def _pending_required_map(db: AsyncSession) -> dict[int, int]:
    """Plate id -> number of pending queue jobs that asked for that plate."""
    rows = await db.execute(
        select(PrintQueueItem.required_plate_id, func.count())
        .where(PrintQueueItem.required_plate_id.is_not(None))
        .where(PrintQueueItem.status == "pending")
        .group_by(PrintQueueItem.required_plate_id)
    )
    return {plate_id: int(count) for plate_id, count in rows.all() if plate_id is not None}


def _clear_pending_requests(plate_id: int):
    """Pending jobs that asked for *plate_id* fall back to any plate of their file's type.

    Only pending rows: a job that already ran on the plate keeps that as history.
    """
    return (
        update(PrintQueueItem)
        .where(PrintQueueItem.required_plate_id == plate_id)
        .where(PrintQueueItem.status == "pending")
        .values(required_plate_id=None)
    )


def _to_response(
    plate: BuildPlate, installed_on: list[int] | None = None, required_by_pending: int = 0
) -> BuildPlateResponse:
    return BuildPlateResponse(
        id=plate.id,
        builtin_key=plate.builtin_key,
        is_builtin=plate.builtin_key is not None,
        name=plate.name,
        base_type=plate.base_type,
        base_type_label=bed_type_label(plate.base_type),
        pattern=plate.pattern,
        image=plate.image,
        notes=plate.notes,
        enabled=bool(plate.enabled),
        sort_order=plate.sort_order or 0,
        installed_on=sorted(installed_on or []),
        required_by_pending=required_by_pending,
        created_at=plate.created_at,
        updated_at=plate.updated_at,
    )


async def _get_plate(db: AsyncSession, plate_id: int) -> BuildPlate:
    plate = (await db.execute(select(BuildPlate).where(BuildPlate.id == plate_id))).scalar_one_or_none()
    if plate is None:
        raise HTTPException(404, "Build plate not found")
    return plate


@router.get("/build-plates/bed-types", response_model=list[BedTypeOption])
async def list_bed_types(
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
) -> list[BedTypeOption]:
    """The slicer plate types a build plate can belong to."""
    return [BedTypeOption(key=k, label=v) for k, v in BED_TYPES.items()]


@router.get("/build-plates", response_model=list[BuildPlateResponse])
@router.get("/build-plates/", response_model=list[BuildPlateResponse])
async def list_build_plates(
    db: AsyncSession = Depends(get_db, scope="function"),
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
) -> list[BuildPlateResponse]:
    """Every plate, enabled or not, in display order."""
    plates = (await db.execute(select(BuildPlate).order_by(BuildPlate.sort_order, BuildPlate.id))).scalars().all()
    installed = await _installed_map(db)
    required = await _pending_required_map(db)
    return [_to_response(p, installed.get(p.id), required.get(p.id, 0)) for p in plates]


@router.post("/build-plates", response_model=BuildPlateResponse, status_code=201)
@router.post("/build-plates/", response_model=BuildPlateResponse, status_code=201)
async def create_build_plate(
    payload: BuildPlateCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    _=RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
) -> BuildPlateResponse:
    """Add a custom plate (a third-party or patterned plate of a known type)."""
    plate = BuildPlate(
        name=payload.name.strip(),
        base_type=payload.base_type,
        pattern=(payload.pattern or "").strip() or None,
        image=payload.image,
        notes=payload.notes,
        enabled=payload.enabled,
        sort_order=payload.sort_order,
    )
    db.add(plate)
    await db.commit()
    await db.refresh(plate)
    return _to_response(plate)


@router.patch("/build-plates/{plate_id}", response_model=BuildPlateResponse)
async def update_build_plate(
    plate_id: int,
    payload: BuildPlateUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    _=RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
) -> BuildPlateResponse:
    """Edit a plate, or tick/untick it (``enabled``)."""
    plate = await _get_plate(db, plate_id)
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        data["name"] = data["name"].strip()
    if data.get("base_type") is not None and data["base_type"] != plate.base_type:
        # Jobs that asked for this plate did so for a file sliced for its old
        # type. Holding them for a plate that is now another type would hold
        # them forever, so they fall back to any plate of their file's type.
        await db.execute(_clear_pending_requests(plate_id))
    for field, value in data.items():
        if field in ("name", "base_type", "enabled", "sort_order") and value is None:
            continue  # non-nullable columns: null means "leave as is"
        setattr(plate, field, value)
    await db.commit()
    await db.refresh(plate)
    installed = await _installed_map(db)
    required = await _pending_required_map(db)
    return _to_response(plate, installed.get(plate.id), required.get(plate.id, 0))


@router.delete("/build-plates/{plate_id}", status_code=204, response_model=None)
async def delete_build_plate(
    plate_id: int,
    db: AsyncSession = Depends(get_db, scope="function"),
    _=RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
) -> None:
    """Delete a custom plate.

    Built-in plates can only be switched off — they are re-seeded on every
    start, so deleting one would just bring it back. Printers that had this
    plate installed go back to "not tracked", and queued jobs that asked for it
    specifically fall back to any plate of the file's type: the plate no longer
    exists, so holding them for it would hold them forever. A job printing or
    uploading right now is unaffected — the scheduler's dispatch-time check
    treats a deleted plate the same way.
    """
    plate = await _get_plate(db, plate_id)
    if plate.builtin_key is not None:
        raise HTTPException(400, "Built-in plates cannot be deleted — untick them instead")
    await db.execute(update(Printer).where(Printer.installed_plate_id == plate_id).values(installed_plate_id=None))
    await db.execute(_clear_pending_requests(plate_id))
    await db.delete(plate)
    await db.commit()


@router.put("/printers/{printer_id}/installed-plate", response_model=PrinterResponse)
async def set_installed_plate(
    printer_id: int,
    payload: InstalledPlateUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
) -> PrinterResponse:
    """Record which plate is now on a printer; ``plate_id: null`` stops tracking it."""
    printer = (await db.execute(select(Printer).where(Printer.id == printer_id))).scalar_one_or_none()
    if printer is None:
        raise HTTPException(404, "Printer not found")
    if payload.plate_id is not None:
        await _get_plate(db, payload.plate_id)
    printer.installed_plate_id = payload.plate_id
    await db.commit()
    await db.refresh(printer)
    logger.info("Printer %s installed plate set to %s", printer_id, payload.plate_id)
    return PrinterResponse.model_validate(printer)
