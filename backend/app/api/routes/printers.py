import logging
import zipfile

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.models.printer import Printer
from backend.app.models.slot_preset import SlotPresetMapping
from backend.app.schemas.printer import (
    AMSTray,
    AMSUnit,
    HMSErrorResponse,
    NozzleInfoResponse,
    PrinterCreate,
    PrinterResponse,
    PrinterStatus,
    PrinterUpdate,
    PrintOptionsResponse,
)
from backend.app.services.bambu_ftp import (
    delete_file_async,
    download_file_bytes_async,
    download_file_try_paths_async,
    get_storage_info_async,
    list_files_async,
)
from backend.app.services.printer_manager import get_derived_status_name, printer_manager

logger = logging.getLogger(__name__)
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
    result = await db.execute(select(Printer).where(Printer.serial_number == printer_data.serial_number))
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
async def delete_printer(
    printer_id: int,
    delete_archives: bool = True,
    db: AsyncSession = Depends(get_db),
):
    """Delete a printer.

    Args:
        printer_id: ID of the printer to delete
        delete_archives: If True (default), delete all print archives for this printer.
                        If False, keep archives but remove their printer association.
    """
    from backend.app.models.archive import PrintArchive

    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    printer_manager.disconnect_printer(printer_id)

    if not delete_archives:
        # Orphan the archives instead of deleting them
        from sqlalchemy import update

        await db.execute(update(PrintArchive).where(PrintArchive.printer_id == printer_id).values(printer_id=None))

    await db.delete(printer)
    await db.commit()

    return {"status": "deleted", "archives_deleted": delete_archives}


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

    # Determine cover URL if there's an active print (including paused)
    cover_url = None
    if state.state in ("RUNNING", "PAUSE", "PAUSED") and state.gcode_file:
        cover_url = f"/api/v1/printers/{printer_id}/cover"

    # Convert HMS errors to response format
    hms_errors = [
        HMSErrorResponse(code=e.code, attr=e.attr, module=e.module, severity=e.severity)
        for e in (state.hms_errors or [])
    ]

    # Parse AMS data from raw_data
    ams_units = []
    vt_tray = None
    ams_exists = False
    raw_data = state.raw_data or {}

    # Build K-profile lookup map: cali_idx -> k_value
    # This allows looking up the calibrated K value for each AMS slot
    kprofile_map: dict[int, float] = {}
    for kp in state.kprofiles or []:
        if kp.slot_id is not None and kp.k_value:
            try:
                kprofile_map[kp.slot_id] = float(kp.k_value)
            except (ValueError, TypeError):
                pass

    if "ams" in raw_data and isinstance(raw_data["ams"], list):
        ams_exists = True
        for ams_data in raw_data["ams"]:
            # Skip if ams_data is not a dict (defensive check)
            if not isinstance(ams_data, dict):
                continue
            trays = []
            for tray_data in ams_data.get("tray", []):
                # Filter out empty/invalid tag values
                tag_uid = tray_data.get("tag_uid", "")
                if tag_uid in ("", "0000000000000000"):
                    tag_uid = None
                tray_uuid = tray_data.get("tray_uuid", "")
                if tray_uuid in ("", "00000000000000000000000000000000"):
                    tray_uuid = None

                # Get K value: first try tray's k field, then lookup from K-profiles
                k_value = tray_data.get("k")
                cali_idx = tray_data.get("cali_idx")
                if k_value is None and cali_idx is not None and cali_idx in kprofile_map:
                    k_value = kprofile_map[cali_idx]

                trays.append(
                    AMSTray(
                        id=tray_data.get("id", 0),
                        tray_color=tray_data.get("tray_color"),
                        tray_type=tray_data.get("tray_type"),
                        tray_sub_brands=tray_data.get("tray_sub_brands"),
                        tray_id_name=tray_data.get("tray_id_name"),
                        tray_info_idx=tray_data.get("tray_info_idx"),
                        remain=tray_data.get("remain", 0),
                        k=k_value,
                        cali_idx=cali_idx,
                        tag_uid=tag_uid,
                        tray_uuid=tray_uuid,
                        nozzle_temp_min=tray_data.get("nozzle_temp_min"),
                        nozzle_temp_max=tray_data.get("nozzle_temp_max"),
                    )
                )
            # Prefer humidity_raw (percentage) over humidity (index 1-5)
            # humidity_raw is the actual percentage value from the sensor
            humidity_raw = ams_data.get("humidity_raw")
            humidity_idx = ams_data.get("humidity")
            humidity_value = None

            if humidity_raw is not None:
                try:
                    humidity_value = int(humidity_raw)
                except (ValueError, TypeError):
                    pass
            if humidity_value is None and humidity_idx is not None:
                try:
                    humidity_value = int(humidity_idx)
                except (ValueError, TypeError):
                    pass
            # AMS-HT has 1 tray, regular AMS has 4 trays
            is_ams_ht = len(trays) == 1

            ams_units.append(
                AMSUnit(
                    id=ams_data.get("id", 0),
                    humidity=humidity_value,
                    temp=ams_data.get("temp"),
                    is_ams_ht=is_ams_ht,
                    tray=trays,
                )
            )

    # Virtual tray (external spool holder) - comes from vt_tray in raw_data
    if "vt_tray" in raw_data:
        vt_data = raw_data["vt_tray"]
        # Filter out empty/invalid tag values for vt_tray
        vt_tag_uid = vt_data.get("tag_uid", "")
        if vt_tag_uid in ("", "0000000000000000"):
            vt_tag_uid = None
        vt_tray_uuid = vt_data.get("tray_uuid", "")
        if vt_tray_uuid in ("", "00000000000000000000000000000000"):
            vt_tray_uuid = None

        # Get K value: first try tray's k field, then lookup from K-profiles
        vt_k_value = vt_data.get("k")
        vt_cali_idx = vt_data.get("cali_idx")
        if vt_k_value is None and vt_cali_idx is not None and vt_cali_idx in kprofile_map:
            vt_k_value = kprofile_map[vt_cali_idx]

        vt_tray = AMSTray(
            id=254,  # Virtual tray ID
            tray_color=vt_data.get("tray_color"),
            tray_type=vt_data.get("tray_type"),
            tray_sub_brands=vt_data.get("tray_sub_brands"),
            tray_id_name=vt_data.get("tray_id_name"),
            tray_info_idx=vt_data.get("tray_info_idx"),
            remain=vt_data.get("remain", 0),
            k=vt_k_value,
            cali_idx=vt_cali_idx,
            tag_uid=vt_tag_uid,
            tray_uuid=vt_tray_uuid,
            nozzle_temp_min=vt_data.get("nozzle_temp_min"),
            nozzle_temp_max=vt_data.get("nozzle_temp_max"),
        )

    # Convert nozzle info to response format
    nozzles = [
        NozzleInfoResponse(
            nozzle_type=n.nozzle_type,
            nozzle_diameter=n.nozzle_diameter,
        )
        for n in (state.nozzles or [])
    ]

    # Convert print options to response format
    print_options = PrintOptionsResponse(
        spaghetti_detector=state.print_options.spaghetti_detector,
        print_halt=state.print_options.print_halt,
        halt_print_sensitivity=state.print_options.halt_print_sensitivity,
        first_layer_inspector=state.print_options.first_layer_inspector,
        printing_monitor=state.print_options.printing_monitor,
        buildplate_marker_detector=state.print_options.buildplate_marker_detector,
        allow_skip_parts=state.print_options.allow_skip_parts,
        nozzle_clumping_detector=state.print_options.nozzle_clumping_detector,
        nozzle_clumping_sensitivity=state.print_options.nozzle_clumping_sensitivity,
        pileup_detector=state.print_options.pileup_detector,
        pileup_sensitivity=state.print_options.pileup_sensitivity,
        airprint_detector=state.print_options.airprint_detector,
        airprint_sensitivity=state.print_options.airprint_sensitivity,
        auto_recovery_step_loss=state.print_options.auto_recovery_step_loss,
        filament_tangle_detect=state.print_options.filament_tangle_detect,
    )

    # Get AMS mapping from raw_data (which AMS is connected to which nozzle)
    ams_mapping = raw_data.get("ams_mapping", [])
    # Get per-AMS extruder map: {ams_id: extruder_id} where 0=right, 1=left
    ams_extruder_map = raw_data.get("ams_extruder_map", {})
    logger.debug(f"API returning ams_mapping: {ams_mapping}, ams_extruder_map: {ams_extruder_map}")

    # tray_now from MQTT is already a global tray ID: (ams_id * 4) + slot_id
    # Per OpenBambuAPI docs: 254 = external spool, 255 = no filament, otherwise global tray ID
    # No conversion needed - just use the raw value directly
    tray_now = state.tray_now
    logger.debug(f"Using tray_now directly as global ID: {tray_now}")

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
        hms_errors=hms_errors,
        ams=ams_units,
        ams_exists=ams_exists,
        vt_tray=vt_tray,
        sdcard=state.sdcard,
        store_to_sdcard=state.store_to_sdcard,
        timelapse=state.timelapse,
        ipcam=state.ipcam,
        wifi_signal=state.wifi_signal,
        nozzles=nozzles,
        print_options=print_options,
        stg_cur=state.stg_cur,
        stg_cur_name=get_derived_status_name(state),
        stg=state.stg,
        airduct_mode=state.airduct_mode,
        speed_level=state.speed_level,
        chamber_light=state.chamber_light,
        active_extruder=state.active_extruder,
        ams_mapping=ams_mapping,
        ams_extruder_map=ams_extruder_map,
        tray_now=tray_now,
        ams_status_main=state.ams_status_main,
        ams_status_sub=state.ams_status_sub,
        mc_print_sub_stage=state.mc_print_sub_stage,
        last_ams_update=state.last_ams_update,
        printable_objects_count=len(state.printable_objects),
    )


@router.post("/{printer_id}/refresh-status")
async def refresh_printer_status(printer_id: int, db: AsyncSession = Depends(get_db)):
    """Request a full status refresh from the printer (sends pushall command)."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    success = printer_manager.request_status_update(printer_id)
    if not success:
        raise HTTPException(400, "Printer not connected")

    return {"status": "refresh_requested"}


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


# Cache for cover images (printer_id -> {(gcode_file, view) -> image_bytes})
_cover_cache: dict[int, dict[tuple[str, str], bytes]] = {}


@router.get("/{printer_id}/cover")
async def get_printer_cover(
    printer_id: int,
    view: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Get the cover image for the current print job.

    Args:
        view: Optional view type. Use "top" for top-down build plate view (useful for skip objects).
              Default returns angled 3D perspective view.
    """
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

    # Normalize view parameter
    view_key = view or "default"

    # Check cache
    if printer_id in _cover_cache:
        cache_key = (subtask_name, view_key)
        if cache_key in _cover_cache[printer_id]:
            return Response(content=_cover_cache[printer_id][cache_key], media_type="image/png")

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
        raise HTTPException(
            404, f"Could not download 3MF file '{filename}' from printer {printer.ip_address}. Tried: {remote_paths}"
        )

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
            zf = zipfile.ZipFile(temp_path, "r")
        except zipfile.BadZipFile as e:
            raise HTTPException(500, f"Downloaded file is not a valid 3MF/ZIP: {e}")
        except Exception as e:
            raise HTTPException(500, f"Failed to open 3MF file: {e}")

        try:
            # Try common thumbnail paths in 3MF files
            # Use top-down view if requested (better for skip objects modal)
            if view == "top":
                thumbnail_paths = [
                    "Metadata/top_1.png",
                    "Metadata/top_2.png",
                    "Metadata/top_3.png",
                    "Metadata/top_4.png",
                    # Fall back to regular views if no top view
                    "Metadata/plate_1.png",
                    "Metadata/thumbnail.png",
                ]
            else:
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
                    if printer_id not in _cover_cache:
                        _cover_cache[printer_id] = {}
                    _cover_cache[printer_id][(subtask_name, view_key)] = image_data
                    return Response(content=image_data, media_type="image/png")
                except KeyError:
                    continue

            # If no specific thumbnail found, try any PNG in Metadata
            for name in zf.namelist():
                if name.startswith("Metadata/") and name.endswith(".png"):
                    image_data = zf.read(name)
                    if printer_id not in _cover_cache:
                        _cover_cache[printer_id] = {}
                    _cover_cache[printer_id][(subtask_name, view_key)] = image_data
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


# ============================================
# MQTT Debug Logging Endpoints
# ============================================


@router.post("/{printer_id}/logging/enable")
async def enable_mqtt_logging(printer_id: int, db: AsyncSession = Depends(get_db)):
    """Enable MQTT message logging for a printer."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    success = printer_manager.enable_logging(printer_id, True)
    if not success:
        raise HTTPException(400, "Printer not connected")

    return {"logging_enabled": True}


@router.post("/{printer_id}/logging/disable")
async def disable_mqtt_logging(printer_id: int, db: AsyncSession = Depends(get_db)):
    """Disable MQTT message logging for a printer."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    success = printer_manager.enable_logging(printer_id, False)
    if not success:
        raise HTTPException(400, "Printer not connected")

    return {"logging_enabled": False}


@router.get("/{printer_id}/logging")
async def get_mqtt_logs(printer_id: int, db: AsyncSession = Depends(get_db)):
    """Get MQTT message logs for a printer."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    logs = printer_manager.get_logs(printer_id)
    return {
        "logging_enabled": printer_manager.is_logging_enabled(printer_id),
        "logs": [
            {
                "timestamp": log.timestamp,
                "topic": log.topic,
                "direction": log.direction,
                "payload": log.payload,
            }
            for log in logs
        ],
    }


@router.delete("/{printer_id}/logging")
async def clear_mqtt_logs(printer_id: int, db: AsyncSession = Depends(get_db)):
    """Clear MQTT message logs for a printer."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    printer_manager.clear_logs(printer_id)
    return {"status": "cleared"}


# ============================================
# Print Options (AI Detection) Endpoints
# ============================================


@router.post("/{printer_id}/print-options")
async def set_print_option(
    printer_id: int,
    module_name: str,
    enabled: bool,
    print_halt: bool = True,
    sensitivity: str = "medium",
    db: AsyncSession = Depends(get_db),
):
    """Set an AI detection / print option on the printer.

    Valid module_name values:
    - spaghetti_detector: Spaghetti detection
    - first_layer_inspector: First layer inspection
    - printing_monitor: AI print quality monitoring
    - buildplate_marker_detector: Build plate marker detection
    - allow_skip_parts: Allow skipping failed parts
    """
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    client = printer_manager.get_client(printer_id)
    if not client or not client.state.connected:
        raise HTTPException(400, "Printer not connected")

    # Validate module_name
    valid_modules = [
        "spaghetti_detector",
        "first_layer_inspector",
        "printing_monitor",
        "buildplate_marker_detector",
        "allow_skip_parts",
        "pileup_detector",
        "clump_detector",
        "airprint_detector",
        "auto_recovery_step_loss",
    ]
    if module_name not in valid_modules:
        raise HTTPException(400, f"Invalid module_name. Must be one of: {valid_modules}")

    # Validate sensitivity
    valid_sensitivities = ["low", "medium", "high", "never_halt"]
    if sensitivity not in valid_sensitivities:
        raise HTTPException(400, f"Invalid sensitivity. Must be one of: {valid_sensitivities}")

    success = client.set_xcam_option(
        module_name=module_name,
        enabled=enabled,
        print_halt=print_halt,
        sensitivity=sensitivity,
    )

    if not success:
        raise HTTPException(500, "Failed to send command to printer")

    return {
        "success": True,
        "module_name": module_name,
        "enabled": enabled,
        "print_halt": print_halt,
        "sensitivity": sensitivity,
    }


# ============================================
# Calibration
# ============================================


@router.post("/{printer_id}/calibration")
async def start_calibration(
    printer_id: int,
    bed_leveling: bool = False,
    vibration: bool = False,
    motor_noise: bool = False,
    nozzle_offset: bool = False,
    high_temp_heatbed: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Start printer calibration with selected options.

    At least one option must be selected.

    Options:
    - bed_leveling: Run bed leveling calibration
    - vibration: Run vibration compensation calibration
    - motor_noise: Run motor noise cancellation calibration
    - nozzle_offset: Run nozzle offset calibration (dual nozzle printers)
    - high_temp_heatbed: Run high-temperature heatbed calibration
    """
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    client = printer_manager.get_client(printer_id)
    if not client or not client.state.connected:
        raise HTTPException(400, "Printer not connected")

    # Check that at least one option is selected
    if not any([bed_leveling, vibration, motor_noise, nozzle_offset, high_temp_heatbed]):
        raise HTTPException(400, "At least one calibration option must be selected")

    success = client.start_calibration(
        bed_leveling=bed_leveling,
        vibration=vibration,
        motor_noise=motor_noise,
        nozzle_offset=nozzle_offset,
        high_temp_heatbed=high_temp_heatbed,
    )

    if not success:
        raise HTTPException(500, "Failed to send calibration command to printer")

    return {
        "success": True,
        "bed_leveling": bed_leveling,
        "vibration": vibration,
        "motor_noise": motor_noise,
        "nozzle_offset": nozzle_offset,
        "high_temp_heatbed": high_temp_heatbed,
    }


# ============================================================================
# Slot Preset Mapping Endpoints
# ============================================================================


@router.get("/{printer_id}/slot-presets")
async def get_slot_presets(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get all saved slot-to-preset mappings for a printer."""
    result = await db.execute(select(SlotPresetMapping).where(SlotPresetMapping.printer_id == printer_id))
    mappings = result.scalars().all()

    return {
        mapping.ams_id * 4 + mapping.tray_id: {
            "ams_id": mapping.ams_id,
            "tray_id": mapping.tray_id,
            "preset_id": mapping.preset_id,
            "preset_name": mapping.preset_name,
        }
        for mapping in mappings
    }


@router.get("/{printer_id}/slot-presets/{ams_id}/{tray_id}")
async def get_slot_preset(
    printer_id: int,
    ams_id: int,
    tray_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get the saved preset for a specific slot."""
    result = await db.execute(
        select(SlotPresetMapping).where(
            SlotPresetMapping.printer_id == printer_id,
            SlotPresetMapping.ams_id == ams_id,
            SlotPresetMapping.tray_id == tray_id,
        )
    )
    mapping = result.scalar_one_or_none()

    if not mapping:
        return None

    return {
        "ams_id": mapping.ams_id,
        "tray_id": mapping.tray_id,
        "preset_id": mapping.preset_id,
        "preset_name": mapping.preset_name,
    }


@router.put("/{printer_id}/slot-presets/{ams_id}/{tray_id}")
async def save_slot_preset(
    printer_id: int,
    ams_id: int,
    tray_id: int,
    preset_id: str,
    preset_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Save a preset mapping for a specific slot."""
    # Check printer exists
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Printer not found")

    # Check for existing mapping
    result = await db.execute(
        select(SlotPresetMapping).where(
            SlotPresetMapping.printer_id == printer_id,
            SlotPresetMapping.ams_id == ams_id,
            SlotPresetMapping.tray_id == tray_id,
        )
    )
    mapping = result.scalar_one_or_none()

    if mapping:
        # Update existing
        mapping.preset_id = preset_id
        mapping.preset_name = preset_name
    else:
        # Create new
        mapping = SlotPresetMapping(
            printer_id=printer_id,
            ams_id=ams_id,
            tray_id=tray_id,
            preset_id=preset_id,
            preset_name=preset_name,
        )
        db.add(mapping)

    await db.commit()
    await db.refresh(mapping)

    return {
        "ams_id": mapping.ams_id,
        "tray_id": mapping.tray_id,
        "preset_id": mapping.preset_id,
        "preset_name": mapping.preset_name,
    }


@router.delete("/{printer_id}/slot-presets/{ams_id}/{tray_id}")
async def delete_slot_preset(
    printer_id: int,
    ams_id: int,
    tray_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a saved preset mapping for a slot."""
    result = await db.execute(
        select(SlotPresetMapping).where(
            SlotPresetMapping.printer_id == printer_id,
            SlotPresetMapping.ams_id == ams_id,
            SlotPresetMapping.tray_id == tray_id,
        )
    )
    mapping = result.scalar_one_or_none()

    if mapping:
        await db.delete(mapping)
        await db.commit()

    return {"success": True}


@router.post("/{printer_id}/debug/simulate-print-complete")
async def debug_simulate_print_complete(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
):
    """DEBUG: Simulate print completion to test freeze behavior.

    This triggers the same code path as a real print completion,
    without needing to wait for an actual print to finish.
    """
    from backend.app.main import _active_prints, on_print_complete
    from backend.app.models.archive import PrintArchive

    # Get the most recent archive for this printer
    result = await db.execute(
        select(PrintArchive)
        .where(PrintArchive.printer_id == printer_id)
        .order_by(PrintArchive.created_at.desc())
        .limit(1)
    )
    archive = result.scalar_one_or_none()

    if not archive:
        raise HTTPException(status_code=404, detail="No archives found for this printer")

    # Register this archive as "active" so on_print_complete can find it
    filename = archive.file_path.split("/")[-1] if archive.file_path else "test.3mf"
    subtask_name = archive.print_name or "Test Print"
    _active_prints[(printer_id, filename)] = archive.id
    _active_prints[(printer_id, subtask_name)] = archive.id

    # Simulate print completion data
    data = {
        "status": "completed",
        "filename": filename,
        "subtask_name": subtask_name,
        "timelapse_was_active": False,
    }

    logger.info(f"Simulating print complete for printer {printer_id}, archive {archive.id}")

    # Call the actual on_print_complete handler
    await on_print_complete(printer_id, data)

    return {"success": True, "archive_id": archive.id, "message": "Print completion simulated"}


# =============================================================================
# Print Control Endpoints
# =============================================================================


@router.post("/{printer_id}/print/stop")
async def stop_print(printer_id: int, db: AsyncSession = Depends(get_db)):
    """Stop/cancel the current print job."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    client = printer_manager.get_client(printer_id)
    if not client:
        raise HTTPException(400, "Printer not connected")

    success = client.stop_print()
    if not success:
        raise HTTPException(500, "Failed to stop print")

    return {"success": True, "message": "Print stop command sent"}


@router.post("/{printer_id}/print/pause")
async def pause_print(printer_id: int, db: AsyncSession = Depends(get_db)):
    """Pause the current print job."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    client = printer_manager.get_client(printer_id)
    if not client:
        raise HTTPException(400, "Printer not connected")

    success = client.pause_print()
    if not success:
        raise HTTPException(500, "Failed to pause print")

    return {"success": True, "message": "Print pause command sent"}


@router.post("/{printer_id}/print/resume")
async def resume_print(printer_id: int, db: AsyncSession = Depends(get_db)):
    """Resume a paused print job."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    client = printer_manager.get_client(printer_id)
    if not client:
        raise HTTPException(400, "Printer not connected")

    success = client.resume_print()
    if not success:
        raise HTTPException(500, "Failed to resume print")

    return {"success": True, "message": "Print resume command sent"}


@router.get("/{printer_id}/print/objects")
async def get_printable_objects(
    printer_id: int,
    reload: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Get the list of printable objects for the current print.

    Returns a list of objects with id, name, position (if available), and skip status.
    Objects that have already been skipped are marked in the skipped_objects list.

    Args:
        reload: If True, reload objects from the archive file (useful after restart)
    """
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    client = printer_manager.get_client(printer_id)
    if not client:
        raise HTTPException(400, "Printer not connected")

    # Reload objects from 3MF if requested or no objects loaded
    if reload or not client.state.printable_objects:
        subtask_name = client.state.subtask_name
        if subtask_name:
            from backend.app.services.archive import extract_printable_objects_from_3mf
            from backend.app.services.bambu_ftp import download_file_try_paths_async

            # Build 3MF filename
            filename = subtask_name
            if not filename.endswith(".3mf"):
                filename = filename + ".gcode.3mf"

            # Download 3MF from printer
            temp_path = settings.archive_dir / "temp" / f"objects_{printer_id}_{filename}"
            temp_path.parent.mkdir(parents=True, exist_ok=True)

            remote_paths = [f"/{filename}", f"/cache/{filename}", f"/model/{filename}"]

            try:
                downloaded = await download_file_try_paths_async(
                    printer.ip_address, printer.access_code, remote_paths, temp_path
                )
                if downloaded and temp_path.exists():
                    with open(temp_path, "rb") as f:
                        data = f.read()
                    objects = extract_printable_objects_from_3mf(data, include_positions=True)
                    if objects:
                        client.state.printable_objects = objects
                        logger.info(f"Reloaded {len(objects)} objects for printer {printer_id}")
            except Exception as e:
                logger.debug(f"Failed to reload objects from printer: {e}")
            finally:
                if temp_path.exists():
                    temp_path.unlink()

    # Return objects with their skip status and position data
    objects = []
    for obj_id, obj_data in client.state.printable_objects.items():
        # Handle both old format (string name) and new format (dict with name, x, y)
        if isinstance(obj_data, dict):
            obj_entry = {
                "id": obj_id,
                "name": obj_data.get("name", f"Object {obj_id}"),
                "x": obj_data.get("x"),
                "y": obj_data.get("y"),
                "skipped": obj_id in client.state.skipped_objects,
            }
        else:
            # Legacy format: obj_data is just the name string
            obj_entry = {
                "id": obj_id,
                "name": obj_data,
                "x": None,
                "y": None,
                "skipped": obj_id in client.state.skipped_objects,
            }
        objects.append(obj_entry)

    return {
        "objects": objects,
        "total": len(objects),
        "skipped_count": len(client.state.skipped_objects),
        "is_printing": client.state.state in ("RUNNING", "PAUSE"),
    }


@router.post("/{printer_id}/print/skip-objects")
async def skip_objects(
    printer_id: int,
    object_ids: list[int],
    db: AsyncSession = Depends(get_db),
):
    """Skip specific objects during the current print.

    Args:
        object_ids: List of object identify_id values to skip
    """
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    client = printer_manager.get_client(printer_id)
    if not client:
        raise HTTPException(400, "Printer not connected")

    if not object_ids:
        raise HTTPException(400, "No object IDs provided")

    # Validate object IDs exist in printable_objects
    invalid_ids = [oid for oid in object_ids if oid not in client.state.printable_objects]
    if invalid_ids:
        raise HTTPException(400, f"Invalid object IDs: {invalid_ids}")

    success = client.skip_objects(object_ids)
    if not success:
        raise HTTPException(500, "Failed to skip objects")

    # Get names of skipped objects for response (handle both old and new format)
    skipped_names = []
    for oid in object_ids:
        obj_data = client.state.printable_objects.get(oid, str(oid))
        if isinstance(obj_data, dict):
            skipped_names.append(obj_data.get("name", str(oid)))
        else:
            skipped_names.append(obj_data)

    return {
        "success": True,
        "message": f"Skipped {len(object_ids)} object(s): {', '.join(skipped_names)}",
        "skipped_objects": object_ids,
    }


# =============================================================================
# AMS Control Endpoints
# =============================================================================


@router.post("/{printer_id}/ams/{ams_id}/slot/{slot_id}/refresh")
async def refresh_ams_slot(
    printer_id: int,
    ams_id: int,
    slot_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Re-read RFID for an AMS slot (triggers filament info refresh)."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    client = printer_manager.get_client(printer_id)
    if not client:
        raise HTTPException(400, "Printer not connected")

    success, message = client.ams_refresh_tray(ams_id, slot_id)
    if not success:
        raise HTTPException(400, message)

    return {"success": True, "message": message}
