"""Print scheduler service - processes the print queue."""

import asyncio
import logging
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.core.database import async_session
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.smart_plug import SmartPlug
from backend.app.services.bambu_ftp import delete_file_async, get_ftp_retry_settings, upload_file_async, with_ftp_retry
from backend.app.services.notification_service import notification_service
from backend.app.services.printer_manager import printer_manager
from backend.app.services.smart_plug_manager import smart_plug_manager
from backend.app.utils.printer_models import normalize_printer_model

logger = logging.getLogger(__name__)


class PrintScheduler:
    """Background scheduler that processes the print queue."""

    def __init__(self):
        self._running = False
        self._check_interval = 30  # seconds
        self._power_on_wait_time = 180  # seconds to wait for printer after power on (3 min)
        self._power_on_check_interval = 10  # seconds between connection checks

    async def run(self):
        """Main loop - check queue every interval."""
        self._running = True
        logger.info("Print scheduler started")

        while self._running:
            try:
                await self.check_queue()
            except Exception as e:
                logger.error(f"Scheduler error: {e}")

            await asyncio.sleep(self._check_interval)

    def stop(self):
        """Stop the scheduler."""
        self._running = False
        logger.info("Print scheduler stopped")

    async def check_queue(self):
        """Check for prints ready to start."""
        async with async_session() as db:
            # Get all pending items, ordered by printer and position
            result = await db.execute(
                select(PrintQueueItem)
                .where(PrintQueueItem.status == "pending")
                .order_by(PrintQueueItem.printer_id, PrintQueueItem.position)
            )
            items = list(result.scalars().all())

            if not items:
                return

            # Track busy printers to avoid assigning multiple items to same printer
            busy_printers: set[int] = set()

            for item in items:
                # Check scheduled time first (scheduled_time is stored in UTC from ISO string)
                if item.scheduled_time and item.scheduled_time > datetime.utcnow():
                    continue

                # Skip items that require manual start
                if item.manual_start:
                    continue

                if item.printer_id:
                    # Specific printer assignment (existing behavior)
                    if item.printer_id in busy_printers:
                        continue

                    # Check if printer is idle
                    printer_idle = self._is_printer_idle(item.printer_id)
                    printer_connected = printer_manager.is_connected(item.printer_id)

                    # If printer not connected, try to power on via smart plug
                    if not printer_connected:
                        plug = await self._get_smart_plug(db, item.printer_id)
                        if plug and plug.auto_on and plug.enabled:
                            logger.info(f"Printer {item.printer_id} offline, attempting to power on via smart plug")
                            powered_on = await self._power_on_and_wait(plug, item.printer_id, db)
                            if powered_on:
                                printer_connected = True
                                printer_idle = self._is_printer_idle(item.printer_id)
                            else:
                                logger.warning(f"Could not power on printer {item.printer_id} via smart plug")
                                busy_printers.add(item.printer_id)
                                continue
                        else:
                            # No plug or auto_on disabled
                            busy_printers.add(item.printer_id)
                            continue

                    # Check if printer is idle (busy with another print)
                    if not printer_idle:
                        busy_printers.add(item.printer_id)
                        continue

                    # Check condition (previous print success)
                    if item.require_previous_success:
                        if not await self._check_previous_success(db, item):
                            item.status = "skipped"
                            item.error_message = "Previous print failed or was aborted"
                            item.completed_at = datetime.now()
                            await db.commit()
                            logger.info(f"Skipped queue item {item.id} - previous print failed")

                            # Send notification
                            job_name = await self._get_job_name(db, item)
                            printer = await self._get_printer(db, item.printer_id)
                            await notification_service.on_queue_job_skipped(
                                job_name=job_name,
                                printer_id=item.printer_id,
                                printer_name=printer.name if printer else "Unknown",
                                reason="Previous print failed or was aborted",
                                db=db,
                            )
                            continue

                    # Start the print
                    await self._start_print(db, item)
                    busy_printers.add(item.printer_id)

                elif item.target_model:
                    # Model-based assignment - find any idle printer of matching model
                    # Parse required filament types if present
                    required_types = None
                    if item.required_filament_types:
                        try:
                            import json

                            required_types = json.loads(item.required_filament_types)
                        except json.JSONDecodeError:
                            pass

                    printer_id, waiting_reason = await self._find_idle_printer_for_model(
                        db, item.target_model, busy_printers, required_types
                    )

                    # Update waiting_reason if changed and send notification when first waiting
                    if item.waiting_reason != waiting_reason:
                        was_waiting = item.waiting_reason is not None
                        item.waiting_reason = waiting_reason
                        await db.commit()

                        # Send waiting notification only when transitioning to waiting state
                        if waiting_reason and not was_waiting:
                            job_name = await self._get_job_name(db, item)
                            await notification_service.on_queue_job_waiting(
                                job_name=job_name,
                                target_model=item.target_model,
                                waiting_reason=waiting_reason,
                                db=db,
                            )

                    if printer_id:
                        # Check condition (previous print success) before assigning
                        if item.require_previous_success:
                            if not await self._check_previous_success(db, item):
                                item.status = "skipped"
                                item.error_message = "Previous print failed or was aborted"
                                item.completed_at = datetime.now()
                                await db.commit()
                                logger.info(f"Skipped queue item {item.id} - previous print failed")

                                # Send notification
                                job_name = await self._get_job_name(db, item)
                                printer = await self._get_printer(db, printer_id)
                                await notification_service.on_queue_job_skipped(
                                    job_name=job_name,
                                    printer_id=printer_id,
                                    printer_name=printer.name if printer else "Unknown",
                                    reason="Previous print failed or was aborted",
                                    db=db,
                                )
                                continue

                        # Assign printer and start - clear waiting reason
                        item.printer_id = printer_id
                        item.waiting_reason = None
                        logger.info(f"Model-based assignment: queue item {item.id} assigned to printer {printer_id}")

                        # Send assignment notification
                        job_name = await self._get_job_name(db, item)
                        printer = await self._get_printer(db, printer_id)
                        await notification_service.on_queue_job_assigned(
                            job_name=job_name,
                            printer_id=printer_id,
                            printer_name=printer.name if printer else "Unknown",
                            target_model=item.target_model,
                            db=db,
                        )

                        await self._start_print(db, item)
                        busy_printers.add(printer_id)

    async def _find_idle_printer_for_model(
        self,
        db: AsyncSession,
        model: str,
        exclude_ids: set[int],
        required_filament_types: list[str] | None = None,
    ) -> tuple[int | None, str | None]:
        """Find an idle, connected printer matching the model with compatible filaments.

        Args:
            db: Database session
            model: Printer model to match (e.g., "X1C", "P1S")
            exclude_ids: Printer IDs to exclude (already busy)
            required_filament_types: Optional list of filament types needed (e.g., ["PLA", "PETG"])
                                     If provided, only printers with all required types loaded will match.

        Returns:
            Tuple of (printer_id, waiting_reason):
            - (printer_id, None) if a matching printer was found
            - (None, reason) if no printer is available, with explanation
        """
        # Normalize model name and use case-insensitive matching
        normalized_model = normalize_printer_model(model) or model
        result = await db.execute(
            select(Printer)
            .where(func.lower(Printer.model) == normalized_model.lower())
            .where(Printer.is_active == True)  # noqa: E712
        )
        printers = list(result.scalars().all())

        if not printers:
            return None, f"No active {normalized_model} printers configured"

        # Track reasons for skipping printers
        printers_busy = []
        printers_offline = []
        printers_missing_filament = []

        for printer in printers:
            if printer.id in exclude_ids:
                printers_busy.append(printer.name)
                continue

            is_connected = printer_manager.is_connected(printer.id)
            is_idle = self._is_printer_idle(printer.id) if is_connected else False

            if not is_connected:
                printers_offline.append(printer.name)
                continue

            if not is_idle:
                printers_busy.append(printer.name)
                continue

            # Validate filament compatibility if required types are specified
            if required_filament_types:
                missing = self._get_missing_filament_types(printer.id, required_filament_types)
                if missing:
                    printers_missing_filament.append((printer.name, missing))
                    logger.debug(f"Skipping printer {printer.id} ({printer.name}) - missing filaments: {missing}")
                    continue

            # Found a matching printer - clear waiting reason
            return printer.id, None

        # Build waiting reason from what we found
        reasons = []
        if printers_missing_filament:
            # Filament mismatch is most actionable - show first
            names_and_missing = [f"{name} (needs {', '.join(missing)})" for name, missing in printers_missing_filament]
            reasons.append(f"Waiting for filament: {'; '.join(names_and_missing)}")
        if printers_busy:
            reasons.append(f"Busy: {', '.join(printers_busy)}")
        if printers_offline:
            reasons.append(f"Offline: {', '.join(printers_offline)}")

        return None, " | ".join(reasons) if reasons else f"No available {model} printers"

    def _get_missing_filament_types(self, printer_id: int, required_types: list[str]) -> list[str]:
        """Get the list of required filament types that are not loaded on the printer.

        Args:
            printer_id: The printer ID
            required_types: List of filament types needed (e.g., ["PLA", "PETG"])

        Returns:
            List of missing filament types (empty if all are loaded)
        """
        status = printer_manager.get_status(printer_id)
        if not status:
            return required_types  # Can't determine, assume all missing

        # Collect all filament types loaded on this printer (AMS units + external spool)
        loaded_types: set[str] = set()

        # Check AMS units (stored in raw_data["ams"])
        ams_data = status.raw_data.get("ams", [])
        if ams_data:
            for ams_unit in ams_data:
                for tray in ams_unit.get("tray", []):
                    tray_type = tray.get("tray_type")
                    if tray_type:
                        loaded_types.add(tray_type.upper())

        # Check external spool (virtual tray, stored in raw_data["vt_tray"])
        vt_tray = status.raw_data.get("vt_tray")
        if vt_tray:
            vt_type = vt_tray.get("tray_type")
            if vt_type:
                loaded_types.add(vt_type.upper())

        # Find which required types are missing (case-insensitive comparison)
        missing = []
        for req_type in required_types:
            if req_type.upper() not in loaded_types:
                missing.append(req_type)

        return missing

    def _is_printer_idle(self, printer_id: int) -> bool:
        """Check if a printer is connected and idle."""
        if not printer_manager.is_connected(printer_id):
            return False

        state = printer_manager.get_status(printer_id)
        if not state:
            return False

        # Printer is idle if state is IDLE, FINISH, FAILED, or unknown
        # FAILED means previous print failed, printer is ready for new print
        return state.state in ("IDLE", "FINISH", "FAILED", "unknown")

    async def _get_smart_plug(self, db: AsyncSession, printer_id: int) -> SmartPlug | None:
        """Get the smart plug associated with a printer."""
        result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
        return result.scalar_one_or_none()

    async def _power_on_and_wait(self, plug: SmartPlug, printer_id: int, db: AsyncSession) -> bool:
        """Turn on smart plug and wait for printer to connect.

        Returns True if printer connected successfully within timeout.
        """
        # Get the appropriate service for the plug type (Tasmota or Home Assistant)
        service = await smart_plug_manager.get_service_for_plug(plug, db)

        # Check current plug state
        status = await service.get_status(plug)
        if not status.get("reachable"):
            logger.warning(f"Smart plug '{plug.name}' is not reachable")
            return False

        # Turn on if not already on
        if status.get("state") != "ON":
            success = await service.turn_on(plug)
            if not success:
                logger.warning(f"Failed to turn on smart plug '{plug.name}'")
                return False
            logger.info(f"Powered on smart plug '{plug.name}' for printer {printer_id}")

        # Get printer from database for connection
        result = await db.execute(select(Printer).where(Printer.id == printer_id))
        printer = result.scalar_one_or_none()
        if not printer:
            logger.error(f"Printer {printer_id} not found in database")
            return False

        # Wait for printer to boot (give it some time before trying to connect)
        logger.info(f"Waiting 30s for printer {printer_id} to boot...")
        await asyncio.sleep(30)

        # Try to connect to the printer periodically
        elapsed = 30  # Already waited 30s
        while elapsed < self._power_on_wait_time:
            # Try to connect
            logger.info(f"Attempting to connect to printer {printer_id}...")
            try:
                connected = await printer_manager.connect_printer(printer)
                if connected:
                    logger.info(f"Printer {printer_id} connected after {elapsed}s")
                    # Give it a moment to stabilize and get status
                    await asyncio.sleep(5)
                    return True
            except Exception as e:
                logger.debug(f"Connection attempt failed: {e}")

            await asyncio.sleep(self._power_on_check_interval)
            elapsed += self._power_on_check_interval
            logger.debug(f"Waiting for printer {printer_id} to connect... ({elapsed}s)")

        logger.warning(f"Printer {printer_id} did not connect within {self._power_on_wait_time}s after power on")
        return False

    async def _check_previous_success(self, db: AsyncSession, item: PrintQueueItem) -> bool:
        """Check if the previous print on this printer succeeded."""
        # Find the most recent completed queue item for this printer
        result = await db.execute(
            select(PrintQueueItem)
            .where(PrintQueueItem.printer_id == item.printer_id)
            .where(PrintQueueItem.id != item.id)
            .where(PrintQueueItem.status.in_(["completed", "failed", "skipped", "aborted"]))
            .order_by(PrintQueueItem.completed_at.desc())
            .limit(1)
        )
        prev_item = result.scalar_one_or_none()

        # If no previous item, assume success (first in queue)
        if not prev_item:
            return True

        return prev_item.status == "completed"

    async def _power_off_if_needed(self, db: AsyncSession, item: PrintQueueItem):
        """Power off printer if auto_off_after is enabled (waits for cooldown)."""
        if not item.auto_off_after:
            return

        plug = await self._get_smart_plug(db, item.printer_id)
        if plug and plug.enabled:
            logger.info(f"Auto-off: Waiting for printer {item.printer_id} to cool down before power off...")
            # Wait for cooldown (up to 10 minutes)
            await printer_manager.wait_for_cooldown(item.printer_id, target_temp=50.0, timeout=600)
            logger.info(f"Auto-off: Powering off printer {item.printer_id}")
            service = await smart_plug_manager.get_service_for_plug(plug, db)
            await service.turn_off(plug)

    async def _get_job_name(self, db: AsyncSession, item: PrintQueueItem) -> str:
        """Get a human-readable name for a queue item."""
        if item.archive_id:
            result = await db.execute(select(PrintArchive).where(PrintArchive.id == item.archive_id))
            archive = result.scalar_one_or_none()
            if archive:
                return archive.filename.replace(".gcode.3mf", "").replace(".3mf", "")
        if item.library_file_id:
            result = await db.execute(select(LibraryFile).where(LibraryFile.id == item.library_file_id))
            library_file = result.scalar_one_or_none()
            if library_file:
                return library_file.filename.replace(".gcode.3mf", "").replace(".3mf", "")
        return f"Job #{item.id}"

    async def _get_printer(self, db: AsyncSession, printer_id: int) -> Printer | None:
        """Get printer by ID."""
        result = await db.execute(select(Printer).where(Printer.id == printer_id))
        return result.scalar_one_or_none()

    async def _start_print(self, db: AsyncSession, item: PrintQueueItem):
        """Upload file and start print for a queue item.

        Supports two sources:
        - archive_id: Print from an existing archive
        - library_file_id: Print from a library file (file manager)
        """
        logger.info(f"Starting queue item {item.id}")

        # Get printer first (needed for both paths)
        result = await db.execute(select(Printer).where(Printer.id == item.printer_id))
        printer = result.scalar_one_or_none()
        if not printer:
            item.status = "failed"
            item.error_message = "Printer not found"
            item.completed_at = datetime.utcnow()
            await db.commit()
            logger.error(f"Queue item {item.id}: Printer {item.printer_id} not found")
            await self._power_off_if_needed(db, item)
            return

        # Check printer is connected
        if not printer_manager.is_connected(item.printer_id):
            item.status = "failed"
            item.error_message = "Printer not connected"
            item.completed_at = datetime.utcnow()
            await db.commit()
            logger.error(f"Queue item {item.id}: Printer {item.printer_id} not connected")
            await self._power_off_if_needed(db, item)
            return

        # Determine source: archive or library file
        archive = None
        library_file = None
        file_path = None
        filename = None

        if item.archive_id:
            # Print from archive
            result = await db.execute(select(PrintArchive).where(PrintArchive.id == item.archive_id))
            archive = result.scalar_one_or_none()
            if not archive:
                item.status = "failed"
                item.error_message = "Archive not found"
                item.completed_at = datetime.utcnow()
                await db.commit()
                logger.error(f"Queue item {item.id}: Archive {item.archive_id} not found")
                await self._power_off_if_needed(db, item)
                return
            file_path = settings.base_dir / archive.file_path
            filename = archive.filename

        elif item.library_file_id:
            # Print from library file (file manager)
            result = await db.execute(select(LibraryFile).where(LibraryFile.id == item.library_file_id))
            library_file = result.scalar_one_or_none()
            if not library_file:
                item.status = "failed"
                item.error_message = "Library file not found"
                item.completed_at = datetime.utcnow()
                await db.commit()
                logger.error(f"Queue item {item.id}: Library file {item.library_file_id} not found")
                await self._power_off_if_needed(db, item)
                return
            # Library files store absolute paths
            from pathlib import Path

            lib_path = Path(library_file.file_path)
            file_path = lib_path if lib_path.is_absolute() else settings.base_dir / library_file.file_path
            filename = library_file.filename

        else:
            # Neither archive nor library file specified
            item.status = "failed"
            item.error_message = "No source file specified"
            item.completed_at = datetime.utcnow()
            await db.commit()
            logger.error(f"Queue item {item.id}: No archive_id or library_file_id specified")
            await self._power_off_if_needed(db, item)
            return

        # Check file exists on disk
        if not file_path.exists():
            item.status = "failed"
            item.error_message = "Source file not found on disk"
            item.completed_at = datetime.utcnow()
            await db.commit()
            logger.error(f"Queue item {item.id}: File not found: {file_path}")
            await self._power_off_if_needed(db, item)
            return

        # Upload file to printer via FTP
        # Use a clean filename to avoid issues with double extensions like .gcode.3mf
        base_name = filename
        if base_name.endswith(".gcode.3mf"):
            base_name = base_name[:-10]  # Remove .gcode.3mf
        elif base_name.endswith(".3mf"):
            base_name = base_name[:-4]  # Remove .3mf
        remote_filename = f"{base_name}.3mf"
        # Upload to root directory (not /cache/) - the start_print command references
        # files by name only (ftp://{filename}), so they must be in the root
        remote_path = f"/{remote_filename}"

        # Get FTP retry settings
        ftp_retry_enabled, ftp_retry_count, ftp_retry_delay, ftp_timeout = await get_ftp_retry_settings()

        # Delete existing file if present (avoids 553 error on overwrite)
        try:
            await delete_file_async(
                printer.ip_address,
                printer.access_code,
                remote_path,
                socket_timeout=ftp_timeout,
                printer_model=printer.model,
            )
        except Exception:
            pass  # File may not exist, that's fine

        try:
            if ftp_retry_enabled:
                uploaded = await with_ftp_retry(
                    upload_file_async,
                    printer.ip_address,
                    printer.access_code,
                    file_path,
                    remote_path,
                    socket_timeout=ftp_timeout,
                    printer_model=printer.model,
                    max_retries=ftp_retry_count,
                    retry_delay=ftp_retry_delay,
                    operation_name=f"Upload print to {printer.name}",
                )
            else:
                uploaded = await upload_file_async(
                    printer.ip_address,
                    printer.access_code,
                    file_path,
                    remote_path,
                    socket_timeout=ftp_timeout,
                    printer_model=printer.model,
                )
        except Exception as e:
            uploaded = False
            logger.error(f"Queue item {item.id}: FTP error: {e}")

        if not uploaded:
            item.status = "failed"
            item.error_message = "Failed to upload file to printer"
            item.completed_at = datetime.utcnow()
            await db.commit()
            logger.error(f"Queue item {item.id}: FTP upload failed")

            # Send failure notification
            await notification_service.on_queue_job_failed(
                job_name=filename.replace(".gcode.3mf", "").replace(".3mf", ""),
                printer_id=printer.id,
                printer_name=printer.name,
                reason="Failed to upload file to printer",
                db=db,
            )

            await self._power_off_if_needed(db, item)
            return

        # Register as expected print so we don't create a duplicate archive
        # Only applicable for archive-based prints
        if archive:
            from backend.app.main import register_expected_print

            register_expected_print(item.printer_id, remote_filename, archive.id)

        # Parse AMS mapping if stored
        ams_mapping = None
        if item.ams_mapping:
            try:
                import json

                ams_mapping = json.loads(item.ams_mapping)
            except json.JSONDecodeError:
                logger.warning(f"Queue item {item.id}: Invalid AMS mapping JSON, ignoring")

        # Start the print with AMS mapping, plate_id and print options
        started = printer_manager.start_print(
            item.printer_id,
            remote_filename,
            plate_id=item.plate_id or 1,
            ams_mapping=ams_mapping,
            bed_levelling=item.bed_levelling,
            flow_cali=item.flow_cali,
            vibration_cali=item.vibration_cali,
            layer_inspect=item.layer_inspect,
            timelapse=item.timelapse,
            use_ams=item.use_ams,
        )

        if started:
            item.status = "printing"
            item.started_at = datetime.utcnow()
            await db.commit()
            logger.info(f"Queue item {item.id}: Print started - {filename}")

            # Get estimated time for notification
            estimated_time = None
            if archive and archive.print_time_seconds:
                estimated_time = archive.print_time_seconds
            elif library_file and library_file.print_time_seconds:
                estimated_time = library_file.print_time_seconds

            # Send job started notification
            await notification_service.on_queue_job_started(
                job_name=filename.replace(".gcode.3mf", "").replace(".3mf", ""),
                printer_id=printer.id,
                printer_name=printer.name,
                db=db,
                estimated_time=estimated_time,
            )

            # MQTT relay - publish queue job started
            try:
                from backend.app.services.mqtt_relay import mqtt_relay

                await mqtt_relay.on_queue_job_started(
                    job_id=item.id,
                    filename=filename,
                    printer_id=printer.id,
                    printer_name=printer.name,
                    printer_serial=printer.serial_number,
                )
            except Exception:
                pass  # Don't fail if MQTT fails
        else:
            item.status = "failed"
            item.error_message = "Failed to send print command"
            item.completed_at = datetime.utcnow()
            await db.commit()
            logger.error(f"Queue item {item.id}: Failed to start print")

            # Send failure notification
            await notification_service.on_queue_job_failed(
                job_name=filename.replace(".gcode.3mf", "").replace(".3mf", ""),
                printer_id=printer.id,
                printer_name=printer.name,
                reason="Failed to send print command",
                db=db,
            )

            await self._power_off_if_needed(db, item)


# Global scheduler instance
scheduler = PrintScheduler()
