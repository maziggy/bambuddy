"""Model for storing user-defined friendly names for AMS units.

Users can assign a custom label to each AMS (e.g. "Workshop AMS", "Silk Colours")
that is displayed in place of or alongside the auto-generated label (AMS-A, HT-A, …).

Labels are keyed by AMS serial number so they persist when the AMS is moved to a
different printer.  A fallback (printer_id + ams_id) is retained for units whose
serial number is not yet known.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class AmsLabel(Base):
    """Maps an AMS unit serial number to a user-defined friendly name."""

    __tablename__ = "ams_labels"
    __table_args__ = (UniqueConstraint("ams_serial_number", name="uq_ams_label_serial"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ams_serial_number: Mapped[str] = mapped_column(String(50))  # AMS unit serial number (sn from MQTT)
    ams_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # AMS unit ID hint (0, 1, 2, 3, 128…)
    label: Mapped[str] = mapped_column(String(100))  # User-defined friendly name
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
