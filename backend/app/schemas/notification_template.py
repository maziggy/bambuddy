"""Pydantic schemas for notification templates."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class EventType(StrEnum):
    """Supported notification event types."""

    PRINT_START = "print_start"
    PRINT_COMPLETE = "print_complete"
    PRINT_FAILED = "print_failed"
    PRINT_STOPPED = "print_stopped"
    PRINT_PROGRESS = "print_progress"
    PRINTER_OFFLINE = "printer_offline"
    PRINTER_ERROR = "printer_error"
    FILAMENT_LOW = "filament_low"
    MAINTENANCE_DUE = "maintenance_due"
    AMS_HUMIDITY_HIGH = "ams_humidity_high"
    AMS_TEMPERATURE_HIGH = "ams_temperature_high"
    TEST = "test"


# Available variables for each event type
EVENT_VARIABLES: dict[str, list[str]] = {
    "print_start": ["printer", "filename", "estimated_time", "timestamp", "app_name"],
    "print_complete": [
        "printer",
        "filename",
        "duration",
        "filament_grams",
        "finish_photo_url",
        "timestamp",
        "app_name",
    ],
    "print_failed": ["printer", "filename", "duration", "reason", "finish_photo_url", "timestamp", "app_name"],
    "print_stopped": ["printer", "filename", "duration", "finish_photo_url", "timestamp", "app_name"],
    "print_progress": ["printer", "filename", "progress", "remaining_time", "timestamp", "app_name"],
    "printer_offline": ["printer", "timestamp", "app_name"],
    "printer_error": ["printer", "error_type", "error_detail", "timestamp", "app_name"],
    "filament_low": ["printer", "slot", "remaining_percent", "color", "timestamp", "app_name"],
    "maintenance_due": ["printer", "items", "timestamp", "app_name"],
    "ams_humidity_high": ["printer", "ams_label", "humidity", "threshold", "timestamp", "app_name"],
    "ams_temperature_high": ["printer", "ams_label", "temperature", "threshold", "timestamp", "app_name"],
    "test": ["app_name", "timestamp"],
    # Queue notifications
    "queue_job_added": ["job_name", "target", "timestamp", "app_name"],
    "queue_job_assigned": ["job_name", "printer", "target_model", "timestamp", "app_name"],
    "queue_job_started": ["printer", "job_name", "estimated_time", "timestamp", "app_name"],
    "queue_job_waiting": ["job_name", "target_model", "waiting_reason", "timestamp", "app_name"],
    "queue_job_skipped": ["printer", "job_name", "reason", "timestamp", "app_name"],
    "queue_job_failed": ["printer", "job_name", "reason", "timestamp", "app_name"],
    "queue_completed": ["completed_count", "timestamp", "app_name"],
}

# Sample data for previewing templates
SAMPLE_DATA: dict[str, dict[str, str]] = {
    "print_start": {
        "printer": "Bambu X1C",
        "filename": "Benchy.3mf",
        "estimated_time": "1h 23m",
        "timestamp": "2024-01-15 14:30",
        "app_name": "Bambuddy",
    },
    "print_complete": {
        "printer": "Bambu X1C",
        "filename": "Benchy.3mf",
        "duration": "1h 18m",
        "filament_grams": "15.2",
        "finish_photo_url": "/api/v1/archives/123/photos/finish_20240115_154800_abc12345.jpg",
        "timestamp": "2024-01-15 15:48",
        "app_name": "Bambuddy",
    },
    "print_failed": {
        "printer": "Bambu X1C",
        "filename": "Benchy.3mf",
        "duration": "0h 45m",
        "reason": "Filament runout",
        "finish_photo_url": "/api/v1/archives/123/photos/finish_20240115_151500_def67890.jpg",
        "timestamp": "2024-01-15 15:15",
        "app_name": "Bambuddy",
    },
    "print_stopped": {
        "printer": "Bambu X1C",
        "filename": "Benchy.3mf",
        "duration": "0h 30m",
        "finish_photo_url": "/api/v1/archives/123/photos/finish_20240115_150000_ghi11223.jpg",
        "timestamp": "2024-01-15 15:00",
        "app_name": "Bambuddy",
    },
    "print_progress": {
        "printer": "Bambu X1C",
        "filename": "Benchy.3mf",
        "progress": "50",
        "remaining_time": "0h 41m",
        "timestamp": "2024-01-15 15:00",
        "app_name": "Bambuddy",
    },
    "printer_offline": {
        "printer": "Bambu X1C",
        "timestamp": "2024-01-15 14:30",
        "app_name": "Bambuddy",
    },
    "printer_error": {
        "printer": "Bambu X1C",
        "error_type": "AMS Error",
        "error_detail": "Filament slot 1 jammed",
        "timestamp": "2024-01-15 14:30",
        "app_name": "Bambuddy",
    },
    "filament_low": {
        "printer": "Bambu X1C",
        "slot": "1",
        "remaining_percent": "15",
        "color": "Black PLA",
        "timestamp": "2024-01-15 14:30",
        "app_name": "Bambuddy",
    },
    "maintenance_due": {
        "printer": "Bambu X1C",
        "items": "• Nozzle cleaning (OVERDUE)\n• Carbon rod lubrication (Soon)",
        "timestamp": "2024-01-15 14:30",
        "app_name": "Bambuddy",
    },
    "ams_humidity_high": {
        "printer": "Bambu X1C",
        "ams_label": "AMS-A",
        "humidity": "75",
        "threshold": "60",
        "timestamp": "2024-01-15 14:30",
        "app_name": "Bambuddy",
    },
    "ams_temperature_high": {
        "printer": "Bambu X1C",
        "ams_label": "AMS-A",
        "temperature": "42",
        "threshold": "35",
        "timestamp": "2024-01-15 14:30",
        "app_name": "Bambuddy",
    },
    "test": {
        "app_name": "Bambuddy",
        "timestamp": "2024-01-15 14:30",
    },
    # Queue notifications
    "queue_job_added": {
        "job_name": "Benchy.3mf",
        "target": "Bambu X1C",
        "timestamp": "2024-01-15 14:30",
        "app_name": "Bambuddy",
    },
    "queue_job_assigned": {
        "job_name": "Benchy.3mf",
        "printer": "Bambu X1C #1",
        "target_model": "X1C",
        "timestamp": "2024-01-15 14:30",
        "app_name": "Bambuddy",
    },
    "queue_job_started": {
        "printer": "Bambu X1C",
        "job_name": "Benchy.3mf",
        "estimated_time": "1h 23m",
        "timestamp": "2024-01-15 14:30",
        "app_name": "Bambuddy",
    },
    "queue_job_waiting": {
        "job_name": "Benchy.3mf",
        "target_model": "X1C",
        "waiting_reason": "Printer1 (needs PLA)",
        "timestamp": "2024-01-15 14:30",
        "app_name": "Bambuddy",
    },
    "queue_job_skipped": {
        "printer": "Bambu X1C",
        "job_name": "Benchy.3mf",
        "reason": "Previous print failed",
        "timestamp": "2024-01-15 14:30",
        "app_name": "Bambuddy",
    },
    "queue_job_failed": {
        "printer": "Bambu X1C",
        "job_name": "Benchy.3mf",
        "reason": "Upload failed: connection timeout",
        "timestamp": "2024-01-15 14:30",
        "app_name": "Bambuddy",
    },
    "queue_completed": {
        "completed_count": "5",
        "timestamp": "2024-01-15 18:30",
        "app_name": "Bambuddy",
    },
}


class NotificationTemplateBase(BaseModel):
    """Base schema for notification templates."""

    title_template: str = Field(..., min_length=1, max_length=200)
    body_template: str = Field(..., min_length=1, max_length=2000)


class NotificationTemplateUpdate(BaseModel):
    """Schema for updating a notification template."""

    title_template: str | None = Field(default=None, min_length=1, max_length=200)
    body_template: str | None = Field(default=None, min_length=1, max_length=2000)


class NotificationTemplateResponse(NotificationTemplateBase):
    """Schema for notification template API responses."""

    id: int
    event_type: str
    name: str
    is_default: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TemplateVariableInfo(BaseModel):
    """Information about a template variable."""

    name: str
    description: str


class EventVariablesResponse(BaseModel):
    """Response for available variables per event type."""

    event_type: str
    event_name: str
    variables: list[str]


class TemplatePreviewRequest(BaseModel):
    """Request to preview a template with sample data."""

    event_type: str
    title_template: str
    body_template: str


class TemplatePreviewResponse(BaseModel):
    """Response with rendered template preview."""

    title: str
    body: str
