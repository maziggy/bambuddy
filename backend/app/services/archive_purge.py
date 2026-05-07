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
        """Return ``{enabled, days}``. Missing keys default to disabled / 365d."""
        enabled_raw = await self._read_setting(db, AUTO_PURGE_ENABLED_KEY)
        days_raw = await self._read_setting(db, AUTO_PURGE_DAYS_KEY)

        enabled = (enabled_raw or "false").lower() == "true"
        try:
            days = int(days_raw) if days_raw is not None else DEFAULT_AUTO_PURGE_DAYS
        except (TypeError, ValueError):
            days = DEFAULT_AUTO_PURGE_DAYS
        days = max(MIN_AUTO_PURGE_DAYS, min(MAX_AUTO_PURGE_DAYS, days))
        return {"enabled": enabled, "days": days}

    async def set_settings(self, db: AsyncSession, *, enabled: bool, days: int) -> dict:
        clamped_days = max(MIN_AUTO_PURGE_DAYS, min(MAX_AUTO_PURGE_DAYS, int(days)))
        await self._write_setting(db, AUTO_PURGE_ENABLED_KEY, "true" if enabled else "false")
        await self._write_setting(db, AUTO_PURGE_DAYS_KEY, str(clamped_days))
        await db.commit()
        return {"enabled": enabled, "days": clamped_days}

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

        deleted = await self.purge_older_than(db, older_than_days=cfg["days"])
        await self._stamp_last_run(db, now)
        if deleted:
            logger.info(
                "Archive auto-purge: hard-deleted %d archive(s) (threshold=%d days)",
                deleted,
                cfg["days"],
            )
        return deleted

    # ---- Preview / purge ---------------------------------------------

    async def preview_purge(
        self,
        db: AsyncSession,
        older_than_days: int,
        sample_limit: int = 5,
    ) -> dict:
        """Count + size of archives eligible for purge. Read-only."""
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

        count_result = await db.execute(select(func.count(PrintArchive.id)).where(clause))
        count = int(count_result.scalar() or 0)

        size_result = await db.execute(select(func.coalesce(func.sum(PrintArchive.file_size), 0)).where(clause))
        total_bytes = int(size_result.scalar() or 0)

        sample_result = await db.execute(
            select(PrintArchive.filename).where(clause).order_by(last_activity).limit(sample_limit)
        )
        samples = [row[0] for row in sample_result.all()]

        return {
            "count": count,
            "total_bytes": total_bytes,
            "sample_filenames": samples,
            "older_than_days": older_than_days,
        }

    async def purge_older_than(self, db: AsyncSession, older_than_days: int) -> int:
        """Hard-delete archives older than ``older_than_days``. Returns count.

        Delegates to :meth:`ArchiveService.delete_archive` for every row so the
        on-disk cleanup (3MF, thumbnail, timelapse, photos) goes through the
        same safety-checked path as manual deletion. Each delete runs in its
        own session so a commit-per-row doesn't churn the caller's session
        (and matches how the sweeper uses :func:`_database.async_session` in production).
        """
        if older_than_days < 1:
            return 0
        now = datetime.now(timezone.utc)
        cutoff = _age_cutoff(now, older_than_days)

        id_result = await db.execute(select(PrintArchive.id).where(_last_activity_expr() < cutoff))
        ids = [row[0] for row in id_result.all()]
        if not ids:
            return 0

        deleted = 0
        for archive_id in ids:
            async with _database.async_session() as delete_db:
                service = ArchiveService(delete_db)
                if await service.delete_archive(archive_id):
                    deleted += 1
        if deleted:
            logger.info(
                "Archive purge: hard-deleted %d archive(s) (older_than_days=%d)",
                deleted,
                older_than_days,
            )
        return deleted


archive_purge_service = ArchivePurgeService()
