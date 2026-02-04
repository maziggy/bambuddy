"""
Firmware Update Service

Orchestrates firmware updates for Bambu Lab printers:
1. Check prerequisites (SD card, space, update available)
2. Download firmware from Bambu Lab
3. Upload to printer's SD card via FTP
4. Notify user to trigger update from printer screen
"""

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.websocket import ws_manager
from backend.app.models.printer import Printer
from backend.app.services.bambu_ftp import (
    get_ftp_retry_settings,
    get_storage_info_async,
    upload_file_async,
    with_ftp_retry,
)
from backend.app.services.firmware_check import get_firmware_service
from backend.app.services.printer_manager import printer_manager

logger = logging.getLogger(__name__)


class FirmwareUploadStatus(StrEnum):
    """Status of a firmware upload operation."""

    IDLE = "idle"
    PREPARING = "preparing"
    DOWNLOADING = "downloading"
    UPLOADING = "uploading"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class FirmwareUploadState:
    """State of a firmware upload operation for a printer."""

    status: FirmwareUploadStatus = FirmwareUploadStatus.IDLE
    progress: int = 0  # 0-100
    message: str = ""
    error: str | None = None
    firmware_filename: str | None = None
    firmware_version: str | None = None


# Track upload state per printer
_upload_states: dict[int, FirmwareUploadState] = {}


def get_upload_state(printer_id: int) -> FirmwareUploadState:
    """Get the current upload state for a printer."""
    if printer_id not in _upload_states:
        _upload_states[printer_id] = FirmwareUploadState()
    return _upload_states[printer_id]


def reset_upload_state(printer_id: int):
    """Reset the upload state for a printer."""
    _upload_states[printer_id] = FirmwareUploadState()


class FirmwareUpdateService:
    """Service for managing firmware updates."""

    # Minimum free space required (100MB buffer)
    MIN_FREE_SPACE_BYTES = 100 * 1024 * 1024

    async def prepare_update(
        self,
        printer_id: int,
        db: AsyncSession,
    ) -> dict:
        """
        Check prerequisites for firmware update.

        Returns:
            Dict with:
            - can_proceed: bool
            - sd_card_present: bool
            - sd_card_free_space: int (bytes, -1 if unknown)
            - firmware_size: int (bytes, estimated)
            - space_sufficient: bool
            - update_available: bool
            - current_version: str | None
            - latest_version: str | None
            - firmware_filename: str | None
            - errors: list[str]
        """
        result = {
            "can_proceed": False,
            "sd_card_present": False,
            "sd_card_free_space": -1,
            "firmware_size": 0,
            "space_sufficient": False,
            "update_available": False,
            "current_version": None,
            "latest_version": None,
            "firmware_filename": None,
            "errors": [],
        }

        # Get printer from database
        stmt = select(Printer).where(Printer.id == printer_id)
        db_result = await db.execute(stmt)
        printer = db_result.scalar_one_or_none()

        if not printer:
            result["errors"].append("Printer not found")
            return result

        # Check printer is connected
        mqtt_client = printer_manager.get_client(printer_id)
        if not mqtt_client or not mqtt_client.state:
            result["errors"].append("Printer not connected")
            return result

        state = mqtt_client.state

        # Get current firmware version
        result["current_version"] = state.firmware_version

        # Check SD card
        result["sd_card_present"] = state.sdcard
        if not state.sdcard:
            result["errors"].append("No SD card inserted in printer")

        # Get storage info via FTP
        if state.sdcard:
            try:
                storage_info = await get_storage_info_async(
                    printer.ip_address,
                    printer.access_code,
                )
                if storage_info and "free_bytes" in storage_info:
                    result["sd_card_free_space"] = storage_info["free_bytes"]
            except Exception as e:
                logger.warning(f"Could not get storage info: {e}")

        # Check for firmware update
        firmware_service = get_firmware_service()
        model = printer.model or "Unknown"

        if state.firmware_version:
            update_info = await firmware_service.check_for_update(model, state.firmware_version)
            result["update_available"] = update_info["update_available"]
            result["latest_version"] = update_info["latest_version"]
        else:
            # If we don't know current version, just get latest
            latest = await firmware_service.get_latest_version(model)
            if latest:
                result["latest_version"] = latest.version
                result["update_available"] = True  # Assume update needed

        if not result["update_available"]:
            result["errors"].append("Firmware is already up to date")

        # Get firmware file info
        file_info = await firmware_service.get_firmware_file_info(model)
        if file_info:
            result["firmware_filename"] = file_info["filename"]
            # Estimate size (typical firmware is 50-150MB)
            # We'll get actual size during download
            result["firmware_size"] = 100 * 1024 * 1024  # 100MB estimate

        # Check space
        if result["sd_card_free_space"] > 0:
            # Need firmware size + buffer
            required = result["firmware_size"] + self.MIN_FREE_SPACE_BYTES
            result["space_sufficient"] = result["sd_card_free_space"] >= required
            if not result["space_sufficient"]:
                result["errors"].append(
                    f"Insufficient SD card space. Need {required // (1024 * 1024)}MB, "
                    f"have {result['sd_card_free_space'] // (1024 * 1024)}MB"
                )
        elif result["sd_card_present"]:
            # Couldn't determine space, assume sufficient
            result["space_sufficient"] = True

        # Final check
        result["can_proceed"] = (
            result["sd_card_present"]
            and result["space_sufficient"]
            and result["update_available"]
            and len(result["errors"]) == 0
        )

        return result

    async def start_upload(
        self,
        printer_id: int,
        db: AsyncSession,
    ) -> bool:
        """
        Start the firmware upload process.

        This runs asynchronously and broadcasts progress via WebSocket.
        Returns True if upload started successfully.
        """
        state = get_upload_state(printer_id)

        # Check if already in progress
        if state.status in (FirmwareUploadStatus.DOWNLOADING, FirmwareUploadStatus.UPLOADING):
            logger.warning(f"Firmware upload already in progress for printer {printer_id}")
            return False

        # Get printer
        stmt = select(Printer).where(Printer.id == printer_id)
        db_result = await db.execute(stmt)
        printer = db_result.scalar_one_or_none()

        if not printer:
            state.status = FirmwareUploadStatus.ERROR
            state.error = "Printer not found"
            return False

        # Get printer model
        model = printer.model or "Unknown"

        # Reset state
        reset_upload_state(printer_id)
        state = get_upload_state(printer_id)
        state.status = FirmwareUploadStatus.PREPARING
        state.message = "Preparing firmware update..."
        await self._broadcast_progress(printer_id, state)

        # Run the upload in background
        asyncio.create_task(
            self._do_upload(
                printer_id=printer_id,
                ip_address=printer.ip_address,
                access_code=printer.access_code,
                model=model,
            )
        )

        return True

    async def _do_upload(
        self,
        printer_id: int,
        ip_address: str,
        access_code: str,
        model: str,
    ):
        """Perform the actual firmware download and upload."""
        state = get_upload_state(printer_id)
        firmware_service = get_firmware_service()

        try:
            # Download firmware (quick, usually cached)
            state.status = FirmwareUploadStatus.DOWNLOADING
            state.progress = 0
            state.message = "Preparing firmware..."
            await self._broadcast_progress(printer_id, state)

            firmware_path = await firmware_service.download_firmware(model)

            if not firmware_path:
                raise Exception("Failed to download firmware")

            state.firmware_filename = firmware_path.name

            # Get firmware version for state
            latest = await firmware_service.get_latest_version(model)
            if latest:
                state.firmware_version = latest.version

            # Upload to printer (0-100% progress shown here)
            state.status = FirmwareUploadStatus.UPLOADING
            state.progress = 0
            state.message = f"Uploading {firmware_path.name} to printer..."
            await self._broadcast_progress(printer_id, state)

            # Upload to root of SD card (where printer expects firmware)
            remote_path = f"/{firmware_path.name}"

            logger.info(f"Uploading firmware to printer {printer_id}: {remote_path}")

            # Track real progress via FTP callback
            loop = asyncio.get_event_loop()
            last_progress = 0

            def on_upload_progress(uploaded: int, total: int):
                nonlocal last_progress
                if total > 0:
                    progress = int((uploaded / total) * 100)
                    # Only broadcast every 1% to avoid flooding
                    if progress > last_progress:
                        last_progress = progress
                        state.progress = min(99, progress)  # Cap at 99 until complete
                        asyncio.run_coroutine_threadsafe(self._broadcast_progress(printer_id, state), loop)

            # Get FTP retry settings
            ftp_retry_enabled, ftp_retry_count, ftp_retry_delay, ftp_timeout = await get_ftp_retry_settings()

            if ftp_retry_enabled:
                success = await with_ftp_retry(
                    upload_file_async,
                    ip_address,
                    access_code,
                    firmware_path,
                    remote_path,
                    progress_callback=on_upload_progress,
                    socket_timeout=ftp_timeout,
                    printer_model=model,
                    max_retries=ftp_retry_count,
                    retry_delay=ftp_retry_delay,
                    operation_name=f"Upload firmware to printer {printer_id}",
                )
            else:
                success = await upload_file_async(
                    ip_address,
                    access_code,
                    firmware_path,
                    remote_path,
                    progress_callback=on_upload_progress,
                    socket_timeout=ftp_timeout,
                    printer_model=model,
                )

            if not success:
                raise Exception("Failed to upload firmware to printer")

            # Complete
            state.status = FirmwareUploadStatus.COMPLETE
            state.progress = 100
            state.message = (
                f"Firmware {state.firmware_version or ''} uploaded successfully! "
                "Please go to printer screen and trigger the update from Settings > Firmware."
            )
            await self._broadcast_progress(printer_id, state)

            logger.info(f"Firmware upload complete for printer {printer_id}")

        except Exception as e:
            logger.error(f"Firmware upload failed for printer {printer_id}: {e}")
            state.status = FirmwareUploadStatus.ERROR
            state.error = str(e)
            state.message = f"Firmware upload failed: {e}"
            await self._broadcast_progress(printer_id, state)

    async def _broadcast_progress(self, printer_id: int, state: FirmwareUploadState):
        """Broadcast firmware upload progress via WebSocket."""
        await ws_manager.broadcast(
            {
                "type": "firmware_upload_progress",
                "printer_id": printer_id,
                "status": state.status.value,
                "progress": state.progress,
                "message": state.message,
                "error": state.error,
                "firmware_filename": state.firmware_filename,
                "firmware_version": state.firmware_version,
            }
        )


# Singleton instance
_firmware_update_service: FirmwareUpdateService | None = None


def get_firmware_update_service() -> FirmwareUpdateService:
    """Get the singleton firmware update service instance."""
    global _firmware_update_service
    if _firmware_update_service is None:
        _firmware_update_service = FirmwareUpdateService()
    return _firmware_update_service
