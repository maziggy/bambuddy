from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class PrintLogEntry(Base):
    """Independent print log entry. Written when print events occur.

    This is a separate table from archives/queue — clearing the log
    never touches archives or queue items.
    """

    __tablename__ = "print_log_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    print_name: Mapped[str | None] = mapped_column(String(255))
    printer_name: Mapped[str | None] = mapped_column(String(255))
    printer_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))  # completed, failed, stopped, cancelled, skipped
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    filament_type: Mapped[str | None] = mapped_column(String(50))
    filament_color: Mapped[str | None] = mapped_column(String(50))
    filament_used_grams: Mapped[float | None] = mapped_column(Float)
    thumbnail_path: Mapped[str | None] = mapped_column(String(500))
    created_by_username: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
