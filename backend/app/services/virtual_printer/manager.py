"""Virtual Printer Manager - coordinates SSDP, MQTT, and FTP services.

Supports multiple modes:
- immediate: Archive uploads immediately
- review: Queue uploads for user review before archiving
- print_queue: Archive and add to print queue (unassigned)
- proxy: Transparent TCP proxy to a real printer (for remote slicer access)
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from backend.app.core.config import settings as app_settings
from backend.app.services.virtual_printer.certificate import CertificateService
from backend.app.services.virtual_printer.ftp_server import VirtualPrinterFTPServer
from backend.app.services.virtual_printer.mqtt_server import SimpleMQTTServer
from backend.app.services.virtual_printer.ssdp_server import VirtualPrinterSSDPServer
from backend.app.services.virtual_printer.tcp_proxy import SlicerProxyManager

logger = logging.getLogger(__name__)


# Mapping of SSDP model codes to display names
# These are the codes that slicers expect during discovery
# Sources:
#   - https://gist.github.com/Alex-Schaefer/72a9e2491a42da2ef99fb87601955cc3
#   - https://github.com/psychoticbeef/BambuLabOrcaSlicerDiscovery
VIRTUAL_PRINTER_MODELS = {
    # X1 Series
    "3DPrinter-X1-Carbon": "X1C",  # X1 Carbon
    "3DPrinter-X1": "X1",  # X1
    "C13": "X1E",  # X1E
    # P Series
    "C11": "P1P",  # P1P
    "C12": "P1S",  # P1S
    "N7": "P2S",  # P2S
    # A1 Series
    "N2S": "A1",  # A1
    "N1": "A1 Mini",  # A1 Mini
    # H2 Series
    "O1D": "H2D",  # H2D
    "O1C": "H2C",  # H2C
    "O1S": "H2S",  # H2S
}

# Serial number prefixes for each model (based on Bambu Lab serial number format)
# Format: MMM??RYMDDUUUUU (15 chars total)
#   MMM = Model prefix (3 chars)
#   ?? = Unknown/revision code (2 chars)
#   R = Revision letter (1 char)
#   Y = Year digit (1 char)
#   M = Month (1 char, hex: 1-9, A=Oct, B=Nov, C=Dec)
#   DD = Day (2 chars)
#   UUUUU = Unit number (5 chars)
MODEL_SERIAL_PREFIXES = {
    # X1 Series
    "3DPrinter-X1-Carbon": "00M00A",  # X1C
    "3DPrinter-X1": "00M00A",  # X1
    "C13": "03W00A",  # X1E
    # P Series
    "C11": "01S00A",  # P1P
    "C12": "01P00A",  # P1S
    "N7": "22E00A",  # P2S
    # A1 Series
    "N2S": "03900A",  # A1
    "N1": "03000A",  # A1 Mini
    # H2 Series
    "O1D": "09400A",  # H2D
    "O1C": "09400A",  # H2C
    "O1S": "09400A",  # H2S
}

# Default model
DEFAULT_VIRTUAL_PRINTER_MODEL = "3DPrinter-X1-Carbon"  # X1C


class VirtualPrinterManager:
    """Manages the virtual printer lifecycle and coordinates all services."""

    # Fixed configuration
    PRINTER_NAME = "Bambuddy"
    SERIAL_SUFFIX = "391800001"  # Fixed suffix for virtual printer

    def __init__(self):
        """Initialize the virtual printer manager."""
        self._session_factory: Callable | None = None
        self._enabled = False
        self._access_code = ""
        self._mode = "immediate"
        self._model = DEFAULT_VIRTUAL_PRINTER_MODEL
        self._target_printer_ip = ""  # For proxy mode
        self._target_printer_serial = ""  # For proxy mode (real printer's serial)

        # Service instances
        self._ssdp: VirtualPrinterSSDPServer | None = None
        self._ftp: VirtualPrinterFTPServer | None = None
        self._mqtt: SimpleMQTTServer | None = None
        self._proxy: SlicerProxyManager | None = None  # For proxy mode

        # Background tasks
        self._tasks: list[asyncio.Task] = []

        # Directories
        self._base_dir = app_settings.base_dir / "virtual_printer"
        self._upload_dir = self._base_dir / "uploads"
        self._cert_dir = self._base_dir / "certs"

        # Certificate service
        self._cert_service = CertificateService(self._cert_dir)

        # Track pending uploads for MQTT correlation
        self._pending_files: dict[str, Path] = {}

    def _get_serial_for_model(self, model: str) -> str:
        """Get appropriate serial number for the given model.

        Args:
            model: SSDP model code (e.g., 'BL-P001', 'C11')

        Returns:
            Serial number with correct prefix for the model
        """
        prefix = MODEL_SERIAL_PREFIXES.get(model, "00M09A")
        return f"{prefix}{self.SERIAL_SUFFIX}"

    @property
    def printer_serial(self) -> str:
        """Get the current printer serial number based on model."""
        return self._get_serial_for_model(self._model)

    def set_session_factory(self, session_factory: Callable) -> None:
        """Set the database session factory.

        Args:
            session_factory: Async context manager for database sessions
        """
        self._session_factory = session_factory

    @property
    def is_enabled(self) -> bool:
        """Check if virtual printer is enabled."""
        return self._enabled

    @property
    def is_running(self) -> bool:
        """Check if virtual printer services are running."""
        return len(self._tasks) > 0 and all(not t.done() for t in self._tasks)

    async def configure(
        self,
        enabled: bool,
        access_code: str = "",
        mode: str = "immediate",
        model: str = "",
        target_printer_ip: str = "",
        target_printer_serial: str = "",
    ) -> None:
        """Configure and start/stop virtual printer.

        Args:
            enabled: Whether to enable the virtual printer
            access_code: Authentication password for slicer connections
            mode: Archive mode - 'immediate', 'review', 'print_queue', or 'proxy'
            model: SSDP model code (e.g., 'BL-P001' for X1C)
            target_printer_ip: Target printer IP for proxy mode
            target_printer_serial: Target printer serial for proxy mode
        """
        # Proxy mode has different requirements
        if mode == "proxy":
            if enabled and not target_printer_ip:
                raise ValueError("Target printer IP is required for proxy mode")
            # Access code not required for proxy mode (uses printer's credentials)
        else:
            if enabled and not access_code:
                raise ValueError("Access code is required when enabling virtual printer")

        # Validate model if provided
        new_model = model if model and model in VIRTUAL_PRINTER_MODELS else self._model
        model_changed = new_model != self._model
        mode_changed = mode != self._mode
        target_changed = target_printer_ip != self._target_printer_ip
        serial_changed = target_printer_serial != self._target_printer_serial
        old_mode = self._mode

        logger.debug(
            f"configure() called: enabled={enabled}, self._enabled={self._enabled}, "
            f"mode={mode}, old_mode={old_mode}, model={model}, new_model={new_model}, "
            f"target_printer_ip={target_printer_ip}, target_printer_serial={target_printer_serial}"
        )

        self._access_code = access_code
        self._mode = mode
        self._model = new_model
        self._target_printer_ip = target_printer_ip
        self._target_printer_serial = target_printer_serial

        needs_restart = model_changed or mode_changed or (mode == "proxy" and (target_changed or serial_changed))

        if enabled and not self._enabled:
            logger.info("Starting virtual printer (was disabled)")
            await self._start()
        elif not enabled and self._enabled:
            logger.info("Stopping virtual printer (was enabled)")
            await self._stop()
        elif enabled and self._enabled and needs_restart:
            # Configuration changed while running - restart services
            logger.info(f"Configuration changed (mode={old_mode}→{mode}), restarting...")
            await self._stop()
            # Give time for ports to be released
            await asyncio.sleep(0.5)
            await self._start()
            logger.info("Virtual printer restarted with new configuration")
        else:
            logger.debug(f"No state change needed (enabled={enabled}, self._enabled={self._enabled})")

        self._enabled = enabled

    async def _start(self) -> None:
        """Start all virtual printer services."""
        logger.info(f"Starting virtual printer services (mode={self._mode})...")

        # Proxy mode uses different services
        if self._mode == "proxy":
            await self._start_proxy_mode()
            return

        # Standard modes (immediate, review, print_queue) use FTP/MQTT servers
        await self._start_server_mode()

    async def _start_proxy_mode(self) -> None:
        """Start virtual printer in proxy mode (TLS terminating relay)."""
        logger.info(f"Starting proxy mode to {self._target_printer_ip}")

        # In proxy mode, use the REAL printer's serial number
        # This ensures MQTT topic subscriptions match the real printer's topics
        proxy_serial = self._target_printer_serial or self.printer_serial
        logger.info(f"Proxy mode using serial: {proxy_serial}")

        # Update certificate service with the real printer's serial
        self._cert_service.serial = proxy_serial

        # Regenerate printer cert if needed (CA is preserved)
        self._cert_service.delete_printer_certificate()
        cert_path, key_path = self._cert_service.generate_certificates()
        logger.info(f"Generated certificate for proxy serial: {proxy_serial}")

        # Initialize SSDP for local discovery using the real printer's serial
        self._ssdp = VirtualPrinterSSDPServer(
            name=f"{self.PRINTER_NAME} (Proxy)",
            serial=proxy_serial,
            model=self._model,
        )

        # Initialize TLS proxy with our certificates
        self._proxy = SlicerProxyManager(
            target_host=self._target_printer_ip,
            cert_path=cert_path,
            key_path=key_path,
            on_activity=self._on_proxy_activity,
        )

        # Start services as background tasks
        async def run_with_logging(coro, name):
            try:
                await coro
            except Exception as e:
                logger.error(f"Virtual printer {name} failed: {e}")

        self._tasks = [
            asyncio.create_task(
                run_with_logging(self._ssdp.start(), "SSDP"),
                name="virtual_printer_ssdp",
            ),
            asyncio.create_task(
                run_with_logging(self._proxy.start(), "Proxy"),
                name="virtual_printer_proxy",
            ),
        ]

        logger.info(
            f"Virtual printer proxy started: "
            f"FTP 0.0.0.0:{SlicerProxyManager.LOCAL_FTP_PORT} → {self._target_printer_ip}:{SlicerProxyManager.PRINTER_FTP_PORT}, "
            f"MQTT 0.0.0.0:{SlicerProxyManager.LOCAL_MQTT_PORT} → {self._target_printer_ip}:{SlicerProxyManager.PRINTER_MQTT_PORT}"
        )

    async def _start_server_mode(self) -> None:
        """Start virtual printer in server mode (FTP/MQTT servers)."""
        # Update certificate service with current serial (based on model)
        current_serial = self.printer_serial
        self._cert_service.serial = current_serial

        # Regenerate printer cert if serial changed (CA is preserved)
        self._cert_service.delete_printer_certificate()
        cert_path, key_path = self._cert_service.generate_certificates()
        logger.info(f"Generated certificate for serial: {current_serial}")

        # Create directories
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        (self._upload_dir / "cache").mkdir(exist_ok=True)

        # Initialize services
        self._ssdp = VirtualPrinterSSDPServer(
            name=self.PRINTER_NAME,
            serial=self.printer_serial,
            model=self._model,
        )

        self._ftp = VirtualPrinterFTPServer(
            upload_dir=self._upload_dir,
            access_code=self._access_code,
            cert_path=cert_path,
            key_path=key_path,
            on_file_received=self._on_file_received,
        )

        self._mqtt = SimpleMQTTServer(
            serial=self.printer_serial,
            access_code=self._access_code,
            cert_path=cert_path,
            key_path=key_path,
            on_print_command=self._on_print_command,
        )

        # Start services as background tasks
        # Wrap each in error handler so one failure doesn't stop others
        async def run_with_logging(coro, name):
            try:
                await coro
            except Exception as e:
                logger.error(f"Virtual printer {name} failed: {e}")

        self._tasks = [
            asyncio.create_task(run_with_logging(self._ssdp.start(), "SSDP"), name="virtual_printer_ssdp"),
            asyncio.create_task(run_with_logging(self._ftp.start(), "FTP"), name="virtual_printer_ftp"),
            asyncio.create_task(run_with_logging(self._mqtt.start(), "MQTT"), name="virtual_printer_mqtt"),
        ]

        logger.info(f"Virtual printer '{self.PRINTER_NAME}' started (serial: {self.printer_serial})")

    def _on_proxy_activity(self, name: str, message: str) -> None:
        """Handle proxy activity for logging."""
        logger.info(f"Proxy {name}: {message}")

    async def _stop(self) -> None:
        """Stop all virtual printer services."""
        logger.info("Stopping virtual printer services...")

        # Stop services first - this closes servers and cancels active sessions
        if self._ftp:
            await self._ftp.stop()
            self._ftp = None

        if self._mqtt:
            await self._mqtt.stop()
            self._mqtt = None

        if self._ssdp:
            await self._ssdp.stop()
            self._ssdp = None

        if self._proxy:
            await self._proxy.stop()
            self._proxy = None

        # Cancel remaining tasks with short timeout
        for task in self._tasks:
            task.cancel()

        if self._tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*self._tasks, return_exceptions=True), timeout=1.0)
            except TimeoutError:
                logger.debug("Some tasks didn't stop in time")

        self._tasks = []

        logger.info("Virtual printer stopped")

    async def _on_file_received(self, file_path: Path, source_ip: str) -> None:
        """Handle file upload completion from FTP.

        Args:
            file_path: Path to uploaded file
            source_ip: IP address of the uploading slicer
        """
        logger.info(f"Virtual printer received file: {file_path.name} from {source_ip}")

        # Store file reference for MQTT correlation
        self._pending_files[file_path.name] = file_path

        # Handle based on mode:
        # - immediate: archive right away
        # - review: create pending upload record for user review before archiving
        # - print_queue: archive and add to print queue (unassigned)
        if self._mode == "immediate":
            await self._archive_file(file_path, source_ip)
        elif self._mode == "print_queue":
            await self._add_to_print_queue(file_path, source_ip)
        else:
            # "review" mode (or legacy "queue" mode)
            await self._queue_file(file_path, source_ip)

    async def _on_print_command(self, filename: str, data: dict) -> None:
        """Handle print command from MQTT.

        In a real printer, this would start the print. For virtual printer,
        we just log it since archiving is handled by file upload.

        Args:
            filename: Name of the file to print
            data: Print command data (contains settings like timelapse, bed_leveling, etc.)
        """
        logger.info(f"Virtual printer received print command for: {filename}")
        logger.debug(f"Print command data: {data}")

        # The file should already be archived from FTP upload
        # This command just confirms the slicer's intent to "print"

    async def _archive_file(self, file_path: Path, source_ip: str) -> None:
        """Archive file immediately.

        Args:
            file_path: Path to the 3MF file
            source_ip: IP address of uploader
        """
        if not self._session_factory:
            logger.error("Cannot archive: no database session factory configured")
            return

        # Only archive 3MF files
        if file_path.suffix.lower() != ".3mf":
            logger.debug(f"Skipping non-3MF file: {file_path.name}")
            # Remove from pending and clean up
            self._pending_files.pop(file_path.name, None)
            try:
                file_path.unlink()
            except Exception:
                pass
            return

        try:
            from backend.app.services.archive import ArchiveService

            async with self._session_factory() as db:
                service = ArchiveService(db)

                # Archive the print
                archive = await service.archive_print(
                    printer_id=None,  # No physical printer
                    source_file=file_path,
                    print_data={
                        "status": "archived",
                        "source": "virtual_printer",
                        "source_ip": source_ip,
                    },
                )

                if archive:
                    logger.info(f"Archived virtual printer upload: {archive.id} - {archive.print_name}")

                    # Clean up uploaded file (it's now copied to archive)
                    try:
                        file_path.unlink()
                    except Exception:
                        pass

                    # Remove from pending
                    self._pending_files.pop(file_path.name, None)
                else:
                    logger.error(f"Failed to archive file: {file_path.name}")

        except Exception as e:
            logger.error(f"Error archiving file: {e}")

    async def _queue_file(self, file_path: Path, source_ip: str) -> None:
        """Queue file for user review.

        Args:
            file_path: Path to the 3MF file
            source_ip: IP address of uploader
        """
        if not self._session_factory:
            logger.error("Cannot queue: no database session factory configured")
            return

        # Only queue 3MF files
        if file_path.suffix.lower() != ".3mf":
            logger.warning(f"Skipping non-3MF file: {file_path.name}")
            return

        try:
            from backend.app.models.pending_upload import PendingUpload

            async with self._session_factory() as db:
                pending = PendingUpload(
                    filename=file_path.name,
                    file_path=str(file_path),
                    file_size=file_path.stat().st_size,
                    source_ip=source_ip,
                    status="pending",
                    uploaded_at=datetime.now(UTC),
                )
                db.add(pending)
                await db.commit()

                logger.info(f"Queued virtual printer upload: {pending.id} - {file_path.name}")

                # Remove from pending files dict
                self._pending_files.pop(file_path.name, None)

        except Exception as e:
            logger.error(f"Error queueing file: {e}")

    async def _add_to_print_queue(self, file_path: Path, source_ip: str) -> None:
        """Archive file and add to print queue (unassigned).

        Args:
            file_path: Path to the 3MF file
            source_ip: IP address of uploader
        """
        if not self._session_factory:
            logger.error("Cannot add to print queue: no database session factory configured")
            return

        # Only process 3MF files
        if file_path.suffix.lower() != ".3mf":
            logger.debug(f"Skipping non-3MF file: {file_path.name}")
            self._pending_files.pop(file_path.name, None)
            try:
                file_path.unlink()
            except Exception:
                pass
            return

        try:
            from backend.app.models.print_queue import PrintQueueItem
            from backend.app.services.archive import ArchiveService

            async with self._session_factory() as db:
                service = ArchiveService(db)

                # First, archive the print
                archive = await service.archive_print(
                    printer_id=None,  # No physical printer
                    source_file=file_path,
                    print_data={
                        "status": "archived",
                        "source": "virtual_printer",
                        "source_ip": source_ip,
                    },
                )

                if archive:
                    logger.info(f"Archived virtual printer upload: {archive.id} - {archive.print_name}")

                    # Now add to print queue (unassigned)
                    queue_item = PrintQueueItem(
                        printer_id=None,  # Unassigned - user will assign later
                        archive_id=archive.id,
                        position=1,  # Will be adjusted when assigned to a printer
                        status="pending",
                    )
                    db.add(queue_item)
                    await db.commit()

                    logger.info(f"Added to print queue (unassigned): queue_id={queue_item.id}, archive_id={archive.id}")

                    # Clean up uploaded file (it's now copied to archive)
                    try:
                        file_path.unlink()
                    except Exception:
                        pass

                    # Remove from pending
                    self._pending_files.pop(file_path.name, None)
                else:
                    logger.error(f"Failed to archive file: {file_path.name}")

        except Exception as e:
            logger.error(f"Error adding to print queue: {e}")

    def get_status(self) -> dict:
        """Get virtual printer status.

        Returns:
            Status dictionary with enabled, running, mode, etc.
        """
        status = {
            "enabled": self._enabled,
            "running": self.is_running,
            "mode": self._mode,
            "name": self.PRINTER_NAME,
            "serial": self.printer_serial,
            "model": self._model,
            "model_name": VIRTUAL_PRINTER_MODELS.get(self._model, self._model),
            "pending_files": len(self._pending_files),
        }

        # Add proxy-specific status
        if self._mode == "proxy":
            status["target_printer_ip"] = self._target_printer_ip
            if self._proxy:
                proxy_status = self._proxy.get_status()
                status["proxy"] = proxy_status

        return status


# Global instance
virtual_printer_manager = VirtualPrinterManager()
