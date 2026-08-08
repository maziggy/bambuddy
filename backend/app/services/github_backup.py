"""GitHub backup service for printer profiles.

Handles scheduled and on-demand backups of K-profiles and cloud profiles to GitHub.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import async_session
from backend.app.models.archive import PrintArchive
from backend.app.models.github_backup import GitHubBackupConfig, GitHubBackupLog
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.models.spool_usage_history import SpoolUsageHistory
from backend.app.models.user import User
from backend.app.services.git_providers.factory import get_provider_backend
from backend.app.services.printer_manager import printer_manager

logger = logging.getLogger(__name__)

# Bambu's listing endpoint is keyed by preset type and calls process presets
# "print". Same mapping as `routes/cloud.py` — kept in step with it, since a
# divergence here silently drops a whole preset type from every backup.
_BAMBU_PRESET_TYPES = {
    "filament": "filament",
    "printer": "printer",
    "print": "process",
}


def _bambu_preset_record(setting_id, our_type: str, entry: dict, detail: dict) -> dict:
    """One Bambu preset as stored in the backup: metadata plus the payload.

    ``base_id`` and ``setting`` are the two fields ``BambuCloudService.
    create_setting`` needs, so a restore can rebuild the preset rather than
    just list it.

    ``user_id`` from the listing is deliberately dropped. It identifies the
    account and adds nothing to a rebuild, and backup repositories can be
    public.
    """
    return {
        "setting_id": str(setting_id),
        "name": detail.get("name") or entry.get("name") or "Unknown",
        "type": our_type,
        "version": detail.get("version") or entry.get("version"),
        "updated_time": entry.get("updated_time"),
        "base_id": detail.get("base_id"),
        "filament_id": detail.get("filament_id"),
        "setting": detail.get("setting") or {},
    }


def _orca_profile_record(entry: dict) -> dict:
    """One Orca profile as stored in the backup.

    ``content`` is kept whole rather than picked apart: it is the profile, the
    sync API hands it over inline, and Orca owns its shape. Narrowing it here
    would mean guessing which keys a future restore needs.
    """
    return {
        "id": str(entry.get("id")) if entry.get("id") is not None else None,
        "name": entry.get("name"),
        "updated_time": entry.get("updated_time"),
        "created_time": entry.get("created_time"),
        "content": entry.get("content"),
    }


# Schedule intervals in seconds
SCHEDULE_INTERVALS = {
    "hourly": 3600,
    "daily": 86400,
    "weekly": 604800,
}

_PROVIDER_DISPLAY_NAMES = {
    "github": "GitHub",
    "gitlab": "GitLab",
    "gitea": "Gitea",
    "forgejo": "Forgejo",
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
            except Exception:
                logger.exception("Error in GitHub backup scheduler")
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
        # Everything from here to `self._running_backup = True` must stay
        # await-free. Both flags are plain bools and both callers are coroutines
        # on one event loop, so with no suspension point in between the loop
        # cannot run the restore service's mirror-image region (see
        # github_restore.run_restore) in the gap — whichever gets here first sets
        # its flag before the other can read it. Adding an `await` inside this
        # block reintroduces the check-then-set race and lets a backup and a
        # restore run at once.
        if self._running_backup:
            return {"success": False, "message": "A backup is already running", "log_id": None}

        # Imported locally to avoid a module-level import cycle — the restore
        # service imports this module's singleton to take the mirror-image lock.
        # A restore rewrites the same tables this collector reads and publishes
        # K-profiles to the same printers, so the two must not interleave.
        # (A local `import` of an already-loaded module is not a suspension
        # point, so it does not break the await-free rule above.)
        from backend.app.services.github_restore import github_restore_service

        if github_restore_service.is_running:
            return {
                "success": False,
                "message": "A restore is currently running. Wait for it to finish before backing up.",
                "log_id": None,
            }

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

                # Defense in depth: re-verify the repo is private before each
                # push. The save endpoint already enforces this on every config
                # change, but a user can flip a repo from private to public in
                # GitHub's UI between configuration and the next scheduled run.
                test_result = await self.test_connection(
                    config.repository_url, config.access_token, provider=config.provider
                )
                if not test_result.get("success") or test_result.get("is_private") is not True:
                    visibility_note = (
                        "the target repository is no longer private"
                        if test_result.get("is_private") is False
                        else "could not confirm the target repository is private"
                    )
                    abort_message = (
                        f"Backup aborted: {visibility_note}. Bambuddy backups carry credentials "
                        "and are refused for any non-private target. Make the repository private "
                        "to resume scheduled backups."
                    )
                    log = GitHubBackupLog(
                        config_id=config_id,
                        status="failed",
                        trigger=trigger,
                        completed_at=datetime.now(timezone.utc),
                        error_message=abort_message,
                    )
                    db.add(log)
                    config.last_backup_at = datetime.now(timezone.utc)
                    config.last_backup_status = "failed"
                    config.last_backup_message = abort_message
                    if config.schedule_enabled:
                        config.next_scheduled_run = self.calculate_next_run(config.schedule_type)
                    await db.commit()
                    await db.refresh(log)
                    logger.warning(
                        "Backup aborted for config %s: repo not private (is_private=%r, success=%r)",
                        config_id,
                        test_result.get("is_private"),
                        test_result.get("success"),
                    )
                    return {
                        "success": False,
                        "message": abort_message,
                        "log_id": log.id,
                    }

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

                    provider_name = _PROVIDER_DISPLAY_NAMES.get(config.provider, config.provider)
                    self._backup_progress = f"Pushing to {provider_name}..."
                    push_result = await self._push_to_provider(config, backup_data)

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
                    logger.exception("Backup failed")
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
            "cloud_profiles/bambu/{account}/{filament,printer,process}.json": {...},
            "cloud_profiles/orca/{account}/{filament,printer,process}.json": {...},
            "settings/app_settings.json": {...},
        }

        ``{account}`` is ``global`` when auth is disabled, otherwise
        ``user-{id}`` — one directory per connected cloud account (#2717).
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

        # Collect cloud profiles. `contents.cloud_profiles` is corrected below
        # from what was configured to what was actually written — it claimed
        # `true` on every backup, including the ones that collected nothing
        # (#2717), which is exactly the signal a restore needs to be able to
        # trust.
        if config.backup_cloud_profiles:
            self._backup_progress = "Collecting cloud profiles from Bambu Cloud and Orca Cloud..."
            cloud_summary = await self._collect_cloud_profiles(db, files)
            collected = bool(cloud_summary.get("bambu") or cloud_summary.get("orca"))
            metadata["contents"]["cloud_profiles"] = collected
            if collected:
                # Per-cloud, per-account counts, so a restore can tell an empty
                # account from one that failed to collect.
                metadata["cloud_profiles"] = cloud_summary

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

    async def _collect_cloud_profiles(self, db: AsyncSession, files: dict) -> dict:
        """Collect slicer presets from every connected cloud account.

        Two clouds, and on an auth-enabled install any number of accounts in
        each: Bambu Cloud tokens live on ``User.cloud_token`` and Orca Cloud
        tokens on ``User.orca_cloud_token``, falling back to the global
        ``Settings`` table only when auth is disabled. The previous version
        asked for the auth-disabled store unconditionally, so it collected
        nothing at all on any install with auth on (#2717).

        Layout is one directory per cloud per account, both clouds grouped the
        same way so a restore reads them identically::

            cloud_profiles/bambu/user-3/{filament,printer,process}.json
            cloud_profiles/orca/user-3/{filament,printer,process}.json

        Accounts are keyed by Bambuddy user id (``global`` when auth is off),
        never by email — a backup repository can be public.

        Returns a per-cloud summary for ``backup_metadata.json`` so the
        metadata records what was actually collected rather than what was
        merely enabled.
        """
        summary: dict = {"bambu": {}, "orca": {}}

        bambu_accounts, orca_accounts = await self.cloud_accounts(db)
        if not bambu_accounts and not orca_accounts:
            # Enabled but nothing to collect. Deliberately a warning: the INFO
            # line this replaces read as a successful collection of nothing,
            # which is how #2717 went unnoticed through every backup.
            logger.warning(
                "Cloud profiles are enabled for backup, but no Bambu Cloud or Orca Cloud "
                "account is connected — nothing to collect."
            )
            return summary

        for account_key, user in bambu_accounts:
            try:
                counts = await self._collect_bambu_profiles(db, files, account_key, user)
            except Exception:
                logger.warning("Failed to collect Bambu Cloud profiles for %s", account_key, exc_info=True)
                continue
            if counts:
                summary["bambu"][account_key] = counts

        for account_key, user in orca_accounts:
            try:
                counts = await self._collect_orca_profiles(db, files, account_key, user)
            except Exception:
                logger.warning("Failed to collect Orca Cloud profiles for %s", account_key, exc_info=True)
                continue
            if counts:
                summary["orca"][account_key] = counts

        if not summary["bambu"] and not summary["orca"]:
            logger.warning(
                "Cloud profiles are enabled and %d Bambu / %d Orca account(s) are connected, "
                "but no presets were collected — see the per-account warnings above.",
                len(bambu_accounts),
                len(orca_accounts),
            )
        else:
            logger.info("Collected cloud profiles: %s", summary)
        return summary

    async def cloud_accounts(self, db: AsyncSession) -> tuple[list, list]:
        """Enumerate connected accounts as ``(account_key, user_or_None)`` per cloud.

        With auth enabled every user holds their own credentials, so a backup
        that only looked at the global store saw none of them. With auth
        disabled there is a single global row and no ``User`` at all, which is
        what ``user=None`` means to both clouds' credential loaders.

        Both stores are read regardless: a ``Settings`` row survives enabling
        auth later, and dropping it silently would lose that account's presets.
        """
        from backend.app.api.routes.cloud import get_stored_token
        from backend.app.api.routes.orca_cloud import _load_credentials

        bambu: list = []
        orca: list = []

        global_token, _email, _region = await get_stored_token(db, None)
        if global_token:
            bambu.append(("global", None))
        global_orca = await _load_credentials(db, None)
        if global_orca.token:
            orca.append(("global", None))

        result = await db.execute(
            select(User).where(or_(User.cloud_token.isnot(None), User.orca_cloud_token.isnot(None)))
        )
        for user in result.scalars().all():
            if user.cloud_token:
                bambu.append((f"user-{user.id}", user))
            if user.orca_cloud_token:
                orca.append((f"user-{user.id}", user))

        return bambu, orca

    async def _collect_bambu_profiles(self, db: AsyncSession, files: dict, account_key: str, user) -> dict:
        """Collect one Bambu Cloud account's custom presets, with their payloads.

        The listing endpoint is keyed by preset type, each holding ``private``
        and ``public`` lists — there is no flat ``setting`` array, and the
        entries carry no ``type`` of their own, which is why the type comes
        from the outer key here exactly as it does in ``routes/cloud.py``.
        Bambu calls process presets ``print``.

        ``public`` is skipped: those are Bambu's own bundled catalogue, the
        same hundreds of entries for every user, re-downloadable at any time
        and not recreatable under your account anyway. Backing them up would
        churn the repository on every run for nothing.

        Each private preset then costs one ``get_setting_detail`` call, because
        the listing carries only metadata. Without ``base_id`` and ``setting``
        the backup is a list of names, not something a restore can rebuild
        from. Bounded by the number of *custom* presets, and the backup already
        makes a round-trip per printer for K-profiles.
        """
        from backend.app.api.routes.cloud import build_authenticated_cloud

        cloud = await build_authenticated_cloud(db, user=user)
        if cloud is None or not cloud.is_authenticated:
            logger.info("Bambu Cloud not authenticated for %s, skipping", account_key)
            return {}

        counts: dict = {}
        try:
            settings = await cloud.get_slicer_settings()
            if not isinstance(settings, dict) or not settings:
                logger.warning("Bambu Cloud returned no slicer settings for %s", account_key)
                return {}

            failed = 0
            for api_key, our_type in _BAMBU_PRESET_TYPES.items():
                type_data = settings.get(api_key)
                if not isinstance(type_data, dict):
                    continue
                private = type_data.get("private")
                if not isinstance(private, list) or not private:
                    continue

                profiles = []
                for entry in private:
                    setting_id = entry.get("setting_id") or entry.get("id")
                    if not setting_id:
                        continue
                    try:
                        detail = await cloud.get_setting_detail(str(setting_id))
                    except Exception as e:
                        # One unreadable preset must not cost the rest of the
                        # account, but it must not vanish quietly either.
                        failed += 1
                        logger.warning(
                            "Failed to fetch Bambu Cloud preset %s (%s) for %s: %s",
                            setting_id,
                            entry.get("name", "unnamed"),
                            account_key,
                            e,
                        )
                        continue
                    profiles.append(_bambu_preset_record(setting_id, our_type, entry, detail))

                if profiles:
                    files[f"cloud_profiles/bambu/{account_key}/{our_type}.json"] = {
                        "version": "2.0",
                        "cloud": "bambu",
                        "type": our_type,
                        "profiles": profiles,
                    }
                    counts[our_type] = len(profiles)

            if failed:
                counts["failed"] = failed
            return counts
        finally:
            await cloud.close()

    async def _collect_orca_profiles(self, db: AsyncSession, files: dict, account_key: str, user) -> dict:
        """Collect one Orca Cloud account's profiles, grouped the same three ways.

        Cheaper than Bambu: the sync-pull listing already carries each
        profile's full ``content``, so there is no per-profile fetch.

        The type lives at ``content.type`` and is mapped through the same
        ``_ORCA_TYPE_TO_BAMBU`` table the Orca tab uses, so the backup groups
        exactly as the UI does. Where that route *drops* a profile whose type
        it can't map, this writes it to ``other.json`` instead — a backup that
        silently omits a profile because Orca added a type is the same class of
        bug as #2717 itself.

        Uses the route layer's ``_build_authenticated_service`` rather than
        re-implementing the refresh: the Orca refresh token is single-use and
        rotating, and that helper already persists the new pair atomically
        before returning.

        Passes ``clear_on_auth_failure=False``, so a rejected refresh skips the
        account instead of disconnecting it. A backup is an observer; it should
        not change anyone's sign-in state on a schedule, least of all on a
        rejection reason Orca does not disambiguate. The next time the user
        opens the Orca Profiles page that route clears the dead pairing anyway,
        with the user present to pair again.
        """
        from fastapi import HTTPException

        from backend.app.api.routes.orca_cloud import (
            _ORCA_TYPE_TO_BAMBU,
            _build_authenticated_service,
        )

        try:
            svc = await _build_authenticated_service(db, user, clear_on_auth_failure=False)
        except HTTPException as e:
            # Either way the stored credentials are untouched and this account
            # is skipped, not disconnected — but the two need different advice.
            # A rejected refresh will not fix itself and needs the user to pair
            # again; an unreachable Orca is very likely gone by the next run.
            if e.status_code == 401:
                logger.warning(
                    "Orca Cloud rejected the stored session for %s, so its profiles are not in this "
                    "backup. Later runs will skip it too until the account is paired again under "
                    "Profiles > Orca Cloud Profiles — which is also where the dead credentials get "
                    "cleared. Cause: %s",
                    account_key,
                    e.detail,
                )
            else:
                logger.warning(
                    "Orca Cloud unreachable for %s, skipping its profiles this run: %s",
                    account_key,
                    e.detail,
                )
            return {}
        except Exception as e:
            logger.warning("Orca Cloud not usable for %s: %s", account_key, e, exc_info=True)
            return {}

        counts: dict = {}
        try:
            raw_profiles = await svc.list_profiles()
            grouped: dict[str, list] = {}
            unknown_types: dict[str, int] = {}

            for entry in raw_profiles:
                if not isinstance(entry, dict):
                    continue
                content = entry.get("content")
                raw_type = content.get("type") if isinstance(content, dict) else None
                our_type = _ORCA_TYPE_TO_BAMBU.get(str(raw_type)) if raw_type is not None else None
                if our_type is None:
                    unknown_types[str(raw_type) if raw_type is not None else "<missing>"] = (
                        unknown_types.get(str(raw_type) if raw_type is not None else "<missing>", 0) + 1
                    )
                    our_type = "other"
                grouped.setdefault(our_type, []).append(_orca_profile_record(entry))

            for our_type, profiles in grouped.items():
                files[f"cloud_profiles/orca/{account_key}/{our_type}.json"] = {
                    "version": "2.0",
                    "cloud": "orca",
                    "type": our_type,
                    "profiles": profiles,
                }
                counts[our_type] = len(profiles)

            if unknown_types:
                logger.warning(
                    "Orca Cloud sent %d profile(s) for %s with unmapped content.type values %s — "
                    "backed up to other.json rather than dropped.",
                    sum(unknown_types.values()),
                    account_key,
                    unknown_types,
                )
            return counts
        finally:
            await svc.close()

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

        # The natural key for an owner. created_by_id alone is only meaningful on
        # the instance that wrote it: restoring onto a rebuilt instance — this
        # feature's main use case — renumbers the users table, so a live id can
        # land on a different person. username is unique on users, so the restore
        # can resolve on it and treat a rename as unknown rather than guess.
        # One query for the map; archives outnumber users by orders of magnitude.
        user_names = dict((await db.execute(select(User.id, User.username))).all())

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
                # Soft-deleted archives are collected too — their row is kept on
                # purpose so the stats endpoint keeps counting their filament and
                # energy (see archive_service.soft_delete_archive). Recording
                # deleted_at is what lets a restore put them back the way they
                # were instead of resurrecting them as visible archives.
                "deleted_at": str(a.deleted_at) if a.deleted_at else None,
                # Who owns the archive, for the same reason deleted_at is here:
                # it is not decoration, it is what the access check runs on.
                # _ensure_archive_visible (api/routes/archives.py) fails closed on
                # a NULL created_by_id and the list paths filter on it, so a
                # restored row without it is invisible to everyone but an admin —
                # while the restore reports it restored.
                "created_by_id": a.created_by_id,
                # Preferred over the id on restore; the id stays as the fallback
                # for an owner whose row has since gone. Null when the archive
                # has no owner, or when it points at a user row that no longer
                # exists locally — the same "absent is not null" rule the restore
                # applies, so a backup can't claim an owner it cannot name.
                "created_by_username": user_names.get(a.created_by_id),
            }
            archive_list.append(archive_data)

        files["archives/print_history.json"] = {
            "version": "1.0",
            "archives": archive_list,
        }

        logger.info("Collected %d print archives", len(archive_list))

    async def _push_to_provider(self, config: GitHubBackupConfig, files: dict) -> dict:
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
