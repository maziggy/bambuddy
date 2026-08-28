"""Resolve a Bambu Lab colour name from the colour catalogue.

Extracted so the two inventory modes stop answering the same question in two
places. ``spool_tag_matcher.create_spool_from_tray`` has had this logic inline
since #857, and three separate fixes have landed there without crossing to the
Spoolman side -- the sub-brand filter (#1227), the alpha guard (#1545) and the
translucent handling. #2907 is the fourth. One implementation is the only way
that stops.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.color_catalog import ColorCatalogEntry

BAMBU_MANUFACTURER = "BAMBU LAB"


async def resolve_bambu_color_name(db: AsyncSession, rgba: str | None, sub_brand: str | None) -> str | None:
    """The catalogue's name for this colour, or None when it has no row for it.

    Hex alone is not an identity: ``#FFFFFF`` is "Jade White" in PLA Basic,
    "Ivory White" in PLA Matte and "White" in PLA Silk. The printer reports which
    product line the roll belongs to as ``tray_sub_brands``, and the catalogue
    stores the same string in its ``material`` column -- so the two compare
    directly, with no need to rebuild one from a type plus a subtype.

    None is a real answer and not a failure. The catalogue is seeded from Bambu's
    published hex list and lags new colours, so a roll it has never heard of has
    no name to give and the caller has to cope rather than pick something.

    Alpha 00 short-circuits to "Clear" (#1545) before the catalogue is consulted.
    The catalogue stores RGB only, so a clear roll's ``00000000`` would look up
    ``#000000`` and come back "Black" -- the exact bug #1545 was filed for. This
    is a deliberate carry-over, not an oversight: the built-in path does the same
    thing, and the point of this module is that the two modes stop answering this
    question differently. Without it a clear roll is named "Clear" by internal
    mode and "Black" by Spoolman mode on the same printer.

    Naming a genuinely translucent roll from the catalogue would very likely beat
    "Clear", but that changes both modes and what internal mode has returned
    since #1545, so it is not something an extraction should decide on its way
    past.
    """
    if not rgba:
        return None
    # #1545, and the same test the built-in path applies.
    if len(rgba) == 8 and rgba[6:8].lower() == "00":
        return "Clear"
    if len(rgba) < 6:
        return None
    hex_prefix = f"#{rgba[:6].upper()}"
    query = (
        select(ColorCatalogEntry)
        .where(func.upper(ColorCatalogEntry.hex_color) == hex_prefix)
        .where(func.upper(ColorCatalogEntry.manufacturer) == BAMBU_MANUFACTURER)
    )
    if sub_brand:
        query = query.where(func.upper(ColorCatalogEntry.material) == sub_brand.upper())
    # Deterministic tiebreak for the case the sub-brand filter cannot settle --
    # a third-party roll reporting no sub-brand at all. Same ordering the
    # built-in path uses, so the two cannot disagree about which row wins.
    query = query.order_by(ColorCatalogEntry.id).limit(1)
    entry = (await db.execute(query)).scalar_one_or_none()
    return entry.color_name if entry else None


def filament_matches_product_line(
    filament_name: str | None, expected_color_name: str | None, sub_brand: str | None
) -> bool:
    """Whether a Spoolman filament belongs to the same Bambu product line as a tray.

    Material and colour alone cannot answer this: PLA Basic Black and PLA Matte
    Charcoal are both PLA at ``#000000``, which is why a Matte roll was linked to
    the Basic filament (#2907). The name is the only field on a Spoolman filament
    that carries the line -- there is no product-line column to filter on.

    Two spellings count, and the second is what keeps this from minting a
    duplicate filament for every spool on an existing instance:

    * the catalogue's colour name -- what this path creates from now on, and what
      an entry taken from the external library is already called;
    * the sub-brand itself -- what Bambuddy has been naming its own creations
      since before this fix (``name=tray.tray_sub_brands``).

    Both encode the product line, so both distinguish PLA Basic from PLA Matte.
    A filament named neither is not this line and must not be reused.

    The colour name is a proxy for the line and a lossy one, so be clear about
    what it cannot do: ``#000000`` is "Black" under PLA Basic, PLA Tough, PLA-CF,
    ABS, ASA, PETG HF, PETG-CF and TPU 90A alike. Lines that share a base
    material *and* a colour name still collapse into one filament. This is
    pre-existing and not made worse here -- the material check upstream already
    separates ABS from PLA -- but "both encode the product line" is a weaker
    statement than it looks, and the case it does not cover is two PLA lines
    whose catalogue rows carry the same colour name.
    """
    name = (filament_name or "").strip().lower()
    if not name:
        return False
    if expected_color_name and name == expected_color_name.strip().lower():
        return True
    return bool(sub_brand) and name == sub_brand.strip().lower()
