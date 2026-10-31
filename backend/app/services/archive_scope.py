"""What of an archive a printer-scoped caller sees and may change (#1727, archive redesign).

An archive is a file; each print of it is a run, and plates of one archive
can print on several printers. A caller limited to some printers sees an
archive when it, or any of its runs, is on one of theirs, and then only
their prints of it: the outcome, video and finish photos of a run on a
printer they can't see stay hidden. What changes the whole archive needs
every printer it printed on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.printer_scope import PrinterScope
from backend.app.models.print_log import INCLUDE_OPEN_RUNS, PrintLogEntry

# Finish photos are named so (main._background_finish_photo, camera); a
# photo uploaded by hand is not, nor one carried over from the library.
_FINISH_PHOTO_PREFIX = "finish_"


@dataclass(frozen=True)
class ScopedView:
    """What a scoped caller sees of one archive beyond what it holds.

    ``run`` is their latest print of it, shown instead of the archive's
    latest when that one is on a printer outside their scope. The photos
    and the video of prints outside it are left out.
    """

    run: PrintLogEntry | None = None
    # The print whose figures (energy, wear) it shows: ``run``, or the
    # archive's latest when that is in the scope.
    shown: PrintLogEntry | None = None
    hidden_photos: frozenset[str] = field(default_factory=frozenset)
    # The archive's video, when it is of a print outside the scope.
    hidden_timelapse: str | None = None


async def runs_by_archive(db: AsyncSession, archive_ids: list[int]) -> dict[int, list[PrintLogEntry]]:
    """Every run of each archive, a printing one included."""
    if not archive_ids:
        return {}
    rows = await db.scalars(
        select(PrintLogEntry)
        .where(PrintLogEntry.archive_id.in_(archive_ids))
        .execution_options(**{INCLUDE_OPEN_RUNS: True})
    )
    runs: dict[int, list[PrintLogEntry]] = {}
    for run in rows.all():
        if run.archive_id is not None:
            runs.setdefault(run.archive_id, []).append(run)
    return runs


def _on_printer_in(scope: PrinterScope, run: PrintLogEntry) -> bool:
    """A print on a printer of the scope; one whose printer was deleted is no one's."""
    return run.printer_id is not None and scope.allows(run.printer_id)


def view_of(archive, runs: list[PrintLogEntry], scope: PrinterScope) -> ScopedView | None:
    """What ``scope`` sees of ``archive`` given its ``runs``; None when it sees all of it."""
    if scope.is_unrestricted:
        return None
    inside = [r for r in runs if scope.allows(r.printer_id)]
    outside = [r for r in runs if not scope.allows(r.printer_id)]
    archive_inside = scope.allows(archive.printer_id)
    if archive_inside and not outside:
        return None

    shown = {p for r in inside for p in (r.photos or [])}
    hidden = {p for r in outside for p in (r.photos or []) if p not in shown}
    if not archive_inside:
        # A finish photo on no run's list is of a print logged before runs
        # kept their own: the archive's latest, here on a printer outside.
        on_runs = {p for r in runs for p in (r.photos or [])}
        hidden |= {p for p in archive.photos or [] if p not in on_runs and p.startswith(_FINISH_PHOTO_PREFIX)}

    hidden_timelapse = None
    if archive.timelapse_path:
        holders = [r for r in runs if r.timelapse_path == archive.timelapse_path]
        if not any(scope.allows(r.printer_id) for r in holders) if holders else not archive_inside:
            hidden_timelapse = archive.timelapse_path

    def latest(of: list[PrintLogEntry]) -> PrintLogEntry:
        return max(of, key=lambda r: (r.started_at is not None, r.started_at or r.created_at or datetime.min, r.id))

    run = shown = None
    if not archive_inside:
        theirs = [r for r in runs if _on_printer_in(scope, r)]
        if theirs:
            run = shown = latest(theirs)
    elif runs and scope.allows((newest := latest(runs)).printer_id):
        shown = newest
    return ScopedView(run=run, shown=shown, hidden_photos=frozenset(hidden), hidden_timelapse=hidden_timelapse)


async def scoped_views(db: AsyncSession, archives, scope: PrinterScope) -> dict[int, ScopedView]:
    """``view_of`` for each archive that ``scope`` doesn't see whole."""
    if scope.is_unrestricted:
        return {}
    archives = list(archives)
    runs = await runs_by_archive(db, [a.id for a in archives])
    views = {}
    for archive in archives:
        view = view_of(archive, runs.get(archive.id, []), scope)
        if view is not None:
            views[archive.id] = view
    return views


async def scoped_view(db: AsyncSession, archive, scope: PrinterScope) -> ScopedView | None:
    return (await scoped_views(db, [archive], scope)).get(archive.id)


async def printed_in_scope(db: AsyncSession, archive, scope: PrinterScope) -> bool:
    """Whether ``scope`` sees ``archive``: on its own printer, or a run on one of theirs."""
    if scope.allows(archive.printer_id):
        return True
    runs = await runs_by_archive(db, [archive.id])
    return any(_on_printer_in(scope, r) for r in runs.get(archive.id, []))


async def ensure_whole_archive_in_scope(db: AsyncSession, archive, scope: PrinterScope) -> None:
    """Refuse a change to the whole archive (its file, its video, the archive
    itself) unless ``scope`` sees every printer it printed on: it would change
    or remove those prints too. The caller sees the archive, so this says why
    (403) rather than hiding it."""
    if scope.is_unrestricted:
        return
    runs = (await runs_by_archive(db, [archive.id])).get(archive.id, [])
    if scope.allows(archive.printer_id) and all(scope.allows(r.printer_id) for r in runs):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="This archive was also printed on printers outside your access, so only someone "
        "who can see all of them can change or remove it",
    )
