import io
import logging
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.core.database import get_db
from backend.app.core.config import settings
from backend.app.models.printer import Printer
from backend.app.schemas.printer import (
    PrinterCreate,
    PrinterUpdate,
    PrinterResponse,
    PrinterStatus,
)
from backend.app.services.printer_manager import printer_manager
from backend.app.services.bambu_ftp import (
    download_file_try_paths_async,
    list_files_async,
    delete_file_async,
    download_file_bytes_async,
    get_storage_info_async,
)


router = APIRouter(prefix="/printers", tags=["printers"])


@router.get("/", response_model=list[PrinterResponse])
async def list_printers(db: AsyncSession = Depends(get_db)):
    """List all configured printers."""
    result = await db.execute(select(Printer).order_by(Printer.name))
    return list(result.scalars().all())


@router.post("/", response_model=PrinterResponse)
async def create_printer(
    printer_data: PrinterCreate,
    db: AsyncSession = Depends(get_db),
):
    """Add a new printer."""
    # Check if serial number already exists
    result = await db.execute(
        select(Printer).where(Printer.serial_number == printer_data.serial_number)
    )
    if result.scalar_one_or_none():
        raise HTTPException(400, "Printer with this serial number already exists")

    printer = Printer(**printer_data.model_dump())
    db.add(printer)
    await db.commit()
    await db.refresh(printer)

    # Connect to the printer
    if printer.is_active:
        await printer_manager.connect_printer(printer)

    return printer


@router.get("/{printer_id}", response_model=PrinterResponse)
async def get_printer(printer_id: int, db: AsyncSession = Depends(get_db)):
    """Get a specific printer."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")
    return printer


@router.patch("/{printer_id}", response_model=PrinterResponse)
async def update_printer(
    printer_id: int,
    printer_data: PrinterUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a printer."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    update_data = printer_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(printer, field, value)

    await db.commit()
    await db.refresh(printer)

    # Reconnect if connection settings changed
    if any(k in update_data for k in ["ip_address", "access_code", "is_active"]):
        printer_manager.disconnect_printer(printer_id)
        if printer.is_active:
            await printer_manager.connect_printer(printer)

    return printer


@router.delete("/{printer_id}")
async def delete_printer(printer_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a printer."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    printer_manager.disconnect_printer(printer_id)
    await db.delete(printer)
    await db.commit()

    return {"status": "deleted"}


@router.get("/{printer_id}/status", response_model=PrinterStatus)
async def get_printer_status(printer_id: int, db: AsyncSession = Depends(get_db)):
    """Get real-time status of a printer."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    state = printer_manager.get_status(printer_id)
    if not state:
        return PrinterStatus(
            id=printer_id,
            name=printer.name,
            connected=False,
        )

    # Determine cover URL if there's an active print
    cover_url = None
    if state.state == "RUNNING" and state.gcode_file:
        cover_url = f"/api/v1/printers/{printer_id}/cover"

    return PrinterStatus(
        id=printer_id,
        name=printer.name,
        connected=state.connected,
        state=state.state,
        current_print=state.current_print,
        subtask_name=state.subtask_name,
        gcode_file=state.gcode_file,
        progress=state.progress,
        remaining_time=state.remaining_time,
        layer_num=state.layer_num,
        total_layers=state.total_layers,
        temperatures=state.temperatures,
        cover_url=cover_url,
    )


@router.post("/{printer_id}/connect")
async def connect_printer(printer_id: int, db: AsyncSession = Depends(get_db)):
    """Manually connect to a printer."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    success = await printer_manager.connect_printer(printer)
    return {"connected": success}


@router.post("/{printer_id}/disconnect")
async def disconnect_printer(printer_id: int, db: AsyncSession = Depends(get_db)):
    """Manually disconnect from a printer."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    printer_manager.disconnect_printer(printer_id)
    return {"connected": False}


@router.post("/test")
async def test_printer_connection(
    ip_address: str,
    serial_number: str,
    access_code: str,
):
    """Test connection to a printer without saving."""
    result = await printer_manager.test_connection(
        ip_address=ip_address,
        serial_number=serial_number,
        access_code=access_code,
    )
    return result


# Cache for cover images (printer_id -> (gcode_file, image_bytes))
_cover_cache: dict[int, tuple[str, bytes]] = {}


@router.get("/{printer_id}/cover")
async def get_printer_cover(printer_id: int, db: AsyncSession = Depends(get_db)):
    """Get the cover image for the current print job."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    state = printer_manager.get_status(printer_id)
    if not state:
        raise HTTPException(404, "Printer not connected")

    # Use subtask_name as the 3MF filename (gcode_file is the path inside the 3MF)
    subtask_name = state.subtask_name
    if not subtask_name:
        raise HTTPException(404, f"No subtask_name in printer state (state={state.state})")

    # Check cache
    if printer_id in _cover_cache:
        cached_file, cached_image = _cover_cache[printer_id]
        if cached_file == subtask_name:
            return Response(content=cached_image, media_type="image/png")

    # Build 3MF filename from subtask_name
    # Bambu printers store files as "name.gcode.3mf"
    filename = subtask_name
    if not filename.endswith(".3mf"):
        filename = filename + ".gcode.3mf"

    # Try to download the 3MF file from printer
    temp_path = settings.archive_dir / "temp" / f"cover_{printer_id}_{filename}"
    temp_path.parent.mkdir(parents=True, exist_ok=True)

    remote_paths = [
        f"/{filename}",  # Root directory (most common)
        f"/cache/{filename}",
        f"/model/{filename}",
        f"/data/{filename}",
    ]

    logger.info(f"Trying to download cover for '{filename}' from {printer.ip_address}")

    try:
        downloaded = await download_file_try_paths_async(
            printer.ip_address,
            printer.access_code,
            remote_paths,
            temp_path,
        )
    except Exception as e:
        logger.error(f"FTP download exception: {e}")
        raise HTTPException(500, f"FTP download failed: {e}")

    if not downloaded:
        raise HTTPException(404, f"Could not download 3MF file '{filename}' from printer {printer.ip_address}. Tried: {remote_paths}")

    # Verify file actually exists and has content
    if not temp_path.exists():
        raise HTTPException(500, f"Download reported success but file not found: {temp_path}")

    file_size = temp_path.stat().st_size
    logger.info(f"Downloaded file size: {file_size} bytes")

    if file_size == 0:
        temp_path.unlink()
        raise HTTPException(500, f"Downloaded file is empty: {filename}")

    try:
        # Extract thumbnail from 3MF (which is a ZIP file)
        try:
            zf = zipfile.ZipFile(temp_path, 'r')
        except zipfile.BadZipFile as e:
            raise HTTPException(500, f"Downloaded file is not a valid 3MF/ZIP: {e}")
        except Exception as e:
            raise HTTPException(500, f"Failed to open 3MF file: {e}")

        try:
            # Try common thumbnail paths in 3MF files
            thumbnail_paths = [
                "Metadata/plate_1.png",
                "Metadata/thumbnail.png",
                "Metadata/plate_1_small.png",
                "Thumbnails/thumbnail.png",
                "thumbnail.png",
            ]

            for thumb_path in thumbnail_paths:
                try:
                    image_data = zf.read(thumb_path)
                    # Cache the result
                    _cover_cache[printer_id] = (subtask_name, image_data)
                    return Response(content=image_data, media_type="image/png")
                except KeyError:
                    continue

            # If no specific thumbnail found, try any PNG in Metadata
            for name in zf.namelist():
                if name.startswith("Metadata/") and name.endswith(".png"):
                    image_data = zf.read(name)
                    _cover_cache[printer_id] = (subtask_name, image_data)
                    return Response(content=image_data, media_type="image/png")

            raise HTTPException(404, "No thumbnail found in 3MF file")
        finally:
            zf.close()

    finally:
        if temp_path.exists():
            temp_path.unlink()


# ============================================
# File Manager Endpoints
# ============================================

@router.get("/{printer_id}/files")
async def list_printer_files(
    printer_id: int,
    path: str = "/",
    db: AsyncSession = Depends(get_db),
):
    """List files on the printer at the specified path."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    files = await list_files_async(printer.ip_address, printer.access_code, path)

    # Add full path to each file
    for f in files:
        f["path"] = f"{path.rstrip('/')}/{f['name']}" if path != "/" else f"/{f['name']}"

    return {
        "path": path,
        "files": files,
    }


@router.get("/{printer_id}/files/download")
async def download_printer_file(
    printer_id: int,
    path: str,
    db: AsyncSession = Depends(get_db),
):
    """Download a file from the printer."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    data = await download_file_bytes_async(printer.ip_address, printer.access_code, path)
    if data is None:
        raise HTTPException(404, f"File not found: {path}")

    # Determine content type based on extension
    filename = path.split("/")[-1]
    ext = filename.lower().split(".")[-1] if "." in filename else ""

    content_types = {
        "3mf": "application/vnd.ms-package.3dmanufacturing-3dmodel+xml",
        "gcode": "text/plain",
        "mp4": "video/mp4",
        "avi": "video/x-msvideo",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "json": "application/json",
        "txt": "text/plain",
    }
    content_type = content_types.get(ext, "application/octet-stream")

    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/{printer_id}/files")
async def delete_printer_file(
    printer_id: int,
    path: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a file from the printer."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    success = await delete_file_async(printer.ip_address, printer.access_code, path)
    if not success:
        raise HTTPException(500, f"Failed to delete file: {path}")

    return {"status": "deleted", "path": path}


@router.get("/{printer_id}/storage")
async def get_printer_storage(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get storage information from the printer."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    storage_info = await get_storage_info_async(printer.ip_address, printer.access_code)

    return storage_info or {"used_bytes": None, "free_bytes": None}
