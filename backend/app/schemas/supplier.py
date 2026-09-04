"""Pydantic schemas for the supplier master list (#2988)."""

from datetime import datetime

from pydantic import BaseModel, Field


class SupplierBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    website: str | None = Field(default=None, max_length=500)
    # The business's own customer number AT this supplier.
    customer_number: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=500)


class SupplierCreate(SupplierBase):
    pass


class SupplierUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    website: str | None = Field(default=None, max_length=500)
    customer_number: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=500)


class SupplierResponse(SupplierBase):
    id: int
    # Number of spools referencing this supplier — shown in the settings list
    # and the reason a delete is refused (409) instead of orphaning links.
    spool_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SpoolSupplierLinkInput(BaseModel):
    """One spool-to-supplier assignment as written by the spool dialog."""

    supplier_id: int = Field(..., gt=0)
    # The supplier's own article number for the product — NOT the internal
    # material number (#2870).
    supplier_article_number: str | None = Field(default=None, max_length=100)
    # Quoted price per kg at this supplier, for comparing sources. Never a
    # cost basis — ``spool.cost_per_kg`` stays authoritative and is not
    # written from assignments.
    quoted_price_per_kg: float | None = Field(default=None, ge=0)
    # Marks where this concrete spool was actually bought; the other
    # assignments are alternative sources.
    is_purchase_source: bool = False


class SpoolSupplierResponse(BaseModel):
    id: int
    supplier_id: int
    supplier_name: str
    supplier_article_number: str | None = None
    quoted_price_per_kg: float | None = None
    is_purchase_source: bool = False

    class Config:
        from_attributes = True


class SupplierStats(BaseModel):
    """Per-supplier inventory aggregate (#2988), purchase-source spools only.

    ``spool_count`` and ``remaining_g`` cover active spools bought at this
    supplier; ``consumed_g`` and ``cost`` sum the recorded usage history of
    every purchase-source spool, archived included.
    """

    supplier_id: int
    supplier_name: str
    spool_count: int
    remaining_g: float
    consumed_g: float
    cost: float
