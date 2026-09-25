"""Free-text filament catalog search for the SpoolBuddy "Find This Filament" flow.

Searches the user's inventory (Spoolman or local) and the cached community
databases (OFD, then SpoolmanDB-Community) for filaments matching free text,
ranking the user's own inventory first. The two external sources are gated on
``barcode_lookup_enabled`` but stay fully offline-capable — both clients serve
from their on-disk cache — so this works without network access once the
databases have been fetched once.

Route-layer concerns (auth, query validation, resolving the Spoolman client)
stay in ``routes/inventory.py``; this module owns the search itself.
"""

import logging

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool
from backend.app.schemas.spool import LinkedCode
from backend.app.services import ofd_client, spoolmandb_community_client
from backend.app.services.barcode_resolver import BARCODE_FIELD_KEYS, barcode_lookup_enabled, codes_for_spool
from backend.app.services.spoolman import SpoolmanClient

logger = logging.getLogger(__name__)


class CatalogSearchRow(BaseModel):
    """One candidate filament for the SpoolBuddy "Find This Filament" picker —
    a row the user can pick to link a scanned-but-unmatched barcode to a known
    product. Mirrors the barcode-lookup field set plus a source tag and the
    sibling codes to persist when the row is chosen."""

    source: str  # "inventory" | "ofd" | "spoolmandb-community"
    spool_id: int | None = None
    material: str | None = None
    brand: str | None = None
    subtype: str | None = None
    color_name: str | None = None
    rgba: str | None = None
    label_weight: int | None = None
    nozzle_temp_min: int | None = None
    nozzle_temp_max: int | None = None
    codes: list[LinkedCode] = []


def catalog_match(tokens: list[str], *values: str | None) -> bool:
    """Every token must appear somewhere in the concatenated searchable text."""
    haystack = " ".join(v.lower() for v in values if v)
    return all(tok in haystack for tok in tokens)


async def search_catalog(
    db: AsyncSession,
    q: str,
    limit: int,
    settings: dict[str, str],
    spoolman_client: SpoolmanClient | None,
) -> list[CatalogSearchRow]:
    """Search inventory + community databases for filaments matching ``q``.

    ``spoolman_client`` is the resolved client when Spoolman mode is active
    (the caller owns that resolution), or None to search the local inventory.
    """
    tokens = [t for t in q.lower().split() if t]
    if not tokens:
        return []

    lookup_enabled = barcode_lookup_enabled(settings)
    rows: list[CatalogSearchRow] = []

    # 1. The user's own inventory (exact, authoritative).
    if spoolman_client is not None:
        try:
            from backend.app.api.routes._spoolman_helpers import _map_spoolman_spool

            for sm in await spoolman_client.get_spools():
                try:
                    mapped = _map_spoolman_spool(sm)
                except ValueError:
                    continue
                if catalog_match(
                    tokens,
                    mapped.get("brand"),
                    mapped.get("material"),
                    mapped.get("subtype"),
                    mapped.get("color_name"),
                    mapped.get("gtin_code"),
                    mapped.get("sku_code"),
                    mapped.get("asin_code"),
                    mapped.get("other_code"),
                ):
                    rows.append(
                        CatalogSearchRow(
                            source="inventory",
                            spool_id=mapped.get("id"),
                            codes=[LinkedCode(**c) for c in (mapped.get("linked_codes") or [])],
                            **{k: mapped.get(k) for k in BARCODE_FIELD_KEYS},
                        )
                    )
                if len(rows) >= limit:
                    break
        except Exception:
            logger.warning("Spoolman inventory search failed for catalog-search", exc_info=True)
    else:
        result = await db.execute(select(Spool).where(Spool.archived_at.is_(None)))
        for spool in result.scalars().all():
            if catalog_match(
                tokens,
                spool.brand,
                spool.material,
                spool.subtype,
                spool.color_name,
                spool.gtin_code,
                spool.sku_code,
                spool.asin_code,
                spool.other_code,
            ):
                rows.append(
                    CatalogSearchRow(
                        source="inventory",
                        spool_id=spool.id,
                        codes=[LinkedCode(**c) for c in codes_for_spool(spool)],
                        **{k: getattr(spool, k) for k in BARCODE_FIELD_KEYS},
                    )
                )
            if len(rows) >= limit:
                break

    # 2 & 3. Community databases (OFD, then SpoolmanDB-Community).
    #
    # OFD rows are PER PACKAGE (one row per size entry), not per variant: a
    # brand can sell the identical filament in 0.5/1/3 kg boxes, each with its
    # own GTIN + SKU, and a merged per-variant row showed one arbitrary
    # label_weight over a mixed multi-size code list — the three sizes were
    # indistinguishable and picking one could store another package's codes.
    # Each index entry IS one size row (its fields carry that size's
    # label_weight; "paired" is the code printed on the same box), so a result
    # row now means "a purchasable package" — what the user is holding.
    if lookup_enabled and len(rows) < limit:
        try:
            gtin_index = await ofd_client.get_gtin_index()
            for gtin, entry in gtin_index.items():
                fields = entry.get("fields", {})
                if catalog_match(
                    tokens, fields.get("brand"), fields.get("material"), fields.get("subtype"), fields.get("color_name")
                ):
                    entry_refill = bool(entry.get("is_refill"))
                    codes = [LinkedCode(code=gtin, kind="gtin", is_refill=entry_refill)]
                    if entry.get("paired"):
                        codes.append(LinkedCode(code=entry["paired"], kind="sku", is_refill=entry_refill))
                    rows.append(
                        CatalogSearchRow(
                            source="ofd",
                            codes=codes,
                            **{k: fields.get(k) for k in BARCODE_FIELD_KEYS},
                        )
                    )
                if len(rows) >= limit:
                    break
            # Sizes sold without a GTIN exist only in the article index; the
            # paired ones were already emitted via their GTIN entry above.
            if len(rows) < limit:
                article_index = await ofd_client.get_article_index()
                for article, entry in article_index.items():
                    if entry.get("paired"):
                        continue
                    fields = entry.get("fields", {})
                    if catalog_match(
                        tokens,
                        fields.get("brand"),
                        fields.get("material"),
                        fields.get("subtype"),
                        fields.get("color_name"),
                    ):
                        rows.append(
                            CatalogSearchRow(
                                source="ofd",
                                codes=[LinkedCode(code=article, kind="sku", is_refill=bool(entry.get("is_refill")))],
                                **{k: fields.get(k) for k in BARCODE_FIELD_KEYS},
                            )
                        )
                    if len(rows) >= limit:
                        break
        except Exception:
            logger.warning("OFD catalog-search failed", exc_info=True)

    if lookup_enabled and len(rows) < limit:
        try:
            for variant in await spoolmandb_community_client.get_filaments():
                if catalog_match(
                    tokens,
                    variant.get("brand"),
                    variant.get("material"),
                    variant.get("subtype"),
                    variant.get("color_name"),
                ):
                    codes = spoolmandb_community_client.codes_for_variant(variant)
                    rows.append(
                        CatalogSearchRow(
                            source="spoolmandb-community",
                            codes=[LinkedCode(**c) for c in codes],
                            **{k: variant.get(k) for k in BARCODE_FIELD_KEYS},
                        )
                    )
                if len(rows) >= limit:
                    break
        except Exception:
            logger.warning("SpoolmanDB-Community catalog-search failed", exc_info=True)

    return rows[:limit]
