"""Notification provider and log models for push notifications."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, false, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base

if TYPE_CHECKING:
    from backend.app.models.printer import Printer


class NotificationDigestQueue(Base):
    """Model for queuing notifications to be sent in daily digest."""

    __tablename__ = "notification_digest_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    provider_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("notification_providers.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)  # print_start, print_complete, etc.
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    printer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("printers.id", ondelete="SET NULL"), nullable=True
    )
    printer_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    provider: Mapped["NotificationProvider"] = relationship(back_populates="digest_queue")


class NotificationLog(Base):
    """Model for logging sent notifications."""

    __tablename__ = "notification_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    provider_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("notification_providers.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)  # print_start, print_complete, etc.
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    success: Mapped[bool | None] = mapped_column(Boolean, default=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    printer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("printers.id", ondelete="SET NULL"), nullable=True
    )
    printer_name: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )  # Store name in case printer is deleted
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    provider: Mapped["NotificationProvider"] = relationship(back_populates="logs")


class NotificationProvider(Base):
    """Model for notification providers (WhatsApp, ntfy, Pushover, etc.)."""

    __tablename__ = "notification_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # User-defined name
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False)  # callmebot, ntfy, pushover, telegram, email
    enabled: Mapped[bool | None] = mapped_column(Boolean, default=True)

    # Provider-specific configuration stored as JSON string
    config: Mapped[str] = mapped_column(Text, nullable=False)

    # Attach a camera snapshot when one was captured for the event. Defaults
    # True so ntfy/Pushover/Telegram/Discord keep behaving as they always
    # did; this is the opt-out for whoever doesn't want it.
    attach_photo: Mapped[bool | None] = mapped_column(Boolean, default=True, server_default=true())

    # Event triggers - print lifecycle
    on_print_start: Mapped[bool | None] = mapped_column(Boolean, default=False)
    on_print_complete: Mapped[bool | None] = mapped_column(Boolean, default=True)
    on_print_failed: Mapped[bool | None] = mapped_column(Boolean, default=True)
    on_print_stopped: Mapped[bool | None] = mapped_column(Boolean, default=True)  # User cancelled/stopped print
    on_print_progress: Mapped[bool | None] = mapped_column(Boolean, default=False)  # 25%, 50%, 75% milestones
    on_print_missing_spool_assignment: Mapped[bool | None] = mapped_column(
        Boolean, default=False
    )  # Print started with unassigned required tray(s)
    on_billing_charge_failed: Mapped[bool | None] = mapped_column(
        Boolean, default=True, server_default=true()
    )  # A completed/stopped print could not be charged

    # Event triggers - printer status
    on_printer_offline: Mapped[bool | None] = mapped_column(Boolean, default=False)
    on_printer_error: Mapped[bool | None] = mapped_column(Boolean, default=False)  # AMS issues, etc.
    on_ai_failure_detection: Mapped[bool | None] = mapped_column(
        Boolean, default=False, server_default=false()
    )  # Obico spaghetti / failure detection (#1794)
    on_filament_low: Mapped[bool | None] = mapped_column(Boolean, default=False)
    on_maintenance_due: Mapped[bool | None] = mapped_column(Boolean, default=False)  # Maintenance reminder

    # Event triggers - AMS environmental alarms (regular AMS with 4 slots)
    on_ams_humidity_high: Mapped[bool | None] = mapped_column(Boolean, default=False)  # AMS humidity above threshold
    on_ams_temperature_high: Mapped[bool | None] = mapped_column(
        Boolean, default=False
    )  # AMS temperature above threshold
    # Auto-drying gave up on a unit (#2770). Defaults True: it reports that
    # Bambuddy has stopped acting, which nothing else in the UI would say.
    on_ams_drying_suspended: Mapped[bool | None] = mapped_column(Boolean, default=True, server_default=true())

    # Event triggers - AMS-HT environmental alarms (single slot heated AMS)
    on_ams_ht_humidity_high: Mapped[bool | None] = mapped_column(
        Boolean, default=False
    )  # AMS-HT humidity above threshold
    on_ams_ht_temperature_high: Mapped[bool | None] = mapped_column(
        Boolean, default=False
    )  # AMS-HT temperature above threshold

    # Event triggers - Home Assistant sensors bound to a printer (#1148)
    on_ha_sensor_alert: Mapped[bool | None] = mapped_column(
        Boolean, default=False, server_default=false()
    )  # Bound HA sensor entered its alert state

    # Event triggers - Home Assistant sensors bound to a storage location (#2824)
    # Its own column rather than reusing on_ha_sensor_alert above: that one can
    # be scoped to a single printer, and a location alert has no printer to
    # scope by, so sharing it would leak drybox alerts to a provider narrowed
    # to one printer's sensors.
    on_location_ha_sensor_alert: Mapped[bool | None] = mapped_column(Boolean, default=False, server_default=false())

    # Event triggers - Build plate detection
    on_plate_not_empty: Mapped[bool | None] = mapped_column(
        Boolean, default=True
    )  # Objects detected on plate before print
    # Off by default: fires after every print, alongside the print-complete alert (#2525)
    on_plate_clear_required: Mapped[bool | None] = mapped_column(
        Boolean, default=False, server_default=false()
    )  # Print ended, queue gated until plate is confirmed clear

    # Print asked for an outcome verdict (#1898). Defaults ON: it only ever
    # fires for prints where the user opted in per-job, so the provider-level
    # toggle exists to silence a channel, not to enable the feature.
    on_print_confirm_request: Mapped[bool | None] = mapped_column(Boolean, default=True, server_default=true())
    # How a Telegram provider collects that verdict (#3046): inline URL
    # buttons ("buttons", the original behaviour), a thumbs-up/down reaction
    # on the message ("reactions"), or both. Reactions need no inbound
    # connectivity — the poller fetches them — so they work without an
    # external_url. Ignored for every other provider type.
    telegram_verdict_mode: Mapped[str | None] = mapped_column(String(16), default="buttons", server_default="buttons")

    # Event triggers - Bed cooled after print
    on_bed_cooled: Mapped[bool | None] = mapped_column(Boolean, default=False)  # Bed cooled below threshold after print
    on_first_layer_complete: Mapped[bool | None] = mapped_column(
        Boolean, default=False
    )  # First layer finished printing
    # A pause the slicer put into the print is coming up (#2984). Off by
    # default: nothing new arrives on upgrade.
    on_print_pause_upcoming: Mapped[bool | None] = mapped_column(Boolean, default=False, server_default=false())
    # Messages another application sends through Bambuddy (POST /notifications/app-message),
    # e.g. Bambuddy Orders' "an order needs you". Off by default: nothing new arrives on upgrade.
    on_app_message: Mapped[bool | None] = mapped_column(Boolean, default=False, server_default=false())

    # Event triggers - Inventory stock alerts
    on_stock_reorder_alert: Mapped[bool | None] = mapped_column(
        Boolean, default=False, server_default=false()
    )  # SKU hits reorder point
    on_stock_break_alert: Mapped[bool | None] = mapped_column(
        Boolean, default=False, server_default=false()
    )  # Stock will run out before replenishment

    # Event triggers - Print queue
    on_queue_job_added: Mapped[bool | None] = mapped_column(Boolean, default=False)  # Job added to queue
    on_queue_job_assigned: Mapped[bool | None] = mapped_column(
        Boolean, default=False
    )  # Model-based job assigned to printer
    on_queue_job_started: Mapped[bool | None] = mapped_column(Boolean, default=False)  # Queue job started printing
    on_queue_job_waiting: Mapped[bool | None] = mapped_column(
        Boolean, default=True
    )  # Job waiting for filament or printer
    on_queue_job_skipped: Mapped[bool | None] = mapped_column(
        Boolean, default=True
    )  # Job skipped (previous print failed)
    on_queue_job_failed: Mapped[bool | None] = mapped_column(Boolean, default=True)  # Job failed to start
    on_queue_completed: Mapped[bool | None] = mapped_column(Boolean, default=False)  # All pending jobs finished

    # Quiet hours (do not disturb)
    quiet_hours_enabled: Mapped[bool | None] = mapped_column(Boolean, default=False)
    quiet_hours_start: Mapped[str | None] = mapped_column(String(5), nullable=True)  # HH:MM format, e.g., "22:00"
    quiet_hours_end: Mapped[str | None] = mapped_column(String(5), nullable=True)  # HH:MM format, e.g., "07:00"

    # Daily digest (batch notifications into a single daily summary)
    daily_digest_enabled: Mapped[bool | None] = mapped_column(Boolean, default=False)
    daily_digest_time: Mapped[str | None] = mapped_column(String(5), nullable=True)  # HH:MM format, e.g., "08:00"

    # Optional: Link to specific printer (NULL = all printers). CASCADE, not SET
    # NULL: a provider limited to a deleted printer must not become one for all
    # of them. The printer delete route removes it first (remote cleanup).
    printer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("printers.id", ondelete="CASCADE"), nullable=True
    )

    # Status tracking
    last_success: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Timestamps
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    printer: Mapped["Printer | None"] = relationship(back_populates="notification_providers")
    logs: Mapped[list["NotificationLog"]] = relationship(back_populates="provider", cascade="all, delete-orphan")
    digest_queue: Mapped[list["NotificationDigestQueue"]] = relationship(
        back_populates="provider", cascade="all, delete-orphan"
    )
    # ORM-level cascade like the two above: SQLite ships with foreign keys
    # off, so the column's ON DELETE CASCADE alone would leave rows behind.
    pending_verdicts: Mapped[list["TelegramPendingVerdict"]] = relationship(
        back_populates="provider", cascade="all, delete-orphan"
    )


class TelegramPendingVerdict(Base):
    """A Telegram outcome prompt still waiting for a thumbs-up/down reaction (#3046).

    One row per delivered ``print_confirm_request`` message on a provider in
    "reactions" or "both" mode. The reaction poller matches incoming
    ``message_reaction`` updates against (provider, chat, message) and deletes
    the row once a verdict landed; rows older than a week are pruned.
    """

    __tablename__ = "telegram_pending_verdicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    provider_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("notification_providers.id", ondelete="CASCADE"), nullable=False
    )
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    archive_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("print_archives.id", ondelete="CASCADE"), nullable=False
    )
    # The archive's confirm_token when the prompt went out. A reaction counts
    # only while that is still the archive's live token, i.e. exactly as long
    # as the prompt's one-tap links would: a reprint clears the token, so an
    # earlier run's message can no longer answer the new run.
    confirm_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # sendPhoto messages carry the prompt as a caption, which is edited with a
    # different Bot API method than a plain text message.
    has_caption: Mapped[bool | None] = mapped_column(Boolean, default=False)
    # The Markdown we sent, so the confirmation edit can append to it — the
    # reaction update does not carry the message body.
    message_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    provider: Mapped["NotificationProvider"] = relationship(back_populates="pending_verdicts")
