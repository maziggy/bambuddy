from datetime import datetime

from pydantic import BaseModel


class APIKeyCreate(BaseModel):
    """Schema for creating a new API key."""

    name: str
    can_queue: bool = True
    can_control_printer: bool = False
    can_read_status: bool = True
    can_access_cloud: bool = False  # Read /cloud/* on the creator's behalf — default off (#1182)
    printer_ids: list[int] | None = None  # null = all printers
    expires_at: datetime | None = None


class APIKeyUpdate(BaseModel):
    """Schema for updating an API key."""

    name: str | None = None
    can_queue: bool | None = None
    can_control_printer: bool | None = None
    can_read_status: bool | None = None
    can_access_cloud: bool | None = None
    printer_ids: list[int] | None = None
    enabled: bool | None = None
    expires_at: datetime | None = None


class APIKeyResponse(BaseModel):
    """Schema for API key response (without full key)."""

    id: int
    name: str
    key_prefix: str  # First 8 chars for identification
    user_id: int | None  # Owner — NULL on legacy keys created before per-user ownership (#1182)
    can_queue: bool
    can_control_printer: bool
    can_read_status: bool
    can_access_cloud: bool
    printer_ids: list[int] | None
    enabled: bool
    last_used: datetime | None
    created_at: datetime
    expires_at: datetime | None

    class Config:
        from_attributes = True


class APIKeyCreateResponse(APIKeyResponse):
    """Response when creating a key - includes full key (shown only once)."""

    key: str  # Full API key, only shown on creation
