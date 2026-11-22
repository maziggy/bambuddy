from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class SpoolAssignment(Base):
    """Assignment of a spool to a specific AMS slot on a printer."""

    __tablename__ = "spool_assignment"

    id: Mapped[int] = mapped_column(primary_key=True)
    spool_id: Mapped[int] = mapped_column(ForeignKey("spool.id", ondelete="CASCADE"))
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"))
    ams_id: Mapped[int] = mapped_column(Integer)  # 0-3, 128+ (HT), 254/255 (ext)
    tray_id: Mapped[int] = mapped_column(Integer)  # 0-3
    fingerprint_color: Mapped[str | None] = mapped_column(String(8))  # tray_color snapshot
    fingerprint_type: Mapped[str | None] = mapped_column(String(50))  # tray_type snapshot
    # Who assigned it from the inventory route: the signed-in user, or an API
    # key's owner. The replay on insert resolves a custom cloud preset with this
    # user's cloud sign-in; it has none of its own (#3519).
    assigned_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    spool: Mapped["Spool"] = relationship(back_populates="assignments")
    printer: Mapped["Printer"] = relationship()

    __table_args__ = (UniqueConstraint("printer_id", "ams_id", "tray_id"),)

    @property
    def printer_name(self) -> str | None:
        """Get printer name from loaded relationship."""
        return self.printer.name if self.printer else None

    @property
    def printer_model(self) -> str | None:
        """Printer model from the loaded relationship (lets the UI label an H3C's per-hotend external inputs)."""
        return self.printer.model if self.printer else None


from backend.app.models.printer import Printer  # noqa: E402, F401
from backend.app.models.spool import Spool  # noqa: E402, F401
