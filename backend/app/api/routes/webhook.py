import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import (
    api_key_printer_scope,
    check_webhook_permission,
    ensure_api_key_printer_access,
    get_api_key,
    is_auth_enabled,
    queue_review_required_for,
    resolve_apikey_owner,
)
from backend.app.core.database import get_db
from backend.app.models.api_key import APIKey
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.schemas.printer import HMSErrorResponse, hms_error_responses
from backend.app.services.print_confirmation import confirm_outcome_for_new_queue_item
from backend.app.services.print_control import stop_print_by_user
from backend.app.services.printer_manager import printer_manager
from backend.app.services.queue_position import next_queue_position

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])


# Request schemas
class QueueAddRequest(BaseModel):
    archive_id: int
    printer_id: int
    project_id: int | None = None
    scheduled_time: str | None = None  # ISO format datetime
    require_previous_success: bool = False
    auto_off_after: bool = False


class QueueAddResponse(BaseModel):
    id: int
    archive_id: int
    printer_id: int
    position: int
    status: str
    message: str


class PrinterStatusResponse(BaseModel):
    id: int
    name: str
    # The printer's own serial, so a client can tell printers apart by what
    # they report rather than by Bambuddy's row id (#2919).
    serial_number: str
    connected: bool
    state: str | None
    current_print: str | None
    progress: float | None
    # Minutes, as the printer reports it. Kept for existing clients;
    # remaining_seconds is the same estimate in seconds, the unit the
    # notification pipeline uses (#2919).
    remaining_time: int | None
    remaining_seconds: int | None = None
    layer_num: int | None = None
    total_layers: int | None = None
    # Bambu's id for the running job. A new value marks a new print, even when
    # two prints of the same file run back to back between two polls. None
    # when the job has no id: Bambu reports "0" or "" for local prints (for
    # example one started on the printer), the same reading main.py uses.
    subtask_id: str | None = None
    # Live HMS faults, in the same shape as GET /printers/{id}/status. They
    # tell a filament runout apart from someone pressing pause.
    hms_errors: list[HMSErrorResponse] = []


class QueueStatusResponse(BaseModel):
    printer_id: int
    printer_name: str
    pending: int
    printing: int
    items: list[dict]


def _job_id(subtask_id) -> str | None:
    """The printer's job id as text, or None when the job has none.

    Stored as the printer sent it, so coerce: a numeric id would fail
    validation and turn a status poll into a 500.
    """
    if subtask_id is None:
        return None
    value = str(subtask_id).strip()
    return None if value in ("", "0") else value


# Webhook endpoints


@router.post("/queue/add", response_model=QueueAddResponse)
async def webhook_add_to_queue(
    data: QueueAddRequest,
    api_key: APIKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Add a print to the queue via webhook.

    Requires 'can_queue' permission.
    """
    await check_webhook_permission(db, api_key, "queue")
    await ensure_api_key_printer_access(db, api_key, data.printer_id)

    # Verify archive exists
    result = await db.execute(select(PrintArchive).where(PrintArchive.id == data.archive_id))
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(status_code=404, detail="Archive not found")

    # Verify printer exists
    result = await db.execute(select(Printer).where(Printer.id == data.printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")

    # Append to the end of the queue: positions are one sequence across all
    # pending items, not one per printer (#3200).
    next_position = await next_queue_position(db)

    # Parse scheduled time if provided
    scheduled_time = None
    if data.scheduled_time:
        from datetime import datetime

        try:
            scheduled_time = datetime.fromisoformat(data.scheduled_time.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid scheduled_time format")

    # Create queue item
    queue_item = PrintQueueItem(
        printer_id=data.printer_id,
        archive_id=data.archive_id,
        project_id=data.project_id,
        position=next_position,
        scheduled_time=scheduled_time,
        require_previous_success=data.require_previous_success,
        auto_off_after=data.auto_off_after,
        # No dialog to pick this per job, so the install-wide default decides
        # whether the finished print asks for a verdict (#1898).
        confirm_outcome=await confirm_outcome_for_new_queue_item(db),
        # Attribute to the key's owner so the item shows up under `queue:read_own`
        # for the person whose key it is. Legacy keys predating per-user ownership
        # have no `user_id`, and those rows stay ownerless.
        created_by_id=api_key.user_id,
        # Waits for someone to start it unless the owner may print without review (#1620)
        manual_start=await is_auth_enabled(db) and queue_review_required_for(await resolve_apikey_owner(db, api_key)),
    )
    db.add(queue_item)
    await db.flush()
    await db.refresh(queue_item)

    return QueueAddResponse(
        id=queue_item.id,
        archive_id=queue_item.archive_id,
        printer_id=queue_item.printer_id,
        position=queue_item.position,
        status=queue_item.status,
        message=f"Added to queue at position {queue_item.position}",
    )


@router.post("/printer/{printer_id}/start")
async def webhook_start_print(
    printer_id: int,
    api_key: APIKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Trigger the next manual-start queue item on a printer.

    Mirrors `POST /print-queue/{item_id}/start`: clears `manual_start` on
    the next pending item so the scheduler picks it up — which handles
    FTP upload, AMS mapping, and all print options (timelapse,
    bed_levelling, etc.) correctly via the queue's stored fields. The
    previous implementation called `printer_manager.start_print()`
    directly with `archive_id` as the filename arg and no print options,
    bypassing the upload step entirely and discarding the user's
    workflow choices — it 500'd before ever reaching the printer.

    Requires 'can_control_printer' permission.
    """
    await check_webhook_permission(db, api_key, "control_printer")
    await ensure_api_key_printer_access(db, api_key, printer_id)

    # Get printer
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")

    # Get next pending queue item
    result = await db.execute(
        select(PrintQueueItem)
        .where(
            PrintQueueItem.printer_id == printer_id,
            PrintQueueItem.status == "pending",
        )
        .order_by(PrintQueueItem.position)
        .limit(1)
    )
    queue_item = result.scalar_one_or_none()
    if not queue_item:
        raise HTTPException(status_code=404, detail="No pending prints in queue")

    # Starting a waiting job is a review decision (#1620): a key whose owner
    # needs review for their own jobs can't make one for anybody's
    if (
        queue_item.manual_start
        and await is_auth_enabled(db)
        and queue_review_required_for(await resolve_apikey_owner(db, api_key))
    ):
        raise HTTPException(
            status_code=403,
            detail="The next job waits for review: someone who can manage all queue jobs has to start it",
        )

    # Clear manual_start so the scheduler will dispatch. If the item was
    # already auto-dispatchable this is a no-op; the scheduler will still
    # pick it up on its next tick.
    queue_item.manual_start = False
    await db.commit()
    await db.refresh(queue_item)

    logger.info("Webhook started queue item %s on printer %s", queue_item.id, printer_id)
    return {"message": "Print started", "queue_item_id": queue_item.id}


# States in which a print can be stopped, as with the Stop button on the
# printer card: printing or paused.
_STOPPABLE_STATES = ("RUNNING", "PAUSE")


def _stop_current_print(printer_id: int) -> None:
    """Stop the print on the user's behalf (cancel and stop are the same command)."""
    try:
        sent = stop_print_by_user(printer_id)
    except Exception as e:
        logger.error("Failed to stop print on printer %s: %s", printer_id, e)
        raise HTTPException(status_code=500, detail=str(e))
    if not sent:
        raise HTTPException(status_code=503, detail="Printer not connected")


@router.post("/printer/{printer_id}/stop")
async def webhook_stop_print(
    printer_id: int,
    api_key: APIKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Stop the current print on a printer.

    Requires 'can_control_printer' permission.
    """
    await check_webhook_permission(db, api_key, "control_printer")
    await ensure_api_key_printer_access(db, api_key, printer_id)

    status = printer_manager.get_status(printer_id)
    # `printer_manager.get_status(...)` returns a ``PrinterState`` dataclass
    # (see backend/app/services/bambu_mqtt.py), not a dict — `.get(...)` on it
    # raises AttributeError and surfaces as a generic 500 (#1584).
    if not status or not status.connected:
        raise HTTPException(status_code=503, detail="Printer not connected")

    if status.state not in _STOPPABLE_STATES:
        raise HTTPException(status_code=409, detail="No print in progress")

    _stop_current_print(printer_id)

    return {"message": "Print stopped"}


@router.post("/printer/{printer_id}/cancel")
async def webhook_cancel_print(
    printer_id: int,
    api_key: APIKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Cancel the current print on a printer.

    Requires 'can_control_printer' permission.
    """
    await check_webhook_permission(db, api_key, "control_printer")
    await ensure_api_key_printer_access(db, api_key, printer_id)

    status = printer_manager.get_status(printer_id)
    # Same dataclass-not-dict shape as stop_print above (#1584).
    if not status or not status.connected:
        raise HTTPException(status_code=503, detail="Printer not connected")

    if status.state not in _STOPPABLE_STATES:
        raise HTTPException(status_code=409, detail="No print to cancel")

    _stop_current_print(printer_id)

    return {"message": "Print cancelled"}


@router.get("/printer/{printer_id}/status", response_model=PrinterStatusResponse)
async def webhook_get_printer_status(
    printer_id: int,
    api_key: APIKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Get status of a printer.

    Requires 'can_read_status' permission.
    """
    await check_webhook_permission(db, api_key, "read_status")
    await ensure_api_key_printer_access(db, api_key, printer_id)

    # Get printer
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")

    status = printer_manager.get_status(printer_id)

    # `printer_manager.get_status(...)` returns a ``PrinterState`` dataclass —
    # attribute access, not dict lookup. The previous `.get(...)` calls raised
    # AttributeError and surfaced as a generic 500 for any printer that
    # actually had a status row (#1584).
    if status is None:
        return PrinterStatusResponse(
            id=printer.id,
            name=printer.name,
            serial_number=printer.serial_number,
            connected=False,
            state=None,
            current_print=None,
            progress=None,
            remaining_time=None,
        )
    return PrinterStatusResponse(
        id=printer.id,
        name=printer.name,
        serial_number=printer.serial_number,
        connected=status.connected,
        state=status.state,
        current_print=status.current_print,
        progress=status.progress,
        remaining_time=status.remaining_time,
        remaining_seconds=status.remaining_time * 60 if status.remaining_time is not None else None,
        layer_num=status.layer_num,
        total_layers=status.total_layers,
        subtask_id=_job_id(status.subtask_id),
        hms_errors=hms_error_responses(status.hms_errors),
    )


@router.get("/queue", response_model=list[QueueStatusResponse])
async def webhook_get_queue_status(
    printer_id: int | None = None,
    api_key: APIKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Get queue status for all printers or a specific printer.

    Requires 'can_read_status' permission.
    """
    await check_webhook_permission(db, api_key, "read_status")

    # Get printers
    if printer_id:
        await ensure_api_key_printer_access(db, api_key, printer_id)
        result = await db.execute(select(Printer).where(Printer.id == printer_id))
        printers = result.scalars().all()
    else:
        result = await db.execute(select(Printer))
        printers = result.scalars().all()
        # Only the printers the key (and its owner) may reach
        scope = await api_key_printer_scope(db, api_key)
        printers = [p for p in printers if scope.allows(p.id)]

    response = []
    for printer in printers:
        # Get queue items
        result = await db.execute(
            select(PrintQueueItem)
            .where(
                PrintQueueItem.printer_id == printer.id,
                PrintQueueItem.status.in_(["pending", "printing"]),
            )
            .order_by(PrintQueueItem.position)
        )
        items = result.scalars().all()

        pending_count = sum(1 for i in items if i.status == "pending")
        printing_count = sum(1 for i in items if i.status == "printing")

        response.append(
            QueueStatusResponse(
                printer_id=printer.id,
                printer_name=printer.name,
                pending=pending_count,
                printing=printing_count,
                items=[
                    {
                        "id": item.id,
                        "archive_id": item.archive_id,
                        "position": item.position,
                        "status": item.status,
                    }
                    for item in items
                ],
            )
        )

    return response
