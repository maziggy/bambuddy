"""API routes for print queue management."""

import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import defusedxml.ElementTree as ET
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.auth import RequirePermissionIfAuthEnabled, require_ownership_permission
from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.core.tasks import spawn_background_task
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.print_batch import PrintBatch
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.project import Project
from backend.app.models.user import User
from backend.app.schemas.print_queue import (
    PrintBatchCreate,
    PrintBatchResponse,
    PrintBatchUngroupResponse,
    PrintQueueBulkUpdate,
    PrintQueueBulkUpdateResponse,
    PrintQueueItemCreate,
    PrintQueueItemResponse,
    PrintQueueItemUpdate,
    PrintQueueReorder,
)
from backend.app.services.filament_deficit import compute_deficit_for_queue_item
from backend.app.services.notification_service import notification_service
from backend.app.utils.printer_models import normalize_printer_model, normalize_printer_model_id
from backend.app.utils.threemf_tools import (
    extract_bed_type_from_3mf,
    extract_filament_usage_from_3mf,
    extract_print_time_from_3mf,
)

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


# Local alias kept so existing call sites stay compact; the implementation lives
# in utils/threemf_tools.py so the notification path (main.py) can reuse it
# without importing from a routes module (#1785).
_extract_print_time_from_3mf = extract_print_time_from_3mf


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

    # Parse filament_overrides from JSON string
    filament_overrides_parsed = None
    if item.filament_overrides:
        try:
            filament_overrides_parsed = json.loads(item.filament_overrides)
        except json.JSONDecodeError:
            filament_overrides_parsed = None

    # Parse nozzle_mapping from JSON string (#1780 — H2C rack slicer-pick
    # preservation). Nullable opaque JSON blob stored verbatim from
    # BambuStudio's project_file; surface it parsed for the response model
    # and any future "edit print → nozzle" UI.
    nozzle_mapping_parsed = None
    if item.nozzle_mapping:
        try:
            nozzle_mapping_parsed = json.loads(item.nozzle_mapping)
        except json.JSONDecodeError:
            nozzle_mapping_parsed = None

    # Create response with parsed ams_mapping
    item_dict = {
        "id": item.id,
        "printer_id": item.printer_id,
        "target_model": item.target_model,
        "target_location": item.target_location,
        "required_filament_types": required_filament_types_parsed,
        "filament_overrides": filament_overrides_parsed,
        "waiting_reason": item.waiting_reason,
        "archive_id": item.archive_id,
        "library_file_id": item.library_file_id,
        "position": item.position,
        "scheduled_time": item.scheduled_time,
        "require_previous_success": item.require_previous_success,
        "auto_off_after": item.auto_off_after,
        "manual_start": item.manual_start,
        "filament_short": bool(item.filament_short),
        "skip_filament_check": bool(item.skip_filament_check),
        "ams_mapping": ams_mapping_parsed,
        "plate_id": item.plate_id,
        "bed_levelling": item.bed_levelling,
        "flow_cali": item.flow_cali,
        "vibration_cali": item.vibration_cali,
        "layer_inspect": item.layer_inspect,
        "timelapse": item.timelapse,
        "use_ams": item.use_ams,
        "nozzle_offset_cali": item.nozzle_offset_cali,
        "status": item.status,
        "started_at": item.started_at,
        "completed_at": item.completed_at,
        "error_message": item.error_message,
        "created_at": item.created_at,
        # User tracking (Issue #206)
        "created_by_id": item.created_by_id,
        "created_by_username": item.created_by.username if item.created_by else None,
        # Batch grouping
        "batch_id": item.batch_id,
        "batch_name": item.batch.name if item.batch else None,
        # SJF scheduling
        "been_jumped": item.been_jumped,
        # Auto-print G-code injection
        "gcode_injection": item.gcode_injection,
        # H2C rack-swap nozzle pick (#1780)
        "nozzle_mapping": nozzle_mapping_parsed,
        "nozzles_info": nozzles_info_parsed,
        "cleanup_library_after_dispatch": item.cleanup_library_after_dispatch,
    }
    response = PrintQueueItemResponse(**item_dict)
    if item.archive:
        # Soft-deleted archive: files are gone from disk but the row stays
        # (its filament/cost contribution still flows into stats per #1343).
        # Suppress the archive-derived UI surface so the queue page doesn't
        # 404-storm the thumbnail / plates / plate-thumbnail endpoints — the
        # frontend's existing truthy gate on archive_thumbnail covers it
        # (#1348 follow-up). The archive_deleted flag lets the UI render a
        # "source deleted" badge on these rows.
        if item.archive.deleted_at is not None:
            response.archive_deleted = True
        else:
            response.archive_name = item.archive.print_name or item.archive.filename
            response.archive_thumbnail = item.archive.thumbnail_path
            response.print_time_seconds = item.archive.print_time_seconds
            response.filament_used_grams = item.archive.filament_used_grams
            response.filament_type = item.archive.filament_type
            response.filament_color = item.archive.filament_color
            response.layer_height = item.archive.layer_height
            response.nozzle_diameter = item.archive.nozzle_diameter
            response.sliced_for_model = item.archive.sliced_for_model
            response.bed_type = item.archive.bed_type
            if item.plate_id:
                archive_path = settings.base_dir / item.archive.file_path
                if archive_path.exists():
                    plate_time = _extract_print_time_from_3mf(archive_path, item.plate_id)
                    plate_weight = sum(
                        f["used_g"] for f in extract_filament_usage_from_3mf(archive_path, item.plate_id)
                    )
                    plate_bed = extract_bed_type_from_3mf(archive_path, item.plate_id)
                    if plate_time is not None:
                        response.print_time_seconds = plate_time
                    if plate_weight > 0:
                        response.filament_used_grams = plate_weight
                    if plate_bed:
                        response.bed_type = plate_bed
    if item.library_file:
        response.library_file_name = (
            item.library_file.file_metadata.get("print_name") if item.library_file.file_metadata else None
        )
        if not response.library_file_name:
            response.library_file_name = item.library_file.filename
        response.library_file_thumbnail = item.library_file.thumbnail_path
        # Get metadata from library file if no archive
        if not item.archive and item.library_file.file_metadata:
            response.print_time_seconds = item.library_file.file_metadata.get("print_time_seconds")
            response.filament_used_grams = item.library_file.file_metadata.get("filament_used_grams")
            response.filament_type = item.library_file.file_metadata.get("filament_type")
            response.filament_color = item.library_file.file_metadata.get("filament_color")
            response.layer_height = item.library_file.file_metadata.get("layer_height")
            response.nozzle_diameter = item.library_file.file_metadata.get("nozzle_diameter")
            response.sliced_for_model = item.library_file.file_metadata.get("sliced_for_model")
            response.bed_type = item.library_file.file_metadata.get("bed_type")
        if item.plate_id:
            lib_path = Path(item.library_file.file_path)
            library_file_path = lib_path if lib_path.is_absolute() else settings.base_dir / item.library_file.file_path
            if library_file_path.exists():
                plate_time = _extract_print_time_from_3mf(library_file_path, item.plate_id)
                plate_weight = sum(
                    f["used_g"] for f in extract_filament_usage_from_3mf(library_file_path, item.plate_id)
                )
                plate_bed = extract_bed_type_from_3mf(library_file_path, item.plate_id)
                if plate_time is not None:
                    response.print_time_seconds = plate_time
                if plate_weight > 0:
                    response.filament_used_grams = plate_weight
                if plate_bed:
                    response.bed_type = plate_bed
    if item.printer:
        response.printer_name = item.printer.name
    return response


@router.get("/", response_model=list[PrintQueueItemResponse])
async def list_queue(
    printer_id: int | None = Query(None, description="Filter by printer (-1 for unassigned)"),
    status: str | None = Query(None, description="Filter by status"),
    target_model: str | None = Query(
        None, description="Filter by target model (also includes model-based items when combined with printer_id)"
    ),
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.QUEUE_READ_ALL,
            Permission.QUEUE_READ_OWN,
        )
    ),
):
    """List all queue items, optionally filtered by printer or status."""
    user, can_read_all = auth_result
    query = (
        select(PrintQueueItem)
        .options(
            selectinload(PrintQueueItem.archive),
            selectinload(PrintQueueItem.printer),
            selectinload(PrintQueueItem.library_file),
            selectinload(PrintQueueItem.created_by),
            selectinload(PrintQueueItem.batch),
        )
        .order_by(PrintQueueItem.printer_id.nulls_first(), PrintQueueItem.position)
    )
    if user is not None and not can_read_all:
        query = query.where(PrintQueueItem.created_by_id == user.id)

    if printer_id is not None:
        if printer_id == -1:
            # Special value: filter for unassigned items
            query = query.where(PrintQueueItem.printer_id.is_(None))
        else:
            # Resolve effective model: prefer explicit param, fall back to printer's DB model.
            # This ensures model-based "Any X" items are returned even when the frontend
            # doesn't send target_model (e.g. printer.model is NULL on the client side).
            effective_model = target_model
            if not effective_model:
                printer_row = (
                    await db.execute(select(Printer.model).where(Printer.id == printer_id))
                ).scalar_one_or_none()
                effective_model = printer_row

            if effective_model:
                # Include both printer-specific items AND model-based (unassigned) items
                query = query.where(
                    or_(
                        PrintQueueItem.printer_id == printer_id,
                        and_(
                            PrintQueueItem.printer_id.is_(None),
                            func.lower(PrintQueueItem.target_model) == effective_model.lower(),
                        ),
                    )
                )
            else:
                query = query.where(PrintQueueItem.printer_id == printer_id)
    elif target_model:
        query = query.where(func.lower(PrintQueueItem.target_model) == target_model.lower())
    if status:
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
        # IDOR fix (maziggy/bambuddy-security #2): without this check, a
        # caller with QUEUE_CREATE could queue any user's archive even
        # without ARCHIVES_READ on it — Landon's PoC enumerated this on
        # admin's archives as operator1. Gate on ARCHIVES_READ_ALL OR
        # ownership of the archive. 404 (not 403) so we don't leak
        # "this id exists but you can't queue it" for enumeration.
        if (
            current_user is not None
            and not current_user.has_permission(Permission.ARCHIVES_READ_ALL.value)
            and archive.created_by_id != current_user.id
        ):
            raise HTTPException(404, "Archive not found")

    # Validate library file exists (if provided) and get it for filament extraction
    library_file = None
    if data.library_file_id:
        result = await db.execute(LibraryFile.active().where(LibraryFile.id == data.library_file_id))
        library_file = result.scalar_one_or_none()
        if not library_file:
            raise HTTPException(400, "Library file not found")
        # Same shape: gate cross-user library-file queueing on LIBRARY_READ_ALL.
        if (
            current_user is not None
            and not current_user.has_permission(Permission.LIBRARY_READ_ALL.value)
            and library_file.created_by_id != current_user.id
        ):
            raise HTTPException(404, "Library file not found")
        # Bambu SD card is FAT32/exFAT — illegal filename chars would 553 at
        # FTP upload time (#1540). Reject at queue time so the user gets the
        # actionable error before waiting in queue.
        from backend.app.utils.filename import InvalidFilenameError, validate_print_filename

        try:
            validate_print_filename(library_file.filename)
        except InvalidFilenameError as e:
            raise HTTPException(400, str(e)) from e

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

    # If filament overrides are provided, update required_filament_types to match override types
    filament_overrides_json = None
    if data.filament_overrides and target_model_norm:
        filament_overrides_json = json.dumps(data.filament_overrides)
        # Update required_filament_types from overrides so scheduler validates against overridden types
        override_types = sorted({o["type"] for o in data.filament_overrides if "type" in o})
        if override_types:
            # Merge with existing types (overrides may only cover some slots)
            existing_types = set(json.loads(required_filament_types)) if required_filament_types else set()
            # Replace types for overridden slots, keep others
            all_types = existing_types | set(override_types)
            required_filament_types = json.dumps(sorted(all_types))

    # Validate quantity
    quantity = max(1, data.quantity)

    # Validate batch_id if provided. Client passes batch_id when adding items
    # into a pre-created batch (multi-plate auto-batch or "Group as batch" flow).
    # 404 keeps the existing-id leak surface low.
    batch = None
    batch_id = None
    if data.batch_id is not None:
        result = await db.execute(select(PrintBatch).where(PrintBatch.id == data.batch_id))
        existing_batch = result.scalar_one_or_none()
        if not existing_batch:
            raise HTTPException(404, "Batch not found")
        if existing_batch.status != "active":
            raise HTTPException(400, "Cannot add items to a non-active batch")
        if (
            current_user is not None
            and existing_batch.created_by_id is not None
            and existing_batch.created_by_id != current_user.id
            and not current_user.has_permission(Permission.QUEUE_UPDATE_ALL.value)
        ):
            raise HTTPException(404, "Batch not found")
        batch = existing_batch
        batch_id = existing_batch.id

    # Create batch if quantity > 1 and no batch_id provided
    if batch_id is None and quantity > 1:
        # Derive batch name from source file
        batch_name_base = "Batch"
        if archive:
            batch_name_base = archive.print_name or archive.filename or "Batch"
        elif library_file:
            if library_file.file_metadata:
                batch_name_base = library_file.file_metadata.get("print_name") or library_file.filename
            else:
                batch_name_base = library_file.filename
        batch_name_base = batch_name_base.replace(".gcode.3mf", "").replace(".3mf", "")

        batch = PrintBatch(
            name=f"{batch_name_base} ×{quantity}",
            archive_id=data.archive_id,
            library_file_id=data.library_file_id,
            quantity=quantity,
            status="active",
            created_by_id=current_user.id if current_user else None,
        )
        db.add(batch)
        await db.flush()  # Get batch.id before creating items
        batch_id = batch.id

    # Get queue scope for this printer (or for unassigned/model-based items).
    if data.printer_id is not None:
        queue_scope = (
            PrintQueueItem.printer_id == data.printer_id,
            PrintQueueItem.status == "pending",
        )
    else:
        # For unassigned/model-based items, scope across all unassigned.
        queue_scope = (
            PrintQueueItem.printer_id.is_(None),
            PrintQueueItem.status == "pending",
        )

    insert_position = max(1, data.insert_position or 1)
    if data.insert_at_top or data.insert_position is not None:
        await db.execute(
            update(PrintQueueItem)
            .where(*queue_scope)
            .where(PrintQueueItem.position >= insert_position)
            .values(position=PrintQueueItem.position + quantity)
        )
        start_position = insert_position
    else:
        result = await db.execute(select(func.max(PrintQueueItem.position)).where(*queue_scope))
        max_pos = result.scalar() or 0
        start_position = max_pos + 1

    # Resolve print_time_seconds for SJF scheduling (cache on item at creation)
    cached_print_time = None
    if archive:
        cached_print_time = archive.print_time_seconds
        if data.plate_id:
            archive_path = settings.base_dir / archive.file_path
            if archive_path.exists():
                plate_time = _extract_print_time_from_3mf(archive_path, data.plate_id)
                if plate_time is not None:
                    cached_print_time = plate_time
    elif library_file:
        if library_file.file_metadata:
            cached_print_time = library_file.file_metadata.get("print_time_seconds")
        if data.plate_id:
            lib_path = Path(library_file.file_path)
            library_file_path = lib_path if lib_path.is_absolute() else settings.base_dir / library_file.file_path
            if library_file_path.exists():
                plate_time = _extract_print_time_from_3mf(library_file_path, data.plate_id)
                if plate_time is not None:
                    cached_print_time = plate_time

    # Validate project exists before insert so a bogus ID yields 404, not an FK-constraint 500
    if data.project_id is not None:
        project_result = await db.execute(select(Project).where(Project.id == data.project_id))
        if not project_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Project not found")

    ams_mapping_json = json.dumps(data.ams_mapping) if data.ams_mapping else None
    items = []
    for i in range(quantity):
        item = PrintQueueItem(
            printer_id=data.printer_id,
            target_model=target_model_norm,
            target_location=data.target_location,
            required_filament_types=required_filament_types,
            filament_overrides=filament_overrides_json,
            archive_id=data.archive_id,
            library_file_id=data.library_file_id,
            scheduled_time=data.scheduled_time,
            require_previous_success=data.require_previous_success,
            auto_off_after=data.auto_off_after,
            manual_start=data.manual_start,
            skip_filament_check=data.skip_filament_check,
            ams_mapping=ams_mapping_json,
            plate_id=data.plate_id,
            bed_levelling=data.bed_levelling,
            flow_cali=data.flow_cali,
            vibration_cali=data.vibration_cali,
            layer_inspect=data.layer_inspect,
            timelapse=data.timelapse,
            use_ams=data.use_ams,
            nozzle_offset_cali=data.nozzle_offset_cali,
            gcode_injection=data.gcode_injection,
            cleanup_library_after_dispatch=data.cleanup_library_after_dispatch,
            project_id=data.project_id,
            position=start_position + i,
            status="pending",
            created_by_id=current_user.id if current_user else None,
            batch_id=batch_id,
            print_time_seconds=cached_print_time,
        )
        db.add(item)
        items.append(item)

    await db.commit()

    # Refresh the first item for the response
    item = items[0]
    await db.refresh(item)
    await db.refresh(item, ["archive", "printer", "library_file", "created_by", "batch"])

    source_name = f"archive {data.archive_id}" if data.archive_id else f"library file {data.library_file_id}"
    target_desc = data.printer_id or (f"model {target_model_norm}" if target_model_norm else "unassigned")
    qty_desc = f" (×{quantity})" if quantity > 1 else ""
    logger.info("Added %s to queue for %s%s", source_name, target_desc, qty_desc)

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
        if quantity > 1:
            job_name = f"{job_name} ×{quantity}"
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


# --- Batch endpoints ---


@router.post("/batches", response_model=PrintBatchResponse)
async def create_batch(
    data: PrintBatchCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.QUEUE_CREATE),
):
    """Create a batch.

    Two modes:
    * ``item_ids`` provided: assign the listed pending queue items to a new
      batch ("Group as batch" UI action).
    * ``item_ids`` omitted/empty: create an empty batch so the client can
      pass the returned ``id`` on subsequent ``POST /queue/`` calls. Used by
      the multi-plate auto-batch flow in PrintModal.
    """
    if not data.name or not data.name.strip():
        raise HTTPException(400, "Batch name is required")

    batch = PrintBatch(
        name=data.name.strip()[:255],
        archive_id=data.archive_id,
        library_file_id=data.library_file_id,
        quantity=len(data.item_ids) if data.item_ids else 1,
        status="active",
        created_by_id=current_user.id if current_user else None,
    )
    db.add(batch)
    await db.flush()  # Need batch.id before assigning to items

    assigned = 0
    if data.item_ids:
        result = await db.execute(select(PrintQueueItem).where(PrintQueueItem.id.in_(data.item_ids)))
        items = result.scalars().all()
        for item in items:
            if item.status != "pending":
                continue
            if item.batch_id is not None:
                continue
            if (
                current_user is not None
                and item.created_by_id != current_user.id
                and not current_user.has_permission(Permission.QUEUE_UPDATE_ALL.value)
            ):
                continue
            item.batch_id = batch.id
            assigned += 1
        batch.quantity = max(assigned, 1)

    await db.commit()
    await db.refresh(batch)

    logger.info("Created batch %s '%s' with %s assigned items", batch.id, batch.name, assigned)
    return await _build_batch_response(db, batch)


@router.post("/batches/{batch_id}/ungroup", response_model=PrintBatchUngroupResponse)
async def ungroup_batch(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.QUEUE_UPDATE_OWN),
):
    """Disband a batch: clear batch_id from all members and delete the batch row.

    Items stay in the queue. Only ungroups items the caller owns (unless they
    hold QUEUE_UPDATE_ALL). A batch with all members ungrouped is deleted.
    """
    result = await db.execute(select(PrintBatch).where(PrintBatch.id == batch_id))
    batch = result.scalar_one_or_none()
    if not batch:
        raise HTTPException(404, "Batch not found")

    can_modify_all = current_user is None or current_user.has_permission(Permission.QUEUE_UPDATE_ALL.value)
    if not can_modify_all and batch.created_by_id != (current_user.id if current_user else None):
        raise HTTPException(404, "Batch not found")

    result = await db.execute(select(PrintQueueItem).where(PrintQueueItem.batch_id == batch_id))
    items = result.scalars().all()
    ungrouped = 0
    remaining = 0
    for item in items:
        if not can_modify_all and item.created_by_id != (current_user.id if current_user else None):
            remaining += 1
            continue
        item.batch_id = None
        ungrouped += 1

    # Delete the batch row only when all members were ungrouped — otherwise it
    # still owns the items the caller couldn't touch.
    if remaining == 0:
        await db.delete(batch)

    await db.commit()

    logger.info("Ungrouped batch %s (%s items)", batch_id, ungrouped)
    return PrintBatchUngroupResponse(
        ungrouped_count=ungrouped,
        message=f"Ungrouped {ungrouped} item(s)",
    )


@router.get("/batches", response_model=list[PrintBatchResponse])
async def list_batches(
    status: str | None = Query(None, description="Filter by status (active, completed, cancelled)"),
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.QUEUE_READ_ALL,
            Permission.QUEUE_READ_OWN,
        )
    ),
):
    """List all print batches with progress stats."""
    current_user, can_read_all = auth_result
    query = select(PrintBatch).order_by(PrintBatch.created_at.desc())
    if status:
        query = query.where(PrintBatch.status == status)
    if current_user is not None and not can_read_all:
        query = query.where(PrintBatch.created_by_id == current_user.id)
    result = await db.execute(query)
    batches = result.scalars().all()

    responses = []
    for batch in batches:
        responses.append(await _build_batch_response(db, batch))
    return responses


@router.get("/batches/{batch_id}", response_model=PrintBatchResponse)
async def get_batch(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.QUEUE_READ_ALL,
            Permission.QUEUE_READ_OWN,
        )
    ),
):
    """Get a print batch with progress stats."""
    current_user, can_read_all = auth_result
    result = await db.execute(select(PrintBatch).where(PrintBatch.id == batch_id))
    batch = result.scalar_one_or_none()
    if not batch:
        raise HTTPException(404, "Batch not found")
    if (
        current_user is not None
        and not can_read_all
        and (batch.created_by_id is None or batch.created_by_id != current_user.id)
    ):
        raise HTTPException(404, "Batch not found")
    return await _build_batch_response(db, batch)


@router.delete("/batches/{batch_id}")
async def cancel_batch(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.QUEUE_DELETE_ALL),
):
    """Cancel all pending items in a batch and mark batch as cancelled."""
    result = await db.execute(select(PrintBatch).where(PrintBatch.id == batch_id))
    batch = result.scalar_one_or_none()
    if not batch:
        raise HTTPException(404, "Batch not found")

    # Cancel all pending queue items in this batch
    result = await db.execute(
        select(PrintQueueItem).where(and_(PrintQueueItem.batch_id == batch_id, PrintQueueItem.status == "pending"))
    )
    pending_items = result.scalars().all()
    cancelled_count = 0
    for item in pending_items:
        item.status = "cancelled"
        cancelled_count += 1

    batch.status = "cancelled"
    await db.commit()

    return {"message": f"Batch cancelled, {cancelled_count} pending items cancelled"}


async def _build_batch_response(db: AsyncSession, batch: PrintBatch) -> PrintBatchResponse:
    """Build a batch response with derived counts from queue items."""
    # Count queue items by status
    result = await db.execute(
        select(PrintQueueItem.status, func.count(PrintQueueItem.id))
        .where(PrintQueueItem.batch_id == batch.id)
        .group_by(PrintQueueItem.status)
    )
    status_counts = {row[0]: row[1] for row in result.fetchall()}

    # Load created_by for username
    created_by_username = None
    if batch.created_by_id:
        result = await db.execute(select(User).where(User.id == batch.created_by_id))
        user = result.scalar_one_or_none()
        if user:
            created_by_username = user.username

    return PrintBatchResponse(
        id=batch.id,
        name=batch.name,
        archive_id=batch.archive_id,
        library_file_id=batch.library_file_id,
        quantity=batch.quantity,
        status=batch.status,
        created_at=batch.created_at,
        created_by_id=batch.created_by_id,
        created_by_username=created_by_username,
        pending_count=status_counts.get("pending", 0),
        printing_count=status_counts.get("printing", 0),
        completed_count=status_counts.get("completed", 0),
        failed_count=status_counts.get("failed", 0),
        cancelled_count=status_counts.get("cancelled", 0),
    )


@router.get("/{item_id}", response_model=PrintQueueItemResponse)
async def get_queue_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.QUEUE_READ_ALL,
            Permission.QUEUE_READ_OWN,
        )
    ),
):
    """Get a specific queue item."""
    current_user, can_read_all = auth_result
    result = await db.execute(
        select(PrintQueueItem)
        .options(
            selectinload(PrintQueueItem.archive),
            selectinload(PrintQueueItem.printer),
            selectinload(PrintQueueItem.library_file),
            selectinload(PrintQueueItem.created_by),
            selectinload(PrintQueueItem.batch),
        )
        .where(PrintQueueItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Queue item not found")
    if (
        current_user is not None
        and not can_read_all
        and (item.created_by_id is None or item.created_by_id != current_user.id)
    ):
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

    # Serialize filament_overrides to JSON for TEXT column storage
    if "filament_overrides" in update_data:
        update_data["filament_overrides"] = (
            json.dumps(update_data["filament_overrides"]) if update_data["filament_overrides"] else None
        )

    # Serialize H2C rack-swap nozzle pick (#1780) to JSON for TEXT column
    # storage; same Text-as-opaque-blob convention as ams_mapping above.
    if "nozzle_mapping" in update_data:
        update_data["nozzle_mapping"] = (
            json.dumps(update_data["nozzle_mapping"]) if update_data["nozzle_mapping"] else None
        )

    for field, value in update_data.items():
        setattr(item, field, value)

    await db.commit()
    await db.refresh(item, ["archive", "printer", "library_file", "created_by", "batch"])

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
    item.completed_at = datetime.now(timezone.utc)
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

    # Mark this printer as user-stopped BEFORE the first await so that if the
    # MQTT on_print_complete callback fires during the db.commit() yield the flag
    # is already set and the "failed" status will be correctly overridden to
    # "cancelled" (preventing a spurious "print failed" notification).
    try:
        from backend.app.main import mark_printer_stopped_by_user

        mark_printer_stopped_by_user(printer_id)
    except Exception as _mark_err:
        logger.warning("Failed to mark printer %s as user-stopped: %s", printer_id, _mark_err)

    # Update queue item status regardless - if printer is off, print is already stopped
    item.status = "cancelled"
    item.completed_at = datetime.now(timezone.utc)
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

        spawn_background_task(cooldown_and_poweroff(), name=f"queue-cooldown-poweroff-{printer_id}")

    return {"message": "Print stopped" if stop_sent else "Queue item cancelled (printer was offline)"}


@router.post("/{item_id}/start")
async def start_queue_item(
    item_id: int,
    skip_filament_check: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: User | None = RequirePermissionIfAuthEnabled(Permission.QUEUE_UPDATE_OWN),
):
    """Manually start a staged (manual_start) queue item.

    Clears the manual_start flag so the scheduler picks it up. When
    ``skip_filament_check`` is false (the default) the live filament
    deficit (#1496) is checked first — if the assigned spool can't satisfy
    a slot's required grams, the route returns ``409`` with the deficit
    payload so the caller can show a confirm dialog and retry with
    ``skip_filament_check=true``.
    """
    result = await db.execute(
        select(PrintQueueItem)
        .options(
            selectinload(PrintQueueItem.archive),
            selectinload(PrintQueueItem.printer),
            selectinload(PrintQueueItem.library_file),
            selectinload(PrintQueueItem.batch),
        )
        .where(PrintQueueItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Queue item not found")

    if item.status != "pending":
        raise HTTPException(400, f"Can only start pending items, current status: '{item.status}'")

    # Live deficit check — re-evaluated against current spool state, so a
    # spool swap between scheduler flagging and the user clicking ▶ clears
    # the block automatically.
    if not skip_filament_check:
        deficit = await compute_deficit_for_queue_item(db, item)
        if deficit:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "insufficient_filament",
                    "deficit": [d.to_dict() for d in deficit],
                },
            )

    # Print Anyway / no deficit: clear the flags and let the scheduler dispatch.
    item.manual_start = False
    item.filament_short = False
    # Persist the user's "Print Anyway" decision so the scheduler does not
    # immediately re-flag this item on the next tick (#1698-followup). The
    # pre-fix behaviour bounced between "user said anyway" and
    # "scheduler re-blocked on same deficit" forever.
    if skip_filament_check:
        item.skip_filament_check = True
    # Credit the clicker as the item's owner when no prior owner is set —
    # VP-uploaded queue items arrive over FTP unattributed, so without this
    # the print log's User column stays blank even when auth is on
    # (#1670). An item that already has a creator (UI-added queue items)
    # keeps that attribution; the dispatcher is not promoted over the
    # original uploader.
    if user is not None and item.created_by_id is None:
        item.created_by_id = user.id
    await db.commit()
    await db.refresh(item, ["archive", "printer", "library_file", "created_by", "batch"])

    logger.info(
        "Manually started queue item %s (cleared manual_start; skip_filament_check=%s)",
        item_id,
        skip_filament_check,
    )
    return _enrich_response(item)
