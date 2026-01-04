from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
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
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
