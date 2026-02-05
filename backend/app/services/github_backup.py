"""GitHub backup service for printer profiles.

Handles scheduled and on-demand backups of K-profiles and cloud profiles to GitHub.
"""

import asyncio
import base64
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import async_session
from backend.app.models.github_backup import GitHubBackupConfig, GitHubBackupLog
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.services.bambu_cloud import get_cloud_service
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
                logger.error(f"Error in GitHub backup scheduler: {e}")
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
                    logger.info(f"Running scheduled backup for config {config.id}")
                    await self.run_backup(config.id, trigger="scheduled")

    def _calculate_next_run(self, schedule_type: str, from_time: datetime | None = None) -> datetime:
        """Calculate the next scheduled run time."""
        now = from_time or datetime.now(timezone.utc)
        interval = SCHEDULE_INTERVALS.get(schedule_type, SCHEDULE_INTERVALS["daily"])
        return now + timedelta(seconds=interval)

    async def test_connection(self, repo_url: str, token: str) -> dict:
        """Test GitHub connection and permissions.

        Args:
            repo_url: GitHub repository URL
            token: Personal Access Token

        Returns:
            dict with success, message, repo_name, permissions
        """
        try:
            owner, repo = self._parse_repo_url(repo_url)
            client = await self._get_client()

            # Test API access
            response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}",
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "Bambuddy-Backup",
                },
            )

            if response.status_code == 401:
                return {"success": False, "message": "Invalid access token", "repo_name": None, "permissions": None}

            if response.status_code == 404:
                return {
                    "success": False,
                    "message": "Repository not found. Check URL and token permissions.",
                    "repo_name": None,
                    "permissions": None,
                }

            if response.status_code != 200:
                return {
                    "success": False,
                    "message": f"GitHub API error: {response.status_code}",
                    "repo_name": None,
                    "permissions": None,
                }

            data = response.json()
            permissions = data.get("permissions", {})

            # Check for push permission
            if not permissions.get("push", False):
                return {
                    "success": False,
                    "message": "Token does not have push permission to this repository",
                    "repo_name": data.get("full_name"),
                    "permissions": permissions,
                }

            return {
                "success": True,
                "message": "Connection successful",
                "repo_name": data.get("full_name"),
                "permissions": permissions,
            }

        except Exception as e:
            logger.error(f"GitHub connection test failed: {e}")
            # Sanitize error - don't expose internal details
            error_type = type(e).__name__
            return {
                "success": False,
                "message": f"Connection failed: {error_type}",
                "repo_name": None,
                "permissions": None,
            }

    def _parse_repo_url(self, url: str) -> tuple[str, str]:
        """Parse owner and repo from GitHub URL."""
        # Limit URL length to prevent ReDoS attacks
        if not url or len(url) > 500:
            raise ValueError("Invalid GitHub URL: URL too long or empty")

        # Handle HTTPS URLs - use atomic groups via limited character classes
        # GitHub usernames: 1-39 chars, alphanumeric and hyphens
        # Repo names: 1-100 chars, alphanumeric, hyphens, underscores, dots
        match = re.match(r"https://github\.com/([\w-]{1,39})/([\w.\-]{1,100})(?:\.git)?/?$", url)
        if match:
            return match.group(1), match.group(2)

        # Handle SSH URLs
        match = re.match(r"git@github\.com:([\w-]{1,39})/([\w.\-]{1,100})(?:\.git)?$", url)
        if match:
            return match.group(1), match.group(2)

        raise ValueError(f"Invalid GitHub URL: {url}")

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
                            config.next_scheduled_run = self._calculate_next_run(config.schedule_type)
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
                        config.next_scheduled_run = self._calculate_next_run(config.schedule_type)

                    await db.commit()

                    return {
                        "success": push_result["status"] in ("success", "skipped"),
                        "message": push_result.get("message", "Backup completed"),
                        "log_id": log_id,
                        "commit_sha": push_result.get("commit_sha"),
                        "files_changed": push_result.get("files_changed", 0),
                    }

                except Exception as e:
                    logger.error(f"Backup failed: {e}")
                    log.status = "failed"
                    log.completed_at = datetime.now(timezone.utc)
                    log.error_message = str(e)

                    config.last_backup_at = datetime.now(timezone.utc)
                    config.last_backup_status = "failed"
                    config.last_backup_message = str(e)

                    if config.schedule_enabled:
                        config.next_scheduled_run = self._calculate_next_run(config.schedule_type)

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
                    logger.warning(f"Failed to get K-profiles for printer {serial} nozzle {nozzle}: {e}")

            if printer_profiles:
                logger.info(f"Collected K-profiles for {serial}: {printer_profiles}")

    async def _collect_cloud_profiles(self, db: AsyncSession, files: dict):
        """Collect Bambu Cloud profiles if authenticated."""
        # Check if cloud is authenticated
        cloud = get_cloud_service()

        # Try to restore token from DB
        result = await db.execute(select(Settings).where(Settings.key == "bambu_cloud_token"))
        setting = result.scalar_one_or_none()
        if setting and setting.value:
            cloud.set_token(setting.value)

        if not cloud.is_authenticated:
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
            logger.warning(f"Failed to collect cloud profiles: {e}")

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

    async def _push_to_github(self, config: GitHubBackupConfig, files: dict) -> dict:
        """Push files to GitHub using the GitHub API.

        Uses the Git Data API to create blobs, tree, and commit.

        Returns:
            dict with status, message, commit_sha, files_changed
        """
        try:
            owner, repo = self._parse_repo_url(config.repository_url)
            branch = config.branch
            client = await self._get_client()
            headers = {
                "Authorization": f"token {config.access_token}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "Bambuddy-Backup",
            }

            # Get current branch reference
            ref_response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/git/refs/heads/{branch}", headers=headers
            )

            if ref_response.status_code == 404:
                # Branch doesn't exist, need to create it from default branch
                return await self._create_branch_and_push(client, headers, owner, repo, branch, files)

            if ref_response.status_code != 200:
                return {
                    "status": "failed",
                    "message": f"Failed to get branch ref: {ref_response.status_code}",
                    "error": ref_response.text,
                }

            ref_data = ref_response.json()
            current_commit_sha = ref_data["object"]["sha"]

            # Get the current tree
            commit_response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/git/commits/{current_commit_sha}", headers=headers
            )
            if commit_response.status_code != 200:
                return {"status": "failed", "message": "Failed to get current commit"}

            current_tree_sha = commit_response.json()["tree"]["sha"]

            # Get existing files to check for changes
            tree_response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/git/trees/{current_tree_sha}?recursive=1", headers=headers
            )
            existing_files = {}
            if tree_response.status_code == 200:
                for item in tree_response.json().get("tree", []):
                    if item["type"] == "blob":
                        existing_files[item["path"]] = item["sha"]

            # Create blobs for changed files
            tree_items = []
            files_changed = 0

            for path, content in files.items():
                content_str = json.dumps(content, indent=2, default=str)
                content_bytes = content_str.encode("utf-8")
                content_sha = hashlib.sha1(f"blob {len(content_bytes)}\0".encode() + content_bytes).hexdigest()

                # Skip if file hasn't changed
                if path in existing_files and existing_files[path] == content_sha:
                    continue

                # Create blob
                blob_response = await client.post(
                    f"https://api.github.com/repos/{owner}/{repo}/git/blobs",
                    headers=headers,
                    json={"content": base64.b64encode(content_bytes).decode(), "encoding": "base64"},
                )

                if blob_response.status_code != 201:
                    logger.error(f"Failed to create blob for {path}: {blob_response.text}")
                    continue

                blob_sha = blob_response.json()["sha"]
                tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": blob_sha})
                files_changed += 1

            if not tree_items:
                return {"status": "skipped", "message": "No changes to commit", "commit_sha": None, "files_changed": 0}

            # Create new tree
            tree_response = await client.post(
                f"https://api.github.com/repos/{owner}/{repo}/git/trees",
                headers=headers,
                json={"base_tree": current_tree_sha, "tree": tree_items},
            )

            if tree_response.status_code != 201:
                return {"status": "failed", "message": f"Failed to create tree: {tree_response.text}"}

            new_tree_sha = tree_response.json()["sha"]

            # Create commit
            commit_message = f"Bambuddy backup - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
            commit_response = await client.post(
                f"https://api.github.com/repos/{owner}/{repo}/git/commits",
                headers=headers,
                json={"message": commit_message, "tree": new_tree_sha, "parents": [current_commit_sha]},
            )

            if commit_response.status_code != 201:
                return {"status": "failed", "message": f"Failed to create commit: {commit_response.text}"}

            new_commit_sha = commit_response.json()["sha"]

            # Update branch reference
            ref_update = await client.patch(
                f"https://api.github.com/repos/{owner}/{repo}/git/refs/heads/{branch}",
                headers=headers,
                json={"sha": new_commit_sha},
            )

            if ref_update.status_code != 200:
                return {"status": "failed", "message": f"Failed to update branch: {ref_update.text}"}

            return {
                "status": "success",
                "message": f"Backup successful - {files_changed} files updated",
                "commit_sha": new_commit_sha,
                "files_changed": files_changed,
            }

        except Exception as e:
            logger.error(f"Push to GitHub failed: {e}")
            return {"status": "failed", "message": str(e), "error": str(e)}

    async def _create_branch_and_push(
        self, client: httpx.AsyncClient, headers: dict, owner: str, repo: str, branch: str, files: dict
    ) -> dict:
        """Create a new branch and push files when branch doesn't exist."""
        try:
            # Get default branch
            repo_response = await client.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers)
            if repo_response.status_code != 200:
                return {"status": "failed", "message": "Failed to get repo info"}

            default_branch = repo_response.json().get("default_branch", "main")

            # Get default branch ref
            ref_response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/git/refs/heads/{default_branch}", headers=headers
            )
            if ref_response.status_code != 200:
                # Empty repo - create initial commit
                return await self._create_initial_commit(client, headers, owner, repo, branch, files)

            base_sha = ref_response.json()["object"]["sha"]

            # Create new branch
            create_ref = await client.post(
                f"https://api.github.com/repos/{owner}/{repo}/git/refs",
                headers=headers,
                json={"ref": f"refs/heads/{branch}", "sha": base_sha},
            )

            if create_ref.status_code != 201:
                return {"status": "failed", "message": f"Failed to create branch: {create_ref.text}"}

            # Now push to the new branch (recursive call will find the branch)
            return await self._push_to_github(
                type(
                    "Config",
                    (),
                    {
                        "repository_url": f"https://github.com/{owner}/{repo}",
                        "access_token": headers["Authorization"].replace("token ", ""),
                        "branch": branch,
                    },
                )(),
                files,
            )

        except Exception as e:
            return {"status": "failed", "message": str(e)}

    async def _create_initial_commit(
        self, client: httpx.AsyncClient, headers: dict, owner: str, repo: str, branch: str, files: dict
    ) -> dict:
        """Create initial commit in an empty repository."""
        try:
            # Create blobs
            tree_items = []
            for path, content in files.items():
                content_str = json.dumps(content, indent=2, default=str)
                blob_response = await client.post(
                    f"https://api.github.com/repos/{owner}/{repo}/git/blobs",
                    headers=headers,
                    json={"content": base64.b64encode(content_str.encode()).decode(), "encoding": "base64"},
                )
                if blob_response.status_code == 201:
                    tree_items.append(
                        {"path": path, "mode": "100644", "type": "blob", "sha": blob_response.json()["sha"]}
                    )

            # Create tree
            tree_response = await client.post(
                f"https://api.github.com/repos/{owner}/{repo}/git/trees",
                headers=headers,
                json={"tree": tree_items},
            )
            if tree_response.status_code != 201:
                return {"status": "failed", "message": "Failed to create tree"}

            tree_sha = tree_response.json()["sha"]

            # Create commit (no parents for initial)
            commit_response = await client.post(
                f"https://api.github.com/repos/{owner}/{repo}/git/commits",
                headers=headers,
                json={
                    "message": f"Initial Bambuddy backup - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
                    "tree": tree_sha,
                },
            )
            if commit_response.status_code != 201:
                return {"status": "failed", "message": "Failed to create commit"}

            commit_sha = commit_response.json()["sha"]

            # Create branch ref
            ref_response = await client.post(
                f"https://api.github.com/repos/{owner}/{repo}/git/refs",
                headers=headers,
                json={"ref": f"refs/heads/{branch}", "sha": commit_sha},
            )
            if ref_response.status_code != 201:
                return {"status": "failed", "message": "Failed to create branch ref"}

            return {
                "status": "success",
                "message": f"Initial backup created - {len(files)} files",
                "commit_sha": commit_sha,
                "files_changed": len(files),
            }

        except Exception as e:
            return {"status": "failed", "message": str(e)}

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
