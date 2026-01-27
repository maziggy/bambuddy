import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings as app_settings
from backend.app.core.database import get_db
from backend.app.models.api_key import APIKey
from backend.app.models.archive import PrintArchive
from backend.app.models.external_link import ExternalLink
from backend.app.models.filament import Filament
from backend.app.models.github_backup import GitHubBackupConfig
from backend.app.models.maintenance import MaintenanceHistory, MaintenanceType, PrinterMaintenance
from backend.app.models.notification import NotificationProvider
from backend.app.models.notification_template import NotificationTemplate
from backend.app.models.pending_upload import PendingUpload
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.project import Project
from backend.app.models.project_bom import ProjectBOMItem
from backend.app.models.settings import Settings
from backend.app.models.smart_plug import SmartPlug
from backend.app.models.user import User
from backend.app.schemas.settings import AppSettings, AppSettingsUpdate
from backend.app.services.printer_manager import printer_manager
from backend.app.services.spoolman import init_spoolman_client

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
    from sqlalchemy import func
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    # Use upsert (INSERT ... ON CONFLICT UPDATE) for reliability
    stmt = sqlite_insert(Settings).values(key=key, value=value)
    stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={"value": value, "updated_at": func.now()})
    await db.execute(stmt)


@router.get("", response_model=AppSettings)
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
            if setting.key in [
                "auto_archive",
                "save_thumbnails",
                "capture_finish_photo",
                "spoolman_enabled",
                "check_updates",
                "virtual_printer_enabled",
                "ftp_retry_enabled",
                "mqtt_enabled",
                "mqtt_use_tls",
                "ha_enabled",
                "per_printer_mapping_expanded",
                "prometheus_enabled",
            ]:
                settings_dict[setting.key] = setting.value.lower() == "true"
            elif setting.key in [
                "default_filament_cost",
                "energy_cost_per_kwh",
                "ams_temp_good",
                "ams_temp_fair",
                "library_disk_warning_gb",
            ]:
                settings_dict[setting.key] = float(setting.value)
            elif setting.key in [
                "ams_humidity_good",
                "ams_humidity_fair",
                "ams_history_retention_days",
                "ftp_retry_count",
                "ftp_retry_delay",
                "mqtt_port",
            ]:
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

    # Check if any MQTT settings are being updated
    mqtt_keys = {
        "mqtt_enabled",
        "mqtt_broker",
        "mqtt_port",
        "mqtt_username",
        "mqtt_password",
        "mqtt_topic_prefix",
        "mqtt_use_tls",
    }
    mqtt_updated = bool(mqtt_keys & set(update_data.keys()))

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
    # Expire all objects to ensure fresh reads after commit
    db.expire_all()

    # Reconfigure MQTT relay if any MQTT settings changed
    if mqtt_updated:
        try:
            from backend.app.services.mqtt_relay import mqtt_relay

            mqtt_settings = {
                "mqtt_enabled": (await get_setting(db, "mqtt_enabled") or "false") == "true",
                "mqtt_broker": await get_setting(db, "mqtt_broker") or "",
                "mqtt_port": int(await get_setting(db, "mqtt_port") or "1883"),
                "mqtt_username": await get_setting(db, "mqtt_username") or "",
                "mqtt_password": await get_setting(db, "mqtt_password") or "",
                "mqtt_topic_prefix": await get_setting(db, "mqtt_topic_prefix") or "bambuddy",
                "mqtt_use_tls": (await get_setting(db, "mqtt_use_tls") or "false") == "true",
            }
            await mqtt_relay.configure(mqtt_settings)
        except Exception:
            pass  # Don't fail the settings update if MQTT reconfiguration fails

    # Return updated settings
    return await get_settings(db)


@router.patch("/", response_model=AppSettings)
@router.patch("", response_model=AppSettings)
async def patch_settings(
    settings_update: AppSettingsUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Partially update application settings (same as PUT, for REST compatibility)."""
    return await update_settings(settings_update, db)


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
    db.expire_all()

    # Return updated settings
    return await get_spoolman_settings(db)


@router.get("/backup")
async def export_backup(
    db: AsyncSession = Depends(get_db),
    include_settings: bool = Query(True, description="Include app settings"),
    include_notifications: bool = Query(True, description="Include notification providers"),
    include_templates: bool = Query(True, description="Include notification templates"),
    include_smart_plugs: bool = Query(True, description="Include smart plugs"),
    include_external_links: bool = Query(True, description="Include external sidebar links"),
    include_printers: bool = Query(False, description="Include printers (without access codes)"),
    include_plate_calibration: bool = Query(False, description="Include plate detection reference images"),
    include_filaments: bool = Query(False, description="Include filament inventory"),
    include_maintenance: bool = Query(
        False, description="Include maintenance types, per-printer settings, and history"
    ),
    include_print_queue: bool = Query(False, description="Include print queue items"),
    include_archives: bool = Query(False, description="Include print archive metadata"),
    include_projects: bool = Query(False, description="Include projects with BOM items"),
    include_pending_uploads: bool = Query(False, description="Include pending virtual printer uploads"),
    include_access_codes: bool = Query(False, description="Include printer access codes (security risk!)"),
    include_api_keys: bool = Query(False, description="Include API keys (keys will need to be regenerated on import)"),
    include_users: bool = Query(
        False, description="Include users (passwords not exported - users will need new passwords)"
    ),
    include_github_backup: bool = Query(False, description="Include GitHub backup configuration (token not exported)"),
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
        # Build printer ID to serial lookup for cross-system backup
        printer_id_to_serial: dict[int, str] = {}
        pr_result = await db.execute(select(Printer))
        for pr in pr_result.scalars().all():
            printer_id_to_serial[pr.id] = pr.serial_number

        result = await db.execute(select(NotificationProvider))
        providers = result.scalars().all()
        backup["notification_providers"] = []
        for p in providers:
            # Use printer_serial for cross-system compatibility
            provider_printer_id = getattr(p, "printer_id", None)
            printer_serial = printer_id_to_serial.get(provider_printer_id) if provider_printer_id else None

            backup["notification_providers"].append(
                {
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
                    "on_ams_humidity_high": getattr(p, "on_ams_humidity_high", False),
                    "on_ams_temperature_high": getattr(p, "on_ams_temperature_high", False),
                    "on_ams_ht_humidity_high": getattr(p, "on_ams_ht_humidity_high", False),
                    "on_ams_ht_temperature_high": getattr(p, "on_ams_ht_temperature_high", False),
                    "on_plate_not_empty": getattr(p, "on_plate_not_empty", True),
                    "quiet_hours_enabled": p.quiet_hours_enabled,
                    "quiet_hours_start": p.quiet_hours_start,
                    "quiet_hours_end": p.quiet_hours_end,
                    "daily_digest_enabled": getattr(p, "daily_digest_enabled", False),
                    "daily_digest_time": getattr(p, "daily_digest_time", None),
                    "printer_serial": printer_serial,
                }
            )
        backup["included"].append("notification_providers")

    # Notification templates
    if include_templates:
        result = await db.execute(select(NotificationTemplate))
        templates = result.scalars().all()
        backup["notification_templates"] = []
        for t in templates:
            backup["notification_templates"].append(
                {
                    "event_type": t.event_type,
                    "name": t.name,
                    "title_template": t.title_template,
                    "body_template": t.body_template,
                    "is_default": t.is_default,
                }
            )
        backup["included"].append("notification_templates")

    # Smart plugs
    if include_smart_plugs:
        result = await db.execute(select(SmartPlug))
        plugs = result.scalars().all()
        backup["smart_plugs"] = []

        # Build printer ID to serial mapping
        printer_id_to_serial: dict[int, str] = {}
        pr_result = await db.execute(select(Printer))
        for pr in pr_result.scalars().all():
            printer_id_to_serial[pr.id] = pr.serial_number

        for plug in plugs:
            backup["smart_plugs"].append(
                {
                    "name": plug.name,
                    "plug_type": plug.plug_type,
                    "ip_address": plug.ip_address,
                    "ha_entity_id": plug.ha_entity_id,
                    "ha_power_entity": plug.ha_power_entity,
                    "ha_energy_today_entity": plug.ha_energy_today_entity,
                    "ha_energy_total_entity": plug.ha_energy_total_entity,
                    "printer_serial": printer_id_to_serial.get(plug.printer_id) if plug.printer_id else None,
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
                    "show_in_switchbar": plug.show_in_switchbar,
                }
            )
        backup["included"].append("smart_plugs")

    # External links
    if include_external_links:
        result = await db.execute(select(ExternalLink).order_by(ExternalLink.sort_order))
        links = result.scalars().all()
        backup["external_links"] = []
        icons_dir = app_settings.base_dir / "icons"
        for link in links:
            link_data = {
                "name": link.name,
                "url": link.url,
                "icon": link.icon,
                "sort_order": link.sort_order,
            }
            # Include custom icon file path if exists
            if link.custom_icon:
                link_data["custom_icon"] = link.custom_icon
                icon_path = icons_dir / link.custom_icon
                if icon_path.exists():
                    link_data["custom_icon_path"] = f"icons/{link.custom_icon}"
            backup["external_links"].append(link_data)
        backup["included"].append("external_links")

    # Printers (access codes only included if explicitly requested)
    if include_printers:
        result = await db.execute(select(Printer))
        printers = result.scalars().all()
        backup["printers"] = []
        for printer in printers:
            printer_data = {
                "name": printer.name,
                "serial_number": printer.serial_number,
                "ip_address": printer.ip_address,
                "model": printer.model,
                "location": printer.location,
                "nozzle_count": printer.nozzle_count,
                "is_active": printer.is_active,
                "auto_archive": printer.auto_archive,
                "print_hours_offset": printer.print_hours_offset,
                "runtime_seconds": printer.runtime_seconds,
                "external_camera_url": printer.external_camera_url,
                "external_camera_type": printer.external_camera_type,
                "external_camera_enabled": printer.external_camera_enabled,
                "plate_detection_enabled": printer.plate_detection_enabled,
                "plate_detection_roi_x": printer.plate_detection_roi_x,
                "plate_detection_roi_y": printer.plate_detection_roi_y,
                "plate_detection_roi_w": printer.plate_detection_roi_w,
                "plate_detection_roi_h": printer.plate_detection_roi_h,
            }
            if include_access_codes:
                printer_data["access_code"] = printer.access_code
            backup["printers"].append(printer_data)
        backup["included"].append("printers")
        if include_access_codes:
            backup["included"].append("access_codes")

    # Plate calibration references (requires include_printers)
    if include_printers and include_plate_calibration:
        plate_cal_dir = app_settings.plate_calibration_dir
        if plate_cal_dir.exists():
            backup["plate_calibration"] = {
                "files": [],
                "printer_id_to_serial": {},  # Map old printer IDs to serial numbers for restore
            }
            for cal_file in plate_cal_dir.iterdir():
                if cal_file.is_file():
                    backup["plate_calibration"]["files"].append(cal_file.name)
                    # Extract printer ID from filename (e.g., "printer_1_ref_0.jpg" -> 1)
                    if cal_file.name.startswith("printer_"):
                        parts = cal_file.name.split("_")
                        if len(parts) >= 2 and parts[1].isdigit():
                            old_printer_id = int(parts[1])
                            if old_printer_id not in backup["plate_calibration"]["printer_id_to_serial"]:
                                # Look up serial number for this printer ID
                                backup["plate_calibration"]["printer_id_to_serial"][old_printer_id] = (
                                    printer_id_to_serial.get(old_printer_id)
                                )
            if backup["plate_calibration"]["files"]:
                backup["included"].append("plate_calibration")

    # Filaments
    if include_filaments:
        result = await db.execute(select(Filament))
        filaments = result.scalars().all()
        backup["filaments"] = []
        for f in filaments:
            backup["filaments"].append(
                {
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
                }
            )
        backup["included"].append("filaments")

    # Maintenance types and records
    if include_maintenance:
        # Maintenance types
        result = await db.execute(select(MaintenanceType))
        types = result.scalars().all()
        backup["maintenance_types"] = []
        for mt in types:
            backup["maintenance_types"].append(
                {
                    "name": mt.name,
                    "description": mt.description,
                    "default_interval_hours": mt.default_interval_hours,
                    "interval_type": mt.interval_type,
                    "icon": mt.icon,
                    "is_system": mt.is_system,
                }
            )
        backup["included"].append("maintenance_types")

        # Printer maintenance settings (per-printer custom intervals, enabled status, last performed)
        result = await db.execute(select(PrinterMaintenance))
        printer_maint = result.scalars().all()
        backup["printer_maintenance"] = []

        # Build lookups for printer serial and maintenance type name
        printer_id_to_serial: dict[int, str] = {}
        maint_type_id_to_name: dict[int, str] = {}
        pr_result = await db.execute(select(Printer))
        for pr in pr_result.scalars().all():
            printer_id_to_serial[pr.id] = pr.serial_number
        for mt in types:
            maint_type_id_to_name[mt.id] = mt.name

        for pm in printer_maint:
            backup["printer_maintenance"].append(
                {
                    "printer_serial": printer_id_to_serial.get(pm.printer_id),
                    "maintenance_type_name": maint_type_id_to_name.get(pm.maintenance_type_id),
                    "custom_interval_hours": pm.custom_interval_hours,
                    "custom_interval_type": pm.custom_interval_type,
                    "enabled": pm.enabled,
                    "last_performed_at": pm.last_performed_at.isoformat() if pm.last_performed_at else None,
                    "last_performed_hours": pm.last_performed_hours,
                }
            )
        backup["included"].append("printer_maintenance")

        # Maintenance history
        result = await db.execute(select(MaintenanceHistory))
        history = result.scalars().all()
        backup["maintenance_history"] = []

        # Build printer_maintenance ID to (printer_serial, maint_type_name) mapping
        pm_id_to_info: dict[int, tuple[str | None, str | None]] = {}
        for pm in printer_maint:
            pm_id_to_info[pm.id] = (
                printer_id_to_serial.get(pm.printer_id),
                maint_type_id_to_name.get(pm.maintenance_type_id),
            )

        for mh in history:
            info = pm_id_to_info.get(mh.printer_maintenance_id, (None, None))
            backup["maintenance_history"].append(
                {
                    "printer_serial": info[0],
                    "maintenance_type_name": info[1],
                    "performed_at": mh.performed_at.isoformat() if mh.performed_at else None,
                    "hours_at_maintenance": mh.hours_at_maintenance,
                    "notes": mh.notes,
                }
            )
        backup["included"].append("maintenance_history")

    # Print queue
    if include_print_queue:
        result = await db.execute(select(PrintQueueItem))
        queue_items = result.scalars().all()
        backup["print_queue"] = []

        # Build lookups
        printer_id_to_serial: dict[int, str] = {}
        archive_id_to_hash: dict[int, str | None] = {}
        project_id_to_name: dict[int, str] = {}

        pr_result = await db.execute(select(Printer))
        for pr in pr_result.scalars().all():
            printer_id_to_serial[pr.id] = pr.serial_number
        ar_result = await db.execute(select(PrintArchive))
        for ar in ar_result.scalars().all():
            archive_id_to_hash[ar.id] = ar.content_hash
        proj_result = await db.execute(select(Project))
        for proj in proj_result.scalars().all():
            project_id_to_name[proj.id] = proj.name

        for qi in queue_items:
            backup["print_queue"].append(
                {
                    "printer_serial": printer_id_to_serial.get(qi.printer_id) if qi.printer_id else None,
                    "archive_hash": archive_id_to_hash.get(qi.archive_id),
                    "project_name": project_id_to_name.get(qi.project_id) if qi.project_id else None,
                    "position": qi.position,
                    "scheduled_time": qi.scheduled_time.isoformat() if qi.scheduled_time else None,
                    "require_previous_success": qi.require_previous_success,
                    "auto_off_after": qi.auto_off_after,
                    "manual_start": qi.manual_start,
                    "ams_mapping": qi.ams_mapping,
                    "plate_id": qi.plate_id,
                    "bed_levelling": qi.bed_levelling,
                    "flow_cali": qi.flow_cali,
                    "vibration_cali": qi.vibration_cali,
                    "layer_inspect": qi.layer_inspect,
                    "timelapse": qi.timelapse,
                    "use_ams": qi.use_ams,
                    "status": qi.status,
                    "started_at": qi.started_at.isoformat() if qi.started_at else None,
                    "completed_at": qi.completed_at.isoformat() if qi.completed_at else None,
                    "error_message": qi.error_message,
                }
            )
        backup["included"].append("print_queue")

    # Collect files for ZIP (icons + archives + project attachments)
    backup_files: list[tuple[str, Path]] = []  # (zip_path, local_path)
    base_dir = app_settings.base_dir

    # Add external link icon files
    if include_external_links and "external_links" in backup:
        icons_dir = base_dir / "icons"
        for link_data in backup["external_links"]:
            if "custom_icon_path" in link_data:
                icon_path = icons_dir / link_data["custom_icon"]
                if icon_path.exists():
                    backup_files.append((link_data["custom_icon_path"], icon_path))

    # Add plate calibration reference images
    if "plate_calibration" in backup:
        plate_cal_dir = app_settings.plate_calibration_dir
        plate_cal_data = backup["plate_calibration"]
        # Support both old list format and new dict format
        filenames = plate_cal_data.get("files", []) if isinstance(plate_cal_data, dict) else plate_cal_data
        for filename in filenames:
            file_path = plate_cal_dir / filename
            if file_path.exists():
                backup_files.append((f"plate_calibration/{filename}", file_path))

    # Print archives with file paths for ZIP
    if include_archives:
        result = await db.execute(select(PrintArchive))
        archives = result.scalars().all()
        backup["archives"] = []

        # Build project ID to name mapping for archive export
        project_id_to_name: dict[int, str] = {}
        if include_projects:
            proj_result = await db.execute(select(Project))
            for proj in proj_result.scalars().all():
                project_id_to_name[proj.id] = proj.name

        # Build printer ID to serial mapping for archive export
        printer_id_to_serial: dict[int, str] = {}
        if include_printers:
            printer_result = await db.execute(select(Printer))
            for pr in printer_result.scalars().all():
                printer_id_to_serial[pr.id] = pr.serial_number

        for a in archives:
            archive_data = {
                "filename": a.filename,
                "project_name": project_id_to_name.get(a.project_id) if a.project_id else None,
                "printer_serial": printer_id_to_serial.get(a.printer_id) if a.printer_id else None,
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
                "external_url": a.external_url,
                "is_favorite": a.is_favorite,
                "tags": a.tags,
                "notes": a.notes,
                "cost": a.cost,
                "failure_reason": a.failure_reason,
                "quantity": a.quantity,
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
                    backup_files.append((a.file_path, file_path))

            if a.thumbnail_path:
                thumb_path = base_dir / a.thumbnail_path
                if thumb_path.exists():
                    archive_data["thumbnail_path"] = a.thumbnail_path
                    backup_files.append((a.thumbnail_path, thumb_path))

            if a.timelapse_path:
                timelapse_path = base_dir / a.timelapse_path
                if timelapse_path.exists():
                    archive_data["timelapse_path"] = a.timelapse_path
                    backup_files.append((a.timelapse_path, timelapse_path))

            if a.source_3mf_path:
                source_path = base_dir / a.source_3mf_path
                if source_path.exists():
                    archive_data["source_3mf_path"] = a.source_3mf_path
                    backup_files.append((a.source_3mf_path, source_path))

            if a.f3d_path:
                f3d_path = base_dir / a.f3d_path
                if f3d_path.exists():
                    archive_data["f3d_path"] = a.f3d_path
                    backup_files.append((a.f3d_path, f3d_path))

            # Include photos
            if a.photos:
                for photo in a.photos:
                    photo_path = base_dir / "archive" / "photos" / photo
                    if photo_path.exists():
                        zip_photo_path = f"archive/photos/{photo}"
                        backup_files.append((zip_photo_path, photo_path))

            backup["archives"].append(archive_data)
        backup["included"].append("archives")

    # Projects with BOM items
    if include_projects:
        result = await db.execute(select(Project))
        projects = result.scalars().all()
        backup["projects"] = []

        for p in projects:
            # Get BOM items for this project
            bom_result = await db.execute(select(ProjectBOMItem).where(ProjectBOMItem.project_id == p.id))
            bom_items = bom_result.scalars().all()

            project_data = {
                "name": p.name,
                "description": p.description,
                "color": p.color,
                "status": p.status,
                "target_count": p.target_count,
                "notes": p.notes,
                "tags": p.tags,
                "due_date": p.due_date.isoformat() if p.due_date else None,
                "priority": p.priority,
                "budget": p.budget,
                "is_template": p.is_template,
                "template_source_id": p.template_source_id,
                "parent_id": p.parent_id,
                "bom_items": [
                    {
                        "name": item.name,
                        "quantity_needed": item.quantity_needed,
                        "quantity_acquired": item.quantity_acquired,
                        "unit_price": item.unit_price,
                        "sourcing_url": item.sourcing_url,
                        "stl_filename": item.stl_filename,
                        "remarks": item.remarks,
                        "sort_order": item.sort_order,
                    }
                    for item in bom_items
                ],
            }

            # Include attachment files for ZIP
            if p.attachments:
                project_data["attachments"] = p.attachments
                attachments_dir = base_dir / "projects" / str(p.id) / "attachments"
                for att in p.attachments:
                    att_path = attachments_dir / att.get("filename", "")
                    if att_path.exists():
                        zip_path = f"projects/{p.id}/attachments/{att['filename']}"
                        backup_files.append((zip_path, att_path))

            backup["projects"].append(project_data)
        backup["included"].append("projects")

    # Pending uploads (virtual printer queue mode)
    if include_pending_uploads:
        result = await db.execute(select(PendingUpload).where(PendingUpload.status == "pending"))
        pending_uploads = result.scalars().all()
        backup["pending_uploads"] = []

        for p in pending_uploads:
            upload_data = {
                "filename": p.filename,
                "file_size": p.file_size,
                "source_ip": p.source_ip,
                "status": p.status,
                "tags": p.tags,
                "notes": p.notes,
                "project_id": p.project_id,
                "uploaded_at": p.uploaded_at.isoformat() if p.uploaded_at else None,
            }

            # Include the actual file if it exists
            if p.file_path:
                file_path = Path(p.file_path)
                if file_path.exists():
                    # Store relative path for ZIP
                    rel_path = f"pending_uploads/{p.filename}"
                    upload_data["file_path"] = rel_path
                    backup_files.append((rel_path, file_path))

            backup["pending_uploads"].append(upload_data)
        backup["included"].append("pending_uploads")

    # API keys (note: key_hash cannot be restored, new keys must be generated)
    if include_api_keys:
        # Build printer ID to serial mapping for cross-system compatibility
        printer_id_to_serial: dict[int, str] = {}
        pr_result = await db.execute(select(Printer))
        for pr in pr_result.scalars().all():
            printer_id_to_serial[pr.id] = pr.serial_number

        result = await db.execute(select(APIKey))
        api_keys = result.scalars().all()
        backup["api_keys"] = []
        for key in api_keys:
            # Convert printer_ids from list of IDs to list of serials
            printer_serials = None
            if key.printer_ids:
                printer_serials = [
                    printer_id_to_serial.get(pid) for pid in key.printer_ids if pid in printer_id_to_serial
                ]

            backup["api_keys"].append(
                {
                    "name": key.name,
                    "key_prefix": key.key_prefix,  # For identification only
                    "can_queue": key.can_queue,
                    "can_control_printer": key.can_control_printer,
                    "can_read_status": key.can_read_status,
                    "printer_serials": printer_serials,  # Use serials instead of IDs
                    "enabled": key.enabled,
                    "expires_at": key.expires_at.isoformat() if key.expires_at else None,
                }
            )
        backup["included"].append("api_keys")

    # Users (note: passwords not exported for security - users will need new passwords on import)
    if include_users:
        result = await db.execute(select(User))
        users = result.scalars().all()
        backup["users"] = []
        for user in users:
            backup["users"].append(
                {
                    "username": user.username,
                    "role": user.role,
                    "is_active": user.is_active,
                    # password_hash intentionally not exported for security
                }
            )
        backup["included"].append("users")

    # GitHub backup configuration
    if include_github_backup:
        result = await db.execute(select(GitHubBackupConfig).limit(1))
        config = result.scalar_one_or_none()
        if config:
            backup["github_backup"] = {
                "repository_url": config.repository_url,
                # access_token intentionally not exported for security
                "branch": config.branch,
                "schedule_enabled": config.schedule_enabled,
                "schedule_type": config.schedule_type,
                "backup_kprofiles": config.backup_kprofiles,
                "backup_cloud_profiles": config.backup_cloud_profiles,
                "backup_settings": config.backup_settings,
                "enabled": config.enabled,
            }
            backup["included"].append("github_backup")

    # If there are files to include (icons or archives), create ZIP file
    if backup_files:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            # Add backup.json
            zf.writestr("backup.json", json.dumps(backup, indent=2))

            # Add all backup files (icons, archives, etc.)
            added_files = set()
            for zip_path, local_path in backup_files:
                if zip_path not in added_files and local_path.exists():
                    try:
                        zf.write(local_path, zip_path)
                        added_files.add(zip_path)
                    except Exception:
                        pass  # Skip files that can't be read

        zip_buffer.seek(0)
        filename = f"bambuddy-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    # Otherwise return JSON
    return JSONResponse(
        content=backup,
        headers={
            "Content-Disposition": f"attachment; filename=bambuddy-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        },
    )


@router.post("/restore")
async def import_backup(
    file: UploadFile = File(...),
    overwrite: bool = Query(False, description="Overwrite existing data instead of skipping duplicates"),
    db: AsyncSession = Depends(get_db),
):
    """Restore data from JSON or ZIP backup. By default skips duplicates, set overwrite=true to replace existing."""
    try:
        content = await file.read()
        base_dir = app_settings.base_dir
        files_restored = 0
        # Store plate calibration files for later (need printer ID remapping after printers restored)
        plate_cal_files: dict[str, bytes] = {}

        # Check if it's a ZIP file
        if file.filename and file.filename.endswith(".zip"):
            try:
                zip_buffer = io.BytesIO(content)
                with zipfile.ZipFile(zip_buffer, "r") as zf:
                    # Extract backup.json
                    if "backup.json" not in zf.namelist():
                        return {"success": False, "message": "Invalid ZIP: missing backup.json"}

                    backup_content = zf.read("backup.json")
                    backup = json.loads(backup_content.decode("utf-8"))

                    # Extract all other files to base_dir
                    for zip_path in zf.namelist():
                        if zip_path == "backup.json":
                            continue
                        # Ensure path is safe (no path traversal)
                        if ".." in zip_path or zip_path.startswith("/"):
                            continue
                        # Plate calibration files - store for later processing after printers are restored
                        if zip_path.startswith("plate_calibration/"):
                            filename = zip_path.replace("plate_calibration/", "", 1)
                            if filename:  # Skip directory entries
                                plate_cal_files[filename] = zf.read(zip_path)
                            continue
                        target_path = base_dir / zip_path
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(zip_path) as src, open(target_path, "wb") as dst:
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
        "external_links": 0,
        "printers": 0,
        "filaments": 0,
        "maintenance_types": 0,
        "projects": 0,
        "pending_uploads": 0,
        "users": 0,
        "github_backup": 0,
    }
    skipped = {
        "settings": 0,
        "notification_providers": 0,
        "notification_templates": 0,
        "smart_plugs": 0,
        "external_links": 0,
        "printers": 0,
        "filaments": 0,
        "maintenance_types": 0,
        "archives": 0,
        "projects": 0,
        "pending_uploads": 0,
        "users": 0,
        "github_backup": 0,
    }
    skipped_details = {
        "notification_providers": [],
        "smart_plugs": [],
        "external_links": [],
        "printers": [],
        "filaments": [],
        "maintenance_types": [],
        "archives": [],
        "projects": [],
        "pending_uploads": [],
        "users": [],
    }

    # Restore settings (always overwrites)
    if "settings" in backup:
        for key, value in backup["settings"].items():
            # Convert value to proper string format for storage
            if isinstance(value, bool):
                str_value = "true" if value else "false"
            elif value is None:
                str_value = "None"
            else:
                str_value = str(value)
            await set_setting(db, key, str_value)
            restored["settings"] += 1
        # Flush settings to ensure they're persisted before continuing
        await db.flush()

    # Restore printers FIRST (skip or overwrite duplicates by serial_number)
    # Nearly everything in the app references printers, so they must be imported first
    if "printers" in backup:
        for printer_data in backup["printers"]:
            result = await db.execute(select(Printer).where(Printer.serial_number == printer_data["serial_number"]))
            existing = result.scalar_one_or_none()
            if existing:
                if overwrite:
                    existing.name = printer_data["name"]
                    existing.ip_address = printer_data["ip_address"]
                    existing.model = printer_data.get("model")
                    existing.location = printer_data.get("location")
                    existing.nozzle_count = printer_data.get("nozzle_count", 1)
                    existing.auto_archive = printer_data.get("auto_archive", True)
                    existing.print_hours_offset = printer_data.get("print_hours_offset", 0.0)
                    existing.runtime_seconds = printer_data.get("runtime_seconds", 0)

                    # If backup includes access_code, also update access_code and is_active
                    backup_access_code = printer_data.get("access_code")
                    if backup_access_code and backup_access_code != "CHANGE_ME":
                        existing.access_code = backup_access_code
                        is_active_val = printer_data.get("is_active", False)
                        if isinstance(is_active_val, str):
                            is_active_val = is_active_val.lower() == "true"
                        existing.is_active = is_active_val

                    # Restore external camera settings
                    existing.external_camera_url = printer_data.get("external_camera_url")
                    existing.external_camera_type = printer_data.get("external_camera_type")
                    existing.external_camera_enabled = printer_data.get("external_camera_enabled", False)

                    # Restore plate detection settings
                    existing.plate_detection_enabled = printer_data.get("plate_detection_enabled", False)
                    existing.plate_detection_roi_x = printer_data.get("plate_detection_roi_x")
                    existing.plate_detection_roi_y = printer_data.get("plate_detection_roi_y")
                    existing.plate_detection_roi_w = printer_data.get("plate_detection_roi_w")
                    existing.plate_detection_roi_h = printer_data.get("plate_detection_roi_h")

                    restored["printers"] += 1
                else:
                    skipped["printers"] += 1
                    skipped_details["printers"].append(f"{printer_data['name']} ({printer_data['serial_number']})")
            else:
                # Use access code from backup if provided, otherwise require manual setup
                access_code = printer_data.get("access_code")
                has_access_code = access_code and access_code != "CHANGE_ME"
                is_active_from_backup = printer_data.get("is_active", False)
                # Handle bool or string "true"/"false"
                if isinstance(is_active_from_backup, str):
                    is_active_from_backup = is_active_from_backup.lower() == "true"

                printer = Printer(
                    name=printer_data["name"],
                    serial_number=printer_data["serial_number"],
                    ip_address=printer_data["ip_address"],
                    access_code=access_code if has_access_code else "CHANGE_ME",
                    model=printer_data.get("model"),
                    location=printer_data.get("location"),
                    nozzle_count=printer_data.get("nozzle_count", 1),
                    is_active=is_active_from_backup if has_access_code else False,
                    auto_archive=printer_data.get("auto_archive", True),
                    print_hours_offset=printer_data.get("print_hours_offset", 0.0),
                    runtime_seconds=printer_data.get("runtime_seconds", 0),
                    external_camera_url=printer_data.get("external_camera_url"),
                    external_camera_type=printer_data.get("external_camera_type"),
                    external_camera_enabled=printer_data.get("external_camera_enabled", False),
                    plate_detection_enabled=printer_data.get("plate_detection_enabled", False),
                    plate_detection_roi_x=printer_data.get("plate_detection_roi_x"),
                    plate_detection_roi_y=printer_data.get("plate_detection_roi_y"),
                    plate_detection_roi_w=printer_data.get("plate_detection_roi_w"),
                    plate_detection_roi_h=printer_data.get("plate_detection_roi_h"),
                )
                db.add(printer)
                restored["printers"] += 1
        # Flush printers so other sections can look them up
        await db.flush()

    # Restore plate calibration files (remap printer IDs based on serial numbers)
    if plate_cal_files:
        # Build serial_number -> new_printer_id mapping
        serial_to_new_id: dict[str, int] = {}
        pr_result = await db.execute(select(Printer))
        for pr in pr_result.scalars().all():
            serial_to_new_id[pr.serial_number] = pr.id

        # Get old_id -> serial mapping from backup (supports both old list format and new dict format)
        plate_cal_data = backup.get("plate_calibration", {})
        if isinstance(plate_cal_data, dict):
            old_id_to_serial: dict[int, str | None] = {
                int(k): v for k, v in plate_cal_data.get("printer_id_to_serial", {}).items()
            }
        else:
            old_id_to_serial = {}

        # Build old_id -> new_id mapping
        old_id_to_new_id: dict[int, int] = {}
        for old_id, serial in old_id_to_serial.items():
            if serial and serial in serial_to_new_id:
                old_id_to_new_id[old_id] = serial_to_new_id[serial]

        app_settings.plate_calibration_dir.mkdir(parents=True, exist_ok=True)

        for filename, file_data in plate_cal_files.items():
            # Parse old printer ID from filename (e.g., "printer_3_ref_0.jpg" -> 3)
            new_filename = filename
            if filename.startswith("printer_"):
                parts = filename.split("_")
                if len(parts) >= 2 and parts[1].isdigit():
                    old_printer_id = int(parts[1])
                    if old_printer_id in old_id_to_new_id:
                        new_printer_id = old_id_to_new_id[old_printer_id]
                        # Replace old ID with new ID in filename
                        new_filename = filename.replace(f"printer_{old_printer_id}_", f"printer_{new_printer_id}_", 1)

            target_path = app_settings.plate_calibration_dir / new_filename
            with open(target_path, "wb") as f:
                f.write(file_data)
            files_restored += 1

    # Restore notification providers (skip or overwrite duplicates by name)
    # Build printer serial to ID lookup (printers were restored first)
    if "notification_providers" in backup:
        printer_serial_to_id: dict[str, int] = {}
        pr_result = await db.execute(select(Printer))
        for pr in pr_result.scalars().all():
            printer_serial_to_id[pr.serial_number] = pr.id

        for provider_data in backup["notification_providers"]:
            # Look up printer_id from serial (supports both old printer_id and new printer_serial format)
            printer_serial = provider_data.get("printer_serial")
            printer_id = printer_serial_to_id.get(printer_serial) if printer_serial else provider_data.get("printer_id")

            result = await db.execute(
                select(NotificationProvider).where(NotificationProvider.name == provider_data["name"])
            )
            existing = result.scalar_one_or_none()
            if existing:
                if overwrite:
                    # Update existing provider
                    existing.provider_type = provider_data["provider_type"]
                    existing.enabled = provider_data.get("enabled", True)
                    existing.config = json.dumps(provider_data.get("config", {}))
                    existing.on_print_start = provider_data.get("on_print_start", False)
                    existing.on_print_complete = provider_data.get("on_print_complete", True)
                    existing.on_print_failed = provider_data.get("on_print_failed", True)
                    existing.on_print_stopped = provider_data.get("on_print_stopped", True)
                    existing.on_print_progress = provider_data.get("on_print_progress", False)
                    existing.on_printer_offline = provider_data.get("on_printer_offline", False)
                    existing.on_printer_error = provider_data.get("on_printer_error", False)
                    existing.on_filament_low = provider_data.get("on_filament_low", False)
                    existing.on_maintenance_due = provider_data.get("on_maintenance_due", False)
                    existing.on_ams_humidity_high = provider_data.get("on_ams_humidity_high", False)
                    existing.on_ams_temperature_high = provider_data.get("on_ams_temperature_high", False)
                    existing.on_ams_ht_humidity_high = provider_data.get("on_ams_ht_humidity_high", False)
                    existing.on_ams_ht_temperature_high = provider_data.get("on_ams_ht_temperature_high", False)
                    existing.on_plate_not_empty = provider_data.get("on_plate_not_empty", True)
                    existing.quiet_hours_enabled = provider_data.get("quiet_hours_enabled", False)
                    existing.quiet_hours_start = provider_data.get("quiet_hours_start")
                    existing.quiet_hours_end = provider_data.get("quiet_hours_end")
                    existing.daily_digest_enabled = provider_data.get("daily_digest_enabled", False)
                    existing.daily_digest_time = provider_data.get("daily_digest_time")
                    existing.printer_id = printer_id
                    restored["notification_providers"] += 1
                else:
                    skipped["notification_providers"] += 1
                    skipped_details["notification_providers"].append(provider_data["name"])
            else:
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
                    on_ams_humidity_high=provider_data.get("on_ams_humidity_high", False),
                    on_ams_temperature_high=provider_data.get("on_ams_temperature_high", False),
                    on_ams_ht_humidity_high=provider_data.get("on_ams_ht_humidity_high", False),
                    on_ams_ht_temperature_high=provider_data.get("on_ams_ht_temperature_high", False),
                    on_plate_not_empty=provider_data.get("on_plate_not_empty", True),
                    quiet_hours_enabled=provider_data.get("quiet_hours_enabled", False),
                    quiet_hours_start=provider_data.get("quiet_hours_start"),
                    quiet_hours_end=provider_data.get("quiet_hours_end"),
                    daily_digest_enabled=provider_data.get("daily_digest_enabled", False),
                    daily_digest_time=provider_data.get("daily_digest_time"),
                    printer_id=printer_id,
                )
                db.add(provider)
                restored["notification_providers"] += 1

    # Restore notification templates (update existing by event_type)
    if "notification_templates" in backup:
        for template_data in backup["notification_templates"]:
            result = await db.execute(
                select(NotificationTemplate).where(NotificationTemplate.event_type == template_data["event_type"])
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

    # Restore smart plugs (skip or overwrite duplicates by IP)
    # Note: Smart plugs reference printers, so printers should be restored first
    if "smart_plugs" in backup:
        # Build printer serial to ID lookup
        printer_serial_to_id: dict[str, int] = {}
        pr_result = await db.execute(select(Printer))
        for pr in pr_result.scalars().all():
            printer_serial_to_id[pr.serial_number] = pr.id

        for plug_data in backup["smart_plugs"]:
            # Look up printer_id from serial (supports both old printer_id and new printer_serial format)
            printer_serial = plug_data.get("printer_serial")
            printer_id = printer_serial_to_id.get(printer_serial) if printer_serial else plug_data.get("printer_id")

            # Determine plug type (default to tasmota for backwards compatibility)
            plug_type = plug_data.get("plug_type", "tasmota")

            # Find existing plug by IP (Tasmota) or entity_id (Home Assistant)
            existing = None
            if plug_type == "homeassistant" and plug_data.get("ha_entity_id"):
                result = await db.execute(select(SmartPlug).where(SmartPlug.ha_entity_id == plug_data["ha_entity_id"]))
                existing = result.scalar_one_or_none()
                plug_identifier = plug_data["ha_entity_id"]
            elif plug_data.get("ip_address"):
                result = await db.execute(select(SmartPlug).where(SmartPlug.ip_address == plug_data["ip_address"]))
                existing = result.scalar_one_or_none()
                plug_identifier = plug_data["ip_address"]
            else:
                # Skip invalid plug data
                continue

            if existing:
                if overwrite:
                    existing.name = plug_data["name"]
                    existing.plug_type = plug_type
                    existing.ha_entity_id = plug_data.get("ha_entity_id")
                    existing.ha_power_entity = plug_data.get("ha_power_entity")
                    existing.ha_energy_today_entity = plug_data.get("ha_energy_today_entity")
                    existing.ha_energy_total_entity = plug_data.get("ha_energy_total_entity")
                    existing.printer_id = printer_id
                    existing.enabled = plug_data.get("enabled", True)
                    existing.auto_on = plug_data.get("auto_on", True)
                    existing.auto_off = plug_data.get("auto_off", True)
                    existing.off_delay_mode = plug_data.get("off_delay_mode", "time")
                    existing.off_delay_minutes = plug_data.get("off_delay_minutes", 5)
                    existing.off_temp_threshold = plug_data.get("off_temp_threshold", 70)
                    existing.username = plug_data.get("username")
                    existing.password = plug_data.get("password")
                    existing.power_alert_enabled = plug_data.get("power_alert_enabled", False)
                    existing.power_alert_high = plug_data.get("power_alert_high")
                    existing.power_alert_low = plug_data.get("power_alert_low")
                    existing.schedule_enabled = plug_data.get("schedule_enabled", False)
                    existing.schedule_on_time = plug_data.get("schedule_on_time")
                    existing.schedule_off_time = plug_data.get("schedule_off_time")
                    existing.show_in_switchbar = plug_data.get("show_in_switchbar", False)
                    restored["smart_plugs"] += 1
                else:
                    skipped["smart_plugs"] += 1
                    skipped_details["smart_plugs"].append(f"{plug_data['name']} ({plug_identifier})")
            else:
                plug = SmartPlug(
                    name=plug_data["name"],
                    plug_type=plug_type,
                    ip_address=plug_data.get("ip_address"),
                    ha_entity_id=plug_data.get("ha_entity_id"),
                    ha_power_entity=plug_data.get("ha_power_entity"),
                    ha_energy_today_entity=plug_data.get("ha_energy_today_entity"),
                    ha_energy_total_entity=plug_data.get("ha_energy_total_entity"),
                    printer_id=printer_id,
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
                    show_in_switchbar=plug_data.get("show_in_switchbar", False),
                )
                db.add(plug)
                restored["smart_plugs"] += 1

    # Restore external links (skip or overwrite duplicates by name+url)
    if "external_links" in backup:
        icons_dir = base_dir / "icons"
        icons_dir.mkdir(parents=True, exist_ok=True)

        for link_data in backup["external_links"]:
            result = await db.execute(
                select(ExternalLink).where(ExternalLink.name == link_data["name"], ExternalLink.url == link_data["url"])
            )
            existing = result.scalar_one_or_none()
            if existing:
                if overwrite:
                    existing.icon = link_data.get("icon", "link")
                    existing.sort_order = link_data.get("sort_order", 0)
                    # Handle custom icon
                    if link_data.get("custom_icon"):
                        existing.custom_icon = link_data["custom_icon"]
                    restored["external_links"] += 1
                else:
                    skipped["external_links"] += 1
                    skipped_details["external_links"].append(link_data["name"])
            else:
                link = ExternalLink(
                    name=link_data["name"],
                    url=link_data["url"],
                    icon=link_data.get("icon", "link"),
                    custom_icon=link_data.get("custom_icon"),
                    sort_order=link_data.get("sort_order", 0),
                )
                db.add(link)
                restored["external_links"] += 1

    # Restore filaments (skip or overwrite duplicates by name+type+brand)
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
            if existing:
                if overwrite:
                    existing.color = filament_data.get("color")
                    existing.color_hex = filament_data.get("color_hex")
                    existing.cost_per_kg = filament_data.get("cost_per_kg", 25.0)
                    existing.spool_weight_g = filament_data.get("spool_weight_g", 1000.0)
                    existing.currency = filament_data.get("currency", "USD")
                    existing.density = filament_data.get("density")
                    existing.print_temp_min = filament_data.get("print_temp_min")
                    existing.print_temp_max = filament_data.get("print_temp_max")
                    existing.bed_temp_min = filament_data.get("bed_temp_min")
                    existing.bed_temp_max = filament_data.get("bed_temp_max")
                    restored["filaments"] += 1
                else:
                    skipped["filaments"] += 1
                    skipped_details["filaments"].append(
                        f"{filament_data.get('brand', '')} {filament_data['name']} ({filament_data['type']})"
                    )
            else:
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

    # Restore maintenance types (skip or overwrite duplicates by name)
    if "maintenance_types" in backup:
        for mt_data in backup["maintenance_types"]:
            result = await db.execute(select(MaintenanceType).where(MaintenanceType.name == mt_data["name"]))
            existing = result.scalar_one_or_none()
            if existing:
                if overwrite:
                    existing.description = mt_data.get("description")
                    existing.default_interval_hours = mt_data.get("default_interval_hours", 100.0)
                    existing.interval_type = mt_data.get("interval_type", "hours")
                    existing.icon = mt_data.get("icon")
                    # Don't overwrite is_system
                    restored["maintenance_types"] += 1
                else:
                    skipped["maintenance_types"] += 1
                    skipped_details["maintenance_types"].append(mt_data["name"])
            else:
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

    # Restore printer maintenance settings (per-printer)
    if "printer_maintenance" in backup:
        # Build lookups
        printer_serial_to_id: dict[str, int] = {}
        maint_type_name_to_id: dict[str, int] = {}

        pr_result = await db.execute(select(Printer))
        for pr in pr_result.scalars().all():
            printer_serial_to_id[pr.serial_number] = pr.id

        mt_result = await db.execute(select(MaintenanceType))
        for mt in mt_result.scalars().all():
            maint_type_name_to_id[mt.name] = mt.id

        restored["printer_maintenance"] = 0
        skipped["printer_maintenance"] = 0
        skipped_details["printer_maintenance"] = []

        for pm_data in backup["printer_maintenance"]:
            printer_serial = pm_data.get("printer_serial")
            maint_type_name = pm_data.get("maintenance_type_name")

            if not printer_serial or not maint_type_name:
                continue

            printer_id = printer_serial_to_id.get(printer_serial)
            maint_type_id = maint_type_name_to_id.get(maint_type_name)

            if not printer_id or not maint_type_id:
                skipped["printer_maintenance"] += 1
                skipped_details["printer_maintenance"].append(f"{printer_serial}/{maint_type_name}")
                continue

            # Check if exists
            result = await db.execute(
                select(PrinterMaintenance).where(
                    PrinterMaintenance.printer_id == printer_id,
                    PrinterMaintenance.maintenance_type_id == maint_type_id,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                if overwrite:
                    existing.custom_interval_hours = pm_data.get("custom_interval_hours")
                    existing.custom_interval_type = pm_data.get("custom_interval_type")
                    existing.enabled = pm_data.get("enabled", True)
                    existing.last_performed_hours = pm_data.get("last_performed_hours", 0.0)
                    if pm_data.get("last_performed_at"):
                        existing.last_performed_at = datetime.fromisoformat(pm_data["last_performed_at"])
                    restored["printer_maintenance"] += 1
                else:
                    skipped["printer_maintenance"] += 1
                    skipped_details["printer_maintenance"].append(f"{printer_serial}/{maint_type_name}")
            else:
                pm = PrinterMaintenance(
                    printer_id=printer_id,
                    maintenance_type_id=maint_type_id,
                    custom_interval_hours=pm_data.get("custom_interval_hours"),
                    custom_interval_type=pm_data.get("custom_interval_type"),
                    enabled=pm_data.get("enabled", True),
                    last_performed_hours=pm_data.get("last_performed_hours", 0.0),
                )
                if pm_data.get("last_performed_at"):
                    pm.last_performed_at = datetime.fromisoformat(pm_data["last_performed_at"])
                db.add(pm)
                restored["printer_maintenance"] += 1

    # Restore maintenance history
    if "maintenance_history" in backup:
        # Build lookups
        printer_serial_to_id: dict[str, int] = {}
        maint_type_name_to_id: dict[str, int] = {}

        pr_result = await db.execute(select(Printer))
        for pr in pr_result.scalars().all():
            printer_serial_to_id[pr.serial_number] = pr.id

        mt_result = await db.execute(select(MaintenanceType))
        for mt in mt_result.scalars().all():
            maint_type_name_to_id[mt.name] = mt.id

        restored["maintenance_history"] = 0
        skipped["maintenance_history"] = 0
        skipped_details["maintenance_history"] = []

        for mh_data in backup["maintenance_history"]:
            printer_serial = mh_data.get("printer_serial")
            maint_type_name = mh_data.get("maintenance_type_name")

            if not printer_serial or not maint_type_name:
                continue

            printer_id = printer_serial_to_id.get(printer_serial)
            maint_type_id = maint_type_name_to_id.get(maint_type_name)

            if not printer_id or not maint_type_id:
                skipped["maintenance_history"] += 1
                continue

            # Find the PrinterMaintenance record
            result = await db.execute(
                select(PrinterMaintenance).where(
                    PrinterMaintenance.printer_id == printer_id,
                    PrinterMaintenance.maintenance_type_id == maint_type_id,
                )
            )
            pm = result.scalar_one_or_none()

            if not pm:
                skipped["maintenance_history"] += 1
                continue

            # Create history entry (no duplicate check - history is append-only)
            mh = MaintenanceHistory(
                printer_maintenance_id=pm.id,
                hours_at_maintenance=mh_data.get("hours_at_maintenance", 0.0),
                notes=mh_data.get("notes"),
            )
            if mh_data.get("performed_at"):
                mh.performed_at = datetime.fromisoformat(mh_data["performed_at"])
            db.add(mh)
            restored["maintenance_history"] += 1

    # Restore archives (skip duplicates by content_hash - overwrite not supported for archives)
    if "archives" in backup:
        # Build printer serial to ID mapping
        printer_serial_to_id: dict[str, int] = {}
        printer_result = await db.execute(select(Printer))
        for pr in printer_result.scalars().all():
            printer_serial_to_id[pr.serial_number] = pr.id

        for archive_data in backup["archives"]:
            # Skip if no content_hash or already exists
            content_hash = archive_data.get("content_hash")
            if content_hash:
                result = await db.execute(select(PrintArchive).where(PrintArchive.content_hash == content_hash))
                existing = result.scalar_one_or_none()
                if existing:
                    skipped["archives"] += 1
                    skipped_details["archives"].append(archive_data.get("filename", "Unknown"))
                    continue

            # Only restore if file exists (from ZIP extraction)
            file_path = archive_data.get("file_path")
            if file_path and (base_dir / file_path).exists():
                # Look up printer_id from serial
                printer_serial = archive_data.get("printer_serial")
                printer_id = printer_serial_to_id.get(printer_serial) if printer_serial else None

                archive = PrintArchive(
                    filename=archive_data["filename"],
                    file_path=file_path,
                    file_size=archive_data.get("file_size", 0),
                    content_hash=content_hash,
                    printer_id=printer_id,
                    thumbnail_path=archive_data.get("thumbnail_path"),
                    timelapse_path=archive_data.get("timelapse_path"),
                    source_3mf_path=archive_data.get("source_3mf_path"),
                    f3d_path=archive_data.get("f3d_path"),
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
                    external_url=archive_data.get("external_url"),
                    is_favorite=archive_data.get("is_favorite", False),
                    tags=archive_data.get("tags"),
                    notes=archive_data.get("notes"),
                    cost=archive_data.get("cost"),
                    failure_reason=archive_data.get("failure_reason"),
                    quantity=archive_data.get("quantity", 1),
                    energy_kwh=archive_data.get("energy_kwh"),
                    energy_cost=archive_data.get("energy_cost"),
                    extra_data=archive_data.get("extra_data"),
                    photos=archive_data.get("photos"),
                )
                db.add(archive)
                restored["archives"] = restored.get("archives", 0) + 1

    # Restore projects (skip or overwrite duplicates by name)
    if "projects" in backup:
        for project_data in backup["projects"]:
            result = await db.execute(select(Project).where(Project.name == project_data["name"]))
            existing = result.scalar_one_or_none()
            if existing:
                if overwrite:
                    # Update existing project
                    existing.description = project_data.get("description")
                    existing.color = project_data.get("color")
                    existing.status = project_data.get("status", "active")
                    existing.target_count = project_data.get("target_count")
                    existing.notes = project_data.get("notes")
                    existing.tags = project_data.get("tags")
                    existing.priority = project_data.get("priority", "normal")
                    existing.budget = project_data.get("budget")
                    existing.is_template = project_data.get("is_template", False)
                    existing.template_source_id = project_data.get("template_source_id")
                    existing.parent_id = project_data.get("parent_id")
                    existing.attachments = project_data.get("attachments")
                    if project_data.get("due_date"):
                        existing.due_date = datetime.fromisoformat(project_data["due_date"])

                    # Delete existing BOM items and re-add
                    await db.execute(ProjectBOMItem.__table__.delete().where(ProjectBOMItem.project_id == existing.id))
                    for bom_data in project_data.get("bom_items", []):
                        bom_item = ProjectBOMItem(
                            project_id=existing.id,
                            name=bom_data["name"],
                            quantity_needed=bom_data.get("quantity_needed", 1),
                            quantity_acquired=bom_data.get("quantity_acquired", 0),
                            unit_price=bom_data.get("unit_price"),
                            sourcing_url=bom_data.get("sourcing_url"),
                            stl_filename=bom_data.get("stl_filename"),
                            remarks=bom_data.get("remarks"),
                            sort_order=bom_data.get("sort_order", 0),
                        )
                        db.add(bom_item)

                    restored["projects"] += 1
                else:
                    skipped["projects"] += 1
                    skipped_details["projects"].append(project_data["name"])
            else:
                # Create new project
                project = Project(
                    name=project_data["name"],
                    description=project_data.get("description"),
                    color=project_data.get("color"),
                    status=project_data.get("status", "active"),
                    target_count=project_data.get("target_count"),
                    notes=project_data.get("notes"),
                    tags=project_data.get("tags"),
                    priority=project_data.get("priority", "normal"),
                    budget=project_data.get("budget"),
                    is_template=project_data.get("is_template", False),
                    template_source_id=project_data.get("template_source_id"),
                    parent_id=project_data.get("parent_id"),
                    attachments=project_data.get("attachments"),
                )
                if project_data.get("due_date"):
                    project.due_date = datetime.fromisoformat(project_data["due_date"])

                db.add(project)
                await db.flush()  # Get the project ID

                # Add BOM items
                for bom_data in project_data.get("bom_items", []):
                    bom_item = ProjectBOMItem(
                        project_id=project.id,
                        name=bom_data["name"],
                        quantity_needed=bom_data.get("quantity_needed", 1),
                        quantity_acquired=bom_data.get("quantity_acquired", 0),
                        unit_price=bom_data.get("unit_price"),
                        sourcing_url=bom_data.get("sourcing_url"),
                        stl_filename=bom_data.get("stl_filename"),
                        remarks=bom_data.get("remarks"),
                        sort_order=bom_data.get("sort_order", 0),
                    )
                    db.add(bom_item)

                restored["projects"] += 1

    # Link archives to projects by name (after both are restored)
    if "archives" in backup and "projects" in backup:
        # Build project name to ID mapping
        proj_result = await db.execute(select(Project))
        project_name_to_id: dict[str, int] = {}
        for proj in proj_result.scalars().all():
            project_name_to_id[proj.name] = proj.id

        # Update archives with project_id
        for archive_data in backup["archives"]:
            project_name = archive_data.get("project_name")
            if project_name and project_name in project_name_to_id:
                content_hash = archive_data.get("content_hash")
                if content_hash:
                    result = await db.execute(select(PrintArchive).where(PrintArchive.content_hash == content_hash))
                    archive = result.scalar_one_or_none()
                    if archive:
                        archive.project_id = project_name_to_id[project_name]

    # Restore print queue (must be after archives and projects)
    if "print_queue" in backup:
        # Build lookups
        printer_serial_to_id: dict[str, int] = {}
        archive_hash_to_id: dict[str, int] = {}
        project_name_to_id: dict[str, int] = {}

        pr_result = await db.execute(select(Printer))
        for pr in pr_result.scalars().all():
            printer_serial_to_id[pr.serial_number] = pr.id

        ar_result = await db.execute(select(PrintArchive))
        for ar in ar_result.scalars().all():
            if ar.content_hash:
                archive_hash_to_id[ar.content_hash] = ar.id

        proj_result = await db.execute(select(Project))
        for proj in proj_result.scalars().all():
            project_name_to_id[proj.name] = proj.id

        restored["print_queue"] = 0
        skipped["print_queue"] = 0
        skipped_details["print_queue"] = []

        for qi_data in backup["print_queue"]:
            printer_serial = qi_data.get("printer_serial")  # Can be None for unassigned items
            archive_hash = qi_data.get("archive_hash")

            # Archive is required, but printer can be None (unassigned)
            if not archive_hash:
                skipped["print_queue"] += 1
                continue

            # Look up printer_id (None if unassigned or printer not found)
            printer_id = printer_serial_to_id.get(printer_serial) if printer_serial else None
            archive_id = archive_hash_to_id.get(archive_hash)

            # Archive must exist, but printer is optional (unassigned items)
            if not archive_id:
                skipped["print_queue"] += 1
                skipped_details["print_queue"].append(
                    f"{printer_serial or 'unassigned'}/{archive_hash[:8] if archive_hash else 'N/A'}"
                )
                continue

            # If printer_serial was specified but printer not found, skip
            if printer_serial and not printer_id:
                skipped["print_queue"] += 1
                skipped_details["print_queue"].append(f"{printer_serial}/{archive_hash[:8]}")
                continue

            project_name = qi_data.get("project_name")
            project_id = project_name_to_id.get(project_name) if project_name else None

            qi = PrintQueueItem(
                printer_id=printer_id,  # Can be None for unassigned items
                archive_id=archive_id,
                project_id=project_id,
                position=qi_data.get("position", 0),
                require_previous_success=qi_data.get("require_previous_success", False),
                auto_off_after=qi_data.get("auto_off_after", False),
                manual_start=qi_data.get("manual_start", False),
                ams_mapping=qi_data.get("ams_mapping"),
                plate_id=qi_data.get("plate_id"),
                bed_levelling=qi_data.get("bed_levelling", True),
                flow_cali=qi_data.get("flow_cali", False),
                vibration_cali=qi_data.get("vibration_cali", True),
                layer_inspect=qi_data.get("layer_inspect", False),
                timelapse=qi_data.get("timelapse", False),
                use_ams=qi_data.get("use_ams", True),
                status=qi_data.get("status", "pending"),
                error_message=qi_data.get("error_message"),
            )
            if qi_data.get("scheduled_time"):
                qi.scheduled_time = datetime.fromisoformat(qi_data["scheduled_time"])
            if qi_data.get("started_at"):
                qi.started_at = datetime.fromisoformat(qi_data["started_at"])
            if qi_data.get("completed_at"):
                qi.completed_at = datetime.fromisoformat(qi_data["completed_at"])
            db.add(qi)
            restored["print_queue"] += 1

    # Restore pending uploads (skip duplicates by filename)
    if "pending_uploads" in backup:
        # Ensure the pending uploads directory exists
        pending_uploads_dir = base_dir / "virtual_printer" / "uploads"
        pending_uploads_dir.mkdir(parents=True, exist_ok=True)

        for upload_data in backup["pending_uploads"]:
            # Check for existing by filename
            result = await db.execute(
                select(PendingUpload).where(
                    PendingUpload.filename == upload_data["filename"],
                    PendingUpload.status == "pending",
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                if overwrite:
                    # Update existing
                    existing.file_size = upload_data.get("file_size", 0)
                    existing.source_ip = upload_data.get("source_ip")
                    existing.tags = upload_data.get("tags")
                    existing.notes = upload_data.get("notes")
                    existing.project_id = upload_data.get("project_id")
                    # Update file path if file was restored from ZIP
                    if upload_data.get("file_path"):
                        restored_file = base_dir / upload_data["file_path"]
                        if restored_file.exists():
                            # Move to proper location
                            target_path = pending_uploads_dir / upload_data["filename"]
                            if restored_file != target_path:
                                import shutil

                                shutil.move(str(restored_file), str(target_path))
                            existing.file_path = str(target_path)
                    restored["pending_uploads"] += 1
                else:
                    skipped["pending_uploads"] += 1
                    skipped_details["pending_uploads"].append(upload_data["filename"])
            else:
                # Determine file path
                file_path_str = None
                if upload_data.get("file_path"):
                    restored_file = base_dir / upload_data["file_path"]
                    if restored_file.exists():
                        # Move to proper location
                        target_path = pending_uploads_dir / upload_data["filename"]
                        if restored_file != target_path:
                            import shutil

                            shutil.move(str(restored_file), str(target_path))
                        file_path_str = str(target_path)

                # Parse uploaded_at
                uploaded_at = None
                if upload_data.get("uploaded_at"):
                    try:
                        uploaded_at = datetime.fromisoformat(upload_data["uploaded_at"].replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        uploaded_at = datetime.utcnow()
                else:
                    uploaded_at = datetime.utcnow()

                pending = PendingUpload(
                    filename=upload_data["filename"],
                    file_path=file_path_str or "",
                    file_size=upload_data.get("file_size", 0),
                    source_ip=upload_data.get("source_ip"),
                    status="pending",
                    tags=upload_data.get("tags"),
                    notes=upload_data.get("notes"),
                    project_id=upload_data.get("project_id"),
                    uploaded_at=uploaded_at,
                )
                db.add(pending)
                restored["pending_uploads"] += 1

    # Restore API keys (generates new keys since we can't restore the hash)
    new_api_keys: list[dict] = []  # Track newly generated keys for response
    if "api_keys" in backup:
        from backend.app.core.auth import generate_api_key

        # Build printer serial to ID mapping
        printer_serial_to_id: dict[str, int] = {}
        pr_result = await db.execute(select(Printer))
        for pr in pr_result.scalars().all():
            printer_serial_to_id[pr.serial_number] = pr.id

        restored["api_keys"] = 0
        skipped["api_keys"] = 0
        skipped_details["api_keys"] = []

        for key_data in backup["api_keys"]:
            # Check if key with same name already exists
            result = await db.execute(select(APIKey).where(APIKey.name == key_data["name"]))
            existing = result.scalar_one_or_none()
            if existing:
                if overwrite:
                    # Update permissions but keep the existing key
                    existing.can_queue = key_data.get("can_queue", True)
                    existing.can_control_printer = key_data.get("can_control_printer", False)
                    existing.can_read_status = key_data.get("can_read_status", True)
                    existing.enabled = key_data.get("enabled", True)
                    if key_data.get("expires_at"):
                        existing.expires_at = datetime.fromisoformat(key_data["expires_at"])
                    # Convert printer serials to IDs
                    if key_data.get("printer_serials"):
                        existing.printer_ids = [
                            printer_serial_to_id[s] for s in key_data["printer_serials"] if s in printer_serial_to_id
                        ]
                    restored["api_keys"] += 1
                else:
                    skipped["api_keys"] += 1
                    skipped_details["api_keys"].append(key_data["name"])
            else:
                # Generate new key
                full_key, key_hash, key_prefix = generate_api_key()

                # Convert printer serials to IDs
                printer_ids = None
                if key_data.get("printer_serials"):
                    printer_ids = [
                        printer_serial_to_id[s] for s in key_data["printer_serials"] if s in printer_serial_to_id
                    ]

                api_key = APIKey(
                    name=key_data["name"],
                    key_hash=key_hash,
                    key_prefix=key_prefix,
                    can_queue=key_data.get("can_queue", True),
                    can_control_printer=key_data.get("can_control_printer", False),
                    can_read_status=key_data.get("can_read_status", True),
                    printer_ids=printer_ids,
                    enabled=key_data.get("enabled", True),
                )
                if key_data.get("expires_at"):
                    api_key.expires_at = datetime.fromisoformat(key_data["expires_at"])
                db.add(api_key)
                restored["api_keys"] += 1

                # Track the new key so user can see it
                new_api_keys.append(
                    {
                        "name": key_data["name"],
                        "key": full_key,
                        "key_prefix": key_prefix,
                    }
                )

    # Restore users (note: passwords not included in backup - users will need new passwords)
    # Users are skipped by default since they have no passwords; admin must recreate them
    new_users: list[str] = []
    if "users" in backup:
        from backend.app.core.auth import get_password_hash

        for user_data in backup["users"]:
            result = await db.execute(select(User).where(User.username == user_data["username"]))
            existing = result.scalar_one_or_none()
            if existing:
                if overwrite:
                    existing.role = user_data.get("role", "user")
                    existing.is_active = user_data.get("is_active", True)
                    # Don't change password - keep existing
                    restored["users"] += 1
                else:
                    skipped["users"] += 1
                    skipped_details["users"].append(user_data["username"])
            else:
                # Create user with a temporary password that must be changed
                # Generate a random temporary password
                import secrets

                temp_password = secrets.token_urlsafe(16)
                user = User(
                    username=user_data["username"],
                    password_hash=get_password_hash(temp_password),
                    role=user_data.get("role", "user"),
                    is_active=user_data.get("is_active", True),
                )
                db.add(user)
                restored["users"] += 1
                new_users.append(f"{user_data['username']} (temp password: {temp_password})")

    # Restore GitHub backup configuration (note: access_token not included for security)
    if "github_backup" in backup:
        github_data = backup["github_backup"]
        result = await db.execute(select(GitHubBackupConfig).limit(1))
        existing = result.scalar_one_or_none()
        if existing:
            if overwrite:
                existing.repository_url = github_data.get("repository_url", existing.repository_url)
                existing.branch = github_data.get("branch", existing.branch)
                existing.schedule_enabled = github_data.get("schedule_enabled", existing.schedule_enabled)
                existing.schedule_type = github_data.get("schedule_type", existing.schedule_type)
                existing.backup_kprofiles = github_data.get("backup_kprofiles", existing.backup_kprofiles)
                existing.backup_cloud_profiles = github_data.get(
                    "backup_cloud_profiles", existing.backup_cloud_profiles
                )
                existing.backup_settings = github_data.get("backup_settings", existing.backup_settings)
                existing.enabled = github_data.get("enabled", existing.enabled)
                # Note: access_token must be re-entered after restore
                restored["github_backup"] += 1
            else:
                skipped["github_backup"] += 1
        else:
            config = GitHubBackupConfig(
                repository_url=github_data.get("repository_url", ""),
                access_token="",  # Must be entered after restore
                branch=github_data.get("branch", "main"),
                schedule_enabled=github_data.get("schedule_enabled", False),
                schedule_type=github_data.get("schedule_type", "daily"),
                backup_kprofiles=github_data.get("backup_kprofiles", True),
                backup_cloud_profiles=github_data.get("backup_cloud_profiles", True),
                backup_settings=github_data.get("backup_settings", False),
                enabled=False,  # Disabled until token is entered
            )
            db.add(config)
            restored["github_backup"] += 1

    await db.commit()

    # If printers were in the backup (restored, updated, or skipped), reconnect all active printers
    # This ensures connections are re-established after restore, even if printers were skipped
    if "printers" in backup:
        # Need fresh query after commit to get proper IDs for newly created printers
        result = await db.execute(select(Printer).where(Printer.is_active.is_(True)))
        active_printers = result.scalars().all()
        for printer in active_printers:
            # This will disconnect existing connection (if any) and reconnect
            try:
                await printer_manager.connect_printer(printer)
            except Exception:
                pass  # Connection failed, but don't fail the restore

    # If settings were restored, check if Spoolman needs to be reconnected
    if "settings" in backup:
        spoolman_enabled = await get_setting(db, "spoolman_enabled")
        spoolman_url = await get_setting(db, "spoolman_url")
        if spoolman_enabled and spoolman_enabled.lower() == "true" and spoolman_url:
            try:
                client = await init_spoolman_client(spoolman_url)
                if await client.health_check():
                    pass  # Connected successfully
            except Exception:
                pass  # Spoolman connection failed, but don't fail the restore

        # Reconfigure virtual printer if settings were restored
        try:
            from backend.app.services.virtual_printer import virtual_printer_manager

            vp_enabled = await get_setting(db, "virtual_printer_enabled")
            vp_access_code = await get_setting(db, "virtual_printer_access_code")
            vp_mode = await get_setting(db, "virtual_printer_mode")
            vp_model = await get_setting(db, "virtual_printer_model")

            enabled = vp_enabled and vp_enabled.lower() == "true"
            access_code = vp_access_code or ""
            mode = vp_mode or "immediate"
            model = vp_model or ""

            if enabled and access_code:
                await virtual_printer_manager.configure(
                    enabled=True,
                    access_code=access_code,
                    mode=mode,
                    model=model,
                )
            elif not enabled and virtual_printer_manager.is_enabled:
                await virtual_printer_manager.configure(
                    enabled=False,
                    access_code=access_code,
                    mode=mode,
                    model=model,
                )
        except Exception:
            pass  # Virtual printer config failed, but don't fail the restore

        # Reconfigure MQTT relay if settings were restored
        try:
            from backend.app.services.mqtt_relay import mqtt_relay

            mqtt_settings = {
                "mqtt_enabled": (await get_setting(db, "mqtt_enabled") or "false") == "true",
                "mqtt_broker": await get_setting(db, "mqtt_broker") or "",
                "mqtt_port": int(await get_setting(db, "mqtt_port") or "1883"),
                "mqtt_username": await get_setting(db, "mqtt_username") or "",
                "mqtt_password": await get_setting(db, "mqtt_password") or "",
                "mqtt_topic_prefix": await get_setting(db, "mqtt_topic_prefix") or "bambuddy",
                "mqtt_use_tls": (await get_setting(db, "mqtt_use_tls") or "false") == "true",
            }
            await mqtt_relay.configure(mqtt_settings)
        except Exception:
            pass  # MQTT relay config failed, but don't fail the restore

    # Build summary message
    restored_parts = []
    for key, count in restored.items():
        if count > 0:
            restored_parts.append(f"{count} {key.replace('_', ' ')}")

    if files_restored > 0:
        restored_parts.append(f"{files_restored} files")

    skipped_parts = []
    total_skipped = sum(skipped.values())
    for key, count in skipped.items():
        if count > 0:
            skipped_parts.append(f"{count} {key.replace('_', ' ')}")

    message_parts = []
    if restored_parts:
        message_parts.append(f"Restored: {', '.join(restored_parts)}")
    if skipped_parts:
        message_parts.append(f"Skipped (already exist): {', '.join(skipped_parts)}")

    response = {
        "success": True,
        "message": ". ".join(message_parts) if message_parts else "Nothing to restore",
        "restored": restored,
        "skipped": skipped,
        "skipped_details": skipped_details,
        "files_restored": files_restored,
        "total_skipped": total_skipped,
    }

    # Include newly generated API keys if any (so user can see them)
    if new_api_keys:
        response["new_api_keys"] = new_api_keys

    # Include newly created users with temp passwords (so admin can share them)
    if new_users:
        response["new_users"] = new_users

    return response


# =============================================================================
# Virtual Printer Settings
# =============================================================================


@router.get("/virtual-printer/models")
async def get_virtual_printer_models():
    """Get available virtual printer models."""
    from backend.app.services.virtual_printer import (
        DEFAULT_VIRTUAL_PRINTER_MODEL,
        VIRTUAL_PRINTER_MODELS,
    )

    return {
        "models": VIRTUAL_PRINTER_MODELS,
        "default": DEFAULT_VIRTUAL_PRINTER_MODEL,
    }


@router.get("/virtual-printer")
async def get_virtual_printer_settings(db: AsyncSession = Depends(get_db)):
    """Get virtual printer settings and status."""
    from backend.app.services.virtual_printer import (
        DEFAULT_VIRTUAL_PRINTER_MODEL,
        virtual_printer_manager,
    )

    enabled = await get_setting(db, "virtual_printer_enabled")
    access_code = await get_setting(db, "virtual_printer_access_code")
    mode = await get_setting(db, "virtual_printer_mode")
    model = await get_setting(db, "virtual_printer_model")

    return {
        "enabled": enabled == "true" if enabled else False,
        "access_code_set": bool(access_code),
        "mode": mode or "immediate",
        "model": model or DEFAULT_VIRTUAL_PRINTER_MODEL,
        "status": virtual_printer_manager.get_status(),
    }


@router.put("/virtual-printer")
async def update_virtual_printer_settings(
    enabled: bool = None,
    access_code: str = None,
    mode: str = None,
    model: str = None,
    db: AsyncSession = Depends(get_db),
):
    """Update virtual printer settings and restart services if needed."""
    from backend.app.services.virtual_printer import (
        DEFAULT_VIRTUAL_PRINTER_MODEL,
        VIRTUAL_PRINTER_MODELS,
        virtual_printer_manager,
    )

    # Get current values
    current_enabled = await get_setting(db, "virtual_printer_enabled") == "true"
    current_access_code = await get_setting(db, "virtual_printer_access_code") or ""
    current_mode = await get_setting(db, "virtual_printer_mode") or "immediate"
    current_model = await get_setting(db, "virtual_printer_model") or DEFAULT_VIRTUAL_PRINTER_MODEL

    # Apply updates
    new_enabled = enabled if enabled is not None else current_enabled
    new_access_code = access_code if access_code is not None else current_access_code
    new_mode = mode if mode is not None else current_mode
    new_model = model if model is not None else current_model

    # Validate mode
    # "review" is the new name for "queue" (pending review before archiving)
    # "print_queue" archives and adds to print queue (unassigned)
    if new_mode not in ("immediate", "queue", "review", "print_queue"):
        return JSONResponse(
            status_code=400,
            content={"detail": "Mode must be 'immediate', 'review', or 'print_queue'"},
        )
    # Normalize legacy "queue" to "review" for storage
    if new_mode == "queue":
        new_mode = "review"

    # Validate model
    if model is not None and model not in VIRTUAL_PRINTER_MODELS:
        return JSONResponse(
            status_code=400,
            content={"detail": f"Invalid model. Must be one of: {', '.join(VIRTUAL_PRINTER_MODELS.keys())}"},
        )

    # Validate access code when enabling
    if new_enabled and not new_access_code:
        return JSONResponse(
            status_code=400,
            content={"detail": "Access code is required when enabling virtual printer"},
        )

    # Validate access code length (Bambu Studio requires exactly 8 characters)
    if access_code is not None and len(access_code) != 8:
        return JSONResponse(
            status_code=400,
            content={"detail": "Access code must be exactly 8 characters"},
        )

    # Save settings
    await set_setting(db, "virtual_printer_enabled", "true" if new_enabled else "false")
    if access_code is not None:
        await set_setting(db, "virtual_printer_access_code", access_code)
    await set_setting(db, "virtual_printer_mode", new_mode)
    if model is not None:
        await set_setting(db, "virtual_printer_model", model)
    await db.commit()
    db.expire_all()

    # Reconfigure virtual printer
    try:
        await virtual_printer_manager.configure(
            enabled=new_enabled,
            access_code=new_access_code,
            mode=new_mode,
            model=new_model,
        )
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={"detail": str(e)},
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"detail": f"Failed to configure virtual printer: {e}"},
        )

    return await get_virtual_printer_settings(db)


# =============================================================================
# MQTT Relay Settings
# =============================================================================


@router.get("/mqtt/status")
async def get_mqtt_status():
    """Get MQTT relay connection status."""
    from backend.app.services.mqtt_relay import mqtt_relay

    return mqtt_relay.get_status()
