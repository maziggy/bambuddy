"""Build plate vocabulary for plate-aware queue dispatch (#1306).

The slicer records which plate a file was sliced for in ``curr_bed_type``.
Bambu Studio and OrcaSlicer write internal names that do not match what their
UI shows ("High Temp Plate" is labelled "Smooth PEI Plate"), and older
Bambuddy code and third-party slicers use a few more spellings. Everything here
funnels those strings onto one small set of *base types*.

A base type is what G-code compatibility actually depends on: the bed
temperature and first-layer settings the slicer baked in. Physical plates the
user owns (``BuildPlate`` rows) each belong to exactly one base type — a
Bambu 3D Effect "Carbon Fiber" plate is a Smooth PEI plate with a pattern
sheet on it, not a new kind of plate.
"""

from __future__ import annotations

from dataclasses import dataclass

# Stored in a queue row's required_plate_type when the job has no plate
# constraint. NULL there means "not worked out yet" instead: the row was created
# by a path that never read the file, and the scheduler derives it on first use.
PLATE_TYPE_ANY = "any"

# Canonical base-type key -> display label. Ordered as the UI lists them.
BED_TYPES: dict[str, str] = {
    "cool_plate": "Cool Plate",
    "textured_cool_plate": "Textured Cool Plate",
    "supertack": "Cool Plate SuperTack",
    "engineering": "Engineering Plate",
    "smooth_pei": "Smooth PEI / High Temp Plate",
    "textured_pei": "Textured PEI Plate",
}

# Every spelling seen in ``curr_bed_type`` (lower-cased) -> base type.
# Bambu Studio: Cool Plate, Engineering Plate, High Temp Plate, Textured PEI
# Plate, Supertack Plate. OrcaSlicer adds Textured Cool Plate. The rest are
# UI labels and older spellings that frontend/src/utils/bedType.ts already
# recognises.
_ALIASES: dict[str, str] = {
    "cool plate": "cool_plate",
    "pc plate": "cool_plate",
    "smooth cool plate": "cool_plate",
    "textured cool plate": "textured_cool_plate",
    "supertack plate": "supertack",
    "cool plate (supertack)": "supertack",
    "cool plate supertack": "supertack",
    "bambu cool plate supertack": "supertack",
    "engineering plate": "engineering",
    "high temp plate": "smooth_pei",
    "smooth high temp plate": "smooth_pei",
    "smooth pei plate": "smooth_pei",
    "smooth pei plate / high temp plate": "smooth_pei",
    "textured pei plate": "textured_pei",
    "pei plate": "textured_pei",
}


def normalize_bed_type(raw: str | None) -> str | None:
    """Map a slicer plate name (or a base-type key) to a base-type key.

    Returns None for "Default Plate", empty and unrecognised values, which the
    queue treats as "no plate constraint" — an unknown slicer string must never
    make a job impossible to dispatch.
    """
    if not raw:
        return None
    value = raw.strip()
    if value in BED_TYPES:
        return value
    return _ALIASES.get(value.lower())


def bed_type_label(key: str | None) -> str:
    """Display label for a base-type key (the key itself if unknown)."""
    if not key:
        return ""
    return BED_TYPES.get(key, key)


@dataclass(frozen=True)
class InstalledPlate:
    """The plate currently on a printer, as the scheduler sees it."""

    id: int
    name: str
    base_type: str


def plate_mismatch(
    installed: InstalledPlate | None,
    required_type: str | None,
    required_plate_id: int | None,
    required_plate_name: str | None = None,
) -> str | None:
    """Why *installed* cannot take a job, or None if it can.

    * A printer with no plate recorded is "not tracked" and accepts anything,
      so turning the feature on never strands a printer the user has not set up.
    * A job asking for one specific plate (``required_plate_id``) needs exactly
      that plate — and, when the job also names a type, that plate must still be
      of it (a custom plate's type can be edited after jobs asked for it).
    * Otherwise a job needs any plate of its base type — a patterned plate is a
      subset of its base type, so a Carbon Fiber plate takes Smooth PEI jobs.
    * A job with no requirement (None or PLATE_TYPE_ANY) goes anywhere.

    The returned text is the "needs X" half of a waiting reason.
    """
    if installed is None:
        return None
    if required_type == PLATE_TYPE_ANY:
        required_type = None
    if required_plate_id is not None:
        if installed.id != required_plate_id:
            return f"needs {required_plate_name or f'plate #{required_plate_id}'}, has {installed.name}"
        if required_type and installed.base_type != required_type:
            return f"needs {bed_type_label(required_type)}, has {installed.name}"
        return None
    if required_type and installed.base_type != required_type:
        return f"needs {bed_type_label(required_type)}, has {installed.name}"
    return None
