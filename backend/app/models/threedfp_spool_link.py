"""A spool's link to a filament on 3D Filament Profiles (#2981).

One table for both inventories, as each spool links to at most one 3DFP
filament: ``spool_id`` for a built-in spool, ``spoolman_spool_id`` for a
Spoolman one. Spoolman owns its spools but not this link, which is why it lives
here rather than in a Spoolman extra field: those would appear, editable, in
Spoolman's own UI and in every other client of it.

The 3DFP spool sync adds the spool's ``short_code`` and the last synced values
here; the filament link is all the lookup and the Reorder button need.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class ThreeDfpSpoolLink(Base):
    __tablename__ = "threedfp_spool_links"
    __table_args__ = (
        CheckConstraint(
            "(spool_id IS NULL) <> (spoolman_spool_id IS NULL)",
            name="ck_threedfp_spool_links_one_inventory",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    spool_id: Mapped[int | None] = mapped_column(
        ForeignKey("spool.id", ondelete="CASCADE"), unique=True, index=True, nullable=True
    )
    spoolman_spool_id: Mapped[int | None] = mapped_column(Integer, unique=True, index=True, nullable=True)
    # The 3DFP filament id, as 3DFP last returned it: ids change when two
    # filaments are merged there, and the response carries the surviving one.
    filament_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
