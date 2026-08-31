"""Resolve which slicer filament preset a spool should use on a given nozzle.

``Spool.slicer_filament`` is the spool's single, printer-agnostic answer. It is
right until the same spool is used on two printer models, because a cloud or
Orca preset is bound to a model (``@BBL X1C``): assigning that spool to an H2C
writes a slot preset the H2C has no profile for. ``SpoolFilamentPreset`` stores
the per-model exceptions and this module is the only thing that reads them, so
the internal-inventory and Spoolman-inventory assign paths cannot drift apart
the way they did before #1713.

Resolution order, most specific first:

    1. (printer_model, nozzle_diameter)  -- what the spool form writes, one
                                            row per nozzle size
    2. (printer_model, "")               -- a whole-model value; the form does
                                            not write these, but the API accepts
                                            them and they still resolve
    3. ``Spool.slicer_filament``         -- what the spool carries today

Every step is a plain equality match on stored strings; nothing is inferred
from preset names. A model with no row at all resolves to step 3, which is
exactly the behaviour every install has now, so a spool nobody has configured
per-model behaves identically before and after this feature.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool_filament_preset import SpoolFilamentPreset, SpoolmanFilamentPreset

logger = logging.getLogger(__name__)

# What ``resolve_*`` returns: (slicer_filament, slicer_filament_name).
PresetPair = tuple[str | None, str | None]


def _pick(
    rows: list[SpoolFilamentPreset] | list[SpoolmanFilamentPreset],
    printer_model: str | None,
    nozzle_diameter: str | None,
    fallback: PresetPair,
) -> PresetPair:
    """Apply the cascade to rows already fetched for one spool.

    Split out so both spool flavours share it, and so callers that already
    hold the rows (the spool form's read path) do not re-query.
    """
    model = (printer_model or "").strip()
    if not model:
        # No model means no way to be more specific than the spool's own value.
        # This is the normal answer for a printer that has not reported yet.
        return fallback

    diameter = (nozzle_diameter or "").strip()
    exact: PresetPair | None = None
    model_default: PresetPair | None = None

    for row in rows:
        if row.printer_model != model:
            continue
        if diameter and row.nozzle_diameter == diameter:
            exact = (row.slicer_filament, row.slicer_filament_name)
        elif row.nozzle_diameter == "":
            model_default = (row.slicer_filament, row.slicer_filament_name)

    chosen = exact or model_default
    if chosen is None:
        return fallback
    # A row that exists but carries no preset id is a deliberate "use nothing
    # here", not a hole to fall through: the user picked the blank entry for
    # this model. Falling back would silently reinstate the value they cleared.
    return chosen


def printer_safe_filament_id(*candidates: str | None) -> str:
    """First candidate the printer will accept as a filament id, or "".

    ``extrusion_cali_sel`` carries a filament id so the printer can link the
    calibration index to the slot's filament. A cloud USER preset id
    (``PFUS``/``PFCN`` prefix) is not one the slicer accepts -- the assign paths
    have refused those for tray_info_idx since #1713, and the same holds here.

    This matters now that a per-model override can BE such an id: a user picking
    their own cloud preset for a model stores its ``PFUS...`` id, and passing
    that straight through would send the printer a value it rejects, silently
    losing the K-profile link. Falls through to the next candidate instead --
    normally the spool's own preset, then the tray's RFID value.
    """
    for candidate in candidates:
        value = (candidate or "").strip()
        if value and not value.startswith(("PFUS", "PFCN")):
            return value
    return ""


async def resolve_spool_preset(
    db: AsyncSession,
    *,
    spool_id: int,
    printer_model: str | None,
    nozzle_diameter: str | None,
    fallback_filament: str | None,
    fallback_name: str | None,
) -> PresetPair:
    """Cascade for an internal-inventory spool. See the module docstring."""
    result = await db.execute(select(SpoolFilamentPreset).where(SpoolFilamentPreset.spool_id == spool_id))
    return _pick(list(result.scalars().all()), printer_model, nozzle_diameter, (fallback_filament, fallback_name))


async def resolve_spoolman_preset(
    db: AsyncSession,
    *,
    spoolman_spool_id: int,
    printer_model: str | None,
    nozzle_diameter: str | None,
    fallback_filament: str | None,
    fallback_name: str | None,
) -> PresetPair:
    """Cascade for a Spoolman-managed spool. See the module docstring."""
    result = await db.execute(
        select(SpoolmanFilamentPreset).where(SpoolmanFilamentPreset.spoolman_spool_id == spoolman_spool_id)
    )
    return _pick(list(result.scalars().all()), printer_model, nozzle_diameter, (fallback_filament, fallback_name))
