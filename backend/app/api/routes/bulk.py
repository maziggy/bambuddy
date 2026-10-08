"""Bulk reads for pages that show many printers (or archives) at once.

The Printers page asked for each printer's status, slot presets, AMS labels,
plugs, sensor readings, firmware and queue with one request per printer, and
the Archives page for each card's folders with one per archive. On a print
farm that was hundreds of requests per page load, queued one behind the other
on the server. The frontend (api/batch.ts) coalesces those per-id calls into
these.

Each route answers exactly what its single-id route answers, through the same
helper. A printer outside the caller's scope, or one that doesn't exist, is
left out; the client then asks the single route for it, which gives its 404.

They live under their own prefix so no ``/{id}`` route of another router can
take them for an id.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.firmware import FirmwareUpdateInfo, _checks_enabled, _not_checked, _printer_update_info
from backend.app.api.routes.ha_sensors import _card_sensors, _reading
from backend.app.api.routes.library import _folders_by_archive
from backend.app.api.routes.print_queue import _QUEUE_ITEM_LOADS, _enrich_response
from backend.app.api.routes.printers import _ams_labels, _printer_status, _slot_presets_by_key
from backend.app.api.routes.smart_plugs import PrinterCardPlugs, _card_script_plugs, _pick_main_plug
from backend.app.core.auth import (
    RequestPrinterScope,
    RequirePermissionIfAuthEnabled,
    require_ownership_permission,
)
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.core.printer_scope import PrinterScope
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.printer_ha_sensor import PrinterHASensor
from backend.app.models.slot_preset import SlotPresetMapping
from backend.app.models.smart_plug import SmartPlug
from backend.app.models.user import User
from backend.app.schemas.library import FolderResponse
from backend.app.schemas.print_queue import PrintQueueItemResponse
from backend.app.schemas.printer import PrinterStatus
from backend.app.schemas.printer_ha_sensor import PrinterHASensorReading
from backend.app.services.firmware_check import get_firmware_service
from backend.app.utils.id_list import parse_id_list

router = APIRouter(prefix="/bulk", tags=["bulk"])


@router.get("/printer-statuses", response_model=list[PrinterStatus])
async def get_printer_statuses(
    ids: str = Query(..., description="Comma-separated printer ids"),
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
    printer_scope: PrinterScope = RequestPrinterScope,
    db: AsyncSession = Depends(get_db),
):
    """``GET /printers/{printer_id}/status`` for several printers. Unknown ids are left out."""
    wanted = [i for i in parse_id_list(ids) if printer_scope.allows(i)]
    if not wanted:
        return []
    result = await db.execute(select(Printer).where(Printer.id.in_(wanted)))
    printers = {p.id: p for p in result.scalars().all()}
    return [await _printer_status(db, printers[i]) for i in wanted if i in printers]


@router.get("/slot-presets")
async def get_slot_presets_bulk(
    ids: str = Query(..., description="Comma-separated printer ids"),
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
    printer_scope: PrinterScope = RequestPrinterScope,
    db: AsyncSession = Depends(get_db),
):
    """``GET /printers/{printer_id}/slot-presets`` for several printers, keyed by printer id."""
    wanted = [i for i in parse_id_list(ids) if printer_scope.allows(i)]
    by_printer: dict[int, list] = {i: [] for i in wanted}
    if wanted:
        result = await db.execute(select(SlotPresetMapping).where(SlotPresetMapping.printer_id.in_(wanted)))
        for mapping in result.scalars().all():
            by_printer[mapping.printer_id].append(mapping)
    return {i: _slot_presets_by_key(mappings) for i, mappings in by_printer.items()}


@router.get("/ams-labels")
async def get_ams_labels_bulk(
    ids: str = Query(..., description="Comma-separated printer ids"),
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
    printer_scope: PrinterScope = RequestPrinterScope,
    db: AsyncSession = Depends(get_db),
):
    """``GET /printers/{printer_id}/ams-labels`` for several printers, keyed by printer id."""
    wanted = [i for i in parse_id_list(ids) if printer_scope.allows(i)]
    return {i: await _ams_labels(db, i) for i in wanted}


@router.get("/card-plugs", response_model=dict[int, PrinterCardPlugs])
async def get_smart_plugs_by_printers(
    ids: str = Query(..., description="Comma-separated printer ids"),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_READ),
    printer_scope: PrinterScope = RequestPrinterScope,
):
    """``GET /smart-plugs/by-printer/{printer_id}`` and its ``/scripts``, for several printers.

    The Printers page asked both once per printer card. A printer outside the
    caller's scope is left out, which the client reports as the single routes'
    404.
    """
    wanted = [i for i in parse_id_list(ids) if printer_scope.allows(i)]
    by_printer: dict[int, list[SmartPlug]] = {i: [] for i in wanted}
    if wanted:
        result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id.in_(wanted)).order_by(SmartPlug.id))
        for plug in result.scalars().all():
            by_printer[plug.printer_id].append(plug)
    return {
        i: PrinterCardPlugs(
            plug=_pick_main_plug(plugs),
            scripts=_card_script_plugs(plugs),
        )
        for i, plugs in by_printer.items()
    }


@router.get("/ha-sensor-readings", response_model=dict[int, list[PrinterHASensorReading]])
async def get_sensor_readings_by_printers(
    ids: str = Query(..., description="Comma-separated printer ids"),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_READ),
    printer_scope: PrinterScope = RequestPrinterScope,
):
    """``GET /ha-sensors/by-printer/{printer_id}/readings`` for several printers, keyed by printer id.

    The Printers page asked once per card every 15 seconds. A printer outside
    the caller's scope is left out, which the client reports as the single
    route's 404.
    """
    wanted = [i for i in parse_id_list(ids) if printer_scope.allows(i)]
    readings: dict[int, list[PrinterHASensorReading]] = {i: [] for i in wanted}
    if wanted:
        for sensor in await _card_sensors(db, PrinterHASensor.printer_id.in_(wanted)):
            readings[sensor.printer_id].append(_reading(sensor))
    return readings


@router.get("/firmware-updates", response_model=list[FirmwareUpdateInfo])
async def check_printers_firmware(
    ids: str = Query(..., description="Comma-separated printer ids"),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FIRMWARE_READ),
    printer_scope: PrinterScope = RequestPrinterScope,
):
    """``GET /firmware/updates/{printer_id}`` for several printers.

    The Printers page asked once per card. Unknown printers, and printers
    outside the caller's scope, are left out, which the client reports as the
    single route's 404.
    """
    wanted = [i for i in parse_id_list(ids) if printer_scope.allows(i)]
    if not wanted:
        return []
    result = await db.execute(select(Printer).where(Printer.id.in_(wanted)))
    printers = {p.id: p for p in result.scalars().all()}
    ordered = [printers[i] for i in wanted if i in printers]
    if not await _checks_enabled(db):
        return [_not_checked(p) for p in ordered]
    firmware_service = get_firmware_service()
    return [await _printer_update_info(firmware_service, p) for p in ordered]


@router.get("/printer-queues", response_model=dict[int, list[PrintQueueItemResponse]])
async def list_queue_by_printers(
    ids: str = Query(..., description="Comma-separated printer ids"),
    status: str | None = Query(None, description="Filter by status; several may be given comma-separated"),
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.QUEUE_READ_ALL,
            Permission.QUEUE_READ_OWN,
        )
    ),
    printer_scope: PrinterScope = RequestPrinterScope,
):
    """``GET /queue/?printer_id=N&status=...`` for several printers, keyed by printer id.

    Every printer card asked for its own queue, twice (the card and its queue
    widget). Each list is what the single call returns: the printer's own jobs
    plus the "Any <model>" jobs for its model, in dispatch order. A job for a
    model shows up under every printer of that model.
    """
    user, can_read_all = auth_result
    wanted = parse_id_list(ids)
    if not wanted:
        return {}
    models = {
        printer_id: model.lower()
        for printer_id, model in (
            await db.execute(select(Printer.id, Printer.model).where(Printer.id.in_(wanted)))
        ).all()
        if model
    }

    query = (
        select(PrintQueueItem)
        .options(*_QUEUE_ITEM_LOADS)
        .where(
            or_(
                PrintQueueItem.printer_id.in_(wanted),
                and_(
                    PrintQueueItem.printer_id.is_(None),
                    func.lower(PrintQueueItem.target_model).in_(set(models.values())),
                ),
            )
        )
        # The order the scheduler dispatches in (#3200), as GET /queue/ returns it
        .order_by(PrintQueueItem.position, PrintQueueItem.id)
    )
    if user is not None and not can_read_all:
        query = query.where(PrintQueueItem.created_by_id == user.id)
    # Items bound to printers the caller can't see stay out (#1727)
    if (clause := printer_scope.where(PrintQueueItem.printer_id)) is not None:
        query = query.where(clause)
    if status:
        query = query.where(PrintQueueItem.status.in_([s.strip() for s in status.split(",") if s.strip()]))

    items = (await db.execute(query)).scalars().all()
    responses = {item.id: _enrich_response(item) for item in items}

    def belongs(item: PrintQueueItem, printer_id: int) -> bool:
        if item.printer_id is not None:
            return item.printer_id == printer_id
        model = models.get(printer_id)
        return bool(model and item.target_model and item.target_model.lower() == model)

    return {printer_id: [responses[item.id] for item in items if belongs(item, printer_id)] for printer_id in wanted}


@router.get("/archive-folders", response_model=dict[int, list[FolderResponse]])
async def get_folders_by_archives(
    ids: str = Query(..., description="Comma-separated archive ids"),
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.LIBRARY_READ_ALL,
            Permission.LIBRARY_READ_OWN,
        )
    ),
):
    """``GET /library/folders/by-archive/{archive_id}`` for several archives, keyed by archive id.

    The Archives page asked once per archive card, each time loading the whole
    folder index to work out what the user may see.
    """
    user, _ = auth_result
    archive_ids = parse_id_list(ids)
    if not archive_ids:
        return {}
    return await _folders_by_archive(db, user, archive_ids)
