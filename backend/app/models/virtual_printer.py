from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base

# Canonical VP mode values. The legacy values `immediate` (→ archive) and
# `print_queue` (→ queue) shipped before the UI labels were aligned with the
# wire format. `normalize_vp_mode()` translates input from either form and
# the DB migration in `core/database.py` rewrites existing rows once at boot.
VP_MODE_ARCHIVE = "archive"
VP_MODE_REVIEW = "review"
VP_MODE_QUEUE = "queue"
VP_MODE_PROXY = "proxy"
VP_MODE_VALUES = (VP_MODE_ARCHIVE, VP_MODE_REVIEW, VP_MODE_QUEUE, VP_MODE_PROXY)

# Legacy → canonical map. Kept narrow on purpose so unrelated typos surface
# instead of getting silently re-pointed at a default.
_VP_MODE_ALIASES = {
    "immediate": VP_MODE_ARCHIVE,
    "print_queue": VP_MODE_QUEUE,
}


def normalize_vp_mode(value: str | None) -> str | None:
    """Map legacy wire values (`immediate`, `print_queue`) to canonical names.

    Returns `None` unchanged so callers can decide whether to apply a default.
    Returns unknown values unchanged so validators still see them and reject.
    """
    if value is None:
        return None
    return _VP_MODE_ALIASES.get(value, value)


class VirtualPrinter(Base):
    """Virtual printer configuration for multi-instance support."""

    __tablename__ = "virtual_printers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), default="Bambuddy")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mode: Mapped[str] = mapped_column(String(20), default=VP_MODE_ARCHIVE)  # archive|review|queue|proxy
    auto_dispatch: Mapped[bool] = mapped_column(Boolean, server_default="true")  # queue mode: auto-start or manual
    queue_force_color_match: Mapped[bool] = mapped_column(
        Boolean, server_default="false"
    )  # queue mode: pin per-slot type+color from the 3MF onto the queue
    # item so the scheduler refuses to dispatch onto a printer with the wrong
    # filament loaded (#1188).
    model: Mapped[str | None] = mapped_column(String(50), nullable=True)  # SSDP model code (server mode)
    access_code: Mapped[str | None] = mapped_column(String(8), nullable=True)  # 8 chars (server mode)
    target_printer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("printers.id", ondelete="SET NULL"), nullable=True
    )  # proxy mode
    bind_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)  # dedicated IP (proxy mode)
    remote_interface_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)  # SSDP advertise IP
    tailscale_disabled: Mapped[bool] = mapped_column(
        Boolean, server_default="true"
    )  # opt-in: user must explicitly enable; auto-detect only runs then
    serial_suffix: Mapped[str] = mapped_column(String(9), default="391800001")  # unique per printer
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
