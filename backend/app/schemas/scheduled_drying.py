from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

from backend.app.utils.local_time import to_naive_utc

# Coerces any client-sent UTC offset to the naive UTC the DB stores.
NaiveUTCDatetime = Annotated[datetime | None, AfterValidator(to_naive_utc)]


class ScheduledDryingCreate(BaseModel):
    printer_id: int
    ams_id: int = 0
    temp: int = Field(ge=45, le=85)
    duration_hours: int = Field(ge=1, le=24)
    filament: str = ""
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
    start_after: datetime | None
    wait_for_off_peak: bool
    status: str
    waiting_reason: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}
