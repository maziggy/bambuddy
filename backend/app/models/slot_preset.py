"""Model for storing AMS slot to filament preset mappings.

This stores the user's preferred filament preset for each AMS slot,
similar to how Bambu Studio remembers preset selections.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class SlotPresetMapping(Base):
    """Maps an AMS slot to a cloud filament preset."""

    __tablename__ = "slot_preset_mappings"
    __table_args__ = (UniqueConstraint("printer_id", "ams_id", "tray_id", name="uq_slot_preset"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"))
    ams_id: Mapped[int] = mapped_column(Integer)  # AMS unit ID (0, 1, 2, 3)
    tray_id: Mapped[int] = mapped_column(Integer)  # Tray ID within AMS (0-3)
    preset_id: Mapped[str] = mapped_column(String(100))  # Cloud preset setting_id
    preset_name: Mapped[str] = mapped_column(String(200))  # Preset name for display
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationship
    printer: Mapped["Printer"] = relationship()


from backend.app.models.printer import Printer  # noqa: E402
