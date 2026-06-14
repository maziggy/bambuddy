"""Pydantic schemas for the pending slot assignment API."""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class PendingSlotAssignmentCreate(BaseModel):
    """Request body for POST /api/v1/inventory/spools/assign-on-next-slot."""

    tray_uuid: str | None = Field(
        default=None,
        max_length=64,
        description="The spool's tray_uuid (Bambu Lab spool UUID, 32 hex chars). "
        "Read from the NFC tag NDEF payload or decoded from a QR code.",
    )
    tag_uid: str | None = Field(
        default=None,
        max_length=32,
        description="The NFC tag hardware UID (up to 32 hex chars). "
        "Used as fallback when tray_uuid is not available (generic/3rd-party tags).",
    )
    printer_id: int | None = Field(
        default=None,
        description="Target printer ID. If omitted, listens on all printers.",
    )
    source: str = Field(
        ...,
        pattern=r"^(nfc|qr|spoolbuddy)$",
        description="Source of the scan: nfc, qr, or spoolbuddy.",
    )
    timeout: int = Field(
        default=300,
        ge=10,
        le=3600,
        description="Timeout in seconds before marking as timed_out (10–3600).",
    )

    @model_validator(mode="after")
    def _require_at_least_one_identifier(self) -> "PendingSlotAssignmentCreate":
        if not self.tray_uuid and not self.tag_uid:
            raise ValueError("At least one of tray_uuid or tag_uid must be provided.")
        return self


class PendingSlotAssignmentResponse(BaseModel):
    """Response body for assignment endpoints."""

    assignment_id: int
    tray_uuid: str | None = None
    tag_uid: str | None = None
    spool_id: int | None = None
    printer_id: int | None = None
    source: str
    status: str  # pending, completed, timed_out, cancelled
    timeout_seconds: int
    # Completion details
    assigned_printer_id: int | None = None
    assigned_ams_id: int | None = None
    assigned_tray_id: int | None = None
    completed_at: datetime | None = None
    time_to_placement: float | None = None
    created_at: datetime

    class Config:
        from_attributes = True
