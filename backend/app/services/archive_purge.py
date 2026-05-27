"""Archive auto-purge service (#1008 follow-up).

Age-based hard-delete of print archives. Unlike the library trash flow there is
no soft-delete intermediate — archives are historical print records, so the
"undo" window the library bin provides doesn't apply here. A user who wants to
keep an archive should download or favourite it before the purge window elapses.

The sweeper runs on the same 15-minute cadence as the library trash sweeper but
throttles actual purge runs to once per 24h. Admins can also trigger a manual
purge from the Settings UI.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core import database as _database
from backend.app.models.archive import PrintArchive
from backend.app.models.settings import Settings
from backend.app.services.archive import ArchiveService

logger = logging.getLogger(__name__)

AUTO_PURGE_ENABLED_KEY = "archive_auto_purge_enabled"
AUTO_PURGE_DAYS_KEY = "archive_auto_purge_days"
AUTO_PURGE_LAST_RUN_KEY = "archive_auto_purge_last_run"
# #1390 follow-up: bulk and scheduled purge inherit the same "soft vs hard"
# choice the single-archive delete already exposes (#1343). When False
# (default), each purged archive goes through soft_delete_archive — files
# removed from disk, row hidden via `deleted_at`, PrintLogEntry rows
# untouched so Quick Stats keeps every contribution. When True, the linked
# log rows are deleted up front and the archive row is hard-removed,
# matching the route's `?purge_stats=true` semantics.
AUTO_PURGE_STATS_KEY = "archive_auto_purge_stats"

DEFAULT_AUTO_PURGE_DAYS = 365
# 7-day floor mirrors the library auto-purge; anything shorter treats archives
# as ephemeral which is rarely what anyone wants.
MIN_AUTO_PURGE_DAYS = 7
MAX_AUTO_PURGE_DAYS = 3650


def _age_cutoff(now: datetime, older_than_days: int) -> datetime:
    return now - timedelta(days=older_than_days)


def _last_activity_expr():
    """Most-recent timestamp on an archive row.

    Reprints reuse the archive row and update ``completed_at``/``started_at`` but
    leave ``created_at`` pinned to the first print, so purging on ``created_at``
    would evict recently-reprinted archives. Use the latest of the three instead.
    """
    return func.coalesce(
        PrintArchive.completed_at,
        PrintArchive.started_at,
        PrintArchive.created_at,
    )


class ArchivePurgeService:
    """Manages archive auto-purge sweeper + admin-triggered manual purges."""

    def __init__(self):
        self._scheduler_task: asyncio.Task | None = None
        # Match library trash cadence — the 24h throttle keeps actual work rare.
        self._check_interval = 900

    async def start_scheduler(self):
        if self._scheduler_task is not None:
            return
        logger.info("Starting archive auto-purge sweeper")
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    def stop_scheduler(self):
        if self._scheduler_task:
            self._scheduler_task.cancel()
            self._scheduler_task = None
            logger.info("Stopped archive auto-purge sweeper")

    async def _scheduler_loop(self):
        while True:
            try:
                await asyncio.sleep(self._check_interval)
                async with _database.async_session() as db:
                    await self._maybe_run_auto_purge(db)
            except asyncio.CancelledError:
                break
            except Exception as e:  # pragma: no cover - defensive
                logger.error("Error in archive auto-purge sweeper: %s", e)
                await asyncio.sleep(60)

    # ---- Settings -----------------------------------------------------

    @staticmethod
    async def _read_setting(db: AsyncSession, key: str) -> str | None:
        result = await db.execute(select(Settings.value).where(Settings.key == key))
        return result.scalar_one_or_none()

    @staticmethod
    async def _write_setting(db: AsyncSession, key: str, value: str) -> None:
        result = await db.execute(select(Settings).where(Settings.key == key))
        row = result.scalar_one_or_none()
        if row is None:
            db.add(Settings(key=key, value=value))
        else:
            row.value = value

    async def get_settings(self, db: AsyncSession) -> dict:
        """Return ``{enabled, days, purge_stats}``. Missing keys default to
        disabled / 365d / soft-delete (Quick Stats preserved)."""
        enabled_raw = await self._read_setting(db, AUTO_PURGE_ENABLED_KEY)
        days_raw = await self._read_setting(db, AUTO_PURGE_DAYS_KEY)
        stats_raw = await self._read_setting(db, AUTO_PURGE_STATS_KEY)

        enabled = (enabled_raw or "false").lower() == "true"
        try:
            days = int(days_raw) if days_raw is not None else DEFAULT_AUTO_PURGE_DAYS
        except (TypeError, ValueError):
            days = DEFAULT_AUTO_PURGE_DAYS
        days = max(MIN_AUTO_PURGE_DAYS, min(MAX_AUTO_PURGE_DAYS, days))
        purge_stats = (stats_raw or "false").lower() == "true"
        return {"enabled": enabled, "days": days, "purge_stats": purge_stats}

    async def set_settings(self, db: AsyncSession, *, enabled: bool, days: int, purge_stats: bool = False) -> dict:
        clamped_days = max(MIN_AUTO_PURGE_DAYS, min(MAX_AUTO_PURGE_DAYS, int(days)))
        await self._write_setting(db, AUTO_PURGE_ENABLED_KEY, "true" if enabled else "false")
        await self._write_setting(db, AUTO_PURGE_DAYS_KEY, str(clamped_days))
        await self._write_setting(db, AUTO_PURGE_STATS_KEY, "true" if purge_stats else "false")
        await db.commit()
        return {"enabled": enabled, "days": clamped_days, "purge_stats": purge_stats}

    async def _get_last_run(self, db: AsyncSession) -> datetime | None:
        raw = await self._read_setting(db, AUTO_PURGE_LAST_RUN_KEY)
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None

    async def _stamp_last_run(self, db: AsyncSession, when: datetime) -> None:
        await self._write_setting(db, AUTO_PURGE_LAST_RUN_KEY, when.isoformat())
        await db.commit()

    async def _maybe_run_auto_purge(self, db: AsyncSession) -> int:
        """Run the auto-purge if enabled and >=24h has elapsed since last run."""
        cfg = await self.get_settings(db)
        if not cfg["enabled"]:
            return 0

        now = datetime.now(timezone.utc)
        last = await self._get_last_run(db)
        if last is not None and (now - last) < timedelta(hours=24):
            return 0

        deleted = await self.purge_older_than(
            db,
            older_than_days=cfg["days"],
            purge_stats=cfg["purge_stats"],
        )
        await self._stamp_last_run(db, now)
        if deleted:
            logger.info(
                "Archive auto-purge: %s %d archive(s) (threshold=%d days, purge_stats=%s)",
                "hard-deleted" if cfg["purge_stats"] else "soft-deleted",
                deleted,
                cfg["days"],
                cfg["purge_stats"],
            )
        return deleted

    # ---- Preview / purge ---------------------------------------------

    async def preview_purge(
        self,
        db: AsyncSession,
        older_than_days: int,
        sample_limit: int = 5,
        *,
        purge_stats: bool = False,
    ) -> dict:
        """Count + size of archives eligible for purge. Read-only.

        Soft-delete mode (default) excludes already-soft-deleted rows so the
        admin slider's "eligible" count matches what a fresh purge would
        actually touch. Hard-delete mode counts every row past the cutoff —
        already-soft-deleted rows are eligible for promotion to hard-delete.
        """
        if older_than_days < 1:
            return {
                "count": 0,
                "total_bytes": 0,
                "sample_filenames": [],
                "older_than_days": older_than_days,
            }
        now = datetime.now(timezone.utc)
        cutoff = _age_cutoff(now, older_than_days)
        last_activity = _last_activity_expr()
        clause = last_activity < cutoff

        count_stmt = select(func.count(PrintArchive.id)).where(clause)
        size_stmt = select(func.coalesce(func.sum(PrintArchive.file_size), 0)).where(clause)
        sample_stmt = select(PrintArchive.filename).where(clause).order_by(last_activity).limit(sample_limit)
        if not purge_stats:
            count_stmt = count_stmt.where(PrintArchive.deleted_at.is_(None))
            size_stmt = size_stmt.where(PrintArchive.deleted_at.is_(None))
            sample_stmt = sample_stmt.where(PrintArchive.deleted_at.is_(None))

        count_result = await db.execute(count_stmt)
        count = int(count_result.scalar() or 0)

        size_result = await db.execute(size_stmt)
        total_bytes = int(size_result.scalar() or 0)

        sample_result = await db.execute(sample_stmt)
        samples = [row[0] for row in sample_result.all()]

        return {
            "count": count,
            "total_bytes": total_bytes,
            "sample_filenames": samples,
            "older_than_days": older_than_days,
        }

    async def purge_older_than(
        self,
        db: AsyncSession,
        older_than_days: int,
        *,
        purge_stats: bool = False,
    ) -> int:
        """Bulk-delete archives older than ``older_than_days``. Returns count.

        Two modes, parameter-controlled (#1390):

        * ``purge_stats=False`` (default): each archive goes through
          :meth:`ArchiveService.soft_delete_archive` — files removed from disk
          and the row hidden via ``deleted_at``, but the linked
          ``PrintLogEntry`` rows are untouched so Quick Stats keeps every
          contribution (filament, cost, energy, time accuracy).
        * ``purge_stats=True``: linked log rows are hard-deleted up front and
          the archive row is hard-removed via
          :meth:`ArchiveService.delete_archive`. Matches the single-archive
          ``DELETE /archives/{id}?purge_stats=true`` semantics from #1343.

        Each delete runs in its own session so a commit-per-row doesn't churn
        the caller's session (matches how the sweeper uses
        :func:`_database.async_session` in production).
        """
        if older_than_days < 1:
            return 0
        now = datetime.now(timezone.utc)
        cutoff = _age_cutoff(now, older_than_days)

        # Soft-delete mode must also skip rows already soft-deleted, otherwise
        # a repeat sweeper run keeps re-touching the same rows. Hard-delete
        # mode doesn't filter — already-soft-deleted rows are eligible for
        # promotion to hard-delete when the user opts in.
        select_stmt = select(PrintArchive.id).where(_last_activity_expr() < cutoff)
        if not purge_stats:
            select_stmt = select_stmt.where(PrintArchive.deleted_at.is_(None))
        id_result = await db.execute(select_stmt)
        ids = [row[0] for row in id_result.all()]
        if not ids:
            return 0

        deleted = 0
        for archive_id in ids:
            async with _database.async_session() as delete_db:
                service = ArchiveService(delete_db)
                if purge_stats:
                    # Hard-delete linked PrintLogEntry rows first so their
                    # filament / cost contributions stop counting in /stats.
                    # FK is ON DELETE SET NULL, so without this they'd
                    # survive the archive row and keep showing up in totals
                    # (#1343 / #1378 / #1390).
                    from sqlalchemy import delete as sa_delete

                    from backend.app.models.print_log import PrintLogEntry

                    await delete_db.execute(sa_delete(PrintLogEntry).where(PrintLogEntry.archive_id == archive_id))
                    await delete_db.commit()
                    if await service.delete_archive(archive_id):
                        deleted += 1
                else:
                    if await service.soft_delete_archive(archive_id):
                        deleted += 1
        if deleted:
            logger.info(
                "Archive purge: %s %d archive(s) (older_than_days=%d, purge_stats=%s)",
                "hard-deleted" if purge_stats else "soft-deleted",
                deleted,
                older_than_days,
                purge_stats,
            )
        return deleted


archive_purge_service = ArchivePurgeService()
