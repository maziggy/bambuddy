"""Pure helper functions for Spoolman spool mapping.

No heavy dependencies — importable in unit tests without the full backend stack.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone

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

    filament: dict = spool.get("filament") or {}
    vendor: dict = filament.get("vendor") or {}
    extra: dict = spool.get("extra") or {}

    # RFID tag stored as JSON-encoded string in Spoolman extra.tag
    raw_tag: str = (extra.get("tag") or "").strip('"').upper()
    tag_uid = raw_tag if len(raw_tag) == 16 else None
    tray_uuid = raw_tag if len(raw_tag) == 32 else None

    # Subtype = filament name with material prefix stripped
    material: str = (filament.get("material") or "").strip()
    filament_name: str = (filament.get("name") or "").strip()
    if material and filament_name.upper().startswith(material.upper()):
        subtype: str | None = filament_name[len(material) :].strip() or None
    else:
        subtype = filament_name or None

    # Colour: validate as 6-char hex; fall back to neutral grey for invalid values
    raw_color = (filament.get("color_hex") or "").upper().lstrip("#")
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

    return {
        "id": spool_id,
        "material": material,
        "subtype": subtype,
        "color_name": None,
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
        "nozzle_temp_min": None,
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
        "updated_at": created_at,
        "cost_per_kg": _safe_optional_float(spool.get("price")),
        "storage_location": spool.get("location") or None,
        "k_profiles": [],
    }
