from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, PlainSerializer


# Custom serializer to ensure UTC datetimes have Z suffix
def serialize_utc_datetime(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    # Add Z suffix to indicate UTC
    return dt.isoformat() + "Z"


UTCDatetime = Annotated[datetime | None, PlainSerializer(serialize_utc_datetime)]


class PrintQueueItemCreate(BaseModel):
    printer_id: int | None = None  # None = unassigned, user assigns later
    target_model: str | None = None  # Target printer model (mutually exclusive with printer_id)
    target_location: str | None = None  # Target location filter (only used with target_model)
    required_filament_types: list[str] | None = None  # Required filament types for model-based assignment
    filament_overrides: list[dict] | None = None  # Filament overrides for model-based assignment
    # Either archive_id OR library_file_id must be provided
    archive_id: int | None = None
    library_file_id: int | None = None
    scheduled_time: datetime | None = None  # None = ASAP (next when idle)
    require_previous_success: bool = False
    auto_off_after: bool = False  # Power off printer after print completes
    manual_start: bool = False  # Requires manual trigger to start (staged)
    insert_at_top: bool = False  # Insert ahead of other pending items in the same queue scope
    insert_position: int | None = None  # 1-indexed insertion position for priority queueing
    # Persistent "Print Anyway" acknowledgement (#1698-followup). When set,
    # PrintModal already showed the deficit warning and the user confirmed,
    # so the scheduler does not re-flag this item on the next tick.
    skip_filament_check: bool = False
    # AMS mapping: list of global tray IDs for each filament slot
    # Format: [5, -1, 2, -1] where position = slot_id-1, value = global tray ID (-1 = unused)
    ams_mapping: list[int] | None = None
    # Plate ID for multi-plate 3MF files (1-indexed, None = auto-detect/plate 1)
    plate_id: int | None = None
    # Print options
    bed_levelling: bool = True
    flow_cali: bool = False
    vibration_cali: bool = True
    layer_inspect: bool = False
    timelapse: bool = False
    use_ams: bool = True
    # Nozzle offset calibration — dual-nozzle printers only (#1682). Default True
    # matches BambuStudio's default; the MQTT layer ignores the flag on
    # single-nozzle printers so the wire value stays "skip" there.
    nozzle_offset_cali: bool = True
    # Auto-print G-code injection
    gcode_injection: bool = False
    # Batch: create multiple copies (creates a batch if > 1)
    quantity: int = 1
    # Existing batch to add this item into. When set, the item's batch_id is
    # populated on insert so the queue UI groups it with its siblings. Used by
    # the multi-plate auto-batch flow and by the "Group as batch" action.
    batch_id: int | None = None
    # Project to associate the resulting archive with
    project_id: int | None = None
    # Direct printer-card uploads are temporary library files. The scheduler
    # deletes them after creating the durable archive copy.
    cleanup_library_after_dispatch: bool = False


class PrintQueueItemUpdate(BaseModel):
    printer_id: int | None = None
    target_model: str | None = None  # Target printer model (mutually exclusive with printer_id)
    target_location: str | None = None  # Target location filter (only used with target_model)
    filament_overrides: list[dict] | None = None  # Filament overrides for model-based assignment
    position: int | None = None
    scheduled_time: datetime | None = None
    require_previous_success: bool | None = None
    auto_off_after: bool | None = None
    manual_start: bool | None = None
    ams_mapping: list[int] | None = None
    plate_id: int | None = None
    # Print options
    bed_levelling: bool | None = None
    flow_cali: bool | None = None
    vibration_cali: bool | None = None
    layer_inspect: bool | None = None
    timelapse: bool | None = None
    use_ams: bool | None = None
    nozzle_offset_cali: bool | None = None
    # Auto-print G-code injection
    gcode_injection: bool | None = None
    # H2C dual-nozzle-rack slicer pick (#1780). list[int] per-filament
    # physical nozzle position IDs from BambuStudio's project_file MQTT
    # body; sent back to the printer verbatim on dispatch.
    nozzle_mapping: list[int] | None = None


class PrintQueueItemResponse(BaseModel):
    id: int
    printer_id: int | None  # None = unassigned
    target_model: str | None = None  # Target printer model for model-based assignment
    target_location: str | None = None  # Target location filter for model-based assignment
    required_filament_types: list[str] | None = None  # Required filament types for model-based assignment
    filament_overrides: list[dict] | None = None  # Filament overrides for model-based assignment
    waiting_reason: str | None = None  # Why a model-based job hasn't started yet
    archive_id: int | None  # None if library_file_id is set (archive created at print start)
    library_file_id: int | None  # For queue items from library files
    position: int
    scheduled_time: UTCDatetime
    require_previous_success: bool
    auto_off_after: bool
    manual_start: bool
    # True when the dispatch scheduler last evaluated this item and the
    # assigned spool could not satisfy at least one slot's required grams
    # (#1496). Display-only — the ▶ click recomputes deficit against live
    # spool state.
    filament_short: bool = False
    # User has acknowledged "Print Anyway" — scheduler skips the deficit check
    # for this item (#1698-followup).
    skip_filament_check: bool = False
    ams_mapping: list[int] | None = None
    plate_id: int | None = None  # Plate ID for multi-plate 3MF files
    # Print options
    bed_levelling: bool = True
    flow_cali: bool = False
    vibration_cali: bool = True
    layer_inspect: bool = False
    timelapse: bool = False
    use_ams: bool = True
    nozzle_offset_cali: bool = True
    status: Literal["pending", "printing", "completed", "failed", "skipped", "cancelled"]
    started_at: UTCDatetime
    completed_at: UTCDatetime
    error_message: str | None
    created_at: UTCDatetime

    # Nested info for UI (populated in route)
    archive_name: str | None = None
    archive_thumbnail: str | None = None
    # True when the linked archive has been soft-deleted (its files are gone
    # from disk). In that case the *archive_name* / *archive_thumbnail* /
    # downstream metadata fields are intentionally left None so the frontend
    # doesn't 404-storm the now-missing thumbnail / plates / plate-thumbnail
    # endpoints (#1348 follow-up). Frontends can render a "source deleted"
    # badge based on this flag.
    archive_deleted: bool = False
    library_file_name: str | None = None  # Name of library file (if library_file_id is set)
    library_file_thumbnail: str | None = None  # Thumbnail of library file
    printer_name: str | None = None
    print_time_seconds: int | None = None  # Estimated print time from archive or library file
    filament_used_grams: float | None = None  # Estimated print weight from archive or library file
    filament_type: str | None = None  # e.g. "PLA", "PETG" (from archive/library file)
    filament_color: str | None = None  # e.g. "#FFFFFF" (from archive/library file)
    layer_height: float | None = None  # e.g. 0.2 (from archive/library file)
    nozzle_diameter: float | None = None  # e.g. 0.4 (from archive/library file)
    sliced_for_model: str | None = None  # e.g. "P1S" (from archive/library file)
    # Build plate type (e.g. "Textured PEI Plate") so the user knows which
    # plate to mount on the printer (#1281). Per-plate accurate on multi-plate
    # 3MFs: when `plate_id` is set, the value is the matching plate's
    # `curr_bed_type` rather than the archive-level first-plate default.
    bed_type: str | None = None

    # User tracking (Issue #206)
    created_by_id: int | None = None
    created_by_username: str | None = None

    # Batch grouping
    batch_id: int | None = None
    batch_name: str | None = None

    # Shortest-job-first scheduling
    been_jumped: bool = False

    # Auto-print G-code injection
    gcode_injection: bool = False
    cleanup_library_after_dispatch: bool = False

    # H2C dual-nozzle-rack slicer pick (#1780). Surface for any future
    # "edit print → choose nozzle" UI; null on every model except O1C2
    # uploads from BambuStudio.
    nozzle_mapping: list[int] | None = None

    class Config:
        from_attributes = True


class PrintQueueReorderItem(BaseModel):
    id: int
    position: int


class PrintQueueReorder(BaseModel):
    items: list[PrintQueueReorderItem]


class PrintQueueBulkUpdate(BaseModel):
    """Bulk update multiple queue items with the same values."""

    item_ids: list[int]
    # Fields to update (all optional - only set fields are applied)
    printer_id: int | None = None
    scheduled_time: datetime | None = None
    require_previous_success: bool | None = None
    auto_off_after: bool | None = None
    manual_start: bool | None = None
    # Print options
    bed_levelling: bool | None = None
    flow_cali: bool | None = None
    vibration_cali: bool | None = None
    layer_inspect: bool | None = None
    timelapse: bool | None = None
    use_ams: bool | None = None
    nozzle_offset_cali: bool | None = None
    # Auto-print G-code injection
    gcode_injection: bool | None = None


class PrintQueueBulkUpdateResponse(BaseModel):
    """Response for bulk update operation."""

    updated_count: int
    skipped_count: int  # Items that were not pending
    message: str


class PrintBatchCreate(BaseModel):
    """Create a batch, either empty (multi-plate pre-batch flow) or by
    assigning existing pending queue items into it (manual "Group as batch")."""

    name: str
    archive_id: int | None = None
    library_file_id: int | None = None
    # Existing pending queue items to assign to this batch. None / empty for
    # the empty-batch flow (client passes the returned id on subsequent
    # addToQueue calls).
    item_ids: list[int] | None = None


class PrintBatchUngroupResponse(BaseModel):
    """Response after ungrouping a batch."""

    ungrouped_count: int
    message: str


class PrintBatchResponse(BaseModel):
    """Response for a print batch with progress stats."""

    id: int
    name: str
    archive_id: int | None = None
    library_file_id: int | None = None
    quantity: int
    status: str
    created_at: UTCDatetime
    created_by_id: int | None = None
    created_by_username: str | None = None
    # Derived counts
    pending_count: int = 0
    printing_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    cancelled_count: int = 0

    class Config:
        from_attributes = True
