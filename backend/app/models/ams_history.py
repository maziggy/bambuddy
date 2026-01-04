from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class AMSSensorHistory(Base):
    """Historical sensor data from AMS units (humidity and temperature)."""

    __tablename__ = "ams_sensor_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"))
    ams_id: Mapped[int] = mapped_column(Integer)  # AMS unit index (0, 1, 2, 3)
    humidity: Mapped[float | None] = mapped_column(Float)  # Humidity percentage
    humidity_raw: Mapped[float | None] = mapped_column(Float)  # Raw humidity value
    temperature: Mapped[float | None] = mapped_column(Float)  # Temperature in Celsius
    recorded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    # Indexes for efficient querying
    __table_args__ = (Index("ix_ams_history_printer_ams_time", "printer_id", "ams_id", "recorded_at"),)

    # Relationship
    printer: Mapped["Printer"] = relationship(back_populates="ams_history")


from backend.app.models.printer import Printer  # noqa: E402
