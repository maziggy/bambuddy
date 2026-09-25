"""Open Filament Database (OFD) barcode lookup client.

The OFD (https://openfilamentdatabase.org) publishes a data dump of retail
spool barcodes (GTINs) joined to brand / material / colour / weight. This
client downloads it and builds a barcode -> fields index, on the shared
download/cache spine in ``external_catalog_cache.py``.

Each OFD ``sizes`` row can carry a ``gtin`` (retail barcode) AND/OR an
``article_number`` (manufacturer SKU — what SpoolmanDB-Community calls
``codes``) AND a ``spool_refill`` flag, independently of each other. Multiple
``sizes`` rows (one per package weight) share one ``variant_id`` (one per
colour), so this client groups all codes sharing a ``variant_id`` together —
a hit on any one of them (via `lookup`/`lookup_article`) also returns every
sibling code for that colour, letting a scan of an *unfamiliar* code still
resolve once *any* of its siblings has been seen before (see
``backend/app/services/barcode_resolver.py``).

Ported from the standalone `filament_to_bambuddy` companion app's `ofd.py`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from backend.app.services.external_catalog_cache import CachedCatalogClient, canon, hex_to_rgba, stream_download

logger = logging.getLogger(__name__)

OFD_ALL_URL = "https://api.openfilamentdatabase.org/json/all.json"

# The real dump is ~1.5 MB; ~10x headroom is generous without letting a
# malicious/broken upstream buffer an effectively unbounded body. Matches the
# proportionate caps on the SpoolmanDB-Community client.
_MAX_ALL_JSON_BYTES = 16 * 1024 * 1024


def _subtype_from(filament_name: str, material: str) -> str | None:
    """Best-effort subtype: the filament name minus the material word."""
    if not filament_name:
        return None
    s = filament_name
    if material:
        s = re.sub(rf"\b{re.escape(material)}\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" -+")
    return s or None


def _build_index(all_json: dict) -> tuple[dict[str, dict], dict[str, dict], dict[str, list[dict]]]:
    """Build (gtin_index, article_index, variant_codes) from the OFD all.json dump.

    ``gtin_index`` / ``article_index`` map a canonicalized code to
    ``{"fields": {...}, "variant_id": str}`` — fields are computed per
    *size* row (e.g. `label_weight` legitimately differs across package
    sizes of the same colour), so each code keeps its own accurate fields.

    ``variant_codes`` maps ``variant_id`` -> every code (GTIN or SKU/article,
    across every package size) sharing that colour, so a hit on any one code
    can recover its siblings for cross-referencing and storage.
    """
    brands = {b["id"]: b for b in all_json.get("brands", []) if isinstance(b, dict) and "id" in b}
    filaments = {f["id"]: f for f in all_json.get("filaments", []) if isinstance(f, dict) and "id" in f}
    variants = {v["id"]: v for v in all_json.get("variants", []) if isinstance(v, dict) and "id" in v}

    gtin_index: dict[str, dict] = {}
    article_index: dict[str, dict] = {}
    variant_codes: dict[str, list[dict]] = {}

    sizes = all_json.get("sizes", [])
    if not isinstance(sizes, list):
        sizes = []
    for size in sizes:
        if not isinstance(size, dict):
            continue
        gtin = size.get("gtin")
        article = size.get("article_number")
        if not isinstance(gtin, str) or not gtin.strip():
            gtin = None
        if not isinstance(article, str) or not article.strip():
            article = None
        if not gtin and not article:
            continue

        variant_id = size.get("variant_id")
        variant = variants.get(variant_id)
        if not variant:
            continue
        # Always key/store variant_id as a string: dict keys become strings
        # after a JSON cache round-trip regardless of the source type, so
        # storing anything else here would silently break get()-lookups the
        # moment the cache is reloaded from disk.
        variant_id = str(variant_id)
        fil = filaments.get(variant.get("filament_id"))
        if not fil:
            continue
        brand = brands.get(fil.get("brand_id"))
        material = fil.get("material") or ""

        fields: dict = {"material": material} if material else {}
        if brand and brand.get("name"):
            fields["brand"] = brand["name"]
        sub = _subtype_from(fil.get("name", ""), material)
        if sub:
            fields["subtype"] = sub
        if variant.get("name"):
            fields["color_name"] = variant["name"]
        rgba = hex_to_rgba(variant.get("color_hex"))
        if rgba:
            fields["rgba"] = rgba
        if size.get("filament_weight"):
            try:
                fields["label_weight"] = int(round(float(size["filament_weight"])))
            except (TypeError, ValueError):
                pass
        for src, dst in (
            ("min_print_temperature", "nozzle_temp_min"),
            ("max_print_temperature", "nozzle_temp_max"),
        ):
            if fil.get(src) is not None:
                try:
                    fields[dst] = int(fil[src])
                except (TypeError, ValueError):
                    pass

        is_refill = bool(size.get("spool_refill"))
        codes_list = variant_codes.setdefault(variant_id, [])

        if gtin:
            canonical_gtin = canon(gtin)
            # "paired": the code printed on this SAME size row (purchasable
            # package) — the article for a gtin and vice versa. This is the
            # only same-package linkage OFD offers once codes are flattened
            # per variant, and the size-consistency fill rule depends on it:
            # a variant spans every package size, so variant-level siblings
            # must never be copied onto a spool as if they named its package.
            gtin_index[canonical_gtin] = {
                "fields": fields,
                "variant_id": variant_id,
                "paired": article.strip().upper() if article else None,
                "is_refill": is_refill,
            }
            if not any(c["code"] == canonical_gtin for c in codes_list):
                codes_list.append({"code": canonical_gtin, "kind": "gtin", "is_refill": is_refill})
        if article:
            normalized_article = article.strip().upper()
            article_index[normalized_article] = {
                "fields": fields,
                "variant_id": variant_id,
                "paired": canon(gtin) if gtin else None,
                "is_refill": is_refill,
            }
            if not any(c["code"] == normalized_article for c in codes_list):
                codes_list.append({"code": normalized_article, "kind": "sku", "is_refill": is_refill})

    return gtin_index, article_index, variant_codes


class _OfdClient(CachedCatalogClient):
    name = "OFD"
    cache_filename = "ofd_cache.json"
    # Bump whenever the payload shape changes, so an old cache file is treated
    # as stale and rebuilt instead of being misread.
    # v4: index entries gained "paired" (same-size-row sibling code).
    cache_version = 4

    async def _download_and_build(self) -> dict:
        raw = await stream_download(OFD_ALL_URL, max_bytes=_MAX_ALL_JSON_BYTES, timeout=60.0)
        # Parse + index off the event loop: this runs inside some unlucky
        # user's request when the 24h TTL lapses, and shouldn't stall every
        # other request while a multi-MB JSON dump is chewed through.
        all_json = await asyncio.to_thread(json.loads, raw)
        gtin_index, article_index, variant_codes = await asyncio.to_thread(_build_index, all_json)
        if not gtin_index and not article_index:
            # A 200 that parses to zero entries (e.g. upstream's dump shape
            # changes) must not overwrite a good cache with an empty one and
            # silently return "no match" for everyone for a full TTL — raising
            # here reuses the base's stale-cache fallback instead.
            raise RuntimeError("OFD refresh parsed zero entries - keeping previous cache")
        return {"gtin_index": gtin_index, "article_index": article_index, "variant_codes": variant_codes}


_client = _OfdClient()


async def get_gtin_index() -> dict[str, dict]:
    """Return the canonical-GTIN -> {fields, variant_id} index (memory -> disk cache -> download)."""
    return (await _client.payload()).get("gtin_index", {})


async def get_article_index() -> dict[str, dict]:
    """Return the normalized-article-number -> {fields, variant_id} index."""
    return (await _client.payload()).get("article_index", {})


async def codes_for_variant(variant_id: str) -> list[dict]:
    """Every GTIN/SKU sibling code for a variant id."""
    variant_codes = (await _client.payload()).get("variant_codes", {})
    return list(variant_codes.get(variant_id, []))


async def lookup(barcode: str) -> tuple[dict, list[dict]] | None:
    """Resolve a GTIN barcode: (fields, all_codes) for its colour, or None if not found.

    ``all_codes`` includes every GTIN/SKU sibling (other package sizes, the
    refill code, the manufacturer article number) sharing the same colour.
    """
    entry = (await get_gtin_index()).get(canon(barcode))
    if not entry:
        return None
    return entry["fields"], await codes_for_variant(entry["variant_id"])


async def lookup_article(code: str) -> tuple[dict, list[dict]] | None:
    """Resolve a manufacturer SKU/article number the same way `lookup` resolves a GTIN."""
    entry = (await get_article_index()).get((code or "").strip().upper())
    if not entry:
        return None
    return entry["fields"], await codes_for_variant(entry["variant_id"])


async def same_package_code(code: str, kind: str) -> tuple[bool, str | None]:
    """(entry known?, the code printed on the SAME size row) for `code`.

    A size row pairs one gtin with one article_number — the only
    same-purchasable-package linkage OFD has. Returns (False, None) for an
    unknown code, (True, None) when that size row carried only one code.
    """
    if kind == "gtin":
        entry = (await get_gtin_index()).get(canon(code))
    else:
        entry = (await get_article_index()).get((code or "").strip().upper())
    if not entry:
        return False, None
    return True, entry.get("paired") or None
