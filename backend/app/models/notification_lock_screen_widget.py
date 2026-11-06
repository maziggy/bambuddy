"""Durable ownership of one persistent Notify! widget per printer/provider."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class NotificationLockScreenWidget(Base):
    __tablename__ = "notification_lock_screen_widgets"
    __table_args__ = (UniqueConstraint("provider_id", "printer_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("notification_providers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # A removed printer must leave ownership intact until the remote DELETE succeeds.
    printer_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    credential_key: Mapped[str] = mapped_column(String(64), nullable=False)
    widget_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
