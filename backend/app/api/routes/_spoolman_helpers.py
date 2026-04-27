"""Pure helper functions for Spoolman spool mapping.

No heavy dependencies — importable in unit tests without the full backend stack.
"""

from __future__ import annotations

import ipaddress
import logging
import math
import re
from datetime import datetime, timezone
from typing import TypedDict
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class MappedSpoolFields(TypedDict, total=False):
    """Full shape of the dict returned by _map_spoolman_spool (InventorySpool-compatible)."""

    id: int
    material: str | None
    subtype: str | None
    brand: str | None
    color_name: str | None
    rgba: str | None
    label_weight: int | None
    core_weight: int | None
    core_weight_catalog_id: None
    weight_used: float | None
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
    created_at: str | None
    updated_at: str | None
    cost_per_kg: float | None
    storage_location: str | None
    k_profiles: list


_CLOUD_METADATA_IPS = frozenset(
    {
        # AWS / GCP / Azure / Oracle / DigitalOcean IMDS
        ipaddress.ip_address("169.254.169.254"),
        # Alibaba Cloud metadata
        ipaddress.ip_address("100.100.100.200"),
        # AWS IMDS IPv6
        ipaddress.ip_address("fd00:ec2::254"),
    }
)


def assert_safe_spoolman_url(url: str) -> None:
    """Raise ValueError if *url* should be blocked as an SSRF risk.

    Bambuddy is typically deployed on a home LAN alongside Spoolman, so
    loopback (127.0.0.1) and RFC-1918 private ranges (192.168.x.x, 10.x.x.x,
    172.16-31.x) must be permitted — they are THE normal Spoolman topology.
    This guard therefore targets the genuinely dangerous cases only.

    Checks performed:
    - Scheme must be http or https (no file://, gopher://, dict://, etc.).
    - Numeric-encoded IP addresses in decimal (e.g. ``2130706433``) or hex
      (e.g. ``0x7f000001``) are rejected. Python's ``ipaddress`` module raises
      ``ValueError`` for these forms so they would otherwise bypass the
      explicit-IP block below, but libc (and browsers) resolve them as valid
      IPv4 addresses.
    - Cloud provider metadata endpoints (169.254.169.254, 100.100.100.200,
      fd00:ec2::254) are blocked — the classic SSRF credential-exfil target.
    - Multicast (224.0.0.0/4, ff00::/8) and unspecified (0.0.0.0, ::) addresses
      are blocked — pointless as a destination and suggests misuse.
    - IPv4-mapped IPv6 addresses (::ffff:x.x.x.x) are unwrapped so they cannot
      bypass the checks above.

    Hostname-based addresses ("localhost", "spoolman.lan", "internal.corp")
    are out of scope — DNS resolution is deliberately not performed here.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("Spoolman URL must use http or https")

    hostname = (parsed.hostname or "").lower()

    # Reject decimal- and hex-encoded IPs (e.g. http://2130706433/ or
    # http://0x7f000001/). These slip past ipaddress.ip_address() but libc
    # (and browsers) parse them as IPv4 — an obvious bypass if not caught.
    if re.match(r"^(0x[0-9a-f]+|[0-9]+)$", hostname, re.I):
        raise ValueError("Spoolman URL must not use numeric-encoded IP addresses; use standard dotted-decimal notation")

    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        # Not a bare IP — hostname-based addresses are out of scope.
        return

    # Unwrap IPv4-mapped IPv6 (::ffff:169.254.169.254 etc.) so attackers can't
    # encode a blocked IPv4 into an IPv6 literal to bypass the check.
    effective: ipaddress.IPv4Address | ipaddress.IPv6Address = addr
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        effective = addr.ipv4_mapped

    if effective in _CLOUD_METADATA_IPS:
        raise ValueError("Spoolman URL must not point to a cloud metadata endpoint")

    if effective.is_multicast or effective.is_unspecified:
        raise ValueError("Spoolman URL must not point to a multicast or unspecified address")


_COLOR_HEX_RE = re.compile(r"^[0-9A-Fa-f]{6}$")
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
        "k_profiles": [],
    }
