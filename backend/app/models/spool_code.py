from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class SpoolCode(Base):
    """A single code (GTIN barcode or manufacturer SKU/article number) associated with a spool.

    A spool can have several: the code actually scanned/typed (``is_primary``),
    plus siblings discovered by cross-referencing the Open Filament Database
    and SpoolmanDB-Community (other package-size GTINs, the refill-pack GTIN,
    the manufacturer SKU/article number) — see ``_resolve_barcode`` in
    ``backend/app/api/routes/inventory.py``. ``Spool.barcode`` itself stays the
    single denormalized "primary code" column so existing CSV/table/form
    behavior is untouched; this table is the additive one-to-many store.
    """

    __tablename__ = "spool_code"

    id: Mapped[int] = mapped_column(primary_key=True)
    spool_id: Mapped[int] = mapped_column(ForeignKey("spool.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # "gtin" | "sku"
    is_refill: Mapped[bool] = mapped_column(Boolean, default=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    spool: Mapped["Spool"] = relationship(back_populates="codes")

    __table_args__ = (UniqueConstraint("spool_id", "code", name="uq_spool_code_spool_id_code"),)


from backend.app.models.spool import Spool  # noqa: E402, F401
