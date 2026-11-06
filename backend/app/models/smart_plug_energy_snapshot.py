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
    # The electricity price when the snapshot was taken (#1251). The energy used
    # until the next snapshot is costed at it, so a price that changes during the
    # day is applied hour by hour instead of to all of history at once.
    price_per_kwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Running totals of the energy counted and what it cost, up to this row
    # (#1251). The cost of any span is the difference of two rows, so the
    # Statistics never have to walk the whole history.
    kwh_to_date: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_to_date: Mapped[float | None] = mapped_column(Float, nullable=True)
