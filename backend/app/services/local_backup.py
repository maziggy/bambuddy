"""Scheduled local backup service.

Creates ZIP snapshots of the full Bambuddy data (database + data directories)
on a configurable schedule with retention management.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from backend.app.core.config import settings as app_settings
from backend.app.core.database import async_session
from backend.app.models.settings import Settings

logger = logging.getLogger(__name__)

SCHEDULE_INTERVALS = {
    "hourly": 3600,
    "daily": 86400,
    "weekly": 604800,
}


def _default_backup_dir() -> Path:
    return app_settings.base_dir / "backups"


class LocalBackupService:
    """Manages scheduled local backup snapshots with retention."""

    def __init__(self):
        self._scheduler_task: asyncio.Task | None = None
        self._check_interval = 60
        self._running: bool = False
        self._last_backup_at: str | None = None
        self._last_status: str | None = None
        self._last_message: str | None = None
        self._next_run: datetime | None = None

    async def start_scheduler(self):
        """Start the background scheduler loop."""
        if self._scheduler_task is not None:
            return
        logger.info("Starting local backup scheduler")
        # Seed next_run from settings so the first check has a target
        await self._seed_next_run()
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    def stop_scheduler(self):
        """Stop the scheduler."""
        if self._scheduler_task:
            self._scheduler_task.cancel()
            self._scheduler_task = None
            logger.info("Stopped local backup scheduler")

    async def _scheduler_loop(self):
        """Main scheduler loop — checks for due backups every minute."""
        while True:
            try:
                await asyncio.sleep(self._check_interval)
                await self._check_scheduled_backup()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in local backup scheduler: %s", e)
                await asyncio.sleep(60)

    async def _seed_next_run(self):
        """Load settings and calculate initial next_run."""
        try:
            settings = await self._load_settings()
            if settings.get("enabled"):
                self._next_run = self._calculate_next_run(
                    settings.get("schedule", "daily"),
                    settings.get("time", "03:00"),
                )
        except Exception as e:
            logger.debug("Could not seed local backup next_run: %s", e)

    async def _load_settings(self) -> dict:
        """Read local backup settings from the DB."""
        async with async_session() as db:
            keys = [
                "local_backup_enabled",
                "local_backup_schedule",
                "local_backup_time",
                "local_backup_retention",
                "local_backup_path",
            ]
            result = await db.execute(select(Settings).where(Settings.key.in_(keys)))
            rows = {r.key: r.value for r in result.scalars().all()}
        return {
            "enabled": rows.get("local_backup_enabled", "false").lower() == "true",
            "schedule": rows.get("local_backup_schedule", "daily"),
            "time": rows.get("local_backup_time", "03:00"),
            "retention": int(rows.get("local_backup_retention", "5")),
            "path": rows.get("local_backup_path", ""),
        }

    async def _check_scheduled_backup(self):
        """Check if a scheduled backup is due and run it."""
        settings = await self._load_settings()
        if not settings["enabled"]:
            self._next_run = None
            return

        now = datetime.now(timezone.utc)

        # If no next_run set, schedule one
        if self._next_run is None:
            self._next_run = self._calculate_next_run(settings["schedule"], settings["time"])
            return

        if self._next_run <= now:
            logger.info("Running scheduled local backup")
            await self.run_backup(settings)
            self._next_run = self._calculate_next_run(settings["schedule"], settings["time"])

    def _calculate_next_run(self, schedule_type: str, time_str: str = "03:00") -> datetime:
        """Calculate the next scheduled run time.

        For hourly: next full hour.
        For daily/weekly: next occurrence of the configured time (HH:MM).
        """
        now = datetime.now(timezone.utc)

        if schedule_type == "hourly":
            # Next full hour
            next_run = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            return next_run

        # Parse HH:MM time
        try:
            parts = time_str.strip().split(":")
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            hour, minute = 3, 0

        # Next occurrence of this time today or tomorrow
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)

        if schedule_type == "weekly":
            next_run += timedelta(weeks=1)

        return next_run

    def _resolve_backup_dir(self, path_setting: str) -> Path:
        """Resolve the backup output directory from settings."""
        if path_setting.strip():
            return Path(path_setting.strip())
        return _default_backup_dir()

    async def run_backup(self, settings: dict | None = None) -> dict:
        """Run a backup now. Returns {success, message, filename}."""
        if self._running:
            return {"success": False, "message": "Backup already in progress"}

        self._running = True
        try:
            if settings is None:
                settings = await self._load_settings()

            backup_dir = self._resolve_backup_dir(settings["path"])
            backup_dir.mkdir(parents=True, exist_ok=True)

            from backend.app.api.routes.settings import create_backup_zip

            zip_path, filename = await create_backup_zip(output_path=backup_dir)

            # Prune old backups
            retention = max(1, settings["retention"])
            self._prune_backups(backup_dir, retention)

            self._last_backup_at = datetime.now(timezone.utc).isoformat()
            self._last_status = "success"
            self._last_message = filename
            logger.info("Local backup created: %s", zip_path)
            return {"success": True, "message": "Backup created", "filename": filename}

        except Exception as e:
            self._last_backup_at = datetime.now(timezone.utc).isoformat()
            self._last_status = "failed"
            self._last_message = str(e)
            logger.error("Local backup failed: %s", e, exc_info=True)
            return {"success": False, "message": f"Backup failed: {e}"}
        finally:
            self._running = False

    def _prune_backups(self, backup_dir: Path, retention: int):
        """Delete oldest backups exceeding the retention count."""
        backups = sorted(
            backup_dir.glob("bambuddy-backup-*.zip"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old_backup in backups[retention:]:
            try:
                old_backup.unlink()
                logger.info("Pruned old backup: %s", old_backup.name)
            except OSError as e:
                logger.warning("Could not delete old backup %s: %s", old_backup.name, e)

    def get_status(self) -> dict:
        """Return current scheduler status."""
        return {
            "is_running": self._running,
            "last_backup_at": self._last_backup_at,
            "last_status": self._last_status,
            "last_message": self._last_message,
            "next_run": self._next_run.isoformat() if self._next_run else None,
        }

    def resolve_backup_file(self, path_setting: str, filename: str) -> Path | None:
        """Resolve a backup filename to a full path, with safety checks."""
        if "/" in filename or "\\" in filename or ".." in filename:
            return None
        if not filename.startswith("bambuddy-backup-") or not filename.endswith(".zip"):
            return None
        backup_dir = self._resolve_backup_dir(path_setting)
        target = backup_dir / filename
        if not target.exists():
            return None
        return target

    def list_backups(self, path_setting: str) -> list[dict]:
        """List backup ZIP files in the backup directory."""
        backup_dir = self._resolve_backup_dir(path_setting)
        if not backup_dir.exists():
            return []

        backups = []
        for f in sorted(backup_dir.glob("bambuddy-backup-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
            stat = f.stat()
            backups.append(
                {
                    "filename": f.name,
                    "size": stat.st_size,
                    "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                }
            )
        return backups

    def delete_backup(self, path_setting: str, filename: str) -> dict:
        """Delete a specific backup file. Returns {success, message}."""
        # Path traversal protection
        if "/" in filename or "\\" in filename or ".." in filename:
            return {"success": False, "message": "Invalid filename"}

        backup_dir = self._resolve_backup_dir(path_setting)
        target = backup_dir / filename

        if not target.exists():
            return {"success": False, "message": "Backup not found"}
        if not target.name.startswith("bambuddy-backup-") or not target.name.endswith(".zip"):
            return {"success": False, "message": "Invalid backup file"}

        try:
            target.unlink()
            return {"success": True, "message": "Backup deleted"}
        except OSError as e:
            return {"success": False, "message": f"Could not delete: {e}"}


local_backup_service = LocalBackupService()
