"""API routes for print queue management."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.database import get_db
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.schemas.print_queue import (
    PrintQueueItemCreate,
    PrintQueueItemResponse,
    PrintQueueItemUpdate,
    PrintQueueReorder,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/queue", tags=["queue"])


def _enrich_response(item: PrintQueueItem) -> PrintQueueItemResponse:
    """Add nested archive/printer info to response."""
    response = PrintQueueItemResponse.model_validate(item)
    if item.archive:
        response.archive_name = item.archive.print_name or item.archive.filename
        response.archive_thumbnail = item.archive.thumbnail_path
        response.print_time_seconds = item.archive.print_time_seconds
    if item.printer:
        response.printer_name = item.printer.name
    return response


@router.get("/", response_model=list[PrintQueueItemResponse])
async def list_queue(
    printer_id: int | None = Query(None, description="Filter by printer"),
    status: str | None = Query(None, description="Filter by status"),
    db: AsyncSession = Depends(get_db),
):
    """List all queue items, optionally filtered by printer or status."""
    query = (
        select(PrintQueueItem)
        .options(selectinload(PrintQueueItem.archive), selectinload(PrintQueueItem.printer))
        .order_by(PrintQueueItem.printer_id, PrintQueueItem.position)
    )

    if printer_id is not None:
        query = query.where(PrintQueueItem.printer_id == printer_id)
    if status:
        query = query.where(PrintQueueItem.status == status)

    result = await db.execute(query)
    items = result.scalars().all()
    return [_enrich_response(item) for item in items]


@router.post("/", response_model=PrintQueueItemResponse)
async def add_to_queue(
    data: PrintQueueItemCreate,
    db: AsyncSession = Depends(get_db),
):
    """Add an item to the print queue."""
    # Validate printer exists
    result = await db.execute(select(Printer).where(Printer.id == data.printer_id))
    if not result.scalar_one_or_none():
        raise HTTPException(400, "Printer not found")

    # Validate archive exists
    result = await db.execute(select(PrintArchive).where(PrintArchive.id == data.archive_id))
    if not result.scalar_one_or_none():
        raise HTTPException(400, "Archive not found")

    # Get next position for this printer
    result = await db.execute(
        select(func.max(PrintQueueItem.position))
        .where(PrintQueueItem.printer_id == data.printer_id)
        .where(PrintQueueItem.status == "pending")
    )
    max_pos = result.scalar() or 0

    item = PrintQueueItem(
        printer_id=data.printer_id,
        archive_id=data.archive_id,
        scheduled_time=data.scheduled_time,
        require_previous_success=data.require_previous_success,
        auto_off_after=data.auto_off_after,
        manual_start=data.manual_start,
        position=max_pos + 1,
        status="pending",
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)

    # Load relationships for response
    await db.refresh(item, ["archive", "printer"])

    logger.info(f"Added archive {data.archive_id} to queue for printer {data.printer_id}")
    return _enrich_response(item)


@router.get("/{item_id}", response_model=PrintQueueItemResponse)
async def get_queue_item(item_id: int, db: AsyncSession = Depends(get_db)):
    """Get a specific queue item."""
    result = await db.execute(
        select(PrintQueueItem)
        .options(selectinload(PrintQueueItem.archive), selectinload(PrintQueueItem.printer))
        .where(PrintQueueItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Queue item not found")
    return _enrich_response(item)


@router.patch("/{item_id}", response_model=PrintQueueItemResponse)
async def update_queue_item(
    item_id: int,
    data: PrintQueueItemUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a queue item."""
    result = await db.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Queue item not found")

    if item.status != "pending":
        raise HTTPException(400, "Can only update pending items")

    update_data = data.model_dump(exclude_unset=True)

    # Validate new printer_id if being changed
    if "printer_id" in update_data:
        result = await db.execute(select(Printer).where(Printer.id == update_data["printer_id"]))
        if not result.scalar_one_or_none():
            raise HTTPException(400, "Printer not found")

    for field, value in update_data.items():
        setattr(item, field, value)

    await db.commit()
    await db.refresh(item, ["archive", "printer"])

    logger.info(f"Updated queue item {item_id}")
    return _enrich_response(item)


@router.delete("/{item_id}")
async def delete_queue_item(item_id: int, db: AsyncSession = Depends(get_db)):
    """Remove an item from the queue."""
    result = await db.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Queue item not found")

    if item.status == "printing":
        raise HTTPException(400, "Cannot delete item that is currently printing")

    await db.delete(item)
    await db.commit()

    logger.info(f"Deleted queue item {item_id}")
    return {"message": "Queue item deleted"}


@router.post("/reorder")
async def reorder_queue(
    data: PrintQueueReorder,
    db: AsyncSession = Depends(get_db),
):
    """Bulk update positions for queue items."""
    for reorder_item in data.items:
        result = await db.execute(select(PrintQueueItem).where(PrintQueueItem.id == reorder_item.id))
        item = result.scalar_one_or_none()
        if item and item.status == "pending":
            item.position = reorder_item.position

    await db.commit()
    logger.info(f"Reordered {len(data.items)} queue items")
    return {"message": f"Reordered {len(data.items)} items"}


@router.post("/{item_id}/cancel")
async def cancel_queue_item(item_id: int, db: AsyncSession = Depends(get_db)):
    """Cancel a pending queue item."""
    result = await db.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Queue item not found")

    if item.status not in ("pending",):
        raise HTTPException(400, f"Cannot cancel item with status '{item.status}'")

    item.status = "cancelled"
    item.completed_at = datetime.now()
    await db.commit()

    logger.info(f"Cancelled queue item {item_id}")
    return {"message": "Queue item cancelled"}


@router.post("/{item_id}/stop")
async def stop_queue_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Stop an actively printing queue item."""
    import asyncio

    from backend.app.models.smart_plug import SmartPlug
    from backend.app.services.printer_manager import printer_manager
    from backend.app.services.tasmota import tasmota_service

    result = await db.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Queue item not found")

    if item.status != "printing":
        raise HTTPException(400, f"Can only stop items that are printing, current status: '{item.status}'")

    # Capture values we need for background task
    printer_id = item.printer_id
    auto_off_after = item.auto_off_after

    # Try to send stop command to printer
    stop_sent = False
    try:
        stop_sent = printer_manager.stop_print(printer_id)
        if not stop_sent:
            logger.warning(f"stop_print returned False for printer {printer_id} - printer may not be connected")
    except Exception as e:
        logger.error(f"Error sending stop command for queue item {item_id}: {e}")

    # Update queue item status regardless - if printer is off, print is already stopped
    item.status = "cancelled"
    item.completed_at = datetime.now()
    item.error_message = "Stopped by user" if stop_sent else "Stopped by user (printer was offline)"
    await db.commit()

    # Get smart plug info if auto-off is enabled
    plug_ip = None
    if auto_off_after:
        result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
        plug = result.scalar_one_or_none()
        if plug and plug.enabled:
            plug_ip = plug.ip_address

    logger.info(f"Stopped printing queue item {item_id} (stop command sent: {stop_sent})")

    # Schedule background task for cooldown + power off
    if plug_ip:

        async def cooldown_and_poweroff():
            logger.info(f"Auto-off: Waiting for printer {printer_id} to cool down before power off...")
            await printer_manager.wait_for_cooldown(printer_id, target_temp=50.0, timeout=600)
            # Re-fetch plug since we're in a new async context
            from backend.app.core.database import async_session

            async with async_session() as new_db:
                result = await new_db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
                plug = result.scalar_one_or_none()
                if plug and plug.enabled:
                    logger.info(f"Auto-off: Powering off printer {printer_id}")
                    await tasmota_service.turn_off(plug)

        asyncio.create_task(cooldown_and_poweroff())

    return {"message": "Print stopped" if stop_sent else "Queue item cancelled (printer was offline)"}


@router.post("/{item_id}/start")
async def start_queue_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Manually start a staged (manual_start) queue item.

    This clears the manual_start flag so the scheduler will pick it up,
    or starts immediately if the printer is ready.
    """
    result = await db.execute(
        select(PrintQueueItem)
        .options(selectinload(PrintQueueItem.archive), selectinload(PrintQueueItem.printer))
        .where(PrintQueueItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Queue item not found")

    if item.status != "pending":
        raise HTTPException(400, f"Can only start pending items, current status: '{item.status}'")

    # Clear manual_start flag so scheduler picks it up
    item.manual_start = False
    await db.commit()
    await db.refresh(item, ["archive", "printer"])

    logger.info(f"Manually started queue item {item_id} (cleared manual_start flag)")
    return _enrich_response(item)
