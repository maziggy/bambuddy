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
    printer_id: int
    archive_id: int
    scheduled_time: datetime | None = None  # None = ASAP (next when idle)
    require_previous_success: bool = False
    auto_off_after: bool = False  # Power off printer after print completes
    manual_start: bool = False  # Requires manual trigger to start (staged)


class PrintQueueItemUpdate(BaseModel):
    printer_id: int | None = None
    position: int | None = None
    scheduled_time: datetime | None = None
    require_previous_success: bool | None = None
    auto_off_after: bool | None = None
    manual_start: bool | None = None


class PrintQueueItemResponse(BaseModel):
    id: int
    printer_id: int
    archive_id: int
    position: int
    scheduled_time: UTCDatetime
    require_previous_success: bool
    auto_off_after: bool
    manual_start: bool
    status: Literal["pending", "printing", "completed", "failed", "skipped", "cancelled"]
    started_at: UTCDatetime
    completed_at: UTCDatetime
    error_message: str | None
    created_at: UTCDatetime

    # Nested info for UI (populated in route)
    archive_name: str | None = None
    archive_thumbnail: str | None = None
    printer_name: str | None = None
    print_time_seconds: int | None = None  # Estimated print time from archive

    class Config:
        from_attributes = True


class PrintQueueReorderItem(BaseModel):
    id: int
    position: int


class PrintQueueReorder(BaseModel):
    items: list[PrintQueueReorderItem]
