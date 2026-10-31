"""Merge duplicate archives of one file into one (archive redesign P2).

An archive is a file; each print of it is a run. Before runs, every dispatch
of a library or project file archived it anew, so a file printed plate by
plate has several archives. They are one when they hold the same file
(content hash), have the same owner and are in the same project.

Merging keeps the oldest archive and moves everything the others hold to it:
their runs (one is written for an archive printed before runs were logged),
queue items, batches, BOM items, pipeline runs, library folders, spool usage
and pending Telegram verdicts, and their photos and videos, into its folder.
The others stay, soft-deleted, each with an alias to the one it went into: the
money booked against them (wallet charges, budget reservations) is never
rewritten, and their ids keep finding the archive (``resolve_alias``).

An archive at work is left out: printing, queued, tracked by Spoolman, being
dispatched or converted in this process (services/archive_work), sliced by a
pipeline, edited, or printed within the last half hour (its timelapse and
finish photo may still be on their way, by its own id). So is a group whose
archives carry different source or design files: only one can stay.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.core.printer_scope import PrinterScope
from backend.app.models.active_print_spoolman import ActivePrintSpoolman
from backend.app.models.archive import PrintArchive
from backend.app.models.archive_alias import ArchiveAlias
from backend.app.models.print_log import INCLUDE_OPEN_RUNS, RUN_OPEN, PrintLogEntry
from backend.app.models.print_queue import PrintQueueItem
from backend.app.services import archive_work
from backend.app.utils.archive_paths import archive_dir, archive_photos_dir, find_archive_photo
from backend.app.utils.local_time import utcnow_naive
from backend.app.utils.safe_path import PathTraversalError, safe_join_under

logger = logging.getLogger(__name__)

# Long enough for the completion's timelapse scan and finish-photo upgrade,
# which look for their print by its archive's id.
_JUST_PRINTED = timedelta(minutes=30)
# What an archive printed before runs were logged can say of its print.
_ENDED = frozenset({"completed", "failed", "cancelled", "aborted", "stopped"})
_PIPELINE_ACTIVE = ("queued", "slicing", "dispatching", "in_progress")
# The file's own extras, of which the archive keeps one each.
_FILE_EXTRAS = ("source_3mf_path", "f3d_path")

SKIPPED_BUSY = "busy"
SKIPPED_CONFLICTING_FILES = "conflicting_files"
SKIPPED_FAILED = "failed"


@dataclass
class MergeGroup:
    survivor: PrintArchive
    merged: list[PrintArchive]
    run_counts: dict[int, int] = field(default_factory=dict)
    skipped: str | None = None


async def resolve_alias(db: AsyncSession, archive_id: int) -> int | None:
    """The archive a merged one went into, or None."""
    return await db.scalar(select(ArchiveAlias.archive_id).where(ArchiveAlias.merged_archive_id == archive_id))


def _key(archive: PrintArchive) -> tuple:
    return (archive.content_hash, archive.created_by_id, archive.project_id)


async def find_groups(db: AsyncSession, visible: list) -> list[MergeGroup]:
    """The duplicate groups among the archives ``visible`` (WHERE clauses)
    admits, oldest first, each with why it is left out (``skipped``)."""
    keys = (
        select(PrintArchive.content_hash, PrintArchive.created_by_id, PrintArchive.project_id)
        .where(PrintArchive.content_hash.is_not(None), PrintArchive.deleted_at.is_(None), *visible)
        .group_by(PrintArchive.content_hash, PrintArchive.created_by_id, PrintArchive.project_id)
        .having(func.count(PrintArchive.id) > 1)
    )
    hashes = {row.content_hash for row in (await db.execute(keys)).all()}
    if not hashes:
        return []
    archives = (
        await db.scalars(
            select(PrintArchive)
            .where(PrintArchive.content_hash.in_(hashes), PrintArchive.deleted_at.is_(None), *visible)
            .order_by(PrintArchive.id)
        )
    ).all()
    by_key: dict[tuple, list[PrintArchive]] = {}
    for archive in archives:
        by_key.setdefault(_key(archive), []).append(archive)
    members = [a for group in by_key.values() if len(group) > 1 for a in group]
    busy = await _busy(db, [a.id for a in members])
    run_counts = dict(
        (
            await db.execute(
                select(PrintLogEntry.archive_id, func.count(PrintLogEntry.id))
                .where(PrintLogEntry.archive_id.in_([a.id for a in members]))
                .group_by(PrintLogEntry.archive_id)
            )
        ).all()
    )
    groups = []
    for group in by_key.values():
        if len(group) < 2:
            continue
        skipped = None
        if any(a.id in busy for a in group):
            skipped = SKIPPED_BUSY
        elif await asyncio.to_thread(_conflicting_extras, group):
            skipped = SKIPPED_CONFLICTING_FILES
        groups.append(
            MergeGroup(
                survivor=group[0],
                merged=group[1:],
                run_counts={a.id: run_counts.get(a.id, 0) for a in group},
                skipped=skipped,
            )
        )
    return sorted(groups, key=lambda g: g.survivor.id)


def _digest(relpath: str) -> str | None:
    path = _under_base(relpath)
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _conflicting_extras(group: list[PrintArchive]) -> bool:
    """Whether two archives of the group carry different source 3MFs or
    design files: the archive keeps one, and the other is the user's."""
    for attr in _FILE_EXTRAS:
        digests = {_digest(getattr(a, attr)) for a in group if getattr(a, attr)}
        if len(digests - {None}) > 1:
            return True
    return False


async def _busy(db: AsyncSession, archive_ids: list[int]) -> set[int]:
    """The archives at work (see the module docstring)."""
    from backend.app.main import _active_prints, _expected_prints
    from backend.app.models.pipeline_run import PipelineRun
    from backend.app.services import timelapse_jobs

    if not archive_ids:
        return set()
    recent = utcnow_naive() - _JUST_PRINTED
    queries = [
        select(PrintArchive.id).where(
            PrintArchive.id.in_(archive_ids),
            or_(PrintArchive.status == "printing", PrintArchive.completed_at >= recent),
        ),
        select(PrintLogEntry.archive_id)
        .where(
            PrintLogEntry.archive_id.in_(archive_ids),
            or_(PrintLogEntry.status == RUN_OPEN, PrintLogEntry.completed_at >= recent),
        )
        .execution_options(**{INCLUDE_OPEN_RUNS: True}),
        select(PrintQueueItem.archive_id).where(
            PrintQueueItem.archive_id.in_(archive_ids), PrintQueueItem.status.in_(("pending", "printing"))
        ),
        select(ActivePrintSpoolman.archive_id).where(ActivePrintSpoolman.archive_id.in_(archive_ids)),
        select(PipelineRun.source_archive_id).where(
            PipelineRun.source_archive_id.in_(archive_ids), PipelineRun.status.in_(_PIPELINE_ACTIVE)
        ),
    ]
    busy: set[int | None] = set()
    for query in queries:
        busy |= set((await db.scalars(query)).all())
    # Held in memory by id: a print waiting for its start or end, work in
    # this process (a dispatch, a conversion), an editor job.
    in_memory = set(_active_prints.values()) | set(_expected_prints.values())
    for archive_id in archive_ids:
        job = timelapse_jobs.get_job(archive_id)
        if (
            archive_id in in_memory
            or archive_work.is_held(archive_id)
            or (job is not None and job.status == timelapse_jobs.RUNNING)
        ):
            busy.add(archive_id)
    return {archive_id for archive_id in busy if archive_id is not None}


# Rows that point at an archive and move with it. Wallet charges and budget
# reservations stay on the merged one (soft-deleted, not removed): they are
# money already booked, and the old charges are unique per archive.
def _moved_references():
    from backend.app.models.library import LibraryFolder
    from backend.app.models.notification import TelegramPendingVerdict
    from backend.app.models.pending_upload import PendingUpload
    from backend.app.models.pipeline_run import PipelineRun
    from backend.app.models.print_batch import PrintBatch
    from backend.app.models.project_bom import ProjectBOMItem
    from backend.app.models.spool_usage_history import SpoolUsageHistory

    return (
        (PrintLogEntry, "archive_id"),
        (PrintQueueItem, "archive_id"),
        (PrintBatch, "archive_id"),
        (ProjectBOMItem, "archive_id"),
        (SpoolUsageHistory, "archive_id"),
        (TelegramPendingVerdict, "archive_id"),
        (LibraryFolder, "archive_id"),
        (PendingUpload, "archived_id"),
        (PipelineRun, "source_archive_id"),
    )


class _Files:
    """Files placed in the survivor's folder, removed again if the merge fails."""

    def __init__(self) -> None:
        self.created: list[Path] = []

    def place(self, source: Path | None, target_dir: Path) -> Path | None:
        """``source`` in ``target_dir`` under a free name (a link where it can)."""
        if source is None or not source.is_file():
            return None
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / source.name
        n = 2
        while target.exists():
            if target.resolve() == source.resolve():
                return target
            target = target_dir / f"{source.stem}_{n}{source.suffix}"
            n += 1
        try:
            os.link(source, target)
        except OSError:
            try:
                shutil.copy2(source, target)
            except BaseException:
                target.unlink(missing_ok=True)  # a partial copy
                raise
        self.created.append(target)
        return target

    def undo(self) -> None:
        for path in self.created:
            path.unlink(missing_ok=True)


def _relpath(path: Path) -> str:
    return str(path.relative_to(settings.base_dir))


def _under_base(relpath: str | None) -> Path | None:
    if not relpath:
        return None
    try:
        return safe_join_under(settings.base_dir, relpath, http=False)
    except PathTraversalError:
        return None


def _missing(relpath: str | None) -> bool:
    path = _under_base(relpath)
    return path is None or not path.is_file()


def _gets_a_run(archive: PrintArchive, runs: list[PrintLogEntry]) -> bool:
    """Whether the merge writes a run for the archive's print (``_run_from_archive``)."""
    return not runs and archive.status in _ENDED and bool(archive.started_at or archive.completed_at)


def _orphan_video(archive: PrintArchive, runs: list[PrintLogEntry]) -> bool:
    """Whether the archive's video is its alone, with no print to take it."""
    from backend.app.services.print_log import owns_media

    if not archive.timelapse_path or _gets_a_run(archive, runs):
        return False
    if any(r.timelapse_path == archive.timelapse_path for r in runs):
        return False
    return not any(r.timelapse_path is None and not owns_media(r) for r in runs)


def _latest(runs, *, where) -> PrintLogEntry | None:
    """The latest by start of ``runs`` that ``where`` admits."""
    return max(
        (r for r in runs if where(r)),
        key=lambda r: (r.started_at is not None, r.started_at or datetime.min, r.id or 0),
        default=None,
    )


def _run_from_archive(archive: PrintArchive, survivor_id: int, printer_name: str | None) -> PrintLogEntry:
    """The run of a print logged before runs were: what its archive says of it."""
    duration = None
    if archive.started_at and archive.completed_at and archive.completed_at > archive.started_at:
        duration = int((archive.completed_at - archive.started_at).total_seconds())
    return PrintLogEntry(
        archive_id=survivor_id,
        print_name=archive.print_name,
        plate_id=archive.plate_id,
        printer_id=archive.printer_id,
        printer_name=printer_name,
        status=archive.status,
        started_at=archive.started_at,
        completed_at=archive.completed_at,
        duration_seconds=duration,
        filament_type=archive.filament_type,
        filament_color=archive.filament_color,
        filament_used_grams=archive.filament_used_grams,
        cost=archive.cost,
        energy_kwh=archive.energy_kwh,
        energy_cost=archive.energy_cost,
        wear_cost=archive.wear_cost,
        failure_reason=archive.failure_reason,
        user_verdict=archive.user_verdict,
        user_verdict_source=archive.user_verdict_source,
        user_verdict_at=archive.user_verdict_at,
        thumbnail_path=archive.thumbnail_path,
        created_by_id=archive.created_by_id,
        quantity=archive.quantity,
        created_at=archive.completed_at or archive.created_at,
    )


def _joined_tags(*tag_lists: str | None) -> str | None:
    seen: list[str] = []
    for tags in tag_lists:
        for tag in (tags or "").split(","):
            if (tag := tag.strip()) and tag.lower() not in {t.lower() for t in seen}:
                seen.append(tag)
    return ", ".join(seen) or None


def _joined_notes(*notes: str | None) -> str | None:
    kept: list[str] = []
    for note in notes:
        if note and note.strip() and note.strip() not in kept:
            kept.append(note.strip())
    return "\n\n".join(kept) or None


@dataclass
class _Plan:
    """The files of one merged archive and where they go."""

    archive: PrintArchive
    photos: dict[str, Path] = field(default_factory=dict)  # name -> source
    videos: dict[str, Path] = field(default_factory=dict)  # relpath -> source
    extras: dict[str, Path] = field(default_factory=dict)  # attribute -> source


async def merge_group(db: AsyncSession, survivor_id: int, merged_ids: list[int]) -> str | None:
    """Merge ``merged_ids`` into ``survivor_id`` and commit. Returns why it
    was left out instead (a ``SKIPPED_*``), or None once merged."""
    with archive_work.merging([survivor_id, *merged_ids]) as marked:
        if not marked:
            return SKIPPED_BUSY
        return await _merge_marked(db, survivor_id, merged_ids)


async def _merge_marked(db: AsyncSession, survivor_id: int, merged_ids: list[int]) -> str | None:
    """``_merge``, rolled back (with the files it placed) on any failure."""
    files = _Files()
    try:
        reason, leftover = await _merge(db, survivor_id, merged_ids, files)
        if reason is not None:
            # Left out before anything was written.
            return reason
    except BaseException:
        await db.rollback()
        await asyncio.to_thread(files.undo)
        raise
    for directory in leftover:
        await asyncio.to_thread(shutil.rmtree, directory, True)
    logger.info("Merged archives %s into archive %s", merged_ids, survivor_id)
    return None


async def _merge(
    db: AsyncSession, survivor_id: int, merged_ids: list[int], files: _Files
) -> tuple[str | None, list[Path]]:
    """Merge and commit; returns why it was left out instead, or the folders
    to remove now that it has committed."""
    from backend.app.models.printer import Printer
    from backend.app.services.archive import ArchiveService
    from backend.app.services.print_log import mirror_latest_run, newest_run, owns_media

    ids = [survivor_id, *merged_ids]
    # As they are now, not as the preview or the group search found them.
    rows = {
        a.id: a
        for a in await db.scalars(
            select(PrintArchive).where(PrintArchive.id.in_(ids)).execution_options(populate_existing=True)
        )
    }
    if len(rows) != len(ids) or any(a.deleted_at is not None for a in rows.values()):
        return SKIPPED_BUSY, []
    survivor, merged = rows[survivor_id], [rows[i] for i in merged_ids]
    if len({_key(a) for a in rows.values()}) != 1 or await _busy(db, ids):
        return SKIPPED_BUSY, []
    if await asyncio.to_thread(_conflicting_extras, [survivor, *merged]):
        return SKIPPED_CONFLICTING_FILES, []
    runs: dict[int, list[PrintLogEntry]] = {a.id: [] for a in rows.values()}
    for run in await db.scalars(
        select(PrintLogEntry)
        .where(PrintLogEntry.archive_id.in_(ids))
        .execution_options(populate_existing=True, **{INCLUDE_OPEN_RUNS: True})
    ):
        if run.archive_id is not None:
            runs[run.archive_id].append(run)

    # A video on an archive alone that no print of it can take (attached by
    # hand to one never printed): the archive keeps it only if it is the one
    # video of the group, or it would be lost or hide another.
    orphans = {a.id: a.timelapse_path for a in rows.values() if _orphan_video(a, runs[a.id])}
    if orphans:
        videos = {r.timelapse_path for own in runs.values() for r in own if r.timelapse_path} | {
            a.timelapse_path for a in rows.values() if a.timelapse_path
        }
        if len(orphans) > 1 or videos - set(orphans.values()):
            return SKIPPED_CONFLICTING_FILES, []

    # Plan, reading only: the files to place in the survivor's folder. The
    # survivor's own files where it lost them, from the first archive that
    # still has them.
    plans = []
    taken: set[str] = set()
    for archive in merged:
        plan = _Plan(archive)
        for name in archive.photos or []:
            if (source := find_archive_photo(archive, name)) is not None:
                plan.photos[name] = source
        for relpath in {r.timelapse_path for r in runs[archive.id] if r.timelapse_path} | (
            {archive.timelapse_path} if archive.timelapse_path else set()
        ):
            if (source := _under_base(relpath)) is not None:
                plan.videos[relpath] = source
        for attr in (*_FILE_EXTRAS, "file_path", "thumbnail_path"):
            source = _under_base(getattr(archive, attr))
            if attr not in taken and _missing(getattr(survivor, attr)) and source is not None and source.is_file():
                plan.extras[attr] = source
                taken.add(attr)
        plans.append(plan)

    # Place them off the event loop, before any write: SQLite would hold its
    # write lock through a copy that cannot link.
    survivor_dir = archive_dir(survivor)
    photos_dir = archive_photos_dir(survivor)

    def place(source: Path, target_dir: Path) -> Path | None:
        return files.place(source, target_dir)

    def place_all() -> list[tuple[dict[str, str], dict[str, str], dict[str, str]]]:
        placed = []
        for plan in plans:
            photos = {n: p.name for n, s in plan.photos.items() if (p := place(s, photos_dir)) is not None}
            videos = {r: _relpath(p) for r, s in plan.videos.items() if (p := place(s, survivor_dir)) is not None}
            extras = {a: _relpath(p) for a, s in plan.extras.items() if (p := place(s, survivor_dir)) is not None}
            placed.append((photos, videos, extras))
        return placed

    placed = await asyncio.to_thread(place_all)

    printer_ids = {a.printer_id for a in rows.values() if a.printer_id is not None}
    printer_names = dict(
        (await db.execute(select(Printer.id, Printer.name).where(Printer.id.in_(printer_ids)))).tuples().all()
    )
    for archive in [survivor, *merged]:
        own = runs[archive.id]
        # A print logged before runs keeps its only record on its archive.
        if _gets_a_run(archive, own):
            name = printer_names.get(archive.printer_id) if archive.printer_id is not None else None
            run = _run_from_archive(archive, archive.id, name)
            run.photos = list(archive.photos or [])
            run.timelapse_path = archive.timelapse_path
            db.add(run)
            own.append(run)
        # Its video, kept on the archive alone by prints logged before
        # runs kept their own: its latest such print's.
        elif (
            archive.timelapse_path
            and archive.id not in orphans
            and not any(r.timelapse_path == archive.timelapse_path for r in own)
        ):
            if (heir := _latest(own, where=lambda r: r.timelapse_path is None and not owns_media(r))) is not None:
                heir.timelapse_path = archive.timelapse_path
        # A one-tap link of the archive's question still answers it: on
        # its latest print, where questions are asked now.
        if archive.confirm_token and archive.confirm_token_used_at is None and archive.user_verdict is None:
            token, archive.confirm_token = archive.confirm_token, None
            await db.flush()
            if (heir := _latest(own, where=lambda r: r.confirm_token is None)) is not None:
                heir.confirm_token = token
                heir.confirm_requested = True

    survivor_photos = list(survivor.photos or [])
    for plan, (photos, videos, extras) in zip(plans, placed, strict=True):
        archive = plan.archive
        for name in archive.photos or []:
            if name in photos and photos[name] not in survivor_photos:
                survivor_photos.append(photos[name])
        for attr, relpath in extras.items():
            setattr(survivor, attr, relpath)
        for run in runs[archive.id]:
            if run.photos:
                run.photos = [photos[p] for p in run.photos if p in photos]
            if run.timelapse_path:
                run.timelapse_path = videos.get(run.timelapse_path)
            # Its thumbnail was its archive's copy of the file's.
            if run.thumbnail_path == archive.thumbnail_path:
                run.thumbnail_path = survivor.thumbnail_path
            run.archive_id = survivor.id

        # The file's description: one note, every tag, a favourite if any.
        survivor.notes = _joined_notes(survivor.notes, archive.notes)
        survivor.tags = _joined_tags(survivor.tags, archive.tags)
        survivor.is_favorite = bool(survivor.is_favorite or archive.is_favorite)
        for attr in ("library_file_id", "external_url", "makerworld_url", "designer"):
            if getattr(survivor, attr) is None and getattr(archive, attr) is not None:
                setattr(survivor, attr, getattr(archive, attr))
    await db.flush()

    for archive in merged:
        for model, column in _moved_references():
            await db.execute(
                update(model)
                .where(getattr(model, column) == archive.id)
                .values({column: survivor.id})
                .execution_options(**{INCLUDE_OPEN_RUNS: True})
            )
        await db.execute(
            update(ArchiveAlias).where(ArchiveAlias.archive_id == archive.id).values(archive_id=survivor.id)
        )
        db.add(ArchiveAlias(merged_archive_id=archive.id, archive_id=survivor.id))
    survivor.photos = survivor_photos or None
    await db.flush()

    # The archive shows its latest run, its video too.
    await mirror_latest_run(db, survivor.id)
    newest = await newest_run(db, survivor.id)
    if orphans:
        # The group's one video, which no print holds.
        [(holder_id, orphan)] = orphans.items()
        if holder_id != survivor.id and orphan is not None:
            orphan = placed[merged_ids.index(holder_id)][1].get(orphan)
        survivor.timelapse_path = orphan
        survivor.timelapse_plate_id = None
    elif newest is not None and owns_media(newest):
        survivor.timelapse_path = newest.timelapse_path
        survivor.timelapse_plate_id = newest.plate_id if newest.timelapse_path else None

    # Their folders go once committed, unless another archive still uses
    # one (archived in the same second under the same name).
    service = ArchiveService(db)
    leftover = []
    for archive in merged:
        for directory in service._resolve_archive_dirs_for_delete(archive):
            if not await _folder_in_use(db, directory, merged_ids=merged_ids):
                leftover.append(directory)
    for archive in merged:
        archive.deleted_at = datetime.now(timezone.utc)
        # Its files are the survivor's now: a later purge of the row must
        # find nothing of theirs to remove.
        archive.file_path = ""
        archive.thumbnail_path = archive.timelapse_path = None
        archive.source_3mf_path = archive.f3d_path = None
        archive.photos = None
    await db.commit()
    return None, leftover


async def _folder_in_use(db: AsyncSession, directory: Path, *, merged_ids: list[int]) -> bool:
    """Whether ``directory`` holds a file of an archive other than the merged
    ones (the survivor's included). Compared as paths, not as text: stored
    paths use the platform's separator."""
    folder = directory.resolve()
    rows = await db.execute(
        select(PrintArchive.file_path, PrintArchive.source_3mf_path, PrintArchive.f3d_path).where(
            PrintArchive.id.not_in(merged_ids),
            PrintArchive.deleted_at.is_(None),
            or_(
                PrintArchive.file_path.contains(directory.name, autoescape=True),
                PrintArchive.source_3mf_path.contains(directory.name, autoescape=True),
                PrintArchive.f3d_path.contains(directory.name, autoescape=True),
            ),
        )
    )
    for row in rows.all():
        for relpath in row:
            if (path := _under_base(relpath)) is not None and path.resolve().is_relative_to(folder):
                return True
    return False


async def merge(db: AsyncSession, survivor_ids: list[int], visible: list) -> tuple[list[dict], list[dict]]:
    """Merge the groups of ``survivor_ids`` as they stand now (each its
    group's oldest archive among those ``visible`` admits). Returns what was
    merged and what was left out, and why; one group failing leaves the
    others as they went."""
    # Read up front: a group that fails rolls back, which expires the rows.
    groups = {g.survivor.id: (g.skipped, [a.id for a in g.merged]) for g in await find_groups(db, visible)}
    done, skipped = [], []
    for survivor_id in survivor_ids:
        if survivor_id not in groups:
            skipped.append({"survivor_id": survivor_id, "reason": "not_a_group"})
            continue
        reason, merged_ids = groups[survivor_id]
        if reason is not None:
            skipped.append({"survivor_id": survivor_id, "reason": reason})
            continue
        try:
            reason = await merge_group(db, survivor_id, merged_ids)
        except Exception:
            logger.exception("Merging archives %s into archive %s failed", merged_ids, survivor_id)
            reason = SKIPPED_FAILED
        if reason is None:
            done.append({"survivor_id": survivor_id, "merged_ids": merged_ids})
        else:
            skipped.append({"survivor_id": survivor_id, "reason": reason})
    return done, skipped


def merge_scope_clauses(printer_scope: PrinterScope) -> list:
    """Only archives every print of which the caller's printers made: a
    merge changes and removes whole archives (archive redesign, step 8)."""
    from backend.app.core.row_visibility import whole_archive_scope_clause

    clause = whole_archive_scope_clause(printer_scope)
    return [clause] if clause is not None else []
