"""Support endpoints for debug logging and support bundle generation."""

import io
import json
import logging
import os
import platform
import re
import zipfile
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.config import APP_VERSION, settings
from backend.app.core.database import async_session
from backend.app.core.permissions import Permission
from backend.app.models.archive import PrintArchive
from backend.app.models.filament import Filament
from backend.app.models.printer import Printer
from backend.app.models.project import Project
from backend.app.models.settings import Settings
from backend.app.models.smart_plug import SmartPlug
from backend.app.models.user import User

router = APIRouter(prefix="/support", tags=["support"])
logger = logging.getLogger(__name__)

# In-memory state for debug logging (persisted to settings DB)
_debug_logging_enabled = False
_debug_logging_enabled_at: datetime | None = None


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
        except ValueError:
            pass

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
    enabled_at = datetime.now() if enabled else None
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

    # Also adjust third-party loggers
    if debug:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)
        logging.getLogger("httpcore").setLevel(logging.DEBUG)
        logging.getLogger("httpx").setLevel(logging.DEBUG)
    else:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)

    logger.info(f"Log level changed to {'DEBUG' if debug else 'INFO'}")


@router.get("/debug-logging", response_model=DebugLoggingState)
async def get_debug_logging_state(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """Get current debug logging state."""
    global _debug_logging_enabled, _debug_logging_enabled_at

    async with async_session() as db:
        enabled, enabled_at = await _get_debug_setting(db)
        _debug_logging_enabled = enabled
        _debug_logging_enabled_at = enabled_at

    duration = None
    if enabled and enabled_at:
        duration = int((datetime.now() - enabled_at).total_seconds())

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
    global _debug_logging_enabled, _debug_logging_enabled_at

    async with async_session() as db:
        enabled_at = await _set_debug_setting(db, toggle.enabled)
        _debug_logging_enabled = toggle.enabled
        _debug_logging_enabled_at = enabled_at

    _apply_log_level(toggle.enabled)

    duration = None
    if toggle.enabled and enabled_at:
        duration = int((datetime.now() - enabled_at).total_seconds())

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
        logger.error(f"Error reading log file: {e}")
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
            logger.error(f"Error clearing log file: {e}", exc_info=True)
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


async def _collect_support_info() -> dict:
    """Collect all support information."""
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
            "docker": os.path.exists("/.dockerenv"),
            "data_dir": _sanitize_path(str(settings.base_dir)),
            "log_dir": _sanitize_path(str(settings.log_dir)),
        },
        "database": {},
        "printers": [],
        "settings": {},
    }

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

        # Printer info (anonymized - just models and connection status)
        result = await db.execute(select(Printer))
        printers = result.scalars().all()
        for i, printer in enumerate(printers):
            info["printers"].append(
                {
                    "index": i + 1,
                    "model": printer.model or "Unknown",
                    "nozzle_count": printer.nozzle_count,
                }
            )

        # Non-sensitive settings
        result = await db.execute(select(Settings))
        all_settings = result.scalars().all()
        sensitive_keys = {
            "access_code",
            "password",
            "token",
            "secret",
            "api_key",
            "installation_id",
            "cloud_token",
            "mqtt_password",
            "email",
            "vapid",
            "private_key",
            "public_key",
            "webhook",
            "url",
            "config",  # URLs may contain IPs, configs may have embedded secrets
        }
        for s in all_settings:
            # Skip sensitive settings
            if any(sensitive in s.key.lower() for sensitive in sensitive_keys):
                continue
            info["settings"][s.key] = s.value

    return info


def _sanitize_log_content(content: str) -> str:
    """Remove sensitive data from log content."""
    import re

    # Replace IP addresses with [IP]
    content = re.sub(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "[IP]", content)

    # Replace email addresses
    content = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[EMAIL]", content)

    # Replace Bambu Lab printer serial numbers (format: 00M/01D/01S/01P/03W + alphanumeric, 12-16 chars total)
    # These appear in logs as [SERIAL] or in messages
    content = re.sub(r"\b(0[0-3][A-Z0-9])[A-Z0-9]{9,13}\b", r"\1[SERIAL]", content)

    # Replace paths with usernames
    content = re.sub(r"/home/[^/\s]+/", "/home/[user]/", content)
    content = re.sub(r"/Users/[^/\s]+/", "/Users/[user]/", content)
    content = re.sub(r"/opt/[^/\s]+/", "/opt/[user]/", content)

    return content


def _get_log_content(max_bytes: int = 10 * 1024 * 1024) -> bytes:
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
    content = _sanitize_log_content(content)
    return content.encode("utf-8")


@router.get("/bundle")
async def generate_support_bundle(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """Generate a support bundle ZIP file for issue reporting."""
    global _debug_logging_enabled, _debug_logging_enabled_at

    # Check if debug logging is enabled
    async with async_session() as db:
        enabled, enabled_at = await _get_debug_setting(db)
        _debug_logging_enabled = enabled
        _debug_logging_enabled_at = enabled_at

    if not enabled:
        raise HTTPException(
            status_code=400,
            detail="Debug logging must be enabled before generating a support bundle. "
            "Please enable debug logging, reproduce the issue, then generate the bundle.",
        )

    # Collect support info
    support_info = await _collect_support_info()

    # Create ZIP in memory
    zip_buffer = io.BytesIO()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add support info JSON
        zf.writestr("support-info.json", json.dumps(support_info, indent=2, default=str))

        # Add log file
        log_content = _get_log_content()
        zf.writestr("bambuddy.log", log_content)

    zip_buffer.seek(0)

    filename = f"bambuddy-support-{timestamp}.zip"
    logger.info(f"Generated support bundle: {filename}")

    return StreamingResponse(
        zip_buffer, media_type="application/zip", headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


async def init_debug_logging():
    """Initialize debug logging state from database on startup."""
    global _debug_logging_enabled, _debug_logging_enabled_at

    try:
        async with async_session() as db:
            enabled, enabled_at = await _get_debug_setting(db)
            _debug_logging_enabled = enabled
            _debug_logging_enabled_at = enabled_at

            if enabled:
                _apply_log_level(True)
                logger.info("Debug logging restored from previous session")
    except Exception as e:
        logger.warning(f"Could not restore debug logging state: {e}")
