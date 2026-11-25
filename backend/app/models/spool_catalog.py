from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, false, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class SpoolCatalogEntry(Base):
    """Spool weight catalog entry for weight lookup when adding spools."""

    __tablename__ = "spool_catalog"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    weight: Mapped[int] = mapped_column(Integer)
    # A seeded row (Reset puts these back), not the brand default below.
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    # The brand whose rolls ship on this spool (#3727). Seeded rows take it from
    # the name; rows users add may leave it empty.
    manufacturer: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # The spool this brand's rolls come on, used as the empty-spool weight when a
    # roll of that brand is added. At most one per manufacturer; the API clears
    # the others when one is set.
    default_for_brand: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
