"""Track Spoolman data for active prints."""

from sqlalchemy import JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class ActivePrintSpoolman(Base):
    """Stores Spoolman tracking data for active prints.

    This data is captured at print start and used at print completion
    to report per-filament usage to the correct Spoolman spools.
    Rows are deleted after print completes.

    Key: (printer_id, archive_id) - allows same archive on different printers
    """

    __tablename__ = "active_print_spoolman"
    __table_args__ = (UniqueConstraint("printer_id", "archive_id", name="uq_printer_archive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"))
    archive_id: Mapped[int] = mapped_column(ForeignKey("print_archives.id", ondelete="CASCADE"))

    # Per-filament usage from 3MF: [{"slot_id": 1, "used_g": 50.5, "type": "PLA"}, ...]
    filament_usage: Mapped[list] = mapped_column(JSON)

    # AMS tray state at print start: {0: {"tray_uuid": "...", "tag_uid": "..."}, ...}
    ams_trays: Mapped[dict] = mapped_column(JSON)

    # Custom slot-to-tray mapping from queue (optional): [5, -1, 2, -1]
    slot_to_tray: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Per-layer cumulative usage from G-code parsing (for accurate partial usage)
    # Format: {"0": {0: 125.5}, "1": {0: 250.0, 1: 50.0}, ...}
    # Keys are layer numbers (as strings for JSON), values are filament_id -> mm
    layer_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Filament properties (density, diameter per filament slot)
    # Format: {1: {"density": 1.24, "diameter": 1.75, "type": "PLA"}, ...}
    filament_properties: Mapped[dict | None] = mapped_column(JSON, nullable=True)
