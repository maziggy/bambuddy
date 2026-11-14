"""Supplier-assignment inheritance for newly created spools (#2988).

A new spool of a product that other spools already carry supplier
assignments for should arrive knowing where it can be bought — whether it
is created manually, via the API, or by the RFID auto-add. Only the source
list is copied; ``is_purchase_source`` is deliberately reset, because where
THIS spool was bought is not something the donor can know.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool
from backend.app.models.supplier import SpoolSupplier


async def find_supplier_link_templates_for_product(
    db: AsyncSession,
    *,
    material: str | None,
    subtype: str | None,
    brand: str | None,
    color_name: str | None,
) -> list[dict]:
    """Return supplier-link field dicts from the newest matching spool.

    Product identity is the (material, subtype, brand, color_name) string
    tuple — the same key FilamentSkuSettings groups by. Archived spools
    count as donors: a product being out of stock doesn't change where it
    can be bought.
    """
    if not material:
        return []

    def _same(column, value):
        return column.is_(None) if value is None else column == value

    donor = await db.execute(
        select(Spool.id)
        .join(SpoolSupplier, SpoolSupplier.spool_id == Spool.id)
        .where(
            Spool.material == material,
            _same(Spool.subtype, subtype),
            _same(Spool.brand, brand),
            _same(Spool.color_name, color_name),
        )
        .order_by(Spool.updated_at.desc())
        .limit(1)
    )
    donor_id = donor.scalars().first()
    if donor_id is None:
        return []

    links = await db.execute(select(SpoolSupplier).where(SpoolSupplier.spool_id == donor_id))
    return [
        {
            "supplier_id": link.supplier_id,
            "supplier_article_number": link.supplier_article_number,
            "quoted_price_per_kg": link.quoted_price_per_kg,
            "is_purchase_source": False,
            # The product's page is the product's, so a new spool of it keeps it (#2540).
            "product_url": link.product_url,
        }
        for link in links.scalars().all()
    ]


def with_purchase_source(templates: list[dict], source: dict) -> list[dict]:
    """``templates`` with ``source`` as where the new spools were bought (#2540).

    A shopping list item received knows its supplier: its link becomes the
    purchase source, taking the item's article number and product page where
    it names them, or is added when the product had no link to it yet.
    """
    named = {k: v for k, v in source.items() if k in _SOURCE_FIELDS and v is not None}
    merged = []
    found = False
    for template in templates:
        if template["supplier_id"] == source["supplier_id"]:
            found = True
            template = {**template, **named, "is_purchase_source": True}
        merged.append(template)
    if not found:
        merged.append(
            {
                "supplier_id": source["supplier_id"],
                "supplier_article_number": None,
                "quoted_price_per_kg": None,
                "product_url": None,
                **named,
                "is_purchase_source": True,
            }
        )
    return merged


# What a purchase source can name of its link; the rest is the inherited link's.
_SOURCE_FIELDS = ("supplier_article_number", "quoted_price_per_kg", "product_url")


async def apply_supplier_inheritance(db: AsyncSession, spool: Spool) -> None:
    """Copy supplier assignments onto a freshly created (flushed) spool.

    No-op when the spool already has assignments. The caller commits.
    """
    existing = await db.execute(select(SpoolSupplier.id).where(SpoolSupplier.spool_id == spool.id).limit(1))
    if existing.first() is not None:
        return
    templates = await find_supplier_link_templates_for_product(
        db,
        material=spool.material,
        subtype=spool.subtype,
        brand=spool.brand,
        color_name=spool.color_name,
    )
    _attach(db, spool, templates)


async def apply_supplier_inheritance_to_batch(
    db: AsyncSession, spools: list[Spool], purchase_source: dict | None = None
) -> None:
    """Inheritance for a bulk create: one donor lookup for the whole batch.

    Every copy is built from the same payload, so the product tuple — and with
    it the answer — is identical for all of them. ``purchase_source`` (a
    supplier link's fields) is where the batch was bought, when the caller
    knows (#2540). The caller commits.
    """
    if not spools:
        return
    first = spools[0]
    templates = await find_supplier_link_templates_for_product(
        db,
        material=first.material,
        subtype=first.subtype,
        brand=first.brand,
        color_name=first.color_name,
    )
    if purchase_source is not None:
        templates = with_purchase_source(templates, purchase_source)
    for spool in spools:
        _attach(db, spool, templates)


def _attach(db: AsyncSession, spool: Spool, templates: list[dict]) -> None:
    for template in templates:
        db.add(SpoolSupplier(spool_id=spool.id, **template))
