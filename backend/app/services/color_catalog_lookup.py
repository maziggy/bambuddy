"""Resolve a Bambu Lab roll's colour from the colour catalogue, and recognise its product line.

Extracted so the two inventory modes stop answering the same question in two
places. ``spool_tag_matcher.create_spool_from_tray`` had this logic inline since
#857, and three separate fixes landed there without crossing to the Spoolman
side -- the sub-brand filter (#1227), the alpha guard (#1545) and the translucent
handling. #2907 is the fourth. Both callers now go through here.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.color_catalog import ColorCatalogEntry

BAMBU_MANUFACTURER = "BAMBU LAB"


@dataclass(frozen=True)
class CatalogColor:
    """What the catalogue row says about a roll's colour.

    The name alone is not enough for the built-in path: a row can carry gradient
    stops (``extra_colors``) and a rendering hint (``effect_type``), which is how
    a roll the AMS identified draws the same as one picked by hand from that row.
    """

    name: str
    extra_colors: str | None = None
    effect_type: str | None = None


async def resolve_bambu_color(db: AsyncSession, rgba: str | None, sub_brand: str | None) -> CatalogColor | None:
    """The catalogue's colour for this roll, or None when it has no row for it.

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
    ``#000000`` and come back "Black" -- the exact bug #1545 was filed for.

    That short-circuit is also a limit worth stating. "Clear" is the answer for
    every product line, so a PLA Matte clear roll and a PLA Basic clear roll get
    the same name, and nothing downstream that keys on the name can tell them
    apart. The Spoolman path's product-line criterion is inert for translucent
    rolls for exactly this reason. Naming a genuinely translucent roll from the
    catalogue would very likely beat "Clear", but that changes both modes and
    what the built-in path has returned since #1545, so it is not something an
    extraction should decide on its way past.
    """
    if not rgba:
        return None
    # #1545, and the same test the built-in path has always applied.
    if len(rgba) == 8 and rgba[6:8].lower() == "00":
        return CatalogColor("Clear")
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
    # a roll reporting no sub-brand at all.
    query = query.order_by(ColorCatalogEntry.id).limit(1)
    entry = (await db.execute(query)).scalar_one_or_none()
    if entry is None:
        return None
    return CatalogColor(entry.color_name, entry.extra_colors, entry.effect_type)


def _normalise(name: str | None) -> str:
    # "Tough+ Black" and "Silk+ Gold" are the library's spelling of lines the
    # printer reports as "PLA Tough" and "PLA Silk". The plus is part of the
    # product name, not the line, so it is dropped before comparing.
    return " ".join((name or "").replace("+", " ").lower().split())


def _line_word(sub_brand: str | None, material: str | None) -> str:
    """The product line with the base material taken off: "PLA Matte" -> "matte"."""
    line = _normalise(sub_brand)
    base = _normalise(material)
    if base and line.startswith(base + " "):
        return line[len(base) + 1 :]
    return ""


def product_line_rank(
    filament_name: str | None,
    color_name: str | None,
    sub_brand: str | None,
    material: str | None,
) -> int | None:
    """How specifically a filament name identifies this roll's product line, or None if it does not.

    Material and colour alone cannot answer this: PLA Basic Black and PLA Matte
    Charcoal are both PLA at ``#000000``, which is why a Matte roll was linked to
    the Basic filament (#2907). The name is the only field on a Spoolman filament
    that carries the line.

    Three spellings count, ranked by how much of the line they carry:

    0. the sub-brand itself ("PLA Matte") -- what Bambuddy names the filaments
       it creates, and has since before this fix;
    1. the line folded into the colour ("Matte Charcoal", "Tough+ Black",
       "Brown Galaxy") -- how SpoolmanDB names every Bambu Lab entry outside PLA
       Basic, and so what a filament taken from the library used to be called;
    2. the bare colour name ("Black") -- how SpoolmanDB names PLA Basic, and the
       lines that have a base material of their own (ABS, ASA, PLA-CF ...).

    Rank 2 is the weak one and callers must prefer anything better. "Black" is
    the bare name for PLA Basic, PLA Tough, PETG HF and TPU 90A alike, so on its
    own it cannot tell two lines of the same material apart -- it is accepted
    because an existing instance is full of filaments called that and refusing
    it would duplicate every one of them.
    """
    name = _normalise(filament_name)
    if not name:
        return None
    if sub_brand and name == _normalise(sub_brand):
        return 0
    color = _normalise(color_name)
    if not color:
        return None
    line = _line_word(sub_brand, material)
    if line and name in (f"{line} {color}", f"{color} {line}"):
        return 1
    if name == color:
        return 2
    return None


def best_product_line_match(
    filaments: list[dict],
    color_name: str | None,
    sub_brand: str | None,
    material: str | None,
) -> dict | None:
    """The filament whose name identifies this roll's line most specifically; first wins a tie."""
    best: dict | None = None
    best_rank: int | None = None
    for filament in filaments:
        rank = product_line_rank(filament.get("name"), color_name, sub_brand, material)
        if rank is not None and (best_rank is None or rank < best_rank):
            best, best_rank = filament, rank
    return best
