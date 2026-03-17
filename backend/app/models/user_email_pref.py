"""User email notification preference model."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class UserEmailPreference(Base):
    """Stores per-user email notification preferences for their own print jobs."""

    __tablename__ = "user_email_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )

    # Print lifecycle notifications (only for jobs submitted by this user)
    notify_print_start: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_print_complete: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_print_failed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_print_stopped: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationship
    user: Mapped["User"] = relationship(back_populates="email_preferences")
