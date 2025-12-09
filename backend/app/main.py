import asyncio
import logging
import os
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI

# Import settings first for logging configuration
from backend.app.core.config import settings as app_settings, APP_VERSION

# Configure logging based on settings
# DEBUG=true -> DEBUG level, else use LOG_LEVEL setting
log_level_str = "DEBUG" if app_settings.debug else app_settings.log_level.upper()
log_level = getattr(logging, log_level_str, logging.INFO)
log_format = '%(asctime)s %(levelname)s [%(name)s] %(message)s'

# Create root logger
root_logger = logging.getLogger()
root_logger.setLevel(log_level)

# Console handler - always enabled
console_handler = logging.StreamHandler()
console_handler.setLevel(log_level)
console_handler.setFormatter(logging.Formatter(log_format))
root_logger.addHandler(console_handler)

# File handler - only in production or if explicitly enabled
if app_settings.log_to_file:
    log_file = app_settings.log_dir / "bambuddy.log"
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5*1024*1024,  # 5MB
        backupCount=3,
        encoding='utf-8'
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter(log_format))
    root_logger.addHandler(file_handler)
    logging.info(f"Logging to file: {log_file}")

# Reduce noise from third-party libraries in production
if not app_settings.debug:
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

logging.info(f"Bambuddy starting - debug={app_settings.debug}, log_level={log_level_str}")
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.app.core.database import init_db, async_session
from sqlalchemy import select, or_
from backend.app.core.websocket import ws_manager
from backend.app.api.routes import printers, archives, websocket, filaments, cloud, smart_plugs, print_queue, kprofiles, notifications, notification_templates, spoolman, updates, maintenance, camera
from backend.app.api.routes import settings as settings_routes
from backend.app.services.notification_service import notification_service
from backend.app.services.printer_manager import (
    printer_manager,
    printer_state_to_dict,
    init_printer_connections,
)
from backend.app.services.print_scheduler import scheduler as print_scheduler
from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.archive import ArchiveService
from backend.app.services.bambu_ftp import download_file_async
from backend.app.services.smart_plug_manager import smart_plug_manager
from backend.app.services.tasmota import tasmota_service
from backend.app.models.smart_plug import SmartPlug
from backend.app.services.spoolman import get_spoolman_client, init_spoolman_client, close_spoolman_client
from backend.app.api.routes.maintenance import _get_printer_maintenance_internal, ensure_default_types


# Track active prints: {(printer_id, filename): archive_id}
_active_prints: dict[tuple[int, str], int] = {}

# Track expected prints from reprint/scheduled (skip auto-archiving for these)
# {(printer_id, filename): archive_id}
_expected_prints: dict[tuple[int, str], int] = {}

# Track starting energy for prints: {archive_id: starting_kwh}
_print_energy_start: dict[int, float] = {}


def register_expected_print(printer_id: int, filename: str, archive_id: int):
    """Register an expected print from reprint/scheduled so we don't create duplicate archives."""
    # Store with multiple filename variations to catch different naming patterns
    _expected_prints[(printer_id, filename)] = archive_id
    # Also store without .3mf extension if present
    if filename.endswith(".3mf"):
        base = filename[:-4]
        _expected_prints[(printer_id, base)] = archive_id
        _expected_prints[(printer_id, f"{base}.gcode")] = archive_id
    logging.getLogger(__name__).info(
        f"Registered expected print: printer={printer_id}, file={filename}, archive={archive_id}"
    )


_last_status_broadcast: dict[int, str] = {}
_nozzle_count_updated: set[int] = set()  # Track printers where we've updated nozzle_count


async def _report_spoolman_usage(printer_id: int, archive_id: int, logger):
    """Report filament usage to Spoolman after print completion.

    This finds the spool by RFID tag_uid from current AMS state and reports
    the filament_used_grams from the archive metadata.
    """
    async with async_session() as db:
        from backend.app.api.routes.settings import get_setting
        from backend.app.models.archive import PrintArchive

        # Check if Spoolman is enabled
        spoolman_enabled = await get_setting(db, "spoolman_enabled")
        if not spoolman_enabled or spoolman_enabled.lower() != "true":
            return

        # Get Spoolman URL
        spoolman_url = await get_setting(db, "spoolman_url")
        if not spoolman_url:
            return

        # Get or create Spoolman client
        client = await get_spoolman_client()
        if not client:
            client = await init_spoolman_client(spoolman_url)

        # Check if Spoolman is reachable
        if not await client.health_check():
            logger.warning(f"Spoolman not reachable for usage reporting")
            return

        # Get archive to find filament usage
        result = await db.execute(
            select(PrintArchive).where(PrintArchive.id == archive_id)
        )
        archive = result.scalar_one_or_none()
        if not archive or not archive.filament_used_grams:
            logger.debug(f"No filament usage data for archive {archive_id}")
            return

        filament_used = archive.filament_used_grams
        logger.info(f"[SPOOLMAN] Archive {archive_id} used {filament_used}g of filament")

        # Get current AMS state from printer to find the active spool
        state = printer_manager.get_status(printer_id)
        if not state or not state.raw_data:
            logger.debug(f"No printer state available for usage reporting")
            return

        ams_data = state.raw_data.get("ams")
        if not ams_data:
            logger.debug(f"No AMS data available for usage reporting")
            return

        # Find spools with RFID tags in Spoolman and report usage
        # For now, we report usage to the first spool found with a matching tag
        # TODO: In future, track which specific trays were used during the print
        spools_updated = 0
        for ams_unit in ams_data:
            ams_id = int(ams_unit.get("id", 0))
            trays = ams_unit.get("tray", [])

            for tray_data in trays:
                tag_uid = tray_data.get("tag_uid")
                if not tag_uid:
                    continue

                # Find spool in Spoolman by tag
                spool = await client.find_spool_by_tag(tag_uid)
                if spool:
                    # Report usage to Spoolman
                    result = await client.use_spool(spool["id"], filament_used)
                    if result:
                        logger.info(
                            f"[SPOOLMAN] Reported {filament_used}g usage to spool {spool['id']} "
                            f"(tag: {tag_uid})"
                        )
                        spools_updated += 1
                        # Only report to one spool for single-material prints
                        # Multi-material prints would need more sophisticated tracking
                        return

        if spools_updated == 0:
            logger.debug(f"No matching Spoolman spools found for printer {printer_id}")


async def on_printer_status_change(printer_id: int, state: PrinterState):
    """Handle printer status changes - broadcast via WebSocket."""
    # Only broadcast if something meaningful changed (reduce WebSocket spam)
    # Include rounded temperatures to detect meaningful temp changes (within 1 degree)
    temps = state.temperatures or {}
    nozzle_temp = round(temps.get("nozzle", 0))
    bed_temp = round(temps.get("bed", 0))
    nozzle_2_temp = round(temps.get("nozzle_2", 0)) if "nozzle_2" in temps else ""
    chamber_temp = round(temps.get("chamber", 0)) if "chamber" in temps else ""

    # Auto-detect dual-nozzle printers from MQTT temperature data
    if "nozzle_2" in temps and printer_id not in _nozzle_count_updated:
        _nozzle_count_updated.add(printer_id)
        # Update nozzle_count in database
        async with async_session() as db:
            from backend.app.models.printer import Printer
            result = await db.execute(
                select(Printer).where(Printer.id == printer_id)
            )
            printer = result.scalar_one_or_none()
            if printer and printer.nozzle_count != 2:
                printer.nozzle_count = 2
                await db.commit()
                logging.getLogger(__name__).info(
                    f"Auto-detected dual-nozzle printer {printer_id}, updated nozzle_count=2"
                )

    status_key = (
        f"{state.connected}:{state.state}:{state.progress}:{state.layer_num}:"
        f"{nozzle_temp}:{bed_temp}:{nozzle_2_temp}:{chamber_temp}"
    )
    if _last_status_broadcast.get(printer_id) == status_key:
        return  # No change, skip broadcast

    _last_status_broadcast[printer_id] = status_key

    await ws_manager.send_printer_status(
        printer_id,
        printer_state_to_dict(state, printer_id),
    )


async def on_ams_change(printer_id: int, ams_data: list):
    """Handle AMS data changes - sync to Spoolman if enabled and auto mode."""
    import logging
    logger = logging.getLogger(__name__)

    try:
        async with async_session() as db:
            from backend.app.api.routes.settings import get_setting
            from backend.app.models.printer import Printer

            # Check if Spoolman is enabled
            spoolman_enabled = await get_setting(db, "spoolman_enabled")
            if not spoolman_enabled or spoolman_enabled.lower() != "true":
                return

            # Check sync mode
            sync_mode = await get_setting(db, "spoolman_sync_mode")
            if sync_mode and sync_mode != "auto":
                return  # Only sync on auto mode

            # Get Spoolman URL
            spoolman_url = await get_setting(db, "spoolman_url")
            if not spoolman_url:
                return

            # Get or create Spoolman client
            client = await get_spoolman_client()
            if not client:
                client = await init_spoolman_client(spoolman_url)

            # Check if Spoolman is reachable
            if not await client.health_check():
                logger.warning(f"Spoolman not reachable at {spoolman_url}")
                return

            # Get printer name for location
            result = await db.execute(
                select(Printer).where(Printer.id == printer_id)
            )
            printer = result.scalar_one_or_none()
            printer_name = printer.name if printer else f"Printer {printer_id}"

            # Sync each AMS tray
            synced = 0
            for ams_unit in ams_data:
                ams_id = int(ams_unit.get("id", 0))
                trays = ams_unit.get("tray", [])

                for tray_data in trays:
                    tray = client.parse_ams_tray(ams_id, tray_data)
                    if not tray:
                        continue  # Empty tray

                    try:
                        result = await client.sync_ams_tray(tray, printer_name)
                        if result:
                            synced += 1
                    except Exception as e:
                        logger.error(f"Error syncing AMS {ams_id} tray {tray.tray_id}: {e}")

            if synced > 0:
                logger.info(f"Auto-synced {synced} AMS trays to Spoolman for printer {printer_id}")

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Spoolman AMS sync failed: {e}")


async def on_print_start(printer_id: int, data: dict):
    """Handle print start - archive the 3MF file immediately."""
    import logging
    logger = logging.getLogger(__name__)

    await ws_manager.send_print_start(printer_id, data)

    async with async_session() as db:
        from backend.app.models.printer import Printer
        from backend.app.services.bambu_ftp import list_files_async

        result = await db.execute(
            select(Printer).where(Printer.id == printer_id)
        )
        printer = result.scalar_one_or_none()

        if not printer or not printer.auto_archive:
            return

        # Get the filename and subtask_name
        filename = data.get("filename", "")
        subtask_name = data.get("subtask_name", "")

        logger.info(f"Print start detected - filename: {filename}, subtask: {subtask_name}")

        if not filename and not subtask_name:
            return

        # Check if this is an expected print from reprint/scheduled
        # Build list of possible keys to check
        expected_keys = []
        if subtask_name:
            expected_keys.append((printer_id, subtask_name))
            expected_keys.append((printer_id, f"{subtask_name}.3mf"))
            expected_keys.append((printer_id, f"{subtask_name}.gcode.3mf"))
        if filename:
            fname = filename.split("/")[-1] if "/" in filename else filename
            expected_keys.append((printer_id, fname))
            # Strip extensions to match
            base = fname.replace(".gcode", "").replace(".3mf", "")
            expected_keys.append((printer_id, base))
            expected_keys.append((printer_id, f"{base}.3mf"))

        expected_archive_id = None
        for key in expected_keys:
            expected_archive_id = _expected_prints.pop(key, None)
            if expected_archive_id:
                # Clean up other possible keys for this print
                for other_key in expected_keys:
                    _expected_prints.pop(other_key, None)
                break

        if expected_archive_id:
            # This is a reprint/scheduled print - use existing archive, don't create new one
            logger.info(f"Using expected archive {expected_archive_id} for print (skipping duplicate)")
            from backend.app.models.archive import PrintArchive
            from datetime import datetime

            result = await db.execute(
                select(PrintArchive).where(PrintArchive.id == expected_archive_id)
            )
            archive = result.scalar_one_or_none()

            if archive:
                # Update archive status to printing
                archive.status = "printing"
                archive.started_at = datetime.now()
                await db.commit()

                # Track as active print
                _active_prints[(printer_id, archive.filename)] = archive.id
                if subtask_name:
                    _active_prints[(printer_id, f"{subtask_name}.3mf")] = archive.id

                # Set up energy tracking
                try:
                    plug_result = await db.execute(
                        select(SmartPlug).where(SmartPlug.printer_id == printer_id)
                    )
                    plug = plug_result.scalar_one_or_none()
                    logger.info(f"[ENERGY] Print start - archive {archive.id}, printer {printer_id}, plug found: {plug is not None}")
                    if plug:
                        energy = await tasmota_service.get_energy(plug)
                        logger.info(f"[ENERGY] Energy response from plug: {energy}")
                        if energy and energy.get("total") is not None:
                            _print_energy_start[archive.id] = energy["total"]
                            logger.info(f"[ENERGY] Recorded starting energy for archive {archive.id}: {energy['total']} kWh")
                        else:
                            logger.warning(f"[ENERGY] No 'total' in energy response for archive {archive.id}")
                    else:
                        logger.info(f"[ENERGY] No smart plug found for printer {printer_id}")
                except Exception as e:
                    logger.warning(f"Failed to record starting energy: {e}")

                await ws_manager.send_archive_updated({
                    "id": archive.id,
                    "status": "printing",
                })

            # Smart plug automation for expected prints too
            try:
                await smart_plug_manager.on_print_start(printer_id, db)
            except Exception as e:
                logger.warning(f"Smart plug on_print_start failed: {e}")

            return  # Skip creating a new archive

        # Check if there's already a "printing" archive for this printer/file
        # This prevents duplicates when backend restarts during an active print
        from backend.app.models.archive import PrintArchive
        check_name = subtask_name or filename.split("/")[-1].replace(".gcode", "").replace(".3mf", "")
        existing = await db.execute(
            select(PrintArchive)
            .where(PrintArchive.printer_id == printer_id)
            .where(PrintArchive.status == "printing")
            .where(PrintArchive.print_name.ilike(f"%{check_name}%"))
            .order_by(PrintArchive.created_at.desc())
            .limit(1)
        )
        existing_archive = existing.scalar_one_or_none()
        if existing_archive:
            logger.info(f"Skipping duplicate - already have printing archive {existing_archive.id} for {check_name}")
            # Track this as the active print
            _active_prints[(printer_id, existing_archive.filename)] = existing_archive.id
            # Also set up energy tracking if not already tracked
            if existing_archive.id not in _print_energy_start:
                try:
                    plug_result = await db.execute(
                        select(SmartPlug).where(SmartPlug.printer_id == printer_id)
                    )
                    plug = plug_result.scalar_one_or_none()
                    if plug:
                        energy = await tasmota_service.get_energy(plug)
                        if energy and energy.get("total") is not None:
                            _print_energy_start[existing_archive.id] = energy["total"]
                            logger.info(f"Recorded starting energy for existing archive {existing_archive.id}: {energy['total']} kWh")
                except Exception as e:
                    logger.warning(f"Failed to record starting energy for existing archive: {e}")
            return

        # Build list of possible 3MF filenames to try
        possible_names = []

        # Bambu printers typically store files as "Name.gcode.3mf"
        # The subtask_name is usually the best source for the filename
        if subtask_name:
            # Try common Bambu naming patterns
            possible_names.append(f"{subtask_name}.gcode.3mf")
            possible_names.append(f"{subtask_name}.3mf")

        # Try original filename with .3mf extension
        if filename:
            # Extract just the filename part, not the full path
            fname = filename.split("/")[-1] if "/" in filename else filename
            if fname.endswith(".3mf"):
                possible_names.append(fname)
            elif fname.endswith(".gcode"):
                base = fname.rsplit(".", 1)[0]
                possible_names.append(f"{base}.gcode.3mf")
                possible_names.append(f"{base}.3mf")
            else:
                possible_names.append(f"{fname}.gcode.3mf")
                possible_names.append(f"{fname}.3mf")

        # Remove duplicates while preserving order
        seen = set()
        possible_names = [x for x in possible_names if not (x in seen or seen.add(x))]

        logger.info(f"Trying filenames: {possible_names}")

        # Try to find and download the 3MF file
        temp_path = None
        downloaded_filename = None

        for try_filename in possible_names:
            if not try_filename.endswith(".3mf"):
                continue

            remote_paths = [
                f"/cache/{try_filename}",
                f"/model/{try_filename}",
                f"/{try_filename}",
            ]

            temp_path = app_settings.archive_dir / "temp" / try_filename
            temp_path.parent.mkdir(parents=True, exist_ok=True)

            for remote_path in remote_paths:
                logger.debug(f"Trying FTP download: {remote_path}")
                try:
                    if await download_file_async(
                        printer.ip_address,
                        printer.access_code,
                        remote_path,
                        temp_path,
                    ):
                        downloaded_filename = try_filename
                        logger.info(f"Downloaded: {remote_path}")
                        break
                except Exception as e:
                    logger.debug(f"FTP download failed for {remote_path}: {e}")

            if downloaded_filename:
                break

        # If still not found, try listing /cache to find matching file
        if not downloaded_filename and (filename or subtask_name):
            search_term = (subtask_name or filename).lower().replace(".gcode", "").replace(".3mf", "")
            try:
                cache_files = await list_files_async(printer.ip_address, printer.access_code, "/cache")
                for f in cache_files:
                    if f.get("is_directory"):
                        continue
                    fname = f.get("name", "")
                    if fname.endswith(".3mf") and search_term in fname.lower():
                        temp_path = app_settings.archive_dir / "temp" / fname
                        temp_path.parent.mkdir(parents=True, exist_ok=True)
                        if await download_file_async(
                            printer.ip_address,
                            printer.access_code,
                            f"/cache/{fname}",
                            temp_path,
                        ):
                            downloaded_filename = fname
                            logger.info(f"Found and downloaded from cache: {fname}")
                            break
            except Exception as e:
                logger.warning(f"Failed to list cache: {e}")

        if not downloaded_filename or not temp_path:
            logger.warning(f"Could not find 3MF file for print: {filename or subtask_name}")
            return

        try:
            # Archive the file with status "printing"
            service = ArchiveService(db)
            archive = await service.archive_print(
                printer_id=printer_id,
                source_file=temp_path,
                print_data={**data, "status": "printing"},
            )

            if archive:
                # Track this active print (use both original filename and downloaded filename)
                _active_prints[(printer_id, downloaded_filename)] = archive.id
                if filename and filename != downloaded_filename:
                    _active_prints[(printer_id, filename)] = archive.id
                if subtask_name:
                    _active_prints[(printer_id, f"{subtask_name}.3mf")] = archive.id

                logger.info(f"Created archive {archive.id} for {downloaded_filename}")

                # Record starting energy from smart plug if available
                try:
                    plug_result = await db.execute(
                        select(SmartPlug).where(SmartPlug.printer_id == printer_id)
                    )
                    plug = plug_result.scalar_one_or_none()
                    logger.info(f"[ENERGY] Auto-archive print start - archive {archive.id}, printer {printer_id}, plug found: {plug is not None}")
                    if plug:
                        energy = await tasmota_service.get_energy(plug)
                        logger.info(f"[ENERGY] Auto-archive energy response: {energy}")
                        if energy and energy.get("total") is not None:
                            _print_energy_start[archive.id] = energy["total"]
                            logger.info(f"[ENERGY] Recorded starting energy for archive {archive.id}: {energy['total']} kWh")
                        else:
                            logger.warning(f"[ENERGY] No 'total' in energy response for archive {archive.id}")
                    else:
                        logger.info(f"[ENERGY] No smart plug found for printer {printer_id}")
                except Exception as e:
                    logger.warning(f"Failed to record starting energy: {e}")

                await ws_manager.send_archive_created({
                    "id": archive.id,
                    "printer_id": archive.printer_id,
                    "filename": archive.filename,
                    "print_name": archive.print_name,
                    "status": archive.status,
                })
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()

    # Smart plug automation: turn on plug when print starts
    try:
        async with async_session() as db:
            await smart_plug_manager.on_print_start(printer_id, db)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Smart plug on_print_start failed: {e}")

    # Send print start notifications
    try:
        async with async_session() as db:
            from backend.app.models.printer import Printer
            result = await db.execute(
                select(Printer).where(Printer.id == printer_id)
            )
            printer = result.scalar_one_or_none()
            printer_name = printer.name if printer else f"Printer {printer_id}"
            await notification_service.on_print_start(printer_id, printer_name, data, db)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Notification on_print_start failed: {e}")


async def on_print_complete(printer_id: int, data: dict):
    """Handle print completion - update the archive status."""
    import logging
    logger = logging.getLogger(__name__)

    await ws_manager.send_print_complete(printer_id, data)

    filename = data.get("filename", "")
    subtask_name = data.get("subtask_name", "")

    if not filename and not subtask_name:
        logger.warning(f"Print complete without filename or subtask_name")
        return

    logger.info(f"Print complete - filename: {filename}, subtask: {subtask_name}, status: {data.get('status')}")

    # Build list of possible keys to try (matching how they were registered in on_print_start)
    possible_keys = []

    # Try subtask_name variations first (most reliable for matching)
    if subtask_name:
        possible_keys.append((printer_id, f"{subtask_name}.3mf"))
        possible_keys.append((printer_id, f"{subtask_name}.gcode.3mf"))
        possible_keys.append((printer_id, subtask_name))

    # Try filename variations
    if filename:
        # Extract just the filename if it's a path
        fname = filename.split("/")[-1] if "/" in filename else filename

        if fname.endswith(".3mf"):
            possible_keys.append((printer_id, fname))
        elif fname.endswith(".gcode"):
            base_name = fname.rsplit(".", 1)[0]
            possible_keys.append((printer_id, f"{base_name}.gcode.3mf"))
            possible_keys.append((printer_id, f"{base_name}.3mf"))
            possible_keys.append((printer_id, fname))
        else:
            possible_keys.append((printer_id, f"{fname}.gcode.3mf"))
            possible_keys.append((printer_id, f"{fname}.3mf"))
            possible_keys.append((printer_id, fname))

        # Also try full path versions
        if filename.endswith(".3mf"):
            possible_keys.append((printer_id, filename))
        elif filename.endswith(".gcode"):
            base_name = filename.rsplit(".", 1)[0]
            possible_keys.append((printer_id, f"{base_name}.3mf"))
            possible_keys.append((printer_id, filename))
        else:
            possible_keys.append((printer_id, f"{filename}.3mf"))
            possible_keys.append((printer_id, filename))

    # Find the archive for this print
    logger.info(f"Looking for archive in _active_prints, keys to try: {possible_keys[:5]}...")
    logger.info(f"Current _active_prints: {list(_active_prints.keys())}")
    archive_id = None
    for key in possible_keys:
        archive_id = _active_prints.pop(key, None)
        if archive_id:
            logger.info(f"Found archive {archive_id} with key {key}")
            # Also clean up any other keys pointing to this archive
            keys_to_remove = [k for k, v in _active_prints.items() if v == archive_id]
            for k in keys_to_remove:
                _active_prints.pop(k, None)
            break

    if not archive_id:
        # Try to find by filename or subtask_name if not tracked (for prints started before app)
        async with async_session() as db:
            from backend.app.models.archive import PrintArchive

            # Try matching by subtask_name (stored as print_name) first
            if subtask_name:
                result = await db.execute(
                    select(PrintArchive)
                    .where(PrintArchive.printer_id == printer_id)
                    .where(PrintArchive.status == "printing")
                    .where(or_(
                        PrintArchive.print_name.ilike(f"%{subtask_name}%"),
                        PrintArchive.filename.ilike(f"%{subtask_name}%"),
                    ))
                    .order_by(PrintArchive.created_at.desc())
                    .limit(1)
                )
                archive = result.scalar_one_or_none()
                if archive:
                    archive_id = archive.id
                    logger.info(f"Found archive {archive_id} by subtask_name match: {subtask_name}")

            # Also try by filename
            if not archive_id and filename:
                result = await db.execute(
                    select(PrintArchive)
                    .where(PrintArchive.printer_id == printer_id)
                    .where(PrintArchive.filename == filename)
                    .where(PrintArchive.status == "printing")
                    .order_by(PrintArchive.created_at.desc())
                    .limit(1)
                )
                archive = result.scalar_one_or_none()
                if archive:
                    archive_id = archive.id

    if not archive_id:
        logger.warning(f"Could not find archive for print complete: filename={filename}, subtask={subtask_name}")
        return

    # Update archive status
    async with async_session() as db:
        service = ArchiveService(db)
        status = data.get("status", "completed")
        await service.update_archive_status(
            archive_id,
            status=status,
            completed_at=datetime.now() if status in ("completed", "failed", "aborted") else None,
        )

        await ws_manager.send_archive_updated({
            "id": archive_id,
            "status": status,
        })

    # Report filament usage to Spoolman if print completed successfully
    if data.get("status") == "completed":
        try:
            await _report_spoolman_usage(printer_id, archive_id, logger)
        except Exception as e:
            logger.warning(f"Spoolman usage reporting failed: {e}")

    # Calculate energy used for this print (always per-print: end - start)
    try:
        starting_kwh = _print_energy_start.pop(archive_id, None)
        logger.info(f"[ENERGY] Print complete for archive {archive_id}, starting_kwh={starting_kwh}")

        async with async_session() as db:
            # Get smart plug for this printer (SmartPlug is imported at module level)
            plug_result = await db.execute(
                select(SmartPlug).where(SmartPlug.printer_id == printer_id)
            )
            plug = plug_result.scalar_one_or_none()

            if plug:
                energy = await tasmota_service.get_energy(plug)
                logger.info(f"[ENERGY] Print complete - energy response: {energy}")

                energy_used = None

                # Calculate per-print energy: end total - start total
                if starting_kwh is not None and energy and energy.get("total") is not None:
                    ending_kwh = energy["total"]
                    energy_used = round(ending_kwh - starting_kwh, 4)
                    logger.info(f"[ENERGY] Per-print energy: ending={ending_kwh}, starting={starting_kwh}, used={energy_used}")
                elif starting_kwh is None:
                    logger.info(f"[ENERGY] No starting energy recorded for this archive")
                else:
                    logger.warning(f"[ENERGY] No 'total' in ending energy response")

                if energy_used is not None and energy_used >= 0:
                    # Get energy cost per kWh from settings (default to 0.15)
                    from backend.app.api.routes.settings import get_setting
                    energy_cost_per_kwh = await get_setting(db, "energy_cost_per_kwh")
                    cost_per_kwh = float(energy_cost_per_kwh) if energy_cost_per_kwh else 0.15
                    energy_cost = round(energy_used * cost_per_kwh, 2)

                    # Update archive with energy data
                    from backend.app.models.archive import PrintArchive
                    result = await db.execute(
                        select(PrintArchive).where(PrintArchive.id == archive_id)
                    )
                    archive = result.scalar_one_or_none()
                    if archive:
                        archive.energy_kwh = energy_used
                        archive.energy_cost = energy_cost
                        await db.commit()
                        logger.info(f"[ENERGY] Saved to archive {archive_id}: {energy_used} kWh, cost={energy_cost}")
                    else:
                        logger.warning(f"[ENERGY] Archive {archive_id} not found when saving energy")
            else:
                logger.info(f"[ENERGY] No smart plug found for printer {printer_id} at print complete")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to calculate energy: {e}")

    # Capture finish photo from printer camera
    logger.info(f"[PHOTO] Starting finish photo capture for archive {archive_id}")
    try:
        async with async_session() as db:
            # Check if finish photo capture is enabled
            from backend.app.api.routes.settings import get_setting
            capture_enabled = await get_setting(db, "capture_finish_photo")
            logger.info(f"[PHOTO] capture_finish_photo setting: {capture_enabled}")
            if capture_enabled is None or capture_enabled.lower() == "true":
                # Get printer details
                from backend.app.models.printer import Printer
                result = await db.execute(
                    select(Printer).where(Printer.id == printer_id)
                )
                printer = result.scalar_one_or_none()

                if printer and archive_id:
                    # Get archive to find its directory
                    from backend.app.models.archive import PrintArchive
                    result = await db.execute(
                        select(PrintArchive).where(PrintArchive.id == archive_id)
                    )
                    archive = result.scalar_one_or_none()

                    if archive:
                        from backend.app.services.camera import capture_finish_photo
                        from pathlib import Path

                        archive_dir = app_settings.base_dir / Path(archive.file_path).parent
                        photo_filename = await capture_finish_photo(
                            printer_id=printer_id,
                            ip_address=printer.ip_address,
                            access_code=printer.access_code,
                            model=printer.model,
                            archive_dir=archive_dir,
                        )

                        if photo_filename:
                            # Add photo to archive's photos list
                            photos = archive.photos or []
                            photos.append(photo_filename)
                            archive.photos = photos
                            await db.commit()
                            logger.info(f"Added finish photo to archive {archive_id}: {photo_filename}")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Finish photo capture failed: {e}")

    # Smart plug automation: schedule turn off when print completes
    logger.info(f"[AUTO-OFF] Calling smart_plug_manager.on_print_complete for printer {printer_id}")
    try:
        async with async_session() as db:
            status = data.get("status", "completed")
            await smart_plug_manager.on_print_complete(printer_id, status, db)
            logger.info(f"[AUTO-OFF] smart_plug_manager.on_print_complete completed")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Smart plug on_print_complete failed: {e}")

    # Send print complete notifications
    try:
        async with async_session() as db:
            from backend.app.models.printer import Printer
            result = await db.execute(
                select(Printer).where(Printer.id == printer_id)
            )
            printer = result.scalar_one_or_none()
            printer_name = printer.name if printer else f"Printer {printer_id}"
            status = data.get("status", "completed")

            # on_print_complete handles all status types: completed, failed, aborted, stopped
            await notification_service.on_print_complete(
                printer_id, printer_name, status, data, db
            )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Notification on_print_complete failed: {e}")

    # Check for maintenance due and send notifications (only for completed prints)
    if data.get("status") == "completed":
        try:
            async with async_session() as db:
                from backend.app.models.printer import Printer

                # Get printer name
                result = await db.execute(
                    select(Printer).where(Printer.id == printer_id)
                )
                printer = result.scalar_one_or_none()
                printer_name = printer.name if printer else f"Printer {printer_id}"

                # Get maintenance overview for this printer
                await ensure_default_types(db)
                overview = await _get_printer_maintenance_internal(printer_id, db, commit=True)

                # Check for any items that are due or have warnings
                items_needing_attention = [
                    {
                        "name": item.maintenance_type_name,
                        "is_due": item.is_due,
                        "is_warning": item.is_warning,
                    }
                    for item in overview.maintenance_items
                    if item.enabled and (item.is_due or item.is_warning)
                ]

                if items_needing_attention:
                    await notification_service.on_maintenance_due(
                        printer_id, printer_name, items_needing_attention, db
                    )
                    logger.info(
                        f"Sent maintenance notification for printer {printer_id}: "
                        f"{len(items_needing_attention)} items need attention"
                    )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Maintenance notification check failed: {e}")

    # Update queue item if this was a scheduled print
    try:
        async with async_session() as db:
            from backend.app.models.print_queue import PrintQueueItem
            # Note: SmartPlug is already imported at module level (line 56)
            # Do NOT import it here as it would shadow the module-level import
            # and cause "cannot access local variable" errors earlier in this function

            result = await db.execute(
                select(PrintQueueItem)
                .where(PrintQueueItem.printer_id == printer_id)
                .where(PrintQueueItem.status == "printing")
            )
            queue_item = result.scalar_one_or_none()
            if queue_item:
                status = data.get("status", "completed")
                queue_item.status = status
                queue_item.completed_at = datetime.now()
                await db.commit()
                logger.info(f"Updated queue item {queue_item.id} status to {status}")

                # Handle auto_off_after - power off printer if requested (after cooldown)
                if queue_item.auto_off_after:
                    result = await db.execute(
                        select(SmartPlug).where(SmartPlug.printer_id == printer_id)
                    )
                    plug = result.scalar_one_or_none()
                    if plug and plug.enabled:
                        logger.info(f"Auto-off requested for printer {printer_id}, waiting for cooldown...")

                        async def cooldown_and_poweroff(pid: int, plug_id: int):
                            # Wait for nozzle to cool down
                            await printer_manager.wait_for_cooldown(pid, target_temp=50.0, timeout=600)
                            # Re-fetch plug in new session
                            async with async_session() as new_db:
                                result = await new_db.execute(
                                    select(SmartPlug).where(SmartPlug.id == plug_id)
                                )
                                p = result.scalar_one_or_none()
                                if p and p.enabled:
                                    success = await tasmota_service.turn_off(p)
                                    if success:
                                        logger.info(f"Powered off printer {pid} via smart plug '{p.name}'")
                                    else:
                                        logger.warning(f"Failed to power off printer {pid} via smart plug")

                        asyncio.create_task(cooldown_and_poweroff(printer_id, plug.id))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Queue item update failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()

    # Set up printer manager callbacks
    loop = asyncio.get_event_loop()
    printer_manager.set_event_loop(loop)
    printer_manager.set_status_change_callback(on_printer_status_change)
    printer_manager.set_print_start_callback(on_print_start)
    printer_manager.set_print_complete_callback(on_print_complete)
    printer_manager.set_ams_change_callback(on_ams_change)

    # Connect to all active printers
    async with async_session() as db:
        await init_printer_connections(db)

    # Auto-connect to Spoolman if enabled
    async with async_session() as db:
        from backend.app.api.routes.settings import get_setting
        spoolman_enabled = await get_setting(db, "spoolman_enabled")
        spoolman_url = await get_setting(db, "spoolman_url")

        if spoolman_enabled and spoolman_enabled.lower() == "true" and spoolman_url:
            try:
                client = await init_spoolman_client(spoolman_url)
                if await client.health_check():
                    logging.info(f"Auto-connected to Spoolman at {spoolman_url}")
                else:
                    logging.warning(f"Spoolman at {spoolman_url} is not reachable")
            except Exception as e:
                logging.warning(f"Failed to auto-connect to Spoolman: {e}")

    # Start the print scheduler
    asyncio.create_task(print_scheduler.run())

    # Start the smart plug scheduler for time-based on/off
    smart_plug_manager.start_scheduler()

    # Start the notification digest scheduler
    notification_service.start_digest_scheduler()

    yield

    # Shutdown
    print_scheduler.stop()
    smart_plug_manager.stop_scheduler()
    notification_service.stop_digest_scheduler()
    printer_manager.disconnect_all()
    await close_spoolman_client()


app = FastAPI(
    title=app_settings.app_name,
    description="Archive and manage Bambu Lab 3MF files",
    version=APP_VERSION,
    lifespan=lifespan,
)

# API routes
app.include_router(printers.router, prefix=app_settings.api_prefix)
app.include_router(archives.router, prefix=app_settings.api_prefix)
app.include_router(filaments.router, prefix=app_settings.api_prefix)
app.include_router(settings_routes.router, prefix=app_settings.api_prefix)
app.include_router(cloud.router, prefix=app_settings.api_prefix)
app.include_router(smart_plugs.router, prefix=app_settings.api_prefix)
app.include_router(print_queue.router, prefix=app_settings.api_prefix)
app.include_router(kprofiles.router, prefix=app_settings.api_prefix)
app.include_router(notifications.router, prefix=app_settings.api_prefix)
app.include_router(notification_templates.router, prefix=app_settings.api_prefix)
app.include_router(spoolman.router, prefix=app_settings.api_prefix)
app.include_router(updates.router, prefix=app_settings.api_prefix)
app.include_router(maintenance.router, prefix=app_settings.api_prefix)
app.include_router(camera.router, prefix=app_settings.api_prefix)
app.include_router(websocket.router, prefix=app_settings.api_prefix)


# Serve static files (React build)
if app_settings.static_dir.exists() and any(app_settings.static_dir.iterdir()):
    app.mount(
        "/assets",
        StaticFiles(directory=app_settings.static_dir / "assets"),
        name="assets",
    )
    if (app_settings.static_dir / "img").exists():
        app.mount(
            "/img",
            StaticFiles(directory=app_settings.static_dir / "img"),
            name="img",
        )
    if (app_settings.static_dir / "icons").exists():
        app.mount(
            "/icons",
            StaticFiles(directory=app_settings.static_dir / "icons"),
            name="icons",
        )


@app.get("/")
async def serve_frontend():
    """Serve the React frontend."""
    index_file = app_settings.static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {
        "message": "Bambuddy API",
        "docs": "/docs",
        "frontend": "Build and place React app in /static directory",
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


# Catch-all route for React Router (must be last)
@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """Serve React app for client-side routing."""
    # Don't intercept API routes
    if full_path.startswith("api/"):
        return {"error": "Not found"}

    index_file = app_settings.static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)

    return {"error": "Frontend not built"}
