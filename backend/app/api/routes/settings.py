import io
import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, UploadFile, File, Query
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.core.config import settings as app_settings
from backend.app.core.database import get_db
from backend.app.models.settings import Settings
from backend.app.models.notification import NotificationProvider
from backend.app.models.notification_template import NotificationTemplate
from backend.app.models.smart_plug import SmartPlug
from backend.app.models.printer import Printer
from backend.app.models.filament import Filament
from backend.app.models.maintenance import MaintenanceType, PrinterMaintenance, MaintenanceHistory
from backend.app.models.archive import PrintArchive
from backend.app.schemas.settings import AppSettings, AppSettingsUpdate


router = APIRouter(prefix="/settings", tags=["settings"])

# Default settings
DEFAULT_SETTINGS = AppSettings()


async def get_setting(db: AsyncSession, key: str) -> str | None:
    """Get a single setting value by key."""
    result = await db.execute(select(Settings).where(Settings.key == key))
    setting = result.scalar_one_or_none()
    return setting.value if setting else None


async def set_setting(db: AsyncSession, key: str, value: str) -> None:
    """Set a single setting value."""
    result = await db.execute(select(Settings).where(Settings.key == key))
    setting = result.scalar_one_or_none()

    if setting:
        setting.value = value
    else:
        setting = Settings(key=key, value=value)
        db.add(setting)


@router.get("/", response_model=AppSettings)
async def get_settings(db: AsyncSession = Depends(get_db)):
    """Get all application settings."""
    settings_dict = DEFAULT_SETTINGS.model_dump()

    # Load saved settings from database
    result = await db.execute(select(Settings))
    db_settings = result.scalars().all()

    for setting in db_settings:
        if setting.key in settings_dict:
            # Parse the value based on the expected type
            if setting.key in ["auto_archive", "save_thumbnails", "capture_finish_photo", "spoolman_enabled", "check_updates"]:
                settings_dict[setting.key] = setting.value.lower() == "true"
            elif setting.key in ["default_filament_cost", "energy_cost_per_kwh", "ams_temp_good", "ams_temp_fair"]:
                settings_dict[setting.key] = float(setting.value)
            elif setting.key in ["ams_humidity_good", "ams_humidity_fair"]:
                settings_dict[setting.key] = int(setting.value)
            elif setting.key == "default_printer_id":
                # Handle nullable integer
                settings_dict[setting.key] = int(setting.value) if setting.value and setting.value != "None" else None
            else:
                settings_dict[setting.key] = setting.value

    return AppSettings(**settings_dict)


@router.put("/", response_model=AppSettings)
async def update_settings(
    settings_update: AppSettingsUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update application settings."""
    update_data = settings_update.model_dump(exclude_unset=True)

    for key, value in update_data.items():
        # Convert value to string for storage
        if isinstance(value, bool):
            str_value = "true" if value else "false"
        elif value is None:
            str_value = "None"
        else:
            str_value = str(value)
        await set_setting(db, key, str_value)

    await db.commit()

    # Return updated settings
    return await get_settings(db)


@router.post("/reset", response_model=AppSettings)
async def reset_settings(db: AsyncSession = Depends(get_db)):
    """Reset all settings to defaults."""
    # Delete all settings
    result = await db.execute(select(Settings))
    for setting in result.scalars().all():
        await db.delete(setting)

    await db.commit()

    return DEFAULT_SETTINGS


@router.get("/check-ffmpeg")
async def check_ffmpeg():
    """Check if ffmpeg is installed and available."""
    from backend.app.services.camera import get_ffmpeg_path

    ffmpeg_path = get_ffmpeg_path()

    return {
        "installed": ffmpeg_path is not None,
        "path": ffmpeg_path,
    }


@router.get("/spoolman")
async def get_spoolman_settings(db: AsyncSession = Depends(get_db)):
    """Get Spoolman integration settings."""
    spoolman_enabled = await get_setting(db, "spoolman_enabled") or "false"
    spoolman_url = await get_setting(db, "spoolman_url") or ""
    spoolman_sync_mode = await get_setting(db, "spoolman_sync_mode") or "auto"

    return {
        "spoolman_enabled": spoolman_enabled,
        "spoolman_url": spoolman_url,
        "spoolman_sync_mode": spoolman_sync_mode,
    }


@router.put("/spoolman")
async def update_spoolman_settings(
    settings: dict,
    db: AsyncSession = Depends(get_db),
):
    """Update Spoolman integration settings."""
    if "spoolman_enabled" in settings:
        await set_setting(db, "spoolman_enabled", settings["spoolman_enabled"])
    if "spoolman_url" in settings:
        await set_setting(db, "spoolman_url", settings["spoolman_url"])
    if "spoolman_sync_mode" in settings:
        await set_setting(db, "spoolman_sync_mode", settings["spoolman_sync_mode"])

    await db.commit()

    # Return updated settings
    return await get_spoolman_settings(db)


@router.get("/backup")
async def export_backup(
    db: AsyncSession = Depends(get_db),
    include_settings: bool = Query(True, description="Include app settings"),
    include_notifications: bool = Query(True, description="Include notification providers"),
    include_templates: bool = Query(True, description="Include notification templates"),
    include_smart_plugs: bool = Query(True, description="Include smart plugs"),
    include_printers: bool = Query(False, description="Include printers (without access codes)"),
    include_filaments: bool = Query(False, description="Include filament inventory"),
    include_maintenance: bool = Query(False, description="Include maintenance types and records"),
    include_archives: bool = Query(False, description="Include print archive metadata"),
):
    """Export selected data as JSON backup."""
    backup: dict = {
        "version": "2.0",
        "exported_at": datetime.utcnow().isoformat(),
        "included": [],
    }

    # Settings
    if include_settings:
        result = await db.execute(select(Settings))
        db_settings = result.scalars().all()
        backup["settings"] = {s.key: s.value for s in db_settings}
        backup["included"].append("settings")

    # Notification providers
    if include_notifications:
        result = await db.execute(select(NotificationProvider))
        providers = result.scalars().all()
        backup["notification_providers"] = []
        for p in providers:
            backup["notification_providers"].append({
                "name": p.name,
                "provider_type": p.provider_type,
                "enabled": p.enabled,
                "config": json.loads(p.config) if isinstance(p.config, str) else p.config,
                "on_print_start": p.on_print_start,
                "on_print_complete": p.on_print_complete,
                "on_print_failed": p.on_print_failed,
                "on_print_stopped": p.on_print_stopped,
                "on_print_progress": p.on_print_progress,
                "on_printer_offline": p.on_printer_offline,
                "on_printer_error": p.on_printer_error,
                "on_filament_low": p.on_filament_low,
                "on_maintenance_due": p.on_maintenance_due,
                "quiet_hours_enabled": p.quiet_hours_enabled,
                "quiet_hours_start": p.quiet_hours_start,
                "quiet_hours_end": p.quiet_hours_end,
                "daily_digest_enabled": getattr(p, 'daily_digest_enabled', False),
                "daily_digest_time": getattr(p, 'daily_digest_time', None),
                "printer_id": getattr(p, 'printer_id', None),
            })
        backup["included"].append("notification_providers")

    # Notification templates
    if include_templates:
        result = await db.execute(select(NotificationTemplate))
        templates = result.scalars().all()
        backup["notification_templates"] = []
        for t in templates:
            backup["notification_templates"].append({
                "event_type": t.event_type,
                "name": t.name,
                "title_template": t.title_template,
                "body_template": t.body_template,
                "is_default": t.is_default,
            })
        backup["included"].append("notification_templates")

    # Smart plugs
    if include_smart_plugs:
        result = await db.execute(select(SmartPlug))
        plugs = result.scalars().all()
        backup["smart_plugs"] = []
        for plug in plugs:
            backup["smart_plugs"].append({
                "name": plug.name,
                "ip_address": plug.ip_address,
                "printer_id": plug.printer_id,
                "enabled": plug.enabled,
                "auto_on": plug.auto_on,
                "auto_off": plug.auto_off,
                "off_delay_mode": plug.off_delay_mode,
                "off_delay_minutes": plug.off_delay_minutes,
                "off_temp_threshold": plug.off_temp_threshold,
                "username": plug.username,
                "password": plug.password,
                "power_alert_enabled": plug.power_alert_enabled,
                "power_alert_high": plug.power_alert_high,
                "power_alert_low": plug.power_alert_low,
                "schedule_enabled": plug.schedule_enabled,
                "schedule_on_time": plug.schedule_on_time,
                "schedule_off_time": plug.schedule_off_time,
            })
        backup["included"].append("smart_plugs")

    # Printers (without access codes for security)
    if include_printers:
        result = await db.execute(select(Printer))
        printers = result.scalars().all()
        backup["printers"] = []
        for printer in printers:
            backup["printers"].append({
                "name": printer.name,
                "serial_number": printer.serial_number,
                "ip_address": printer.ip_address,
                # access_code intentionally excluded for security
                "model": printer.model,
                "location": printer.location,
                "nozzle_count": printer.nozzle_count,
                "is_active": printer.is_active,
                "auto_archive": printer.auto_archive,
                "print_hours_offset": printer.print_hours_offset,
            })
        backup["included"].append("printers")

    # Filaments
    if include_filaments:
        result = await db.execute(select(Filament))
        filaments = result.scalars().all()
        backup["filaments"] = []
        for f in filaments:
            backup["filaments"].append({
                "name": f.name,
                "type": f.type,
                "brand": f.brand,
                "color": f.color,
                "color_hex": f.color_hex,
                "cost_per_kg": f.cost_per_kg,
                "spool_weight_g": f.spool_weight_g,
                "currency": f.currency,
                "density": f.density,
                "print_temp_min": f.print_temp_min,
                "print_temp_max": f.print_temp_max,
                "bed_temp_min": f.bed_temp_min,
                "bed_temp_max": f.bed_temp_max,
            })
        backup["included"].append("filaments")

    # Maintenance types and records
    if include_maintenance:
        # Maintenance types
        result = await db.execute(select(MaintenanceType))
        types = result.scalars().all()
        backup["maintenance_types"] = []
        for mt in types:
            backup["maintenance_types"].append({
                "name": mt.name,
                "description": mt.description,
                "default_interval_hours": mt.default_interval_hours,
                "interval_type": mt.interval_type,
                "icon": mt.icon,
                "is_system": mt.is_system,
            })
        backup["included"].append("maintenance_types")

    # Print archives with file paths for ZIP
    archive_files: list[tuple[str, Path]] = []  # (zip_path, local_path)
    if include_archives:
        result = await db.execute(select(PrintArchive))
        archives = result.scalars().all()
        backup["archives"] = []
        base_dir = app_settings.base_dir

        for a in archives:
            archive_data = {
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
                "status": a.status,
                "started_at": a.started_at.isoformat() if a.started_at else None,
                "completed_at": a.completed_at.isoformat() if a.completed_at else None,
                "makerworld_url": a.makerworld_url,
                "designer": a.designer,
                "is_favorite": a.is_favorite,
                "tags": a.tags,
                "notes": a.notes,
                "cost": a.cost,
                "failure_reason": a.failure_reason,
                "energy_kwh": a.energy_kwh,
                "energy_cost": a.energy_cost,
                "extra_data": a.extra_data,
                "photos": a.photos,
            }

            # Collect file paths for ZIP
            if a.file_path:
                file_path = base_dir / a.file_path
                if file_path.exists():
                    archive_data["file_path"] = a.file_path
                    archive_files.append((a.file_path, file_path))

            if a.thumbnail_path:
                thumb_path = base_dir / a.thumbnail_path
                if thumb_path.exists():
                    archive_data["thumbnail_path"] = a.thumbnail_path
                    archive_files.append((a.thumbnail_path, thumb_path))

            if a.timelapse_path:
                timelapse_path = base_dir / a.timelapse_path
                if timelapse_path.exists():
                    archive_data["timelapse_path"] = a.timelapse_path
                    archive_files.append((a.timelapse_path, timelapse_path))

            if a.source_3mf_path:
                source_path = base_dir / a.source_3mf_path
                if source_path.exists():
                    archive_data["source_3mf_path"] = a.source_3mf_path
                    archive_files.append((a.source_3mf_path, source_path))

            # Include photos
            if a.photos:
                for photo in a.photos:
                    photo_path = base_dir / "archive" / "photos" / photo
                    if photo_path.exists():
                        zip_photo_path = f"archive/photos/{photo}"
                        archive_files.append((zip_photo_path, photo_path))

            backup["archives"].append(archive_data)
        backup["included"].append("archives")

    # If archives included, create ZIP file with all files
    if include_archives and archive_files:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Add backup.json
            zf.writestr("backup.json", json.dumps(backup, indent=2))

            # Add all archive files
            added_files = set()
            for zip_path, local_path in archive_files:
                if zip_path not in added_files and local_path.exists():
                    try:
                        zf.write(local_path, zip_path)
                        added_files.add(zip_path)
                    except Exception:
                        pass  # Skip files that can't be read

        zip_buffer.seek(0)
        filename = f"bambusy-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    # Otherwise return JSON
    return JSONResponse(
        content=backup,
        headers={
            "Content-Disposition": f"attachment; filename=bambusy-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        }
    )


@router.post("/restore")
async def import_backup(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Restore data from JSON or ZIP backup. Skips duplicates."""
    try:
        content = await file.read()
        base_dir = app_settings.base_dir
        files_restored = 0

        # Check if it's a ZIP file
        if file.filename and file.filename.endswith('.zip'):
            try:
                zip_buffer = io.BytesIO(content)
                with zipfile.ZipFile(zip_buffer, 'r') as zf:
                    # Extract backup.json
                    if 'backup.json' not in zf.namelist():
                        return {"success": False, "message": "Invalid ZIP: missing backup.json"}

                    backup_content = zf.read('backup.json')
                    backup = json.loads(backup_content.decode("utf-8"))

                    # Extract all other files to base_dir
                    for zip_path in zf.namelist():
                        if zip_path == 'backup.json':
                            continue
                        # Ensure path is safe (no path traversal)
                        if '..' in zip_path or zip_path.startswith('/'):
                            continue
                        target_path = base_dir / zip_path
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(zip_path) as src, open(target_path, 'wb') as dst:
                            dst.write(src.read())
                            files_restored += 1
            except zipfile.BadZipFile:
                return {"success": False, "message": "Invalid ZIP file"}
        else:
            backup = json.loads(content.decode("utf-8"))
    except json.JSONDecodeError as e:
        return {"success": False, "message": f"Invalid JSON: {str(e)}"}
    except Exception as e:
        return {"success": False, "message": f"Invalid backup file: {str(e)}"}

    restored = {
        "settings": 0,
        "notification_providers": 0,
        "notification_templates": 0,
        "smart_plugs": 0,
        "printers": 0,
        "filaments": 0,
        "maintenance_types": 0,
    }

    # Restore settings
    if "settings" in backup:
        for key, value in backup["settings"].items():
            await set_setting(db, key, value)
            restored["settings"] += 1

    # Restore notification providers (skip duplicates by name)
    if "notification_providers" in backup:
        for provider_data in backup["notification_providers"]:
            result = await db.execute(
                select(NotificationProvider).where(NotificationProvider.name == provider_data["name"])
            )
            existing = result.scalar_one_or_none()
            if not existing:
                provider = NotificationProvider(
                    name=provider_data["name"],
                    provider_type=provider_data["provider_type"],
                    enabled=provider_data.get("enabled", True),
                    config=json.dumps(provider_data.get("config", {})),
                    on_print_start=provider_data.get("on_print_start", False),
                    on_print_complete=provider_data.get("on_print_complete", True),
                    on_print_failed=provider_data.get("on_print_failed", True),
                    on_print_stopped=provider_data.get("on_print_stopped", True),
                    on_print_progress=provider_data.get("on_print_progress", False),
                    on_printer_offline=provider_data.get("on_printer_offline", False),
                    on_printer_error=provider_data.get("on_printer_error", False),
                    on_filament_low=provider_data.get("on_filament_low", False),
                    on_maintenance_due=provider_data.get("on_maintenance_due", False),
                    quiet_hours_enabled=provider_data.get("quiet_hours_enabled", False),
                    quiet_hours_start=provider_data.get("quiet_hours_start"),
                    quiet_hours_end=provider_data.get("quiet_hours_end"),
                    daily_digest_enabled=provider_data.get("daily_digest_enabled", False),
                    daily_digest_time=provider_data.get("daily_digest_time"),
                    printer_id=provider_data.get("printer_id"),
                )
                db.add(provider)
                restored["notification_providers"] += 1

    # Restore notification templates (update existing by event_type)
    if "notification_templates" in backup:
        for template_data in backup["notification_templates"]:
            result = await db.execute(
                select(NotificationTemplate).where(
                    NotificationTemplate.event_type == template_data["event_type"]
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                # Update existing template
                existing.name = template_data.get("name", existing.name)
                existing.title_template = template_data.get("title_template", existing.title_template)
                existing.body_template = template_data.get("body_template", existing.body_template)
                existing.is_default = template_data.get("is_default", False)
            else:
                template = NotificationTemplate(
                    event_type=template_data["event_type"],
                    name=template_data["name"],
                    title_template=template_data["title_template"],
                    body_template=template_data["body_template"],
                    is_default=template_data.get("is_default", False),
                )
                db.add(template)
            restored["notification_templates"] += 1

    # Restore smart plugs (skip duplicates by IP)
    if "smart_plugs" in backup:
        for plug_data in backup["smart_plugs"]:
            result = await db.execute(
                select(SmartPlug).where(SmartPlug.ip_address == plug_data["ip_address"])
            )
            existing = result.scalar_one_or_none()
            if not existing:
                plug = SmartPlug(
                    name=plug_data["name"],
                    ip_address=plug_data["ip_address"],
                    printer_id=plug_data.get("printer_id"),
                    enabled=plug_data.get("enabled", True),
                    auto_on=plug_data.get("auto_on", True),
                    auto_off=plug_data.get("auto_off", True),
                    off_delay_mode=plug_data.get("off_delay_mode", "time"),
                    off_delay_minutes=plug_data.get("off_delay_minutes", 5),
                    off_temp_threshold=plug_data.get("off_temp_threshold", 70),
                    username=plug_data.get("username"),
                    password=plug_data.get("password"),
                    power_alert_enabled=plug_data.get("power_alert_enabled", False),
                    power_alert_high=plug_data.get("power_alert_high"),
                    power_alert_low=plug_data.get("power_alert_low"),
                    schedule_enabled=plug_data.get("schedule_enabled", False),
                    schedule_on_time=plug_data.get("schedule_on_time"),
                    schedule_off_time=plug_data.get("schedule_off_time"),
                )
                db.add(plug)
                restored["smart_plugs"] += 1

    # Restore printers (skip duplicates by serial_number, requires access_code to be set manually)
    if "printers" in backup:
        for printer_data in backup["printers"]:
            result = await db.execute(
                select(Printer).where(Printer.serial_number == printer_data["serial_number"])
            )
            existing = result.scalar_one_or_none()
            if not existing:
                printer = Printer(
                    name=printer_data["name"],
                    serial_number=printer_data["serial_number"],
                    ip_address=printer_data["ip_address"],
                    access_code="CHANGE_ME",  # Must be set manually for security
                    model=printer_data.get("model"),
                    location=printer_data.get("location"),
                    nozzle_count=printer_data.get("nozzle_count", 1),
                    is_active=False,  # Disabled until access_code is set
                    auto_archive=printer_data.get("auto_archive", True),
                    print_hours_offset=printer_data.get("print_hours_offset", 0.0),
                )
                db.add(printer)
                restored["printers"] += 1

    # Restore filaments (skip duplicates by name+type+brand)
    if "filaments" in backup:
        for filament_data in backup["filaments"]:
            result = await db.execute(
                select(Filament).where(
                    Filament.name == filament_data["name"],
                    Filament.type == filament_data["type"],
                    Filament.brand == filament_data.get("brand"),
                )
            )
            existing = result.scalar_one_or_none()
            if not existing:
                filament = Filament(
                    name=filament_data["name"],
                    type=filament_data["type"],
                    brand=filament_data.get("brand"),
                    color=filament_data.get("color"),
                    color_hex=filament_data.get("color_hex"),
                    cost_per_kg=filament_data.get("cost_per_kg", 25.0),
                    spool_weight_g=filament_data.get("spool_weight_g", 1000.0),
                    currency=filament_data.get("currency", "USD"),
                    density=filament_data.get("density"),
                    print_temp_min=filament_data.get("print_temp_min"),
                    print_temp_max=filament_data.get("print_temp_max"),
                    bed_temp_min=filament_data.get("bed_temp_min"),
                    bed_temp_max=filament_data.get("bed_temp_max"),
                )
                db.add(filament)
                restored["filaments"] += 1

    # Restore maintenance types (skip duplicates by name)
    if "maintenance_types" in backup:
        for mt_data in backup["maintenance_types"]:
            result = await db.execute(
                select(MaintenanceType).where(MaintenanceType.name == mt_data["name"])
            )
            existing = result.scalar_one_or_none()
            if not existing:
                mt = MaintenanceType(
                    name=mt_data["name"],
                    description=mt_data.get("description"),
                    default_interval_hours=mt_data.get("default_interval_hours", 100.0),
                    interval_type=mt_data.get("interval_type", "hours"),
                    icon=mt_data.get("icon"),
                    is_system=mt_data.get("is_system", False),
                )
                db.add(mt)
                restored["maintenance_types"] += 1

    # Restore archives (skip duplicates by content_hash)
    if "archives" in backup:
        for archive_data in backup["archives"]:
            # Skip if no content_hash or already exists
            content_hash = archive_data.get("content_hash")
            if content_hash:
                result = await db.execute(
                    select(PrintArchive).where(PrintArchive.content_hash == content_hash)
                )
                existing = result.scalar_one_or_none()
                if existing:
                    continue

            # Only restore if file exists (from ZIP extraction)
            file_path = archive_data.get("file_path")
            if file_path and (base_dir / file_path).exists():
                archive = PrintArchive(
                    filename=archive_data["filename"],
                    file_path=file_path,
                    file_size=archive_data.get("file_size", 0),
                    content_hash=content_hash,
                    thumbnail_path=archive_data.get("thumbnail_path"),
                    timelapse_path=archive_data.get("timelapse_path"),
                    source_3mf_path=archive_data.get("source_3mf_path"),
                    print_name=archive_data.get("print_name"),
                    print_time_seconds=archive_data.get("print_time_seconds"),
                    filament_used_grams=archive_data.get("filament_used_grams"),
                    filament_type=archive_data.get("filament_type"),
                    filament_color=archive_data.get("filament_color"),
                    layer_height=archive_data.get("layer_height"),
                    total_layers=archive_data.get("total_layers"),
                    nozzle_diameter=archive_data.get("nozzle_diameter"),
                    bed_temperature=archive_data.get("bed_temperature"),
                    nozzle_temperature=archive_data.get("nozzle_temperature"),
                    status=archive_data.get("status", "completed"),
                    makerworld_url=archive_data.get("makerworld_url"),
                    designer=archive_data.get("designer"),
                    is_favorite=archive_data.get("is_favorite", False),
                    tags=archive_data.get("tags"),
                    notes=archive_data.get("notes"),
                    cost=archive_data.get("cost"),
                    failure_reason=archive_data.get("failure_reason"),
                    energy_kwh=archive_data.get("energy_kwh"),
                    energy_cost=archive_data.get("energy_cost"),
                    extra_data=archive_data.get("extra_data"),
                    photos=archive_data.get("photos"),
                )
                db.add(archive)
                restored["archives"] = restored.get("archives", 0) + 1

    await db.commit()

    # Build summary message
    parts = []
    for key, count in restored.items():
        if count > 0:
            parts.append(f"{count} {key.replace('_', ' ')}")

    if files_restored > 0:
        parts.append(f"{files_restored} files")

    return {
        "success": True,
        "message": f"Restored: {', '.join(parts)}" if parts else "Nothing to restore",
        "restored": restored,
        "files_restored": files_restored,
    }
