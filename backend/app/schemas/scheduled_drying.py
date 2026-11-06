from pydantic import BaseModel, Field

from backend.app.schemas.print_queue import NaiveUTCDatetime, UTCDatetime


class ScheduledDryingCreate(BaseModel):
    printer_id: int
    ams_id: int = 0
    temp: int = Field(ge=45, le=85)
    duration_hours: int = Field(ge=1, le=24)
    # max_length matches the String(50) column: without it PostgreSQL 500s on
    # an over-long value and SQLite silently accepts it.
    filament: str = Field("", max_length=50)
    rotate_tray: bool = False
    start_after: NaiveUTCDatetime = None


class ScheduledDryingResponse(BaseModel):
    id: int
    printer_id: int
    ams_id: int
    temp: int
    duration_hours: int
    filament: str
    rotate_tray: bool
    # UTCDatetime, not a bare datetime: every queue route sends the Z suffix and
    # the frontend parses these the same way.
    start_after: UTCDatetime
    status: str
    waiting_reason: str | None
    error_message: str | None
    error_code: str | None = None
    created_at: UTCDatetime
    started_at: UTCDatetime
    completed_at: UTCDatetime

    model_config = {"from_attributes": True}
