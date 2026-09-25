"""Barcode resolution + code routing: the one engine every barcode path shares.

A scanned/typed code resolves through one chain — the user's own inventory
first (instant, offline, exact), then the Open Filament Database, then
SpoolmanDB-Community — and, on create, routes into the spool's typed code
columns (``gtin_code`` / ``asin_code`` / ``sku_code`` / ``other_code``) with
same-package siblings cross-filled from the community databases.

Living in the services layer (rather than inside ``routes/inventory.py``)
is deliberate: the web inventory routes, the SpoolBuddy scan endpoint, CSV
import, and Spoolman-mode writes all need these functions, and during PR
#1895's review the cross-route private imports and per-path reimplementations
this replaces were a recurring source of drift bugs (scan and save
classifying the same code differently, paths missing the lookup toggle).

The ``barcode_lookup_enabled`` setting gates every external call in exactly
one place — ``external_all_codes`` (and the routing helper, which funnels
through the same flag) — so with the toggle off, saving a spool that carries
a code must not download anything: on a first-ever offline instance the
OFD/tarball timeouts would otherwise block that save for minutes under the
refresh lock.

Callers supply the settings map and (when Spoolman mode is active) the
SpoolmanClient — loading those is request-plumbing that stays in the routes
layer (``_load_settings_map`` / ``_ensure_spoolman_client``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool
from backend.app.schemas.spool import classify_code
from backend.app.services import ofd_client, spoolmandb_community_client

if TYPE_CHECKING:
    from backend.app.services.spoolman import SpoolmanClient

logger = logging.getLogger(__name__)

# The fixed field set a lookup can prefill on the add-spool form, whatever
# source it resolved from. Shared by the resolver, the SpoolBuddy catalog
# search, and the lookup endpoints.
BARCODE_FIELD_KEYS = (
    "material",
    "brand",
    "subtype",
    "color_name",
    "rgba",
    "label_weight",
    "nozzle_temp_min",
    "nozzle_temp_max",
)


def barcode_lookup_enabled(settings: dict[str, str]) -> bool:
    """Whether external (OFD / SpoolmanDB-Community) lookups are allowed."""
    return settings.get("barcode_lookup_enabled", "true") == "true"


def codes_for_spool(spool: Spool) -> list[dict]:
    """The typed code columns of one spool as lookup/display code dicts.

    Every stored code carries the roll's ``bought_as_refill`` — they all name
    the same purchasable package, so the flag is shared. ``kind`` stays in the
    "gtin" | "sku" vocabulary of LinkedCode (ASINs/user codes file as "sku",
    matching how the community databases file them).
    """
    codes: list[dict] = []
    if spool.gtin_code:
        codes.append({"code": spool.gtin_code, "kind": "gtin", "is_refill": spool.bought_as_refill})
    for value in (spool.sku_code, spool.asin_code, spool.other_code):
        if value:
            codes.append({"code": value, "kind": "sku", "is_refill": spool.bought_as_refill})
    return codes


async def external_all_codes(code: str, kind: str, settings: dict[str, str]) -> tuple[dict, str, list[dict]] | None:
    """Cross-reference OFD and SpoolmanDB-Community for `code`, merging both hits.

    Returns (fields, source, all_codes) where `source` is whichever database
    resolved first, `fields` prefers that source's values but fills any gaps
    (e.g. missing nozzle temps) from the other, and `all_codes` is the union
    of every sibling code (other package-size GTINs, the refill GTIN, the
    SKU/article number) discovered across both databases. If only one
    database resolves `code` directly, its sibling codes are also probed
    against the *other* database to recover cross-referenced fields/codes.

    Returns None without any network/cache activity when the
    ``barcode_lookup_enabled`` setting is off — this is THE gate, sitting in
    the one function every external-lookup path funnels through.
    """
    if not barcode_lookup_enabled(settings):
        return None

    lookup_kind = "gtin" if kind == "gtin" else "sku"  # the DBs file ASINs under their SKU fields

    async def _ofd_lookup(c: str, k: str) -> tuple[dict, list[dict]] | None:
        return await (ofd_client.lookup(c) if k == "gtin" else ofd_client.lookup_article(c))

    async def _smdb_lookup(c: str, k: str) -> tuple[dict, list[dict]] | None:
        return await (
            spoolmandb_community_client.lookup(c) if k == "gtin" else spoolmandb_community_client.lookup_sku(c)
        )

    try:
        ofd_hit = await _ofd_lookup(code, lookup_kind)
    except Exception:
        logger.warning("OFD lookup failed for %s", code, exc_info=True)
        ofd_hit = None
    try:
        smdb_hit = await _smdb_lookup(code, lookup_kind)
    except Exception:
        logger.warning("SpoolmanDB-Community lookup failed for %s", code, exc_info=True)
        smdb_hit = None

    if not ofd_hit and not smdb_hit:
        return None

    fields: dict = {}
    all_codes: list[dict] = []
    source: str | None = None

    def _merge(hit: tuple[dict, list[dict]], src_name: str) -> None:
        nonlocal source
        hit_fields, hit_codes = hit
        for key, value in hit_fields.items():
            if value is not None and fields.get(key) is None:
                fields[key] = value
        for entry in hit_codes:
            if not any(existing["code"] == entry["code"] for existing in all_codes):
                all_codes.append(entry)
        if source is None:
            source = src_name

    if ofd_hit:
        _merge(ofd_hit, "ofd")
    if smdb_hit:
        _merge(smdb_hit, "spoolmandb-community")

    tried = {code}
    for entry in list(all_codes):
        if ofd_hit and smdb_hit:
            break
        sibling_code = entry["code"]
        if sibling_code in tried:
            continue
        tried.add(sibling_code)
        if not ofd_hit:
            try:
                probe = await _ofd_lookup(sibling_code, entry["kind"])
            except Exception:
                probe = None
            if probe:
                _merge(probe, "ofd")
                ofd_hit = probe
        if not smdb_hit:
            try:
                probe = await _smdb_lookup(sibling_code, entry["kind"])
            except Exception:
                probe = None
            if probe:
                _merge(probe, "spoolmandb-community")
                smdb_hit = probe

    return fields, source, all_codes


async def resolve_barcode(
    db: AsyncSession,
    code: str,
    kind: str,
    settings: dict[str, str],
    spoolman_client: SpoolmanClient | None = None,
) -> tuple[dict, str | None, list[dict]]:
    """Resolve a classified code (see `classify_code`): the user's own inventory
    first, then OFD, then SpoolmanDB-Community, cross-referencing between the
    two external databases along the way.

    When Spoolman mode is active (caller passes its client), "the user's own
    inventory" means Spoolman's spools (codes stored under extras — see
    SpoolmanClient.find_spool_by_barcode) since that's where the visible
    inventory actually lives; otherwise it means the local ``Spool`` table's
    typed code columns. Falls back to OFD, then SpoolmanDB-Community, if
    barcode_lookup_enabled — OFD stays first since it's purpose-built for
    barcode lookups; SpoolmanDB-Community's coverage is far sparser in
    barcodes but broader in brands, so it's a secondary fallback, not a
    replacement.

    Returns (fields, source, all_codes). ``source`` is "inventory", "ofd",
    "spoolmandb-community", or None (no match). An inventory hit's
    ``all_codes`` are the matched spool's own stored codes.
    """
    if spoolman_client:
        try:
            spool = await spoolman_client.find_spool_by_barcode(code)
        except Exception:
            logger.warning("Spoolman barcode lookup failed for %s", code, exc_info=True)
            spool = None
        if spool:
            # Lazy one-way exception to routes-never-imported-by-services:
            # _map_spoolman_spool is the single shared Spoolman-dict -> fields
            # mapping and lives with the Spoolman routes; importing it lazily
            # here avoids duplicating that mapping while keeping module import
            # graphs acyclic.
            from backend.app.api.routes._spoolman_helpers import _map_spoolman_spool

            mapped = _map_spoolman_spool(spool)
            fields = {key: mapped.get(key) for key in BARCODE_FIELD_KEYS}
            return fields, "inventory", mapped.get("linked_codes") or []
    else:
        # Match on the code string across ALL typed columns — whichever column
        # it lives in, the same physical string identifies the same package.
        # Newest roll wins so the freshest user edits become the template.
        # other_code compares case-insensitively: it stores the user's casing
        # verbatim, while the scanned side arrives canonicalized (uppercased).
        result = await db.execute(
            select(Spool)
            .where(
                or_(
                    Spool.gtin_code == code,
                    Spool.sku_code == code,
                    Spool.asin_code == code,
                    func.upper(Spool.other_code) == code,
                )
            )
            .order_by(Spool.created_at.desc())
            .limit(1)
        )
        existing = result.scalars().first()
        if existing:
            fields = {key: getattr(existing, key) for key in BARCODE_FIELD_KEYS}
            return fields, "inventory", codes_for_spool(existing)

    external = await external_all_codes(code, kind, settings)
    if external is None:
        return {}, None, []
    return external


def _is_asin_shaped(code: str | None) -> bool:
    if not code:
        return False
    return classify_code(code)[1] == "asin"


def _pick_same_package_gtin(codes: list[dict], bought_as_refill: bool) -> str | None:
    """Choose a GTIN from a same-package code set, preferring the one whose
    refill flag matches how this roll was bought (a SpoolmanDB variant lists
    the with-spool EAN and the refill EAN side by side)."""
    gtins = [c for c in codes if c.get("kind") == "gtin" and c.get("code")]
    for c in gtins:
        if bool(c.get("is_refill")) == bought_as_refill:
            return c["code"]
    return gtins[0]["code"] if gtins else None


def _pick_same_package_sku(codes: list[dict], scanned: str, want_asin: bool) -> str | None:
    for c in codes:
        value = c.get("code") or ""
        if not value or value == scanned or c.get("kind") == "gtin":
            continue
        if _is_asin_shaped(value) == want_asin:
            return value
    return None


async def route_scanned_code(
    scanned: str | None,
    settings: dict[str, str],
    bought_as_refill: bool = False,
    symbology: str | None = None,
) -> dict[str, str | None]:
    """Route a raw scanned code into the typed spool columns.

    Classification ladder: GTIN (structural checksum) → ASIN (B0-shape) →
    SKU *candidate*, promoted to ``sku_code`` only when a community database
    actually knows it — otherwise it lands in ``other_code`` verbatim
    (trimmed, case preserved: that column is the user's own code space).

    Sibling columns cross-fill under the size-consistency rule: OFD supplies
    the code printed on the SAME size row (its gtin↔article pairing);
    SpoolmanDB variants are per-package already, with the with-spool vs
    refill EAN chosen by ``bought_as_refill``. When neither database can
    prove same-package, the sibling column stays None — never guess a
    different package's code onto this roll.

    Returns a dict with the four ``*_code`` keys (values or None). Callers
    merge it under any explicitly-supplied columns (explicit always wins).
    """
    routed: dict[str, str | None] = {
        "gtin_code": None,
        "asin_code": None,
        "sku_code": None,
        "other_code": None,
    }
    if not scanned or not scanned.strip():
        return routed
    # `symbology` carries the scanner's AIM hint from scan time so routing at
    # create time can't re-promote a code the scan already demoted (a Code 128
    # numeric with a lucky checksum must not become a GTIN here).
    canonical, kind = classify_code(scanned, symbology=symbology)
    if not canonical:
        return routed

    lookup_kind = "gtin" if kind == "gtin" else "sku"

    ofd_known = False
    ofd_paired: str | None = None
    smdb_codes: list[dict] = []
    smdb_known = False
    if barcode_lookup_enabled(settings):
        try:
            ofd_known, ofd_paired = await ofd_client.same_package_code(canonical, lookup_kind)
        except Exception:
            logger.warning("OFD same-package lookup failed for %s", canonical, exc_info=True)
        try:
            smdb_hit = await (
                spoolmandb_community_client.lookup(canonical)
                if lookup_kind == "gtin"
                else spoolmandb_community_client.lookup_sku(canonical)
            )
        except Exception:
            logger.warning("SpoolmanDB-Community lookup failed for %s", canonical, exc_info=True)
            smdb_hit = None
        if smdb_hit:
            smdb_known = True
            smdb_codes = smdb_hit[1] or []

    def _fill_from_paired_or_smdb(*, want_gtin: bool, want_asin: bool) -> None:
        if want_gtin and routed["gtin_code"] is None:
            if ofd_paired and classify_code(ofd_paired)[1] == "gtin":
                routed["gtin_code"] = ofd_paired
            else:
                routed["gtin_code"] = _pick_same_package_gtin(smdb_codes, bought_as_refill)
        if routed["sku_code"] is None:
            if ofd_paired and classify_code(ofd_paired)[1] == "sku":
                routed["sku_code"] = ofd_paired
            else:
                routed["sku_code"] = _pick_same_package_sku(smdb_codes, canonical, want_asin=False)
        if want_asin and routed["asin_code"] is None:
            if ofd_paired and _is_asin_shaped(ofd_paired):
                routed["asin_code"] = ofd_paired
            else:
                routed["asin_code"] = _pick_same_package_sku(smdb_codes, canonical, want_asin=True)

    if kind == "gtin":
        routed["gtin_code"] = canonical
        _fill_from_paired_or_smdb(want_gtin=False, want_asin=True)
    elif kind == "asin":
        routed["asin_code"] = canonical
        _fill_from_paired_or_smdb(want_gtin=True, want_asin=False)
    else:
        if ofd_known or smdb_known:
            routed["sku_code"] = canonical
            _fill_from_paired_or_smdb(want_gtin=True, want_asin=True)
        else:
            # Unknown everywhere: the user's own code space, stored verbatim
            # (trimmed). Still matched on future scans via the inventory rung.
            routed["other_code"] = scanned.strip()
    return routed
