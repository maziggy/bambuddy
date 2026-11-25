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

Every run is priced this way, not only the first (#3580): the figure for the
plate a run prints is kept on the run opened at its start
(``PrintLogEntry.planned_cost``), and billing charges the run from it. The
archive's cost stays its first run's (#1378), so a reprint on other or
re-priced spools is charged at those while the card does not change.

Strictly best-effort. Nothing here may fail a print start.
"""

import asyncio
import contextlib
import json
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings as app_settings
from backend.app.utils.tray_ids import RACK_EXTERNAL_IDS

logger = logging.getLogger(__name__)

# Every external input id, 254/255 first (a rack printer's 247-253 after).
_EXTERNAL_TRAY_IDS = (254, 255, *RACK_EXTERNAL_IDS[:-2])


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
    ``cost_per_kg``, or the default rate when the spool has none. A spool
    priced 0 is free. One without a price is not: with a default rate of 0 it
    gets no rate here, as a tray without a spool gets none, so a run only on
    such spools is not priced 0 but left to the archive's cost (#3580).
    """
    from backend.app.models.spool import Spool
    from backend.app.models.spool_assignment import SpoolAssignment
    from backend.app.services.printer_manager import printer_tray_layout
    from backend.app.services.spoolman_tracking import _global_tray_id_to_ams_slot

    rates: dict[int, float] = {}
    layout = printer_tray_layout(printer_id)
    for tray in trays:
        ams_id, tray_id = _global_tray_id_to_ams_slot(tray, layout)
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
        cost_per_kg = spool.cost_per_kg
        if cost_per_kg is None or cost_per_kg < 0:
            if default_cost_per_kg <= 0:
                continue
            cost_per_kg = default_cost_per_kg
        rates[tray] = cost_per_kg / 1000.0
    return rates


class SpoolmanOnce:
    """What the queue estimates of one request ask Spoolman once (#3580): the
    health-checked client, and the list of spools the trays' tags are matched
    against. A bulk edit prices every item it changes, and each asked again.

    A failure is kept too: a Spoolman that did not answer is not asked again
    for every tray and item. ``None`` then, and the caller goes on without.
    """

    def __init__(self) -> None:
        self._client = None
        self._client_asked = False
        self._spools: list[dict] | None = None
        self._spools_asked = False

    async def client(self):
        if not self._client_asked:
            self._client_asked = True
            from backend.app.services.spoolman_tracking import _get_spoolman_client_with_fallback

            try:
                self._client = await _get_spoolman_client_with_fallback()
            except Exception as exc:  # noqa: BLE001 -- an estimate falls back to the default rate
                logger.debug("[COST] Spoolman cannot be asked for the queue estimate: %s", exc)
        return self._client

    async def spools(self, client) -> list[dict] | None:
        if not self._spools_asked:
            self._spools_asked = True
            try:
                self._spools = await client.get_spools()
            except Exception as exc:  # noqa: BLE001 -- the slot assignments still find their spools
                logger.debug("[COST] Could not list the Spoolman spools: %s", exc)
        return self._spools


async def _spoolman_rates(
    printer_id: int,
    trays: set[int],
    ams_trays: dict[int, dict],
    once: SpoolmanOnce | None = None,
) -> dict[int, float]:
    """Price per gram of the Spoolman spool in each tray.

    Resolves the spool the way usage reporting does (tag first, then the local
    slot assignment, #1459) and prices it with ``_spool_cost_per_gram``, so the
    estimate and the completion figure read the same price fields.

    The queue's reservation resolves its spools here too (#3580), so it prices
    the spool the run is then charged at. It passes ``once``: the client is
    then the request's health-checked one, and the tags are matched against
    its one list of the spools instead of a lookup per tray, as it runs on
    every queue add and dispatch.

    A tag that could not be looked up (the list or the lookup failed) is not
    the end of its tray: the slot assignment is still tried.
    """
    from backend.app.services.printer_manager import printer_tray_layout
    from backend.app.services.spoolman import get_spoolman_client
    from backend.app.services.spoolman_tracking import (
        _get_printer_serial,
        _global_tray_id_to_ams_slot,
        _resolve_spool_id_via_slot_assignment,
        _resolve_spool_tag,
        _spool_cost_per_gram,
    )

    # At a print start, the client the integration already set up. No health
    # check: that's an extra request per print, and its failure log says usage
    # reporting is being skipped. An unreachable Spoolman fails per tray below
    # instead.
    client = await once.client() if once is not None else await get_spoolman_client()
    if client is None:
        return {}
    serial = await _get_printer_serial(printer_id)
    layout = printer_tray_layout(printer_id)

    rates: dict[int, float] = {}
    for tray in trays:
        spool = None
        try:
            tray_info = ams_trays.get(tray)
            tag = _resolve_spool_tag(tray_info, serial, tray, layout) if tray_info else None
            if tag and once is not None:
                spool_list = await once.spools(client)
                if spool_list is not None:
                    spool = await client.find_spool_by_tag(tag, spool_list)
            elif tag:
                spool = await client.find_spool_by_tag(tag)
        except Exception as exc:  # noqa: BLE001 -- the slot assignment may still know the spool
            logger.debug("[COST] Tray %s: could not look its tag up in Spoolman: %s", tray, exc)
        try:
            if spool is None:
                ams_id, tray_id = _global_tray_id_to_ams_slot(tray, layout)
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
    run_only: bool = False,
) -> asyncio.Task | None:
    """Run ``estimate_archive_cost_at_start`` in the background, on its own session.

    Print start must not wait for it: in Spoolman mode it makes a request per
    tray, and a slow Spoolman would hold up everything after it.

    ``session_factory`` is the caller's ``async_session``, so the estimate
    opens its session the same way the rest of the caller does.

    ``run_only`` is for a print seen again (a restart resuming it, #3580): the
    estimate of its start may have gone with the restart, so the run is priced
    if it has no price yet, and the card is left alone. While the start's own
    estimate still runs, that one is returned and nothing is scheduled.
    """
    from backend.app.core.tasks import spawn_background_task

    if session_factory is None:
        from backend.app.core.database import async_session as session_factory

    try:
        mapping = list(ams_mapping) if ams_mapping else None
    except TypeError:
        mapping = None

    key = (printer_id, archive_id)
    if run_only:
        running = _running_estimates.get(key)
        if running is not None and not running.done():
            return running

    async def _run() -> None:
        async with session_factory() as db:
            if run_only:
                await price_run_without_a_price(db, printer_id, archive_id, printer_manager, mapping, plate_id)
            else:
                await estimate_archive_cost_at_start(db, printer_id, archive_id, printer_manager, mapping, plate_id)

    try:
        task = spawn_background_task(_run(), name=f"archive-cost-estimate-{archive_id}")
    except Exception:  # noqa: BLE001 -- an estimate must never fail a print start
        logger.warning("[COST] Archive %s: could not schedule the cost estimate", archive_id, exc_info=True)
        return None
    _running_estimates[key] = task

    def _forget(done: asyncio.Task) -> None:
        if _running_estimates.get(key) is done:
            del _running_estimates[key]

    task.add_done_callback(_forget)
    return task


# Estimates still running, by the print they price: completion stops its own
# before writing the real cost (COST-4). By printer too (#3580): an archive
# printing on two printers has an estimate for each run, and one's start or
# end must not take the other's.
_running_estimates: dict[tuple[int, int], asyncio.Task] = {}


async def settle_archive_cost_estimate(printer_id: int, archive_id: int) -> None:
    """Stop the start estimate of the archive's print on the printer, if still
    running, before the real cost.

    Completion commits the measured cost before it marks the archive ended; an
    estimate still waiting on Spoolman then saw a printing archive with no runs
    logged, and wrote its figure over the real one on a very short print. It
    would price the run after its charge the same way.
    """
    task = _running_estimates.pop((printer_id, archive_id), None)
    if task is None or task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


# How long a print's end waits for the price of a run its start did not price.
RUN_PRICING_TIMEOUT_SECONDS = 10.0


async def price_unpriced_run(
    printer_id: int,
    archive_id: int,
    printer_manager,
    ams_mapping: list | None = None,
    plate_id: int | None = None,
    session_factory=None,
    timeout: float = RUN_PRICING_TIMEOUT_SECONDS,
) -> None:
    """Price the archive's run open on the printer now, at its end, if its
    start has not (#3580).

    The start's estimate runs in the background and is stopped at the end
    (``settle_archive_cost_estimate``): a print that failed within seconds, a
    slow Spoolman or a restart could leave the run without a price, and it was
    then charged the archive's first-run cost without a word. Does nothing for
    a run that has a price, and nothing with billing off. Waits ``timeout``
    seconds at most; a run still not priced is charged from the archive's
    cost, as before.
    """
    if session_factory is None:
        from backend.app.core.database import async_session as session_factory

    async def _price() -> None:
        async with session_factory() as db:
            await price_run_without_a_price(db, printer_id, archive_id, printer_manager, ams_mapping, plate_id)

    try:
        await asyncio.wait_for(_price(), timeout)
    except Exception:  # noqa: BLE001 -- the charge falls back to the archive's cost
        logger.warning("[COST] Archive %s: could not price its run at the print's end", archive_id, exc_info=True)


async def price_run_without_a_price(
    db: AsyncSession,
    printer_id: int,
    archive_id: int,
    printer_manager,
    ams_mapping: list | None = None,
    plate_id: int | None = None,
) -> None:
    """Price the archive's run open on the printer if it has no price yet,
    and nothing else: the card is its start's to write (#3580)."""
    try:
        await _estimate(db, printer_id, archive_id, printer_manager, ams_mapping, plate_id, run_only=True)
    except Exception:  # noqa: BLE001 -- the charge falls back to the archive's cost
        logger.warning("[COST] Archive %s: could not price its open run", archive_id, exc_info=True)


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
    run_only: bool = False,
) -> None:
    """``run_only`` prices the open run if it has no price yet and nothing
    else: the card is the start's to write."""
    from backend.app.api.routes.settings import get_setting
    from backend.app.models.archive import PrintArchive
    from backend.app.models.print_queue import PrintQueueItem
    from backend.app.services.finance_budget import is_billing_enabled
    from backend.app.services.print_cost_estimate import plate_scoped_run_estimate
    from backend.app.services.print_log import _open_run_on
    from backend.app.services.spoolman_tracking import build_ams_tray_lookup
    from backend.app.services.usage_tracker import _match_slots_by_color
    from backend.app.utils.threemf_tools import extract_filament_usage_from_3mf

    archive = (await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))).scalar_one_or_none()
    if archive is None or not archive.file_path or not archive.filament_used_grams:
        return

    # A reprint keeps its first run's cost on the card (#1378); completion
    # doesn't overwrite it either. Its own run is still priced, for billing
    # (#3580): without a run opened at this start there is nothing to price.
    is_reprint = bool(await _ended_runs(db, archive_id))

    # The run of this archive open on the printer: the one this start opened.
    # Its price is only what billing charges, so with billing off none is
    # worked out, and a reprint asks its spools nothing, as before #3580.
    run = await _open_run_on(db, archive_id, printer_id) if await is_billing_enabled(db) else None
    if run_only and run is not None and run.planned_cost is not None:
        return
    if run is None and (is_reprint or run_only):
        return
    prices_card = not is_reprint and not run_only

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
    if queue_item is None and run is not None and run.queue_item_id is not None:
        # At the print's end (``price_unpriced_run``) its item is no longer printing.
        queue_item = await db.get(PrintQueueItem, run.queue_item_id)
    if plate_id is None and queue_item is not None:
        plate_id = queue_item.plate_id

    state = printer_manager.get_status(printer_id)
    raw_data = getattr(state, "raw_data", None) or {}
    ams_trays = build_ams_tray_lookup(raw_data)
    default_cost_per_kg = await _default_cost_per_kg(db)
    spoolman_enabled = (await get_setting(db, "spoolman_enabled") or "").lower() == "true"
    source_name = "Spoolman" if spoolman_enabled else "inventory"
    rates: dict[int, float] = {}
    looked_up: set[int] = set()
    unmapped_logged = False

    async def at_spools(plate: int | None) -> tuple[float, float, str] | None:
        """The plate's slots at the spools mapped for this start: their cost,
        the grams they cover, and where the mapping came from. None when
        nothing of it can be priced. ``plate`` None is the whole file."""
        filament_usage = extract_filament_usage_from_3mf(full_path, plate) or []
        used = [(u.get("slot_id", 0), u.get("used_g", 0) or 0) for u in filament_usage]
        used = [(slot, grams) for slot, grams in used if grams > 0]
        if not used:
            return None

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
        if not slot_to_tray or source is None:
            nonlocal unmapped_logged
            if not unmapped_logged:  # once, though the card and the run may both ask
                unmapped_logged = True
                logger.info(
                    "[COST] Archive %s: no slot-to-tray mapping at print start, keeping the placeholder", archive_id
                )
            return None

        slot_trays = {slot: _mapped_tray(slot, slot_to_tray, ams_trays) for slot, _ in used}
        trays = {tray for tray in slot_trays.values() if tray is not None}
        if not trays:
            return None
        # Each tray is looked up once, whichever plate asks for it first.
        new_trays = trays - looked_up
        if new_trays:
            looked_up.update(new_trays)
            if spoolman_enabled:
                rates.update(await _spoolman_rates(printer_id, new_trays, ams_trays))
            else:
                rates.update(await _internal_rates(db, printer_id, new_trays, default_cost_per_kg))

        cost = 0.0
        priced_grams = 0.0
        for slot, grams in used:
            tray = slot_trays[slot]
            rate = rates.get(tray) if tray is not None else None
            if rate is None:
                continue
            cost += grams * rate
            priced_grams += grams
        if priced_grams <= 0:
            return None
        return cost, priced_grams, source

    def with_the_rest(spools: tuple[float, float, str], total_grams: float) -> tuple[float, float]:
        """Grams no spool priced -- unmapped slots, empty trays, unpriced spools,
        and anything the 3MF didn't attribute to a slot -- at the default rate,
        in one subtraction against the total, as both completion writers do.
        Returns the cost and those grams."""
        cost, priced_grams, _source = spools
        unpriced_grams = max(0.0, total_grams - priced_grams)
        return round(cost + (unpriced_grams / 1000.0) * max(0.0, default_cost_per_kg), 2), unpriced_grams

    # The card: the plate the start named, against the archive's own total.
    # Only a first run's is written, so only a first run's is worked out.
    card_spools = await at_spools(plate_id) if prices_card else None
    card_cost, card_unpriced_grams = with_the_rest(card_spools, archive.filament_used_grams) if card_spools else (0, 0)

    # The run's own figure, which billing charges (#3580): the plate the run
    # opened at this start carries, against that plate's grams. Both are what
    # the charge at its end works from (``plate_scoped_run_estimate``). A start
    # at the printer names no plate, and the card is then the whole file's
    # while the run still prints one plate of it: the run is priced for that
    # plate, on its own.
    run_spools = None
    run_cost, run_unpriced_grams, run_grams = 0.0, 0.0, 0.0
    if run is not None:
        run_plate = run.plate_id
        if run_plate is None:
            run_plate = plate_id if plate_id is not None else archive.plate_id
        run_grams = plate_scoped_run_estimate(archive, full_path, run_plate)[0] or archive.filament_used_grams
        run_spools = card_spools if prices_card and run_plate == plate_id else await at_spools(run_plate)
        if run_spools:
            run_cost, run_unpriced_grams = with_the_rest(run_spools, run_grams)
    if not card_spools and not run_spools:
        return

    # The Spoolman lookups above can take a while. If the print already ended
    # -- a failure seconds in -- completion has written the real figure, and
    # an estimate must not replace it. The run is read again for the same
    # reason: ended, it is no longer open, and its charge is written.
    await db.refresh(archive)
    if run is not None:
        run = await _open_run_on(db, archive_id, printer_id)
    still_running_first_run = archive.status == "printing" and not await _ended_runs(db, archive_id)
    # A run priced 0 is kept as that: its spools are free (priced 0), and it
    # is charged nothing, not the archive's cost a run nothing priced gets.
    if run is not None and (not run_spools or (run.planned_cost, run.planned_grams) == (run_cost, run_grams)):
        run = None
    price_card = bool(card_spools) and still_running_first_run and card_cost > 0 and card_cost != archive.cost
    if run is None and not price_card:
        return

    if run is not None and run_spools:
        logger.info(
            "[COST] Archive %s: run %s planned at %s for %.2fg (%.2fg priced from %s spools via %s mapping, "
            "%.2fg at the default rate)",
            archive_id,
            run.id,
            run_cost,
            run_grams,
            run_spools[1],
            source_name,
            run_spools[2],
            run_unpriced_grams,
        )
        # Together: the charge of a run that did not finish is its share of
        # these grams, of this cost.
        run.planned_cost = run_cost
        run.planned_grams = run_grams
    if price_card and card_spools:
        logger.info(
            "[COST] Archive %s: estimated cost %s -> %s at print start (%.2fg priced from %s spools via %s mapping, "
            "%.2fg at the default rate)",
            archive_id,
            archive.cost,
            card_cost,
            card_spools[1],
            source_name,
            card_spools[2],
            card_unpriced_grams,
        )
        archive.cost = card_cost
    await db.commit()

    if price_card:
        from backend.app.core.websocket import ws_manager

        await ws_manager.send_archive_updated({"id": archive_id, "cost": card_cost})


async def _ended_runs(db: AsyncSession, archive_id: int) -> int:
    """How many prints of the archive have ended. A run still printing is open
    and not counted (``PrintLogEntry``'s open runs are left out of reads)."""
    from backend.app.models.print_log import PrintLogEntry

    return (
        await db.execute(select(func.count(PrintLogEntry.id)).where(PrintLogEntry.archive_id == archive_id))
    ).scalar() or 0
