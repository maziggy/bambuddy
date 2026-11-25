"""Schemas for user email notification preferences."""

from pydantic import BaseModel

from backend.app.schemas.not_null import IgnoresNullForRequiredColumns


class UserEmailPreferenceResponse(BaseModel):
    """Response schema for user email notification preferences."""

    notify_print_start: bool
    notify_print_complete: bool
    notify_print_failed: bool
    notify_print_stopped: bool

    class Config:
        from_attributes = True


class UserEmailPreferenceUpdate(IgnoresNullForRequiredColumns):
    """Update schema for user email notification preferences."""

    __orm_table__ = "user_email_preferences"

    notify_print_start: bool
    notify_print_complete: bool
    notify_print_failed: bool
    notify_print_stopped: bool
