"""Schemas for user email notification preferences."""

from pydantic import BaseModel


class UserEmailPreferenceResponse(BaseModel):
    """Response schema for user email notification preferences."""

    notify_print_start: bool
    notify_print_complete: bool
    notify_print_failed: bool
    notify_print_stopped: bool

    class Config:
        from_attributes = True


class UserEmailPreferenceUpdate(BaseModel):
    """Update schema for user email notification preferences."""

    notify_print_start: bool
    notify_print_complete: bool
    notify_print_failed: bool
    notify_print_stopped: bool
