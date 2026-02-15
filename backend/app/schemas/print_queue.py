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
    # Either archive_id OR library_file_id must be provided
    archive_id: int | None = None
    library_file_id: int | None = None
    scheduled_time: datetime | None = None  # None = ASAP (next when idle)
    require_previous_success: bool = False
    auto_off_after: bool = False  # Power off printer after print completes
    manual_start: bool = False  # Requires manual trigger to start (staged)
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


class PrintQueueItemUpdate(BaseModel):
    printer_id: int | None = None
    target_model: str | None = None  # Target printer model (mutually exclusive with printer_id)
    target_location: str | None = None  # Target location filter (only used with target_model)
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


class PrintQueueItemResponse(BaseModel):
    id: int
    printer_id: int | None  # None = unassigned
    target_model: str | None = None  # Target printer model for model-based assignment
    target_location: str | None = None  # Target location filter for model-based assignment
    required_filament_types: list[str] | None = None  # Required filament types for model-based assignment
    waiting_reason: str | None = None  # Why a model-based job hasn't started yet
    archive_id: int | None  # None if library_file_id is set (archive created at print start)
    library_file_id: int | None  # For queue items from library files
    position: int
    scheduled_time: UTCDatetime
    require_previous_success: bool
    auto_off_after: bool
    manual_start: bool
    ams_mapping: list[int] | None = None
    plate_id: int | None = None  # Plate ID for multi-plate 3MF files
    # Print options
    bed_levelling: bool = True
    flow_cali: bool = False
    vibration_cali: bool = True
    layer_inspect: bool = False
    timelapse: bool = False
    use_ams: bool = True
    status: Literal["pending", "printing", "completed", "failed", "skipped", "cancelled"]
    started_at: UTCDatetime
    completed_at: UTCDatetime
    error_message: str | None
    created_at: UTCDatetime

    # Nested info for UI (populated in route)
    archive_name: str | None = None
    archive_thumbnail: str | None = None
    library_file_name: str | None = None  # Name of library file (if library_file_id is set)
    library_file_thumbnail: str | None = None  # Thumbnail of library file
    printer_name: str | None = None
    print_time_seconds: int | None = None  # Estimated print time from archive or library file
    filament_used_grams: float | None = None  # Estimated print weight from archive or library file

    # User tracking (Issue #206)
    created_by_id: int | None = None
    created_by_username: str | None = None

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


class PrintQueueBulkUpdateResponse(BaseModel):
    """Response for bulk update operation."""

    updated_count: int
    skipped_count: int  # Items that were not pending
    message: str
