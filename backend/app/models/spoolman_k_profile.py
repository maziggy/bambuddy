from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class SpoolmanKProfile(Base):
    """K-value calibration profile for a Spoolman spool on a specific printer/nozzle combo."""

    __tablename__ = "spoolman_k_profile"

    __table_args__ = (
        UniqueConstraint("spoolman_spool_id", "printer_id", "extruder", "nozzle_diameter"),
        CheckConstraint("extruder >= 0 AND extruder <= 1", name="ck_extruder_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    spoolman_spool_id: Mapped[int] = mapped_column(Integer, nullable=False)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"))
    extruder: Mapped[int] = mapped_column(Integer, default=0)
    nozzle_diameter: Mapped[str] = mapped_column(String(10), default="0.4")
    nozzle_type: Mapped[str | None] = mapped_column(String(50))
    k_value: Mapped[float] = mapped_column(Float)
    name: Mapped[str | None] = mapped_column(String(100))
    cali_idx: Mapped[int | None] = mapped_column(Integer)
    setting_id: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    printer: Mapped["Printer"] = relationship()


from backend.app.models.printer import Printer  # noqa: E402, F401
