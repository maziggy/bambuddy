from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class ScheduledDrying(Base):
    """A manual AMS drying run scheduled to start later (#2638).

    Dispatched by PrintScheduler._check_scheduled_dryings() when start_after
    has passed and the printer is idle. Parameters mirror the immediate
    POST /printers/{id}/drying/start endpoint.
    """

    __tablename__ = "scheduled_dryings"

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"))
    ams_id: Mapped[int] = mapped_column(Integer, default=0)

    temp: Mapped[int] = mapped_column(Integer)
    duration_hours: Mapped[int] = mapped_column(Integer)
    filament: Mapped[str] = mapped_column(String(50), default="")
    rotate_tray: Mapped[bool] = mapped_column(Boolean, default=False)

    # Earliest start instant, naive UTC (same convention as
    # print_queue.scheduled_time). None = start as soon as the printer is idle.
    start_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # pending / running / completed / cancelled / failed
    status: Mapped[str] = mapped_column(String(20), default="pending")
    waiting_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    printer: Mapped["Printer"] = relationship()
    created_by: Mapped["User | None"] = relationship()


from backend.app.models.printer import Printer  # noqa: E402
from backend.app.models.user import User  # noqa: E402
