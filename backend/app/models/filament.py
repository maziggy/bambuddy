from datetime import datetime
from sqlalchemy import String, Float, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class Filament(Base):
    """Filament type with cost information."""

    __tablename__ = "filaments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(50))  # PLA, PETG, ABS, etc.
    brand: Mapped[str | None] = mapped_column(String(100))
    color: Mapped[str | None] = mapped_column(String(50))
    color_hex: Mapped[str | None] = mapped_column(String(7))  # #RRGGBB

    # Cost information
    cost_per_kg: Mapped[float] = mapped_column(Float, default=25.0)
    spool_weight_g: Mapped[float] = mapped_column(Float, default=1000.0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # Properties
    density: Mapped[float | None] = mapped_column(Float)  # g/cm³
    print_temp_min: Mapped[int | None] = mapped_column()
    print_temp_max: Mapped[int | None] = mapped_column()
    bed_temp_min: Mapped[int | None] = mapped_column()
    bed_temp_max: Mapped[int | None] = mapped_column()

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
