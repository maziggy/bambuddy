"""Pydantic schemas for notification providers."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ProviderType(str, Enum):
    """Supported notification provider types."""

    CALLMEBOT = "callmebot"
    NTFY = "ntfy"
    PUSHOVER = "pushover"
    TELEGRAM = "telegram"
    EMAIL = "email"
    DISCORD = "discord"
    WEBHOOK = "webhook"


class NotificationProviderBase(BaseModel):
    """Base schema for notification providers."""

    name: str = Field(..., min_length=1, max_length=100, description="User-defined name")
    provider_type: ProviderType = Field(..., description="Type of notification provider")
    enabled: bool = Field(default=True, description="Whether notifications are enabled")
    config: dict[str, Any] = Field(..., description="Provider-specific configuration")

    # Event triggers - print lifecycle
    on_print_start: bool = Field(default=False, description="Notify on print start")
    on_print_complete: bool = Field(default=True, description="Notify on print complete")
    on_print_failed: bool = Field(default=True, description="Notify on print failed")
    on_print_stopped: bool = Field(default=True, description="Notify when print is stopped/cancelled")
    on_print_progress: bool = Field(default=False, description="Notify at 25%, 50%, 75% progress")

    # Event triggers - printer status
    on_printer_offline: bool = Field(default=False, description="Notify when printer goes offline")
    on_printer_error: bool = Field(default=False, description="Notify on printer errors (AMS, etc.)")
    on_filament_low: bool = Field(default=False, description="Notify when filament is running low")
    on_maintenance_due: bool = Field(default=False, description="Notify when maintenance is due")

    # Event triggers - AMS environmental alarms (regular AMS)
    on_ams_humidity_high: bool = Field(default=False, description="Notify when AMS humidity exceeds threshold")
    on_ams_temperature_high: bool = Field(default=False, description="Notify when AMS temperature exceeds threshold")

    # Event triggers - AMS-HT environmental alarms
    on_ams_ht_humidity_high: bool = Field(default=False, description="Notify when AMS-HT humidity exceeds threshold")
    on_ams_ht_temperature_high: bool = Field(
        default=False, description="Notify when AMS-HT temperature exceeds threshold"
    )

    # Event triggers - Build plate detection
    on_plate_not_empty: bool = Field(default=True, description="Notify when objects detected on plate before print")

    # Event triggers - Print queue
    on_queue_job_added: bool = Field(default=False, description="Notify when job is added to queue")
    on_queue_job_assigned: bool = Field(default=False, description="Notify when model-based job is assigned to printer")
    on_queue_job_started: bool = Field(default=False, description="Notify when queue job starts printing")
    on_queue_job_waiting: bool = Field(default=True, description="Notify when job is waiting for filament")
    on_queue_job_skipped: bool = Field(default=True, description="Notify when job is skipped")
    on_queue_job_failed: bool = Field(default=True, description="Notify when job fails to start")
    on_queue_completed: bool = Field(default=False, description="Notify when all queue jobs finish")

    # Quiet hours
    quiet_hours_enabled: bool = Field(default=False, description="Enable quiet hours")
    quiet_hours_start: str | None = Field(default=None, description="Start time in HH:MM format")
    quiet_hours_end: str | None = Field(default=None, description="End time in HH:MM format")

    # Daily digest
    daily_digest_enabled: bool = Field(default=False, description="Batch notifications into daily digest")
    daily_digest_time: str | None = Field(default=None, description="Time to send digest in HH:MM format")

    # Printer filter
    printer_id: int | None = Field(default=None, description="Specific printer ID or null for all")

    @field_validator("quiet_hours_start", "quiet_hours_end", "daily_digest_time")
    @classmethod
    def validate_time_format(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            parts = v.split(":")
            if len(parts) != 2:
                raise ValueError("Invalid time format")
            hour, minute = int(parts[0]), int(parts[1])
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("Invalid time range")
            return f"{hour:02d}:{minute:02d}"
        except (ValueError, TypeError):
            raise ValueError("Time must be in HH:MM format (e.g., 22:00)")


class NotificationProviderCreate(NotificationProviderBase):
    """Schema for creating a notification provider."""

    pass


class NotificationProviderUpdate(BaseModel):
    """Schema for updating a notification provider (all fields optional)."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    provider_type: ProviderType | None = None
    enabled: bool | None = None
    config: dict[str, Any] | None = None

    # Event triggers - print lifecycle
    on_print_start: bool | None = None
    on_print_complete: bool | None = None
    on_print_failed: bool | None = None
    on_print_stopped: bool | None = None
    on_print_progress: bool | None = None

    # Event triggers - printer status
    on_printer_offline: bool | None = None
    on_printer_error: bool | None = None
    on_filament_low: bool | None = None
    on_maintenance_due: bool | None = None

    # Event triggers - AMS environmental alarms (regular AMS)
    on_ams_humidity_high: bool | None = None
    on_ams_temperature_high: bool | None = None

    # Event triggers - AMS-HT environmental alarms
    on_ams_ht_humidity_high: bool | None = None
    on_ams_ht_temperature_high: bool | None = None

    # Event triggers - Build plate detection
    on_plate_not_empty: bool | None = None

    # Event triggers - Print queue
    on_queue_job_added: bool | None = None
    on_queue_job_assigned: bool | None = None
    on_queue_job_started: bool | None = None
    on_queue_job_waiting: bool | None = None
    on_queue_job_skipped: bool | None = None
    on_queue_job_failed: bool | None = None
    on_queue_completed: bool | None = None

    # Quiet hours
    quiet_hours_enabled: bool | None = None
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None

    # Daily digest
    daily_digest_enabled: bool | None = None
    daily_digest_time: str | None = None

    # Printer filter
    printer_id: int | None = None


class NotificationProviderResponse(NotificationProviderBase):
    """Schema for notification provider API responses."""

    id: int
    last_success: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class NotificationTestRequest(BaseModel):
    """Schema for testing notification configuration."""

    provider_type: ProviderType
    config: dict[str, Any]


class NotificationTestResponse(BaseModel):
    """Schema for test notification response."""

    success: bool
    message: str


# Provider-specific config schemas for documentation/validation reference
class CallMeBotConfig(BaseModel):
    """CallMeBot/WhatsApp configuration."""

    phone: str = Field(..., description="Phone number with country code (e.g., +1234567890)")
    apikey: str = Field(..., description="API key from CallMeBot")


class NtfyConfig(BaseModel):
    """ntfy configuration."""

    server: str = Field(default="https://ntfy.sh", description="ntfy server URL")
    topic: str = Field(..., description="Topic name to publish to")
    auth_token: str | None = Field(default=None, description="Optional authentication token")


class PushoverConfig(BaseModel):
    """Pushover configuration."""

    user_key: str = Field(..., description="Your Pushover user key")
    app_token: str = Field(..., description="Your Pushover application token")
    priority: int = Field(default=0, ge=-2, le=2, description="Message priority (-2 to 2)")


class TelegramConfig(BaseModel):
    """Telegram bot configuration."""

    bot_token: str = Field(..., description="Bot token from @BotFather")
    chat_id: str = Field(..., description="Chat ID to send messages to")


class EmailConfig(BaseModel):
    """Email/SMTP configuration."""

    smtp_server: str = Field(..., description="SMTP server hostname")
    smtp_port: int = Field(default=587, description="SMTP port (587 for TLS, 465 for SSL)")
    username: str = Field(..., description="SMTP username/email")
    password: str = Field(..., description="SMTP password or app password")
    from_email: str = Field(..., description="From email address")
    to_email: str = Field(..., description="Recipient email address")
    use_tls: bool = Field(default=True, description="Use TLS encryption")


# Notification Log schemas
class NotificationLogResponse(BaseModel):
    """Schema for notification log API responses."""

    id: int
    provider_id: int
    provider_name: str | None = None
    provider_type: str | None = None
    event_type: str
    title: str
    message: str
    success: bool
    error_message: str | None = None
    printer_id: int | None = None
    printer_name: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class NotificationLogStats(BaseModel):
    """Statistics for notification logs."""

    total: int
    success_count: int
    failure_count: int
    by_event_type: dict[str, int]
    by_provider: dict[str, int]
