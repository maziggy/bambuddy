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
    # Target printer model for model-based assignment (mutually exclusive with printer_id)
    # When set, scheduler assigns to any idle printer of matching model
    target_model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Target location filter for model-based assignment (only used with target_model)
    # When set, only printers in this location are considered
    target_location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Required filament types for model-based assignment (JSON array, e.g., '["PLA", "PETG"]')
    # Used by scheduler to validate printer has compatible filaments loaded
    required_filament_types: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Waiting reason - explains why a model-based job hasn't started yet
    # Set by scheduler when no matching printer is available
    waiting_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Either archive_id OR library_file_id must be set (archive created at print start from library file)
    archive_id: Mapped[int | None] = mapped_column(ForeignKey("print_archives.id", ondelete="CASCADE"), nullable=True)
    library_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("library_files.id", ondelete="CASCADE"), nullable=True
    )
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

    # Filament overrides for model-based assignment: JSON array of override objects
    # Format: '[{"slot_id": 1, "type": "PLA", "color": "#FFFFFF"}]'
    # Only slots with overrides are included (sparse). null = use original 3MF values.
    filament_overrides: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    # User tracking (who added this to the queue)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    printer: Mapped["Printer"] = relationship()
    archive: Mapped["PrintArchive | None"] = relationship()
    library_file: Mapped["LibraryFile | None"] = relationship()
    project: Mapped["Project | None"] = relationship(back_populates="queue_items")
    created_by: Mapped["User | None"] = relationship()


from backend.app.models.archive import PrintArchive  # noqa: E402
from backend.app.models.library import LibraryFile  # noqa: E402
from backend.app.models.printer import Printer  # noqa: E402
from backend.app.models.project import Project  # noqa: E402
from backend.app.models.user import User  # noqa: E402
