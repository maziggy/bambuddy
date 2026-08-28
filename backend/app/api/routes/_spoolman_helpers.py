"""Pure helper functions for Spoolman spool mapping.

No heavy dependencies — importable in unit tests without the full backend stack.
"""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any

from typing_extensions import TypedDict

from backend.app.api.routes._url_safety import assert_safe_lan_service_url

logger = logging.getLogger(__name__)


class MappedSpoolFields(TypedDict):
    """Full shape of the dict returned by _map_spoolman_spool (InventorySpool-compatible)."""

    id: int
    material: str | None
    subtype: str | None
    brand: str | None
    color_name: str | None
    color_name_is_synthesized: bool
    rgba: str | None
    label_weight: int | None
    core_weight: int | None
    core_weight_catalog_id: None
    weight_used: float | None
    weight_used_baseline: float | None
    weight_locked: bool
    last_scale_weight: None
    last_weighed_at: None
    slicer_filament: None
    slicer_filament_name: str | None
    nozzle_temp_min: int | None
    nozzle_temp_max: None
    note: str | None
    added_full: None
    last_used: str | None
    encode_time: str | None
    tag_uid: str | None
    tray_uuid: str | None
    data_origin: str | None
    tag_type: str | None
    archived_at: str | None
    created_at: str | None  # None when Spoolman spool has no registered timestamp
    updated_at: str | None
    cost_per_kg: float | None
    storage_location: str | None
    location_id: int | None
    k_profiles: list[Any]


class NormalizedVendorRef(TypedDict):
    """Vendor reference embedded in a NormalizedFilament."""

    id: int
    name: str


class NormalizedFilament(TypedDict):
    """Normalised Spoolman filament dict returned by the /filaments catalog endpoint."""

    id: int
    name: str
    material: str | None
    color_hex: str | None
    color_name: str | None
    weight: int | None
    spool_weight: float | None
    vendor: NormalizedVendorRef | None


def assert_safe_spoolman_url(url: str) -> None:
    """Raise ValueError if the Spoolman *url* should be blocked as an SSRF risk.

    Thin wrapper over the shared LAN-service policy — see
    ``_url_safety.assert_safe_lan_service_url`` for what is and isn't
    rejected, and why loopback/RFC-1918 are deliberately permitted (running
    Spoolman on the same host or home LAN is THE normal topology).

    Kept as a named function because the "Spoolman URL …" wording in its
    errors is user-facing and asserted by existing tests.
    """
    assert_safe_lan_service_url(url, label="Spoolman URL")


# Six characters, or eight when the filament carries an alpha byte. The write
# side stores eight only for genuinely translucent spools (#2912); rejecting
# them here turned every clear spool into neutral grey on read.
_COLOR_HEX_RE = re.compile(r"^[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?$")
_TAG_HEX_RE = re.compile(r"^[0-9A-F]+$")


def _safe_int(value: object, fallback: int) -> int:
    """Convert value to int, returning fallback for None/NaN/Inf/non-numeric."""
    try:
        f = float(value)  # type: ignore[arg-type]
        if math.isfinite(f):
            return int(f)
    except (TypeError, ValueError):
        pass
    return fallback


def _safe_float(value: object, fallback: float) -> float:
    """Convert value to float, returning fallback for None/NaN/Inf/non-numeric."""
    try:
        f = float(value)  # type: ignore[arg-type]
        if math.isfinite(f):
            return f
    except (TypeError, ValueError):
        pass
    return fallback


def _safe_optional_float(value: object) -> float | None:
    """Convert value to finite float, or None if missing/NaN/Infinite/non-numeric.

    Used for optional monetary fields (price) to prevent Infinity/NaN from
    reaching JSON serialisation, which raises ValueError with allow_nan=False.
    """
    if value is None:
        return None
    try:
        f = float(value)  # type: ignore[arg-type]
        if math.isfinite(f):
            return f
    except (TypeError, ValueError):
        pass
    return None


def _extract_extra_str(extra: dict, key: str) -> str:
    """Extract a JSON-encoded string from a Spoolman extra dict.

    Spoolman stores extra values as JSON-stringified text — a stored string
    "GFL05" appears as `'"GFL05"'` (six chars including the quotes). This
    unwraps that, returning the bare string. Returns "" for missing keys,
    non-strings, or invalid JSON.
    """
    raw = extra.get(key)
    if not isinstance(raw, str):
        return ""
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # Tolerate bare-string values written without JSON encoding.
        return raw
    return decoded if isinstance(decoded, str) else ""


# Spool.extra key holding the consumed-counter baseline: the value Spoolman's
# used_weight had when the user last pressed "Reset usage to 0". Displayed
# consumed is used_weight minus this, which is what internal mode has always
# done with its own weight_used_baseline column (#1644). See #2906.
BAMBU_WEIGHT_USED_BASELINE_KEY = "bambu_weight_used_baseline"


def _extract_extra_float(extra: dict, key: str) -> float | None:
    """Extract a JSON-encoded number from a Spoolman extra dict.

    Same storage convention as :func:`_extract_extra_str` — Spoolman keeps
    extra values as JSON text, so 250.0 is stored as ``"250.0"``. Returns
    ``None`` for missing keys, non-finite values, or anything that does not
    decode to a number, so the caller can tell "no baseline recorded" from
    "a baseline of zero".
    """
    raw = extra.get(key)
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = float(raw)
    elif isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None
        if isinstance(decoded, str):
            # Spoolman's extra fields default to field_type "text", which only
            # accepts values that decode to a str, so numbers written through
            # it arrive as '"263.0"' rather than '263.0'. Both spellings have
            # to read back the same, or a baseline written by the reset would
            # be invisible to the code that subtracts it.
            try:
                value = float(decoded)
            except ValueError:
                return None
        elif isinstance(decoded, (int, float)) and not isinstance(decoded, bool):
            value = float(decoded)
        else:
            return None
    else:
        return None
    return value if math.isfinite(value) else None


def _map_spoolman_spool(spool: dict) -> MappedSpoolFields:
    """Convert a raw Spoolman spool dict to the InventorySpool-compatible format.

    Fields not supported by Spoolman (k_profiles, slicer_filament, …) are
    returned as None / empty so the frontend can still render them without
    errors.  The ``data_origin`` field is set to ``"spoolman"`` so UI code can
    distinguish these spools from local ones.
    """
    raw_id = spool.get("id")
    if raw_id is None:
        raise ValueError("Spoolman spool is missing required 'id' field")
    try:
        spool_id: int = int(raw_id)
    except (TypeError, ValueError):
        raise ValueError(f"Spoolman spool 'id' is not a valid integer: {raw_id!r}")
    if spool_id <= 0:
        raise ValueError(f"Spoolman spool 'id' must be a positive integer, got {spool_id}")

    filament: dict = spool.get("filament") or {}
    if not filament:
        logger.warning(
            "Spoolman spool %s has no filament data — all filament fields will use defaults",
            spool_id,
        )
    vendor: dict = filament.get("vendor") or {}
    extra: dict = spool.get("extra") or {}

    # RFID tag stored as JSON-encoded string in Spoolman extra.tag.
    # 32-char hex → Bambu Lab tray UUID; 8–30-char hex → NFC tag UID.
    # Accepting the full realistic UID range (4-byte = 8 chars, 7-byte = 14 chars,
    # 10-byte = 20 chars) avoids silently dropping valid SpoolBuddy-written tags.
    raw_tag: str = (extra.get("tag") or "").strip('"').upper()
    _raw_is_hex = bool(_TAG_HEX_RE.match(raw_tag))
    tag_uid = raw_tag if _raw_is_hex and 8 <= len(raw_tag) <= 30 else None
    tray_uuid = raw_tag if _raw_is_hex and len(raw_tag) == 32 else None

    # Subtype = filament name with material prefix stripped
    material: str = (filament.get("material") or "").strip()
    filament_name: str = (filament.get("name") or "").strip()
    if material and filament_name.upper().startswith(material.upper()):
        subtype: str | None = filament_name[len(material) :].strip() or None
    else:
        subtype = filament_name or None

    # Colour: validate as 6- or 8-char hex; fall back to neutral grey for invalid
    # values. An 8-char value already carries its alpha, so appending the opaque
    # byte would push it to ten and lose the translucency it was stored to keep.
    raw_color = (filament.get("color_hex") or "").upper().removeprefix("#")
    color_hex: str = raw_color if _COLOR_HEX_RE.match(raw_color) else "808080"
    rgba: str = color_hex if len(color_hex) == 8 else color_hex + "FF"

    label_weight: int = _safe_int(filament.get("weight"), 1000)
    real_used_weight: float = _safe_float(spool.get("used_weight"), 0.0)
    # Parity with internal mode (#1390): the InventorySpool shape lets the
    # frontend compute `remaining = label_weight - weight_used` and
    # `consumed = weight_used - weight_used_baseline`. Map Spoolman's two
    # independent fields (used_weight, remaining_weight) onto that shape:
    #   weight_used = label_weight - remaining_weight  (so remaining matches)
    #   baseline    = weight_used - used_weight        (so consumed matches)
    # When remaining_weight is unset (legacy spools, or filament linked but
    # never primed), fall back to the old behaviour: weight_used =
    # used_weight, baseline = 0.
    #
    # A "Reset usage to 0" records the then-current used_weight under
    # BAMBU_WEIGHT_USED_BASELINE_KEY in spool.extra and touches nothing else,
    # so displayed consumed is used_weight minus that. It is folded into the
    # baseline here because the frontend's arithmetic is fixed: it always shows
    # `weight_used - weight_used_baseline`. The reset used to PATCH Spoolman's
    # used_weight to 0 instead, and Spoolman recomputes remaining from initial
    # minus used, so the spool jumped back to full (#2906).
    # `or 0.0` would collapse None and 0.0, which is the one distinction
    # _extract_extra_float exists to preserve; spell the absent case out.
    # Clamp the low end too: the write side already refuses a negative
    # baseline, and without the same rule here a negative value stored by hand
    # would be subtracted, inflating the displayed counter. min() below only
    # bounds the other end.
    stored = _extract_extra_float(extra, BAMBU_WEIGHT_USED_BASELINE_KEY)
    stored_baseline = 0.0 if stored is None else max(0.0, stored)
    remaining_raw = spool.get("remaining_weight")
    if remaining_raw is not None:
        remaining_weight: float = _safe_float(remaining_raw, 0.0)
        used_weight: float = max(0.0, float(label_weight) - remaining_weight)
        weight_used_baseline: float = max(0.0, used_weight - real_used_weight + stored_baseline)
    else:
        used_weight = real_used_weight
        weight_used_baseline = stored_baseline  # already clamped to >= 0 above
    # Never let the displayed counter read negative: a baseline larger than
    # what the spool has consumed means used_weight moved backwards in Spoolman
    # after the reset, which is the user's edit and not ours to reinterpret.
    weight_used_baseline = min(weight_used_baseline, used_weight)

    # Archived state – Spoolman uses a boolean ``archived`` field
    archived: bool = spool.get("archived", False)
    archived_at: str | None = None
    if archived:
        archived_at = spool.get("last_used") or spool.get("registered") or None

    created_at: str | None = spool.get("registered") or None

    # Spoolman has no `color_name` field on Filament — confirmed against the
    # FilamentUpdateParameters schema in 0.23.1: name/vendor_id/material/price/
    # density/diameter/weight/spool_weight/article_number/comment/extruder_temp/
    # bed_temp/color_hex/multi_color_hexes/multi_color_direction/external_id/
    # extra, no color_name (#1357). The previous attempt (b8e350c3) was
    # PATCHing a key Spoolman silently discards, which is why color_name
    # never actually persisted from the user's edits.
    #
    # We persist it ourselves under spool.extra.bambu_color_name (JSON-encoded
    # string, same pattern as bambu_slicer_filament). Read order:
    #   1. spool.extra.bambu_color_name (the canonical store)
    #   2. filament.color_name (forward-compat — picks up the value if a
    #      future Spoolman release adds the field, or if an admin populated
    #      it via a custom extra-field they registered themselves)
    #   3. subtype (synth fallback so the inventory list isn't a sea of
    #      "Unknown color" entries on installs with neither field set)
    #
    # color_name_is_synthesized = True only when we fell back to subtype.
    # The edit form uses it to leave the input blank, so the user doesn't
    # round-trip the synth value back as if they had set it.
    extra_color_name = _extract_extra_str(extra, "bambu_color_name") or None
    stored_color_name = extra_color_name or (filament.get("color_name") or None)
    color_name: str | None = stored_color_name or subtype or None
    color_name_is_synthesized: bool = stored_color_name is None and color_name is not None

    nozzle_temp_raw = filament.get("settings_extruder_temp")
    nozzle_temp_min: int | None = _safe_int(nozzle_temp_raw, 0) or None

    return {
        "id": spool_id,
        "material": material,
        "subtype": subtype,
        "color_name": color_name,
        "color_name_is_synthesized": color_name_is_synthesized,
        "rgba": rgba,
        "brand": vendor.get("name") or None,
        "label_weight": label_weight,
        "core_weight": _safe_int(
            spool.get("spool_weight") if spool.get("spool_weight") is not None else filament.get("spool_weight"), 250
        ),
        "core_weight_catalog_id": None,
        "weight_used": used_weight,
        "weight_used_baseline": weight_used_baseline,
        "weight_locked": False,
        "last_scale_weight": None,
        "last_weighed_at": None,
        # BambuStudio slicer preset — Spoolman has no native field, so the
        # update endpoint persists these under bambu_slicer_filament[_name]
        # in the spool's extra dict. Values are JSON-encoded strings; an
        # empty string ("") means cleared. Falls back to Spoolman's
        # filament_name for slicer_filament_name when nothing is stored.
        "slicer_filament": (_extract_extra_str(extra, "bambu_slicer_filament") or None),
        "slicer_filament_name": (_extract_extra_str(extra, "bambu_slicer_filament_name") or (filament_name or None)),
        "nozzle_temp_min": nozzle_temp_min,
        "nozzle_temp_max": None,
        "note": spool.get("comment") or None,
        "added_full": None,
        "last_used": spool.get("last_used"),
        # encode_time semantics differ: local records NFC write time; Spoolman first_used
        # records first print use — different events; using first_used as best available proxy.
        "encode_time": spool.get("first_used"),
        "tag_uid": tag_uid,
        "tray_uuid": tray_uuid,
        "data_origin": "spoolman",
        "tag_type": "spoolman",
        "archived_at": archived_at,
        "created_at": created_at,
        # Spoolman has no updated_at field; use registered timestamp as best available proxy
        "updated_at": created_at,
        "cost_per_kg": _safe_optional_float(spool.get("price")),
        "storage_location": spool.get("location") or None,
        "location_id": None,
        "k_profiles": [],
    }
