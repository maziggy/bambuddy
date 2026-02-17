"""System information API routes."""

import asyncio
import os
import platform
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import psutil
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.config import APP_VERSION, settings
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.archive import PrintArchive
from backend.app.models.filament import Filament
from backend.app.models.printer import Printer
from backend.app.models.project import Project
from backend.app.models.smart_plug import SmartPlug
from backend.app.models.user import User
from backend.app.services.printer_manager import printer_manager

router = APIRouter(prefix="/system", tags=["system"])

STORAGE_USAGE_CACHE_SECONDS = 300
_storage_usage_cache: dict | None = None
_storage_usage_cache_ts: float | None = None
_storage_usage_lock = asyncio.Lock()


def get_directory_size(path: Path) -> int:
    """Calculate total size of a directory in bytes."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                total += entry.stat().st_size
    except (PermissionError, OSError):
        pass  # Return partial total if directory traversal is interrupted
    return total


def format_bytes(bytes_value: int) -> str:
    """Format bytes to human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_value < 1024:
            return f"{bytes_value:.1f} {unit}"
        bytes_value /= 1024
    return f"{bytes_value:.1f} PB"


def format_uptime(seconds: float) -> str:
    """Format uptime in seconds to human-readable string."""
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")

    return " ".join(parts) if parts else "< 1m"


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _get_database_paths() -> list[Path]:
    candidates = [settings.base_dir / "bambuddy.db", settings.base_dir / "bambutrack.db"]
    return [path for path in candidates if path.exists()]


def _get_database_items() -> list[dict]:
    items: list[dict] = []
    for path in _get_database_paths():
        try:
            size = path.stat().st_size
        except OSError:
            continue
        items.append(
            {
                "name": path.name,
                "path": str(path),
                "bytes": size,
                "formatted": format_bytes(size),
            }
        )
    items.sort(key=lambda item: item["bytes"], reverse=True)
    return items


def _get_app_dir() -> Path:
    return settings.static_dir.parent


def _get_data_dirs() -> list[Path]:
    return [
        settings.archive_dir,
        settings.log_dir,
        settings.plate_calibration_dir,
        settings.base_dir / "virtual_printer",
        settings.base_dir / "firmware",
    ]


def _is_system_path(path: Path) -> bool:
    app_dir = _get_app_dir()
    if not _is_under(path, app_dir):
        return False
    return all(not _is_under(path, data_dir) for data_dir in _get_data_dirs())


def _get_storage_rules() -> list[tuple[str, str, Callable]]:
    base_dir = settings.base_dir
    archive_dir = settings.archive_dir
    library_dir = archive_dir / "library"
    virtual_printer_dir = base_dir / "virtual_printer"
    upload_dir = virtual_printer_dir / "uploads"

    db_paths = set(_get_database_paths())

    return [
        (
            "database",
            "Database",
            lambda path: path in db_paths,
        ),
        (
            "library_thumbnails",
            "Library Thumbnails",
            lambda path: _is_under(path, library_dir / "thumbnails"),
        ),
        (
            "library_files",
            "Library Files",
            lambda path: _is_under(path, library_dir / "files"),
        ),
        (
            "library_other",
            "Library Other",
            lambda path: _is_under(path, library_dir),
        ),
        (
            "archive_timelapses",
            "Timelapses",
            lambda path: _is_under(path, archive_dir) and "timelapse" in path.name.lower(),
        ),
        (
            "archive_thumbnails",
            "Thumbnails",
            lambda path: _is_under(path, archive_dir) and path.name.lower().startswith("thumbnail"),
        ),
        (
            "archive_files",
            "Archives",
            lambda path: _is_under(path, archive_dir),
        ),
        (
            "virtual_printer_upload_cache",
            "Virtual Printer Upload Cache",
            lambda path: _is_under(path, upload_dir / "cache"),
        ),
        (
            "virtual_printer_uploads",
            "Virtual Printer Uploads",
            lambda path: _is_under(path, upload_dir),
        ),
        (
            "virtual_printer_certs",
            "Virtual Printer Certs",
            lambda path: _is_under(path, virtual_printer_dir / "certs"),
        ),
        (
            "virtual_printer_other",
            "Virtual Printer Other",
            lambda path: _is_under(path, virtual_printer_dir),
        ),
        (
            "downloads",
            "Downloads",
            lambda path: _is_under(path, base_dir / "firmware"),
        ),
        (
            "plate_calibration",
            "Plate Calibration",
            lambda path: _is_under(path, settings.plate_calibration_dir),
        ),
        (
            "logs",
            "Logs",
            lambda path: _is_under(path, settings.log_dir),
        ),
    ]


def _classify_file(path: Path, rules: list[tuple[str, str, Callable]]) -> tuple[str, str]:
    for key, label, matcher in rules:
        try:
            if matcher(path):
                return key, label
        except OSError:
            continue
    return "other_data", "Other"


def _format_percentage(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((part / total) * 100, 2)


def _get_other_bucket(path: Path, base_dir: Path) -> str:
    try:
        relative = path.resolve().relative_to(base_dir.resolve())
    except ValueError:
        return path.parent.name or path.name

    parts = relative.parts
    return parts[0] if parts else path.name


def _walk_files(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    stack = [root for root in roots if root.exists()]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            files.append(Path(entry.path))
                    except OSError:
                        continue
        except OSError:
            continue
    return files


def _scan_storage_usage() -> dict:
    base_dir = settings.base_dir
    rules = _get_storage_rules()

    roots = _get_data_dirs()

    seen_roots = set()
    unique_roots = []
    for root in roots:
        resolved = root.resolve()
        if resolved not in seen_roots:
            seen_roots.add(resolved)
            unique_roots.append(root)

    total_bytes = 0
    error_count = 0
    category_sizes: dict[str, dict] = {}
    other_breakdown: dict[tuple[str, str], int] = {}
    database_items = _get_database_items()

    files = _walk_files(unique_roots)
    for file_path in files:
        try:
            size = file_path.stat().st_size
        except OSError:
            error_count += 1
            continue

        total_bytes += size

        key, label = _classify_file(file_path, rules)
        if key not in category_sizes:
            category_sizes[key] = {"key": key, "label": label, "bytes": 0}
        category_sizes[key]["bytes"] += size

        if key == "other_data":
            bucket = _get_other_bucket(file_path, base_dir)
            kind = "system" if _is_system_path(file_path) else "data"
            other_breakdown[(bucket, kind)] = other_breakdown.get((bucket, kind), 0) + size

    for item in database_items:
        total_bytes += item["bytes"]
        key = "database"
        label = "Database"
        if key not in category_sizes:
            category_sizes[key] = {"key": key, "label": label, "bytes": 0}
        category_sizes[key]["bytes"] += item["bytes"]

    categories = []
    for item in category_sizes.values():
        bytes_value = item["bytes"]
        categories.append(
            {
                "key": item["key"],
                "label": item["label"],
                "bytes": bytes_value,
                "formatted": format_bytes(bytes_value),
                "percent_of_total": _format_percentage(bytes_value, total_bytes),
            }
        )

    categories.sort(key=lambda entry: entry["bytes"], reverse=True)

    other_items = []
    for (bucket, kind), size in other_breakdown.items():
        other_items.append(
            {
                "bucket": bucket,
                "label": bucket,
                "kind": kind,
                "deletable": kind != "system",
                "bytes": size,
                "formatted": format_bytes(size),
                "percent_of_total": _format_percentage(size, total_bytes),
            }
        )

    other_items.sort(key=lambda entry: entry["bytes"], reverse=True)

    return {
        "roots": [str(root) for root in unique_roots],
        "total_bytes": total_bytes,
        "total_formatted": format_bytes(total_bytes),
        "categories": categories,
        "other_breakdown": other_items,
        "scan_errors": error_count,
    }


async def _get_storage_usage_cached(refresh: bool, max_age_seconds: int) -> dict:
    global _storage_usage_cache
    global _storage_usage_cache_ts

    now = time.time()
    if not refresh and _storage_usage_cache and _storage_usage_cache_ts is not None:
        age = now - _storage_usage_cache_ts
        if age < max_age_seconds:
            return {
                **_storage_usage_cache,
                "cache": {
                    "hit": True,
                    "age_seconds": round(age, 2),
                    "max_age_seconds": max_age_seconds,
                },
            }

    async with _storage_usage_lock:
        now = time.time()
        if not refresh and _storage_usage_cache and _storage_usage_cache_ts is not None:
            age = now - _storage_usage_cache_ts
            if age < max_age_seconds:
                return {
                    **_storage_usage_cache,
                    "cache": {
                        "hit": True,
                        "age_seconds": round(age, 2),
                        "max_age_seconds": max_age_seconds,
                    },
                }

        snapshot = await asyncio.to_thread(_scan_storage_usage)
        _storage_usage_cache = {
            **snapshot,
            "generated_at": datetime.now().isoformat(),
        }
        _storage_usage_cache_ts = time.time()
        return {
            **_storage_usage_cache,
            "cache": {
                "hit": False,
                "age_seconds": 0,
                "max_age_seconds": max_age_seconds,
            },
        }


@router.get("/info")
async def get_system_info(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SYSTEM_READ),
):
    """Get comprehensive system information."""

    # Database stats
    archive_count = await db.scalar(select(func.count(PrintArchive.id)))
    printer_count = await db.scalar(select(func.count(Printer.id)))
    filament_count = await db.scalar(select(func.count(Filament.id)))
    project_count = await db.scalar(select(func.count(Project.id)))
    smart_plug_count = await db.scalar(select(func.count(SmartPlug.id)))

    # Archive stats by status
    completed_count = await db.scalar(select(func.count(PrintArchive.id)).where(PrintArchive.status == "completed"))
    failed_count = await db.scalar(select(func.count(PrintArchive.id)).where(PrintArchive.status == "failed"))
    printing_count = await db.scalar(select(func.count(PrintArchive.id)).where(PrintArchive.status == "printing"))

    # Total print time
    total_print_time = (
        await db.scalar(
            select(func.sum(PrintArchive.print_time_seconds)).where(PrintArchive.print_time_seconds.isnot(None))
        )
        or 0
    )

    # Total filament used
    total_filament = (
        await db.scalar(
            select(func.sum(PrintArchive.filament_used_grams)).where(PrintArchive.filament_used_grams.isnot(None))
        )
        or 0
    )

    # Connected printers
    connected_printers = []
    for printer_id, client in printer_manager._clients.items():
        state = client.state
        if state and state.connected:
            # Get printer name and model from database
            result = await db.execute(select(Printer.name, Printer.model).where(Printer.id == printer_id))
            row = result.first()
            name = row[0] if row else f"Printer {printer_id}"
            model = row[1] if row else "unknown"
            connected_printers.append(
                {
                    "id": printer_id,
                    "name": name,
                    "state": state.state,
                    "model": model,
                }
            )

    # Storage info
    archive_dir = settings.archive_dir
    archive_size = get_directory_size(archive_dir) if archive_dir.exists() else 0

    # Database file size
    db_path = settings.base_dir / "bambuddy.db"
    db_size = db_path.stat().st_size if db_path.exists() else 0

    # Disk usage
    disk = psutil.disk_usage(str(settings.base_dir))

    # System info
    memory = psutil.virtual_memory()
    boot_time = datetime.fromtimestamp(psutil.boot_time())
    uptime_seconds = (datetime.now() - boot_time).total_seconds()

    # Python and system info
    import sys

    return {
        "app": {
            "version": APP_VERSION,
            "base_dir": str(settings.base_dir),
            "archive_dir": str(archive_dir),
        },
        "database": {
            "archives": archive_count,
            "archives_completed": completed_count,
            "archives_failed": failed_count,
            "archives_printing": printing_count,
            "printers": printer_count,
            "filaments": filament_count,
            "projects": project_count,
            "smart_plugs": smart_plug_count,
            "total_print_time_seconds": total_print_time,
            "total_print_time_formatted": format_uptime(total_print_time),
            "total_filament_grams": round(total_filament, 1),
            "total_filament_kg": round(total_filament / 1000, 2),
        },
        "printers": {
            "total": printer_count,
            "connected": len(connected_printers),
            "connected_list": connected_printers,
        },
        "storage": {
            "archive_size_bytes": archive_size,
            "archive_size_formatted": format_bytes(archive_size),
            "database_size_bytes": db_size,
            "database_size_formatted": format_bytes(db_size),
            "disk_total_bytes": disk.total,
            "disk_total_formatted": format_bytes(disk.total),
            "disk_used_bytes": disk.used,
            "disk_used_formatted": format_bytes(disk.used),
            "disk_free_bytes": disk.free,
            "disk_free_formatted": format_bytes(disk.free),
            "disk_percent_used": disk.percent,
        },
        "system": {
            "platform": platform.system(),
            "platform_release": platform.release(),
            "platform_version": platform.version(),
            "architecture": platform.machine(),
            "hostname": platform.node(),
            "python_version": sys.version.split()[0],
            "uptime_seconds": uptime_seconds,
            "uptime_formatted": format_uptime(uptime_seconds),
            "boot_time": boot_time.isoformat(),
        },
        "memory": {
            "total_bytes": memory.total,
            "total_formatted": format_bytes(memory.total),
            "available_bytes": memory.available,
            "available_formatted": format_bytes(memory.available),
            "used_bytes": memory.used,
            "used_formatted": format_bytes(memory.used),
            "percent_used": memory.percent,
        },
        "cpu": {
            "count": psutil.cpu_count(),
            "count_logical": psutil.cpu_count(logical=True),
            "percent": psutil.cpu_percent(interval=0.1),
        },
    }


@router.get("/storage-usage")
async def get_storage_usage(
    refresh: bool = False,
    max_age_seconds: int = STORAGE_USAGE_CACHE_SECONDS,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SYSTEM_READ),
):
    """Get storage usage breakdown for Bambuddy data directories."""
    max_age_seconds = max(0, min(max_age_seconds, 3600))
    return await _get_storage_usage_cached(refresh=refresh, max_age_seconds=max_age_seconds)
