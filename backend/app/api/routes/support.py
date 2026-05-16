"""Support endpoints for debug logging and support bundle generation."""

import asyncio
import importlib.metadata
import io
import ipaddress
import json
import logging
import os
import platform
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.config import APP_VERSION, settings
from backend.app.core.database import async_session
from backend.app.core.permissions import Permission
from backend.app.core.websocket import ws_manager
from backend.app.models.archive import PrintArchive
from backend.app.models.filament import Filament
from backend.app.models.notification import NotificationProvider
from backend.app.models.printer import Printer
from backend.app.models.project import Project
from backend.app.models.settings import Settings
from backend.app.models.smart_plug import SmartPlug
from backend.app.models.user import User
from backend.app.services.discovery import is_running_in_docker
from backend.app.services.network_utils import get_network_interfaces
from backend.app.services.printer_manager import printer_manager

router = APIRouter(prefix="/support", tags=["support"])
logger = logging.getLogger(__name__)


class DebugLoggingState(BaseModel):
    enabled: bool
    enabled_at: str | None = None
    duration_seconds: int | None = None


class DebugLoggingToggle(BaseModel):
    enabled: bool


async def _get_debug_setting(db: AsyncSession) -> tuple[bool, datetime | None]:
    """Get debug logging state from database."""
    result = await db.execute(select(Settings).where(Settings.key == "debug_logging_enabled"))
    enabled_setting = result.scalar_one_or_none()

    result = await db.execute(select(Settings).where(Settings.key == "debug_logging_enabled_at"))
    enabled_at_setting = result.scalar_one_or_none()

    enabled = enabled_setting.value.lower() == "true" if enabled_setting else False
    enabled_at = None
    if enabled_at_setting and enabled_at_setting.value:
        try:
            enabled_at = datetime.fromisoformat(enabled_at_setting.value)
            if enabled_at.tzinfo is None:
                enabled_at = enabled_at.replace(tzinfo=timezone.utc)
        except ValueError:
            pass  # Ignore malformed timestamp; enabled_at stays None

    return enabled, enabled_at


async def _set_debug_setting(db: AsyncSession, enabled: bool) -> datetime | None:
    """Set debug logging state in database."""
    # Update or create enabled setting
    result = await db.execute(select(Settings).where(Settings.key == "debug_logging_enabled"))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = str(enabled).lower()
    else:
        db.add(Settings(key="debug_logging_enabled", value=str(enabled).lower()))

    # Update enabled_at timestamp
    enabled_at = datetime.now(tz=timezone.utc) if enabled else None
    result = await db.execute(select(Settings).where(Settings.key == "debug_logging_enabled_at"))
    at_setting = result.scalar_one_or_none()
    if at_setting:
        at_setting.value = enabled_at.isoformat() if enabled_at else ""
    else:
        db.add(Settings(key="debug_logging_enabled_at", value=enabled_at.isoformat() if enabled_at else ""))

    await db.commit()
    return enabled_at


def _apply_log_level(debug: bool):
    """Apply log level change to root logger."""
    root_logger = logging.getLogger()
    new_level = logging.DEBUG if debug else logging.INFO

    root_logger.setLevel(new_level)
    for handler in root_logger.handlers:
        handler.setLevel(new_level)

    # Also adjust third-party loggers. httpx/httpcore stay pinned to WARNING
    # even in debug mode — at INFO/DEBUG they log full request URLs, which
    # leaks secrets embedded in webhook URLs (Discord, generic webhooks, etc.).
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("paho.mqtt").setLevel(logging.DEBUG if debug else logging.WARNING)

    logger.info("Log level changed to %s", "DEBUG" if debug else "INFO")


@router.get("/debug-logging", response_model=DebugLoggingState)
async def get_debug_logging_state(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """Get current debug logging state."""
    async with async_session() as db:
        enabled, enabled_at = await _get_debug_setting(db)

    duration = None
    if enabled and enabled_at:
        duration = int((datetime.now(tz=timezone.utc) - enabled_at).total_seconds())

    return DebugLoggingState(
        enabled=enabled,
        enabled_at=enabled_at.isoformat() if enabled_at else None,
        duration_seconds=duration,
    )


@router.post("/debug-logging", response_model=DebugLoggingState)
async def toggle_debug_logging(
    toggle: DebugLoggingToggle,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Enable or disable debug logging."""
    async with async_session() as db:
        enabled_at = await _set_debug_setting(db, toggle.enabled)

    _apply_log_level(toggle.enabled)

    duration = None
    if toggle.enabled and enabled_at:
        duration = int((datetime.now(tz=timezone.utc) - enabled_at).total_seconds())

    return DebugLoggingState(
        enabled=toggle.enabled,
        enabled_at=enabled_at.isoformat() if enabled_at else None,
        duration_seconds=duration,
    )


class LogEntry(BaseModel):
    """A single log entry."""

    timestamp: str
    level: str
    logger_name: str
    message: str


class LogsResponse(BaseModel):
    """Response containing log entries."""

    entries: list[LogEntry]
    total_in_file: int
    filtered_count: int


# Log line regex pattern: "2024-01-15 10:30:45,123 INFO [module.name] Message here"
LOG_LINE_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d{3})\s+(\w+)\s+\[([^\]]+)\]\s+(.*)$")


def _parse_log_line(line: str) -> LogEntry | None:
    """Parse a single log line into a LogEntry."""
    match = LOG_LINE_PATTERN.match(line.strip())
    if match:
        return LogEntry(
            timestamp=match.group(1),
            level=match.group(2),
            logger_name=match.group(3),
            message=match.group(4),
        )
    return None


def _read_log_entries(
    limit: int = 200,
    level_filter: str | None = None,
    search: str | None = None,
) -> tuple[list[LogEntry], int]:
    """Read and parse log entries from file with optional filtering."""
    log_file = settings.log_dir / "bambuddy.log"
    if not log_file.exists():
        return [], 0

    entries: list[LogEntry] = []
    total_lines = 0

    try:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            # Read all lines and process
            lines = f.readlines()
            total_lines = len(lines)

            # Parse lines in reverse order (newest first)
            current_entry: LogEntry | None = None
            multi_line_buffer: list[str] = []

            for line in reversed(lines):
                parsed = _parse_log_line(line)
                if parsed:
                    # Found a new log entry start
                    if current_entry:
                        # Apply filters and add previous entry (without multi_line_buffer - it belongs to new entry)
                        should_include = True

                        # Level filter
                        if level_filter and current_entry.level.upper() != level_filter.upper():
                            should_include = False

                        # Search filter (case-insensitive)
                        if search and should_include:
                            search_lower = search.lower()
                            if not (
                                search_lower in current_entry.message.lower()
                                or search_lower in current_entry.logger_name.lower()
                            ):
                                should_include = False

                        if should_include:
                            entries.append(current_entry)

                            if len(entries) >= limit:
                                break

                    # Set new entry and attach any accumulated multi-line content to it
                    # (in reverse order, continuation lines come before their parent entry)
                    current_entry = parsed
                    if multi_line_buffer:
                        current_entry.message += "\n" + "\n".join(reversed(multi_line_buffer))
                    multi_line_buffer = []
                elif line.strip():
                    # Continuation of multi-line log entry (will be attached to next parsed entry)
                    multi_line_buffer.append(line.rstrip())

            # Don't forget the last (oldest) entry
            # Note: any remaining multi_line_buffer would be orphaned lines before the first entry
            if current_entry and len(entries) < limit:
                should_include = True
                if level_filter and current_entry.level.upper() != level_filter.upper():
                    should_include = False
                if search and should_include:
                    search_lower = search.lower()
                    if not (
                        search_lower in current_entry.message.lower()
                        or search_lower in current_entry.logger_name.lower()
                    ):
                        should_include = False
                if should_include:
                    entries.append(current_entry)

    except Exception as e:
        logger.error("Error reading log file: %s", e)
        return [], 0

    # Entries are already in newest-first order
    return entries, total_lines


@router.get("/logs", response_model=LogsResponse)
async def get_logs(
    limit: int = Query(200, ge=1, le=1000, description="Maximum number of entries to return"),
    level: str | None = Query(None, description="Filter by log level (DEBUG, INFO, WARNING, ERROR)"),
    search: str | None = Query(None, description="Search in message or logger name"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """Get recent application log entries with optional filtering."""
    entries, total_lines = _read_log_entries(limit=limit, level_filter=level, search=search)

    return LogsResponse(
        entries=entries,
        total_in_file=total_lines,
        filtered_count=len(entries),
    )


@router.delete("/logs")
async def clear_logs(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Clear the application log file."""
    log_file = settings.log_dir / "bambuddy.log"

    if log_file.exists():
        try:
            # Truncate the file instead of deleting (keeps file handles valid)
            with open(log_file, "w", encoding="utf-8") as f:
                f.write("")
            logger.info("Log file cleared by user")
            return {"message": "Logs cleared successfully"}
        except Exception as e:
            logger.error("Error clearing log file: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to clear logs. Check server logs for details.")

    return {"message": "Log file does not exist"}


def _sanitize_path(path: str) -> str:
    """Remove username from paths for privacy."""

    # Replace /home/username/ or /Users/username/ with /home/[user]/
    path = re.sub(r"/home/[^/]+/", "/home/[user]/", path)
    path = re.sub(r"/Users/[^/]+/", "/Users/[user]/", path)
    # Replace /opt/username/ patterns
    path = re.sub(r"/opt/[^/]+/", "/opt/[user]/", path)
    return path


def _detect_docker_network_mode() -> str:
    """Detect Docker network mode by checking for host-level interfaces.

    In host mode the container shares the host network namespace, so Docker
    infrastructure interfaces (docker0, br-*, veth*) are visible.  In bridge
    mode the container is isolated and only sees its own veth (named eth0).
    """
    try:
        import socket

        for _idx, name in socket.if_nameindex():
            if name.startswith(("docker", "br-", "veth", "virbr")):
                return "host"
    except Exception:
        pass
    return "bridge"


def _mask_subnet(subnet: str) -> str:
    """Mask the first two octets of a subnet string. e.g. '192.168.1.0/24' -> 'x.x.1.0/24'."""
    try:
        parts = subnet.split(".")
        if len(parts) >= 4:
            parts[0] = "x"
            parts[1] = "x"
            return ".".join(parts)
    except Exception:
        pass
    return subnet


def _anonymize_mqtt_broker(broker: str) -> str:
    """Anonymize MQTT broker address. IPs become [IP], hostnames become *.domain."""
    if not broker:
        return ""
    try:
        ipaddress.ip_address(broker)
        return "[IP]"
    except ValueError:
        # It's a hostname — show *.domain pattern
        parts = broker.split(".")
        if len(parts) >= 2:
            return "*." + ".".join(parts[-2:])
        return broker


async def _check_port(ip: str, port: int, timeout: float = 2.0) -> bool:
    """Test TCP connectivity to ip:port. Returns True if reachable."""
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


def _get_container_memory_limit() -> int | None:
    """Read cgroup memory limit. Returns bytes or None."""
    # cgroup v2
    v2 = Path("/sys/fs/cgroup/memory.max")
    if v2.exists():
        try:
            val = v2.read_text().strip()
            if val != "max":
                return int(val)
        except Exception:
            pass
    # cgroup v1
    v1 = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    if v1.exists():
        try:
            val = int(v1.read_text().strip())
            # Values near page-aligned max (2^63-4096) mean unlimited
            if val < 2**62:
                return val
        except Exception:
            pass
    return None


def _format_bytes(size_bytes: int) -> str:
    """Format bytes into human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


async def _collect_auth_info(db: AsyncSession) -> dict:
    """Auth-related configuration that's stored OUTSIDE the settings table.

    The settings-table passthrough already captures `ldap_*`, `advanced_auth_enabled`,
    etc. The blocks below come from dedicated tables that the support bundle did
    not previously surface — every recent SSO / 2FA / group bug needed this data
    to triage.
    """
    from backend.app.models.api_key import APIKey
    from backend.app.models.group import Group
    from backend.app.models.long_lived_token import LongLivedToken
    from backend.app.models.oidc_provider import OIDCProvider, UserOIDCLink
    from backend.app.models.user_otp_code import UserOTPCode
    from backend.app.models.user_totp import UserTOTP

    now = datetime.now(timezone.utc)
    auth: dict = {}

    # OIDC providers — names are public (login-button labels), no secrets.
    providers_result = await db.execute(select(OIDCProvider).order_by(OIDCProvider.id))
    providers = providers_result.scalars().all()
    oidc_list = []
    for p in providers:
        # Count linked users per provider — separate query so failure on one
        # provider doesn't blank the whole list.
        try:
            link_count = (
                await db.execute(select(func.count(UserOIDCLink.id)).where(UserOIDCLink.provider_id == p.id))
            ).scalar() or 0
        except Exception:
            link_count = None
        oidc_list.append(
            {
                "name": p.name,
                "is_enabled": p.is_enabled,
                "scopes": p.scopes,
                "email_claim": p.email_claim,
                "require_email_verified": p.require_email_verified,
                "auto_create_users": p.auto_create_users,
                "auto_link_existing_accounts": p.auto_link_existing_accounts,
                "has_default_group": p.default_group_id is not None,
                # Derive from icon_content_type (non-deferred) rather than
                # icon_data (deferred BLOB) to avoid an async lazy-load.
                # Falls back to icon_url for pre-#1333 rows that have a URL
                # configured but no cached bytes yet.
                "has_icon": bool(p.icon_content_type) or bool(p.icon_url),
                "linked_user_count": link_count,
            }
        )
    auth["oidc_providers"] = oidc_list

    # 2FA enrollment — counts only, no per-user data.
    totp_enabled = (
        await db.execute(select(func.count(UserTOTP.id)).where(UserTOTP.is_enabled.is_(True)))
    ).scalar() or 0
    auth["users_with_totp"] = totp_enabled
    # Active (not-yet-expired, not-yet-used) email OTP codes — bounded count;
    # spikes here would point at someone hammering the email OTP flow.
    email_otp_pending = (
        await db.execute(
            select(func.count(UserOTPCode.id)).where(
                UserOTPCode.used.is_(False),
                UserOTPCode.expires_at > now,
            )
        )
    ).scalar() or 0
    auth["email_otp_codes_pending"] = email_otp_pending

    # API keys
    api_keys_total = (await db.execute(select(func.count(APIKey.id)))).scalar() or 0
    api_keys_enabled = (await db.execute(select(func.count(APIKey.id)).where(APIKey.enabled.is_(True)))).scalar() or 0
    api_keys_expired = (
        await db.execute(
            select(func.count(APIKey.id)).where(
                APIKey.expires_at.is_not(None),
                APIKey.expires_at < now,
            )
        )
    ).scalar() or 0
    auth["api_keys_total"] = api_keys_total
    auth["api_keys_enabled"] = api_keys_enabled
    auth["api_keys_expired"] = api_keys_expired

    # Long-lived tokens (camera-stream tokens used by kiosks etc.)
    llt_total = (await db.execute(select(func.count(LongLivedToken.id)))).scalar() or 0
    llt_active = (
        await db.execute(
            select(func.count(LongLivedToken.id)).where(
                LongLivedToken.revoked_at.is_(None),
                LongLivedToken.expires_at > now,
            )
        )
    ).scalar() or 0
    auth["long_lived_tokens_total"] = llt_total
    auth["long_lived_tokens_active"] = llt_active

    # Groups — system vs custom split matters for permission triage.
    groups_system = (await db.execute(select(func.count(Group.id)).where(Group.is_system.is_(True)))).scalar() or 0
    groups_custom = (await db.execute(select(func.count(Group.id)).where(Group.is_system.is_(False)))).scalar() or 0
    auth["groups_system"] = groups_system
    auth["groups_custom"] = groups_custom

    return auth


async def _collect_library_info(db: AsyncSession) -> dict:
    """Library file / folder totals, including external-link and trash counts."""
    from backend.app.models.external_link import ExternalLink
    from backend.app.models.library import LibraryFile, LibraryFolder

    info: dict = {}
    info["library_files_total"] = (
        await db.execute(select(func.count(LibraryFile.id)).where(LibraryFile.deleted_at.is_(None)))
    ).scalar() or 0
    info["library_files_in_trash"] = (
        await db.execute(select(func.count(LibraryFile.id)).where(LibraryFile.deleted_at.is_not(None)))
    ).scalar() or 0
    info["library_folders_total"] = (await db.execute(select(func.count(LibraryFolder.id)))).scalar() or 0
    info["external_folders_total"] = (
        await db.execute(select(func.count(LibraryFolder.id)).where(LibraryFolder.is_external.is_(True)))
    ).scalar() or 0
    info["external_links_total"] = (await db.execute(select(func.count(ExternalLink.id)))).scalar() or 0
    # MakerWorld imports — counted here because they're LibraryFile rows with
    # source_type='makerworld' (the import path doesn't have its own table).
    info["makerworld_imports_total"] = (
        await db.execute(
            select(func.count(LibraryFile.id)).where(
                LibraryFile.deleted_at.is_(None),
                LibraryFile.source_type == "makerworld",
            )
        )
    ).scalar() or 0
    return info


async def _collect_inventory_info(db: AsyncSession) -> dict:
    """Spool / k-profile totals from the inventory feature."""
    from backend.app.models.spool import Spool
    from backend.app.models.spool_k_profile import SpoolKProfile
    from backend.app.models.spoolman_k_profile import SpoolmanKProfile

    info: dict = {}
    info["spools_internal"] = (await db.execute(select(func.count(Spool.id)))).scalar() or 0
    info["k_profiles_internal"] = (await db.execute(select(func.count(SpoolKProfile.id)))).scalar() or 0
    info["k_profiles_spoolman"] = (await db.execute(select(func.count(SpoolmanKProfile.id)))).scalar() or 0
    return info


async def _collect_queue_info(db: AsyncSession) -> dict:
    """Print-queue health: pending count + oldest pending age."""
    from backend.app.models.print_queue import PrintQueueItem

    info: dict = {}
    info["pending_total"] = (
        await db.execute(select(func.count(PrintQueueItem.id)).where(PrintQueueItem.status == "pending"))
    ).scalar() or 0
    info["manual_start_pending"] = (
        await db.execute(
            select(func.count(PrintQueueItem.id)).where(
                PrintQueueItem.status == "pending",
                PrintQueueItem.manual_start.is_(True),
            )
        )
    ).scalar() or 0
    # Oldest pending item — derived from created_at to detect items stuck in queue
    # (target printer offline, missing filament match, etc.).
    oldest_row = (
        await db.execute(
            select(PrintQueueItem.created_at)
            .where(PrintQueueItem.status == "pending")
            .order_by(PrintQueueItem.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if oldest_row is not None:
        # created_at is naive in this codebase (server_default=func.now()); compare
        # against naive utc-now to get the actual age without TZ-conversion surprises.
        age = (datetime.now() - oldest_row).total_seconds()
        info["oldest_pending_age_seconds"] = int(age)
    else:
        info["oldest_pending_age_seconds"] = None
    return info


async def _collect_maintenance_info(db: AsyncSession) -> dict:
    """Maintenance schedule totals: enabled items count + last-serviced-never count."""
    from backend.app.models.maintenance import PrinterMaintenance

    info: dict = {}
    info["items_total"] = (await db.execute(select(func.count(PrinterMaintenance.id)))).scalar() or 0
    info["items_enabled"] = (
        await db.execute(select(func.count(PrinterMaintenance.id)).where(PrinterMaintenance.enabled.is_(True)))
    ).scalar() or 0
    return info


async def _collect_github_backup_info(db: AsyncSession) -> dict:
    """GitHub-backup configs: count per provider + recent-failure indicator."""
    from backend.app.models.github_backup import GitHubBackupConfig

    rows = (await db.execute(select(GitHubBackupConfig))).scalars().all()
    providers_used: dict[str, int] = {}
    last_failure_count = 0
    schedule_enabled_count = 0
    for cfg in rows:
        providers_used[cfg.provider] = providers_used.get(cfg.provider, 0) + 1
        if cfg.last_backup_status == "failed":
            last_failure_count += 1
        if cfg.schedule_enabled:
            schedule_enabled_count += 1
    return {
        "configs_total": len(rows),
        "providers_used": providers_used,
        "schedule_enabled_count": schedule_enabled_count,
        "last_failure_count": last_failure_count,
    }


async def _check_url_reachable(url: str, timeout: float = 2.0) -> bool | None:
    """Single HEAD/GET ping with a short timeout. Returns None if URL is empty."""
    if not url or not url.strip():
        return None
    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout, verify=False) as client:  # nosec B501 — local sidecars often use self-signed; this is a reachability/health probe only, no secrets are sent
            r = await client.get(url, follow_redirects=False)
            # Anything that returned a status code counts as reachable, even 404
            # (the API server is up, just the path was wrong) — separates network
            # failure from configuration mistakes for the user.
            return r.status_code is not None
    except Exception:
        return False


async def _fetch_slicer_health(url: str, timeout: float = 2.0) -> dict | None:
    """Fetch ``/health`` from a slicer sidecar and extract the CLI version.

    Returns ``None`` when ``url`` is empty (so the caller can distinguish
    "not configured" from "unreachable"). On any failure to fetch or parse,
    returns ``{"reachable": False, "version": None}``. The slicer-API wrapper
    labels both sidecars' CLI under ``checks.orcaslicer`` regardless of which
    slicer is actually bundled (cosmetic wrapper bug), so we read the version
    from whichever non-``dataPath`` child key exists rather than hardcoding
    one. This lets the bundle reviewer answer "is the user running the image
    they think they are?" without a separate curl round-trip.
    """
    if not url or not url.strip():
        return None
    health_url = url.rstrip("/") + "/health"
    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout, verify=False) as client:  # nosec B501 — local sidecars often use self-signed; this is a reachability/health probe only, no secrets are sent
            r = await client.get(health_url, follow_redirects=False)
            if r.status_code != 200:
                return {"reachable": True, "version": None}
            try:
                data = r.json()
            except Exception:
                return {"reachable": True, "version": None}
            checks = data.get("checks") if isinstance(data, dict) else None
            if not isinstance(checks, dict):
                return {"reachable": True, "version": None}
            for key, value in checks.items():
                if key == "dataPath":
                    continue
                if isinstance(value, dict) and "version" in value:
                    return {"reachable": True, "version": value.get("version")}
            return {"reachable": True, "version": None}
    except Exception:
        return {"reachable": False, "version": None}


async def _collect_slicer_api_info() -> dict:
    """Reachability check for configured slicer-API sidecars.

    Mirrors the URL-resolution precedence used by the real slicer routes
    (``archives.py:_slice_for_archive`` and ``library.py``) — DB setting first,
    falling back to ``app_settings.bambu_studio_api_url`` / ``slicer_api_url``
    which themselves respect the ``BAMBU_STUDIO_API_URL`` / ``SLICER_API_URL``
    env vars and default to ``http://localhost:3001`` / ``http://localhost:3003``.
    A bundle-time reachability check that only looked at the DB setting would
    return ``null`` for every user who runs the sidecar via env var or on the
    default port — i.e. most users.

    Also reads URLs directly from ``Settings.value`` rather than from
    ``info["settings"]``, which has already been redacted by the time the
    integrations block runs (``bambu_studio_api_url`` matches the ``url``
    keyword filter, so its value there is ``"[REDACTED]"`` and pinging that
    crashes httpx).
    """
    async with async_session() as db:
        keys_we_need = (
            "use_slicer_api",
            "preferred_slicer",
            "bambu_studio_api_url",
            "orcaslicer_api_url",
        )
        rows = (await db.execute(select(Settings).where(Settings.key.in_(keys_we_need)))).scalars().all()
        raw = {s.key: (s.value or "") for s in rows}

    # Resolve with the same DB-then-env-then-default precedence as the route
    # that the slicer-API client actually uses, so the bundle reflects what
    # the running app would resolve at request time.
    bs_db = raw.get("bambu_studio_api_url", "").strip()
    oc_db = raw.get("orcaslicer_api_url", "").strip()
    bs_url = bs_db or (settings.bambu_studio_api_url or "").strip()
    oc_url = oc_db or (settings.slicer_api_url or "").strip()

    info: dict = {
        "enabled": (raw.get("use_slicer_api", "false") or "false").lower() == "true",
        "preferred": raw.get("preferred_slicer", ""),
        # Layer accounting helps triage: was the URL set in the DB, or are
        # we falling through to the env-var / default? "Reachable but no
        # DB setting" is the env-var case.
        "bambu_studio_url_set_in_db": bool(bs_db),
        "orcaslicer_url_set_in_db": bool(oc_db),
        # Effective URL is the resolved one — kept as a host-portion-only
        # echo so we can confirm it's the expected sidecar without leaking
        # the full URL (which `url` keyword would have redacted anyway).
        "bambu_studio_url_source": ("db" if bs_db else ("env_or_default" if bs_url else "unset")),
        "orcaslicer_url_source": ("db" if oc_db else ("env_or_default" if oc_url else "unset")),
    }
    if info["enabled"]:
        bs_health, oc_health = await asyncio.gather(
            _fetch_slicer_health(bs_url),
            _fetch_slicer_health(oc_url),
        )
        info["bambu_studio_reachable"] = (bs_health or {}).get("reachable") if bs_health is not None else None
        info["bambu_studio_version"] = (bs_health or {}).get("version") if bs_health is not None else None
        info["orcaslicer_reachable"] = (oc_health or {}).get("reachable") if oc_health is not None else None
        info["orcaslicer_version"] = (oc_health or {}).get("version") if oc_health is not None else None
    return info


def _parse_obico_enabled_printers(raw: str) -> set[int]:
    """Parse the comma-separated `obico_enabled_printers` setting. Same shape as
    obico_detection.py uses but tolerant of legacy formats."""
    if not raw or not raw.strip():
        return set()
    result: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            result.add(int(token))
        except ValueError:
            continue
    return result


async def _collect_support_info() -> dict:
    """Collect all support information."""
    in_docker = is_running_in_docker()

    info = {
        "generated_at": datetime.now().isoformat(),
        "app": {
            "version": APP_VERSION,
            "debug_mode": settings.debug,
        },
        "system": {
            "platform": platform.system(),
            "platform_release": platform.release(),
            "platform_version": platform.version(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
        },
        "environment": {
            "docker": in_docker,
            "data_dir": _sanitize_path(str(settings.base_dir)),
            "log_dir": _sanitize_path(str(settings.log_dir)),
            "timezone": os.environ.get("TZ", ""),
        },
        "database": {},
        "printers": [],
        "settings": {},
    }

    # Docker-specific info
    if in_docker:
        try:
            mem_limit = _get_container_memory_limit()
            info["docker"] = {
                "container_memory_limit_bytes": mem_limit,
                "container_memory_limit_formatted": _format_bytes(mem_limit) if mem_limit else None,
                "network_mode_hint": _detect_docker_network_mode(),
            }
        except Exception:
            logger.debug("Failed to collect Docker info", exc_info=True)

    async with async_session() as db:
        # Database stats
        result = await db.execute(select(func.count(PrintArchive.id)))
        info["database"]["archives_total"] = result.scalar() or 0

        result = await db.execute(select(func.count(PrintArchive.id)).where(PrintArchive.status == "completed"))
        info["database"]["archives_completed"] = result.scalar() or 0

        result = await db.execute(select(func.count(Printer.id)))
        info["database"]["printers_total"] = result.scalar() or 0

        result = await db.execute(select(func.count(Filament.id)))
        info["database"]["filaments_total"] = result.scalar() or 0

        result = await db.execute(select(func.count(Project.id)))
        info["database"]["projects_total"] = result.scalar() or 0

        result = await db.execute(select(func.count(SmartPlug.id)))
        info["database"]["smart_plugs_total"] = result.scalar() or 0

        # Printer info (anonymized - no names, IPs, or serials)
        result = await db.execute(select(Printer))
        printers = result.scalars().all()
        statuses = printer_manager.get_all_statuses()

        # Pre-load the obico per-printer enabled-list. Settings are loaded later
        # in this function (and would overwrite this key in info["settings"]),
        # so do a targeted query here for the per-printer flag below.
        obico_enabled_set: set[int] = set()
        try:
            obico_row = (
                await db.execute(select(Settings).where(Settings.key == "obico_enabled_printers"))
            ).scalar_one_or_none()
            if obico_row is not None:
                obico_enabled_set = _parse_obico_enabled_printers(obico_row.value)
        except Exception:
            logger.debug("Failed to load obico_enabled_printers", exc_info=True)

        # Check reachability in parallel
        reachability_tasks = [_check_port(p.ip_address, 8883) for p in printers]
        reachable_results = await asyncio.gather(*reachability_tasks, return_exceptions=True)

        for i, printer in enumerate(printers):
            state = statuses.get(printer.id)
            reachable = reachable_results[i] if not isinstance(reachable_results[i], Exception) else False

            # Count AMS units and trays from raw_data
            ams_unit_count = 0
            ams_tray_count = 0
            has_vt_tray = False
            if state:
                ams_data = state.raw_data.get("ams")
                if isinstance(ams_data, list):
                    ams_units = ams_data
                elif isinstance(ams_data, dict) and "ams" in ams_data:
                    ams_units = ams_data["ams"] if isinstance(ams_data["ams"], list) else []
                else:
                    ams_units = []
                ams_unit_count = len(ams_units)
                for unit in ams_units:
                    trays = unit.get("tray", [])
                    ams_tray_count += len([t for t in trays if t.get("tray_type")])
                has_vt_tray = bool(state.raw_data.get("vt_tray"))

            info["printers"].append(
                {
                    "index": i + 1,
                    "model": printer.model or "Unknown",
                    "nozzle_count": printer.nozzle_count,
                    "is_active": printer.is_active,
                    "mqtt_connected": state.connected if state else False,
                    "state": state.state if state else "unknown",
                    "firmware_version": state.firmware_version if state else None,
                    "wifi_signal": state.wifi_signal if state else None,
                    "reachable": bool(reachable),
                    "ams_unit_count": ams_unit_count,
                    "ams_tray_count": ams_tray_count,
                    "has_vt_tray": has_vt_tray,
                    "external_camera_configured": bool(printer.external_camera_url),
                    "plate_detection_enabled": printer.plate_detection_enabled,
                    "obico_enabled": printer.id in obico_enabled_set,
                    "hms_error_count": len(state.hms_errors) if state else 0,
                    "developer_mode": state.developer_mode if state else None,
                    "nozzle_rack_count": len(state.nozzle_rack) if state else 0,
                }
            )

        # Virtual printers
        try:
            from backend.app.models.virtual_printer import VirtualPrinter
            from backend.app.services.virtual_printer import VIRTUAL_PRINTER_MODELS, virtual_printer_manager

            result = await db.execute(select(VirtualPrinter).order_by(VirtualPrinter.id))
            vps = result.scalars().all()
            info["virtual_printers"] = []
            for vp in vps:
                instance = virtual_printer_manager.get_instance(vp.id)
                status = instance.get_status() if instance else None
                model_code = vp.model or "C12"
                info["virtual_printers"].append(
                    {
                        "index": vp.id,
                        "enabled": vp.enabled,
                        "mode": vp.mode,
                        "model": model_code,
                        "model_name": VIRTUAL_PRINTER_MODELS.get(model_code, model_code),
                        "has_target_printer": vp.target_printer_id is not None,
                        "has_bind_ip": bool(vp.bind_ip),
                        "running": status.get("running", False) if status else False,
                        "pending_files": status.get("pending_files", 0) if status else 0,
                    }
                )
        except Exception:
            logger.debug("Failed to collect virtual printer info", exc_info=True)

        # All settings — sensitive values are redacted rather than dropped so
        # new settings automatically show up in support bundles without a code
        # change. The value is replaced with "[REDACTED]" but the key is kept
        # so we can still see which integrations are configured.
        result = await db.execute(select(Settings))
        all_settings = result.scalars().all()
        sensitive_keys = {
            "access_code",
            "password",
            "token",
            "secret",
            "api_key",
            "auth_key",  # Tailscale auth keys: virtual_printer_tailscale_auth_key
            "installation_id",
            "cloud_token",
            "mqtt_password",
            "email",
            "username",
            "vapid",
            "private_key",
            "public_key",
            "webhook",
            "url",
            "path",  # Filesystem paths may contain usernames
            "config",  # URLs may contain IPs, configs may have embedded secrets
            "_ip",  # IP address fields (e.g. virtual_printer_remote_interface_ip)
            "host",
            "broker",  # MQTT broker hostname / IP — network exposure
            "credential",
        }
        # Value-based safety net: redact anything whose value carries an
        # unambiguous secret prefix, even if the key name didn't match.
        # `tskey-` is the Tailscale auth-key prefix — future Tailscale settings
        # with unexpected names won't leak just because we forgot to add them.
        sensitive_value_prefixes = ("tskey-",)
        for s in all_settings:
            key_lower = s.key.lower()
            value = s.value or ""
            if any(sensitive in key_lower for sensitive in sensitive_keys) or any(
                value.startswith(prefix) for prefix in sensitive_value_prefixes
            ):
                # Preserve shape: mark presence without leaking the value
                info["settings"][s.key] = "[REDACTED]" if s.value else ""
            else:
                info["settings"][s.key] = s.value

        # Notification providers (anonymized — type/enabled/error status only)
        try:
            result = await db.execute(select(NotificationProvider))
            providers = result.scalars().all()
            info["integrations"] = info.get("integrations", {})
            info["integrations"]["notification_providers"] = [
                {
                    "type": p.provider_type,
                    "enabled": p.enabled,
                    "has_last_error": bool(p.last_error),
                }
                for p in providers
            ]
        except Exception:
            logger.debug("Failed to collect notification provider info", exc_info=True)

        # Database health
        try:
            from backend.app.core.db_dialect import is_sqlite

            if is_sqlite():
                result = await db.execute(text("PRAGMA journal_mode"))
                journal_mode = result.scalar()
                result = await db.execute(text("PRAGMA quick_check"))
                quick_check = result.scalar()

                db_path = settings.base_dir / "bambuddy.db"
                db_size = db_path.stat().st_size if db_path.exists() else 0
                wal_path = settings.base_dir / "bambuddy.db-wal"
                wal_size = wal_path.stat().st_size if wal_path.exists() else 0

                info["database_health"] = {
                    "backend": "sqlite",
                    "journal_mode": journal_mode,
                    "quick_check": quick_check,
                    "db_size_bytes": db_size,
                    "wal_size_bytes": wal_size,
                }
            else:
                result = await db.execute(text("SELECT version()"))
                pg_version = result.scalar()
                result = await db.execute(text("SELECT pg_database_size(current_database())"))
                db_size = result.scalar() or 0

                info["database_health"] = {
                    "backend": "postgresql",
                    "version": pg_version,
                    "db_size_bytes": db_size,
                }
        except Exception:
            logger.debug("Failed to collect database health info", exc_info=True)

    # Auth section — OIDC, 2FA, API keys, long-lived tokens, groups.
    # Stored in dedicated tables that the settings-table passthrough doesn't see.
    try:
        async with async_session() as auth_db:
            info["auth"] = await _collect_auth_info(auth_db)
    except Exception:
        logger.debug("Failed to collect auth info", exc_info=True)

    # Library + folder + makerworld import totals
    try:
        async with async_session() as lib_db:
            info["library"] = await _collect_library_info(lib_db)
    except Exception:
        logger.debug("Failed to collect library info", exc_info=True)

    # Spool / k-profile totals (inventory feature)
    try:
        async with async_session() as inv_db:
            info["inventory"] = await _collect_inventory_info(inv_db)
    except Exception:
        logger.debug("Failed to collect inventory info", exc_info=True)

    # Print queue health
    try:
        async with async_session() as q_db:
            info["queue"] = await _collect_queue_info(q_db)
    except Exception:
        logger.debug("Failed to collect queue info", exc_info=True)

    # Maintenance schedules
    try:
        async with async_session() as m_db:
            info["maintenance"] = await _collect_maintenance_info(m_db)
    except Exception:
        logger.debug("Failed to collect maintenance info", exc_info=True)

    # Integrations (lazy imports to avoid circular dependencies)
    info.setdefault("integrations", {})

    # Spoolman
    try:
        from backend.app.services.spoolman import get_spoolman_client

        client = await get_spoolman_client()
        if client:
            reachable = await client.health_check()
            info["integrations"]["spoolman"] = {"enabled": True, "reachable": reachable}
        else:
            info["integrations"]["spoolman"] = {"enabled": False, "reachable": False}
    except Exception:
        logger.debug("Failed to collect Spoolman info", exc_info=True)

    # MQTT relay
    try:
        from backend.app.services.mqtt_relay import mqtt_relay

        status = mqtt_relay.get_status()
        info["integrations"]["mqtt_relay"] = {
            "enabled": status.get("enabled", False),
            "connected": status.get("connected", False),
            "broker": _anonymize_mqtt_broker(status.get("broker", "")),
            "port": status.get("port", 0),
            "topic_prefix": status.get("topic_prefix", ""),
        }
    except Exception:
        logger.debug("Failed to collect MQTT relay info", exc_info=True)

    # SpoolBuddy devices (anonymized — no hostnames, IPs or device IDs)
    try:
        async with async_session() as db:
            from backend.app.models.spoolbuddy_device import SpoolBuddyDevice

            result = await db.execute(select(SpoolBuddyDevice))
            devices = result.scalars().all()
            info["integrations"]["spoolbuddy"] = {
                "device_count": len(devices),
                "online_count": sum(
                    1
                    for d in devices
                    if d.last_seen
                    and (datetime.now(tz=timezone.utc) - d.last_seen.replace(tzinfo=timezone.utc)).total_seconds() < 30
                ),
                "devices": [
                    {
                        "index": i + 1,
                        "firmware_version": d.firmware_version,
                        "has_nfc": d.has_nfc,
                        "has_scale": d.has_scale,
                        "nfc_reader_type": d.nfc_reader_type,
                        "nfc_connection": d.nfc_connection,
                        "has_backlight": d.has_backlight,
                        "nfc_ok": d.nfc_ok,
                        "scale_ok": d.scale_ok,
                        "uptime_s": d.uptime_s,
                        "calibration_factor": d.calibration_factor,
                        "tare_offset": d.tare_offset,
                        "last_calibrated_at": d.last_calibrated_at.isoformat() if d.last_calibrated_at else None,
                        "update_status": d.update_status,
                    }
                    for i, d in enumerate(devices)
                ],
            }
    except Exception:
        logger.debug("Failed to collect SpoolBuddy info", exc_info=True)

    # Home Assistant (check ha_enabled setting)
    try:
        info["integrations"]["homeassistant"] = {
            "enabled": info["settings"].get("ha_enabled", "false").lower() == "true",
        }
    except Exception:
        logger.debug("Failed to collect Home Assistant info", exc_info=True)

    # GitHub backup — providers + recent-failure counts from github_backup_config.
    try:
        async with async_session() as gb_db:
            info["integrations"]["github_backup"] = await _collect_github_backup_info(gb_db)
    except Exception:
        logger.debug("Failed to collect GitHub backup info", exc_info=True)

    # Slicer-API sidecar reachability (#X1C-investigation-style triage)
    try:
        info["integrations"]["slicer_api"] = await _collect_slicer_api_info()
    except Exception:
        logger.debug("Failed to collect slicer-API info", exc_info=True)

    # Dependencies
    try:
        dep_packages = [
            "fastapi",
            "uvicorn",
            "pydantic",
            "sqlalchemy",
            "paho-mqtt",
            "psutil",
            "httpx",
            "aiofiles",
            "cryptography",
            "opencv-python-headless",
            "numpy",
        ]
        info["dependencies"] = {}
        for pkg in dep_packages:
            try:
                info["dependencies"][pkg] = importlib.metadata.version(pkg)
            except importlib.metadata.PackageNotFoundError:
                info["dependencies"][pkg] = None
    except Exception:
        logger.debug("Failed to collect dependency info", exc_info=True)

    # Log file info
    try:
        log_file = settings.log_dir / "bambuddy.log"
        if log_file.exists():
            size = log_file.stat().st_size
            info["log_file"] = {
                "size_bytes": size,
                "size_formatted": _format_bytes(size),
            }
        else:
            info["log_file"] = {"size_bytes": 0, "size_formatted": "0 B"}
    except Exception:
        logger.debug("Failed to collect log file info", exc_info=True)

    # Network interfaces (subnets with first two octets masked)
    try:
        interfaces = get_network_interfaces()
        info["network"] = {
            "interface_count": len(interfaces),
            "interfaces": [{"name": iface["name"], "subnet": _mask_subnet(iface["subnet"])} for iface in interfaces],
        }
    except Exception:
        logger.debug("Failed to collect network info", exc_info=True)

    # WebSocket connections
    try:
        info["websockets"] = {
            "active_connections": len(ws_manager.active_connections),
        }
    except Exception:
        logger.debug("Failed to collect WebSocket info", exc_info=True)

    return info


def _sanitize_log_content(content: str, sensitive_strings: dict[str, str] | None = None) -> str:
    """Remove sensitive data from log content."""
    # First, replace known sensitive values (database-aware exact matching)
    # This catches printer names, usernames, and other arbitrary user-chosen strings
    # that regex patterns cannot detect
    if sensitive_strings:
        # Sort by length descending to avoid partial matches (e.g. "My Printer 1" before "My Printer")
        for value, label in sorted(sensitive_strings.items(), key=lambda x: len(x[0]), reverse=True):
            if len(value) < 3:
                continue  # Skip very short strings to prevent over-redaction
            content = re.sub(re.escape(value), label, content)

    # Replace credentials in URLs (e.g. http://user:pass@host, rtsps://bblp:code@host)
    content = re.sub(r"((?:https?|rtsps?)://)[^/:@\s]+:[^/@\s]+@", r"\1[CREDENTIALS]@", content)

    # Replace email addresses
    content = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[EMAIL]", content)

    # Replace Bambu Lab printer serial numbers (format: 00M/01D/01S/01P/03W + alphanumeric, 12-16 chars total)
    content = re.sub(r"\b0[0-3][A-Z0-9][A-Z0-9]{9,13}\b", "[SERIAL]", content, flags=re.IGNORECASE)

    # Replace IPv4 addresses (skip firmware versions like 01.09.01.00 which have leading zeros)
    content = re.sub(
        r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]\d|\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]\d|\d)\b",
        "[IP]",
        content,
    )

    # Replace paths with usernames
    content = re.sub(r"/home/[^/\s]+/", "/home/[user]/", content)
    content = re.sub(r"/Users/[^/\s]+/", "/Users/[user]/", content)
    content = re.sub(r"/opt/[^/\s]+/", "/opt/[user]/", content)

    return content


def _get_log_content(max_bytes: int = 10 * 1024 * 1024, sensitive_strings: dict[str, str] | None = None) -> bytes:
    """Get log file content, limited to max_bytes from the end."""
    log_file = settings.log_dir / "bambuddy.log"
    if not log_file.exists():
        return b"Log file not found"

    file_size = log_file.stat().st_size
    if file_size <= max_bytes:
        content = log_file.read_text(encoding="utf-8", errors="replace")
    else:
        # Read last max_bytes
        with open(log_file, "rb") as f:
            f.seek(file_size - max_bytes)
            # Skip partial line at start
            f.readline()
            content = f.read().decode("utf-8", errors="replace")

    # Sanitize sensitive data
    content = _sanitize_log_content(content, sensitive_strings)
    return content.encode("utf-8")


async def _get_recent_sanitized_logs(max_lines: int = 200) -> str:
    """Get recent log lines, sanitized for inclusion in bug reports."""
    # Collect sensitive strings from DB for redaction
    sensitive_strings: dict[str, str] = {}
    async with async_session() as db:
        result = await db.execute(select(Printer.name, Printer.serial_number, Printer.ip_address, Printer.access_code))
        for name, serial, ip_address, access_code in result.all():
            if name:
                sensitive_strings[name] = "[PRINTER]"
            if serial:
                sensitive_strings[serial] = "[SERIAL]"
            if ip_address:
                sensitive_strings[ip_address] = "[IP]"
            if access_code:
                sensitive_strings[access_code] = "[ACCESS_CODE]"

        result = await db.execute(select(User.username))
        for (username,) in result.all():
            if username:
                sensitive_strings[username] = "[USER]"

        result = await db.execute(select(Settings.value).where(Settings.key == "bambu_cloud_email"))
        cloud_email = result.scalar_one_or_none()
        if cloud_email:
            sensitive_strings[cloud_email] = "[EMAIL]"

    log_file = settings.log_dir / "bambuddy.log"
    if not log_file.exists():
        return ""

    # Read last portion of log file
    try:
        content = log_file.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        recent = "\n".join(lines[-max_lines:])
        return _sanitize_log_content(recent, sensitive_strings)
    except Exception:
        logger.debug("Failed to read logs for bug report", exc_info=True)
        return ""


@router.get("/bundle")
async def generate_support_bundle(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """Generate a support bundle ZIP file for issue reporting."""
    # Check if debug logging is enabled and collect sensitive values for redaction
    async with async_session() as db:
        enabled, _enabled_at = await _get_debug_setting(db)

        if not enabled:
            raise HTTPException(
                status_code=400,
                detail="Debug logging must be enabled before generating a support bundle. "
                "Please enable debug logging, reproduce the issue, then generate the bundle.",
            )

        # Collect known sensitive values for log redaction
        sensitive_strings: dict[str, str] = {}

        # Printer names, serial numbers, IP addresses, and access codes
        result = await db.execute(select(Printer.name, Printer.serial_number, Printer.ip_address, Printer.access_code))
        for name, serial, ip_address, access_code in result.all():
            if name:
                sensitive_strings[name] = "[PRINTER]"
            if serial:
                sensitive_strings[serial] = "[SERIAL]"
            if ip_address:
                sensitive_strings[ip_address] = "[IP]"
            if access_code:
                sensitive_strings[access_code] = "[ACCESS_CODE]"

        # Auth usernames
        result = await db.execute(select(User.username))
        for (username,) in result.all():
            if username:
                sensitive_strings[username] = "[USER]"

        # Bambu Cloud email
        result = await db.execute(select(Settings.value).where(Settings.key == "bambu_cloud_email"))
        cloud_email = result.scalar_one_or_none()
        if cloud_email:
            sensitive_strings[cloud_email] = "[EMAIL]"

    # Collect support info
    support_info = await _collect_support_info()

    # Create ZIP in memory
    zip_buffer = io.BytesIO()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add support info JSON
        zf.writestr("support-info.json", json.dumps(support_info, indent=2, default=str))

        # Add log file
        log_content = _get_log_content(sensitive_strings=sensitive_strings)
        zf.writestr("bambuddy.log", log_content)

    zip_buffer.seek(0)

    filename = f"bambuddy-support-{timestamp}.zip"
    logger.info("Generated support bundle: %s", filename)

    return StreamingResponse(
        zip_buffer, media_type="application/zip", headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


async def init_debug_logging():
    """Initialize debug logging state from database on startup."""
    try:
        async with async_session() as db:
            enabled, _ = await _get_debug_setting(db)

            if enabled:
                _apply_log_level(True)
                logger.info("Debug logging restored from previous session")
    except Exception as e:
        logger.warning("Could not restore debug logging state: %s", e)
