"""
Firmware Update API Routes

Check for firmware updates from Bambu Lab.
Also provides endpoints for uploading firmware to printers via SD card.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.printer import Printer
from backend.app.models.user import User
from backend.app.services.firmware_check import get_firmware_service
from backend.app.services.firmware_update import (
    FirmwareUploadStatus,
    get_firmware_update_service,
    get_upload_state,
)
from backend.app.services.printer_manager import printer_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/firmware", tags=["firmware"])


class FirmwareUpdateInfo(BaseModel):
    """Firmware update information for a printer."""

    printer_id: int
    printer_name: str
    model: str | None
    current_version: str | None
    latest_version: str | None
    update_available: bool
    download_url: str | None = None
    release_notes: str | None = None


class FirmwareUpdatesResponse(BaseModel):
    """Response containing firmware updates for all printers."""

    updates: list[FirmwareUpdateInfo] = Field(default_factory=list)
    updates_available: int = Field(0, description="Number of printers with updates available")


class LatestFirmwareInfo(BaseModel):
    """Latest firmware version info for a model."""

    model_key: str
    version: str
    download_url: str
    release_notes: str | None = None


@router.get("/updates", response_model=FirmwareUpdatesResponse)
async def check_firmware_updates(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FIRMWARE_READ),
):
    """
    Check for firmware updates for all connected printers.

    Compares each printer's current firmware version against the latest
    available version from Bambu Lab's official firmware download page.

    Note: This does not require cloud authentication - it uses public
    firmware information from bambulab.com.
    """
    firmware_service = get_firmware_service()

    # Get all printers from database
    result = await db.execute(select(Printer).where(Printer.is_active.is_(True)))
    printers = result.scalars().all()

    updates = []
    updates_available = 0

    for printer in printers:
        # Get current firmware version from MQTT state
        current_version = None
        mqtt_client = printer_manager.get_client(printer.id)
        if mqtt_client and mqtt_client.state:
            current_version = mqtt_client.state.firmware_version

        # Check for update
        model = printer.model or "Unknown"
        update_info = await firmware_service.check_for_update(model, current_version or "")

        if update_info["update_available"]:
            updates_available += 1

        updates.append(
            FirmwareUpdateInfo(
                printer_id=printer.id,
                printer_name=printer.name,
                model=model,
                current_version=current_version,
                latest_version=update_info["latest_version"],
                update_available=update_info["update_available"],
                download_url=update_info["download_url"],
                release_notes=update_info["release_notes"],
            )
        )

    return FirmwareUpdatesResponse(updates=updates, updates_available=updates_available)


@router.get("/updates/{printer_id}", response_model=FirmwareUpdateInfo)
async def check_printer_firmware(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FIRMWARE_READ),
):
    """
    Check for firmware update for a specific printer.
    """
    firmware_service = get_firmware_service()

    # Get printer from database
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()

    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")

    # Get current firmware version from MQTT state
    current_version = None
    mqtt_client = printer_manager.get_client(printer.id)
    if mqtt_client and mqtt_client.state:
        current_version = mqtt_client.state.firmware_version

    # Check for update
    model = printer.model or "Unknown"
    update_info = await firmware_service.check_for_update(model, current_version or "")

    return FirmwareUpdateInfo(
        printer_id=printer.id,
        printer_name=printer.name,
        model=model,
        current_version=current_version,
        latest_version=update_info["latest_version"],
        update_available=update_info["update_available"],
        download_url=update_info["download_url"],
        release_notes=update_info["release_notes"],
    )


@router.get("/latest", response_model=list[LatestFirmwareInfo])
async def get_all_latest_firmware(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FIRMWARE_READ),
):
    """
    Get the latest firmware versions for all Bambu Lab printer models.

    This endpoint fetches the latest available firmware versions from
    Bambu Lab's official firmware download page.
    """
    firmware_service = get_firmware_service()
    versions = await firmware_service.get_all_latest_versions()

    return [
        LatestFirmwareInfo(
            model_key=key,
            version=info.version,
            download_url=info.download_url,
            release_notes=info.release_notes,
        )
        for key, info in versions.items()
    ]


# ============================================================================
# Firmware Upload Endpoints (for LAN-only firmware updates)
# ============================================================================


class FirmwareUploadPrepareResponse(BaseModel):
    """Response from firmware upload preparation check."""

    can_proceed: bool
    sd_card_present: bool
    sd_card_free_space: int = Field(-1, description="Free space in bytes, -1 if unknown")
    firmware_size: int = Field(0, description="Estimated firmware size in bytes")
    space_sufficient: bool
    update_available: bool
    current_version: str | None = None
    latest_version: str | None = None
    firmware_filename: str | None = None
    errors: list[str] = Field(default_factory=list)


class FirmwareUploadStatusResponse(BaseModel):
    """Response containing firmware upload status."""

    status: str  # idle, preparing, downloading, uploading, complete, error
    progress: int = Field(0, ge=0, le=100)
    message: str = ""
    error: str | None = None
    firmware_filename: str | None = None
    firmware_version: str | None = None


class FirmwareUploadStartResponse(BaseModel):
    """Response when starting a firmware upload."""

    started: bool
    message: str


@router.get("/updates/{printer_id}/prepare", response_model=FirmwareUploadPrepareResponse)
async def prepare_firmware_upload(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FIRMWARE_READ),
):
    """
    Check prerequisites for uploading firmware to a printer.

    This performs pre-flight checks including:
    - SD card presence
    - Available storage space
    - Update availability

    Call this before starting a firmware upload to ensure the operation
    can succeed.
    """
    update_service = get_firmware_update_service()
    result = await update_service.prepare_update(printer_id, db)
    return FirmwareUploadPrepareResponse(**result)


@router.post("/updates/{printer_id}/upload", response_model=FirmwareUploadStartResponse)
async def start_firmware_upload(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FIRMWARE_UPDATE),
):
    """
    Start uploading firmware to a printer's SD card.

    This initiates a background process that:
    1. Downloads the firmware from Bambu Lab
    2. Uploads it to the printer's SD card via FTP

    Progress is broadcast via WebSocket with type "firmware_upload_progress".
    Use GET /firmware/updates/{printer_id}/upload/status for polling fallback.

    After upload completes, the user must trigger the update from the
    printer's screen (Settings > Firmware).
    """
    # First check prerequisites
    update_service = get_firmware_update_service()
    prepare_result = await update_service.prepare_update(printer_id, db)

    if not prepare_result["can_proceed"]:
        errors = prepare_result.get("errors", ["Cannot proceed with firmware upload"])
        raise HTTPException(
            status_code=400,
            detail="; ".join(errors),
        )

    # Start the upload
    started = await update_service.start_upload(printer_id, db)

    if not started:
        state = get_upload_state(printer_id)
        if state.status == FirmwareUploadStatus.DOWNLOADING:
            return FirmwareUploadStartResponse(
                started=False,
                message="Firmware upload already in progress",
            )
        raise HTTPException(
            status_code=500,
            detail=state.error or "Failed to start firmware upload",
        )

    return FirmwareUploadStartResponse(
        started=True,
        message="Firmware upload started. Progress will be broadcast via WebSocket.",
    )


@router.get("/updates/{printer_id}/upload/status", response_model=FirmwareUploadStatusResponse)
async def get_firmware_upload_status(
    printer_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FIRMWARE_READ),
):
    """
    Get the current status of a firmware upload operation.

    This is a polling fallback for clients that don't use WebSocket.
    For real-time updates, connect to WebSocket and listen for
    "firmware_upload_progress" messages.
    """
    state = get_upload_state(printer_id)
    return FirmwareUploadStatusResponse(
        status=state.status.value,
        progress=state.progress,
        message=state.message,
        error=state.error,
        firmware_filename=state.firmware_filename,
        firmware_version=state.firmware_version,
    )
