from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class ExternalLink(Base):
    """External links for sidebar navigation."""

    __tablename__ = "external_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    url: Mapped[str] = mapped_column(String(500))
    icon: Mapped[str] = mapped_column(String(50), default="link")
    custom_icon: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Filename of uploaded icon
    open_in_new_tab: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ExternalLinkBadge(Base):
    """A count on an external sidebar link, set by the app the link points to
    (Bambuddy Orders' To do, say). Kept apart from the link so that setting it
    every minute leaves the link's own ``updated_at`` alone."""

    __tablename__ = "external_link_badges"

    link_id: Mapped[int] = mapped_column(ForeignKey("external_links.id", ondelete="CASCADE"), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    # When the app last reported; an old count isn't shown.
    updated_at: Mapped[datetime] = mapped_column(DateTime)
