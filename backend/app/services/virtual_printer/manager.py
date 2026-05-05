"""Virtual Printer Manager - coordinates SSDP, MQTT, and FTP services.

Each virtual printer runs its own independent services (FTP, MQTT, SSDP, Bind)
bound to its dedicated IP address, regardless of mode.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from backend.app.core.config import settings as app_settings
from backend.app.services.virtual_printer.bind_server import BindServer
from backend.app.services.virtual_printer.certificate import CertificateService
from backend.app.services.virtual_printer.ftp_server import VirtualPrinterFTPServer
from backend.app.services.virtual_printer.mqtt_bridge import MQTTBridge
from backend.app.services.virtual_printer.mqtt_server import SimpleMQTTServer
from backend.app.services.virtual_printer.ssdp_server import SSDPProxy, VirtualPrinterSSDPServer
from backend.app.services.virtual_printer.tcp_proxy import SlicerProxyManager, TCPProxy

if TYPE_CHECKING:
    from backend.app.services.printer_manager import PrinterManager

logger = logging.getLogger(__name__)


# Mapping of SSDP model codes to display names
# These are the codes that slicers expect during discovery
# Sources:
#   - https://gist.github.com/Alex-Schaefer/72a9e2491a42da2ef99fb87601955cc3
#   - https://github.com/psychoticbeef/BambuLabOrcaSlicerDiscovery
VIRTUAL_PRINTER_MODELS = {
    # X1 Series
    "BL-P001": "X1C",  # X1 Carbon
    "BL-P002": "X1",  # X1
    "C13": "X1E",  # X1E
    # X2 Series
    "N6": "X2D",  # X2D
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
    "O1C2": "H2C",  # H2C (dual nozzle variant)
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
    "BL-P001": "00M00A",  # X1C
    "BL-P002": "00M00A",  # X1
    "C13": "03W00A",  # X1E
    # X2 Series
    "N6": "20P90A",  # X2D (first 4 chars "20P9" match real serials)
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
    "O1C2": "09400A",  # H2C (dual nozzle variant)
    "O1S": "09400A",  # H2S
}

# Reverse mapping: display name → SSDP model code (for auto-inheriting from printer model)
DISPLAY_NAME_TO_MODEL_CODE = {v: k for k, v in VIRTUAL_PRINTER_MODELS.items()}

# Default model
DEFAULT_VIRTUAL_PRINTER_MODEL = "BL-P001"  # X1C


def _get_serial_for_model(model: str, serial_suffix: str) -> str:
    """Get serial number for the given model and suffix."""
    prefix = MODEL_SERIAL_PREFIXES.get(model, "00M09A")
    return f"{prefix}{serial_suffix}"


class VirtualPrinterInstance:
    """Per-printer state and file handling logic.

    Each instance represents one virtual printer with its own config,
    upload directory, certificates, and file handling mode.
    """

    def __init__(
        self,
        *,
        vp_id: int,
        name: str,
        mode: str,
        model: str,
        access_code: str,
        serial_suffix: str,
        target_printer_ip: str = "",
        target_printer_serial: str = "",
        target_printer_id: int | None = None,
        auto_dispatch: bool = True,
        queue_force_color_match: bool = False,
        bind_ip: str = "",
        remote_interface_ip: str = "",
        tailscale_disabled: bool = True,
        base_dir: Path,
        session_factory: Callable | None = None,
        printer_manager: "PrinterManager | None" = None,
    ):
        self.id = vp_id
        self.name = name
        self.mode = mode
        self.model = model
        self.access_code = access_code
        self.serial_suffix = serial_suffix
        self.target_printer_ip = target_printer_ip
        self.target_printer_serial = target_printer_serial
        self.target_printer_id = target_printer_id
        self.auto_dispatch = auto_dispatch
        self.queue_force_color_match = queue_force_color_match
        self.bind_ip = bind_ip
        self.remote_interface_ip = remote_interface_ip
        self.tailscale_disabled = tailscale_disabled
        self._session_factory = session_factory
        self._printer_manager = printer_manager

        # Directories
        self.upload_dir = base_dir / "uploads" / str(vp_id)
        self.cert_dir = base_dir / "certs" / str(vp_id)
        shared_ca_dir = base_dir / "certs"

        # Ensure directories exist
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        (self.upload_dir / "cache").mkdir(exist_ok=True)
        self.cert_dir.mkdir(parents=True, exist_ok=True)

        # Certificate service (shared CA, per-instance printer cert)
        self._cert_service = CertificateService(
            cert_dir=self.cert_dir,
            serial=self.serial,
            shared_ca_dir=shared_ca_dir,
        )

        # Pending files for MQTT correlation
        self._pending_files: dict[str, Path] = {}

        # Per-instance services
        self._proxy: SlicerProxyManager | None = None
        self._ftp: VirtualPrinterFTPServer | None = None
        self._mqtt: SimpleMQTTServer | None = None
        self._mqtt_bridge: MQTTBridge | None = None
        self._rtsp_proxy: TCPProxy | None = None
        self._bind: BindServer | None = None
        self._ssdp: VirtualPrinterSSDPServer | None = None
        self._ssdp_proxy: SSDPProxy | None = None
        self._tasks: list[asyncio.Task] = []

    @property
    def serial(self) -> str:
        """Full serial number for this virtual printer."""
        return _get_serial_for_model(self.model or DEFAULT_VIRTUAL_PRINTER_MODEL, self.serial_suffix)

    @property
    def cert_path(self) -> Path:
        return self._cert_service.cert_path

    @property
    def key_path(self) -> Path:
        return self._cert_service.key_path

    @property
    def is_proxy(self) -> bool:
        return self.mode == "proxy"

    @property
    def is_running(self) -> bool:
        return len(self._tasks) > 0 and all(not t.done() for t in self._tasks)

    def generate_certificates(self) -> tuple[Path, Path]:
        """Generate certificates for this instance."""
        self._cert_service.serial = self.serial if not self.is_proxy else (self.target_printer_serial or self.serial)
        additional_ips = [self.remote_interface_ip] if self.remote_interface_ip else None
        if self.bind_ip:
            additional_ips = additional_ips or []
            additional_ips.append(self.bind_ip)
        self._cert_service.delete_printer_certificate()
        return self._cert_service.generate_certificates(additional_ips=additional_ips)

    # -- File handling callbacks --

    async def on_file_received(self, file_path: Path, source_ip: str) -> None:
        """Handle file upload completion from FTP."""
        logger.info("[VP %s] Received file: %s from %s", self.name, file_path.name, source_ip)

        self._pending_files[file_path.name] = file_path

        if self.mode == "immediate":
            await self._archive_file(file_path, source_ip)
        elif self.mode == "print_queue":
            await self._add_to_print_queue(file_path, source_ip)
        else:
            await self._queue_file(file_path, source_ip)

        # Reset MQTT status back to IDLE
        if self._mqtt and file_path.suffix.lower() == ".3mf":
            self._mqtt.set_gcode_state("IDLE")

    async def on_print_command(self, filename: str, data: dict) -> None:
        """Handle print command from MQTT."""
        logger.info("[VP %s] Print command for: %s", self.name, filename)

    async def _archive_file(self, file_path: Path, source_ip: str) -> None:
        """Archive file immediately."""
        if not self._session_factory:
            logger.error("Cannot archive: no database session factory configured")
            return

        if file_path.suffix.lower() != ".3mf":
            logger.debug("Skipping non-3MF file: %s", file_path.name)
            self._pending_files.pop(file_path.name, None)
            try:
                file_path.unlink()
            except OSError:
                pass
            return

        try:
            from backend.app.api.routes.settings import get_setting
            from backend.app.services.archive import ArchiveService

            async with self._session_factory() as db:
                name_source = await get_setting(db, "virtual_printer_archive_name_source")
                prefer_filename = name_source == "filename"
                service = ArchiveService(db)
                archive = await service.archive_print(
                    printer_id=None,
                    source_file=file_path,
                    print_data={
                        "status": "archived",
                        "source": "virtual_printer",
                        "source_ip": source_ip,
                    },
                    prefer_filename_for_name=prefer_filename,
                )
                if archive:
                    logger.info("[VP %s] Archived: %s - %s", self.name, archive.id, archive.print_name)
                    try:
                        file_path.unlink()
                    except OSError:
                        pass
                    self._pending_files.pop(file_path.name, None)
                else:
                    logger.error("Failed to archive file: %s", file_path.name)
        except Exception as e:
            logger.error("Error archiving file: %s", e)

    async def _queue_file(self, file_path: Path, source_ip: str) -> None:
        """Queue file for user review."""
        if not self._session_factory:
            logger.error("Cannot queue: no database session factory configured")
            return

        if file_path.suffix.lower() != ".3mf":
            self._pending_files.pop(file_path.name, None)
            try:
                file_path.unlink()
            except OSError:
                pass
            return

        # Peek at the 3MF for the embedded title BEFORE we hand it off to the
        # DB. Storing it now means the /pending-uploads/ list doesn't have to
        # reopen every 3MF on every render to keep the review card and the
        # eventual archive name in sync (#1152 follow-up). Failure to parse is
        # not fatal — the response model falls back to the filename stem.
        metadata_print_name: str | None = None
        try:
            from backend.app.services.archive import ThreeMFParser

            parsed = ThreeMFParser(file_path).parse()
            raw_name = parsed.get("print_name")
            if isinstance(raw_name, str) and raw_name.strip():
                metadata_print_name = raw_name.strip()[:255]
        except Exception as e:
            logger.debug("[VP %s] Metadata title peek failed for %s: %s", self.name, file_path.name, e)

        try:
            from backend.app.models.pending_upload import PendingUpload

            async with self._session_factory() as db:
                pending = PendingUpload(
                    filename=file_path.name,
                    file_path=str(file_path),
                    file_size=file_path.stat().st_size,
                    source_ip=source_ip,
                    status="pending",
                    uploaded_at=datetime.now(timezone.utc),
                    metadata_print_name=metadata_print_name,
                )
                db.add(pending)
                await db.commit()
                logger.info("[VP %s] Queued: %s - %s", self.name, pending.id, file_path.name)
                self._pending_files.pop(file_path.name, None)
        except Exception as e:
            logger.error("Error queueing file: %s", e)

    async def _add_to_print_queue(self, file_path: Path, source_ip: str) -> None:
        """Archive file and add to print queue, assigned to target printer or model."""
        if not self._session_factory:
            logger.error("Cannot add to print queue: no database session factory configured")
            return

        if file_path.suffix.lower() != ".3mf":
            self._pending_files.pop(file_path.name, None)
            try:
                file_path.unlink()
            except OSError:
                pass
            return

        try:
            import json

            from backend.app.api.routes.settings import get_setting
            from backend.app.models.print_queue import PrintQueueItem
            from backend.app.services.archive import ArchiveService
            from backend.app.services.filament_requirements import extract_filament_requirements

            async with self._session_factory() as db:
                name_source = await get_setting(db, "virtual_printer_archive_name_source")
                prefer_filename = name_source == "filename"
                service = ArchiveService(db)
                archive = await service.archive_print(
                    printer_id=None,
                    source_file=file_path,
                    print_data={
                        "status": "archived",
                        "source": "virtual_printer",
                        "source_ip": source_ip,
                    },
                    prefer_filename_for_name=prefer_filename,
                )
                if archive:
                    logger.info("[VP %s] Archived: %s - %s", self.name, archive.id, archive.print_name)
                    # Assign to specific printer if configured, otherwise use model for "Any X" scheduling
                    target_model = None
                    if not self.target_printer_id and self.model:
                        target_model = VIRTUAL_PRINTER_MODELS.get(self.model)
                    plate_id = self._extract_plate_id(file_path)

                    # Parse the 3MF for per-slot filament requirements (#1188).
                    # The manual /print-queue/ POST flow does this at queue-add
                    # time; the VP path used to skip it, so the scheduler fell
                    # through to model-only matching and dispatched onto whatever
                    # printer happened to be free regardless of loaded colour.
                    # required_filament_types is populated unconditionally — it's
                    # cheap, lets the scheduler reject obvious mis-matches even
                    # without force_color_match. filament_overrides only carries
                    # force_color_match=True when the per-VP setting is on, so
                    # upgraders keep the old behaviour by default.
                    required_filament_types_json: str | None = None
                    filament_overrides_json: str | None = None
                    requirements = extract_filament_requirements(file_path, plate_id)
                    if requirements:
                        types = sorted({r["type"] for r in requirements if r.get("type")})
                        if types:
                            required_filament_types_json = json.dumps(types)
                        if self.queue_force_color_match:
                            overrides = [
                                {
                                    "slot_id": r["slot_id"],
                                    "type": r.get("type", ""),
                                    "color": r.get("color", ""),
                                    "force_color_match": True,
                                }
                                for r in requirements
                                if r.get("type") and r.get("color")
                            ]
                            if overrides:
                                filament_overrides_json = json.dumps(overrides)

                    queue_item = PrintQueueItem(
                        printer_id=self.target_printer_id,
                        target_model=target_model,
                        archive_id=archive.id,
                        plate_id=plate_id,
                        position=1,
                        status="pending",
                        manual_start=not self.auto_dispatch,
                        required_filament_types=required_filament_types_json,
                        filament_overrides=filament_overrides_json,
                    )
                    db.add(queue_item)
                    await db.commit()
                    logger.info("[VP %s] Added to queue: %s", self.name, queue_item.id)
                    try:
                        file_path.unlink()
                    except OSError:
                        pass
                    self._pending_files.pop(file_path.name, None)
                else:
                    logger.error("Failed to archive file: %s", file_path.name)
        except Exception as e:
            logger.error("Error adding to print queue: %s", e)

    @staticmethod
    def _extract_plate_id(file_path: Path) -> int | None:
        """Extract plate index from 3MF slice_info.config."""
        try:
            import xml.etree.ElementTree as ET
            import zipfile

            with zipfile.ZipFile(file_path, "r") as zf:
                if "Metadata/slice_info.config" in zf.namelist():
                    content = zf.read("Metadata/slice_info.config").decode()
                    root = ET.fromstring(content)  # noqa: S314  # nosec B314
                    plate = root.find(".//plate")
                    if plate is not None:
                        for meta in plate.findall("metadata"):
                            if meta.get("key") == "index" and meta.get("value"):
                                return int(meta.get("value"))
        except Exception:
            return None
        return None

    # -- Service lifecycle --

    def _resolve_cert_and_advertise(self) -> tuple[Path, Path, str]:
        """Return (cert_path, key_path, advertise_address) for TLS services.

        Always uses the self-signed cert chain (signed by `bbl_ca`). The user
        imports `bbl_ca.crt` once into the slicer; per-VP certs validate from
        there. Tailscale exposure is handled by the user picking the Tailscale
        IP in the bind_ip dropdown.
        """
        cert_path, key_path = self.generate_certificates()
        advertise = self.remote_interface_ip or self.bind_ip or ""
        return cert_path, key_path, advertise

    async def start_server(self) -> None:
        """Start server-mode services (FTP, MQTT, SSDP, Bind) on this VP's bind_ip."""
        logger.info("[VP %s] Starting server-mode services on %s", self.name, self.bind_ip)

        cert_path, key_path, advertise_addr = self._resolve_cert_and_advertise()
        bind_addr = self.bind_ip or "0.0.0.0"  # nosec B104

        async def run_with_logging(coro, svc_name):
            try:
                await coro
            except Exception as e:
                logger.error("[VP %s] %s failed: %s", self.name, svc_name, e)

        self._tasks = []

        # FTP server
        self._ftp = VirtualPrinterFTPServer(
            upload_dir=self.upload_dir,
            access_code=self.access_code,
            cert_path=cert_path,
            key_path=key_path,
            on_file_received=self.on_file_received,
            bind_address=bind_addr,
            vp_name=self.name,
        )
        self._tasks.append(
            asyncio.create_task(
                run_with_logging(self._ftp.start(), "FTP"),
                name=f"vp_{self.id}_ftp",
            )
        )

        # MQTT server
        self._mqtt = SimpleMQTTServer(
            serial=self.serial,
            access_code=self.access_code,
            cert_path=cert_path,
            key_path=key_path,
            on_print_command=self.on_print_command,
            model=self.model or DEFAULT_VIRTUAL_PRINTER_MODEL,
            bind_address=bind_addr,
            vp_name=self.name,
        )
        self._tasks.append(
            asyncio.create_task(
                run_with_logging(self._mqtt.start(), "MQTT"),
                name=f"vp_{self.id}_mqtt",
            )
        )

        # MQTT bridge — fans out the target printer's pushes to slicers connected
        # to this VP and forwards their commands back to the printer. Only meaningful
        # when a target printer is configured AND printer_manager was injected (it
        # always is at runtime; tests may omit it).
        if self.target_printer_id is not None and self._printer_manager is not None:
            self._mqtt_bridge = MQTTBridge(
                vp_id=self.id,
                vp_name=self.name,
                vp_serial=self.serial,
                target_printer_id=self.target_printer_id,
                mqtt_server=self._mqtt,
                printer_manager=self._printer_manager,
            )
            self._mqtt.set_bridge(self._mqtt_bridge)
            await self._mqtt_bridge.start()

            # RTSPS camera passthrough on port 322. BambuStudio's camera button
            # connects to the device IP it bound on (the VP), not the IP in
            # `ipcam.rtsp_url`. Without a listener on <bind_ip>:322 the slicer
            # gets connection refused → "LAN connection failed". Same raw TCP
            # pass-through used by SlicerProxyManager in proxy mode.
            target_client = self._printer_manager.get_client(self.target_printer_id)
            target_ip = getattr(target_client, "ip_address", None) if target_client else None
            if target_ip:
                self._rtsp_proxy = TCPProxy(
                    name="RTSP",
                    listen_port=322,
                    target_host=target_ip,
                    target_port=322,
                    bind_address=bind_addr,
                )
                self._tasks.append(
                    asyncio.create_task(
                        run_with_logging(self._rtsp_proxy.start(), "RTSP"),
                        name=f"vp_{self.id}_rtsp",
                    )
                )

        # Bind server
        self._bind = BindServer(
            serial=self.serial,
            model=self.model or DEFAULT_VIRTUAL_PRINTER_MODEL,
            name=self.name,
            bind_address=bind_addr,
            cert_path=cert_path,
            key_path=key_path,
        )
        self._tasks.append(
            asyncio.create_task(
                run_with_logging(self._bind.start(), "Bind"),
                name=f"vp_{self.id}_bind",
            )
        )

        # SSDP server — advertise_addr is the Tailscale FQDN when available,
        # otherwise the bind/remote IP (existing behaviour)
        self._ssdp = VirtualPrinterSSDPServer(
            name=self.name,
            serial=self.serial,
            model=self.model or DEFAULT_VIRTUAL_PRINTER_MODEL,
            advertise_ip=advertise_addr,
            bind_ip=bind_addr,
        )
        self._tasks.append(
            asyncio.create_task(
                run_with_logging(self._ssdp.start(), "SSDP"),
                name=f"vp_{self.id}_ssdp",
            )
        )

        logger.info("[VP %s] Server-mode services started on %s", self.name, bind_addr)

    async def stop_server(self) -> None:
        """Stop server-mode services."""
        if self._mqtt_bridge:
            try:
                await self._mqtt_bridge.stop()
            except Exception:
                logger.exception("[VP %s] MQTT bridge stop failed", self.name)
            if self._mqtt:
                self._mqtt.set_bridge(None)
            self._mqtt_bridge = None
        if self._rtsp_proxy:
            try:
                await self._rtsp_proxy.stop()
            except Exception:
                logger.exception("[VP %s] RTSP proxy stop failed", self.name)
            self._rtsp_proxy = None
        if self._ftp:
            await self._ftp.stop()
            self._ftp = None
        if self._mqtt:
            await self._mqtt.stop()
            self._mqtt = None
        if self._bind:
            await self._bind.stop()
            self._bind = None
        if self._ssdp:
            await self._ssdp.stop()
            self._ssdp = None
        await self._cancel_tasks()

    async def start_proxy(self) -> None:
        """Start proxy mode services for this instance."""
        logger.info("[VP %s] Starting proxy mode to %s", self.name, self.target_printer_ip)

        cert_path, key_path, _ = self._resolve_cert_and_advertise()

        self._proxy = SlicerProxyManager(
            target_host=self.target_printer_ip,
            cert_path=cert_path,
            key_path=key_path,
            on_activity=lambda n, m: logger.info("[VP %s] Proxy %s: %s", self.name, n, m),
            bind_address=self.bind_ip or "0.0.0.0",  # nosec B104
            bind_identity={
                "serial": self.target_printer_serial or self.serial,
                "model": self.model or DEFAULT_VIRTUAL_PRINTER_MODEL,
                "name": self.name,
                "version": "01.00.00.00",
            },
        )

        async def run_with_logging(coro, svc_name):
            try:
                await coro
            except Exception as e:
                logger.error("[VP %s] %s failed: %s", self.name, svc_name, e)

        self._tasks = []

        # SSDP for proxy
        proxy_serial = self.target_printer_serial or self.serial
        if self.remote_interface_ip:
            from backend.app.services.network_utils import find_interface_for_ip

            local_iface = find_interface_for_ip(self.target_printer_ip)
            if local_iface:
                self._ssdp_proxy = SSDPProxy(
                    local_interface_ip=local_iface["ip"],
                    remote_interface_ip=self.remote_interface_ip,
                    target_printer_ip=self.target_printer_ip,
                    name=self.name,
                )
                self._tasks.append(
                    asyncio.create_task(
                        run_with_logging(self._ssdp_proxy.start(), "SSDP Proxy"),
                        name=f"vp_{self.id}_ssdp_proxy",
                    )
                )
            else:
                self._start_fallback_ssdp(proxy_serial, run_with_logging)
        else:
            self._start_fallback_ssdp(proxy_serial, run_with_logging)

        self._tasks.append(
            asyncio.create_task(
                run_with_logging(self._proxy.start(), "Proxy"),
                name=f"vp_{self.id}_proxy",
            )
        )

    def _start_fallback_ssdp(self, proxy_serial: str, run_with_logging) -> None:
        """Start single-interface SSDP server as fallback for proxy mode."""
        self._ssdp = VirtualPrinterSSDPServer(
            name=f"{self.name} (Proxy)",
            serial=proxy_serial,
            model=self.model or DEFAULT_VIRTUAL_PRINTER_MODEL,
            advertise_ip=self.bind_ip or "",
            bind_ip=self.bind_ip or "",
        )
        self._tasks.append(
            asyncio.create_task(
                run_with_logging(self._ssdp.start(), "SSDP"),
                name=f"vp_{self.id}_ssdp",
            )
        )

    async def stop_proxy(self) -> None:
        """Stop proxy mode services for this instance."""
        if self._proxy:
            await self._proxy.stop()
            self._proxy = None
        if self._ssdp:
            await self._ssdp.stop()
            self._ssdp = None
        if self._ssdp_proxy:
            await self._ssdp_proxy.stop()
            self._ssdp_proxy = None
        await self._cancel_tasks()

    async def _cancel_tasks(self) -> None:
        """Cancel all running tasks and wait for cleanup."""
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*self._tasks, return_exceptions=True), timeout=1.0)
            except TimeoutError:
                pass
        self._tasks = []

    def get_status(self) -> dict:
        """Get status for this instance."""
        status: dict = {
            "running": self.is_running,
            "pending_files": len(self._pending_files),
        }
        if self.is_proxy and self._proxy:
            status["proxy"] = self._proxy.get_status()
        return status


class VirtualPrinterManager:
    """Multi-instance virtual printer registry and orchestrator.

    Every VP runs its own independent services on a dedicated bind IP.
    """

    def __init__(self):
        self._session_factory: Callable | None = None
        self._printer_manager: PrinterManager | None = None
        self._instances: dict[int, VirtualPrinterInstance] = {}

        # Directories
        self._base_dir = app_settings.base_dir / "virtual_printer"

        # Ensure base directories exist
        self._ensure_base_directories()

    def _ensure_base_directories(self) -> None:
        """Create base directories at startup."""
        for dir_path in [self._base_dir, self._base_dir / "uploads", self._base_dir / "certs"]:
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
            except PermissionError:
                logger.error(
                    f"Cannot create directory {dir_path}: Permission denied. "
                    f"For Docker: ensure the data volume is writable by the container user. "
                    f"For bare metal: run 'sudo chown -R $(whoami) {self._base_dir}'"
                )

    def set_session_factory(self, session_factory: Callable) -> None:
        """Set the database session factory."""
        self._session_factory = session_factory

    def set_printer_manager(self, printer_manager: "PrinterManager") -> None:
        """Inject the global printer_manager so non-proxy VPs can mirror their target's MQTT stream."""
        self._printer_manager = printer_manager

    @property
    def is_enabled(self) -> bool:
        """Check if any virtual printer is running."""
        return len(self._instances) > 0

    async def sync_from_db(self) -> None:
        """Load all VPs from DB, reconcile running state."""
        if not self._session_factory:
            logger.warning("Cannot sync virtual printers: no session factory")
            return

        from sqlalchemy import select

        from backend.app.models.printer import Printer
        from backend.app.models.virtual_printer import VirtualPrinter

        async with self._session_factory() as db:
            result = await db.execute(
                select(VirtualPrinter).where(VirtualPrinter.enabled == True).order_by(VirtualPrinter.position)  # noqa: E712
            )
            enabled_vps = result.scalars().all()

        # Stop instances that are no longer enabled or changed mode
        enabled_ids = {vp.id for vp in enabled_vps}
        for vp_id in list(self._instances.keys()):
            if vp_id not in enabled_ids:
                await self.remove_instance(vp_id)

        # Look up printer IPs for proxy VPs
        proxy_vps = [vp for vp in enabled_vps if vp.mode == "proxy"]
        proxy_ips: dict[int, tuple[str, str]] = {}
        if proxy_vps:
            async with self._session_factory() as db:
                for pvp in proxy_vps:
                    if pvp.target_printer_id:
                        result = await db.execute(select(Printer).where(Printer.id == pvp.target_printer_id))
                        printer = result.scalar_one_or_none()
                        if printer:
                            proxy_ips[pvp.id] = (printer.ip_address, printer.serial_number)

        # Detect config changes on running instances and restart if needed
        for vp in enabled_vps:
            instance = self._instances.get(vp.id)
            if not instance:
                continue

            changed = (
                instance.mode != vp.mode
                or instance.model != (vp.model or DEFAULT_VIRTUAL_PRINTER_MODEL)
                or instance.access_code != (vp.access_code or "")
                or instance.bind_ip != (vp.bind_ip or "")
                or instance.remote_interface_ip != (vp.remote_interface_ip or "")
                or instance.target_printer_id != vp.target_printer_id
                or instance.auto_dispatch != vp.auto_dispatch
            )

            if changed:
                logger.info(
                    "VP %s config changed (mode: %s→%s), restarting",
                    instance.name,
                    instance.mode,
                    vp.mode,
                )
                await self.remove_instance(vp.id)

        # Start instances for all enabled VPs (skip already running)
        for vp in enabled_vps:
            if vp.id in self._instances:
                continue

            if vp.mode == "proxy":
                ip_info = proxy_ips.get(vp.id)
                if not ip_info:
                    logger.warning("Proxy VP %s: target printer not found, skipping", vp.name)
                    continue
                target_ip, target_serial = ip_info
                instance = VirtualPrinterInstance(
                    vp_id=vp.id,
                    name=vp.name,
                    mode=vp.mode,
                    model=vp.model or DEFAULT_VIRTUAL_PRINTER_MODEL,
                    access_code=vp.access_code or "",
                    serial_suffix=vp.serial_suffix,
                    target_printer_ip=target_ip,
                    target_printer_serial=target_serial,
                    auto_dispatch=vp.auto_dispatch,
                    bind_ip=vp.bind_ip or "",
                    remote_interface_ip=vp.remote_interface_ip or "",
                    tailscale_disabled=vp.tailscale_disabled,
                    base_dir=self._base_dir,
                    session_factory=self._session_factory,
                )
                self._instances[vp.id] = instance
                await instance.start_proxy()
                logger.info("Started proxy VP: %s → %s (bind=%s)", instance.name, target_ip, instance.bind_ip)
            else:
                instance = VirtualPrinterInstance(
                    vp_id=vp.id,
                    name=vp.name,
                    mode=vp.mode,
                    model=vp.model or DEFAULT_VIRTUAL_PRINTER_MODEL,
                    access_code=vp.access_code or "",
                    serial_suffix=vp.serial_suffix,
                    target_printer_id=vp.target_printer_id,
                    auto_dispatch=vp.auto_dispatch,
                    queue_force_color_match=vp.queue_force_color_match,
                    bind_ip=vp.bind_ip or "",
                    remote_interface_ip=vp.remote_interface_ip or "",
                    tailscale_disabled=vp.tailscale_disabled,
                    base_dir=self._base_dir,
                    session_factory=self._session_factory,
                    printer_manager=self._printer_manager,
                )
                self._instances[vp.id] = instance
                await instance.start_server()
                logger.info("Started server-mode VP: %s on %s", instance.name, vp.bind_ip)

    async def remove_instance(self, vp_id: int) -> None:
        """Stop and remove a single VP instance."""
        instance = self._instances.pop(vp_id, None)
        if instance:
            if instance.is_proxy:
                await instance.stop_proxy()
            else:
                await instance.stop_server()
            logger.info("Removed VP instance: %s", instance.name)

    async def stop_all(self) -> None:
        """Shutdown all virtual printer services."""
        logger.info("Stopping all virtual printer services...")

        for vp_id in list(self._instances.keys()):
            await self.remove_instance(vp_id)

        logger.info("All virtual printer services stopped")

    def get_instance(self, vp_id: int) -> VirtualPrinterInstance | None:
        """Get a running instance by ID."""
        return self._instances.get(vp_id)

    def get_all_status(self) -> list[dict]:
        """Get status for all running instances."""
        return [
            {
                "id": inst.id,
                "name": inst.name,
                "mode": inst.mode,
                **inst.get_status(),
            }
            for inst in self._instances.values()
        ]

    # -- Legacy single-printer compat --

    def get_status(self) -> dict:
        """Get status for first virtual printer (backward compat)."""
        if self._instances:
            first = next(iter(self._instances.values()))
            return {
                "enabled": True,
                "running": first.is_running,
                "mode": first.mode,
                "name": first.name,
                "serial": first.serial,
                "model": first.model or DEFAULT_VIRTUAL_PRINTER_MODEL,
                "model_name": VIRTUAL_PRINTER_MODELS.get(
                    first.model or DEFAULT_VIRTUAL_PRINTER_MODEL,
                    first.model or DEFAULT_VIRTUAL_PRINTER_MODEL,
                ),
                "pending_files": first.get_status().get("pending_files", 0),
                **({"target_printer_ip": first.target_printer_ip} if first.is_proxy else {}),
                **({"proxy": first.get_status().get("proxy", {})} if first.is_proxy else {}),
            }
        return {
            "enabled": False,
            "running": False,
            "mode": "immediate",
            "name": "Bambuddy",
            "serial": "",
            "model": DEFAULT_VIRTUAL_PRINTER_MODEL,
            "model_name": VIRTUAL_PRINTER_MODELS[DEFAULT_VIRTUAL_PRINTER_MODEL],
            "pending_files": 0,
        }

    async def configure(
        self,
        enabled: bool,
        access_code: str = "",
        mode: str = "immediate",
        model: str = "",
        target_printer_ip: str = "",
        target_printer_serial: str = "",
        remote_interface_ip: str = "",
    ) -> None:
        """Legacy single-printer configure. Delegates to sync_from_db()."""
        # This method is kept for backward compat with the settings endpoint.
        # The actual work is done by sync_from_db() which reads from the DB.
        await self.sync_from_db()


# Global instance
virtual_printer_manager = VirtualPrinterManager()
