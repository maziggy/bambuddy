from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class SmartPlug(Base):
    """Smart plug for printer power control (Tasmota or Home Assistant)."""

    __tablename__ = "smart_plugs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)  # IPv4/IPv6 (required for Tasmota)

    # Plug type: "tasmota" (default) or "homeassistant"
    plug_type: Mapped[str] = mapped_column(String(20), default="tasmota")
    # Home Assistant entity ID (e.g., "switch.printer_plug")
    ha_entity_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Link to printer (1:1)
    printer_id: Mapped[int | None] = mapped_column(
        ForeignKey("printers.id", ondelete="SET NULL"), unique=True, nullable=True
    )

    # Automation settings
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_on: Mapped[bool] = mapped_column(Boolean, default=True)  # Turn on at print start
    auto_off: Mapped[bool] = mapped_column(Boolean, default=True)  # Turn off at print complete/fail

    # Turn-off delay mode: "time" or "temperature"
    off_delay_mode: Mapped[str] = mapped_column(String(20), default="time")
    off_delay_minutes: Mapped[int] = mapped_column(Integer, default=5)  # For time mode
    off_temp_threshold: Mapped[int] = mapped_column(Integer, default=70)  # For temp mode (°C)

    # Optional auth (some Tasmota configs require it)
    username: Mapped[str | None] = mapped_column(String(50), nullable=True)
    password: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Power alerts
    power_alert_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    power_alert_high: Mapped[float | None] = mapped_column(Float, nullable=True)  # Alert when power > this (watts)
    power_alert_low: Mapped[float | None] = mapped_column(Float, nullable=True)  # Alert when power < this (watts)
    power_alert_last_triggered: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # Cooldown tracking

    # Schedule (time-based on/off)
    schedule_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    schedule_on_time: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "HH:MM" format
    schedule_off_time: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "HH:MM" format

    # Switchbar visibility
    show_in_switchbar: Mapped[bool] = mapped_column(Boolean, default=False)

    # Status tracking
    last_state: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "ON"/"OFF"
    last_checked: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    auto_off_executed: Mapped[bool] = mapped_column(Boolean, default=False)  # True when auto-off was triggered
    auto_off_pending: Mapped[bool] = mapped_column(Boolean, default=False)  # True when waiting for cooldown
    auto_off_pending_since: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )  # When auto-off was scheduled

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationship
    printer: Mapped["Printer"] = relationship(back_populates="smart_plug")


from backend.app.models.printer import Printer  # noqa: E402
