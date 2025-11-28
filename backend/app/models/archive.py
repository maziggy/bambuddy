from datetime import datetime
from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, Text, JSON, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class PrintArchive(Base):
    __tablename__ = "print_archives"

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int | None] = mapped_column(ForeignKey("printers.id"), nullable=True)

    # File info
    filename: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(500))
    file_size: Mapped[int] = mapped_column(Integer)
    thumbnail_path: Mapped[str | None] = mapped_column(String(500))
    timelapse_path: Mapped[str | None] = mapped_column(String(500))

    # Print details from 3MF / printer
    print_name: Mapped[str | None] = mapped_column(String(255))
    print_time_seconds: Mapped[int | None] = mapped_column(Integer)
    filament_used_grams: Mapped[float | None] = mapped_column(Float)
    filament_type: Mapped[str | None] = mapped_column(String(50))
    filament_color: Mapped[str | None] = mapped_column(String(50))
    layer_height: Mapped[float | None] = mapped_column(Float)
    nozzle_diameter: Mapped[float | None] = mapped_column(Float)
    bed_temperature: Mapped[int | None] = mapped_column(Integer)
    nozzle_temperature: Mapped[int | None] = mapped_column(Integer)

    # Print result
    status: Mapped[str] = mapped_column(String(20), default="completed")
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Extended metadata (JSON blob for flexibility)
    extra_data: Mapped[dict | None] = mapped_column(JSON)

    # MakerWorld info
    makerworld_url: Mapped[str | None] = mapped_column(String(500))
    designer: Mapped[str | None] = mapped_column(String(255))

    # User additions
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    cost: Mapped[float | None] = mapped_column(Float)
    photos: Mapped[list | None] = mapped_column(JSON)  # List of photo filenames
    failure_reason: Mapped[str | None] = mapped_column(String(100))  # For failed prints

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    # Relationships
    printer: Mapped["Printer | None"] = relationship(back_populates="archives")


from backend.app.models.printer import Printer  # noqa: E402, F811
