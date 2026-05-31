from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class StorageReading(Base):
    """Time-series temp/humidity reading from an HA sensor for a storage unit."""

    __tablename__ = "storage_readings"

    id: Mapped[int] = mapped_column(primary_key=True)
    storage_unit_id: Mapped[int] = mapped_column(ForeignKey("storage_units.id", ondelete="CASCADE"))
    temp: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity: Mapped[float | None] = mapped_column(Float, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    __table_args__ = (Index("ix_storage_readings_unit_time", "storage_unit_id", "recorded_at"),)

    unit: Mapped["StorageUnit"] = relationship(back_populates="readings")


from backend.app.models.storage_unit import StorageUnit  # noqa: E402
