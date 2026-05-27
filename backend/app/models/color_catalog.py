from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class ColorCatalogEntry(Base):
    """Color catalog entry for automatic color lookup when adding spools."""

    __tablename__ = "color_catalog"

    id: Mapped[int] = mapped_column(primary_key=True)
    manufacturer: Mapped[str] = mapped_column(String(200))
    color_name: Mapped[str] = mapped_column(String(200))
    hex_color: Mapped[str] = mapped_column(String(9))  # #RRGGBB or #RRGGBBAA
    material: Mapped[str | None] = mapped_column(String(100))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    # Optional multi-colour stops + visual effect (#1154), mirrors Spool fields.
    extra_colors: Mapped[str | None] = mapped_column(String(255))
    effect_type: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
