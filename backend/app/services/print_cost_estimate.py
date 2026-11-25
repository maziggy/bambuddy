"""Trusted server-side cost estimates for queued prints."""

import asyncio
import json
import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.config import settings
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.services.inventory_mode import spoolman_owns_assignments
from backend.app.services.printer_manager import printer_tray_layout
from backend.app.utils import threemf_tools
from backend.app.utils.safe_path import safe_join_under
from backend.app.utils.tray_ids import TrayLayout

if TYPE_CHECKING:
    from backend.app.services.archive_cost_estimate import SpoolmanOnce

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunEstimate:
    """The slicer's estimate for one print of an archive (#3375).

    ``per_plate`` is False when the archive's plates are not known (the file
    not read, not sliced, or gone): the figures are then the whole file's,
    which describe a print only of a single-plate file.
    """

    seconds: int | None
    grams: float | None
    per_plate: bool


def run_estimate(archive: Any, plate_id: int | None) -> RunEstimate | None:
    """What one print of plate ``plate_id`` of ``archive`` was estimated at:
    that plate's figures, or the only plate's. None for a print of a
    multi-plate file whose plate is not known. ``archive`` is an archive, or
    a row with its ``plate_estimates``, ``print_time_seconds`` and
    ``filament_used_grams``."""
    plates = archive.plate_estimates
    if not plates:
        return RunEstimate(archive.print_time_seconds, archive.filament_used_grams, per_plate=False)
    if plate_id is not None and str(plate_id) in plates:
        plate = plates[str(plate_id)]
    elif len(plates) == 1:
        plate = next(iter(plates.values()))
    else:
        return None
    return RunEstimate(plate.get("seconds") or None, plate.get("grams") or None, per_plate=True)


def read_plate_estimates(archive: Any) -> dict | None:
    """The plate estimates of the archive's 3MF on disk. ``{}`` for a file that
    is not a 3MF; None when there is no file to read or it cannot be read."""
    if not archive.file_path:
        return None
    path = settings.base_dir / archive.file_path  # SEC-PATH-OK: archive.file_path is DB-stored, internally generated
    if not path.is_file():
        return None
    if path.suffix.lower() != ".3mf":
        return {}
    return threemf_tools.extract_plate_estimates_from_3mf(path)


def refresh_plate_estimates(archive: PrintArchive) -> None:
    """Read the plate estimates of the archive's file into it: when the
    archive is made, or given a file. Does not commit."""
    estimates = read_plate_estimates(archive)
    if estimates is not None:
        archive.plate_estimates = estimates


async def fill_plate_estimates(session_factory, batch: int = 100) -> int:
    """Give every archive not yet read its plate estimates, from its 3MF.
    An archive whose file is missing or unreadable gets ``{}``: it is read
    once, its figures stay the whole file's. Returns how many were read.

    Each is written only while it is still unread: an archive given its 3MF
    meanwhile (``refresh_plate_estimates``) keeps what that read, and one
    deleted meanwhile is skipped."""
    filled = 0
    last_id = 0
    while True:
        async with session_factory() as db:
            rows = (
                await db.execute(
                    select(PrintArchive.id, PrintArchive.file_path)
                    .where(PrintArchive.plate_estimates.is_(None), PrintArchive.id > last_id)
                    .order_by(PrintArchive.id)
                    .limit(batch)
                )
            ).all()
            if not rows:
                return filled
            for row in rows:
                estimates = await asyncio.to_thread(read_plate_estimates, row)
                written = await db.execute(
                    update(PrintArchive)
                    .where(PrintArchive.id == row.id, PrintArchive.plate_estimates.is_(None))
                    .values(plate_estimates=estimates if estimates is not None else {})
                    .execution_options(synchronize_session=False)
                )
                filled += written.rowcount or 0
            last_id = rows[-1].id
            await db.commit()


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


def _global_tray_id(assignment, layout: TrayLayout) -> int:
    return layout.tray_from_slot_key(assignment.ams_id, assignment.tray_id)


async def _spoolman_cost_by_tray(
    printer_id: int, trays: set[int], ams_trays: dict[int, dict], once: "SpoolmanOnce | None" = None
) -> dict[int, float]:
    """Per-kg price of the Spoolman spool in each of ``trays``, from the spool's
    own price or its filament's. A tray without a priced spool is left out.

    The spool is found as the start estimate finds it, with its resolver
    (``archive_cost_estimate._spoolman_rates``): by the tray's RFID tag first,
    then by the slot assignment. The start estimate sets what the run is
    charged (#3580), and a tag-matched spool with no slot assignment was
    otherwise charged at its price after the budget check approved another.

    This runs on queue add and at dispatch, so it asks after a health check,
    for one list of the spools to match the tags against, and for no spool
    outside the mapped trays. ``once`` is the request's, when it prices more
    than one item: the check and the list are then asked for once for all of
    them. Empty when Spoolman cannot be asked, and the estimate uses the
    default rate, as for an unpriced spool.
    """
    from backend.app.services.archive_cost_estimate import SpoolmanOnce, _spoolman_rates

    if not trays:
        return {}
    try:
        rates = await _spoolman_rates(printer_id, trays, ams_trays, once=once or SpoolmanOnce())
    except Exception as exc:  # noqa: BLE001 — an estimate falls back to the default rate
        logger.debug("Spoolman prices unavailable for the queue estimate: %s", exc)
        return {}
    return {tray: per_gram * 1000.0 for tray, per_gram in rates.items()}


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
    spoolman_once: "SpoolmanOnce | None" = None,
) -> float | None:
    """Compute a queue cost without trusting the request's display hint.

    ``spoolman_once`` is shared by a request that prices more than one item,
    so Spoolman is asked for its spools once for all of them."""

    if archive is not None:
        archive_path = settings.base_dir / archive.file_path
        # The plate the dispatch prints (#3580). With none named that is the
        # file's default plate, not the plate the archive printed last.
        run_plate = _dispatched_plate(plate_id, archive_path)
        grams, cost = plate_scoped_run_estimate(archive, archive_path, run_plate)
        # A print of an archive is charged at the spools mapped for it, not at
        # the archive's cost (#3580): with a mapping, what it reserves is
        # priced the same way. Without one, or with no spool in the mapped
        # trays, the charge falls back to the archive's cost and so does this.
        if printer_id is not None and _parse_mapping(ams_mapping) and archive_path.exists():
            usage = threemf_tools.extract_plate_metadata_from_3mf(archive_path, run_plate).filament_usage
            at_spools = await _cost_at_mapped_spools(
                db, usage, ams_mapping, printer_id, total_grams=grams, spoolman_once=spoolman_once
            )
            if at_spools is not None and at_spools[1]:
                return at_spools[0]
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

    at_spools = await _cost_at_mapped_spools(db, usage, ams_mapping, printer_id, spoolman_once=spoolman_once)
    return at_spools[0] if at_spools is not None else None


def _dispatched_plate(plate_id: int | None, file_path: Path) -> int | None:
    """The plate a dispatch of ``file_path`` prints for a queue item's
    ``plate_id``: the scheduler's own rule (``_effective_plate_id``), so a
    job that names none is priced for the plate it prints. None, the whole
    file, when there is no file to read a default plate from."""
    if plate_id is not None and plate_id > 0:
        return plate_id
    if file_path.suffix.lower() != ".3mf" or not file_path.is_file():
        return None
    # Imported here: the scheduler imports this module.
    from backend.app.services.print_scheduler import _effective_plate_id

    return _effective_plate_id(None, file_path)


async def _cost_at_mapped_spools(
    db: AsyncSession,
    usage: list[dict],
    ams_mapping: list[int] | str | None,
    printer_id: int | None,
    total_grams: float | None = None,
    spoolman_once: "SpoolmanOnce | None" = None,
) -> tuple[float, bool] | None:
    """What ``usage`` costs at the spools in the trays its slots are mapped
    to. A slot without a mapped, priced spool is at the default rate, as are
    the grams of ``total_grams`` the slots do not account for. Returns the
    cost, and whether any slot's tray holds a spool; None for no cost.

    Priced as the start estimate prices the run, which is what the run is
    charged (#3580): a slot's tray is read from the mapping by the estimate's
    own rule (``_mapped_tray``, so -1 is the external spool), and a built-in
    spool priced 0 is free. A print only on such spools then costs 0, and
    that is returned, not None."""
    from backend.app.services.archive_cost_estimate import _mapped_tray
    from backend.app.services.printer_manager import printer_manager
    from backend.app.services.spoolman_tracking import build_ams_tray_lookup

    default_cost = await default_cost_per_kg(db)
    cost_by_tray: dict[int, float | None] = {}
    mapping = _parse_mapping(ams_mapping)
    slots: list[tuple[int | None, float]] = []
    if printer_id is not None and mapping:
        state = printer_manager.get_status(printer_id)
        ams_trays = build_ams_tray_lookup(getattr(state, "raw_data", None) or {})
    else:
        ams_trays = {}
    for filament in usage:
        try:
            slot_id = int(filament.get("slot_id") or 0)
            grams = float(filament.get("used_g") or 0)
        except (TypeError, ValueError):
            continue
        slots.append((_mapped_tray(slot_id, mapping, ams_trays) if mapping else None, grams))
    # The prices of the spools the mode's own table puts in those trays. The
    # built-in table may still hold rows from before a switch to Spoolman --
    # nothing clears it since #2812 -- and pricing from a spool the printer is
    # not drawing on would be worse than the default rate this falls back to.
    spoolman_mode = await spoolman_owns_assignments(db)
    if printer_id is not None and mapping and spoolman_mode:
        cost_by_tray = dict(
            await _spoolman_cost_by_tray(
                printer_id, {tray for tray, grams in slots if tray is not None and grams > 0}, ams_trays, spoolman_once
            )
        )
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
        layout = printer_tray_layout(printer_id)
        cost_by_tray = {_global_tray_id(a, layout): a.spool.cost_per_kg for a in assignments}

    total = 0.0
    slot_grams = 0.0
    on_a_spool = False
    for tray_id, grams in slots:
        cost_per_kg = cost_by_tray.get(tray_id) if tray_id is not None else None
        # A built-in spool without a price is at the default rate, one priced
        # 0 is free. Spoolman's table holds priced spools only: 0 there is no
        # price (``_spool_cost_per_gram``). With a default rate of 0 a spool
        # without a price does not count as a spool here: it is not free, and
        # the start estimate gives it no rate either (``_internal_rates``).
        rate = cost_per_kg if cost_per_kg is not None and cost_per_kg >= 0 else None
        if grams > 0 and tray_id in cost_by_tray and (rate is not None or default_cost > 0):
            on_a_spool = True
        if rate is None:
            rate = default_cost
        total += (grams / 1000.0) * rate
        slot_grams += grams
    if total_grams is not None and total_grams > slot_grams:
        total += ((total_grams - slot_grams) / 1000.0) * default_cost

    # Free spools only: 0 is then the cost, as the run is charged (#3580).
    if total > 0 or on_a_spool:
        return round(total, 2), on_a_spool
    return None
