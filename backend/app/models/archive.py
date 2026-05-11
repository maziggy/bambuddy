from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class PrintArchive(Base):
    __tablename__ = "print_archives"

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int | None] = mapped_column(ForeignKey("printers.id"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)

    # File info
    filename: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(500))
    file_size: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str | None] = mapped_column(String(64))  # SHA256 hash for duplicate detection
    thumbnail_path: Mapped[str | None] = mapped_column(String(500))
    timelapse_path: Mapped[str | None] = mapped_column(String(500))
    source_3mf_path: Mapped[str | None] = mapped_column(String(500))  # Original project 3MF from slicer
    f3d_path: Mapped[str | None] = mapped_column(String(500))  # Fusion 360 design file

    # Print details from 3MF / printer
    print_name: Mapped[str | None] = mapped_column(String(255))
    print_time_seconds: Mapped[int | None] = mapped_column(Integer)
    filament_used_grams: Mapped[float | None] = mapped_column(Float)
    filament_type: Mapped[str | None] = mapped_column(String(50))
    filament_color: Mapped[str | None] = mapped_column(String(200))
    layer_height: Mapped[float | None] = mapped_column(Float)
    total_layers: Mapped[int | None] = mapped_column(Integer)
    nozzle_diameter: Mapped[float | None] = mapped_column(Float)
    bed_temperature: Mapped[int | None] = mapped_column(Integer)
    bed_type: Mapped[str | None] = mapped_column(String(64))  # e.g. "Cool Plate", "Textured PEI Plate"
    nozzle_temperature: Mapped[int | None] = mapped_column(Integer)

    # Printer model this file was sliced for (extracted from 3MF metadata)
    sliced_for_model: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Print result
    status: Mapped[str] = mapped_column(String(20), default="completed")
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Printer-assigned subtask identifier from MQTT. Used to resume the same
    # archive row across a backend restart during a long-running print (#972):
    # if the same subtask_id reappears after restart, we know it's the same
    # print and keep the original row instead of cancel-then-create.
    subtask_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Extended metadata (JSON blob for flexibility)
    extra_data: Mapped[dict | None] = mapped_column(JSON)

    # MakerWorld info (auto-extracted from 3MF)
    makerworld_url: Mapped[str | None] = mapped_column(String(500))
    designer: Mapped[str | None] = mapped_column(String(255))

    # User-defined external link (Printables, Thingiverse, etc.)
    external_url: Mapped[str | None] = mapped_column(String(500))

    # User additions
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    cost: Mapped[float | None] = mapped_column(Float)
    photos: Mapped[list | None] = mapped_column(JSON)  # List of photo filenames
    failure_reason: Mapped[str | None] = mapped_column(String(100))  # For failed prints
    quantity: Mapped[int] = mapped_column(Integer, default=1)  # Number of items printed

    # Energy tracking
    energy_kwh: Mapped[float | None] = mapped_column(Float)  # Energy consumed in kWh
    energy_cost: Mapped[float | None] = mapped_column(Float)  # Cost of energy consumed
    # Plug lifetime counter captured at print start; delta at print end becomes energy_kwh.
    # Persisted so per-print tracking survives backend restarts mid-print (#941).
    energy_start_kwh: Mapped[float | None] = mapped_column(Float)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # User tracking (who uploaded/created this archive)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    printer: Mapped["Printer | None"] = relationship(back_populates="archives")
    project: Mapped["Project | None"] = relationship(back_populates="archives")
    created_by: Mapped["User | None"] = relationship()


from backend.app.models.printer import Printer  # noqa: E402, F811
from backend.app.models.project import Project  # noqa: E402, F811
from backend.app.models.user import User  # noqa: E402, F811
