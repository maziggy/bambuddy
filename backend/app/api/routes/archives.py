from pathlib import Path
import zipfile
import io

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.models.archive import PrintArchive
from backend.app.schemas.archive import ArchiveResponse, ArchiveUpdate, ArchiveStats
from backend.app.services.archive import ArchiveService


router = APIRouter(prefix="/archives", tags=["archives"])


@router.get("/", response_model=list[ArchiveResponse])
async def list_archives(
    printer_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """List archived prints."""
    service = ArchiveService(db)
    return await service.list_archives(
        printer_id=printer_id,
        limit=limit,
        offset=offset,
    )


@router.get("/stats", response_model=ArchiveStats)
async def get_archive_stats(db: AsyncSession = Depends(get_db)):
    """Get statistics across all archives."""
    # Total counts
    total_result = await db.execute(select(func.count(PrintArchive.id)))
    total_prints = total_result.scalar() or 0

    successful_result = await db.execute(
        select(func.count(PrintArchive.id)).where(PrintArchive.status == "completed")
    )
    successful_prints = successful_result.scalar() or 0

    failed_result = await db.execute(
        select(func.count(PrintArchive.id)).where(PrintArchive.status == "failed")
    )
    failed_prints = failed_result.scalar() or 0

    # Totals
    time_result = await db.execute(
        select(func.sum(PrintArchive.print_time_seconds))
    )
    total_time = (time_result.scalar() or 0) / 3600  # Convert to hours

    filament_result = await db.execute(
        select(func.sum(PrintArchive.filament_used_grams))
    )
    total_filament = filament_result.scalar() or 0

    cost_result = await db.execute(
        select(func.sum(PrintArchive.cost))
    )
    total_cost = cost_result.scalar() or 0

    # By filament type (split comma-separated values for multi-material prints)
    filament_type_result = await db.execute(
        select(PrintArchive.filament_type)
        .where(PrintArchive.filament_type.isnot(None))
    )
    prints_by_filament: dict[str, int] = {}
    for (filament_types,) in filament_type_result.all():
        # Split by comma and count each type
        for ftype in filament_types.split(","):
            ftype = ftype.strip()
            if ftype:
                prints_by_filament[ftype] = prints_by_filament.get(ftype, 0) + 1

    # By printer
    printer_result = await db.execute(
        select(PrintArchive.printer_id, func.count(PrintArchive.id))
        .group_by(PrintArchive.printer_id)
    )
    prints_by_printer = {str(k): v for k, v in printer_result.all()}

    return ArchiveStats(
        total_prints=total_prints,
        successful_prints=successful_prints,
        failed_prints=failed_prints,
        total_print_time_hours=round(total_time, 1),
        total_filament_grams=round(total_filament, 1),
        total_cost=round(total_cost, 2),
        prints_by_filament_type=prints_by_filament,
        prints_by_printer=prints_by_printer,
    )


@router.get("/{archive_id}", response_model=ArchiveResponse)
async def get_archive(archive_id: int, db: AsyncSession = Depends(get_db)):
    """Get a specific archive."""
    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")
    return archive


@router.patch("/{archive_id}", response_model=ArchiveResponse)
async def update_archive(
    archive_id: int,
    update_data: ArchiveUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update archive metadata (tags, notes, cost, is_favorite)."""
    result = await db.execute(
        select(PrintArchive).where(PrintArchive.id == archive_id)
    )
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    for field, value in update_data.model_dump(exclude_unset=True).items():
        setattr(archive, field, value)

    await db.commit()
    await db.refresh(archive)
    return archive


@router.post("/{archive_id}/favorite", response_model=ArchiveResponse)
async def toggle_favorite(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Toggle favorite status for an archive."""
    result = await db.execute(
        select(PrintArchive).where(PrintArchive.id == archive_id)
    )
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    archive.is_favorite = not archive.is_favorite
    await db.commit()
    await db.refresh(archive)
    return archive


@router.post("/{archive_id}/rescan", response_model=ArchiveResponse)
async def rescan_archive(archive_id: int, db: AsyncSession = Depends(get_db)):
    """Rescan the 3MF file and update metadata."""
    from backend.app.services.archive import ThreeMFParser

    result = await db.execute(
        select(PrintArchive).where(PrintArchive.id == archive_id)
    )
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    file_path = settings.base_dir / archive.file_path
    if not file_path.exists():
        raise HTTPException(404, "Archive file not found")

    # Parse the 3MF file
    parser = ThreeMFParser(file_path)
    metadata = parser.parse()

    # Update fields from metadata
    if metadata.get("filament_type"):
        archive.filament_type = metadata["filament_type"]
    if metadata.get("filament_color"):
        archive.filament_color = metadata["filament_color"]
    if metadata.get("print_time_seconds"):
        archive.print_time_seconds = metadata["print_time_seconds"]
    if metadata.get("filament_used_grams"):
        archive.filament_used_grams = metadata["filament_used_grams"]
    if metadata.get("layer_height"):
        archive.layer_height = metadata["layer_height"]
    if metadata.get("nozzle_diameter"):
        archive.nozzle_diameter = metadata["nozzle_diameter"]
    if metadata.get("bed_temperature"):
        archive.bed_temperature = metadata["bed_temperature"]
    if metadata.get("nozzle_temperature"):
        archive.nozzle_temperature = metadata["nozzle_temperature"]
    if metadata.get("makerworld_url"):
        archive.makerworld_url = metadata["makerworld_url"]
    if metadata.get("designer"):
        archive.designer = metadata["designer"]

    await db.commit()
    await db.refresh(archive)
    return archive


@router.post("/rescan-all")
async def rescan_all_archives(db: AsyncSession = Depends(get_db)):
    """Rescan all archives and update their metadata."""
    from backend.app.services.archive import ThreeMFParser

    result = await db.execute(select(PrintArchive))
    archives = list(result.scalars().all())

    updated = 0
    errors = []

    for archive in archives:
        try:
            file_path = settings.base_dir / archive.file_path
            if not file_path.exists():
                errors.append({"id": archive.id, "error": "File not found"})
                continue

            parser = ThreeMFParser(file_path)
            metadata = parser.parse()

            if metadata.get("filament_type"):
                archive.filament_type = metadata["filament_type"]
            if metadata.get("filament_color"):
                archive.filament_color = metadata["filament_color"]
            if metadata.get("print_time_seconds"):
                archive.print_time_seconds = metadata["print_time_seconds"]
            if metadata.get("filament_used_grams"):
                archive.filament_used_grams = metadata["filament_used_grams"]
            if metadata.get("layer_height"):
                archive.layer_height = metadata["layer_height"]
            if metadata.get("nozzle_diameter"):
                archive.nozzle_diameter = metadata["nozzle_diameter"]
            if metadata.get("makerworld_url"):
                archive.makerworld_url = metadata["makerworld_url"]
            if metadata.get("designer"):
                archive.designer = metadata["designer"]

            updated += 1
        except Exception as e:
            errors.append({"id": archive.id, "error": str(e)})

    await db.commit()
    return {"updated": updated, "errors": errors}


@router.delete("/{archive_id}")
async def delete_archive(archive_id: int, db: AsyncSession = Depends(get_db)):
    """Delete an archive."""
    service = ArchiveService(db)
    if not await service.delete_archive(archive_id):
        raise HTTPException(404, "Archive not found")
    return {"status": "deleted"}


@router.get("/{archive_id}/download")
async def download_archive(
    archive_id: int,
    inline: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Download the 3MF file."""
    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    file_path = settings.base_dir / archive.file_path
    if not file_path.exists():
        raise HTTPException(404, "File not found")

    # Use inline disposition to let browser/OS handle file association
    content_disposition = "inline" if inline else "attachment"

    return FileResponse(
        path=file_path,
        filename=archive.filename,
        media_type="application/vnd.ms-package.3dmanufacturing-3dmodel+xml",
        content_disposition_type=content_disposition,
    )


@router.get("/{archive_id}/file/{filename}")
async def download_archive_with_filename(
    archive_id: int,
    filename: str,
    db: AsyncSession = Depends(get_db),
):
    """Download the 3MF file with filename in URL (for Bambu Studio protocol)."""
    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    file_path = settings.base_dir / archive.file_path
    if not file_path.exists():
        raise HTTPException(404, "File not found")

    return FileResponse(
        path=file_path,
        filename=archive.filename,
        media_type="application/vnd.ms-package.3dmanufacturing-3dmodel+xml",
    )


@router.get("/{archive_id}/thumbnail")
async def get_thumbnail(archive_id: int, db: AsyncSession = Depends(get_db)):
    """Get the thumbnail image."""
    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive or not archive.thumbnail_path:
        raise HTTPException(404, "Thumbnail not found")

    thumb_path = settings.base_dir / archive.thumbnail_path
    if not thumb_path.exists():
        raise HTTPException(404, "Thumbnail file not found")

    return FileResponse(path=thumb_path, media_type="image/png")


@router.get("/{archive_id}/timelapse")
async def get_timelapse(archive_id: int, db: AsyncSession = Depends(get_db)):
    """Get the timelapse video."""
    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive or not archive.timelapse_path:
        raise HTTPException(404, "Timelapse not found")

    timelapse_path = settings.base_dir / archive.timelapse_path
    if not timelapse_path.exists():
        raise HTTPException(404, "Timelapse file not found")

    return FileResponse(
        path=timelapse_path,
        media_type="video/mp4",
        filename=f"{archive.print_name or 'timelapse'}.mp4",
    )


@router.post("/{archive_id}/timelapse/scan")
async def scan_timelapse(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Scan printer for timelapse matching this archive and attach it."""
    from backend.app.models.printer import Printer
    from backend.app.services.bambu_ftp import list_files_async, download_file_bytes_async

    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    if archive.timelapse_path:
        return {"status": "exists", "message": "Timelapse already attached"}

    if not archive.printer_id:
        raise HTTPException(400, "Archive has no associated printer")

    # Get printer
    result = await db.execute(select(Printer).where(Printer.id == archive.printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    # Get base name from archive filename (without .3mf extension)
    base_name = Path(archive.filename).stem

    # Scan timelapse directory on printer
    try:
        files = await list_files_async(printer.ip_address, printer.access_code, "/timelapse/video")
    except Exception:
        raise HTTPException(500, "Failed to connect to printer")

    # Look for matching timelapse
    matching_file = None
    mp4_files = [f for f in files if not f.get("is_directory") and f.get("name", "").endswith(".mp4")]

    # Strategy 1: Match by print name in filename
    for f in mp4_files:
        fname = f.get("name", "")
        if base_name.lower() in fname.lower():
            matching_file = f
            break

    # Strategy 2: Match by timestamp proximity
    if not matching_file and (archive.started_at or archive.completed_at or archive.created_at):
        import re
        from datetime import datetime, timedelta

        archive_time = archive.started_at or archive.completed_at or archive.created_at
        best_match = None
        best_diff = timedelta(hours=24)  # Max 24 hour difference

        for f in mp4_files:
            fname = f.get("name", "")
            # Parse timestamp from filename like "video_2025-11-24_03-17-40.mp4"
            match = re.search(r'(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})', fname)
            if match:
                try:
                    file_time = datetime.strptime(match.group(1), "%Y-%m-%d_%H-%M-%S")
                    # Timelapse is usually created at print end, so compare to completed_at or created_at
                    compare_time = archive.completed_at or archive.created_at
                    if compare_time:
                        diff = abs(file_time - compare_time)
                        if diff < best_diff:
                            best_diff = diff
                            best_match = f
                except ValueError:
                    continue

        if best_match and best_diff < timedelta(hours=2):  # Within 2 hours
            matching_file = best_match

    if not matching_file:
        return {"status": "not_found", "message": "No matching timelapse found on printer"}

    # Download the timelapse
    remote_path = f"/timelapse/video/{matching_file['name']}"
    timelapse_data = await download_file_bytes_async(
        printer.ip_address, printer.access_code, remote_path
    )

    if not timelapse_data:
        raise HTTPException(500, "Failed to download timelapse")

    # Attach timelapse to archive
    success = await service.attach_timelapse(
        archive_id, timelapse_data, matching_file["name"]
    )

    if not success:
        raise HTTPException(500, "Failed to attach timelapse")

    return {
        "status": "attached",
        "message": f"Timelapse '{matching_file['name']}' attached successfully",
        "filename": matching_file["name"],
    }


@router.post("/{archive_id}/timelapse/upload")
async def upload_timelapse(
    archive_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Manually upload a timelapse video to an archive."""
    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    if not file.filename or not file.filename.endswith((".mp4", ".avi", ".mkv")):
        raise HTTPException(400, "File must be a video file (.mp4, .avi, .mkv)")

    content = await file.read()
    success = await service.attach_timelapse(archive_id, content, file.filename)

    if not success:
        raise HTTPException(500, "Failed to attach timelapse")

    return {"status": "attached", "filename": file.filename}


# ============================================
# Photo Endpoints
# ============================================

@router.post("/{archive_id}/photos")
async def upload_photo(
    archive_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a photo of the printed result."""
    result = await db.execute(
        select(PrintArchive).where(PrintArchive.id == archive_id)
    )
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    if not file.filename or not file.filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        raise HTTPException(400, "File must be an image (.jpg, .jpeg, .png, .webp)")

    # Get archive directory
    file_path = settings.base_dir / archive.file_path
    archive_dir = file_path.parent
    photos_dir = archive_dir / "photos"
    photos_dir.mkdir(exist_ok=True)

    # Generate unique filename
    import uuid
    ext = Path(file.filename).suffix.lower()
    photo_filename = f"{uuid.uuid4().hex[:8]}{ext}"
    photo_path = photos_dir / photo_filename

    # Save file
    content = await file.read()
    photo_path.write_bytes(content)

    # Update archive photos list (create new list to trigger SQLAlchemy change detection)
    photos = list(archive.photos or [])
    photos.append(photo_filename)
    archive.photos = photos

    await db.commit()
    await db.refresh(archive)

    return {"status": "uploaded", "filename": photo_filename, "photos": archive.photos}


@router.get("/{archive_id}/photos/{filename}")
async def get_photo(
    archive_id: int,
    filename: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a specific photo."""
    result = await db.execute(
        select(PrintArchive).where(PrintArchive.id == archive_id)
    )
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    file_path = settings.base_dir / archive.file_path
    photo_path = file_path.parent / "photos" / filename

    if not photo_path.exists():
        raise HTTPException(404, "Photo not found")

    # Determine media type
    ext = Path(filename).suffix.lower()
    media_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }
    media_type = media_types.get(ext, "image/jpeg")

    return FileResponse(path=photo_path, media_type=media_type)


@router.delete("/{archive_id}/photos/{filename}")
async def delete_photo(
    archive_id: int,
    filename: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a photo."""
    result = await db.execute(
        select(PrintArchive).where(PrintArchive.id == archive_id)
    )
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    if not archive.photos or filename not in archive.photos:
        raise HTTPException(404, "Photo not found")

    # Delete file
    file_path = settings.base_dir / archive.file_path
    photo_path = file_path.parent / "photos" / filename
    if photo_path.exists():
        photo_path.unlink()

    # Update archive photos list
    photos = [p for p in archive.photos if p != filename]
    archive.photos = photos if photos else None

    await db.commit()

    return {"status": "deleted", "photos": archive.photos}


# ============================================
# QR Code Endpoint
# ============================================

@router.get("/{archive_id}/qrcode")
async def get_qrcode(
    archive_id: int,
    request: Request,
    size: int = 200,
    db: AsyncSession = Depends(get_db),
):
    """Generate a QR code that links to this archive."""
    import qrcode
    from qrcode.image.styledpil import StyledPilImage

    result = await db.execute(
        select(PrintArchive).where(PrintArchive.id == archive_id)
    )
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    # Build URL to archive detail page
    base_url = str(request.base_url).rstrip('/')
    archive_url = f"{base_url}/archives?id={archive_id}"

    # Generate QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(archive_url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    # Resize if needed
    if size != 200:
        img = img.resize((size, size))

    # Convert to bytes
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return Response(
        content=buffer.getvalue(),
        media_type="image/png",
        headers={
            "Content-Disposition": f'inline; filename="qr_{archive.print_name or archive_id}.png"'
        }
    )


@router.get("/{archive_id}/capabilities")
async def get_archive_capabilities(archive_id: int, db: AsyncSession = Depends(get_db)):
    """Check what viewing capabilities are available for this 3MF file."""
    import json
    import re

    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    file_path = settings.base_dir / archive.file_path
    if not file_path.exists():
        raise HTTPException(404, "File not found")

    has_model = False
    has_gcode = False
    build_volume = {"x": 256, "y": 256, "z": 256}  # Default to X1/P1 size

    try:
        with zipfile.ZipFile(file_path, 'r') as zf:
            names = zf.namelist()

            # Check for G-code
            has_gcode = any(n.startswith('Metadata/') and n.endswith('.gcode') for n in names)

            # Check for 3D model - need to look for actual mesh data
            for name in names:
                if name.endswith('.model'):
                    try:
                        content = zf.read(name).decode('utf-8')
                        # Check if this model file contains actual mesh vertices
                        if '<vertex' in content or '<mesh' in content:
                            has_model = True
                            break
                    except Exception:
                        pass

            # Extract build volume from project settings
            if 'Metadata/project_settings.config' in names:
                try:
                    config_content = zf.read('Metadata/project_settings.config').decode('utf-8')
                    config_data = json.loads(config_content)

                    # Parse printable_area: ['0x0', '256x0', '256x256', '0x256']
                    printable_area = config_data.get('printable_area', [])
                    if printable_area and len(printable_area) >= 3:
                        # Get max X and Y from the corner coordinates
                        max_x = 0
                        max_y = 0
                        for coord in printable_area:
                            if 'x' in coord:
                                parts = coord.split('x')
                                if len(parts) == 2:
                                    try:
                                        x, y = int(parts[0]), int(parts[1])
                                        max_x = max(max_x, x)
                                        max_y = max(max_y, y)
                                    except ValueError:
                                        pass
                        if max_x > 0 and max_y > 0:
                            build_volume["x"] = max_x
                            build_volume["y"] = max_y

                    # Parse printable_height
                    printable_height = config_data.get('printable_height')
                    if printable_height:
                        try:
                            build_volume["z"] = int(printable_height)
                        except (ValueError, TypeError):
                            pass
                except Exception:
                    pass

    except zipfile.BadZipFile:
        raise HTTPException(400, "Invalid 3MF file")

    return {
        "has_model": has_model,
        "has_gcode": has_gcode,
        "build_volume": build_volume,
    }


@router.get("/{archive_id}/gcode")
async def get_gcode(archive_id: int, db: AsyncSession = Depends(get_db)):
    """Extract and return G-code from the 3MF file."""
    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    file_path = settings.base_dir / archive.file_path
    if not file_path.exists():
        raise HTTPException(404, "File not found")

    try:
        with zipfile.ZipFile(file_path, 'r') as zf:
            # Bambu 3MF files store G-code in Metadata/plate_X.gcode
            gcode_files = [n for n in zf.namelist() if n.startswith('Metadata/') and n.endswith('.gcode')]
            if not gcode_files:
                raise HTTPException(
                    404,
                    "No G-code found. This file hasn't been sliced yet - G-code is only available after slicing in Bambu Studio."
                )

            # Get the first plate's G-code (usually plate_1.gcode)
            gcode_content = zf.read(gcode_files[0]).decode('utf-8')
            return Response(content=gcode_content, media_type="text/plain")
    except zipfile.BadZipFile:
        raise HTTPException(400, "Invalid 3MF file")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error extracting G-code: {str(e)}")


@router.post("/upload")
async def upload_archive(
    file: UploadFile = File(...),
    printer_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Manually upload a 3MF file to archive."""
    if not file.filename or not file.filename.endswith(".3mf"):
        raise HTTPException(400, "File must be a .3mf file")

    # Save uploaded file temporarily
    temp_path = settings.archive_dir / "temp" / file.filename
    temp_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        content = await file.read()
        temp_path.write_bytes(content)

        service = ArchiveService(db)
        archive = await service.archive_print(
            printer_id=printer_id,
            source_file=temp_path,
        )

        if not archive:
            raise HTTPException(400, "Failed to archive file")

        return ArchiveResponse.model_validate(archive)
    finally:
        if temp_path.exists():
            temp_path.unlink()


@router.post("/upload-bulk")
async def upload_archives_bulk(
    files: list[UploadFile] = File(...),
    printer_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Bulk upload multiple 3MF files to archive."""
    results = []
    errors = []

    for file in files:
        if not file.filename or not file.filename.endswith(".3mf"):
            errors.append({"filename": file.filename or "unknown", "error": "Not a .3mf file"})
            continue

        temp_path = settings.archive_dir / "temp" / file.filename
        temp_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            content = await file.read()
            temp_path.write_bytes(content)

            service = ArchiveService(db)
            archive = await service.archive_print(
                printer_id=printer_id,
                source_file=temp_path,
            )

            if archive:
                results.append({
                    "filename": file.filename,
                    "id": archive.id,
                    "status": "success",
                })
            else:
                errors.append({"filename": file.filename, "error": "Failed to process"})
        except Exception as e:
            errors.append({"filename": file.filename, "error": str(e)})
        finally:
            if temp_path.exists():
                temp_path.unlink()

    return {
        "uploaded": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors,
    }


@router.post("/{archive_id}/reprint")
async def reprint_archive(
    archive_id: int,
    printer_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Send an archived 3MF file to a printer and start printing."""
    from backend.app.models.printer import Printer
    from backend.app.services.bambu_ftp import upload_file_async
    from backend.app.services.printer_manager import printer_manager

    # Get archive
    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    # Get printer
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    # Check printer is connected
    if not printer_manager.is_connected(printer_id):
        raise HTTPException(400, "Printer is not connected")

    # Get the 3MF file path
    file_path = settings.base_dir / archive.file_path
    if not file_path.exists():
        raise HTTPException(404, "Archive file not found")

    # Upload file to printer via FTP
    remote_filename = archive.filename
    remote_path = f"/cache/{remote_filename}"

    uploaded = await upload_file_async(
        printer.ip_address,
        printer.access_code,
        file_path,
        remote_path,
    )

    if not uploaded:
        raise HTTPException(500, "Failed to upload file to printer")

    # Start the print
    started = printer_manager.start_print(printer_id, remote_filename)

    if not started:
        raise HTTPException(500, "Failed to start print")

    return {
        "status": "printing",
        "printer_id": printer_id,
        "archive_id": archive_id,
        "filename": archive.filename,
    }
