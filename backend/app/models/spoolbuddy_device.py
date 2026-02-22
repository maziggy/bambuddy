from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class SpoolBuddyDevice(Base):
    """SpoolBuddy device registration for RPi-based filament management stations."""

    __tablename__ = "spoolbuddy_devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    hostname: Mapped[str] = mapped_column(String(100))
    ip_address: Mapped[str] = mapped_column(String(45))
    firmware_version: Mapped[str | None] = mapped_column(String(20))
    has_nfc: Mapped[bool] = mapped_column(Boolean, default=True)
    has_scale: Mapped[bool] = mapped_column(Boolean, default=True)
    tare_offset: Mapped[int] = mapped_column(Integer, default=0)
    calibration_factor: Mapped[float] = mapped_column(Float, default=1.0)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime)
    pending_command: Mapped[str | None] = mapped_column(String(50))
    nfc_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    scale_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    uptime_s: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
