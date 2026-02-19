from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class VirtualPrinter(Base):
    """Virtual printer configuration for multi-instance support."""

    __tablename__ = "virtual_printers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), default="Bambuddy")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mode: Mapped[str] = mapped_column(String(20), default="immediate")  # immediate|review|print_queue|proxy
    model: Mapped[str | None] = mapped_column(String(50), nullable=True)  # SSDP model code (server mode)
    access_code: Mapped[str | None] = mapped_column(String(8), nullable=True)  # 8 chars (server mode)
    target_printer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("printers.id", ondelete="SET NULL"), nullable=True
    )  # proxy mode
    bind_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)  # dedicated IP (proxy mode)
    remote_interface_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)  # SSDP advertise IP
    serial_suffix: Mapped[str] = mapped_column(String(9), default="391800001")  # unique per printer
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
