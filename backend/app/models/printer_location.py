from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class PrinterLocation(Base):
    """A printer location (group) and how it looks (#2962).

    A printer's location is still the free-text ``printers.location`` column,
    which the scheduler's model-based targeting and the printers filter match
    exactly. This table only adds what that column cannot hold: a location with
    no printers yet (a rack or room planned before the printers move in), and
    the icon and colour the Printer Locations page shows for it. A location used
    by printers but missing here is still a location, with no icon or colour.
    """

    __tablename__ = "printer_locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Same width as printers.location, which holds the same value.
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    # An IconPicker name ("home", "wrench"); None shows the default icon.
    icon: Mapped[str | None] = mapped_column(String(50))
    # "#rrggbb"; None means no colour.
    color: Mapped[str | None] = mapped_column(String(7))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
