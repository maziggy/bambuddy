from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class SpoolKProfile(Base):
    """K-value calibration profile for a spool on a specific printer/nozzle combo."""

    __tablename__ = "spool_k_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    spool_id: Mapped[int] = mapped_column(ForeignKey("spool.id", ondelete="CASCADE"))
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"))
    extruder: Mapped[int] = mapped_column(Integer, default=0)  # 0 or 1 (H2D)
    nozzle_diameter: Mapped[str] = mapped_column(String(10), default="0.4")  # "0.4", "0.6"
    nozzle_type: Mapped[str | None] = mapped_column(String(50))
    k_value: Mapped[float] = mapped_column(Float)  # e.g. 0.020
    name: Mapped[str | None] = mapped_column(String(100))  # Profile display name
    cali_idx: Mapped[int | None] = mapped_column(Integer)  # Calibration index on printer
    setting_id: Mapped[str | None] = mapped_column(String(50))  # Full setting ID
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    spool: Mapped["Spool"] = relationship(back_populates="k_profiles")
    printer: Mapped["Printer"] = relationship()


from backend.app.models.printer import Printer  # noqa: E402, F401
from backend.app.models.spool import Spool  # noqa: E402, F401
