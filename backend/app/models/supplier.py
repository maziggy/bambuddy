from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from backend.app.core.database import Base

if TYPE_CHECKING:
    from backend.app.models.spool import Spool


def supplier_name_key(name: str) -> str:
    """Case-insensitive lookup key stored on ``Supplier.name_key``.

    Folded in Python, not in SQL: SQLite's ``lower()`` folds ASCII only, so a
    unique index on ``lower(name)`` lets “Ökofilament” and “ökofilament” both
    through — and the CSV import, which folds in Python, then collapses them
    onto one map entry and resolves to whichever row it built last. Every
    caller goes through this one function so the two folds cannot drift.
    """
    return name.strip().lower()


class Supplier(Base):
    """Managed supplier master list (#2988).

    A supplier is *where filament is bought* — distinct from ``Spool.brand``,
    which is who made it. One supplier carries many brands, and the same
    product is available from several suppliers, hence the n:m assignment
    below instead of a free-text field that drifts in spelling.
    """

    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    # Case-insensitive uniqueness -- the whole feature keys on the name. The
    # CSV import resolves a column of names against this table and the master
    # list is what a rename re-points, so "Extrudr" and "extrudr" must not be
    # two rows. Stored key rather than a unique index on lower(name), for the
    # reason in supplier_name_key: only the Python fold is Unicode-correct on
    # every backend, and it is the same fold the import map uses.
    name_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    # Shop / website URL, purely informational.
    website: Mapped[str | None] = mapped_column(String(500))
    # The business's own customer number AT this supplier.
    customer_number: Mapped[str | None] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    spool_links: Mapped[list[SpoolSupplier]] = relationship(back_populates="supplier")

    @validates("name")
    def _sync_name_key(self, _key: str, value: str) -> str:
        """Derive name_key from every name write, so no insert path can skip it."""
        self.name_key = supplier_name_key(value)
        return value


class SpoolSupplier(Base):
    """Spool-to-supplier assignment with per-assignment attributes (#2988).

    Modelled after ``CostCenterMember`` (surrogate id + UNIQUE pair) rather
    than a bare association table, because the assignment carries data: the
    supplier's own article number for the product (NOT the internal material
    number from #2870), the price at this supplier, and whether this concrete
    spool was actually bought there — the other rows are alternative sources.
    """

    __tablename__ = "spool_suppliers"
    __table_args__ = (UniqueConstraint("spool_id", "supplier_id", name="uq_spool_suppliers_spool_supplier"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    spool_id: Mapped[int] = mapped_column(ForeignKey("spool.id", ondelete="CASCADE"), index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    # The supplier's article number for this product.
    supplier_article_number: Mapped[str | None] = mapped_column(String(100))
    # QUOTED price per kg at this supplier, for comparing sources. Named so it
    # can never be read as actual cost: ``spool.cost_per_kg`` is the cost basis
    # for every print and is never written from here.
    quoted_price_per_kg: Mapped[float | None] = mapped_column(Float)
    # True on the assignment this spool was actually purchased from.
    is_purchase_source: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    supplier: Mapped[Supplier] = relationship(back_populates="spool_links")
    spool: Mapped[Spool] = relationship(back_populates="supplier_links")

    @property
    def supplier_name(self) -> str:
        """Flattened for SpoolSupplierResponse. Every route that embeds links
        chains selectinload() onto ``supplier``, so this never lazy-loads."""
        return self.supplier.name if self.supplier else ""


class SpoolmanSpoolSupplier(Base):
    """``SpoolSupplier`` for a Spoolman-managed spool.

    Mirrors ``SpoolmanKProfile``: Spoolman owns the spool, Bambuddy owns the
    supplier assignment, so the row is local and keyed by the remote spool id
    with no foreign key to enforce it. Suppliers are Bambuddy-side on purpose —
    Spoolman's ``vendor`` is the manufacturer, not the seller.
    """

    __tablename__ = "spoolman_spool_suppliers"
    __table_args__ = (UniqueConstraint("spoolman_spool_id", "supplier_id", name="uq_spoolman_spool_suppliers_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    spoolman_spool_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    supplier_article_number: Mapped[str | None] = mapped_column(String(100))
    quoted_price_per_kg: Mapped[float | None] = mapped_column(Float)
    is_purchase_source: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    supplier: Mapped[Supplier] = relationship()

    @property
    def supplier_name(self) -> str:
        return self.supplier.name if self.supplier else ""
