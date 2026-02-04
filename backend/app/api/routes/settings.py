import io
import logging
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.config import settings as app_settings
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.schemas.settings import AppSettings, AppSettingsUpdate

logger = logging.getLogger(__name__)

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
async def get_settings(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
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
                "check_printer_firmware",
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
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
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
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Partially update application settings (same as PUT, for REST compatibility)."""
    return await update_settings(settings_update, db, _)


@router.post("/reset", response_model=AppSettings)
async def reset_settings(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
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
async def get_spoolman_settings(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
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
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
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
async def create_backup(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_BACKUP),
):
    """Create a complete backup (database + all files) as a ZIP.

    This is a simplified backup that includes the entire SQLite database
    and all data directories. It is complete by definition and cannot miss data.
    """
    import shutil
    import tempfile

    from sqlalchemy import text

    from backend.app.core.database import engine

    try:
        base_dir = app_settings.base_dir
        db_path = Path(app_settings.database_url.replace("sqlite+aiosqlite:///", ""))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # 1. Checkpoint WAL to ensure all data is in main db file
            async with engine.begin() as conn:
                await conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))

            # 2. Copy database file
            shutil.copy2(db_path, temp_path / "bambuddy.db")

            # 3. Copy data directories (if they exist)
            dirs_to_backup = [
                ("archive", base_dir / "archive"),
                ("virtual_printer", base_dir / "virtual_printer"),
                ("plate_calibration", app_settings.plate_calibration_dir),
                ("icons", base_dir / "icons"),
                ("projects", base_dir / "projects"),
            ]

            for name, src_dir in dirs_to_backup:
                if src_dir.exists() and any(src_dir.iterdir()):
                    try:
                        shutil.copytree(src_dir, temp_path / name)
                    except shutil.Error as e:
                        # Some files may have restricted permissions (e.g., SSL keys)
                        # Log the error but continue with partial backup
                        logger.warning(f"Some files in {name} could not be copied: {e}")
                    except PermissionError as e:
                        logger.warning(f"Permission denied copying {name}: {e}")

            # 4. Create ZIP
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for file_path in temp_path.rglob("*"):
                    if file_path.is_file():
                        arcname = file_path.relative_to(temp_path)
                        zf.write(file_path, arcname)

            zip_buffer.seek(0)
            filename = f"bambuddy-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"

            return StreamingResponse(
                zip_buffer,
                media_type="application/zip",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )
    except Exception as e:
        logger.error(f"Backup failed: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "Backup failed. Check server logs for details."},
        )


@router.post("/restore")
async def restore_backup(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_RESTORE),
):
    """Restore from a complete backup ZIP.

    This is a simplified restore that replaces the database and all data directories
    from the backup ZIP. Requires a restart after restore.
    """
    import shutil
    import tempfile

    from fastapi import HTTPException

    from backend.app.core.database import close_all_connections
    from backend.app.services.virtual_printer import virtual_printer_manager

    base_dir = app_settings.base_dir
    db_path = Path(app_settings.database_url.replace("sqlite+aiosqlite:///", ""))

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # 1. Read and extract ZIP
        content = await file.read()

        # Check if it's a valid ZIP
        if not file.filename or not file.filename.endswith(".zip"):
            raise HTTPException(400, "Invalid backup file: must be a .zip file")

        try:
            with zipfile.ZipFile(io.BytesIO(content), "r") as zf:
                zf.extractall(temp_path)
        except zipfile.BadZipFile:
            raise HTTPException(400, "Invalid backup file: not a valid ZIP")

        # 2. Validate backup (must have database)
        backup_db = temp_path / "bambuddy.db"
        if not backup_db.exists():
            raise HTTPException(400, "Invalid backup: missing bambuddy.db")

        try:
            import asyncio

            # 3. Stop virtual printer if running (releases file locks)
            try:
                if virtual_printer_manager.is_enabled:
                    logger.info("Stopping virtual printer for restore...")
                    await virtual_printer_manager.configure(enabled=False)
                    # Give it time to fully release file handles
                    await asyncio.sleep(1)
            except Exception as e:
                logger.warning(f"Failed to stop virtual printer: {e}")

            # 4. Close current database connections
            logger.info("Closing database connections...")
            await close_all_connections()

            # 5. Replace database
            logger.info("Restoring database from backup...")
            shutil.copy2(backup_db, db_path)

            # 6. Replace data directories
            # For Docker compatibility: clear contents then copy (don't delete mount points)
            dirs_to_restore = [
                ("archive", base_dir / "archive"),
                ("virtual_printer", base_dir / "virtual_printer"),
                ("plate_calibration", app_settings.plate_calibration_dir),
                ("icons", base_dir / "icons"),
                ("projects", base_dir / "projects"),
            ]

            skipped_dirs = []
            for name, dest_dir in dirs_to_restore:
                src_dir = temp_path / name
                if src_dir.exists():
                    logger.info(f"Restoring {name} directory...")
                    try:
                        # Clear destination contents (not the dir itself - may be Docker mount)
                        if dest_dir.exists():
                            for item in dest_dir.iterdir():
                                try:
                                    if item.is_dir():
                                        shutil.rmtree(item)
                                    else:
                                        item.unlink()
                                except OSError as e:
                                    logger.warning(f"Could not delete {item}: {e}")
                        else:
                            dest_dir.mkdir(parents=True, exist_ok=True)
                        # Copy contents from backup
                        for item in src_dir.iterdir():
                            dest_item = dest_dir / item.name
                            if item.is_dir():
                                shutil.copytree(item, dest_item)
                            else:
                                shutil.copy2(item, dest_item)
                    except OSError as e:
                        logger.warning(f"Could not restore {name} directory: {e}")
                        skipped_dirs.append(name)

            # 7. Note: Virtual printer and database will be reinitialized on restart
            # Do NOT try to restart services here - the database session is closed

            logger.info("Restore complete - restart required")
            message = "Backup restored successfully. Please restart Bambuddy for changes to take effect."
            if skipped_dirs:
                message += f" Note: Some directories could not be restored ({', '.join(skipped_dirs)})."
            return {
                "success": True,
                "message": message,
            }

        except Exception as e:
            logger.error(f"Restore failed: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={"success": False, "message": "Restore failed. Check server logs for details."},
            )


@router.get("/network-interfaces")
async def get_network_interfaces(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """Get available network interfaces for SSDP proxy configuration."""
    from backend.app.services.network_utils import get_network_interfaces

    interfaces = get_network_interfaces()
    return {"interfaces": interfaces}


@router.get("/virtual-printer/models")
async def get_virtual_printer_models(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
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
async def get_virtual_printer_settings(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """Get virtual printer settings and status."""
    from backend.app.services.virtual_printer import (
        DEFAULT_VIRTUAL_PRINTER_MODEL,
        virtual_printer_manager,
    )

    enabled = await get_setting(db, "virtual_printer_enabled")
    access_code = await get_setting(db, "virtual_printer_access_code")
    mode = await get_setting(db, "virtual_printer_mode")
    model = await get_setting(db, "virtual_printer_model")
    target_printer_id = await get_setting(db, "virtual_printer_target_printer_id")
    remote_interface_ip = await get_setting(db, "virtual_printer_remote_interface_ip")

    return {
        "enabled": enabled == "true" if enabled else False,
        "access_code_set": bool(access_code),
        "mode": mode or "immediate",
        "model": model or DEFAULT_VIRTUAL_PRINTER_MODEL,
        "target_printer_id": int(target_printer_id) if target_printer_id else None,
        "remote_interface_ip": remote_interface_ip or "",
        "status": virtual_printer_manager.get_status(),
    }


@router.put("/virtual-printer")
async def update_virtual_printer_settings(
    enabled: bool = None,
    access_code: str = None,
    mode: str = None,
    model: str = None,
    target_printer_id: int = None,
    remote_interface_ip: str = None,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Update virtual printer settings and restart services if needed.

    For proxy mode with SSDP proxy (dual-homed setup):
    - remote_interface_ip: IP of interface on slicer's network (LAN B)
    - Local interface is auto-detected based on target printer IP
    """
    from sqlalchemy import select

    from backend.app.models.printer import Printer
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
    current_target_id_str = await get_setting(db, "virtual_printer_target_printer_id")
    current_target_id = int(current_target_id_str) if current_target_id_str else None
    current_remote_iface = await get_setting(db, "virtual_printer_remote_interface_ip") or ""

    # Apply updates
    new_enabled = enabled if enabled is not None else current_enabled
    new_access_code = access_code if access_code is not None else current_access_code
    new_mode = mode if mode is not None else current_mode
    new_model = model if model is not None else current_model
    new_target_id = target_printer_id if target_printer_id is not None else current_target_id
    new_remote_iface = remote_interface_ip if remote_interface_ip is not None else current_remote_iface

    # Validate mode
    # "review" is the new name for "queue" (pending review before archiving)
    # "print_queue" archives and adds to print queue (unassigned)
    # "proxy" is transparent TCP proxy to a real printer
    if new_mode not in ("immediate", "queue", "review", "print_queue", "proxy"):
        return JSONResponse(
            status_code=400,
            content={"detail": "Mode must be 'immediate', 'review', 'print_queue', or 'proxy'"},
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

    # Mode-specific validation and printer lookup
    target_printer_ip = ""
    target_printer_serial = ""
    if new_mode == "proxy":
        # Proxy mode requires target printer when enabling
        if new_enabled and not new_target_id:
            # If just switching to proxy mode (not explicitly enabling), auto-disable
            if enabled is None:
                new_enabled = False
            else:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Target printer is required for proxy mode"},
                )

        # Look up printer IP and serial if we have a target
        if new_target_id:
            result = await db.execute(select(Printer).where(Printer.id == new_target_id))
            printer = result.scalar_one_or_none()
            if not printer:
                return JSONResponse(
                    status_code=400,
                    content={"detail": f"Printer with ID {new_target_id} not found"},
                )
            target_printer_ip = printer.ip_address
            target_printer_serial = printer.serial_number
        # Access code not required for proxy mode
    else:
        # Non-proxy modes require access code when enabling
        if new_enabled and not new_access_code:
            # If just switching modes (not explicitly enabling), auto-disable
            if enabled is None:
                new_enabled = False
            else:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Access code is required when enabling virtual printer"},
                )

        # Validate access code length (Bambu Studio requires exactly 8 characters)
        if access_code is not None and access_code and len(access_code) != 8:
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
    if target_printer_id is not None:
        await set_setting(db, "virtual_printer_target_printer_id", str(target_printer_id))
    if remote_interface_ip is not None:
        await set_setting(db, "virtual_printer_remote_interface_ip", remote_interface_ip)
    await db.commit()
    db.expire_all()

    # Reconfigure virtual printer
    try:
        await virtual_printer_manager.configure(
            enabled=new_enabled,
            access_code=new_access_code,
            mode=new_mode,
            model=new_model,
            target_printer_ip=target_printer_ip,
            target_printer_serial=target_printer_serial,
            remote_interface_ip=new_remote_iface,
        )
    except ValueError as e:
        logger.warning(f"Virtual printer configuration validation error: {e}")
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid virtual printer configuration. Check the provided values."},
        )
    except Exception as e:
        logger.error(f"Failed to configure virtual printer: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"detail": "Failed to configure virtual printer. Check server logs for details."},
        )

    return await get_virtual_printer_settings(db)


# =============================================================================
# MQTT Relay Settings
# =============================================================================


@router.get("/mqtt/status")
async def get_mqtt_status(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """Get MQTT relay connection status."""
    from backend.app.services.mqtt_relay import mqtt_relay

    return mqtt_relay.get_status()
