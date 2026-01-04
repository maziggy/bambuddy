"""Model for K-profile notes stored locally (not on printer)."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class KProfileNote(Base):
    """Notes for K-profiles stored locally since printers don't support notes."""

    __tablename__ = "kprofile_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"))
    # setting_id is the unique identifier for a K-profile on the printer
    setting_id: Mapped[str] = mapped_column(String(100))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationship to printer
    printer: Mapped["Printer"] = relationship(back_populates="kprofile_notes")

    # Composite index for efficient lookups
    __table_args__ = (Index("ix_kprofile_notes_printer_setting", "printer_id", "setting_id", unique=True),)


from backend.app.models.printer import Printer  # noqa: E402
