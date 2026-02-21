import asyncio
import logging
import re
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.printer import Printer
from backend.app.models.slot_preset import SlotPresetMapping
from backend.app.schemas.printer import (
    AMSTray,
    AMSUnit,
    HMSErrorResponse,
    NozzleInfoResponse,
    NozzleRackSlot,
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
from backend.app.services.printer_manager import get_derived_status_name, printer_manager, supports_chamber_temp

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/printers", tags=["printers"])


@router.get("/", response_model=list[PrinterResponse])
async def list_printers(
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
    db: AsyncSession = Depends(get_db),
):
    """List all configured printers."""
    result = await db.execute(select(Printer).order_by(Printer.name))
    return list(result.scalars().all())


@router.post("/", response_model=PrinterResponse)
async def create_printer(
    printer_data: PrinterCreate,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_CREATE),
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


@router.get("/usb-cameras")
async def list_usb_cameras(
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
):
    """List available USB cameras connected to the system.

    Returns a list of detected V4L2 video devices with their info.
    Only works on Linux systems with V4L2 support.

    Returns:
        List of dicts with {device: str, name: str, capabilities: list, formats?: list}
    """
    from backend.app.services.external_camera import list_usb_cameras

    cameras = list_usb_cameras()
    return {"cameras": cameras}


@router.get("/developer-mode-warnings")
async def get_developer_mode_warnings(
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
    db: AsyncSession = Depends(get_db),
):
    """Check if any connected printer lacks developer LAN mode."""
    result = await db.execute(select(Printer).where(Printer.is_active == True))  # noqa: E712
    printers = result.scalars().all()
    statuses = printer_manager.get_all_statuses()

    warnings = []
    for printer in printers:
        state = statuses.get(printer.id)
        if state and state.connected and state.developer_mode is False:
            warnings.append(
                {
                    "printer_id": printer.id,
                    "name": printer.name,
                }
            )
    return warnings


@router.get("/{printer_id}", response_model=PrinterResponse)
async def get_printer(
    printer_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
    db: AsyncSession = Depends(get_db),
):
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
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_UPDATE),
    db: AsyncSession = Depends(get_db),
):
    """Update a printer."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    update_data = printer_data.model_dump(exclude_unset=True)

    # Handle nested ROI object - flatten to individual columns
    if "plate_detection_roi" in update_data:
        roi = update_data.pop("plate_detection_roi")
        if roi:
            update_data["plate_detection_roi_x"] = roi.get("x")
            update_data["plate_detection_roi_y"] = roi.get("y")
            update_data["plate_detection_roi_w"] = roi.get("w")
            update_data["plate_detection_roi_h"] = roi.get("h")
        else:
            # Clear ROI if set to null
            update_data["plate_detection_roi_x"] = None
            update_data["plate_detection_roi_y"] = None
            update_data["plate_detection_roi_w"] = None
            update_data["plate_detection_roi_h"] = None

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
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_DELETE),
    db: AsyncSession = Depends(get_db),
):
    """Delete a printer.

    Args:
        printer_id: ID of the printer to delete
        delete_archives: If True (default), delete all print archives for this printer.
                        If False, keep archives but remove their printer association.
    """
    from sqlalchemy import delete as sql_delete

    from backend.app.models.archive import PrintArchive
    from backend.app.models.maintenance import MaintenanceHistory, PrinterMaintenance

    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    printer_manager.disconnect_printer(printer_id)

    if delete_archives:
        # Delete all archives for this printer
        await db.execute(sql_delete(PrintArchive).where(PrintArchive.printer_id == printer_id))
    else:
        # Orphan the archives instead of deleting them
        from sqlalchemy import update

        await db.execute(update(PrintArchive).where(PrintArchive.printer_id == printer_id).values(printer_id=None))

    # Delete maintenance history and items for this printer
    # (SQLite doesn't enforce FK cascades, so do it explicitly)
    maintenance_ids = (
        (await db.execute(select(PrinterMaintenance.id).where(PrinterMaintenance.printer_id == printer_id)))
        .scalars()
        .all()
    )
    if maintenance_ids:
        await db.execute(
            sql_delete(MaintenanceHistory).where(MaintenanceHistory.printer_maintenance_id.in_(maintenance_ids))
        )
        await db.execute(sql_delete(PrinterMaintenance).where(PrinterMaintenance.printer_id == printer_id))

    await db.delete(printer)
    await db.commit()

    return {"status": "deleted", "archives_deleted": delete_archives}


@router.get("/{printer_id}/status", response_model=PrinterStatus)
async def get_printer_status(
    printer_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
    db: AsyncSession = Depends(get_db),
):
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
    if state.state in ("RUNNING", "PAUSE") and state.gcode_file:
        cover_url = f"/api/v1/printers/{printer_id}/cover"

    # Convert HMS errors to response format
    hms_errors = [
        HMSErrorResponse(code=e.code, attr=e.attr, module=e.module, severity=e.severity)
        for e in (state.hms_errors or [])
    ]

    # Parse AMS data from raw_data
    ams_units = []
    vt_tray = []
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
                pass  # Skip K-profile entries with unparseable values

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
                    pass  # Skip unparseable humidity; will try index fallback
            if humidity_value is None and humidity_idx is not None:
                try:
                    humidity_value = int(humidity_idx)
                except (ValueError, TypeError):
                    pass  # Skip unparseable humidity index; humidity remains None
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

    # Virtual tray (external spool holder) - comes from vt_tray in raw_data (list)
    if "vt_tray" in raw_data:
        for vt_data in raw_data["vt_tray"]:
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

            tray_id = int(vt_data.get("id", 254))
            vt_tray.append(
                AMSTray(
                    id=tray_id,
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
            )

    # Convert nozzle info to response format
    nozzles = [
        NozzleInfoResponse(
            nozzle_type=n.nozzle_type,
            nozzle_diameter=n.nozzle_diameter,
        )
        for n in (state.nozzles or [])
    ]

    # H2C nozzle rack (tool-changer dock positions)
    nozzle_rack = [
        NozzleRackSlot(
            id=n.get("id", 0),
            nozzle_type=n.get("type", ""),
            nozzle_diameter=n.get("diameter", ""),
            wear=n.get("wear"),
            stat=n.get("stat"),
            max_temp=n.get("max_temp", 0),
            serial_number=n.get("serial_number", ""),
            filament_color=n.get("filament_color", ""),
            filament_id=n.get("filament_id", ""),
            filament_type=n.get("filament_type", ""),
        )
        for n in (state.nozzle_rack or [])
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
    # Get per-AMS extruder map from state attribute (not raw_data, to avoid race condition
    # where raw_data gets replaced during MQTT updates and ams_extruder_map is temporarily missing)
    ams_extruder_map = state.ams_extruder_map or {}
    logger.debug("API returning ams_mapping: %s, ams_extruder_map: %s", ams_mapping, ams_extruder_map)

    # tray_now from MQTT is already a global tray ID: (ams_id * 4) + slot_id
    # Per OpenBambuAPI docs: 254 = external spool, 255 = no filament, otherwise global tray ID
    # No conversion needed - just use the raw value directly
    tray_now = state.tray_now
    logger.debug("Using tray_now directly as global ID: %s", tray_now)

    # Filter out chamber temp for models that don't have a real sensor
    # P1P, P1S, A1, A1Mini report meaningless chamber_temper values
    temperatures = state.temperatures
    if not supports_chamber_temp(printer.model):
        temperatures = {
            k: v for k, v in temperatures.items() if k not in ("chamber", "chamber_target", "chamber_heating")
        }

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
        temperatures=temperatures,
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
        nozzle_rack=nozzle_rack,
        print_options=print_options,
        stg_cur=state.stg_cur,
        stg_cur_name=get_derived_status_name(state, printer.model),
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
        cooling_fan_speed=state.cooling_fan_speed,
        big_fan1_speed=state.big_fan1_speed,
        big_fan2_speed=state.big_fan2_speed,
        heatbreak_fan_speed=state.heatbreak_fan_speed,
        firmware_version=state.firmware_version,
        developer_mode=state.developer_mode if state else None,
        plate_cleared=printer_manager.is_plate_cleared(printer_id),
    )


@router.get("/{printer_id}/current-print-user")
async def get_current_print_user(
    printer_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
    db: AsyncSession = Depends(get_db),
):
    """Get the user who started the current print (for reprint tracking).

    Returns user info if available, empty object otherwise.
    This tracks users for reprints (which bypass the queue).
    For queue-based prints, use the queue item's created_by field instead.
    """
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    user_info = printer_manager.get_current_print_user(printer_id)
    return user_info or {}


@router.post("/{printer_id}/refresh-status")
async def refresh_printer_status(
    printer_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
    db: AsyncSession = Depends(get_db),
):
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
async def connect_printer(
    printer_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
    db: AsyncSession = Depends(get_db),
):
    """Manually connect to a printer."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    success = await printer_manager.connect_printer(printer)
    return {"connected": success}


@router.post("/{printer_id}/disconnect")
async def disconnect_printer(
    printer_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
    db: AsyncSession = Depends(get_db),
):
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
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_CREATE),
):
    """Test connection to a printer without saving."""
    result = await printer_manager.test_connection(
        ip_address=ip_address,
        serial_number=serial_number,
        access_code=access_code,
    )
    return result


# Cache for cover images (printer_id -> {(subtask_name, plate_num, view) -> image_bytes})
_cover_cache: dict[int, dict[tuple[str, str], bytes]] = {}


def clear_cover_cache(printer_id: int) -> None:
    """Clear cached cover images for a printer. Call on print start to avoid stale thumbnails."""
    _cover_cache.pop(printer_id, None)


@router.get("/{printer_id}/cover")
async def get_printer_cover(
    printer_id: int,
    view: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    # Note: No auth required - this is an image asset loaded via <img src> which can't send auth headers
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

    # Extract plate number from gcode_file (e.g., "/data/Metadata/plate_12.gcode" -> 12)
    plate_num = 1
    gcode_file = state.gcode_file
    if gcode_file:
        match = re.search(r"plate_(\d+)\.gcode", gcode_file)
        if match:
            plate_num = int(match.group(1))
            logger.info("Detected plate number %s from gcode_file: %s", plate_num, gcode_file)

    # Normalize view parameter
    view_key = view or "default"

    # Check cache - include plate_num in cache key for multi-plate projects
    if printer_id in _cover_cache:
        cache_key = (subtask_name, plate_num, view_key)
        if cache_key in _cover_cache[printer_id]:
            return Response(content=_cover_cache[printer_id][cache_key], media_type="image/png")

    # Build possible 3MF filenames from subtask_name
    # Bambu printers may store files as "name.gcode.3mf" (sliced via Bambu Studio)
    # or just "name.3mf" (uploaded directly)
    possible_filenames = []
    if subtask_name.endswith(".3mf"):
        possible_filenames.append(subtask_name)
    else:
        # Try both naming patterns
        possible_filenames.append(f"{subtask_name}.gcode.3mf")
        possible_filenames.append(f"{subtask_name}.3mf")

    # Also try with spaces converted to underscores (Bambu Studio may normalize filenames)
    if " " in subtask_name:
        normalized = subtask_name.replace(" ", "_")
        if normalized.endswith(".3mf"):
            possible_filenames.append(normalized)
        else:
            possible_filenames.append(f"{normalized}.gcode.3mf")
            possible_filenames.append(f"{normalized}.3mf")

    # Build list of all remote paths to try
    remote_paths = []
    for filename in possible_filenames:
        remote_paths.extend(
            [
                f"/{filename}",  # Root directory (most common)
                f"/cache/{filename}",
                f"/model/{filename}",
                f"/data/{filename}",
            ]
        )

    # Use first filename for temp path (will be reused)
    temp_filename = possible_filenames[0]
    temp_path = settings.archive_dir / "temp" / f"cover_{printer_id}_{temp_filename}"
    temp_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        f"Trying to download cover for '{subtask_name}' from {printer.ip_address} (trying {len(remote_paths)} paths)"
    )

    # Retry logic for transient FTP failures
    max_retries = 2
    last_error = None
    downloaded = False

    for attempt in range(max_retries + 1):
        try:
            downloaded = await download_file_try_paths_async(
                printer.ip_address,
                printer.access_code,
                remote_paths,
                temp_path,
                printer_model=printer.model,
            )
            if downloaded:
                break
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                logger.warning("FTP download attempt %s failed: %s, retrying...", attempt + 1, e)
                await asyncio.sleep(0.5 * (attempt + 1))  # Brief backoff
            else:
                logger.error("FTP download failed after %s attempts: %s", max_retries + 1, e)

    if last_error and not downloaded:
        raise HTTPException(503, f"FTP download temporarily unavailable: {last_error}")

    if not downloaded:
        raise HTTPException(
            404,
            f"Could not download 3MF file for '{subtask_name}' from printer {printer.ip_address}. Tried: {possible_filenames}",
        )

    # Verify file actually exists and has content
    if not temp_path.exists():
        raise HTTPException(500, f"Download reported success but file not found: {temp_path}")

    file_size = temp_path.stat().st_size
    logger.info("Downloaded file size: %s bytes", file_size)

    if file_size == 0:
        temp_path.unlink()
        raise HTTPException(500, f"Downloaded file is empty for '{subtask_name}'")

    try:
        # Extract thumbnail from 3MF (which is a ZIP file)
        try:
            zf = zipfile.ZipFile(temp_path, "r")
        except zipfile.BadZipFile:
            raise HTTPException(500, "Downloaded file is not a valid 3MF/ZIP archive")
        except OSError as e:
            logger.error("Failed to open 3MF file: %s", e, exc_info=True)
            raise HTTPException(500, "Failed to open 3MF file. Check server logs for details.")

        try:
            # Try common thumbnail paths in 3MF files
            # Use plate_num to get the correct plate's thumbnail for multi-plate projects
            # Use top-down view if requested (better for skip objects modal)
            if view == "top":
                thumbnail_paths = [
                    f"Metadata/top_{plate_num}.png",
                    # Fall back to plate 1 if specific plate not found
                    "Metadata/top_1.png",
                    f"Metadata/plate_{plate_num}.png",
                    "Metadata/plate_1.png",
                    "Metadata/thumbnail.png",
                ]
            else:
                thumbnail_paths = [
                    f"Metadata/plate_{plate_num}.png",
                    # Fall back to plate 1 if specific plate not found
                    "Metadata/plate_1.png",
                    "Metadata/thumbnail.png",
                    f"Metadata/plate_{plate_num}_small.png",
                    "Metadata/plate_1_small.png",
                    "Thumbnails/thumbnail.png",
                    "thumbnail.png",
                ]

            for thumb_path in thumbnail_paths:
                try:
                    image_data = zf.read(thumb_path)
                    # Cache the result - include plate_num in cache key
                    if printer_id not in _cover_cache:
                        _cover_cache[printer_id] = {}
                    _cover_cache[printer_id][(subtask_name, plate_num, view_key)] = image_data
                    return Response(content=image_data, media_type="image/png")
                except KeyError:
                    continue

            # If no specific thumbnail found, try any PNG in Metadata
            for name in zf.namelist():
                if name.startswith("Metadata/") and name.endswith(".png"):
                    image_data = zf.read(name)
                    if printer_id not in _cover_cache:
                        _cover_cache[printer_id] = {}
                    _cover_cache[printer_id][(subtask_name, plate_num, view_key)] = image_data
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
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_FILES),
    db: AsyncSession = Depends(get_db),
):
    """List files on the printer at the specified path."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    files = await list_files_async(printer.ip_address, printer.access_code, path, printer_model=printer.model)

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
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_FILES),
    db: AsyncSession = Depends(get_db),
):
    """Download a file from the printer."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    data = await download_file_bytes_async(printer.ip_address, printer.access_code, path, printer_model=printer.model)
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


@router.get("/{printer_id}/files/gcode")
async def get_printer_file_gcode(
    printer_id: int,
    path: str,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_FILES),
    db: AsyncSession = Depends(get_db),
):
    """Get gcode for a file stored on a printer (for preview)."""
    import io

    # Validate printer
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    data = await download_file_bytes_async(printer.ip_address, printer.access_code, path, printer_model=printer.model)
    if data is None:
        raise HTTPException(404, f"File not found: {path}")

    filename = path.split("/")[-1]
    lower = filename.lower()

    if lower.endswith(".gcode"):
        return Response(content=data, media_type="text/plain")
    if lower.endswith(".3mf"):
        try:
            with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
                gcode_files = [n for n in zf.namelist() if n.endswith(".gcode")]
                if not gcode_files:
                    raise HTTPException(status_code=404, detail="No gcode found in 3MF file")
                gcode_content = zf.read(gcode_files[0])
                return Response(content=gcode_content, media_type="text/plain")
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Invalid 3MF file")

    raise HTTPException(status_code=400, detail="Unsupported file type")


@router.get("/{printer_id}/files/plates")
async def get_printer_file_plates(
    printer_id: int,
    path: str = Query(..., description="Full path to the 3MF file on the printer"),
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_FILES),
    db: AsyncSession = Depends(get_db),
):
    """Get available plates from a multi-plate 3MF file stored on a printer."""
    import io
    import json

    import defusedxml.ElementTree as ET

    # Validate printer
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    filename = path.split("/")[-1]
    if not filename.lower().endswith(".3mf"):
        return {
            "printer_id": printer_id,
            "path": path,
            "filename": filename,
            "plates": [],
            "is_multi_plate": False,
        }

    data = await download_file_bytes_async(printer.ip_address, printer.access_code, path, printer_model=printer.model)
    if data is None:
        raise HTTPException(404, f"File not found: {path}")

    plates = []

    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            namelist = zf.namelist()

            # Find all plate gcode files to determine available plates
            gcode_files = [n for n in namelist if n.startswith("Metadata/plate_") and n.endswith(".gcode")]

            # If no gcode is present (source-only or unsliced), fall back to plate JSON/PNG
            plate_indices: list[int] = []
            if gcode_files:
                for gf in gcode_files:
                    try:
                        plate_str = gf[15:-6]  # Remove "Metadata/plate_" and ".gcode"
                        plate_indices.append(int(plate_str))
                    except ValueError:
                        pass  # Skip gcode files with non-numeric plate indices
            else:
                plate_json_files = [n for n in namelist if n.startswith("Metadata/plate_") and n.endswith(".json")]
                plate_png_files = [
                    n
                    for n in namelist
                    if n.startswith("Metadata/plate_")
                    and n.endswith(".png")
                    and "_small" not in n
                    and "no_light" not in n
                ]
                plate_name_candidates = plate_json_files + plate_png_files
                plate_re = re.compile(r"^Metadata/plate_(\d+)\.(json|png)$")
                seen_indices: set[int] = set()
                for name in plate_name_candidates:
                    match = plate_re.match(name)
                    if match:
                        try:
                            index = int(match.group(1))
                        except ValueError:
                            continue
                        if index in seen_indices:
                            continue
                        seen_indices.add(index)
                        plate_indices.append(index)

            if not plate_indices:
                return {
                    "printer_id": printer_id,
                    "path": path,
                    "filename": filename,
                    "plates": [],
                    "is_multi_plate": False,
                }

            plate_indices.sort()

            # Parse model_settings.config for plate names
            plate_names = {}
            if "Metadata/model_settings.config" in namelist:
                try:
                    model_content = zf.read("Metadata/model_settings.config").decode()
                    model_root = ET.fromstring(model_content)
                    for plate_elem in model_root.findall(".//plate"):
                        plater_id = None
                        plater_name = None
                        for meta in plate_elem.findall("metadata"):
                            key = meta.get("key")
                            value = meta.get("value")
                            if key == "plater_id" and value:
                                try:
                                    plater_id = int(value)
                                except ValueError:
                                    pass  # Skip plate with unparseable ID
                            elif key == "plater_name" and value:
                                plater_name = value.strip()
                        if plater_id is not None and plater_name:
                            plate_names[plater_id] = plater_name
                except Exception:
                    pass  # Plate names are optional; continue without them

            # Parse slice_info.config for plate metadata
            plate_metadata = {}
            if "Metadata/slice_info.config" in namelist:
                content = zf.read("Metadata/slice_info.config").decode()
                root = ET.fromstring(content)

                for plate_elem in root.findall(".//plate"):
                    plate_info = {"filaments": [], "prediction": None, "weight": None, "name": None, "objects": []}

                    plate_index = None
                    for meta in plate_elem.findall("metadata"):
                        key = meta.get("key")
                        value = meta.get("value")
                        if key == "index" and value:
                            try:
                                plate_index = int(value)
                            except ValueError:
                                pass  # Skip plate with unparseable index
                        elif key == "prediction" and value:
                            try:
                                plate_info["prediction"] = int(value)
                            except ValueError:
                                pass  # Skip unparseable prediction; leave as None
                        elif key == "weight" and value:
                            try:
                                plate_info["weight"] = float(value)
                            except ValueError:
                                pass  # Skip unparseable weight; leave as None

                    # Get filaments used in this plate
                    for filament_elem in plate_elem.findall("filament"):
                        filament_id = filament_elem.get("id")
                        filament_type = filament_elem.get("type", "")
                        filament_color = filament_elem.get("color", "")
                        used_g = filament_elem.get("used_g", "0")
                        used_m = filament_elem.get("used_m", "0")

                        try:
                            used_grams = float(used_g)
                        except (ValueError, TypeError):
                            used_grams = 0

                        if used_grams > 0 and filament_id:
                            plate_info["filaments"].append(
                                {
                                    "slot_id": int(filament_id),
                                    "type": filament_type,
                                    "color": filament_color,
                                    "used_grams": round(used_grams, 1),
                                    "used_meters": float(used_m) if used_m else 0,
                                }
                            )

                    plate_info["filaments"].sort(key=lambda x: x["slot_id"])

                    # Collect object names
                    for obj_elem in plate_elem.findall("object"):
                        obj_name = obj_elem.get("name")
                        if obj_name and obj_name not in plate_info["objects"]:
                            plate_info["objects"].append(obj_name)

                    # Set plate name
                    if plate_index is not None:
                        custom_name = plate_names.get(plate_index)
                        if custom_name:
                            plate_info["name"] = custom_name
                        elif plate_info["objects"]:
                            plate_info["name"] = plate_info["objects"][0]
                        plate_metadata[plate_index] = plate_info

            # Parse plate_*.json for object lists when slice_info is missing
            plate_json_objects: dict[int, list[str]] = {}
            for name in namelist:
                match = re.match(r"^Metadata/plate_(\d+)\.json$", name)
                if not match:
                    continue
                try:
                    plate_index = int(match.group(1))
                except ValueError:
                    continue
                try:
                    payload = json.loads(zf.read(name).decode())
                    bbox_objects = payload.get("bbox_objects", [])
                    names: list[str] = []
                    for obj in bbox_objects:
                        obj_name = obj.get("name") if isinstance(obj, dict) else None
                        if obj_name and obj_name not in names:
                            names.append(obj_name)
                    if names:
                        plate_json_objects[plate_index] = names
                except Exception:
                    continue

            # Build plate list
            for idx in plate_indices:
                meta = plate_metadata.get(idx, {})
                has_thumbnail = f"Metadata/plate_{idx}.png" in namelist
                objects = meta.get("objects", [])
                if not objects:
                    objects = plate_json_objects.get(idx, [])

                plate_name = meta.get("name")
                if not plate_name:
                    plate_name = plate_names.get(idx)
                if not plate_name and objects:
                    plate_name = objects[0]

                plates.append(
                    {
                        "index": idx,
                        "name": plate_name,
                        "objects": objects,
                        "object_count": len(objects),
                        "has_thumbnail": has_thumbnail,
                        "thumbnail_url": f"/api/v1/printers/{printer_id}/files/plate-thumbnail/{idx}?path={path}",
                        "print_time_seconds": meta.get("prediction"),
                        "filament_used_grams": meta.get("weight"),
                        "filaments": meta.get("filaments", []),
                    }
                )

    except Exception as e:
        logger.warning("Failed to parse plates from printer file %s: %s", path, e)

    return {
        "printer_id": printer_id,
        "path": path,
        "filename": filename,
        "plates": plates,
        "is_multi_plate": len(plates) > 1,
    }


@router.get("/{printer_id}/files/plate-thumbnail/{plate_index}")
async def get_printer_file_plate_thumbnail(
    printer_id: int,
    plate_index: int,
    path: str = Query(..., description="Full path to the 3MF file on the printer"),
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_FILES),
    db: AsyncSession = Depends(get_db),
):
    """Get a plate thumbnail image from a printer-stored 3MF file."""
    import io

    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    data = await download_file_bytes_async(printer.ip_address, printer.access_code, path, printer_model=printer.model)
    if data is None:
        raise HTTPException(404, f"File not found: {path}")

    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            thumb_path = f"Metadata/plate_{plate_index}.png"
            if thumb_path in zf.namelist():
                image_data = zf.read(thumb_path)
                return Response(content=image_data, media_type="image/png")
    except Exception:
        pass  # Corrupt or unreadable 3MF; fall through to 404

    raise HTTPException(status_code=404, detail=f"Thumbnail for plate {plate_index} not found")


@router.post("/{printer_id}/files/download-zip")
async def download_printer_files_as_zip(
    printer_id: int,
    request: dict,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_FILES),
    db: AsyncSession = Depends(get_db),
):
    """Download multiple files from the printer as a ZIP archive."""
    import io

    paths = request.get("paths", [])
    if not paths:
        raise HTTPException(400, "No files specified")

    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    # Create ZIP in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in paths:
            try:
                data = await download_file_bytes_async(
                    printer.ip_address, printer.access_code, path, printer_model=printer.model
                )
                if data:
                    filename = path.split("/")[-1]
                    zf.writestr(filename, data)
            except Exception as e:
                logging.warning("Failed to add %s to ZIP: %s", path, e)
                continue

    zip_buffer.seek(0)
    zip_data = zip_buffer.read()

    if len(zip_data) == 0:
        raise HTTPException(404, "No files could be downloaded")

    return Response(
        content=zip_data,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="printer-files.zip"'},
    )


@router.delete("/{printer_id}/files")
async def delete_printer_file(
    printer_id: int,
    path: str,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_FILES),
    db: AsyncSession = Depends(get_db),
):
    """Delete a file from the printer."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    success = await delete_file_async(printer.ip_address, printer.access_code, path, printer_model=printer.model)
    if not success:
        raise HTTPException(500, f"Failed to delete file: {path}")

    return {"status": "deleted", "path": path}


@router.get("/{printer_id}/storage")
async def get_printer_storage(
    printer_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
    db: AsyncSession = Depends(get_db),
):
    """Get storage information from the printer."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    storage_info = await get_storage_info_async(printer.ip_address, printer.access_code, printer_model=printer.model)

    return storage_info or {"used_bytes": None, "free_bytes": None}


# ============================================
# MQTT Debug Logging Endpoints
# ============================================


@router.post("/{printer_id}/logging/enable")
async def enable_mqtt_logging(
    printer_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
    db: AsyncSession = Depends(get_db),
):
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
async def disable_mqtt_logging(
    printer_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
    db: AsyncSession = Depends(get_db),
):
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
async def get_mqtt_logs(
    printer_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
    db: AsyncSession = Depends(get_db),
):
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
async def clear_mqtt_logs(
    printer_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
    db: AsyncSession = Depends(get_db),
):
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
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
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
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
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
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
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
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
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
    preset_source: str = "cloud",
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_UPDATE),
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
        mapping.preset_source = preset_source
    else:
        # Create new
        mapping = SlotPresetMapping(
            printer_id=printer_id,
            ams_id=ams_id,
            tray_id=tray_id,
            preset_id=preset_id,
            preset_name=preset_name,
            preset_source=preset_source,
        )
        db.add(mapping)

    await db.commit()
    await db.refresh(mapping)

    return {
        "ams_id": mapping.ams_id,
        "tray_id": mapping.tray_id,
        "preset_id": mapping.preset_id,
        "preset_name": mapping.preset_name,
        "preset_source": mapping.preset_source,
    }


@router.delete("/{printer_id}/slot-presets/{ams_id}/{tray_id}")
async def delete_slot_preset(
    printer_id: int,
    ams_id: int,
    tray_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_UPDATE),
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


@router.post("/{printer_id}/slots/{ams_id}/{tray_id}/configure")
async def configure_ams_slot(
    printer_id: int,
    ams_id: int,
    tray_id: int,
    tray_info_idx: str = Query(...),
    tray_type: str = Query(...),
    tray_sub_brands: str = Query(...),
    tray_color: str = Query(...),
    nozzle_temp_min: int = Query(...),
    nozzle_temp_max: int = Query(...),
    cali_idx: int = Query(-1),
    nozzle_diameter: str = Query("0.4"),
    setting_id: str = Query(""),
    kprofile_filament_id: str = Query(""),
    kprofile_setting_id: str = Query(""),
    k_value: float = Query(0.0),
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
):
    """Configure an AMS slot with a specific filament setting and K profile.

    This sends two commands to the printer:
    1. ams_filament_setting - sets filament type, color, temperature
    2. extrusion_cali_sel - sets the K profile (pressure advance value)

    Args:
        printer_id: Database ID of the printer
        ams_id: AMS unit ID (0-3 for regular AMS, 128-135 for HT AMS)
        tray_id: Tray ID within the AMS (0-3)
        tray_info_idx: Filament ID short format (e.g., "GFL05") or user preset ID
        tray_type: Filament type (e.g., "PLA", "PETG")
        tray_sub_brands: Sub-brand/profile name (e.g., "PLA Basic", "PETG HF")
        tray_color: Color in RRGGBBAA hex format (e.g., "FFFF00FF")
        nozzle_temp_min: Minimum nozzle temperature
        nozzle_temp_max: Maximum nozzle temperature
        cali_idx: K profile calibration index (-1 for default 0.020)
        nozzle_diameter: Nozzle diameter string (e.g., "0.4")
        setting_id: Full setting ID with version (e.g., "GFSL05_07") - optional
        kprofile_filament_id: K profile's filament_id for proper K profile linking
        k_value: Direct K value to set (0.0 to skip direct K value setting)
    """
    logger = logging.getLogger(__name__)
    logger.info("[configure_ams_slot] printer_id=%s, ams_id=%s, tray_id=%s", printer_id, ams_id, tray_id)
    logger.info(
        f"[configure_ams_slot] tray_info_idx={tray_info_idx!r}, tray_type={tray_type!r}, tray_sub_brands={tray_sub_brands!r}"
    )
    logger.info(
        f"[configure_ams_slot] setting_id={setting_id!r}, kprofile_filament_id={kprofile_filament_id!r}, kprofile_setting_id={kprofile_setting_id!r}"
    )

    # Get MQTT client for this printer
    client = printer_manager.get_client(printer_id)
    if not client:
        raise HTTPException(status_code=400, detail="Printer not connected")

    # Resolve tray_info_idx for the MQTT command.
    # Priority:
    #   1. Use the provided tray_info_idx if set (including cloud-synced
    #      custom presets like PFUS* / P*).
    #   2. Reuse the slot's existing tray_info_idx if it's a specific
    #      (non-generic) preset for the same material.
    #   3. Fall back to a generic Bambu filament ID.
    _GENERIC_FILAMENT_IDS = {
        "PLA": "GFL99",
        "PETG": "GFG99",
        "ABS": "GFB99",
        "ASA": "GFB98",
        "PC": "GFC99",
        "PA": "GFN99",
        "NYLON": "GFN99",
        "TPU": "GFU99",
        "PVA": "GFS99",
        "HIPS": "GFS98",
        "PLA-CF": "GFL98",
        "PETG-CF": "GFG98",
        "PA-CF": "GFN98",
        "PETG HF": "GFG96",
    }
    _GENERIC_ID_VALUES = set(_GENERIC_FILAMENT_IDS.values())
    effective_tray_info_idx = tray_info_idx

    if not tray_info_idx:
        # No preset provided — try slot reuse or generic fallback
        current_tray_info_idx = ""
        current_tray_type = ""
        state = printer_manager.get_status(printer_id)
        if state and state.raw_data:
            from backend.app.api.routes.inventory import _find_tray_in_ams_data

            if ams_id == 255:
                vt_tray = state.raw_data.get("vt_tray") or []
                ext_id = tray_id + 254
                for vt in vt_tray:
                    if isinstance(vt, dict) and int(vt.get("id", 254)) == ext_id:
                        current_tray_info_idx = vt.get("tray_info_idx", "")
                        current_tray_type = vt.get("tray_type", "")
                        break
            else:
                ams_data = state.raw_data.get("ams", {})
                ams_list = (
                    ams_data.get("ams", [])
                    if isinstance(ams_data, dict)
                    else ams_data
                    if isinstance(ams_data, list)
                    else []
                )
                cur_tray = _find_tray_in_ams_data(ams_list, ams_id, tray_id)
                if cur_tray:
                    current_tray_info_idx = cur_tray.get("tray_info_idx", "")
                    current_tray_type = cur_tray.get("tray_type", "")

        if (
            current_tray_info_idx
            and current_tray_info_idx not in _GENERIC_ID_VALUES
            and current_tray_type
            and current_tray_type.upper() == tray_type.upper()
        ):
            logger.info(
                "[configure_ams_slot] Reusing slot's existing tray_info_idx=%r (same material %r)",
                current_tray_info_idx,
                tray_type,
            )
            effective_tray_info_idx = current_tray_info_idx
        elif tray_type:
            material = tray_type.upper().strip()
            generic = (
                _GENERIC_FILAMENT_IDS.get(material)
                or _GENERIC_FILAMENT_IDS.get(material.split("-")[0].split(" ")[0])
                or ""
            )
            if generic:
                logger.info("[configure_ams_slot] Falling back to generic %r for material %r", generic, tray_type)
                effective_tray_info_idx = generic

    # Send filament setting + K-profile commands
    filament_id_for_kprofile = kprofile_filament_id if kprofile_filament_id else effective_tray_info_idx

    # Always send ams_set_filament_setting — the user explicitly clicked
    # "Configure Slot", so honor that.  Previous versions skipped this for
    # RFID-tagged slots to preserve the slicer eye icon, but printers cache
    # stale tag_uid/tray_uuid after a BL spool is removed, causing the check
    # to false-positive on non-RFID slots and silently drop the command.
    success = client.ams_set_filament_setting(
        ams_id=ams_id,
        tray_id=tray_id,
        tray_info_idx=effective_tray_info_idx,
        tray_type=tray_type,
        tray_sub_brands=tray_sub_brands,
        tray_color=tray_color,
        nozzle_temp_min=nozzle_temp_min,
        nozzle_temp_max=nozzle_temp_max,
        setting_id=setting_id,
    )

    if not success:
        raise HTTPException(status_code=500, detail="Failed to send filament configuration command")

    # Method 1: Select existing calibration profile by cali_idx
    # Do NOT include setting_id — BambuStudio never sends it in extrusion_cali_sel,
    # and including it causes the firmware to mislink the profile on X1C/P1S.
    client.extrusion_cali_sel(
        ams_id=ams_id,
        tray_id=tray_id,
        cali_idx=cali_idx,
        filament_id=filament_id_for_kprofile,
        nozzle_diameter=nozzle_diameter,
    )

    # Method 2: Only send extrusion_cali_set when NO existing profile was selected
    # (cali_idx == -1). When cali_idx >= 0, extrusion_cali_sel already selected the
    # correct profile. Sending extrusion_cali_set with the same cali_idx would MODIFY
    # the existing profile's metadata (extruder_id, nozzle_id, name, setting_id),
    # corrupting it — e.g., overwriting a High Flow extruder 1 profile with
    # hardcoded extruder_id=0 and nozzle_id=HS00.
    if k_value > 0 and cali_idx < 0:
        # Calculate global tray ID for extrusion_cali_set
        if ams_id <= 3:
            global_tray_id = ams_id * 4 + tray_id
        elif ams_id >= 128 and ams_id <= 135:
            global_tray_id = (ams_id - 128) * 4 + tray_id
        else:
            global_tray_id = tray_id

        client.extrusion_cali_set(
            tray_id=global_tray_id,
            k_value=k_value,
            nozzle_diameter=nozzle_diameter,
            nozzle_temp=nozzle_temp_max,
            filament_id=filament_id_for_kprofile,
            setting_id=kprofile_setting_id or "",
            name=tray_sub_brands or "",
            cali_idx=cali_idx,
        )

    # Request fresh status push from printer so frontend gets updated data via WebSocket
    logger.info("[configure_ams_slot] Requesting status update from printer")
    update_result = client.request_status_update()
    logger.info("[configure_ams_slot] Status update request result: %s", update_result)

    return {
        "success": True,
        "message": f"Configured AMS {ams_id} tray {tray_id} with {tray_sub_brands}",
    }


@router.post("/{printer_id}/ams/{ams_id}/tray/{tray_id}/reset")
async def reset_ams_slot(
    printer_id: int,
    ams_id: int,
    tray_id: int,
    db: AsyncSession = Depends(get_db),
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
):
    """Reset an AMS slot to empty/unconfigured state.

    This clears the filament configuration from the slot.
    """
    # Get MQTT client for this printer
    client = printer_manager.get_client(printer_id)
    if not client:
        raise HTTPException(status_code=400, detail="Printer not connected")

    # Reset the slot
    success = client.reset_ams_slot(ams_id=ams_id, tray_id=tray_id)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to send reset command")

    # Also delete any saved slot preset mapping
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

    # Request fresh status push from printer so frontend gets updated data via WebSocket
    client.request_status_update()

    return {
        "success": True,
        "message": f"Reset AMS {ams_id} tray {tray_id}",
    }


@router.post("/{printer_id}/debug/simulate-print-complete")
async def debug_simulate_print_complete(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
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

    logger.info("Simulating print complete for printer %s, archive %s", printer_id, archive.id)

    # Call the actual on_print_complete handler
    await on_print_complete(printer_id, data)

    return {"success": True, "archive_id": archive.id, "message": "Print completion simulated"}


# =============================================================================
# Print Control Endpoints
# =============================================================================


@router.post("/{printer_id}/print/stop")
async def stop_print(
    printer_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
    db: AsyncSession = Depends(get_db),
):
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


@router.post("/{printer_id}/clear-plate")
async def clear_plate(
    printer_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_CLEAR_PLATE),
    db: AsyncSession = Depends(get_db),
):
    """Acknowledge that the build plate has been cleared after a finished/failed print.

    Sets a plate-cleared flag so the scheduler can start the next queued print.
    No MQTT command is sent to the printer — the scheduler's start_print command
    will override the FINISH/FAILED state when it sends the next job.
    """
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    if not printer_manager.is_connected(printer_id):
        raise HTTPException(400, "Printer not connected")

    state = printer_manager.get_status(printer_id)
    if not state or state.state not in ("FINISH", "FAILED"):
        raise HTTPException(
            400, f"Printer is not in FINISH or FAILED state (current: {state.state if state else 'unknown'})"
        )

    printer_manager.set_plate_cleared(printer_id)

    return {"success": True, "message": "Plate cleared, next print will start shortly"}


@router.post("/{printer_id}/print/pause")
async def pause_print(
    printer_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
    db: AsyncSession = Depends(get_db),
):
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
async def resume_print(
    printer_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
    db: AsyncSession = Depends(get_db),
):
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


@router.post("/{printer_id}/chamber-light")
async def set_chamber_light(
    printer_id: int,
    on: bool = Query(..., description="True to turn on, False to turn off"),
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
    db: AsyncSession = Depends(get_db),
):
    """Turn the chamber light on or off."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    client = printer_manager.get_client(printer_id)
    if not client:
        raise HTTPException(400, "Printer not connected")

    success = client.set_chamber_light(on)
    if not success:
        raise HTTPException(500, "Failed to control chamber light")

    return {"success": True, "message": f"Chamber light {'on' if on else 'off'}"}


@router.post("/{printer_id}/hms/clear")
async def clear_hms_errors(
    printer_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
    db: AsyncSession = Depends(get_db),
):
    """Clear HMS/print errors on the printer."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    client = printer_manager.get_client(printer_id)
    if not client:
        raise HTTPException(400, "Printer not connected")

    success = client.clear_hms_errors()
    if not success:
        raise HTTPException(500, "Failed to clear HMS errors")

    return {"success": True, "message": "HMS errors cleared"}


@router.get("/{printer_id}/print/objects")
async def get_printable_objects(
    printer_id: int,
    reload: bool = False,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
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

            # Build possible 3MF filenames (try both .gcode.3mf and .3mf)
            possible_filenames = []
            if subtask_name.endswith(".3mf"):
                possible_filenames.append(subtask_name)
            else:
                possible_filenames.append(f"{subtask_name}.gcode.3mf")
                possible_filenames.append(f"{subtask_name}.3mf")

            # Also try with spaces converted to underscores (Bambu Studio may normalize filenames)
            if " " in subtask_name:
                normalized = subtask_name.replace(" ", "_")
                if normalized.endswith(".3mf"):
                    possible_filenames.append(normalized)
                else:
                    possible_filenames.append(f"{normalized}.gcode.3mf")
                    possible_filenames.append(f"{normalized}.3mf")

            # Download 3MF from printer
            temp_path = settings.archive_dir / "temp" / f"objects_{printer_id}_{possible_filenames[0]}"
            temp_path.parent.mkdir(parents=True, exist_ok=True)

            # Build list of all remote paths to try
            remote_paths = []
            for filename in possible_filenames:
                remote_paths.extend([f"/{filename}", f"/cache/{filename}", f"/model/{filename}"])

            try:
                downloaded = await download_file_try_paths_async(
                    printer.ip_address,
                    printer.access_code,
                    remote_paths,
                    temp_path,
                    printer_model=printer.model,
                )
                if downloaded and temp_path.exists():
                    with open(temp_path, "rb") as f:
                        data = f.read()
                    objects, bbox_all = extract_printable_objects_from_3mf(data, include_positions=True)
                    if objects:
                        client.state.printable_objects = objects
                        client.state.printable_objects_bbox_all = bbox_all
                        logger.info("Reloaded %s objects for printer %s", len(objects), printer_id)
            except Exception as e:
                logger.debug("Failed to reload objects from printer: %s", e)
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
        "bbox_all": getattr(client.state, "printable_objects_bbox_all", None),
    }


@router.post("/{printer_id}/print/skip-objects")
async def skip_objects(
    printer_id: int,
    object_ids: list[int],
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
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
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_AMS_RFID),
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

    # Apply PA profile after delay (RFID re-read takes a few seconds)
    asyncio.create_task(_apply_pa_after_refresh(printer_id, ams_id, slot_id))

    return {"success": True, "message": message}


async def _apply_pa_after_refresh(printer_id: int, ams_id: int, slot_id: int):
    """Apply PA profile after RFID re-read completes.

    Waits for the printer to finish processing the RFID data, then selects
    the K-profile via extrusion_cali_sel.  Does NOT re-send ams_set_filament_setting
    because that would overwrite the RFID-provided filament data.
    """
    await asyncio.sleep(5)
    try:
        from backend.app.api.routes.inventory import _find_tray_in_ams_data
        from backend.app.core.database import async_session
        from backend.app.models.spool import Spool
        from backend.app.models.spool_assignment import SpoolAssignment as SA
        from backend.app.services.spool_tag_matcher import is_bambu_tag

        client = printer_manager.get_client(printer_id)
        if not client:
            return

        state = printer_manager.get_status(printer_id)
        if not state or not state.raw_data:
            return

        # Find current tray data (should have RFID data by now)
        ams_data = state.raw_data.get("ams", {})
        ams_list = (
            ams_data.get("ams", []) if isinstance(ams_data, dict) else ams_data if isinstance(ams_data, list) else []
        )
        tray = _find_tray_in_ams_data(ams_list, ams_id, slot_id)
        if not tray or not tray.get("tray_type"):
            logger.debug("PA re-apply: no tray data for AMS%d-T%d", ams_id, slot_id)
            return

        tag_uid = tray.get("tag_uid", "")
        tray_uuid = tray.get("tray_uuid", "")
        tray_info_idx = tray.get("tray_info_idx", "")
        if not is_bambu_tag(tag_uid, tray_uuid, tray_info_idx):
            return

        async with async_session() as db:
            from sqlalchemy import select as sa_select
            from sqlalchemy.orm import selectinload

            result = await db.execute(
                sa_select(SA)
                .options(selectinload(SA.spool).selectinload(Spool.k_profiles))
                .where(SA.printer_id == printer_id, SA.ams_id == ams_id, SA.tray_id == slot_id)
            )
            assignment = result.scalar_one_or_none()
            if not assignment or not assignment.spool or not assignment.spool.k_profiles:
                return

            spool = assignment.spool
            nozzle_diameter = "0.4"
            if state.nozzles:
                nd = state.nozzles[0].nozzle_diameter
                if nd:
                    nozzle_diameter = nd

            # Determine slot's extruder from ams_extruder_map
            slot_extruder = None
            if state.ams_extruder_map:
                if ams_id == 255:
                    # External slots: ext-L (tray 0) → extruder 1, ext-R (tray 1) → extruder 0
                    slot_extruder = 1 - slot_id  # 0→1, 1→0
                else:
                    slot_extruder = state.ams_extruder_map.get(str(ams_id))

            matching_kp = None
            for kp in spool.k_profiles:
                if kp.printer_id == printer_id and kp.nozzle_diameter == nozzle_diameter:
                    if slot_extruder is not None and kp.extruder_id is not None and kp.extruder_id != slot_extruder:
                        continue
                    matching_kp = kp
                    break

            if not matching_kp or matching_kp.cali_idx is None:
                return

            # The filament_id in extrusion_cali_sel must match the filament preset
            # under which the K-profile was calibrated. Use spool.slicer_filament
            # (the preset assigned in inventory), falling back to tray's RFID value.
            kp_filament_id = spool.slicer_filament or tray_info_idx

            logger.info(
                "PA re-apply AMS%d-T%d: cali_idx=%d, filament_id=%s",
                ams_id,
                slot_id,
                matching_kp.cali_idx,
                kp_filament_id,
            )

            # 1. Select K-profile
            # NOTE: Do NOT send ams_set_filament_setting here — it tells the firmware
            # "this is a manual config" which destroys the RFID-detected spool state
            # (changes eye icon to pen icon in slicer).
            client.extrusion_cali_sel(
                ams_id=ams_id,
                tray_id=slot_id,
                cali_idx=matching_kp.cali_idx,
                filament_id=kp_filament_id,
                nozzle_diameter=nozzle_diameter,
            )

            # NOTE: Do NOT send extrusion_cali_set here. extrusion_cali_sel already
            # selected the correct profile by cali_idx. Sending extrusion_cali_set with
            # the same cali_idx would MODIFY the existing profile's metadata (extruder_id,
            # nozzle_id, name), corrupting it.

            logger.info(
                "Applied PA profile cali_idx=%d k=%.3f to printer %d AMS%d-T%d",
                matching_kp.cali_idx,
                matching_kp.k_value or 0,
                printer_id,
                ams_id,
                slot_id,
            )
    except Exception as e:
        logger.warning("Failed to apply PA profile after RFID re-read: %s", e)


@router.get("/{printer_id}/runtime-debug")
async def get_runtime_debug(
    printer_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
    db: AsyncSession = Depends(get_db),
):
    """Debug endpoint: Get runtime tracking status for a printer."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    state = printer_manager.get_status(printer_id)

    return {
        "printer_name": printer.name,
        "runtime_seconds": printer.runtime_seconds,
        "runtime_hours": printer.runtime_seconds / 3600.0 if printer.runtime_seconds else 0,
        "print_hours_offset": printer.print_hours_offset,
        "total_hours": (printer.runtime_seconds / 3600.0 if printer.runtime_seconds else 0)
        + (printer.print_hours_offset or 0),
        "last_runtime_update": printer.last_runtime_update.isoformat() if printer.last_runtime_update else None,
        "mqtt_state": {
            "connected": state.connected if state else False,
            "state": state.state if state else None,
            "progress": state.progress if state else None,
            "gcode_file": state.gcode_file if state else None,
        }
        if state
        else None,
        "is_active": printer.is_active,
    }
