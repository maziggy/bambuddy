from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class EnclosureReading(Base):
    """Time-series record of HA-sourced enclosure temp/humidity for a printer."""

    __tablename__ = "enclosure_readings"

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"))
    temp: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity: Mapped[float | None] = mapped_column(Float, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    __table_args__ = (Index("ix_enclosure_readings_printer_time", "printer_id", "recorded_at"),)

    printer: Mapped["Printer"] = relationship(back_populates="enclosure_readings")


from backend.app.models.printer import Printer  # noqa: E402
