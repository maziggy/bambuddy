from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class SpoolUsageHistory(Base):
    """Record of filament consumption for a spool during a print."""

    __tablename__ = "spool_usage_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Kept when the spool is deleted, like the archive link below: the usage is
    # what the prints cost, and what recalculating their cost reads.
    spool_id: Mapped[int | None] = mapped_column(ForeignKey("spool.id", ondelete="SET NULL"), nullable=True)
    # The Spoolman spool charged, in Spoolman mode, where ``spool_id`` stays
    # NULL: Spoolman's ids are its own and would collide with built-in ones
    # (#3406). No foreign key, as the spool lives in Spoolman; cleared when
    # the spool is deleted through Bambuddy (#3407).
    spoolman_spool_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    printer_id: Mapped[int | None] = mapped_column(ForeignKey("printers.id", ondelete="SET NULL"))
    print_name: Mapped[str | None] = mapped_column(String(500))
    # The spool's consumption outlives the archive: deleting the archive only
    # drops the link.
    archive_id: Mapped[int | None] = mapped_column(ForeignKey("print_archives.id", ondelete="SET NULL"), nullable=True)
    weight_used: Mapped[float] = mapped_column(Float, default=0)
    percent_used: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="completed")  # completed/failed/aborted
    cost: Mapped[float | None] = mapped_column(Float)  # Calculated cost for this usage event
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
