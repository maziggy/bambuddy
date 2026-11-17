"""Pydantic schemas for the supplier master list (#2988)."""

import re
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

# The CSV `suppliers` column joins names with "; " (#2988). A name carrying
# the separator would split into unknown names on import and silently drop the
# assignments, so it is refused at the edge rather than escaped — the column
# stays readable and hand-editable.
SUPPLIER_NAME_SEPARATOR = ";"


def validate_supplier_name(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("name must not be empty")
    if SUPPLIER_NAME_SEPARATOR in trimmed:
        raise ValueError("name must not contain ';' — it separates suppliers in the CSV export")
    return trimmed


# Long enough for a shop's product page with its tracking and variant query.
PRODUCT_URL_MAX = 1000


# One rule for every address field, mirrored by normalizeWebUrl in the frontend's
# utils/productLinks.ts (keep them in step; both have tests on the same inputs).
_HTTP_PREFIX = re.compile(r"https?://", re.IGNORECASE)
# Another scheme, or a malformed http one ("http:example.com", "https:/x"). A colon
# followed by a port ("example.com:8080/x") is a host, not a scheme.
_OTHER_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*:(?!\d+(?:[/?#]|$))", re.IGNORECASE)
# A host right after "//", ended by a path, query, fragment or the end. A backslash
# ends nothing: browsers read it as a slash, urlsplit does not.
_HTTP_AUTHORITY = re.compile(r"https?://[^\s/?#\\]+(?:[/?#]|$)", re.IGNORECASE)


def validate_http_url(value: object) -> object:
    """A web address, or None for an empty one (#2540).

    These addresses become links in the UI, so only http and https get
    through: a ``javascript:`` or ``data:`` address would run in the page
    of whoever clicks it. Trimmed first; an address without a scheme
    ("3djake.de") gets ``https://``. Non-strings pass through to the type check.
    """
    if not isinstance(value, str):
        return value
    trimmed = value.strip()
    if not trimmed:
        return None
    if not _HTTP_PREFIX.match(trimmed):
        if _OTHER_SCHEME.match(trimmed):
            raise ValueError("must be an http:// or https:// address")
        trimmed = f"https://{trimmed}"
    if not _HTTP_AUTHORITY.match(trimmed) or not urlsplit(trimmed).hostname:
        raise ValueError("must be an http:// or https:// address")
    return trimmed


class SupplierBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    website: str | None = Field(default=None, max_length=500)
    # The business's own customer number AT this supplier.
    customer_number: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=500)


class SupplierCreate(SupplierBase):
    # Only on the write schemas: SupplierResponse inherits SupplierBase, and a
    # row written before the rule existed must still be readable. mode="before"
    # so the name is trimmed before max_length sees it — a padded name at the
    # cap is not too long once stripped. Non-strings pass through to the type
    # check.
    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> object:
        return validate_supplier_name(value) if isinstance(value, str) else value

    @field_validator("website", mode="before")
    @classmethod
    def _check_website(cls, value: object) -> object:
        return validate_http_url(value)


class SupplierUpdate(BaseModel):
    # Optional so a PATCH can leave the name alone. An explicit null is a 422
    # here instead of a NOT NULL violation surfacing as a 500.
    name: str | None = Field(default=None, min_length=1, max_length=200)
    website: str | None = Field(default=None, max_length=500)
    customer_number: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> object:
        if value is None:
            raise ValueError("name must not be null")
        return validate_supplier_name(value) if isinstance(value, str) else value

    @field_validator("website", mode="before")
    @classmethod
    def _check_website(cls, value: object) -> object:
        return validate_http_url(value)


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
    # The product's own page at this supplier (#2540), what the buy buttons
    # open. Not the supplier's storefront (``Supplier.website``).
    product_url: str | None = Field(default=None, max_length=PRODUCT_URL_MAX)

    @field_validator("product_url", mode="before")
    @classmethod
    def _check_product_url(cls, value: object) -> object:
        return validate_http_url(value)


class SpoolPurchaseSource(BaseModel):
    """Where a batch of new spools was bought (#2540): a shopping list item
    received. Becomes their purchase-source assignment."""

    supplier_id: int = Field(..., gt=0)
    supplier_article_number: str | None = Field(default=None, max_length=100)
    product_url: str | None = Field(default=None, max_length=PRODUCT_URL_MAX)

    @field_validator("product_url", mode="before")
    @classmethod
    def _check_product_url(cls, value: object) -> object:
        return validate_http_url(value)


class SpoolSupplierResponse(BaseModel):
    id: int
    supplier_id: int
    supplier_name: str
    supplier_article_number: str | None = None
    quoted_price_per_kg: float | None = None
    is_purchase_source: bool = False
    product_url: str | None = None

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
