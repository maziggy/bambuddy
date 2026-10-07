"""Durable ownership of Notify! tiles, including per-print dismissal tombstones."""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint

from backend.app.core.database import Base


class NotificationLiveActivity(Base):
    __tablename__ = "notification_live_activities"
    __table_args__ = (UniqueConstraint("provider_id", "printer_id", "print_key"),)

    id = Column(Integer, primary_key=True)
    provider_id = Column(
        Integer, ForeignKey("notification_providers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Keep ownership after printer deletion until the worker ends the remote tile.
    printer_id = Column(Integer, nullable=False, index=True)
    print_key = Column(String(200), nullable=False)
    job_key = Column(String(160), nullable=True)
    print_name = Column(String(255), nullable=False, default="")
    eta_seconds = Column(Integer, nullable=True)
    eta_deadline = Column(DateTime, nullable=True)
    credential_key = Column(String(64), nullable=False)
    activity_id = Column(String(16), nullable=True)
    state = Column(String(20), nullable=False, default="pending")
    end_reason = Column(String(40), nullable=True)
    failures = Column(Integer, nullable=False, default=0)
    content = Column(Text, nullable=False, default="{}")
    expires_at = Column(DateTime, nullable=True)
    next_attempt_at = Column(DateTime, nullable=True)
    last_sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
