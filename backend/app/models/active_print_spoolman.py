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
    # Nullable for the no-3MF case ("Untitled" prints where Bambu didn't keep a
    # .gcode.3mf on the printer): the row still gets created so the completion
    # path can use ``tray_remain_start`` for an AMS remain%-delta write,
    # mirroring the internal-inventory Path 2 fallback in usage_tracker (#1820).
    filament_usage: Mapped[list | None] = mapped_column(JSON, nullable=True)

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

    # AMS tray remain% per slot at print start, captured so the completion
    # path can compute a remain-delta when the 3MF didn't cover a slot (or
    # there was no 3MF at all — #1820). Matches the internal-inventory
    # ``tray_remain_start`` snapshot at usage_tracker.py:301.
    # Format: {"<ams_id>-<tray_id>": {"remain": int, "tray_uuid": str}, ...}
    tray_remain_start: Mapped[dict | None] = mapped_column(JSON, nullable=True)
