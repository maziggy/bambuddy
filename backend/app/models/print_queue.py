from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class PrintQueueItem(Base):
    """Print queue item for scheduled/queued prints."""

    __tablename__ = "print_queue"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Links
    printer_id: Mapped[int | None] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"), nullable=True)
    archive_id: Mapped[int] = mapped_column(ForeignKey("print_archives.id", ondelete="CASCADE"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)

    # Scheduling
    position: Mapped[int] = mapped_column(Integer, default=0)  # Queue order
    scheduled_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # None = ASAP
    manual_start: Mapped[bool] = mapped_column(Boolean, default=False)  # Requires manual trigger to start

    # Conditions
    require_previous_success: Mapped[bool] = mapped_column(Boolean, default=False)

    # Power management
    auto_off_after: Mapped[bool] = mapped_column(Boolean, default=False)  # Power off printer after print

    # AMS mapping: JSON array of global tray IDs for each filament slot
    # Format: "[5, -1, 2, -1]" where position = slot_id-1, value = global tray ID (-1 = unused)
    ams_mapping: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Plate ID for multi-plate 3MF files (1-indexed, None = auto-detect/plate 1)
    plate_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Print options
    bed_levelling: Mapped[bool] = mapped_column(Boolean, default=True)
    flow_cali: Mapped[bool] = mapped_column(Boolean, default=False)
    vibration_cali: Mapped[bool] = mapped_column(Boolean, default=True)
    layer_inspect: Mapped[bool] = mapped_column(Boolean, default=False)
    timelapse: Mapped[bool] = mapped_column(Boolean, default=False)
    use_ams: Mapped[bool] = mapped_column(Boolean, default=True)

    # Status: pending, printing, completed, failed, skipped, cancelled
    status: Mapped[str] = mapped_column(String(20), default="pending")

    # Tracking
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationships
    printer: Mapped["Printer"] = relationship()
    archive: Mapped["PrintArchive"] = relationship()
    project: Mapped["Project | None"] = relationship(back_populates="queue_items")


from backend.app.models.archive import PrintArchive  # noqa: E402
from backend.app.models.printer import Printer  # noqa: E402
from backend.app.models.project import Project  # noqa: E402
