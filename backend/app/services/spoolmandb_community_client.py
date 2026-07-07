"""SpoolmanDB-Community filament database client.

SpoolmanDB-Community (https://github.com/Icezaza2543/SpoolmanDB-Community, a
community-maintained continuation of Donkie/SpoolmanDB) publishes a much
broader brand/material/colour catalog than the Open Filament Database (OFD),
and a subset of its entries also carry EAN/GTIN retail barcodes and/or
manufacturer SKUs. Real barcode coverage is sparse compared to OFD (which is
purpose-built for barcode lookups), so this client is consulted as a fallback
*after* OFD, not instead of it — see `_resolve_barcode` in
`backend/app/api/routes/inventory.py`.

The compiled `filaments.json` this project publishes on GitHub Pages does
NOT carry `color_name` as its own field (it's already baked into the `name`
string at compile time, and the `{color_name}` placeholder's position isn't
fixed across manufacturers, so it can't be reliably recovered afterwards).
The raw per-manufacturer source files (`filaments/*.json` in the repo) DO
have exact `color.name` alongside `color.eans`/`color.eans_refill`/
`color.codes`, so this client downloads the whole repo as a tarball and
parses those source files directly instead of fetching the compiled JSON.

Each color's `eans` (retail-pack GTINs), `eans_refill` (refill-pack GTINs),
and `codes` (manufacturer SKUs) are all siblings of the same physical
product — a hit on any one of them (via `lookup`/`lookup_sku`) also returns
every other code for that color, letting a scan of an *unfamiliar* code
still resolve once *any* of its siblings has been seen before.

Caches the downloaded+parsed variant list and the built indexes on disk with
a 24h TTL — refreshed lazily on the next lookup once stale, same pattern as
`ofd_client.py` (this backend has no scheduler/cron).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import tarfile
import time
from pathlib import Path

import httpx

from backend.app.core.paths import resolve_data_dir

logger = logging.getLogger(__name__)

SPOOLMANDB_COMMUNITY_TARBALL_URL = "https://codeload.github.com/Icezaza2543/SpoolmanDB-Community/tar.gz/refs/heads/main"
SPOOLMANDB_COMMUNITY_TTL_SECONDS = 24 * 3600

# Bump whenever the on-disk cache shape changes, so an old cache file (e.g.
# pre-dating codes/SKU support and the gtin/sku index split) is treated as
# stale and rebuilt instead of being misread.
_CACHE_VERSION = 2

# Same fixed field set `_resolve_barcode` reads off any lookup source
# (duplicated here rather than imported from the routes module — services
# shouldn't depend on routes; this mirrors the repo's established convention
# of small shared constants being duplicated per-layer, see `ofd_client.canon()`
# vs `schemas/spool.py`'s `normalize_barcode()`).
_BARCODE_FIELD_KEYS = (
    "material",
    "brand",
    "subtype",
    "color_name",
    "rgba",
    "label_weight",
    "nozzle_temp_min",
    "nozzle_temp_max",
)

# In-process cache so we don't re-download/rebuild on every request.
_gtin_index: dict[str, dict] | None = None
_sku_index: dict[str, dict] | None = None
_variants: list[dict] | None = None
_brands: list[str] | None = None
_index_loaded_at = 0.0
_refresh_lock = asyncio.Lock()


def _cache_path() -> Path:
    return resolve_data_dir() / "spoolmandb_community_cache.json"


def canon(barcode: str) -> str:
    """Canonical GTIN form for matching: digits only, leading zeros stripped.

    Duplicated from `ofd_client.canon()` / `schemas/spool.py`'s
    `normalize_barcode()` deliberately, to keep this client decoupled from
    the OFD client (either can be swapped/removed independently).
    """
    digits = re.sub(r"\D", "", barcode or "")
    return digits.lstrip("0") or "0"


def _hex_to_rgba(color_hex) -> str | None:
    """Accept a single hex string or a list (multi-color `hexes`) and return RRGGBBAA."""
    if isinstance(color_hex, list):
        color_hex = color_hex[0] if color_hex else None
    if not isinstance(color_hex, str):
        return None
    h = color_hex.lstrip("#")
    if len(h) == 6 and re.fullmatch(r"[0-9A-Fa-f]{6}", h):
        return h.upper() + "FF"  # RRGGBBAA, opaque
    return None


def _subtype_from_template(name_template: str) -> str | None:
    """Best-effort subtype: the raw (pre-substitution) name minus the {color_name} token.

    Unlike OFD's `_subtype_from`, which regex-strips a known material word out
    of an already-substituted name (a heuristic guess), SpoolmanDB-Community's
    raw source `name` field still contains the literal `{color_name}`
    placeholder before compilation — so this is a direct, reliable removal,
    not a guess.
    """
    if not name_template:
        return None
    s = name_template.replace("{color_name}", "")
    s = re.sub(r"\s+", " ", s).strip(" -+")
    return s or None


def _extruder_temps(filament: dict) -> tuple[int | None, int | None]:
    temp_range = filament.get("extruder_temp_range")
    if isinstance(temp_range, list) and len(temp_range) == 2:
        try:
            return int(temp_range[0]), int(temp_range[1])
        except (TypeError, ValueError):
            pass
    single = filament.get("extruder_temp")
    if single is not None:
        try:
            t = int(single)
            return t, t
        except (TypeError, ValueError):
            pass
    return None, None


def _parse_manufacturer_file(manufacturer: str, data: dict) -> list[dict]:
    """Expand one manufacturer source file into flat (filament, color) variant dicts.

    Deliberately NOT crossed with `weights`/`diameters` (unlike SpoolmanDB-Community's
    own compiler) — barcode/catalog fields don't need that multiplication, and a
    color's `eans`/`eans_refill`/`codes` aren't associated with a specific weight anyway.
    """
    variants: list[dict] = []
    for filament in data.get("filaments", []):
        material = filament.get("material") or ""
        name_template = filament.get("name") or ""
        subtype = _subtype_from_template(name_template)
        weights = filament.get("weights") or []
        label_weight = None
        if weights:
            try:
                label_weight = int(round(float(weights[0]["weight"])))
            except (TypeError, ValueError, KeyError):
                label_weight = None
        nozzle_temp_min, nozzle_temp_max = _extruder_temps(filament)

        for color in filament.get("colors", []):
            color_name = color.get("name")
            hexes = color.get("hexes")
            rgba = _hex_to_rgba(color.get("hex") or hexes)
            eans = color.get("eans") or []
            eans_refill = color.get("eans_refill") or []
            codes = color.get("codes") or []

            variants.append(
                {
                    "manufacturer": manufacturer,
                    "material": material,
                    "brand": manufacturer,
                    "subtype": subtype,
                    "color_name": color_name,
                    "rgba": rgba,
                    "hexes": hexes,
                    "label_weight": label_weight,
                    "nozzle_temp_min": nozzle_temp_min,
                    "nozzle_temp_max": nozzle_temp_max,
                    "finish": color.get("finish", filament.get("finish")),
                    "pattern": color.get("pattern", filament.get("pattern")),
                    "translucent": color.get("translucent", filament.get("translucent")),
                    "glow": color.get("glow", filament.get("glow")),
                    "multi_color_direction": color.get("multi_color_direction", filament.get("multi_color_direction")),
                    "eans": eans,
                    "eans_refill": eans_refill,
                    "codes": codes,
                }
            )
    return variants


async def _download_and_parse_variants() -> list[dict]:
    """Download the SpoolmanDB-Community repo tarball and parse every manufacturer source file."""
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        resp = await client.get(SPOOLMANDB_COMMUNITY_TARBALL_URL)
        resp.raise_for_status()
        raw = resp.content

    variants: list[dict] = []
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            # Tarball root is "SpoolmanDB-Community-<ref>/filaments/<manufacturer>.json"
            parts = Path(member.name).parts
            if len(parts) < 2 or parts[-2] != "filaments" or not member.name.endswith(".json"):
                continue
            extracted = tar.extractfile(member)
            if not extracted:
                continue
            try:
                data = json.loads(extracted.read())
            except (json.JSONDecodeError, ValueError):
                logger.warning("Skipping malformed SpoolmanDB-Community source file: %s", member.name)
                continue
            manufacturer = data.get("manufacturer")
            if not manufacturer:
                continue
            variants.extend(_parse_manufacturer_file(manufacturer, data))
    return variants


def _all_codes_for(variant: dict) -> list[dict]:
    """Every GTIN/SKU sibling for one color: eans + eans_refill + codes (SKUs)."""
    codes: list[dict] = []
    for barcode in variant.get("eans", []):
        codes.append({"code": canon(barcode), "kind": "gtin", "is_refill": False})
    for barcode in variant.get("eans_refill", []):
        codes.append({"code": canon(barcode), "kind": "gtin", "is_refill": True})
    for sku in variant.get("codes", []):
        if not isinstance(sku, str) or not sku.strip():
            continue
        codes.append({"code": sku.strip().upper(), "kind": "sku", "is_refill": False})
    return codes


def _build_index(variants: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    """Build (gtin_index, sku_index) from every variant's eans/eans_refill/codes.

    Both map a canonicalized/normalized code to ``{"fields": {...}, "all_codes": [...]}``
    — ``all_codes`` lists every sibling code for that same color (see `_all_codes_for`),
    so a hit on any one of them can recover the rest.
    """
    gtin_index: dict[str, dict] = {}
    sku_index: dict[str, dict] = {}
    for variant in variants:
        fields = {key: variant.get(key) for key in _BARCODE_FIELD_KEYS}
        all_codes = _all_codes_for(variant)
        if not all_codes:
            continue
        entry = {"fields": fields, "all_codes": all_codes}
        for c in all_codes:
            if c["kind"] == "gtin":
                gtin_index[c["code"]] = entry
            else:
                sku_index[c["code"]] = entry
    return gtin_index, sku_index


def _load_cached() -> tuple[dict, dict, list, list] | None:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if data.get("cache_version") != _CACHE_VERSION:
            return None
        if time.time() - data.get("built_at", 0) > SPOOLMANDB_COMMUNITY_TTL_SECONDS:
            return None
        return (
            data.get("gtin_index", {}),
            data.get("sku_index", {}),
            data.get("brands", []),
            data.get("variants", []),
        )
    except Exception:
        return None


async def _refresh() -> tuple[dict, dict, list, list]:
    """Download + parse the repo tarball; build the indexes + brand list; cache all of it."""
    variants = await _download_and_parse_variants()
    gtin_index, sku_index = _build_index(variants)
    brands = sorted({v["manufacturer"] for v in variants if v.get("manufacturer")})
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "cache_version": _CACHE_VERSION,
                    "built_at": time.time(),
                    "gtin_index": gtin_index,
                    "sku_index": sku_index,
                    "brands": brands,
                    "variants": variants,
                }
            )
        )
    except Exception:
        logger.warning("Failed to write SpoolmanDB-Community cache file", exc_info=True)
    return gtin_index, sku_index, brands, variants


async def _ensure_loaded(force: bool = False) -> None:
    global _gtin_index, _sku_index, _brands, _variants, _index_loaded_at
    if _gtin_index is not None and not force and (time.time() - _index_loaded_at) < SPOOLMANDB_COMMUNITY_TTL_SECONDS:
        return
    async with _refresh_lock:
        if (
            _gtin_index is not None
            and not force
            and (time.time() - _index_loaded_at) < SPOOLMANDB_COMMUNITY_TTL_SECONDS
        ):
            return
        loaded = None if force else _load_cached()
        if loaded is None:
            loaded = await _refresh()
        _gtin_index, _sku_index, _brands, _variants = loaded
        _index_loaded_at = time.time()


async def get_gtin_index() -> dict[str, dict]:
    """Return the canonical-GTIN -> {fields, all_codes} index (memory -> disk cache -> download)."""
    await _ensure_loaded()
    return _gtin_index or {}


async def get_sku_index() -> dict[str, dict]:
    """Return the normalized-SKU -> {fields, all_codes} index."""
    await _ensure_loaded()
    return _sku_index or {}


async def get_brands() -> list[str]:
    """Return the SpoolmanDB-Community manufacturer list."""
    try:
        await _ensure_loaded()
    except Exception:
        logger.warning("SpoolmanDB-Community brand list unavailable", exc_info=True)
        return []
    return _brands or []


async def get_filaments() -> list[dict]:
    """Return the full flat variant list (for the color-catalog sync endpoint)."""
    await _ensure_loaded()
    return _variants or []


async def lookup(barcode: str) -> tuple[dict, list[dict]] | None:
    """Resolve a GTIN barcode: (fields, all_codes) for its color, or None if not found."""
    idx = await get_gtin_index()
    entry = idx.get(canon(barcode))
    if not entry:
        return None
    return entry["fields"], entry["all_codes"]


async def lookup_sku(code: str) -> tuple[dict, list[dict]] | None:
    """Resolve a manufacturer SKU the same way `lookup` resolves a GTIN."""
    idx = await get_sku_index()
    entry = idx.get((code or "").strip().upper())
    if not entry:
        return None
    return entry["fields"], entry["all_codes"]


async def refresh_database() -> int:
    """Force a re-download of the repo tarball regardless of TTL. Returns the combined entry count."""
    await _ensure_loaded(force=True)
    gtin_idx = await get_gtin_index()
    sku_idx = await get_sku_index()
    return len(gtin_idx) + len(sku_idx)
