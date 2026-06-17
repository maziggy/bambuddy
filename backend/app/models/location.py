from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base

if TYPE_CHECKING:
    from backend.app.models.spool import Spool


class Location(Base):
    """Physical storage location for filament spools (shelf, drawer, drybox, etc.)."""

    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # Case-insensitive uniqueness — LOWER(TRIM(name)); enforced via migration index.
    name_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    # Reserved for Phase 3 RFID shelf tags — unused in Phase 1.
    identifier: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    spools: Mapped[list["Spool"]] = relationship(back_populates="location")
