from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class SmartPlugEnergySnapshot(Base):
    """Hourly snapshot of a smart plug's lifetime energy counter.

    Powers date-range queries in "total consumption" energy mode. For a given
    range we sum `(last_snapshot_in_range - last_snapshot_before_range)` per plug.
    """

    __tablename__ = "smart_plug_energy_snapshots"
    __table_args__ = (Index("ix_plug_energy_snapshots_plug_time", "plug_id", "recorded_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plug_id: Mapped[int] = mapped_column(ForeignKey("smart_plugs.id", ondelete="CASCADE"), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    lifetime_kwh: Mapped[float] = mapped_column(Float, nullable=False)
