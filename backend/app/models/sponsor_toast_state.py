"""Per-user (or install-default) state for the sponsor-prompt toast.

A single row stores which sponsor-toast milestones have already fired for a
given user, when the most recent toast was shown (for the 14-day cooldown),
and the app version last seen so we can fire the "version-update" trigger
exactly once per major bump.

``user_id`` is nullable: in auth-disabled installs (no user concept), the
service stores everything against a single NULL-keyed row.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class SponsorToastState(Base):
    __tablename__ = "sponsor_toast_state"
    __table_args__ = (UniqueConstraint("user_id", name="uq_sponsor_toast_state_user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    last_shown_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # JSON-serialised list[str] of milestone keys already fired (e.g. ["prints-100", "cost-100"]).
    # Stored as Text for SQLite/Postgres uniformity; the service serialises with json.dumps.
    milestones_seen: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    last_seen_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
