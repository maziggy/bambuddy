from datetime import datetime

from pydantic import BaseModel


class PrintLogEntrySchema(BaseModel):
    id: int
    print_name: str | None = None
    printer_name: str | None = None
    printer_id: int | None = None
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: int | None = None
    filament_type: str | None = None
    filament_color: str | None = None
    filament_used_grams: float | None = None
    thumbnail_path: str | None = None
    created_by_username: str | None = None
    created_at: datetime


class PrintLogResponse(BaseModel):
    items: list[PrintLogEntrySchema]
    total: int
