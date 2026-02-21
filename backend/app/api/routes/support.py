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
from datetime import datetime
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
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        logging.getLogger("aiosqlite").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.DEBUG)
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        logging.getLogger("paho.mqtt").setLevel(logging.DEBUG)
    else:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("paho.mqtt").setLevel(logging.WARNING)

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
    async with async_session() as db:
        enabled_at = await _set_debug_setting(db, toggle.enabled)

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
            interfaces = get_network_interfaces()
            info["docker"] = {
                "container_memory_limit_bytes": mem_limit,
                "container_memory_limit_formatted": _format_bytes(mem_limit) if mem_limit else None,
                "network_mode_hint": "host" if len(interfaces) > 2 else "bridge",
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
                    "hms_error_count": len(state.hms_errors) if state else 0,
                    "developer_mode": state.developer_mode if state else None,
                    "nozzle_rack_count": len(state.nozzle_rack) if state else 0,
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
            "username",
            "vapid",
            "private_key",
            "public_key",
            "webhook",
            "url",
            "path",  # Filesystem paths may contain usernames
            "config",  # URLs may contain IPs, configs may have embedded secrets
        }
        for s in all_settings:
            # Skip sensitive settings
            if any(sensitive in s.key.lower() for sensitive in sensitive_keys):
                continue
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
            result = await db.execute(text("PRAGMA journal_mode"))
            journal_mode = result.scalar()
            result = await db.execute(text("PRAGMA quick_check"))
            quick_check = result.scalar()

            db_path = settings.base_dir / "bambuddy.db"
            db_size = db_path.stat().st_size if db_path.exists() else 0
            wal_path = settings.base_dir / "bambuddy.db-wal"
            wal_size = wal_path.stat().st_size if wal_path.exists() else 0

            info["database_health"] = {
                "journal_mode": journal_mode,
                "quick_check": quick_check,
                "db_size_bytes": db_size,
                "wal_size_bytes": wal_size,
            }
        except Exception:
            logger.debug("Failed to collect database health info", exc_info=True)

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

    # Home Assistant (check ha_enabled setting)
    try:
        info["integrations"]["homeassistant"] = {
            "enabled": info["settings"].get("ha_enabled", "false").lower() == "true",
        }
    except Exception:
        logger.debug("Failed to collect Home Assistant info", exc_info=True)

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

    # Network interfaces (subnets only — already anonymized)
    try:
        interfaces = get_network_interfaces()
        info["network"] = {
            "interface_count": len(interfaces),
            "interfaces": [{"name": iface["name"], "subnet": iface["subnet"]} for iface in interfaces],
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

    # Replace credentials in URLs (e.g. http://user:pass@host)
    content = re.sub(r"(https?://)[^/:@\s]+:[^/@\s]+@", r"\1[CREDENTIALS]@", content)

    # Replace email addresses
    content = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[EMAIL]", content)

    # Replace Bambu Lab printer serial numbers (format: 00M/01D/01S/01P/03W + alphanumeric, 12-16 chars total)
    content = re.sub(r"\b0[0-3][A-Z0-9][A-Z0-9]{9,13}\b", "[SERIAL]", content, flags=re.IGNORECASE)

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

        # Printer names and serial numbers
        result = await db.execute(select(Printer.name, Printer.serial_number))
        for name, serial in result.all():
            if name:
                sensitive_strings[name] = "[PRINTER]"
            if serial:
                sensitive_strings[serial] = "[SERIAL]"

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
