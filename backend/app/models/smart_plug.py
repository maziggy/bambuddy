from datetime import datetime
from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class SmartPlug(Base):
    """Tasmota smart plug for printer power control."""

    __tablename__ = "smart_plugs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    ip_address: Mapped[str] = mapped_column(String(45))  # IPv4/IPv6

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

    # Status tracking
    last_state: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "ON"/"OFF"
    last_checked: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationship
    printer: Mapped["Printer"] = relationship(back_populates="smart_plug")


from backend.app.models.printer import Printer  # noqa: E402
