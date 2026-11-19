from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base

# Bit 0 is Monday, bit 6 is Sunday — the same numbering as ``date.weekday()``.
ALL_DAYS_MASK = 0b1111111


class PrinterReservation(Base):
    """A recurring weekly window during which printers are reserved (#2785).

    Not a print job: it only says "keep these printers free for this owner,
    on these weekdays, between these times". The queue shows it on the
    timeline, and a virtual printer with ``respect_reservations`` on holds an
    auto-dispatched job for manual start instead of starting it on a reserved
    printer or letting it run into a reserved window.

    Times are wall-clock minutes in the server's local zone (``TZ``), because
    "15:00–05:00 every weekday" is a statement about the clock on the wall and
    must not shift by an hour when DST changes. ``end_minute <= start_minute``
    means the window runs past midnight into the next day; it still belongs to
    the weekday it starts on.
    """

    __tablename__ = "printer_reservations"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Free text shown on the timeline, e.g. a project name.
    label: Mapped[str] = mapped_column(String(100), default="")
    # JSON list of printer ids. A reservation covering "both X1Cs" is one row
    # the user edits as one thing; ids of deleted printers are dropped on read.
    printer_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    days_mask: Mapped[int] = mapped_column(Integer, default=ALL_DAYS_MASK)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    # Minutes after local midnight, 0..1439. Ignored when all_day is set.
    start_minute: Mapped[int] = mapped_column(Integer, default=0)
    end_minute: Mapped[int] = mapped_column(Integer, default=0)
    # Optional local-date bounds (inclusive) on the weekdays it starts on.
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    created_by: Mapped["User | None"] = relationship()


from backend.app.models.user import User  # noqa: E402
