from datetime import datetime

from pydantic import BaseModel


class PrintLogEntrySchema(BaseModel):
    id: int
    archive_id: int | None = None
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
    cost: float | None = None
    energy_kwh: float | None = None
    energy_cost: float | None = None
    failure_reason: str | None = None
    thumbnail_path: str | None = None
    created_by_id: int | None = None
    created_by_username: str | None = None
    created_at: datetime


class PrintLogResponse(BaseModel):
    items: list[PrintLogEntrySchema]
    total: int


class PrintLogEntryUpdate(BaseModel):
    """Per-row classification edits (#1687 part 4 — IndividualGhost1905).

    Lets the user set ``failure_reason`` (and re-classify ``status``) directly
    on a Print Log row, including on orphan entries that have no archive to
    edit through. The Failure Analysis widget already groups by
    ``PrintLogEntry.failure_reason``, so this just plugs the editor gap.
    """

    failure_reason: str | None = None
    status: str | None = None
