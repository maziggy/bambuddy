"""Model for printer automation codes (start/end) per printer."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class Automation(Base):
    """Store start/end automation snippets for a given printer."""

    __tablename__ = "automation"

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"))

    # G-code / command snippets used for automation detection and actions
    start_code: Mapped[str] = mapped_column(Text, default="")
    start_code_detect: Mapped[str] = mapped_column(Text, default="")
    start_code_after: Mapped[str] = mapped_column(Text, default="")

    end_code: Mapped[str] = mapped_column(Text, default="")
    end_code_detect: Mapped[str] = mapped_column(Text, default="")
    end_code_after: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationship back to printer
    printer: Mapped["Printer"] = relationship(back_populates="automation")


from backend.app.models.printer import Printer  # noqa: E402
