"""Price a print from its spools when it starts, not only when it ends (#3261).

``archive.py`` prices an archive at creation from the built-in Filament catalogue
matched on the primary type, falling back to the global default rate. The
spools that actually feed the print only price it at completion -- the
built-in inventory in ``usage_tracker.on_print_complete``, Spoolman in
``spoolman_tracking._apply_spool_cost_to_archive`` (#2591). In between, the
archive card showed the placeholder, and anyone writing the cost down while a
long print ran recorded a number that later changed without notice.

This runs the completion-time pricing once at print start, on the 3MF's
per-slot estimates instead of charged grams: each slot is priced at the spool
in the tray it is mapped to, grams no spool could price are covered at the
default rate, and the result replaces the placeholder. The figure is still an
estimate, and the archive card says so while the print runs. A completed first
run is re-priced from what was charged; a run that does not finish keeps this
figure as one full print of the file (COST-3) and logs its own cost in the
Print Log, which the card points to (#3318).

Only mappings that are trustworthy at print start are used: the print
command's own ``ams_mapping``, the queue item's stored mapping, and a colour
match against the trays loaded right now. The printer's ``mapping`` field is
deliberately not read here: it still describes the previous job until the
printer pushes an update (see ``spoolman_tracking._resolve_slot_to_tray_fallback``).
With no trustworthy mapping, nothing is priced and the placeholder stays.

Strictly best-effort. Nothing here may fail a print start.
"""

import asyncio
import contextlib
import json
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings as app_settings

logger = logging.getLogger(__name__)

_EXTERNAL_TRAY_IDS = (254, 255)


def _mapped_tray(slot_id: int, slot_to_tray: list, ams_trays: dict[int, dict]) -> int | None:
    """The global tray a 1-based slot is mapped to, or None if the mapping doesn't say.

    Unlike ``spoolman_tracking._resolve_global_tray_id`` there is no positional
    default: a guess would price the slot at an unrelated spool, and the
    default rate is the honest answer for a slot nobody mapped.
    """
    if slot_id <= 0 or slot_id > len(slot_to_tray):
        return None
    tray = slot_to_tray[slot_id - 1]
    if not isinstance(tray, int) or isinstance(tray, bool):
        return None
    if tray >= 0:
        return tray
    if tray == -1:
        # The flat ams_mapping writes the external spool as -1 (see
        # _resolve_global_tray_id).
        for ext_id in _EXTERNAL_TRAY_IDS:
            if ext_id in ams_trays:
                return ext_id
    return None


async def _default_cost_per_kg(db: AsyncSession) -> float:
    from backend.app.api.routes.settings import get_setting

    try:
        value = await get_setting(db, "default_filament_cost")
        return float(value) if value else 25.0
    except (TypeError, ValueError):
        return 25.0


async def _internal_rates(
    db: AsyncSession, printer_id: int, trays: set[int], default_cost_per_kg: float
) -> dict[int, float]:
    """Price per gram of the built-in inventory spool in each tray.

    Same rule as ``usage_tracker``'s completion writer: the spool's own
    ``cost_per_kg``, or the default rate when the spool has none.
    """
    from backend.app.models.spool import Spool
    from backend.app.models.spool_assignment import SpoolAssignment
    from backend.app.services.spoolman_tracking import _global_tray_id_to_ams_slot

    rates: dict[int, float] = {}
    for tray in trays:
        ams_id, tray_id = _global_tray_id_to_ams_slot(tray)
        spool = (
            await db.execute(
                select(Spool)
                .join(SpoolAssignment, SpoolAssignment.spool_id == Spool.id)
                .where(
                    SpoolAssignment.printer_id == printer_id,
                    SpoolAssignment.ams_id == ams_id,
                    SpoolAssignment.tray_id == tray_id,
                )
            )
        ).scalar_one_or_none()  # one assignment per tray (UniqueConstraint)
        if spool is None:
            continue
        cost_per_kg = spool.cost_per_kg if spool.cost_per_kg is not None else default_cost_per_kg
        rates[tray] = cost_per_kg / 1000.0
    return rates


async def _spoolman_rates(printer_id: int, trays: set[int], ams_trays: dict[int, dict]) -> dict[int, float]:
    """Price per gram of the Spoolman spool in each tray.

    Resolves the spool the way usage reporting does (tag first, then the local
    slot assignment, #1459) and prices it with ``_spool_cost_per_gram``, so the
    estimate and the completion figure read the same price fields.
    """
    from backend.app.services.spoolman import get_spoolman_client
    from backend.app.services.spoolman_tracking import (
        _get_printer_serial,
        _global_tray_id_to_ams_slot,
        _resolve_spool_id_via_slot_assignment,
        _resolve_spool_tag,
        _spool_cost_per_gram,
    )

    # The client the integration already set up. No health check: that's an
    # extra request per print, and its failure log says usage reporting is
    # being skipped. An unreachable Spoolman fails per tray below instead.
    client = await get_spoolman_client()
    if client is None:
        return {}
    serial = await _get_printer_serial(printer_id)

    rates: dict[int, float] = {}
    for tray in trays:
        spool = None
        try:
            tray_info = ams_trays.get(tray)
            if tray_info:
                tag = _resolve_spool_tag(tray_info, serial, tray)
                if tag:
                    spool = await client.find_spool_by_tag(tag)
            if spool is None:
                ams_id, tray_id = _global_tray_id_to_ams_slot(tray)
                spool_id = await _resolve_spool_id_via_slot_assignment(printer_id, ams_id, tray_id)
                if spool_id is not None:
                    spool = await client.get_spool(spool_id)
        except Exception as exc:  # noqa: BLE001 -- one unreachable spool leaves its slot at the default rate
            logger.debug("[COST] Tray %s: could not fetch the Spoolman spool: %s", tray, exc)
            continue
        rate = _spool_cost_per_gram(spool)
        if rate is not None:
            rates[tray] = rate
    return rates


def schedule_archive_cost_estimate(
    printer_id: int,
    archive_id: int,
    printer_manager,
    ams_mapping: list | None = None,
    plate_id: int | None = None,
    session_factory=None,
) -> asyncio.Task | None:
    """Run ``estimate_archive_cost_at_start`` in the background, on its own session.

    Print start must not wait for it: in Spoolman mode it makes a request per
    tray, and a slow Spoolman would hold up everything after it.

    ``session_factory`` is the caller's ``async_session``, so the estimate
    opens its session the same way the rest of the caller does.
    """
    from backend.app.core.tasks import spawn_background_task

    if session_factory is None:
        from backend.app.core.database import async_session as session_factory

    try:
        mapping = list(ams_mapping) if ams_mapping else None
    except TypeError:
        mapping = None

    async def _run() -> None:
        async with session_factory() as db:
            await estimate_archive_cost_at_start(db, printer_id, archive_id, printer_manager, mapping, plate_id)

    try:
        task = spawn_background_task(_run(), name=f"archive-cost-estimate-{archive_id}")
    except Exception:  # noqa: BLE001 -- an estimate must never fail a print start
        logger.warning("[COST] Archive %s: could not schedule the cost estimate", archive_id, exc_info=True)
        return None
    _running_estimates[archive_id] = task

    def _forget(done: asyncio.Task, archive_id: int = archive_id) -> None:
        if _running_estimates.get(archive_id) is done:
            del _running_estimates[archive_id]

    task.add_done_callback(_forget)
    return task


# Estimates still running, by archive: completion stops its archive's before
# writing the real cost (COST-4).
_running_estimates: dict[int, asyncio.Task] = {}


async def settle_archive_cost_estimate(archive_id: int) -> None:
    """Stop the archive's start estimate, if still running, before the real cost.

    Completion commits the measured cost before it marks the archive ended; an
    estimate still waiting on Spoolman then saw a printing archive with no runs
    logged, and wrote its figure over the real one on a very short print.
    """
    task = _running_estimates.pop(archive_id, None)
    if task is None or task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


async def estimate_archive_cost_at_start(
    db: AsyncSession,
    printer_id: int,
    archive_id: int,
    printer_manager,
    ams_mapping: list | None = None,
    plate_id: int | None = None,
) -> None:
    """Replace a just-started archive's placeholder cost with a spool-based estimate."""
    try:
        await _estimate(db, printer_id, archive_id, printer_manager, ams_mapping, plate_id)
    except Exception:  # noqa: BLE001 -- an estimate must never fail a print start
        logger.warning("[COST] Archive %s: could not estimate the cost at print start", archive_id, exc_info=True)


async def _estimate(
    db: AsyncSession,
    printer_id: int,
    archive_id: int,
    printer_manager,
    ams_mapping: list | None,
    plate_id: int | None,
) -> None:
    from backend.app.api.routes.settings import get_setting
    from backend.app.models.archive import PrintArchive
    from backend.app.models.print_log import PrintLogEntry
    from backend.app.models.print_queue import PrintQueueItem
    from backend.app.services.spoolman_tracking import build_ams_tray_lookup
    from backend.app.services.usage_tracker import _match_slots_by_color
    from backend.app.utils.threemf_tools import extract_filament_usage_from_3mf

    archive = (await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))).scalar_one_or_none()
    if archive is None or not archive.file_path or not archive.filament_used_grams:
        return

    # A reprint keeps its first run's cost on the card (#1378); completion
    # doesn't overwrite it either.
    existing_runs = (
        await db.execute(select(func.count(PrintLogEntry.id)).where(PrintLogEntry.archive_id == archive_id))
    ).scalar()
    if existing_runs:
        return

    full_path = (
        app_settings.base_dir / archive.file_path
    )  # SEC-PATH-OK: archive.file_path is DB-stored, internally generated
    if not full_path.exists():
        return

    queue_item = (
        (
            await db.execute(
                select(PrintQueueItem)
                .where(PrintQueueItem.archive_id == archive_id)
                .where(PrintQueueItem.printer_id == printer_id)
                .where(PrintQueueItem.status == "printing")
            )
        )
        .scalars()
        .first()
    )
    if plate_id is None and queue_item is not None:
        plate_id = queue_item.plate_id

    filament_usage = extract_filament_usage_from_3mf(full_path, plate_id) or []
    used = [(u.get("slot_id", 0), u.get("used_g", 0) or 0) for u in filament_usage]
    used = [(slot, grams) for slot, grams in used if grams > 0]
    if not used:
        return

    state = printer_manager.get_status(printer_id)
    raw_data = getattr(state, "raw_data", None) or {}
    ams_trays = build_ams_tray_lookup(raw_data)

    slot_to_tray = ams_mapping or None
    source = "print_cmd" if slot_to_tray else None
    if not slot_to_tray and queue_item is not None and queue_item.ams_mapping:
        try:
            slot_to_tray = json.loads(queue_item.ams_mapping)
            source = "queue"
        except json.JSONDecodeError:
            slot_to_tray = None
    if not slot_to_tray:
        slot_to_tray = _match_slots_by_color(filament_usage, raw_data.get("ams"))
        source = "color_match" if slot_to_tray else None
    if not slot_to_tray:
        logger.info("[COST] Archive %s: no slot-to-tray mapping at print start, keeping the placeholder", archive_id)
        return

    slot_trays = {slot: _mapped_tray(slot, slot_to_tray, ams_trays) for slot, _ in used}
    trays = {tray for tray in slot_trays.values() if tray is not None}
    if not trays:
        return

    default_cost_per_kg = await _default_cost_per_kg(db)
    spoolman_enabled = (await get_setting(db, "spoolman_enabled") or "").lower() == "true"
    if spoolman_enabled:
        rates = await _spoolman_rates(printer_id, trays, ams_trays)
    else:
        rates = await _internal_rates(db, printer_id, trays, default_cost_per_kg)

    cost = 0.0
    priced_grams = 0.0
    for slot, grams in used:
        rate = rates.get(slot_trays[slot])
        if rate is None:
            continue
        cost += grams * rate
        priced_grams += grams
    if priced_grams <= 0:
        return

    # Grams no spool priced -- unmapped slots, empty trays, unpriced spools, and
    # anything the 3MF didn't attribute to a slot -- at the default rate, in
    # one subtraction against the archive's own total, as both completion
    # writers do.
    unpriced_grams = max(0.0, archive.filament_used_grams - priced_grams)
    if unpriced_grams > 0 and default_cost_per_kg > 0:
        cost += (unpriced_grams / 1000.0) * default_cost_per_kg
    if cost <= 0:
        return

    # The Spoolman lookups above can take a while. If the print already ended
    # -- a failure seconds in -- completion has written the real figure, and
    # an estimate must not replace it.
    await db.refresh(archive)
    still_running_first_run = (
        archive.status == "printing"
        and not (
            await db.execute(select(func.count(PrintLogEntry.id)).where(PrintLogEntry.archive_id == archive_id))
        ).scalar()
    )
    if not still_running_first_run:
        return

    new_cost = round(cost, 2)
    if new_cost == archive.cost:
        return
    logger.info(
        "[COST] Archive %s: estimated cost %s -> %s at print start (%.2fg priced from %s spools via %s mapping, "
        "%.2fg at the default rate)",
        archive_id,
        archive.cost,
        new_cost,
        priced_grams,
        "Spoolman" if spoolman_enabled else "inventory",
        source,
        unpriced_grams,
    )
    archive.cost = new_cost
    await db.commit()

    from backend.app.core.websocket import ws_manager

    await ws_manager.send_archive_updated({"id": archive_id, "cost": new_cost})
