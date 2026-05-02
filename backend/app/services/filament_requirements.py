"""Parse per-slot filament requirements out of a 3MF file.

The scheduler used to own this logic (`PrintScheduler._get_filament_requirements`)
because it ran during dispatch decisions. Extracted here so the VP queue-mode
write path can use the same parser to populate `filament_overrides` /
`required_filament_types` at upload time (#1188 — Bambuddy was creating queue
items with no filament fields, which made the scheduler fall through to
model-only matching and dispatch onto whatever printer happened to be free
regardless of loaded colour).

The shape returned here matches the `filament_overrides` JSON shape the
scheduler validates against, minus the `force_color_match` flag — callers
add that themselves based on their own setting.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from backend.app.utils.threemf_tools import extract_nozzle_mapping_from_3mf

logger = logging.getLogger(__name__)


def extract_filament_requirements(file_path: Path, plate_id: int | None = None) -> list[dict]:
    """Parse `[{slot_id, type, color, tray_info_idx, used_grams, nozzle_id?}]` from a 3MF.

    Args:
        file_path: Path to the 3MF.
        plate_id: When set, only return filaments used on that plate. When
            None, return every filament with `used_g > 0` across the file.

    Returns:
        Sorted list (by `slot_id`) of filament dicts. Empty list when the
        3MF is unreadable, missing `Metadata/slice_info.config`, or has no
        filaments matching the plate filter — callers treat that as "no
        requirements" rather than an error so a malformed 3MF doesn't break
        the upload path.
    """
    if not file_path.exists():
        return []

    filaments: list[dict] = []
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            if "Metadata/slice_info.config" not in zf.namelist():
                return []

            content = zf.read("Metadata/slice_info.config").decode()
            root = ET.fromstring(content)  # noqa: S314  # nosec B314

            if plate_id is not None:
                for plate_elem in root.findall("./plate"):
                    plate_index = None
                    for meta in plate_elem.findall("metadata"):
                        if meta.get("key") == "index":
                            try:
                                plate_index = int(meta.get("value", "0"))
                            except ValueError:
                                pass
                            break
                    if plate_index == plate_id:
                        _collect_filaments(plate_elem, filaments)
                        break
            else:
                _collect_filaments(root, filaments)

            filaments.sort(key=lambda x: x["slot_id"])

            # Dual-nozzle printers (H2D / X2D) — annotate which extruder each
            # slot is fed into. Empty mapping for single-nozzle printers, in
            # which case we just don't add the key.
            nozzle_mapping = extract_nozzle_mapping_from_3mf(zf)
            if nozzle_mapping:
                for filament in filaments:
                    filament["nozzle_id"] = nozzle_mapping.get(filament["slot_id"])
    except Exception as e:
        logger.warning("Failed to parse filament requirements from %s: %s", file_path, e)
        return []

    return filaments


def _collect_filaments(parent: ET.Element, into: list[dict]) -> None:
    """Walk every `./filament` child under `parent` and append normalised
    entries to `into`. Skips filaments with `used_g <= 0` (slot present in
    the slicer config but not consumed by this plate)."""
    for filament_elem in parent.findall("./filament"):
        filament_id = filament_elem.get("id")
        if not filament_id:
            continue
        try:
            used_grams = float(filament_elem.get("used_g", "0"))
        except (ValueError, TypeError):
            continue
        if used_grams <= 0:
            continue
        try:
            slot_id = int(filament_id)
        except (ValueError, TypeError):
            continue
        into.append(
            {
                "slot_id": slot_id,
                "type": filament_elem.get("type", ""),
                "color": filament_elem.get("color", ""),
                "tray_info_idx": filament_elem.get("tray_info_idx", ""),
                "used_grams": round(used_grams, 1),
            }
        )
