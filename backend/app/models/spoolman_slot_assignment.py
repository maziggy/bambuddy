from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class SpoolmanSlotAssignment(Base):
    """Assignment of a Spoolman spool to a specific AMS slot on a printer.

    Tracks which Spoolman spool ID occupies a given (printer, ams, tray) slot.
    This is the source of truth for Spoolman slot assignments — Spoolman's own
    ``spool.location`` field is NOT managed by Bambuddy and is left for the user.
    """

    __tablename__ = "spoolman_slot_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"))
    ams_id: Mapped[int] = mapped_column(Integer)
    tray_id: Mapped[int] = mapped_column(Integer)
    spoolman_spool_id: Mapped[int] = mapped_column(Integer)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    printer: Mapped["Printer"] = relationship()

    __table_args__ = (UniqueConstraint("printer_id", "ams_id", "tray_id"),)


from backend.app.models.printer import Printer  # noqa: E402, F401
