from datetime import date

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.app.models.printer_reservation import ALL_DAYS_MASK
from backend.app.schemas.print_queue import UTCDatetime


class PrinterReservationBase(BaseModel):
    label: str = Field("", max_length=100)
    printer_ids: list[int] = Field(min_length=1)
    # Bit 0 = Monday … bit 6 = Sunday.
    days_mask: int = Field(ALL_DAYS_MASK, ge=1, le=ALL_DAYS_MASK)
    all_day: bool = False
    start_minute: int = Field(0, ge=0, lt=24 * 60)
    end_minute: int = Field(0, ge=0, lt=24 * 60)
    valid_from: date | None = None
    valid_until: date | None = None

    @field_validator("printer_ids")
    @classmethod
    def _dedupe(cls, v: list[int]) -> list[int]:
        return sorted(set(v))

    @model_validator(mode="after")
    def _check_range(self):
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("valid_until must not be before valid_from")
        return self


class PrinterReservationCreate(PrinterReservationBase):
    pass


class PrinterReservationUpdate(PrinterReservationBase):
    pass


class PrinterReservationResponse(PrinterReservationBase):
    id: int
    # Lenient on the way out: a deleted printer leaves the list empty.
    printer_ids: list[int]
    created_by_id: int | None
    created_by_username: str | None = None
    created_at: UTCDatetime
    updated_at: UTCDatetime

    model_config = {"from_attributes": True}


class ReservationOccurrenceResponse(BaseModel):
    reservation_id: int
    label: str
    printer_ids: list[int]
    created_by_id: int | None
    created_by_username: str | None = None
    start: UTCDatetime
    end: UTCDatetime
