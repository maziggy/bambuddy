"""System information API routes."""

import platform
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


def get_directory_size(path: Path) -> int:
    """Calculate total size of a directory in bytes."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                total += entry.stat().st_size
    except (PermissionError, OSError):
        pass
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
