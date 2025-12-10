"""Notification provider and log models for push notifications."""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, Time
from sqlalchemy.orm import relationship

from backend.app.core.database import Base


class NotificationDigestQueue(Base):
    """Model for queuing notifications to be sent in daily digest."""

    __tablename__ = "notification_digest_queue"

    id = Column(Integer, primary_key=True, index=True)
    provider_id = Column(Integer, ForeignKey("notification_providers.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(50), nullable=False)  # print_start, print_complete, etc.
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    printer_id = Column(Integer, ForeignKey("printers.id", ondelete="SET NULL"), nullable=True)
    printer_name = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    provider = relationship("NotificationProvider", back_populates="digest_queue")


class NotificationLog(Base):
    """Model for logging sent notifications."""

    __tablename__ = "notification_logs"

    id = Column(Integer, primary_key=True, index=True)
    provider_id = Column(Integer, ForeignKey("notification_providers.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(50), nullable=False)  # print_start, print_complete, etc.
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    success = Column(Boolean, default=True)
    error_message = Column(Text, nullable=True)
    printer_id = Column(Integer, ForeignKey("printers.id", ondelete="SET NULL"), nullable=True)
    printer_name = Column(String(100), nullable=True)  # Store name in case printer is deleted
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    provider = relationship("NotificationProvider", back_populates="logs")


class NotificationProvider(Base):
    """Model for notification providers (WhatsApp, ntfy, Pushover, etc.)."""

    __tablename__ = "notification_providers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)  # User-defined name
    provider_type = Column(String(50), nullable=False)  # callmebot, ntfy, pushover, telegram, email
    enabled = Column(Boolean, default=True)

    # Provider-specific configuration stored as JSON string
    config = Column(Text, nullable=False)

    # Event triggers - print lifecycle
    on_print_start = Column(Boolean, default=False)
    on_print_complete = Column(Boolean, default=True)
    on_print_failed = Column(Boolean, default=True)
    on_print_stopped = Column(Boolean, default=True)  # User cancelled/stopped print
    on_print_progress = Column(Boolean, default=False)  # 25%, 50%, 75% milestones

    # Event triggers - printer status
    on_printer_offline = Column(Boolean, default=False)
    on_printer_error = Column(Boolean, default=False)  # AMS issues, etc.
    on_filament_low = Column(Boolean, default=False)
    on_maintenance_due = Column(Boolean, default=False)  # Maintenance reminder

    # Event triggers - AMS environmental alarms
    on_ams_humidity_high = Column(Boolean, default=False)  # Humidity above threshold
    on_ams_temperature_high = Column(Boolean, default=False)  # Temperature above threshold

    # Quiet hours (do not disturb)
    quiet_hours_enabled = Column(Boolean, default=False)
    quiet_hours_start = Column(String(5), nullable=True)  # HH:MM format, e.g., "22:00"
    quiet_hours_end = Column(String(5), nullable=True)  # HH:MM format, e.g., "07:00"

    # Daily digest (batch notifications into a single daily summary)
    daily_digest_enabled = Column(Boolean, default=False)
    daily_digest_time = Column(String(5), nullable=True)  # HH:MM format, e.g., "08:00"

    # Optional: Link to specific printer (NULL = all printers)
    printer_id = Column(Integer, ForeignKey("printers.id", ondelete="SET NULL"), nullable=True)

    # Status tracking
    last_success = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    last_error_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    printer = relationship("Printer", back_populates="notification_providers")
    logs = relationship("NotificationLog", back_populates="provider", cascade="all, delete-orphan")
    digest_queue = relationship("NotificationDigestQueue", back_populates="provider", cascade="all, delete-orphan")
