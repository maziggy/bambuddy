"""Trusted server-side cost estimates for queued prints."""

import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.config import settings
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.services.inventory_mode import spoolman_owns_assignments
from backend.app.utils import threemf_tools
from backend.app.utils.safe_path import safe_join_under

logger = logging.getLogger(__name__)


def plate_scoped_run_estimate(
    archive: PrintArchive,
    full_path: Path | None,
    plate_id: int | None = None,
) -> tuple[float | None, float | None]:
    """Return trusted ``(grams, cost)`` for one run of an archive plate."""

    whole_grams = archive.filament_used_grams
    selected_plate = archive.plate_id if plate_id is None else plate_id
    if selected_plate is None or full_path is None or not full_path.exists():
        return whole_grams, archive.cost
    try:
        plate_grams = threemf_tools.extract_plate_metadata_from_3mf(full_path, selected_plate).filament_used_grams
    except Exception as exc:
        logger.debug(
            "Plate-scoped estimate failed for archive %s (plate %s): %s",
            archive.id,
            selected_plate,
            exc,
        )
        return whole_grams, archive.cost
    if not plate_grams or plate_grams <= 0:
        return whole_grams, archive.cost
    plate_cost = archive.cost
    if archive.cost and whole_grams and whole_grams > 0:
        plate_cost = round(archive.cost * (plate_grams / whole_grams), 2)
    return round(plate_grams, 2), plate_cost


def _source_path(library_file: LibraryFile) -> Path:
    path = Path(library_file.file_path)
    if path.is_absolute():
        # SEC-PATH-OK: absolute paths are persisted LibraryFile locations for
        # configured external libraries; this branch performs no path join.
        return path
    return safe_join_under(settings.base_dir, library_file.file_path, http=False)


def _parse_mapping(mapping: list[int] | str | None) -> list[int] | None:
    if isinstance(mapping, list):
        return mapping
    if isinstance(mapping, str):
        try:
            parsed = json.loads(mapping)
        except (TypeError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, list) else None
    return None


def _global_tray_id(assignment: SpoolAssignment) -> int:
    if assignment.ams_id == 255:
        return 254 + assignment.tray_id
    if assignment.ams_id >= 128:
        return assignment.ams_id
    return assignment.ams_id * 4 + assignment.tray_id


async def _default_cost_per_kg(db: AsyncSession) -> float:
    from backend.app.api.routes.settings import get_setting

    raw = await get_setting(db, "default_filament_cost")
    try:
        return float(raw) if raw is not None else 25.0
    except (TypeError, ValueError):
        return 25.0


async def estimate_queue_source_cost(
    db: AsyncSession,
    *,
    archive: PrintArchive | None = None,
    library_file: LibraryFile | None = None,
    plate_id: int | None = None,
    ams_mapping: list[int] | str | None = None,
    printer_id: int | None = None,
) -> float | None:
    """Compute a queue cost without trusting the request's display hint."""

    if archive is not None:
        archive_path = settings.base_dir / archive.file_path
        grams, cost = plate_scoped_run_estimate(archive, archive_path, plate_id)
        if cost is not None and cost > 0:
            return float(cost)
        # Older archives and imports can have trustworthy filament usage but
        # no stored cost. Model-based and multi-printer jobs have no single
        # spool mapping at enqueue time, so use the server setting rather than
        # requiring the browser to provide an estimate.
        if grams is None or grams <= 0:
            return None
        default_cost = await _default_cost_per_kg(db)
        estimated_cost = (grams / 1000.0) * default_cost
        return max(0.01, round(estimated_cost, 2)) if estimated_cost > 0 else None

    if library_file is None:
        return None

    path = _source_path(library_file)
    usage: list[dict] = []
    if path.exists():
        usage = threemf_tools.extract_plate_metadata_from_3mf(path, plate_id).filament_usage

    metadata = library_file.file_metadata or {}
    if not usage:
        try:
            grams = float(metadata.get("filament_used_grams") or 0)
        except (TypeError, ValueError):
            grams = 0
        if grams > 0:
            usage = [{"slot_id": 1, "used_g": grams}]

    if not usage:
        return None

    default_cost = await _default_cost_per_kg(db)
    cost_by_tray: dict[int, float | None] = {}
    mapping = _parse_mapping(ams_mapping)
    # Built-in spool prices only. In Spoolman mode the built-in table may still
    # hold rows from before the user switched -- nothing clears it since #2812 --
    # and pricing an estimate from a spool the printer is not drawing on would
    # be worse than the default rate this falls back to.
    if printer_id is not None and mapping and not await spoolman_owns_assignments(db):
        assignments = (
            (
                await db.execute(
                    select(SpoolAssignment)
                    .options(selectinload(SpoolAssignment.spool))
                    .where(SpoolAssignment.printer_id == printer_id)
                )
            )
            .scalars()
            .all()
        )
        cost_by_tray = {_global_tray_id(a): a.spool.cost_per_kg for a in assignments}

    total = 0.0
    for filament in usage:
        try:
            slot_id = int(filament.get("slot_id") or 0)
            grams = float(filament.get("used_g") or 0)
        except (TypeError, ValueError):
            continue
        tray_id = mapping[slot_id - 1] if mapping and 0 < slot_id <= len(mapping) else None
        cost_per_kg = cost_by_tray.get(tray_id) if tray_id is not None else None
        if cost_per_kg is None or cost_per_kg <= 0:
            cost_per_kg = default_cost
        total += (grams / 1000.0) * cost_per_kg

    return round(total, 2) if total > 0 else None
