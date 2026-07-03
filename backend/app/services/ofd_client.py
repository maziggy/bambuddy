"""Open Filament Database (OFD) barcode lookup client.

The OFD (https://openfilamentdatabase.org) publishes a data dump of retail
spool barcodes (GTINs) joined to brand / material / colour / weight. This
client downloads it, builds a barcode -> fields index, and caches both the
raw dump and the built index on disk with a 24h TTL — refreshed lazily on
the next lookup once stale, since this backend has no scheduler/cron.

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

# In-process cache so we don't rebuild the index on every request.
_index: dict[str, dict] | None = None
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


def _build_index(all_json: dict) -> dict[str, dict]:
    """Build {canonical_gtin: fields} from the OFD all.json dump."""
    brands = {b["id"]: b for b in all_json.get("brands", []) if "id" in b}
    filaments = {f["id"]: f for f in all_json.get("filaments", []) if "id" in f}
    variants = {v["id"]: v for v in all_json.get("variants", []) if "id" in v}

    index: dict[str, dict] = {}
    for size in all_json.get("sizes", []):
        gtin = size.get("gtin")
        if not gtin:
            continue
        variant = variants.get(size.get("variant_id"))
        if not variant:
            continue
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

        index[canon(gtin)] = fields
    return index


def _load_cached() -> tuple[dict, list] | None:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if time.time() - data.get("built_at", 0) > OFD_TTL_SECONDS:
            return None
        return data.get("index"), data.get("brands", [])
    except Exception:
        return None


async def _refresh() -> tuple[dict, list]:
    """Download all.json; build the barcode index + brand-name list; cache both."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(OFD_ALL_URL)
        resp.raise_for_status()
        all_json = resp.json()
    index = _build_index(all_json)
    brands = sorted({b["name"] for b in all_json.get("brands", []) if b.get("name")})
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"built_at": time.time(), "index": index, "brands": brands}))
    except Exception:
        logger.warning("Failed to write OFD cache file", exc_info=True)
    return index, brands


async def _ensure_loaded(force: bool = False) -> None:
    global _index, _brands, _index_loaded_at
    if _index is not None and not force and (time.time() - _index_loaded_at) < OFD_TTL_SECONDS:
        return
    async with _refresh_lock:
        # Re-check after acquiring the lock — another request may have
        # already refreshed while we were waiting.
        if _index is not None and not force and (time.time() - _index_loaded_at) < OFD_TTL_SECONDS:
            return
        loaded = None if force else _load_cached()
        if loaded is None:
            loaded = await _refresh()
        _index, _brands = loaded
        _index_loaded_at = time.time()


async def get_index(force: bool = False) -> dict[str, dict]:
    """Return the barcode -> fields index (memory -> disk cache -> download)."""
    await _ensure_loaded(force)
    return _index or {}


async def get_brands() -> list[str]:
    """Return the OFD brand-name list (for data-driven brand detection in OCR parsing)."""
    try:
        await _ensure_loaded()
    except Exception:
        logger.warning("OFD brand list unavailable", exc_info=True)
        return []
    return _brands or []


async def lookup(barcode: str) -> dict | None:
    """Return filament fields for a barcode from the OFD, or None if not found."""
    idx = await get_index()
    return idx.get(canon(barcode))


async def refresh_database() -> int:
    """Force a re-download of the OFD dump regardless of TTL. Returns the entry count."""
    idx = await get_index(force=True)
    return len(idx)
