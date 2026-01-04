import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import check_permission, check_printer_access, get_api_key
from backend.app.core.database import get_db
from backend.app.models.api_key import APIKey
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.services.printer_manager import printer_manager

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
    connected: bool
    state: str | None
    current_print: str | None
    progress: float | None
    remaining_time: int | None


class QueueStatusResponse(BaseModel):
    printer_id: int
    printer_name: str
    pending: int
    printing: int
    items: list[dict]


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
    check_permission(api_key, "queue")
    check_printer_access(api_key, data.printer_id)

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

    # Get next position
    result = await db.execute(
        select(PrintQueueItem.position)
        .where(
            PrintQueueItem.printer_id == data.printer_id,
            PrintQueueItem.status == "pending",
        )
        .order_by(PrintQueueItem.position.desc())
        .limit(1)
    )
    max_position = result.scalar()
    next_position = (max_position or 0) + 1

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
    """Start the next queued print on a printer.

    Requires 'can_control_printer' permission.
    """
    check_permission(api_key, "control_printer")
    check_printer_access(api_key, printer_id)

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

    # Check if printer is ready
    status = printer_manager.get_status(printer_id)
    if not status or not status.get("connected"):
        raise HTTPException(status_code=503, detail="Printer not connected")

    if status.get("state") not in ["IDLE", "FINISH", "FAILED"]:
        raise HTTPException(status_code=409, detail=f"Printer is busy (state: {status.get('state')})")

    # Start the print
    try:
        await printer_manager.start_print(printer_id, queue_item.archive_id)
    except Exception as e:
        logger.error(f"Failed to start print: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"message": "Print started", "queue_item_id": queue_item.id}


@router.post("/printer/{printer_id}/stop")
async def webhook_stop_print(
    printer_id: int,
    api_key: APIKey = Depends(get_api_key),
):
    """Stop the current print on a printer.

    Requires 'can_control_printer' permission.
    """
    check_permission(api_key, "control_printer")
    check_printer_access(api_key, printer_id)

    status = printer_manager.get_status(printer_id)
    if not status or not status.get("connected"):
        raise HTTPException(status_code=503, detail="Printer not connected")

    if status.get("state") != "RUNNING":
        raise HTTPException(status_code=409, detail="No print in progress")

    try:
        await printer_manager.stop_print(printer_id)
    except Exception as e:
        logger.error(f"Failed to stop print: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"message": "Print stopped"}


@router.post("/printer/{printer_id}/cancel")
async def webhook_cancel_print(
    printer_id: int,
    api_key: APIKey = Depends(get_api_key),
):
    """Cancel the current print on a printer.

    Requires 'can_control_printer' permission.
    """
    check_permission(api_key, "control_printer")
    check_printer_access(api_key, printer_id)

    status = printer_manager.get_status(printer_id)
    if not status or not status.get("connected"):
        raise HTTPException(status_code=503, detail="Printer not connected")

    if status.get("state") not in ["RUNNING", "PAUSE"]:
        raise HTTPException(status_code=409, detail="No print to cancel")

    try:
        await printer_manager.cancel_print(printer_id)
    except Exception as e:
        logger.error(f"Failed to cancel print: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
    check_permission(api_key, "read_status")
    check_printer_access(api_key, printer_id)

    # Get printer
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")

    status = printer_manager.get_status(printer_id)

    return PrinterStatusResponse(
        id=printer.id,
        name=printer.name,
        connected=status.get("connected", False) if status else False,
        state=status.get("state") if status else None,
        current_print=status.get("current_print") if status else None,
        progress=status.get("progress") if status else None,
        remaining_time=status.get("remaining_time") if status else None,
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
    check_permission(api_key, "read_status")

    # Get printers
    if printer_id:
        check_printer_access(api_key, printer_id)
        result = await db.execute(select(Printer).where(Printer.id == printer_id))
        printers = result.scalars().all()
    else:
        result = await db.execute(select(Printer))
        printers = result.scalars().all()
        # Filter by allowed printers if limited
        if api_key.printer_ids:
            printers = [p for p in printers if p.id in api_key.printer_ids]

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
