from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class StorageUnit(Base):
    """A filament dryer or spool storage box with optional HA temp/humidity sensors."""

    __tablename__ = "storage_units"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    unit_type: Mapped[str] = mapped_column(String(20), default="storage")  # "dryer" or "storage"
    ha_temp_entity: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ha_humidity_entity: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    readings: Mapped[list["StorageReading"]] = relationship(back_populates="unit", cascade="all, delete-orphan")


from backend.app.models.storage_reading import StorageReading  # noqa: E402
