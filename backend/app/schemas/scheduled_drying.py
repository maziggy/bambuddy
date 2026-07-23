from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


class ScheduledDryingCreate(BaseModel):
    printer_id: int
    ams_id: int = 0
    temp: int = Field(ge=45, le=85)
    duration_hours: int = Field(ge=1, le=24)
    filament: str = ""
    rotate_tray: bool = False
    start_after: datetime | None = None

    @field_validator("start_after")
    @classmethod
    def _normalize_to_naive_utc(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is not None:
            v = v.astimezone(timezone.utc).replace(tzinfo=None)
        return v


class ScheduledDryingResponse(BaseModel):
    id: int
    printer_id: int
    ams_id: int
    temp: int
    duration_hours: int
    filament: str
    rotate_tray: bool
    start_after: datetime | None
    wait_for_off_peak: bool
    status: str
    waiting_reason: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}
