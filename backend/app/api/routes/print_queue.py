"""API routes for print queue management."""

import json
import logging
import zipfile
from datetime import datetime
from pathlib import Path

import defusedxml.ElementTree as ET
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.auth import RequirePermissionIfAuthEnabled, require_ownership_permission
from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.user import User
from backend.app.schemas.print_queue import (
    PrintQueueBulkUpdate,
    PrintQueueBulkUpdateResponse,
    PrintQueueItemCreate,
    PrintQueueItemResponse,
    PrintQueueItemUpdate,
    PrintQueueReorder,
)
from backend.app.services.notification_service import notification_service
from backend.app.utils.printer_models import normalize_printer_model, normalize_printer_model_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/queue", tags=["queue"])


def _extract_filament_types_from_3mf(file_path: Path, plate_id: int | None = None) -> list[str]:
    """Extract unique filament types from a 3MF file.

    Args:
        file_path: Path to the 3MF file
        plate_id: Optional plate index to filter for (for multi-plate files)

    Returns:
        List of unique filament types (e.g., ["PLA", "PETG"])
    """
    types: set[str] = set()

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            if "Metadata/slice_info.config" not in zf.namelist():
                return []

            content = zf.read("Metadata/slice_info.config").decode()
            root = ET.fromstring(content)

            if plate_id is not None:
                # Find the plate element with matching index
                for plate_elem in root.findall(".//plate"):
                    plate_index = None
                    for meta in plate_elem.findall("metadata"):
                        if meta.get("key") == "index":
                            try:
                                plate_index = int(meta.get("value", "0"))
                            except ValueError:
                                pass  # Skip plate with unparseable index
                            break

                    if plate_index == plate_id:
                        for filament_elem in plate_elem.findall("filament"):
                            filament_type = filament_elem.get("type", "")
                            used_g = filament_elem.get("used_g", "0")
                            try:
                                used_grams = float(used_g)
                            except (ValueError, TypeError):
                                used_grams = 0
                            if used_grams > 0 and filament_type:
                                types.add(filament_type)
                        break
            else:
                # No plate_id specified - extract all filaments with used_g > 0
                for filament_elem in root.findall(".//filament"):
                    filament_type = filament_elem.get("type", "")
                    used_g = filament_elem.get("used_g", "0")
                    try:
                        used_grams = float(used_g)
                    except (ValueError, TypeError):
                        used_grams = 0
                    if used_grams > 0 and filament_type:
                        types.add(filament_type)

    except Exception as e:
        logger.warning("Failed to extract filament types from %s: %s", file_path, e)

    return sorted(types)


def _extract_print_time_from_3mf(file_path: Path, plate_id: int | None = None) -> int | None:
    """Extract print time (prediction) from a 3MF file.

    Args:
        file_path: Path to the 3MF file
        plate_id: Optional plate index to filter for (for multi-plate files)

    Returns:
        Print time in seconds, or None if not found
    """
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            if "Metadata/slice_info.config" not in zf.namelist():
                return None

            content = zf.read("Metadata/slice_info.config").decode()
            root = ET.fromstring(content)

            if plate_id is not None:
                for plate_elem in root.findall(".//plate"):
                    plate_index = None
                    for meta in plate_elem.findall("metadata"):
                        if meta.get("key") == "index":
                            try:
                                plate_index = int(meta.get("value", "0"))
                            except ValueError:
                                pass  # Skip plate with unparseable index
                            break

                    if plate_index == plate_id:
                        for meta in plate_elem.findall("metadata"):
                            if meta.get("key") == "prediction":
                                try:
                                    return int(meta.get("value", "0"))
                                except ValueError:
                                    return None
                        break
            else:
                plate_elem = root.find(".//plate")
                if plate_elem is not None:
                    for meta in plate_elem.findall("metadata"):
                        if meta.get("key") == "prediction":
                            try:
                                return int(meta.get("value", "0"))
                            except ValueError:
                                return None
    except Exception as e:
        logger.warning("Failed to extract print time from %s: %s", file_path, e)

    return None


def _enrich_response(item: PrintQueueItem) -> PrintQueueItemResponse:
    """Add nested archive/printer/library_file info to response."""
    # Parse ams_mapping from JSON string BEFORE model_validate
    ams_mapping_parsed = None
    if item.ams_mapping:
        try:
            ams_mapping_parsed = json.loads(item.ams_mapping)
        except json.JSONDecodeError:
            ams_mapping_parsed = None

    # Parse required_filament_types from JSON string
    required_filament_types_parsed = None
    if item.required_filament_types:
        try:
            required_filament_types_parsed = json.loads(item.required_filament_types)
        except json.JSONDecodeError:
            required_filament_types_parsed = None

    # Create response with parsed ams_mapping
    item_dict = {
        "id": item.id,
        "printer_id": item.printer_id,
        "target_model": item.target_model,
        "target_location": item.target_location,
        "required_filament_types": required_filament_types_parsed,
        "waiting_reason": item.waiting_reason,
        "archive_id": item.archive_id,
        "library_file_id": item.library_file_id,
        "position": item.position,
        "scheduled_time": item.scheduled_time,
        "require_previous_success": item.require_previous_success,
        "auto_off_after": item.auto_off_after,
        "manual_start": item.manual_start,
        "ams_mapping": ams_mapping_parsed,
        "plate_id": item.plate_id,
        "bed_levelling": item.bed_levelling,
        "flow_cali": item.flow_cali,
        "vibration_cali": item.vibration_cali,
        "layer_inspect": item.layer_inspect,
        "timelapse": item.timelapse,
        "use_ams": item.use_ams,
        "status": item.status,
        "started_at": item.started_at,
        "completed_at": item.completed_at,
        "error_message": item.error_message,
        "created_at": item.created_at,
        # User tracking (Issue #206)
        "created_by_id": item.created_by_id,
        "created_by_username": item.created_by.username if item.created_by else None,
    }
    response = PrintQueueItemResponse(**item_dict)
    if item.archive:
        response.archive_name = item.archive.print_name or item.archive.filename
        response.archive_thumbnail = item.archive.thumbnail_path
        response.print_time_seconds = item.archive.print_time_seconds
        if item.plate_id:
            archive_path = settings.base_dir / item.archive.file_path
            if archive_path.exists():
                plate_time = _extract_print_time_from_3mf(archive_path, item.plate_id)
                if plate_time is not None:
                    response.print_time_seconds = plate_time
    if item.library_file:
        response.library_file_name = (
            item.library_file.file_metadata.get("print_name") if item.library_file.file_metadata else None
        )
        if not response.library_file_name:
            response.library_file_name = item.library_file.filename
        response.library_file_thumbnail = item.library_file.thumbnail_path
        # Get print time from library file metadata if no archive
        if not item.archive and item.library_file.file_metadata:
            response.print_time_seconds = item.library_file.file_metadata.get("print_time_seconds")
        if item.plate_id:
            lib_path = Path(item.library_file.file_path)
            library_file_path = lib_path if lib_path.is_absolute() else settings.base_dir / item.library_file.file_path
            if library_file_path.exists():
                plate_time = _extract_print_time_from_3mf(library_file_path, item.plate_id)
                if plate_time is not None:
                    response.print_time_seconds = plate_time
    if item.printer:
        response.printer_name = item.printer.name
        # Include part_removal_required for completed jobs
        if item.status == "completed" and item.printer.part_removal_enabled:
            response.part_removal_required = item.printer.part_removal_required
    return response


@router.get("/", response_model=list[PrintQueueItemResponse])
async def list_queue(
    printer_id: int | None = Query(None, description="Filter by printer (-1 for unassigned)"),
    status: str | None = Query(None, description="Filter by status"),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.QUEUE_READ),
):
    """List all queue items, optionally filtered by printer or status."""
    query = (
        select(PrintQueueItem)
        .options(
            selectinload(PrintQueueItem.archive),
            selectinload(PrintQueueItem.printer),
            selectinload(PrintQueueItem.library_file),
            selectinload(PrintQueueItem.created_by),
        )
        .order_by(PrintQueueItem.printer_id.nulls_first(), PrintQueueItem.position)
    )

    if printer_id is not None:
        if printer_id == -1:
            # Special value: filter for unassigned items
            query = query.where(PrintQueueItem.printer_id.is_(None))
        else:
            query = query.where(PrintQueueItem.printer_id == printer_id)

    # Special status filter for "not_collected" - completed jobs with part_removal_required
    if status == "not_collected":
        # Join with printer to filter by part_removal_required
        from backend.app.models.printer import Printer
        query = (
            query.join(Printer, PrintQueueItem.printer_id == Printer.id)
            .where(PrintQueueItem.status == "completed")
            .where(Printer.part_removal_enabled == True)  # noqa: E712
            .where(Printer.part_removal_required == True)  # noqa: E712
        )
    elif status:
        query = query.where(PrintQueueItem.status == status)

    result = await db.execute(query)
    items = result.scalars().all()
    return [_enrich_response(item) for item in items]


@router.post("/", response_model=PrintQueueItemResponse)
async def add_to_queue(
    data: PrintQueueItemCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.QUEUE_CREATE),
):
    """Add an item to the print queue."""
    # Normalize target_model (e.g., "Bambu Lab X1E" / "C13" -> "X1E")
    target_model_norm = None
    if data.target_model:
        target_model_norm = (
            normalize_printer_model(data.target_model)
            or normalize_printer_model_id(data.target_model)
            or data.target_model
        )

    # Validate that either archive_id or library_file_id is provided
    if not data.archive_id and not data.library_file_id:
        raise HTTPException(400, "Either archive_id or library_file_id must be provided")

    # Cannot specify both printer_id and target_model
    if data.printer_id and target_model_norm:
        raise HTTPException(400, "Cannot specify both printer_id and target_model")

    # Validate printer exists (if assigned)
    if data.printer_id is not None:
        result = await db.execute(select(Printer).where(Printer.id == data.printer_id))
        if not result.scalar_one_or_none():
            raise HTTPException(400, "Printer not found")

    # Validate target_model has active printers
    if target_model_norm:
        result = await db.execute(
            select(Printer).where(Printer.model == target_model_norm).where(Printer.is_active == True)  # noqa: E712
        )
        if not result.scalars().first():
            raise HTTPException(400, f"No active printers for model: {target_model_norm}")

    # Validate archive exists (if provided) and get it for filament extraction
    archive = None
    if data.archive_id:
        result = await db.execute(select(PrintArchive).where(PrintArchive.id == data.archive_id))
        archive = result.scalar_one_or_none()
        if not archive:
            raise HTTPException(400, "Archive not found")

    # Validate library file exists (if provided) and get it for filament extraction
    library_file = None
    if data.library_file_id:
        result = await db.execute(select(LibraryFile).where(LibraryFile.id == data.library_file_id))
        library_file = result.scalar_one_or_none()
        if not library_file:
            raise HTTPException(400, "Library file not found")

    # Extract filament types for model-based assignment (used by scheduler for validation)
    required_filament_types = None
    if target_model_norm:
        # Get file path from archive or library file
        file_path = None
        if archive:
            file_path = settings.base_dir / archive.file_path
        elif library_file:
            lib_path = Path(library_file.file_path)
            file_path = lib_path if lib_path.is_absolute() else settings.base_dir / library_file.file_path

        if file_path and file_path.exists():
            filament_types = _extract_filament_types_from_3mf(file_path, data.plate_id)
            if filament_types:
                required_filament_types = json.dumps(filament_types)
                logger.info("Extracted filament types for model-based queue: %s", filament_types)

    # Get next position for this printer (or for unassigned/model-based items)
    if data.printer_id is not None:
        result = await db.execute(
            select(func.max(PrintQueueItem.position))
            .where(PrintQueueItem.printer_id == data.printer_id)
            .where(PrintQueueItem.status == "pending")
        )
    else:
        # For unassigned/model-based items, get max position across all unassigned
        result = await db.execute(
            select(func.max(PrintQueueItem.position))
            .where(PrintQueueItem.printer_id.is_(None))
            .where(PrintQueueItem.status == "pending")
        )
    max_pos = result.scalar() or 0

    item = PrintQueueItem(
        printer_id=data.printer_id,
        target_model=target_model_norm,
        target_location=data.target_location,
        required_filament_types=required_filament_types,
        archive_id=data.archive_id,
        library_file_id=data.library_file_id,
        scheduled_time=data.scheduled_time,
        require_previous_success=data.require_previous_success,
        auto_off_after=data.auto_off_after,
        manual_start=data.manual_start,
        ams_mapping=json.dumps(data.ams_mapping) if data.ams_mapping else None,
        plate_id=data.plate_id,
        bed_levelling=data.bed_levelling,
        flow_cali=data.flow_cali,
        vibration_cali=data.vibration_cali,
        layer_inspect=data.layer_inspect,
        timelapse=data.timelapse,
        use_ams=data.use_ams,
        position=max_pos + 1,
        status="pending",
        created_by_id=current_user.id if current_user else None,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)

    # Load relationships for response
    await db.refresh(item, ["archive", "printer", "library_file", "created_by"])

    source_name = f"archive {data.archive_id}" if data.archive_id else f"library file {data.library_file_id}"
    target_desc = data.printer_id or (f"model {target_model_norm}" if target_model_norm else "unassigned")
    logger.info("Added %s to queue for %s", source_name, target_desc)

    # MQTT relay - publish queue job added
    try:
        from backend.app.services.mqtt_relay import mqtt_relay

        await mqtt_relay.on_queue_job_added(
            job_id=item.id,
            filename=item.archive.filename if item.archive else "",
            printer_id=item.printer_id,
            printer_name=item.printer.name if item.printer else None,
        )
    except Exception:
        pass  # Don't fail queue add if MQTT fails

    # Send notification for job added
    try:
        job_name = (
            item.archive.filename
            if item.archive
            else item.library_file.filename
            if item.library_file
            else f"Job #{item.id}"
        )
        job_name = job_name.replace(".gcode.3mf", "").replace(".3mf", "")
        target = (
            item.printer.name if item.printer else (f"Any {item.target_model}" if target_model_norm else "Unassigned")
        )
        await notification_service.on_queue_job_added(
            job_name=job_name,
            target=target,
            db=db,
            printer_id=item.printer_id,
            printer_name=item.printer.name if item.printer else None,
        )
    except Exception:
        pass  # Don't fail queue add if notification fails

    return _enrich_response(item)


@router.patch("/bulk", response_model=PrintQueueBulkUpdateResponse)
async def bulk_update_queue_items(
    data: PrintQueueBulkUpdate,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.QUEUE_UPDATE_ALL,
            Permission.QUEUE_UPDATE_OWN,
        )
    ),
):
    """Bulk update multiple queue items with the same values.

    Only pending items can be updated. Non-pending items are skipped.
    Items not owned by the user are also skipped (unless user has *_all permission).
    """
    user, can_modify_all = auth_result

    if not data.item_ids:
        raise HTTPException(400, "No item IDs provided")

    # Get fields to update (exclude item_ids and unset fields)
    update_data = data.model_dump(exclude={"item_ids"}, exclude_unset=True)
    if not update_data:
        raise HTTPException(400, "No fields to update")

    # Validate printer_id if being changed
    if "printer_id" in update_data and update_data["printer_id"] is not None:
        result = await db.execute(select(Printer).where(Printer.id == update_data["printer_id"]))
        if not result.scalar_one_or_none():
            raise HTTPException(400, "Printer not found")

    # Fetch all items
    result = await db.execute(select(PrintQueueItem).where(PrintQueueItem.id.in_(data.item_ids)))
    items = result.scalars().all()

    updated_count = 0
    skipped_count = 0

    for item in items:
        if item.status != "pending":
            skipped_count += 1
            continue

        # Ownership check
        if not can_modify_all and item.created_by_id != user.id:
            skipped_count += 1
            continue

        for field, value in update_data.items():
            setattr(item, field, value)
        updated_count += 1

    await db.commit()

    logger.info("Bulk updated %s queue items, skipped %s", updated_count, skipped_count)
    return PrintQueueBulkUpdateResponse(
        updated_count=updated_count,
        skipped_count=skipped_count,
        message=f"Updated {updated_count} items"
        + (f", skipped {skipped_count} non-pending/not-owned" if skipped_count else ""),
    )


@router.get("/{item_id}", response_model=PrintQueueItemResponse)
async def get_queue_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.QUEUE_READ),
):
    """Get a specific queue item."""
    result = await db.execute(
        select(PrintQueueItem)
        .options(
            selectinload(PrintQueueItem.archive),
            selectinload(PrintQueueItem.printer),
            selectinload(PrintQueueItem.library_file),
            selectinload(PrintQueueItem.created_by),
        )
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
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.QUEUE_UPDATE_ALL,
            Permission.QUEUE_UPDATE_OWN,
        )
    ),
):
    """Update a queue item."""
    user, can_modify_all = auth_result

    result = await db.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Queue item not found")

    # Ownership check
    if not can_modify_all:
        if item.created_by_id != user.id:
            raise HTTPException(403, "You can only update your own queue items")

    if item.status != "pending":
        raise HTTPException(400, "Can only update pending items")

    update_data = data.model_dump(exclude_unset=True)

    # Normalize target_model if being updated
    if "target_model" in update_data and update_data["target_model"]:
        update_data["target_model"] = (
            normalize_printer_model(update_data["target_model"])
            or normalize_printer_model_id(update_data["target_model"])
            or update_data["target_model"]
        )

    # Cannot specify both printer_id and target_model
    new_printer_id = update_data.get("printer_id", item.printer_id)
    new_target_model = update_data.get("target_model", item.target_model)
    if new_printer_id and new_target_model:
        raise HTTPException(400, "Cannot specify both printer_id and target_model")

    # Validate new printer_id if being changed (and not None)
    if "printer_id" in update_data and update_data["printer_id"] is not None:
        result = await db.execute(select(Printer).where(Printer.id == update_data["printer_id"]))
        if not result.scalar_one_or_none():
            raise HTTPException(400, "Printer not found")

    # Validate target_model has active printers
    if "target_model" in update_data and update_data["target_model"]:
        result = await db.execute(
            select(Printer).where(Printer.model == update_data["target_model"]).where(Printer.is_active == True)  # noqa: E712
        )
        if not result.scalars().first():
            raise HTTPException(400, f"No active printers for model: {update_data['target_model']}")

    # Serialize ams_mapping to JSON for TEXT column storage
    if "ams_mapping" in update_data:
        update_data["ams_mapping"] = json.dumps(update_data["ams_mapping"]) if update_data["ams_mapping"] else None

    for field, value in update_data.items():
        setattr(item, field, value)

    await db.commit()
    await db.refresh(item, ["archive", "printer", "library_file", "created_by"])

    logger.info("Updated queue item %s", item_id)
    return _enrich_response(item)


@router.delete("/{item_id}")
async def delete_queue_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.QUEUE_DELETE_ALL,
            Permission.QUEUE_DELETE_OWN,
        )
    ),
):
    """Remove an item from the queue."""
    user, can_modify_all = auth_result

    result = await db.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Queue item not found")

    # Ownership check
    if not can_modify_all:
        if item.created_by_id != user.id:
            raise HTTPException(403, "You can only delete your own queue items")

    if item.status == "printing":
        raise HTTPException(400, "Cannot delete item that is currently printing")

    await db.delete(item)
    await db.commit()

    logger.info("Deleted queue item %s", item_id)
    return {"message": "Queue item deleted"}


@router.post("/reorder")
async def reorder_queue(
    data: PrintQueueReorder,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.QUEUE_UPDATE_ALL),
):
    """Bulk update positions for queue items."""
    for reorder_item in data.items:
        result = await db.execute(select(PrintQueueItem).where(PrintQueueItem.id == reorder_item.id))
        item = result.scalar_one_or_none()
        if item and item.status == "pending":
            item.position = reorder_item.position

    await db.commit()
    logger.info("Reordered %s queue items", len(data.items))
    return {"message": f"Reordered {len(data.items)} items"}


@router.post("/{item_id}/cancel")
async def cancel_queue_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.QUEUE_UPDATE_ALL,
            Permission.QUEUE_UPDATE_OWN,
        )
    ),
):
    """Cancel a pending queue item."""
    user, can_modify_all = auth_result

    result = await db.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Queue item not found")

    # Ownership check
    if not can_modify_all:
        if item.created_by_id != user.id:
            raise HTTPException(403, "You can only cancel your own queue items")

    if item.status not in ("pending",):
        raise HTTPException(400, f"Cannot cancel item with status '{item.status}'")

    item.status = "cancelled"
    item.completed_at = datetime.now()
    await db.commit()

    logger.info("Cancelled queue item %s", item_id)
    return {"message": "Queue item cancelled"}


@router.post("/{item_id}/stop")
async def stop_queue_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.QUEUE_UPDATE_ALL),
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
            logger.warning("stop_print returned False for printer %s - printer may not be connected", printer_id)
    except Exception as e:
        logger.error("Error sending stop command for queue item %s: %s", item_id, e)

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

    logger.info("Stopped printing queue item %s (stop command sent: %s)", item_id, stop_sent)

    # Schedule background task for cooldown + power off
    if plug_ip:

        async def cooldown_and_poweroff():
            logger.info("Auto-off: Waiting for printer %s to cool down before power off...", printer_id)
            await printer_manager.wait_for_cooldown(printer_id, target_temp=50.0, timeout=600)
            # Re-fetch plug since we're in a new async context
            from backend.app.core.database import async_session

            async with async_session() as new_db:
                result = await new_db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
                plug = result.scalar_one_or_none()
                if plug and plug.enabled:
                    logger.info("Auto-off: Powering off printer %s", printer_id)
                    await tasmota_service.turn_off(plug)

        asyncio.create_task(cooldown_and_poweroff())

    return {"message": "Print stopped" if stop_sent else "Queue item cancelled (printer was offline)"}


@router.post("/{item_id}/start")
async def start_queue_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.QUEUE_UPDATE_OWN),
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
    await db.refresh(item, ["archive", "printer", "library_file", "created_by"])

    logger.info("Manually started queue item %s (cleared manual_start flag)", item_id)
    return _enrich_response(item)
