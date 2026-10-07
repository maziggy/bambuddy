"""Durable ownership of one persistent Notify! widget per printer/provider."""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint

from backend.app.core.database import Base


class NotificationLockScreenWidget(Base):
    __tablename__ = "notification_lock_screen_widgets"
    __table_args__ = (UniqueConstraint("provider_id", "printer_id"),)

    id = Column(Integer, primary_key=True)
    provider_id = Column(
        Integer, ForeignKey("notification_providers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # A removed printer must leave ownership intact until the remote DELETE succeeds.
    printer_id = Column(Integer, nullable=False, index=True)
    credential_key = Column(String(64), nullable=False)
    widget_id = Column(String(16), nullable=True)
    state = Column(String(20), nullable=False, default="pending")
    content = Column(Text, nullable=False, default="{}")
    failures = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime, nullable=True)
    last_sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
