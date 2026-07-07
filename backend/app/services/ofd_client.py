"""Open Filament Database (OFD) barcode lookup client.

The OFD (https://openfilamentdatabase.org) publishes a data dump of retail
spool barcodes (GTINs) joined to brand / material / colour / weight. This
client downloads it, builds a barcode -> fields index, and caches both the
raw dump and the built index on disk with a 24h TTL — refreshed lazily on
the next lookup once stale, since this backend has no scheduler/cron.

Each OFD ``sizes`` row can carry a ``gtin`` (retail barcode) AND/OR an
``article_number`` (manufacturer SKU — what SpoolmanDB-Community calls
``codes``) AND a ``spool_refill`` flag, independently of each other. Multiple
``sizes`` rows (one per package weight) share one ``variant_id`` (one per
colour), so this client groups all codes sharing a ``variant_id`` together —
a hit on any one of them (via `lookup`/`lookup_article`) also returns every
sibling code for that colour, letting a scan of an *unfamiliar* code still
resolve once *any* of its siblings has been seen before (see `_resolve_barcode`
in `backend/app/api/routes/inventory.py`).

Ported from the standalone `filament_to_bambuddy` companion app's `ofd.py`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path

import httpx

from backend.app.core.paths import resolve_data_dir

logger = logging.getLogger(__name__)

OFD_ALL_URL = "https://api.openfilamentdatabase.org/json/all.json"
OFD_TTL_SECONDS = 24 * 3600

# Bump whenever the on-disk cache shape changes, so an old cache file (e.g.
# pre-dating article_number/variant-code support) is treated as stale and
# rebuilt instead of being misread.
_CACHE_VERSION = 2

# In-process cache so we don't rebuild the index on every request.
_gtin_index: dict[str, dict] | None = None
_article_index: dict[str, dict] | None = None
_variant_codes: dict[str, list[dict]] | None = None
_brands: list[str] | None = None
_index_loaded_at = 0.0
_refresh_lock = asyncio.Lock()


def _cache_path() -> Path:
    return resolve_data_dir() / "ofd_cache.json"


def canon(barcode: str) -> str:
    """Canonical GTIN form for matching: digits only, leading zeros stripped.

    Makes a UPC-A (12-digit) barcode and its EAN-13 (leading-zero) form
    compare equal. Also used to canonicalize `Spool.barcode` on write so the
    native-inventory lookup path compares like-for-like against OFD.
    """
    digits = re.sub(r"\D", "", barcode or "")
    return digits.lstrip("0") or "0"


def _hex_to_rgba(color_hex) -> str | None:
    if isinstance(color_hex, list):
        color_hex = color_hex[0] if color_hex else None
    if not isinstance(color_hex, str):
        return None
    h = color_hex.lstrip("#")
    if len(h) == 6 and re.fullmatch(r"[0-9A-Fa-f]{6}", h):
        return h.upper() + "FF"  # RRGGBBAA, opaque
    return None


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
    brands = {b["id"]: b for b in all_json.get("brands", []) if "id" in b}
    filaments = {f["id"]: f for f in all_json.get("filaments", []) if "id" in f}
    variants = {v["id"]: v for v in all_json.get("variants", []) if "id" in v}

    gtin_index: dict[str, dict] = {}
    article_index: dict[str, dict] = {}
    variant_codes: dict[str, list[dict]] = {}

    for size in all_json.get("sizes", []):
        gtin = size.get("gtin")
        article = size.get("article_number")
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
        rgba = _hex_to_rgba(variant.get("color_hex"))
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
        codes_for_variant = variant_codes.setdefault(variant_id, [])

        if gtin:
            canonical_gtin = canon(gtin)
            gtin_index[canonical_gtin] = {"fields": fields, "variant_id": variant_id}
            if not any(c["code"] == canonical_gtin for c in codes_for_variant):
                codes_for_variant.append({"code": canonical_gtin, "kind": "gtin", "is_refill": is_refill})
        if article:
            normalized_article = article.strip().upper()
            article_index[normalized_article] = {"fields": fields, "variant_id": variant_id}
            if not any(c["code"] == normalized_article for c in codes_for_variant):
                codes_for_variant.append({"code": normalized_article, "kind": "sku", "is_refill": is_refill})

    return gtin_index, article_index, variant_codes


def _load_cached() -> tuple[dict, dict, dict, list] | None:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if data.get("cache_version") != _CACHE_VERSION:
            return None
        if time.time() - data.get("built_at", 0) > OFD_TTL_SECONDS:
            return None
        return (
            data.get("gtin_index", {}),
            data.get("article_index", {}),
            data.get("variant_codes", {}),
            data.get("brands", []),
        )
    except Exception:
        return None


async def _refresh() -> tuple[dict, dict, dict, list]:
    """Download all.json; build the indexes + brand-name list; cache all of it."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(OFD_ALL_URL)
        resp.raise_for_status()
        all_json = resp.json()
    gtin_index, article_index, variant_codes = _build_index(all_json)
    brands = sorted({b["name"] for b in all_json.get("brands", []) if b.get("name")})
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "cache_version": _CACHE_VERSION,
                    "built_at": time.time(),
                    "gtin_index": gtin_index,
                    "article_index": article_index,
                    "variant_codes": variant_codes,
                    "brands": brands,
                }
            )
        )
    except Exception:
        logger.warning("Failed to write OFD cache file", exc_info=True)
    return gtin_index, article_index, variant_codes, brands


async def _ensure_loaded(force: bool = False) -> None:
    global _gtin_index, _article_index, _variant_codes, _brands, _index_loaded_at
    if _gtin_index is not None and not force and (time.time() - _index_loaded_at) < OFD_TTL_SECONDS:
        return
    async with _refresh_lock:
        # Re-check after acquiring the lock — another request may have
        # already refreshed while we were waiting.
        if _gtin_index is not None and not force and (time.time() - _index_loaded_at) < OFD_TTL_SECONDS:
            return
        loaded = None if force else _load_cached()
        if loaded is None:
            loaded = await _refresh()
        _gtin_index, _article_index, _variant_codes, _brands = loaded
        _index_loaded_at = time.time()


async def get_gtin_index() -> dict[str, dict]:
    """Return the canonical-GTIN -> {fields, variant_id} index (memory -> disk cache -> download)."""
    await _ensure_loaded()
    return _gtin_index or {}


async def get_article_index() -> dict[str, dict]:
    """Return the normalized-article-number -> {fields, variant_id} index."""
    await _ensure_loaded()
    return _article_index or {}


async def get_brands() -> list[str]:
    """Return the OFD brand-name list (for data-driven brand detection in OCR parsing)."""
    try:
        await _ensure_loaded()
    except Exception:
        logger.warning("OFD brand list unavailable", exc_info=True)
        return []
    return _brands or []


def _codes_for_variant(variant_id: str) -> list[dict]:
    return list(_variant_codes.get(variant_id, [])) if _variant_codes else []


async def lookup(barcode: str) -> tuple[dict, list[dict]] | None:
    """Resolve a GTIN barcode: (fields, all_codes) for its colour, or None if not found.

    ``all_codes`` includes every GTIN/SKU sibling (other package sizes, the
    refill code, the manufacturer article number) sharing the same colour.
    """
    idx = await get_gtin_index()
    entry = idx.get(canon(barcode))
    if not entry:
        return None
    return entry["fields"], _codes_for_variant(entry["variant_id"])


async def lookup_article(code: str) -> tuple[dict, list[dict]] | None:
    """Resolve a manufacturer SKU/article number the same way `lookup` resolves a GTIN."""
    idx = await get_article_index()
    entry = idx.get((code or "").strip().upper())
    if not entry:
        return None
    return entry["fields"], _codes_for_variant(entry["variant_id"])


async def refresh_database() -> int:
    """Force a re-download of the OFD dump regardless of TTL. Returns the combined entry count."""
    await _ensure_loaded(force=True)
    gtin_idx = await get_gtin_index()
    article_idx = await get_article_index()
    return len(gtin_idx) + len(article_idx)
