from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base

AMS_ID_CHECK = "(ams_id >= 0 AND ams_id <= 7) OR ams_id = 32 OR (ams_id >= 128 AND ams_id <= 191) OR ams_id = 255"
# Slots 0-3; 4-8 only on the AMS 3H (32) and among an H3C's external inputs (255).
TRAY_ID_CHECK = "tray_id >= 0 AND (tray_id <= 3 OR (ams_id IN (32, 255) AND tray_id <= 8))"


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

    __table_args__ = (
        UniqueConstraint("printer_id", "ams_id", "tray_id", name="uq_slot_assignment"),
        # 0-7: standard AMS units. 32: the H3C's AMS 3H. 128-191: AMS-HT (each
        # unit uses ams_id 128+, single tray). 255: external / VT tray. Matches
        # the value range the internal `spool_assignment` table accepts. See
        # #1274 — H2C with AMS-HT on the left nozzle reports ams_id=128.
        CheckConstraint(AMS_ID_CHECK, name="ck_ams_id_range"),
        CheckConstraint(TRAY_ID_CHECK, name="ck_tray_id_range"),
    )


from backend.app.models.printer import Printer  # noqa: E402, F401
