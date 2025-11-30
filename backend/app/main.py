import asyncio
import logging
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI

# Configure logging for all modules - console + file
log_format = '%(asctime)s %(levelname)s [%(name)s] %(message)s'
log_level = logging.INFO

# Create root logger
root_logger = logging.getLogger()
root_logger.setLevel(log_level)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(log_level)
console_handler.setFormatter(logging.Formatter(log_format))
root_logger.addHandler(console_handler)

# File handler - rotating log file (5MB max, keep 3 backups)
log_file = Path(__file__).parent.parent.parent / "bambutrack.log"
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
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.app.core.config import settings as app_settings
from backend.app.core.database import init_db, async_session
from backend.app.core.websocket import ws_manager
from backend.app.api.routes import printers, archives, websocket, filaments, cloud, smart_plugs, print_queue
from backend.app.api.routes import settings as settings_routes
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

async def on_printer_status_change(printer_id: int, state: PrinterState):
    """Handle printer status changes - broadcast via WebSocket."""
    # Only broadcast if something meaningful changed (reduce WebSocket spam)
    status_key = f"{state.connected}:{state.state}:{state.progress}:{state.layer_num}"
    if _last_status_broadcast.get(printer_id) == status_key:
        return  # No change, skip broadcast
    _last_status_broadcast[printer_id] = status_key

    await ws_manager.send_printer_status(
        printer_id,
        printer_state_to_dict(state, printer_id),
    )


async def on_print_start(printer_id: int, data: dict):
    """Handle print start - archive the 3MF file immediately."""
    import logging
    logger = logging.getLogger(__name__)

    await ws_manager.send_print_start(printer_id, data)

    async with async_session() as db:
        from backend.app.models.printer import Printer
        from backend.app.services.bambu_ftp import list_files_async
        from sqlalchemy import select

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
                    if plug:
                        energy = await tasmota_service.get_energy(plug)
                        if energy and energy.get("total") is not None:
                            _print_energy_start[archive.id] = energy["total"]
                            logger.info(f"Recorded starting energy for archive {archive.id}: {energy['total']} kWh")
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
                    if plug:
                        energy = await tasmota_service.get_energy(plug)
                        if energy and energy.get("total") is not None:
                            _print_energy_start[archive.id] = energy["total"]
                            logger.info(f"Recorded starting energy for archive {archive.id}: {energy['total']} kWh")
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


async def on_print_complete(printer_id: int, data: dict):
    """Handle print completion - update the archive status."""
    import logging
    logger = logging.getLogger(__name__)

    await ws_manager.send_print_complete(printer_id, data)

    filename = data.get("filename", "")
    if not filename:
        return

    logger.info(f"Print complete - filename: {filename}, status: {data.get('status')}")

    # Build list of possible keys to try
    possible_keys = []

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
    archive_id = None
    for key in possible_keys:
        archive_id = _active_prints.pop(key, None)
        if archive_id:
            # Also clean up any other keys pointing to this archive
            keys_to_remove = [k for k, v in _active_prints.items() if v == archive_id]
            for k in keys_to_remove:
                _active_prints.pop(k, None)
            break

    if not archive_id:
        # Try to find by filename if not tracked (for prints started before app)
        async with async_session() as db:
            from backend.app.models.archive import PrintArchive
            from sqlalchemy import select

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

    # Calculate energy used for this print
    try:
        starting_kwh = _print_energy_start.pop(archive_id, None)
        if starting_kwh is not None:
            async with async_session() as db:
                # Get smart plug for this printer
                plug_result = await db.execute(
                    select(SmartPlug).where(SmartPlug.printer_id == printer_id)
                )
                plug = plug_result.scalar_one_or_none()

                if plug:
                    energy = await tasmota_service.get_energy(plug)
                    if energy and energy.get("total") is not None:
                        ending_kwh = energy["total"]
                        energy_used = round(ending_kwh - starting_kwh, 4)

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
                            logger.info(f"Recorded energy for archive {archive_id}: {energy_used} kWh (${energy_cost})")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to calculate energy: {e}")

    # Capture finish photo from printer camera
    try:
        async with async_session() as db:
            # Check if finish photo capture is enabled
            from backend.app.api.routes.settings import get_setting
            capture_enabled = await get_setting(db, "capture_finish_photo")
            if capture_enabled is None or capture_enabled.lower() == "true":
                # Get printer details
                from backend.app.models.printer import Printer
                from sqlalchemy import select
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
    try:
        async with async_session() as db:
            status = data.get("status", "completed")
            await smart_plug_manager.on_print_complete(printer_id, status, db)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Smart plug on_print_complete failed: {e}")

    # Update queue item if this was a scheduled print
    try:
        async with async_session() as db:
            from backend.app.models.print_queue import PrintQueueItem
            from backend.app.models.smart_plug import SmartPlug
            from backend.app.services.tasmota import tasmota_service

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

    # Connect to all active printers
    async with async_session() as db:
        await init_printer_connections(db)

    # Start the print scheduler
    asyncio.create_task(print_scheduler.run())

    yield

    # Shutdown
    print_scheduler.stop()
    printer_manager.disconnect_all()


app = FastAPI(
    title=app_settings.app_name,
    description="Archive and manage Bambu Lab 3MF files",
    version="0.1.2",
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


@app.get("/")
async def serve_frontend():
    """Serve the React frontend."""
    index_file = app_settings.static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {
        "message": "BambuTrack API",
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
