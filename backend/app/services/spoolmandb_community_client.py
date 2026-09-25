"""SpoolmanDB-Community filament database client.

SpoolmanDB-Community (https://github.com/Icezaza2543/SpoolmanDB-Community, a
community-maintained continuation of Donkie/SpoolmanDB) publishes a much
broader brand/material/colour catalog than the Open Filament Database (OFD),
and a subset of its entries also carry EAN/GTIN retail barcodes and/or
manufacturer SKUs. Real barcode coverage is sparse compared to OFD (which is
purpose-built for barcode lookups), so this client is consulted as a fallback
*after* OFD, not instead of it — see
``backend/app/services/barcode_resolver.py``.

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

Download/cache mechanics live on the shared spine in
``external_catalog_cache.py``; this client additionally ships a build-time
seed snapshot (see ``backend/scripts/seed_spoolmandb_community_cache.py``)
so air-gapped installs still get catalog coverage.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import tarfile
from pathlib import Path

from backend.app.services.external_catalog_cache import CachedCatalogClient, canon, hex_to_rgba, stream_download

logger = logging.getLogger(__name__)

SPOOLMANDB_COMMUNITY_TARBALL_URL = "https://codeload.github.com/Icezaza2543/SpoolmanDB-Community/tar.gz/refs/heads/main"

# The real tarball is ~13 MB and each per-manufacturer source file is a few
# KB. These caps guard the 24h auto-refresh against a malformed or
# maliciously huge upstream response OOMing the backend - well above real
# usage, but firm enough to abort instead of buffering an unbounded body.
_MAX_TARBALL_BYTES = 64 * 1024 * 1024
_MAX_MEMBER_BYTES = 8 * 1024 * 1024
# The per-member cap alone doesn't bound the sum across many members - a
# tarball with thousands of entries each just under _MAX_MEMBER_BYTES would
# still decompress to a huge total in memory. This is the residual
# decompression-bomb guard on top of that.
_MAX_TOTAL_DECOMPRESSED_BYTES = 256 * 1024 * 1024

# Same fixed field set the barcode resolver reads off any lookup source.
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


def _as_list(value) -> list:
    """Container-type guard: an upstream file shipping a plain string where a
    list belongs (e.g. ``"eans": "6938936716785"``) would otherwise iterate
    character by character — and every single character passes an entry-level
    string check, indexing one-character garbage codes. Anything that isn't a
    real list contributes nothing instead."""
    return value if isinstance(value, list) else []


def _parse_manufacturer_file(manufacturer: str, data: dict) -> list[dict]:
    """Expand one manufacturer source file into flat (filament, color) variant dicts.

    Deliberately NOT crossed with `weights`/`diameters` (unlike SpoolmanDB-Community's
    own compiler) — barcode/catalog fields don't need that multiplication, and a
    color's `eans`/`eans_refill`/`codes` aren't associated with a specific weight anyway.
    """
    variants: list[dict] = []
    for filament in _as_list(data.get("filaments")):
        if not isinstance(filament, dict):
            continue
        material = filament.get("material") or ""
        name_template = filament.get("name") or ""
        subtype = _subtype_from_template(name_template)
        weights = _as_list(filament.get("weights"))
        label_weight = None
        if weights:
            try:
                label_weight = int(round(float(weights[0]["weight"])))
            except (TypeError, ValueError, KeyError, IndexError):
                label_weight = None
        nozzle_temp_min, nozzle_temp_max = _extruder_temps(filament)

        for color in _as_list(filament.get("colors")):
            if not isinstance(color, dict):
                continue
            color_name = color.get("name")
            hexes = color.get("hexes")
            rgba = hex_to_rgba(color.get("hex") or hexes)

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
                    "eans": _as_list(color.get("eans")),
                    "eans_refill": _as_list(color.get("eans_refill")),
                    "codes": _as_list(color.get("codes")),
                }
            )
    return variants


def _parse_tarball(raw: bytes) -> list[dict]:
    """Unpack the repo tarball and parse every per-manufacturer source file."""
    variants: list[dict] = []
    total_decompressed = 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            # Tarball root is "SpoolmanDB-Community-<ref>/filaments/<manufacturer>.json"
            parts = Path(member.name).parts
            if len(parts) < 2 or parts[-2] != "filaments" or not member.name.endswith(".json"):
                continue
            if member.size > _MAX_MEMBER_BYTES:
                logger.warning(
                    "Skipping oversized SpoolmanDB-Community source file %s (%d bytes)", member.name, member.size
                )
                continue
            extracted = tar.extractfile(member)
            if not extracted:
                continue
            # Belt-and-braces against a tar header that understates the real
            # member size: read one byte past the cap and bail if it's there.
            content = extracted.read(_MAX_MEMBER_BYTES + 1)
            if len(content) > _MAX_MEMBER_BYTES:
                logger.warning("Skipping SpoolmanDB-Community source file %s - exceeds size cap", member.name)
                continue
            total_decompressed += len(content)
            if total_decompressed > _MAX_TOTAL_DECOMPRESSED_BYTES:
                raise ValueError(
                    f"SpoolmanDB-Community tarball's decompressed contents exceeded "
                    f"{_MAX_TOTAL_DECOMPRESSED_BYTES} byte cap - aborting"
                )
            try:
                data = json.loads(content)
            except (json.JSONDecodeError, ValueError):
                logger.warning("Skipping malformed SpoolmanDB-Community source file: %s", member.name)
                continue
            if not isinstance(data, dict):
                continue
            manufacturer = data.get("manufacturer")
            if not manufacturer or not isinstance(manufacturer, str):
                continue
            variants.extend(_parse_manufacturer_file(manufacturer, data))
    return _dedupe_variants(variants)


def _dedupe_variants(variants: list[dict]) -> list[dict]:
    """Drop byte-identical variants — upstream lists some colors twice (the
    2026-08 snapshot carried 33 exact duplicates), which surfaced as
    indistinguishable twin rows in catalog search."""
    seen: set[str] = set()
    unique: list[dict] = []
    for variant in variants:
        key = json.dumps(variant, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            unique.append(variant)
    return unique


def codes_for_variant(variant: dict) -> list[dict]:
    """Public accessor: every GTIN/SKU sibling code for a flat variant dict
    (as returned by `get_filaments()`)."""
    return _all_codes_for(variant)


def _all_codes_for(variant: dict) -> list[dict]:
    """Every GTIN/SKU sibling for one color: eans + eans_refill + codes (SKUs)."""
    codes: list[dict] = []
    for barcode in _as_list(variant.get("eans")):
        # canon() assumes a string (re.sub raises TypeError on anything else) -
        # a single malformed upstream source file with a numeric EAN would
        # otherwise raise here and abort the whole refresh for every
        # manufacturer.
        if not isinstance(barcode, str) or not barcode.strip():
            continue
        codes.append({"code": canon(barcode), "kind": "gtin", "is_refill": False})
    for barcode in _as_list(variant.get("eans_refill")):
        if not isinstance(barcode, str) or not barcode.strip():
            continue
        codes.append({"code": canon(barcode), "kind": "gtin", "is_refill": True})
    for sku in _as_list(variant.get("codes")):
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


class _SpoolmanDbCommunityClient(CachedCatalogClient):
    name = "SpoolmanDB-Community"
    cache_filename = "spoolmandb_community_cache.json"
    # Bump whenever the payload shape changes, so an old cache file is treated
    # as stale and rebuilt instead of being misread.
    cache_version = 3

    def seed_path(self) -> Path:
        # backend/seeds/spoolmandb_community_seed.json — generated at Docker
        # image build time (never committed); see
        # backend/scripts/seed_spoolmandb_community_cache.py.
        return Path(__file__).resolve().parents[2] / "seeds" / "spoolmandb_community_seed.json"

    async def _download_and_build(self) -> dict:
        payload = await download_and_build_payload()
        return payload


async def download_and_build_payload() -> dict:
    """Download + parse the repo tarball into the cacheable payload dict.

    Module-level (rather than a client method) so the build-time seed script
    can reuse the exact same download/parse/build path the runtime uses —
    the seed is then guaranteed to have the same shape and version semantics
    as a real cache payload.
    """
    raw = await stream_download(
        SPOOLMANDB_COMMUNITY_TARBALL_URL, max_bytes=_MAX_TARBALL_BYTES, timeout=120.0, follow_redirects=True
    )
    # Tarball unpack + parse off the event loop: this runs inside some
    # unlucky user's request when the 24h TTL lapses, and a ~13 MB gzip
    # extraction shouldn't stall every other request meanwhile.
    variants = await asyncio.to_thread(_parse_tarball, raw)
    if not variants:
        # A 200 that parses to zero manufacturer files (e.g. the repo layout
        # changes and the path filter matches nothing) must not overwrite a
        # good cache with an empty one and silently return "no match" for
        # everyone for a full TTL — raising here reuses the shared spine's
        # stale-cache/seed fallback instead.
        raise RuntimeError("SpoolmanDB-Community refresh parsed zero manufacturer files - keeping previous cache")
    gtin_index, sku_index = await asyncio.to_thread(_build_index, variants)
    return {"gtin_index": gtin_index, "sku_index": sku_index, "variants": variants}


_client = _SpoolmanDbCommunityClient()

# The version + built_at wrapper the seed script must write around its payload.
SEED_CACHE_VERSION = _SpoolmanDbCommunityClient.cache_version


async def get_gtin_index() -> dict[str, dict]:
    """Return the canonical-GTIN -> {fields, all_codes} index (memory -> disk cache -> download -> seed)."""
    return (await _client.payload()).get("gtin_index", {})


async def get_sku_index() -> dict[str, dict]:
    """Return the normalized-SKU -> {fields, all_codes} index."""
    return (await _client.payload()).get("sku_index", {})


async def get_filaments() -> list[dict]:
    """Return the full flat variant list (for the SpoolBuddy catalog search)."""
    return (await _client.payload()).get("variants", [])


async def lookup(barcode: str) -> tuple[dict, list[dict]] | None:
    """Resolve a GTIN barcode: (fields, all_codes) for its color, or None if not found."""
    entry = (await get_gtin_index()).get(canon(barcode))
    if not entry:
        return None
    return entry["fields"], entry["all_codes"]


async def lookup_sku(code: str) -> tuple[dict, list[dict]] | None:
    """Resolve a manufacturer SKU the same way `lookup` resolves a GTIN."""
    entry = (await get_sku_index()).get((code or "").strip().upper())
    if not entry:
        return None
    return entry["fields"], entry["all_codes"]
