"""Service for writing independent print log entries.

Log entries are written to a separate table and never touch archives or queue items.
"""

import logging
from collections.abc import Set as AbstractSet
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.print_log import PrintLogEntry

logger = logging.getLogger(__name__)


def wear_cost_for_run(duration_seconds: int | None, wear_cost_per_hour: float | None) -> float | None:
    """Printer wear for one run: its duration at the printer's hourly rate (#694).

    None when the printer has no rate or the run has no measured duration (a
    reconciled run stores 0 because its real end time is unknown).
    """
    if not isinstance(wear_cost_per_hour, int | float) or not isinstance(duration_seconds, int):
        return None
    if wear_cost_per_hour <= 0 or duration_seconds <= 0:
        return None
    return round(duration_seconds / 3600 * wear_cost_per_hour, 3)


async def write_log_entry(
    db: AsyncSession,
    *,
    status: str,
    archive_id: int | None = None,
    queue_item_id: int | None = None,
    print_name: str | None = None,
    printer_name: str | None = None,
    printer_id: int | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    filament_type: str | None = None,
    filament_color: str | None = None,
    filament_used_grams: float | None = None,
    cost: float | None = None,
    energy_kwh: float | None = None,
    energy_cost: float | None = None,
    wear_cost_per_hour: float | None = None,
    failure_reason: str | None = None,
    thumbnail_path: str | None = None,
    created_by_id: int | None = None,
    created_by_username: str | None = None,
    reconciled: bool = False,
    duration_unmeasured: bool = False,
) -> PrintLogEntry:
    """Write a print log entry.

    ``reconciled`` marks a synthetic completion written when a stale
    ``status="printing"`` archive is closed out at reconnect. Its real end time
    is unknown — the print stopped somewhere during the disconnect and
    ``completed_at`` is only the reconnect moment — so ``completed_at -
    started_at`` would bank the entire disconnect gap as print time, adding
    hundreds of fictitious hours across a farm of stale rows (#2592). For those
    entries we store an explicit ``0`` ("no measured runtime") rather than a
    fabricated duration; the stats total trusts a stored 0 instead of
    recomputing from the stale timestamps.

    ``duration_unmeasured`` marks a run closed at reconnect, whose times were
    not measured (#3310): ``completed_at`` is its start plus the slicer's
    estimate, or the reconnect. The time accuracy leaves it out.
    """
    if reconciled:
        duration: int | None = 0
    elif started_at and completed_at:
        duration = int((_utc(completed_at) - _utc(started_at)).total_seconds())
    else:
        duration = None

    fields = {
        "archive_id": archive_id,
        "queue_item_id": queue_item_id,
        "print_name": print_name,
        "printer_name": printer_name,
        "printer_id": printer_id,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_seconds": duration,
        "filament_type": filament_type,
        "filament_color": filament_color,
        "filament_used_grams": filament_used_grams,
        "cost": cost,
        "energy_kwh": energy_kwh,
        "energy_cost": energy_cost,
        "wear_cost": wear_cost_for_run(duration, wear_cost_per_hour),
        "duration_unmeasured": True if duration_unmeasured else None,
        "failure_reason": failure_reason,
        "thumbnail_path": thumbnail_path,
        "created_by_id": created_by_id,
        "created_by_username": created_by_username,
    }
    entry = await _entry_of_this_run(db, archive_id, printer_id, started_at)
    if entry is None:
        entry = PrintLogEntry(**fields)
        db.add(entry)
    elif entry.status != status:
        # The run ended differently from how it was first logged: a print
        # stopped while its printer was offline that finished after all. The
        # real outcome replaces the stop rather than counting the run twice.
        for name, value in fields.items():
            if value is None and name in _KEPT_WHEN_NOT_REPORTED:
                continue
            setattr(entry, name, value)
    # The same outcome reported again (a repeated completion after a reconnect)
    # changes nothing: the repeat no longer has the run's tracked usage, and the
    # entry may have been edited or given its energy since.
    await db.flush()
    return entry


async def mirror_archive_cost(
    db: AsyncSession, archive, previous_cost: float | None, *, skip_runs: AbstractSet[int] = frozenset()
) -> None:
    """Give the archive's newest run the archive's new cost, if it had the old one.

    Statistics, project totals and batch sums read a run's cost. A completed
    run without its own spool price took the archive's (or none), so a new
    archive cost belongs to it too. A run its spools priced keeps that price,
    and a failed or cancelled run keeps its own: it printed only part of it.
    A run in ``skip_runs`` was given its own new cost already.
    """
    entry = await db.scalar(
        select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id).order_by(PrintLogEntry.id.desc()).limit(1)
    )
    if entry is None or entry.id in skip_runs:
        return
    if entry.status == "completed" and entry.cost in (None, previous_cost):
        entry.cost = archive.cost


async def other_runs_of(db: AsyncSession, archive) -> int:
    """How many log entries *archive* has from runs other than its current one.

    The archive card keeps its first run's figures; a run whose own entry is
    already written (its stop, logged while the printer was offline) is still
    the first run.
    """
    # Without a start time or printer on the archive, ``!= None`` reads as
    # ``IS NOT NULL``: every entry is another run's.
    query = select(func.count(PrintLogEntry.id)).where(
        PrintLogEntry.archive_id == archive.id,
        or_(
            PrintLogEntry.started_at.is_(None),
            PrintLogEntry.started_at != archive.started_at,
            PrintLogEntry.printer_id.is_(None),
            PrintLogEntry.printer_id != archive.printer_id,
        ),
    )
    return await db.scalar(query) or 0


# Who and what the run was, which a repeated report may no longer know: a
# completion that finds the queue item already closed reports none. Its
# outcome (status, reason, grams, cost) is replaced as reported.
_KEPT_WHEN_NOT_REPORTED = frozenset(
    {"queue_item_id", "print_name", "printer_name", "thumbnail_path", "created_by_id", "created_by_username"}
)


def _utc(moment: datetime) -> datetime:
    """Naive UTC, as the columns store it; an aware time is converted first."""
    return moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment


async def _entry_of_this_run(
    db: AsyncSession,
    archive_id: int | None,
    printer_id: int | None,
    started_at: datetime | None,
) -> PrintLogEntry | None:
    """The entry already written for this run, if any.

    A run is its archive, printer and start time together: plates of one
    archive printing on two printers share the archive row (and can share its
    start time), and a reprint starts at a new time. The queue item is not
    part of it: a repeated completion finds the item already closed and
    reports none. Without an archive, printer or start time a run can't be
    told apart, so nothing is matched.
    """
    if archive_id is None or printer_id is None or started_at is None:
        return None
    query = select(PrintLogEntry).where(
        PrintLogEntry.archive_id == archive_id,
        PrintLogEntry.printer_id == printer_id,
        PrintLogEntry.started_at == started_at,
    )
    return (await db.scalars(query.order_by(PrintLogEntry.id.desc()).limit(1))).first()


async def record_archive_wear(db: AsyncSession, archive, entry: PrintLogEntry) -> None:
    """Copy a run's wear cost onto its archive when it is the archive's first run.

    Same rule as energy (#1378): the archive shows its first print, and a
    reprint's wear stays on its own log entry (#694). ``entry`` must already be
    flushed, so it counts as one of the archive's runs.
    """
    if entry.wear_cost is None:
        return
    runs = await db.scalar(select(func.count(PrintLogEntry.id)).where(PrintLogEntry.archive_id == archive.id))
    if (runs or 0) <= 1:
        archive.wear_cost = entry.wear_cost
