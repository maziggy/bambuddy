"""Filament-deficit check used by every queue dispatch path.

The PrintModal warns when an assigned spool can't satisfy a print's per-slot
filament weight (``Pre-print checks now also warn when the spool has
insufficient material`` — #720). That check only runs when the user clicks
"Print" inside PrintModal; ``QueuePage`` Play button, ``start_queue_item``
route, and the VP intake + scheduler auto-dispatch path all skip it (#1496).

This module is the single source of truth for the check. Both the route
handler (``POST /print-queue/{id}/start``) and the dispatch scheduler call
``compute_deficit_for_queue_item`` against live spool state.

Design notes:
* The 3MF parser is the same one used by PrintModal: per-slot ``used_grams``
  comes from ``extract_filament_requirements`` (#1188's filament-overrides
  pipeline) or — when the item points at an unsliced library file — falls
  through to the file's archive copy. Anything that yields no requirements
  is treated as "no deficit" so a malformed or stripped 3MF never blocks.
* Both internal-inventory and Spoolman modes are covered. Internal mode
  resolves via ``SpoolAssignment`` joined to ``Spool`` (``label_weight``
  minus ``weight_used``). Spoolman mode resolves via
  ``SpoolmanSlotAssignment`` then ``SpoolmanClient.get_spool`` for the live
  remaining weight; if Spoolman is unreachable we return no deficit rather
  than wedge the queue on a flaky network call.
* The ``disable_filament_warnings`` user setting is respected at the
  service boundary — callers do not have to know about it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.config import settings as app_settings
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services.filament_requirements import extract_filament_requirements

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FilamentDeficit:
    """One slot's filament shortfall."""

    slot_id: int
    ams_id: int | None
    tray_id: int | None
    filament_type: str
    required_grams: float
    remaining_grams: float | None  # None = could not determine

    def to_dict(self) -> dict:
        return {
            "slot_id": self.slot_id,
            "ams_id": self.ams_id,
            "tray_id": self.tray_id,
            "filament_type": self.filament_type,
            "required_grams": self.required_grams,
            "remaining_grams": self.remaining_grams,
        }


def _global_to_ams_key(global_tray_id: int) -> tuple[int, int]:
    """Inverse of ``ams_id * 4 + tray_id`` — matches ``usage_tracker``."""
    if global_tray_id >= 254:
        return (255, global_tray_id - 254)
    if global_tray_id >= 128:
        return (global_tray_id, 0)
    return (global_tray_id // 4, global_tray_id % 4)


def _resolve_source_3mf(item: PrintQueueItem) -> Path | None:
    """Locate the 3MF file backing this queue item (archive or library)."""
    if item.archive is not None and item.archive.file_path:
        return app_settings.base_dir / item.archive.file_path
    if item.library_file is not None and item.library_file.file_path:
        return Path(item.library_file.file_path)
    return None


async def _spoolman_remaining_grams(spoolman_spool_id: int) -> float | None:
    """Live remaining grams for a Spoolman spool, or None if unavailable."""
    try:
        from backend.app.services.spoolman import (
            SpoolmanClientError,
            SpoolmanNotFoundError,
            get_spoolman_client,
        )
    except ImportError:
        return None
    try:
        client = await get_spoolman_client()
        if client is None:
            return None
        spool = await client.get_spool(spoolman_spool_id)
    except (SpoolmanNotFoundError, SpoolmanClientError):
        return None
    except Exception as e:
        logger.debug("Spoolman fetch failed for spool %s: %s", spoolman_spool_id, e)
        return None

    if not spool:
        return None

    # Spoolman exposes either an absolute remaining_weight, or used_weight +
    # filament.weight. Either is sufficient — prefer remaining_weight when
    # present (the user may have overridden it).
    remaining = spool.get("remaining_weight")
    if isinstance(remaining, (int, float)) and remaining >= 0:
        return float(remaining)

    used = spool.get("used_weight")
    filament = spool.get("filament") or {}
    total = filament.get("weight")
    if isinstance(used, (int, float)) and isinstance(total, (int, float)) and total > 0:
        return max(0.0, float(total) - float(used))

    return None


async def _is_spoolman_mode(db: AsyncSession) -> bool:
    """Check whether the user has opted in to Spoolman inventory mode."""
    try:
        from backend.app.api.routes.settings import get_setting

        spoolman_enabled = await get_setting(db, "spoolman_enabled")
        return bool(spoolman_enabled) and spoolman_enabled.lower() == "true"
    except Exception:
        return False


async def _warnings_disabled(db: AsyncSession) -> bool:
    """Honour the ``disable_filament_warnings`` setting (#720)."""
    try:
        from backend.app.api.routes.settings import get_setting

        disabled = await get_setting(db, "disable_filament_warnings")
        return bool(disabled) and disabled.lower() == "true"
    except Exception:
        return False


def _parse_ams_mapping(raw: str | None) -> list[int] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, list):
        return None
    return [v for v in parsed if isinstance(v, int)]


async def compute_deficit_for_queue_item(
    db: AsyncSession,
    item: PrintQueueItem,
) -> list[FilamentDeficit]:
    """Return per-slot filament shortfalls for ``item``, or [] when it's safe to dispatch.

    Returns an empty list whenever any of the following hold:

    * The ``disable_filament_warnings`` setting is on.
    * The item has no resolved ``printer_id`` (model-based assignment not
      yet picked a printer — the scheduler re-runs the check after it does).
    * No source 3MF is available, or the 3MF carries no per-slot
      requirements (treated as "nothing to verify" rather than an error,
      matching the PrintModal behaviour).
    * No AMS mapping is set yet — the scheduler computes the mapping just
      before dispatch; until it does we cannot map slot → tray.
    * Spoolman mode is on but the Spoolman server is unreachable. We do not
      wedge the queue on a network blip.
    """
    if await _warnings_disabled(db):
        return []
    if item.printer_id is None:
        return []

    # Refresh the relationships we need without assuming the caller eagerly
    # loaded them — both the route and the scheduler call this from contexts
    # with different loading strategies.
    refreshed = await db.execute(
        select(PrintQueueItem)
        .options(
            selectinload(PrintQueueItem.archive),
            selectinload(PrintQueueItem.library_file),
        )
        .where(PrintQueueItem.id == item.id)
    )
    item = refreshed.scalar_one_or_none() or item

    source_path = _resolve_source_3mf(item)
    if source_path is None or not source_path.exists():
        return []

    requirements = extract_filament_requirements(source_path, item.plate_id)
    if not requirements:
        return []

    mapping = _parse_ams_mapping(item.ams_mapping)
    if not mapping:
        return []

    spoolman_mode = await _is_spoolman_mode(db)

    deficits: list[FilamentDeficit] = []
    for req in requirements:
        slot_id = req.get("slot_id")
        used_grams = req.get("used_grams")
        if not isinstance(slot_id, int) or slot_id <= 0:
            continue
        if not isinstance(used_grams, (int, float)) or used_grams <= 0:
            continue
        idx = slot_id - 1
        if idx >= len(mapping):
            continue
        global_tray_id = mapping[idx]
        if global_tray_id is None or global_tray_id < 0:
            continue
        ams_id, tray_id = _global_to_ams_key(global_tray_id)

        remaining: float | None = None
        if spoolman_mode:
            sm_result = await db.execute(
                select(SpoolmanSlotAssignment).where(
                    SpoolmanSlotAssignment.printer_id == item.printer_id,
                    SpoolmanSlotAssignment.ams_id == ams_id,
                    SpoolmanSlotAssignment.tray_id == tray_id,
                )
            )
            sm_assignment = sm_result.scalar_one_or_none()
            if sm_assignment is None:
                continue
            remaining = await _spoolman_remaining_grams(sm_assignment.spoolman_spool_id)
        else:
            internal_result = await db.execute(
                select(SpoolAssignment)
                .options(selectinload(SpoolAssignment.spool))
                .where(
                    SpoolAssignment.printer_id == item.printer_id,
                    SpoolAssignment.ams_id == ams_id,
                    SpoolAssignment.tray_id == tray_id,
                )
            )
            assignment = internal_result.scalar_one_or_none()
            if assignment is None or assignment.spool is None:
                continue
            spool = assignment.spool
            label_weight = float(spool.label_weight or 0)
            weight_used = float(spool.weight_used or 0)
            if label_weight <= 0:
                continue
            remaining = max(0.0, label_weight - weight_used)

        if remaining is None:
            # Spoolman unreachable for this spool — skip rather than block.
            continue
        if remaining >= float(used_grams):
            continue

        deficits.append(
            FilamentDeficit(
                slot_id=slot_id,
                ams_id=ams_id,
                tray_id=tray_id,
                filament_type=str(req.get("type", "")),
                required_grams=float(used_grams),
                remaining_grams=remaining,
            )
        )

    return deficits


# Re-export the most useful pieces for callers that just want the data.
__all__ = [
    "FilamentDeficit",
    "compute_deficit_for_queue_item",
]
