"""Pure helper functions for Spoolman spool mapping.

No heavy dependencies — importable in unit tests without the full backend stack.
"""

from __future__ import annotations

import ipaddress
import logging
import math
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def assert_safe_spoolman_url(url: str) -> None:
    """Raise ValueError if *url* should be blocked as an SSRF risk.

    Checks performed:
    - Scheme must be http or https.
    - Bare numeric IP hosts in loopback (127.x, ::1), link-local (169.254.x,
      fe80::), private (RFC-1918), multicast (224.x, ff::/8), or unspecified
      (0.0.0.0, ::) ranges are rejected.
    - IPv4-mapped IPv6 addresses (::ffff:x.x.x.x) are unwrapped to their IPv4
      equivalent and subject to the same checks.

    Hostname-based addresses ("localhost", "internal.corp") require DNS resolution
    and are outside the scope of this guard — they are mitigated by network-level
    controls in the deployment environment.  "localhost" is intentionally *not*
    blocked here because running Spoolman on the same host is a common and
    supported topology.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("Spoolman URL must use http or https")

    hostname = (parsed.hostname or "").lower()

    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        # Not a bare IP — hostname-based addresses are out of scope.
        return

    # Unwrap IPv4-mapped IPv6 (::ffff:169.254.x.x etc.) so their IPv4
    # properties are evaluated correctly.
    effective: ipaddress.IPv4Address | ipaddress.IPv6Address = addr
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        effective = addr.ipv4_mapped

    if (
        effective.is_loopback
        or effective.is_link_local
        or effective.is_private
        or effective.is_multicast
        or effective.is_unspecified
    ):
        raise ValueError(
            "Spoolman URL must not point to a private, loopback, link-local, "
            "multicast, or unspecified address"
        )

_COLOR_HEX_RE = re.compile(r"^[0-9A-Fa-f]{6}$")


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


def _map_spoolman_spool(spool: dict) -> dict:
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
    _HEX = frozenset("0123456789ABCDEF")
    raw_tag: str = (extra.get("tag") or "").strip('"').upper()
    _raw_is_hex = bool(raw_tag) and all(c in _HEX for c in raw_tag)
    tag_uid = raw_tag if _raw_is_hex and 8 <= len(raw_tag) <= 30 else None
    tray_uuid = raw_tag if _raw_is_hex and len(raw_tag) == 32 else None

    # Subtype = filament name with material prefix stripped
    material: str = (filament.get("material") or "").strip()
    filament_name: str = (filament.get("name") or "").strip()
    if material and filament_name.upper().startswith(material.upper()):
        subtype: str | None = filament_name[len(material) :].strip() or None
    else:
        subtype = filament_name or None

    # Colour: validate as 6-char hex; fall back to neutral grey for invalid values
    raw_color = (filament.get("color_hex") or "").upper().removeprefix("#")
    color_hex: str = raw_color if _COLOR_HEX_RE.match(raw_color) else "808080"
    rgba: str = color_hex + "FF"

    label_weight: int = _safe_int(filament.get("weight"), 1000)
    used_weight: float = _safe_float(spool.get("used_weight"), 0.0)

    # Archived state – Spoolman uses a boolean ``archived`` field
    archived: bool = spool.get("archived", False)
    archived_at: str | None = None
    if archived:
        archived_at = spool.get("last_used") or spool.get("registered")
        if not archived_at:
            archived_at = datetime.now(timezone.utc).isoformat()

    created_at: str = spool.get("registered") or datetime.now(timezone.utc).isoformat()

    color_name: str | None = filament.get("color_name") or None

    nozzle_temp_raw = filament.get("settings_extruder_temp")
    nozzle_temp_min: int | None = _safe_int(nozzle_temp_raw, 0) or None

    return {
        "id": spool_id,
        "material": material,
        "subtype": subtype,
        "color_name": color_name,
        "rgba": rgba,
        "brand": vendor.get("name") or None,
        "label_weight": label_weight,
        "core_weight": _safe_int(filament.get("spool_weight"), 250),
        "core_weight_catalog_id": None,
        "weight_used": used_weight,
        "weight_locked": False,
        "last_scale_weight": None,
        "last_weighed_at": None,
        # slicer_filament_name carries the Spoolman filament name for display
        "slicer_filament": None,
        "slicer_filament_name": filament_name or None,
        "nozzle_temp_min": nozzle_temp_min,
        "nozzle_temp_max": None,
        "note": spool.get("comment") or None,
        "added_full": None,
        "last_used": spool.get("last_used"),
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
        "k_profiles": [],
    }
