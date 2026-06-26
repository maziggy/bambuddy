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
from collections import defaultdict
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


def _normalize_color_for_id(raw: str | None) -> str:
    """Canonicalise a hex colour for identity comparison.

    Strips the leading ``#``, uppercases, and drops the alpha channel when
    the hex is 8 chars long (``RRGGBBAA``) so a fully-opaque 8-char hex
    matches a 6-char hex of the same RGB. Empty / None → empty string.
    """
    s = (raw or "").strip().lstrip("#").upper()
    if len(s) == 8:  # RRGGBBAA → strip alpha
        s = s[:6]
    return s


def _material_identity_internal(spool) -> str:
    """Strict same-material key for backup-peer matching in internal mode.

    Requires a Bambu filament preset ID (``slicer_filament``, e.g. ``GFA00``)
    AND a matching colour. The preset identifies the filament profile (PETG
    HF, PLA Basic, etc.) — same hot-end behaviour — but the firmware's
    switch logic also requires the spool to be the same colour (otherwise
    every PETG HF spool would back every other PETG HF spool regardless of
    colour, which would dye prints mid-run). Spools without a preset
    (user-tagged / non-Bambu) get a per-spool unique key so they NEVER
    pair with anything else; without the Bambu preset the firmware can't
    trust the backup decision.
    """
    preset = (spool.slicer_filament or "").strip() if spool else ""
    if preset:
        color = _normalize_color_for_id(spool.rgba if spool else None)
        return f"preset:{preset}|color:{color}"
    # Unique-per-spool key prevents grouping. Use the spool's primary key so
    # the same spool always resolves to the same key within a request.
    spool_id = getattr(spool, "id", None) if spool else None
    return f"unmatched:{spool_id}"


def _material_identity_spoolman(spool: dict | None) -> str:
    """Strict same-material key for backup-peer matching in Spoolman mode.

    Two spools pair only when they reference the same Spoolman ``filament``
    catalog entry (same ``filament.id``) AND share the same colour. The
    catalog entry pins the profile (PETG HF / PLA Basic / ...); the colour
    pins the variant. Spools without a resolvable filament id get a
    per-spool unique key so they never pair.
    """
    if not spool:
        return "unmatched:none"
    filament = spool.get("filament") or {}
    fil_id = filament.get("id")
    if isinstance(fil_id, (int, str)) and str(fil_id).strip():
        # Prefer the per-spool override colour when set (Spoolman lets the user
        # tag a spool with a colour distinct from the filament catalog
        # default); fall back to the filament catalog colour.
        color = _normalize_color_for_id(
            (spool.get("color_hex") if isinstance(spool.get("color_hex"), str) else None) or filament.get("color_hex")
        )
        return f"filament:{fil_id}|color:{color}"
    spool_id = spool.get("id")
    return f"unmatched:{spool_id}"


def _ams_id_from_global(global_tray_id: int) -> int:
    """Inverse of ``_global_to_ams_key`` returning ams_id only."""
    return _global_to_ams_key(global_tray_id)[0]


def _extruder_side_for_ams(
    ams_id: int,
    ams_extruder_map: dict[str, int],
    is_dual_extruder: bool,
) -> int:
    """Resolve the extruder index (0=right, 1=left) for a given AMS unit.

    Single-extruder printers collapse everything to 0. On dual-extruder
    printers (H2D / H2C / X2D), the firmware can't cross extruders even with
    AMS Filament Backup ON, so the pool must be scoped per-side.
    """
    if not is_dual_extruder:
        return 0
    return int(ams_extruder_map.get(str(ams_id), 0))


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


async def _get_printer_backup_context(
    printer_id: int,
) -> tuple[bool, dict[str, int], bool]:
    """Return ``(backup_on, ams_extruder_map, is_dual_extruder)`` for the printer.

    Read from the live MQTT state via ``printer_manager`` (no DB round-trip).
    Defaults conservatively to ``backup_on=False`` when the state is missing
    or the printer is offline — same fallback as today (per-slot deficit
    accounting), so an offline printer is never treated as backup-capable.
    """
    try:
        from backend.app.services.printer_manager import printer_manager
        from backend.app.utils.printer_models import is_dual_nozzle_model
    except ImportError:
        return False, {}, False

    state = printer_manager.get_status(printer_id)
    if state is None:
        return False, {}, False

    backup_on = state.ams_filament_backup is True
    ams_extruder_map = dict(state.ams_extruder_map or {})
    model = printer_manager.get_model(printer_id)
    is_dual = bool(model and is_dual_nozzle_model(model))
    return backup_on, ams_extruder_map, is_dual


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

    #1762: when the printer reports ``ams_filament_backup=True`` in MQTT
    status, available material is pooled across ALL same-material spools on
    the printer (within the same extruder side for dual-nozzle models, since
    firmware can't cross extruders even with the backup bit set). Per-slot
    shortfalls are then only emitted if the POOL is too small for the
    print's total required of that material — matching how the printer
    actually behaves with Filament Backup ON.
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
    backup_on, ams_extruder_map, is_dual = await _get_printer_backup_context(item.printer_id)

    # ------------------------------------------------------------------ phase 1
    # Resolve each requirement to (ams_id, tray_id, identity, remaining_grams).
    # Slot identity is the identity of the spool *assigned to that slot*. A
    # ``None`` remaining means "couldn't determine" — treated as "no deficit"
    # below (preserved from pre-#1762 behaviour for non-backup paths too).
    @dataclass
    class _ReqRow:
        slot_id: int
        ams_id: int
        tray_id: int
        global_tray_id: int
        required: float
        identity: str
        remaining: float | None
        filament_type: str
        extruder: int

    resolved: list[_ReqRow] = []

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

        identity = "attrs:|||"
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
            # Live remaining_weight from Spoolman. The fetch also resolves the
            # filament identity for pooling (material + colour + name).
            from backend.app.services.spoolman import (
                SpoolmanClientError,
                SpoolmanNotFoundError,
                get_spoolman_client,
            )

            try:
                client = await get_spoolman_client()
                spool_dict = await client.get_spool(sm_assignment.spoolman_spool_id) if client else None
            except (SpoolmanNotFoundError, SpoolmanClientError):
                spool_dict = None
            except Exception as e:
                logger.debug("Spoolman fetch failed for spool %s: %s", sm_assignment.spoolman_spool_id, e)
                spool_dict = None
            if spool_dict:
                identity = _material_identity_spoolman(spool_dict)
                rw = spool_dict.get("remaining_weight")
                if isinstance(rw, (int, float)) and rw >= 0:
                    remaining = float(rw)
                else:
                    used = spool_dict.get("used_weight")
                    total = (spool_dict.get("filament") or {}).get("weight")
                    if isinstance(used, (int, float)) and isinstance(total, (int, float)) and total > 0:
                        remaining = max(0.0, float(total) - float(used))
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
            identity = _material_identity_internal(spool)
            label_weight = float(spool.label_weight or 0)
            weight_used = float(spool.weight_used or 0)
            if label_weight <= 0:
                continue
            remaining = max(0.0, label_weight - weight_used)

        if remaining is None:
            # Unable to determine remaining grams — preserve pre-#1762 behaviour
            # (don't block on undetermined data).
            continue

        resolved.append(
            _ReqRow(
                slot_id=slot_id,
                ams_id=ams_id,
                tray_id=tray_id,
                global_tray_id=global_tray_id,
                required=float(used_grams),
                identity=identity,
                remaining=remaining,
                filament_type=str(req.get("type", "")),
                extruder=_extruder_side_for_ams(ams_id, ams_extruder_map, is_dual),
            )
        )

    # ------------------------------------------------------------------ phase 2
    # When backup is OFF, fall back to today's per-slot accounting (one-line
    # equivalence of the original loop), so this path is a strict no-op
    # behaviour-wise vs. the pre-#1762 code.
    if not backup_on:
        return [
            FilamentDeficit(
                slot_id=row.slot_id,
                ams_id=row.ams_id,
                tray_id=row.tray_id,
                filament_type=row.filament_type,
                required_grams=row.required,
                remaining_grams=row.remaining,
            )
            for row in resolved
            if row.remaining is not None and row.remaining < row.required
        ]

    # ------------------------------------------------------------------ phase 3
    # Backup ON: build (identity, extruder)-keyed pool and required-sum maps
    # from EVERY assigned spool on the printer (not just the slots in the
    # print's mapping). Then emit deficits only when the pool for a slot's
    # material is too small for the print's total required of that material.
    pool_by_key: dict[tuple[str, int], float] = defaultdict(float)
    required_by_key: dict[tuple[str, int], float] = defaultdict(float)

    if spoolman_mode:
        sm_all = await db.execute(
            select(SpoolmanSlotAssignment).where(SpoolmanSlotAssignment.printer_id == item.printer_id)
        )
        from backend.app.services.spoolman import (
            SpoolmanClientError,
            SpoolmanNotFoundError,
            get_spoolman_client,
        )

        try:
            client = await get_spoolman_client()
        except Exception:
            client = None
        for sa in sm_all.scalars().all():
            if client is None:
                break
            try:
                spool_dict = await client.get_spool(sa.spoolman_spool_id)
            except (SpoolmanNotFoundError, SpoolmanClientError):
                continue
            except Exception as e:
                logger.debug("Spoolman pool fetch failed for spool %s: %s", sa.spoolman_spool_id, e)
                continue
            if not spool_dict:
                continue
            identity = _material_identity_spoolman(spool_dict)
            rw = spool_dict.get("remaining_weight")
            r: float | None = None
            if isinstance(rw, (int, float)) and rw >= 0:
                r = float(rw)
            else:
                used = spool_dict.get("used_weight")
                total = (spool_dict.get("filament") or {}).get("weight")
                if isinstance(used, (int, float)) and isinstance(total, (int, float)) and total > 0:
                    r = max(0.0, float(total) - float(used))
            if r is None:
                continue
            extruder = _extruder_side_for_ams(sa.ams_id, ams_extruder_map, is_dual)
            pool_by_key[(identity, extruder)] += r
    else:
        internal_all = await db.execute(
            select(SpoolAssignment)
            .options(selectinload(SpoolAssignment.spool))
            .where(SpoolAssignment.printer_id == item.printer_id)
        )
        for assignment in internal_all.scalars().all():
            spool = assignment.spool
            if spool is None:
                continue
            label_weight = float(spool.label_weight or 0)
            weight_used = float(spool.weight_used or 0)
            if label_weight <= 0:
                continue
            r = max(0.0, label_weight - weight_used)
            identity = _material_identity_internal(spool)
            extruder = _extruder_side_for_ams(assignment.ams_id, ams_extruder_map, is_dual)
            pool_by_key[(identity, extruder)] += r

    for row in resolved:
        required_by_key[(row.identity, row.extruder)] += row.required

    deficits: list[FilamentDeficit] = []
    for row in resolved:
        key = (row.identity, row.extruder)
        # Pool insufficient for the print's TOTAL required of this material on
        # this extruder side → real deficit. The per-slot remaining still gets
        # surfaced so the UI can point at the slot the user assigned.
        if pool_by_key[key] < required_by_key[key]:
            deficits.append(
                FilamentDeficit(
                    slot_id=row.slot_id,
                    ams_id=row.ams_id,
                    tray_id=row.tray_id,
                    filament_type=row.filament_type,
                    required_grams=row.required,
                    remaining_grams=row.remaining,
                )
            )

    return deficits


# Re-export the most useful pieces for callers that just want the data.
__all__ = [
    "FilamentDeficit",
    "compute_deficit_for_queue_item",
]
