"""Durable ownership of Notify! tiles, including per-print dismissal tombstones."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class NotificationLiveActivity(Base):
    __tablename__ = "notification_live_activities"
    __table_args__ = (UniqueConstraint("provider_id", "printer_id", "print_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("notification_providers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Keep ownership after printer deletion until the worker ends the remote tile.
    printer_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    print_key: Mapped[str] = mapped_column(String(200), nullable=False)
    job_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    print_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    eta_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eta_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    credential_key: Mapped[str] = mapped_column(String(64), nullable=False)
    activity_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    end_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
