"""Notification template model for customizable notification messages."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class NotificationTemplate(Base):
    """Model for notification message templates."""

    __tablename__ = "notification_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    title_template: Mapped[str] = mapped_column(Text, nullable=False)
    body_template: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


# Default templates for seeding
DEFAULT_TEMPLATES = [
    {
        "event_type": "print_start",
        "name": "Print Started",
        "title_template": "Print Started",
        "body_template": "{printer}: {filename}\nEstimated: {estimated_time}",
    },
    {
        "event_type": "print_complete",
        "name": "Print Completed",
        "title_template": "Print Completed",
        "body_template": "{printer}: {filename}\nTime: {duration}\nFilament: {filament_grams}g",
    },
    {
        "event_type": "print_failed",
        "name": "Print Failed",
        "title_template": "Print Failed",
        "body_template": "{printer}: {filename}\nTime: {duration}\nReason: {reason}",
    },
    {
        "event_type": "print_stopped",
        "name": "Print Stopped",
        "title_template": "Print Stopped",
        "body_template": "{printer}: {filename}\nTime: {duration}",
    },
    {
        "event_type": "print_progress",
        "name": "Print Progress",
        "title_template": "Print {progress}% Complete",
        "body_template": "{printer}: {filename}\nRemaining: {remaining_time}",
    },
    {
        "event_type": "printer_offline",
        "name": "Printer Offline",
        "title_template": "Printer Offline",
        "body_template": "{printer} has disconnected",
    },
    {
        "event_type": "printer_error",
        "name": "Printer Error",
        "title_template": "Printer Error: {error_type}",
        "body_template": "{printer}\n{error_detail}",
    },
    {
        "event_type": "plate_not_empty",
        "name": "Plate Not Empty",
        "title_template": "Plate Not Empty - Print Paused",
        "body_template": "{printer}: Objects detected on build plate. Print has been paused. Clear plate and resume.",
    },
    {
        "event_type": "filament_low",
        "name": "Filament Low",
        "title_template": "Filament Low",
        "body_template": "{printer}: Slot {slot} at {remaining_percent}%",
    },
    {
        "event_type": "maintenance_due",
        "name": "Maintenance Due",
        "title_template": "Maintenance Due",
        "body_template": "{printer}:\n{items}",
    },
    {
        "event_type": "ams_humidity_high",
        "name": "AMS Humidity High",
        "title_template": "AMS Humidity Alert",
        "body_template": "{printer} {ams_label}: Humidity {humidity}% exceeds {threshold}% threshold",
    },
    {
        "event_type": "ams_temperature_high",
        "name": "AMS Temperature High",
        "title_template": "AMS Temperature Alert",
        "body_template": "{printer} {ams_label}: Temperature {temperature}°C exceeds {threshold}°C threshold",
    },
    {
        "event_type": "bed_cooled",
        "name": "Bed Cooled",
        "title_template": "Bed Cooled",
        "body_template": "{printer}: Bed cooled to {bed_temp}°C (threshold: {threshold}°C)",
    },
    {
        "event_type": "test",
        "name": "Test Notification",
        "title_template": "Bambuddy Test",
        "body_template": "This is a test notification. If you see this, notifications are working!",
    },
    # Queue notifications
    {
        "event_type": "queue_job_added",
        "name": "Queue Job Added",
        "title_template": "Job Queued",
        "body_template": "{job_name} added to queue for {target}",
    },
    {
        "event_type": "queue_job_assigned",
        "name": "Queue Job Assigned",
        "title_template": "Job Assigned",
        "body_template": "{job_name} assigned to {printer} (from Any {target_model} queue)",
    },
    {
        "event_type": "queue_job_started",
        "name": "Queue Job Started",
        "title_template": "Queue Job Started",
        "body_template": "{printer}: {job_name}\nEstimated: {estimated_time}",
    },
    {
        "event_type": "queue_job_waiting",
        "name": "Queue Job Waiting",
        "title_template": "Job Waiting for Filament",
        "body_template": "{job_name} waiting for {target_model}\n{waiting_reason}",
    },
    {
        "event_type": "queue_job_skipped",
        "name": "Queue Job Skipped",
        "title_template": "Job Skipped",
        "body_template": "{printer}: {job_name}\nReason: {reason}",
    },
    {
        "event_type": "queue_job_failed",
        "name": "Queue Job Failed",
        "title_template": "Job Failed to Start",
        "body_template": "{printer}: {job_name}\nReason: {reason}",
    },
    {
        "event_type": "queue_completed",
        "name": "Queue Completed",
        "title_template": "Queue Complete",
        "body_template": "All {completed_count} queued jobs have finished",
    },
    {
        "event_type": "user_created",
        "name": "Welcome Email",
        "title_template": "Welcome to {app_name}",
        "body_template": "Welcome {username}!\n\nYour account has been created.\nUsername: {username}\nPassword: {password}\n\nLogin at: {login_url}",
    },
    {
        "event_type": "password_reset",
        "name": "Password Reset",
        "title_template": "{app_name} - Password Reset",
        "body_template": "Hello {username},\n\nYour password has been reset.\nNew Password: {password}\n\nLogin at: {login_url}",
    },
]
