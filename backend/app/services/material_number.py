"""Material-number inheritance for newly created spools (#2870).

The material number is the internal purchasing/article identifier a business
costs by (e.g. "15" = Bambu Lab PLA Basic). All spools of the same product
share it, so a new spool of an already-numbered product should arrive with
the number filled instead of blank — whether it is created manually, via the
API, or by the RFID auto-add.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool


async def find_material_number_for_product(
    db: AsyncSession,
    *,
    material: str | None,
    subtype: str | None,
    brand: str | None,
    color_name: str | None,
) -> str | None:
    """Return the material number an existing spool of this product carries.

    Product identity is the (material, subtype, brand, color_name) string
    tuple — the same key FilamentSkuSettings groups by. The most recently
    updated match wins, so a corrected number beats stale ones. Archived
    spools count: a product being out of stock doesn't change its number.
    """
    if not material:
        return None

    def _same(column, value):
        return column.is_(None) if value is None else column == value

    result = await db.execute(
        select(Spool.material_number)
        .where(
            Spool.material_number.is_not(None),
            Spool.material_number != "",
            Spool.material == material,
            _same(Spool.subtype, subtype),
            _same(Spool.brand, brand),
            _same(Spool.color_name, color_name),
        )
        .order_by(Spool.updated_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def apply_material_number_inheritance(db: AsyncSession, payload: dict) -> dict:
    """Fill ``payload["material_number"]`` from a matching existing spool.

    No-op when the caller already supplied a non-empty number. Only used on
    the create paths — editing a spool never overwrites what the user set.
    """
    if payload.get("material_number"):
        return payload
    number = await find_material_number_for_product(
        db,
        material=payload.get("material"),
        subtype=payload.get("subtype"),
        brand=payload.get("brand"),
        color_name=payload.get("color_name"),
    )
    if number:
        payload = dict(payload)
        payload["material_number"] = number
    return payload
