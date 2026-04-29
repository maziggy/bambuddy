from datetime import datetime

from sqlalchemy import DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class SpoolmanSpoolWeightOverride(Base):
    """Per-spool tare weight override for Spoolman spools.

    Written when a user edits spool_weight with Option A (keep existing spools):
    the old weight is preserved here so weight calculations use the override
    instead of the live Spoolman filament value.
    """

    __tablename__ = "spoolman_spool_weight_override"

    spoolman_spool_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    core_weight: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
