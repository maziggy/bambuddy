"""Reprice recorded spool usage at the spools' current prices (#3414).

A usage row stores what its grams cost when the print completed. Recalculate
Costs adds those up, so a spool price corrected afterwards changed nothing:
the old price was read back from the rows. This puts the spool's current
price on its rows first, and moves the cost of the run each row was charged
in by the same difference, as statistics read a run's own cost.

A row whose spool is gone keeps what it cost: there is no price left to apply.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import PrintLogEntry
from backend.app.models.spool import Spool
from backend.app.models.spool_usage_history import SpoolUsageHistory

logger = logging.getLogger(__name__)

# A row is recorded as its run completes. Its run is the one whose start and
# end (plus this much for the completion to be processed) hold the row's
# time; a row that falls in none -- times that were stored in local time on
# older installs -- moves no run rather than the wrong one.
_COMPLETION_SLACK = timedelta(minutes=30)


@dataclass
class RepriceResult:
    repriced: int = 0
    # Spoolman rows left as they were because Spoolman could not be read.
    spoolman_unreachable: int = 0
    # Runs whose cost their own usage set: the archive's new cost is not
    # theirs to take (``mirror_archive_cost``).
    priced_runs: set[int] = field(default_factory=set)


async def _spoolman_client(db: AsyncSession):
    """The Spoolman client, as usage reporting gets it, off this session's
    settings; None when it can't be reached."""
    from backend.app.api.routes.settings import get_setting
    from backend.app.services.spoolman import get_spoolman_client, init_spoolman_client

    client = await get_spoolman_client()
    if client is None:
        url = await get_setting(db, "spoolman_url")
        if not url:
            return None
        try:
            client = await init_spoolman_client(url)
        except ValueError as exc:
            logger.warning("[COST] Spoolman URL rejected: %s", exc)
            return None
    if client is None or not await client.health_check():
        return None
    return client


async def _spoolman_rates(db: AsyncSession, spool_ids: set[int]) -> dict[int, float | None] | None:
    """Per-gram price of each Spoolman spool still in Spoolman (None for an
    unpriced one); None when Spoolman can't be read."""
    from backend.app.services.spoolman_tracking import _spool_cost_per_gram

    client = await _spoolman_client(db)
    if client is None:
        return None
    try:
        spools = await client.get_all_spools(allow_archived=True)
    except Exception as exc:  # noqa: BLE001 -- the rows are kept, the recalculation goes on
        logger.warning("[COST] Spoolman spools could not be read to reprice their usage: %s", exc)
        return None
    return {
        spool["id"]: _spool_cost_per_gram(spool)
        for spool in spools
        if isinstance(spool, dict) and spool.get("id") in spool_ids
    }


def _run_of(row: SpoolUsageHistory, runs: list[PrintLogEntry]) -> PrintLogEntry | None:
    """The run the row was charged in, of the ``runs`` of its archive (or of
    no archive, the archive deleted): on its printer, and running when the
    row was recorded at its end. A run without an end time holds the rows
    until the next run on the printer starts."""
    if row.created_at is None:
        return None
    if row.printer_id is None and row.archive_id is None:
        return None
    candidates = sorted(
        (
            run
            for run in runs
            if run.started_at is not None and (row.printer_id is None or run.printer_id == row.printer_id)
        ),
        key=lambda run: run.started_at or datetime.min,
    )
    match = None
    for index, run in enumerate(candidates):
        started = run.started_at
        if started is None or started > row.created_at:
            break
        if run.completed_at is not None:
            end = run.completed_at + _COMPLETION_SLACK
        elif index + 1 < len(candidates):
            end = candidates[index + 1].started_at
        else:
            end = None
        match = run if end is None or row.created_at <= end else None
    return match


def _in_run(cost: float | None, grams: float, spoolman: bool, default_cost_per_kg: float) -> float:
    """What a row's cost put into its run's cost. A Spoolman charge without a
    price was covered at the default rate (``spoolman_run_cost``, or the
    archive's figure); a built-in one without a price had none to add."""
    if cost is not None:
        return cost
    return round(grams / 1000.0 * default_cost_per_kg, 2) if spoolman else 0.0


async def reprice_usage(
    db: AsyncSession,
    archive_ids: Select | None,
    *,
    default_cost_per_kg: float,
    spoolman_enabled: bool,
) -> RepriceResult:
    """Reprice the usage rows of the archives ``archive_ids`` selects (every
    row, those of no archive too, when it is None) and the runs they were
    charged in. Does not commit.

    A run is moved by the change of its own rows. The one exception is an
    archive whose rows all belong to its latest run, still at the archive's
    cost: that run is the archive's figure and follows the archive's new cost
    as a whole (``mirror_archive_cost``), default-rate grams included.
    """
    result = RepriceResult()
    query = select(SpoolUsageHistory)
    runs_query = select(PrintLogEntry)
    if archive_ids is not None:
        query = query.where(SpoolUsageHistory.archive_id.in_(archive_ids))
        runs_query = runs_query.where(PrintLogEntry.archive_id.in_(archive_ids))
    rows = list((await db.scalars(query)).all())
    if not rows:
        return result

    spool_ids = {row.spool_id for row in rows if row.spool_id is not None}
    spool_rates = dict(
        (await db.execute(select(Spool.id, Spool.cost_per_kg).where(Spool.id.in_(spool_ids)))).tuples().all()
    )
    spoolman_ids = {row.spoolman_spool_id for row in rows if row.spoolman_spool_id is not None}
    spoolman_rates = await _spoolman_rates(db, spoolman_ids) if spoolman_ids and spoolman_enabled else {}

    runs_by_archive: dict[int | None, list[PrintLogEntry]] = defaultdict(list)
    for run in (await db.scalars(runs_query)).all():
        runs_by_archive[run.archive_id].append(run)
    owner = {row.id: _run_of(row, runs_by_archive.get(row.archive_id, [])) for row in rows}

    archive_ids_seen = {row.archive_id for row in rows if row.archive_id is not None}
    archive_costs = dict(
        (await db.execute(select(PrintArchive.id, PrintArchive.cost).where(PrintArchive.id.in_(archive_ids_seen))))
        .tuples()
        .all()
    )
    follows_archive: set[int] = set()
    rows_by_archive: dict[int, list[SpoolUsageHistory]] = defaultdict(list)
    for row in rows:
        if row.archive_id is not None:
            rows_by_archive[row.archive_id].append(row)
    for archive_id, archive_rows in rows_by_archive.items():
        runs = runs_by_archive.get(archive_id, [])
        if not runs or archive_id not in archive_costs:
            continue
        latest = max(runs, key=lambda run: run.id)
        if (
            latest.status == "completed"
            and latest.cost in (None, archive_costs[archive_id])
            and all(owner[row.id] is latest for row in archive_rows)
        ):
            follows_archive.add(latest.id)

    for row in rows:
        grams = row.weight_used or 0.0
        run = owner[row.id]
        if run is not None and run.id not in follows_archive:
            result.priced_runs.add(run.id)
        if row.spool_id is not None:
            if row.spool_id not in spool_rates:
                continue  # the spool was deleted
            cost_per_kg = spool_rates[row.spool_id]
            if cost_per_kg is None:
                cost_per_kg = default_cost_per_kg
            # As the usage tracker prices it at completion.
            new_cost = round(grams / 1000.0 * cost_per_kg, 2) if cost_per_kg > 0 else None
        elif row.spoolman_spool_id is not None:
            if spoolman_rates is None:
                result.spoolman_unreachable += 1
                continue
            if row.spoolman_spool_id not in spoolman_rates:
                continue  # Spoolman is off, or the spool is gone from it
            rate = spoolman_rates[row.spoolman_spool_id]
            # Unpriced, as completion records it: the archive covers those
            # grams at the default rate.
            new_cost = round(grams * rate, 2) if rate is not None else None
        else:
            continue  # the spool was deleted
        if new_cost == row.cost:
            continue
        spoolman = row.spool_id is None
        delta = _in_run(new_cost, grams, spoolman, default_cost_per_kg) - _in_run(
            row.cost, grams, spoolman, default_cost_per_kg
        )
        row.cost = new_cost
        result.repriced += 1
        if run is not None and run.id not in follows_archive and delta:
            run.cost = round(max(0.0, (run.cost or 0.0) + delta), 2)
    if spoolman_rates is None and result.spoolman_unreachable:
        logger.warning(
            "[COST] Spoolman not reachable: %d usage record(s) kept their price", result.spoolman_unreachable
        )
    return result
