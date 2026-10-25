"""Trusted server-side cost estimates for queued prints."""

import json
import logging
from decimal import ROUND_HALF_UP, Decimal
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


def _global_tray_id(assignment) -> int:
    if assignment.ams_id == 255:
        return 254 + assignment.tray_id
    if assignment.ams_id >= 128:
        return assignment.ams_id
    return assignment.ams_id * 4 + assignment.tray_id


async def _spoolman_cost_by_tray(db: AsyncSession, printer_id: int, trays: set[int]) -> dict[int, float | None]:
    """Per-kg price of the Spoolman spool in each of ``trays``, from the spool's
    own price or its filament's. Only those spools are fetched, after a health
    check: this runs on queue add and at dispatch. Empty when Spoolman cannot
    be asked, and the estimate uses the default rate, as for an unpriced spool.
    """
    from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
    from backend.app.services.spoolman_tracking import _get_spoolman_client_with_fallback, _spool_cost_per_gram

    rows = [
        row
        for row in (
            await db.execute(select(SpoolmanSlotAssignment).where(SpoolmanSlotAssignment.printer_id == printer_id))
        )
        .scalars()
        .all()
        if _global_tray_id(row) in trays
    ]
    if not rows:
        return {}
    by_tray: dict[int, float | None] = {}
    try:
        client = await _get_spoolman_client_with_fallback()
        if client is None:
            return {}
        for row in rows:
            per_gram = _spool_cost_per_gram(await client.get_spool(row.spoolman_spool_id))
            by_tray[_global_tray_id(row)] = per_gram * 1000.0 if per_gram is not None else None
    except Exception as exc:  # noqa: BLE001 — an estimate falls back to the default rate
        logger.debug("Spoolman prices unavailable for the queue estimate: %s", exc)
    return by_tray


async def default_cost_per_kg(db: AsyncSession) -> float:
    """The default filament price per kg, as the Settings page shows it: 25
    when it was never saved."""
    from backend.app.api.routes.settings import get_setting

    raw = await get_setting(db, "default_filament_cost")
    try:
        return float(raw) if raw not in (None, "") else 25.0
    except (TypeError, ValueError):
        return 25.0


def price_one_print(
    grams: float | None,
    usage_cost: float | None,
    tracked_grams: float,
    printed_grams: float,
    runs: int,
    fallback_cost_per_kg: float,
    default_cost_per_kg: float,
) -> float | None:
    """Cost of one print of ``grams``, the figure an archive carries (#1378).

    ``usage_cost`` / ``tracked_grams`` are the priced spool usage recorded
    against the archive, over every run of it; ``printed_grams`` / ``runs`` are
    those runs' grams and count from Print History. The spools tracked
    ``tracked_grams`` of ``printed_grams``: one print gets that share at their
    average price per gram and the rest at the default rate (#1344). Without
    priced usage, everything is priced at ``fallback_cost_per_kg``.
    """
    if usage_cost and usage_cost > 0 and tracked_grams > 0:
        if not grams:
            # No figure for one print: an average run's usage is the best one.
            cost = usage_cost / max(1, runs)
        else:
            expected = printed_grams if printed_grams > 0 else grams
            covered = grams * min(1.0, tracked_grams / expected)
            cost = covered * (usage_cost / tracked_grams) + (grams - covered) / 1000.0 * default_cost_per_kg
    elif grams:
        cost = grams / 1000.0 * fallback_cost_per_kg
    else:
        return None
    return float(Decimal(str(cost)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


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
        default_cost = await default_cost_per_kg(db)
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

    default_cost = await default_cost_per_kg(db)
    cost_by_tray: dict[int, float | None] = {}
    mapping = _parse_mapping(ams_mapping)
    # The prices of the spools the mode's own table puts in those trays. The
    # built-in table may still hold rows from before a switch to Spoolman --
    # nothing clears it since #2812 -- and pricing from a spool the printer is
    # not drawing on would be worse than the default rate this falls back to.
    spoolman_mode = await spoolman_owns_assignments(db)
    if printer_id is not None and mapping and spoolman_mode:
        cost_by_tray = await _spoolman_cost_by_tray(db, printer_id, {t for t in mapping if t >= 0})
    if printer_id is not None and mapping and not spoolman_mode:
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
