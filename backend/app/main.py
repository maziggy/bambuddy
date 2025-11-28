import asyncio
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.app.core.config import settings as app_settings
from backend.app.core.database import init_db, async_session
from backend.app.core.websocket import ws_manager
from backend.app.api.routes import printers, archives, websocket, filaments, cloud, smart_plugs
from backend.app.api.routes import settings as settings_routes
from backend.app.services.printer_manager import (
    printer_manager,
    printer_state_to_dict,
    init_printer_connections,
)
from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.archive import ArchiveService
from backend.app.services.bambu_ftp import download_file_async
from backend.app.services.smart_plug_manager import smart_plug_manager


# Track active prints: {(printer_id, filename): archive_id}
_active_prints: dict[tuple[int, str], int] = {}


async def on_printer_status_change(printer_id: int, state: PrinterState):
    """Handle printer status changes - broadcast via WebSocket."""
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

        # Build list of possible 3MF filenames to try
        possible_names = []

        # Try original filename with .3mf extension
        if filename:
            if filename.endswith(".3mf"):
                possible_names.append(filename)
            elif filename.endswith(".gcode"):
                base = filename.rsplit(".", 1)[0]
                possible_names.append(f"{base}.3mf")
            else:
                # No extension - try adding .3mf
                possible_names.append(f"{filename}.3mf")
                possible_names.append(filename)

        # Try subtask_name with .3mf extension
        if subtask_name and subtask_name != filename:
            possible_names.append(f"{subtask_name}.3mf")

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
                if await download_file_async(
                    printer.ip_address,
                    printer.access_code,
                    remote_path,
                    temp_path,
                ):
                    downloaded_filename = try_filename
                    logger.info(f"Downloaded: {remote_path}")
                    break

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
            completed_at=datetime.now() if status in ("completed", "failed") else None,
        )

        await ws_manager.send_archive_updated({
            "id": archive_id,
            "status": status,
        })

    # Smart plug automation: schedule turn off when print completes
    try:
        async with async_session() as db:
            status = data.get("status", "completed")
            await smart_plug_manager.on_print_complete(printer_id, status, db)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Smart plug on_print_complete failed: {e}")


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

    yield

    # Shutdown
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
