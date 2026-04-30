"""GitHub backup service for printer profiles.

Handles scheduled and on-demand backups of K-profiles and cloud profiles to GitHub.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import async_session
from backend.app.models.archive import PrintArchive
from backend.app.models.github_backup import GitHubBackupConfig, GitHubBackupLog
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.models.spool_usage_history import SpoolUsageHistory
from backend.app.services.git_providers.factory import get_provider_backend
from backend.app.services.printer_manager import printer_manager

logger = logging.getLogger(__name__)

# Schedule intervals in seconds
SCHEDULE_INTERVALS = {
    "hourly": 3600,
    "daily": 86400,
    "weekly": 604800,
}


class GitHubBackupService:
    """Service for backing up profiles to GitHub."""

    def __init__(self):
        self._scheduler_task: asyncio.Task | None = None
        self._check_interval = 60  # Check every minute for scheduled runs
        self._running_backup: bool = False
        self._backup_progress: str | None = None
        self._http_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=60.0)
        return self._http_client

    async def start_scheduler(self):
        """Start the background scheduler loop."""
        if self._scheduler_task is not None:
            return
        logger.info("Starting GitHub backup scheduler")
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    def stop_scheduler(self):
        """Stop the scheduler."""
        if self._scheduler_task:
            self._scheduler_task.cancel()
            self._scheduler_task = None
            logger.info("Stopped GitHub backup scheduler")

    async def _scheduler_loop(self):
        """Main scheduler loop - checks for due backups."""
        while True:
            try:
                await asyncio.sleep(self._check_interval)
                await self._check_scheduled_backups()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in GitHub backup scheduler: %s", e)
                await asyncio.sleep(60)

    async def _check_scheduled_backups(self):
        """Check if any scheduled backups are due."""
        async with async_session() as db:
            result = await db.execute(
                select(GitHubBackupConfig).where(
                    GitHubBackupConfig.enabled == True,  # noqa: E712
                    GitHubBackupConfig.schedule_enabled == True,  # noqa: E712
                )
            )
            configs = result.scalars().all()

            now = datetime.now(timezone.utc)
            for config in configs:
                # Handle both naive (from DB) and aware datetimes
                next_run = config.next_scheduled_run
                if next_run and next_run.tzinfo is None:
                    next_run = next_run.replace(tzinfo=timezone.utc)
                if next_run and next_run <= now:
                    logger.info("Running scheduled backup for config %s", config.id)
                    await self.run_backup(config.id, trigger="scheduled")

    def calculate_next_run(self, schedule_type: str, from_time: datetime | None = None) -> datetime:
        """Calculate the next scheduled run time."""
        now = from_time or datetime.now(timezone.utc)
        interval = SCHEDULE_INTERVALS.get(schedule_type, SCHEDULE_INTERVALS["daily"])
        return now + timedelta(seconds=interval)

    async def test_connection(self, repo_url: str, token: str, provider: str = "github") -> dict:
        """Test connection and permissions for the given provider."""
        backend = get_provider_backend(provider)
        client = await self._get_client()
        return await backend.test_connection(repo_url, token, client)

    async def run_backup(self, config_id: int, trigger: str = "manual") -> dict:
        """Run a backup operation.

        Args:
            config_id: ID of the backup configuration
            trigger: "manual" or "scheduled"

        Returns:
            dict with success, message, log_id, commit_sha, files_changed
        """
        if self._running_backup:
            return {"success": False, "message": "A backup is already running", "log_id": None}

        self._running_backup = True
        log_id = None

        try:
            async with async_session() as db:
                # Get config
                result = await db.execute(select(GitHubBackupConfig).where(GitHubBackupConfig.id == config_id))
                config = result.scalar_one_or_none()

                if not config:
                    return {"success": False, "message": "Configuration not found", "log_id": None}

                if not config.enabled:
                    return {"success": False, "message": "Backup is disabled", "log_id": None}

                # Create log entry
                log = GitHubBackupLog(config_id=config_id, status="running", trigger=trigger)
                db.add(log)
                await db.commit()
                await db.refresh(log)
                log_id = log.id

                try:
                    # Collect backup data
                    self._backup_progress = "Collecting profiles..."
                    backup_data = await self._collect_backup_data(db, config)

                    if not backup_data:
                        # No data to backup
                        log.status = "skipped"
                        log.completed_at = datetime.now(timezone.utc)
                        log.error_message = "No data to backup"
                        config.last_backup_at = datetime.now(timezone.utc)
                        config.last_backup_status = "skipped"
                        config.last_backup_message = "No data to backup"
                        if config.schedule_enabled:
                            config.next_scheduled_run = self.calculate_next_run(config.schedule_type)
                        await db.commit()
                        return {
                            "success": True,
                            "message": "No data to backup",
                            "log_id": log_id,
                            "commit_sha": None,
                            "files_changed": 0,
                        }

                    # Push to GitHub
                    self._backup_progress = "Pushing to GitHub..."
                    push_result = await self._push_to_github(config, backup_data)

                    # Update log and config
                    log.status = push_result["status"]
                    log.completed_at = datetime.now(timezone.utc)
                    log.commit_sha = push_result.get("commit_sha")
                    log.files_changed = push_result.get("files_changed", 0)
                    log.error_message = push_result.get("error")

                    config.last_backup_at = datetime.now(timezone.utc)
                    config.last_backup_status = push_result["status"]
                    config.last_backup_message = push_result.get("message", "")
                    config.last_backup_commit_sha = push_result.get("commit_sha")

                    if config.schedule_enabled:
                        config.next_scheduled_run = self.calculate_next_run(config.schedule_type)

                    await db.commit()

                    return {
                        "success": push_result["status"] in ("success", "skipped"),
                        "message": push_result.get("message", "Backup completed"),
                        "log_id": log_id,
                        "commit_sha": push_result.get("commit_sha"),
                        "files_changed": push_result.get("files_changed", 0),
                    }

                except Exception as e:
                    logger.error("Backup failed: %s", e)
                    log.status = "failed"
                    log.completed_at = datetime.now(timezone.utc)
                    log.error_message = str(e)

                    config.last_backup_at = datetime.now(timezone.utc)
                    config.last_backup_status = "failed"
                    config.last_backup_message = str(e)

                    if config.schedule_enabled:
                        config.next_scheduled_run = self.calculate_next_run(config.schedule_type)

                    await db.commit()
                    return {
                        "success": False,
                        "message": str(e),
                        "log_id": log_id,
                        "commit_sha": None,
                        "files_changed": 0,
                    }

        finally:
            self._running_backup = False
            self._backup_progress = None

    async def _collect_backup_data(self, db: AsyncSession, config: GitHubBackupConfig) -> dict:
        """Collect data to backup based on config settings.

        Returns dict with structure:
        {
            "backup_metadata.json": {...},
            "kprofiles/{serial}/{nozzle}.json": {...},
            "cloud_profiles/filament.json": [...],
            "cloud_profiles/printer.json": [...],
            "cloud_profiles/process.json": [...],
            "settings/app_settings.json": {...},
        }
        """
        files: dict[str, dict | list] = {}

        # Metadata file (no timestamps - git tracks file history)
        metadata = {
            "version": "1.0",
            "backup_type": "bambuddy_profiles",
            "contents": {
                "kprofiles": config.backup_kprofiles,
                "cloud_profiles": config.backup_cloud_profiles,
                "settings": config.backup_settings,
                "spools": config.backup_spools,
                "archives": config.backup_archives,
            },
        }
        files["backup_metadata.json"] = metadata

        # Collect K-profiles from all connected printers
        if config.backup_kprofiles:
            self._backup_progress = "Collecting K-profiles from printers..."
            await self._collect_kprofiles(db, files)

        # Collect cloud profiles
        if config.backup_cloud_profiles:
            self._backup_progress = "Collecting cloud profiles from Bambu Cloud..."
            await self._collect_cloud_profiles(db, files)

        # Collect app settings
        if config.backup_settings:
            self._backup_progress = "Collecting app settings..."
            await self._collect_settings(db, files)

        # Collect spool inventory
        if config.backup_spools:
            self._backup_progress = "Collecting spool inventory..."
            await self._collect_spools(db, files)

        # Collect print archives
        if config.backup_archives:
            self._backup_progress = "Collecting print archives..."
            await self._collect_archives(db, files)

        return files

    async def _collect_kprofiles(self, db: AsyncSession, files: dict):
        """Collect K-profiles from all connected printers."""
        result = await db.execute(select(Printer).where(Printer.is_active == True))  # noqa: E712
        printers = result.scalars().all()

        nozzle_diameters = ["0.2", "0.4", "0.6", "0.8"]

        for printer in printers:
            client = printer_manager.get_client(printer.id)
            if not client or not client.state.connected:
                continue

            serial = printer.serial_number
            printer_profiles = {}

            for nozzle in nozzle_diameters:
                try:
                    profiles = await client.get_kprofiles(nozzle_diameter=nozzle)
                    if profiles:
                        profile_data = {
                            "version": "1.0",
                            "printer_name": printer.name,
                            "printer_serial": serial,
                            "nozzle_diameter": nozzle,
                            "profiles": [
                                {
                                    "slot_id": p.slot_id,
                                    "name": p.name,
                                    "k_value": p.k_value,
                                    "filament_id": p.filament_id,
                                    "nozzle_id": p.nozzle_id,
                                    "extruder_id": p.extruder_id,
                                    "setting_id": p.setting_id,
                                    "n_coef": p.n_coef,
                                }
                                for p in profiles
                            ],
                        }
                        files[f"kprofiles/{serial}/{nozzle}.json"] = profile_data
                        printer_profiles[nozzle] = len(profiles)
                except Exception as e:
                    logger.warning("Failed to get K-profiles for printer %s nozzle %s: %s", serial, nozzle, e)

            if printer_profiles:
                logger.info("Collected K-profiles for %s: %s", serial, printer_profiles)

    async def _collect_cloud_profiles(self, db: AsyncSession, files: dict):
        """Collect Bambu Cloud profiles if authenticated."""
        # Backup runs without a user context, so fall back to the auth-disabled
        # Settings storage. ``build_authenticated_cloud`` honours the stored
        # region so China-region tokens are validated against api.bambulab.cn.
        from backend.app.api.routes.cloud import build_authenticated_cloud

        cloud = await build_authenticated_cloud(db, user=None)
        if cloud is None or not cloud.is_authenticated:
            if cloud is not None:
                await cloud.close()
            logger.info("Cloud not authenticated, skipping cloud profiles")
            return

        try:
            settings = await cloud.get_slicer_settings()
            if not settings:
                return

            # Separate by type
            filament_settings = []
            printer_settings = []
            process_settings = []

            for setting in settings.get("setting", []) if isinstance(settings.get("setting"), list) else []:
                setting_type = setting.get("type", "")
                if setting_type == "filament":
                    filament_settings.append(setting)
                elif setting_type == "printer":
                    printer_settings.append(setting)
                elif setting_type == "process":
                    process_settings.append(setting)

            if filament_settings:
                files["cloud_profiles/filament.json"] = {
                    "version": "1.0",
                    "profiles": filament_settings,
                }

            if printer_settings:
                files["cloud_profiles/printer.json"] = {
                    "version": "1.0",
                    "profiles": printer_settings,
                }

            if process_settings:
                files["cloud_profiles/process.json"] = {
                    "version": "1.0",
                    "profiles": process_settings,
                }

            logger.info(
                f"Collected cloud profiles: {len(filament_settings)} filament, "
                f"{len(printer_settings)} printer, {len(process_settings)} process"
            )

        except Exception as e:
            logger.warning("Failed to collect cloud profiles: %s", e)
        finally:
            await cloud.close()

    async def _collect_settings(self, db: AsyncSession, files: dict):
        """Collect app settings."""
        result = await db.execute(select(Settings))
        settings = result.scalars().all()

        # Filter out sensitive settings
        sensitive_keys = {"bambu_cloud_token", "auth_secret_key"}
        settings_data = {s.key: s.value for s in settings if s.key not in sensitive_keys}

        files["settings/app_settings.json"] = {
            "version": "1.0",
            "settings": settings_data,
        }

    async def _collect_spools(self, db: AsyncSession, files: dict):
        """Collect spool inventory data."""
        result = await db.execute(select(Spool))
        spools = result.scalars().all()

        if not spools:
            return

        spool_list = []
        for s in spools:
            spool_data = {
                "id": s.id,
                "material": s.material,
                "subtype": s.subtype,
                "color_name": s.color_name,
                "rgba": s.rgba,
                "brand": s.brand,
                "label_weight": s.label_weight,
                "core_weight": s.core_weight,
                "weight_used": s.weight_used,
                "weight_locked": s.weight_locked,
                "slicer_filament": s.slicer_filament,
                "slicer_filament_name": s.slicer_filament_name,
                "nozzle_temp_min": s.nozzle_temp_min,
                "nozzle_temp_max": s.nozzle_temp_max,
                "note": s.note,
                "cost_per_kg": s.cost_per_kg,
                "tag_uid": s.tag_uid,
                "tray_uuid": s.tray_uuid,
                "data_origin": s.data_origin,
                "tag_type": s.tag_type,
                "archived_at": str(s.archived_at) if s.archived_at else None,
                "created_at": str(s.created_at) if s.created_at else None,
            }
            spool_list.append(spool_data)

        files["spools/inventory.json"] = {
            "version": "1.0",
            "spools": spool_list,
        }

        # Collect usage history
        usage_result = await db.execute(select(SpoolUsageHistory))
        usages = usage_result.scalars().all()

        if usages:
            usage_list = []
            for u in usages:
                usage_list.append(
                    {
                        "id": u.id,
                        "spool_id": u.spool_id,
                        "printer_id": u.printer_id,
                        "print_name": u.print_name,
                        "archive_id": u.archive_id,
                        "weight_used": u.weight_used,
                        "percent_used": u.percent_used,
                        "status": u.status,
                        "cost": u.cost,
                        "created_at": str(u.created_at) if u.created_at else None,
                    }
                )
            files["spools/usage_history.json"] = {
                "version": "1.0",
                "usage_history": usage_list,
            }

        logger.info("Collected %d spools and %d usage records", len(spool_list), len(usages))

    async def _collect_archives(self, db: AsyncSession, files: dict):
        """Collect print archive metadata (no binary files)."""
        result = await db.execute(select(PrintArchive))
        archives = result.scalars().all()

        if not archives:
            return

        archive_list = []
        for a in archives:
            archive_data = {
                "id": a.id,
                "printer_id": a.printer_id,
                "project_id": a.project_id,
                "filename": a.filename,
                "file_size": a.file_size,
                "content_hash": a.content_hash,
                "print_name": a.print_name,
                "print_time_seconds": a.print_time_seconds,
                "filament_used_grams": a.filament_used_grams,
                "filament_type": a.filament_type,
                "filament_color": a.filament_color,
                "layer_height": a.layer_height,
                "total_layers": a.total_layers,
                "nozzle_diameter": a.nozzle_diameter,
                "bed_temperature": a.bed_temperature,
                "nozzle_temperature": a.nozzle_temperature,
                "sliced_for_model": a.sliced_for_model,
                "status": a.status,
                "started_at": str(a.started_at) if a.started_at else None,
                "completed_at": str(a.completed_at) if a.completed_at else None,
                "makerworld_url": a.makerworld_url,
                "designer": a.designer,
                "external_url": a.external_url,
                "is_favorite": a.is_favorite,
                "tags": a.tags,
                "notes": a.notes,
                "cost": a.cost,
                "failure_reason": a.failure_reason,
                "quantity": a.quantity,
                "energy_kwh": a.energy_kwh,
                "energy_cost": a.energy_cost,
                "created_at": str(a.created_at) if a.created_at else None,
            }
            archive_list.append(archive_data)

        files["archives/print_history.json"] = {
            "version": "1.0",
            "archives": archive_list,
        }

        logger.info("Collected %d print archives", len(archive_list))

    async def _push_to_github(self, config: GitHubBackupConfig, files: dict) -> dict:
        """Push files to the configured Git provider."""
        backend = get_provider_backend(config.provider)
        client = await self._get_client()
        return await backend.push_files(
            repo_url=config.repository_url,
            token=config.access_token,
            branch=config.branch,
            files=files,
            client=client,
        )

    @property
    def is_running(self) -> bool:
        """Check if a backup is currently running."""
        return self._running_backup

    @property
    def progress(self) -> str | None:
        """Get current backup progress message."""
        return self._backup_progress

    async def get_logs(self, config_id: int, limit: int = 50, offset: int = 0) -> list[GitHubBackupLog]:
        """Get backup logs for a configuration."""
        async with async_session() as db:
            result = await db.execute(
                select(GitHubBackupLog)
                .where(GitHubBackupLog.config_id == config_id)
                .order_by(desc(GitHubBackupLog.started_at))
                .offset(offset)
                .limit(limit)
            )
            return list(result.scalars().all())


# Singleton instance
github_backup_service = GitHubBackupService()
