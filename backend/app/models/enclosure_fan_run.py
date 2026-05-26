from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class EnclosureFanRun(Base):
    """Records each on/off session for a printer's HA-connected enclosure fan."""

    __tablename__ = "enclosure_fan_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"))
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("ix_fan_runs_printer_time", "printer_id", "started_at"),)

    printer: Mapped["Printer"] = relationship(back_populates="fan_runs")


from backend.app.models.printer import Printer  # noqa: E402
