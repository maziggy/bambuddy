"""Model for pending spool-to-slot assignment requests.

A pending assignment represents a request to assign a spool to the next AMS slot
that transitions from empty → filled. The backend monitors AMS events and completes
the assignment automatically when a matching slot change is detected.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class PendingSlotAssignment(Base):
    """A pending request to assign a spool to the next available AMS slot."""

    __tablename__ = "pending_slot_assignment"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Spool identifiers — at least one must be non-null
    tray_uuid: Mapped[str | None] = mapped_column(String(64), index=True)
    tag_uid: Mapped[str | None] = mapped_column(String(32), index=True)
    # Resolved spool_id once matched (nullable until resolved)
    spool_id: Mapped[int | None] = mapped_column(Integer)
    # Target printer (NULL = any printer)
    printer_id: Mapped[int | None] = mapped_column(Integer)
    # Source of the request
    source: Mapped[str] = mapped_column(String(20))  # "nfc" | "qr" | "spoolbuddy"
    # Status: pending, completed, timed_out, cancelled
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # Timeout in seconds
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300)

    # Completion details (filled when status transitions to completed)
    assigned_printer_id: Mapped[int | None] = mapped_column(Integer)
    assigned_ams_id: Mapped[int | None] = mapped_column(Integer)
    assigned_tray_id: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Metrics
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    time_to_placement: Mapped[float | None] = mapped_column(Float)  # seconds

    @property
    def lookup_key(self) -> str:
        """Return the primary identifier used for spool lookup (tray_uuid preferred)."""
        return self.tray_uuid or self.tag_uid or ""

    @property
    def is_expired(self) -> bool:
        """Check if this pending assignment has timed out."""
        if self.status != "pending":
            return False
        elapsed = (datetime.now(timezone.utc) - self.created_at.replace(tzinfo=timezone.utc)).total_seconds()
        return elapsed > self.timeout_seconds
