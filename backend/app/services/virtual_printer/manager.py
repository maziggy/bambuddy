"""Virtual Printer Manager - coordinates SSDP, MQTT, and FTP services.

Each virtual printer runs its own independent services (FTP, MQTT, SSDP, Bind)
bound to its dedicated IP address, regardless of mode.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from backend.app.core.config import settings as app_settings
from backend.app.models.virtual_printer import (
    VP_MODE_ARCHIVE,
    VP_MODE_PROXY,
    VP_MODE_QUEUE,
    normalize_vp_mode,
)
from backend.app.services.virtual_printer.bind_server import BindServer
from backend.app.services.virtual_printer.certificate import CertificateService
from backend.app.services.virtual_printer.ftp_server import VirtualPrinterFTPServer, compute_passive_port_slice
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
    # A2 Series (single-FDM + integrated cutter/plotter)
    "N9": "A2L",  # A2L
    # P Series
    "C11": "P1P",  # P1P
    "C12": "P1S",  # P1S
    "N7": "P2S",  # P2S
    # A1 Series
    "N2S": "A1",  # A1
    "N1": "A1 Mini",  # A1 Mini
    # H2 Series
    "O1D": "H2D",  # H2D
    "O1E": "H2D Pro",  # H2D Pro
    "O2D": "H2D Pro",  # H2D Pro
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
    # A2 Series
    "N9": "26A19A",  # A2L (first 5 chars "26A19" match real serials)
    # P Series
    "C11": "01S00A",  # P1P
    "C12": "01P00A",  # P1S
    "N7": "22E00A",  # P2S
    # A1 Series
    "N2S": "03900A",  # A1
    "N1": "03000A",  # A1 Mini
    # H2 Series
    "O1D": "09400A",  # H2D
    "O1E": "09400A",  # H2D Pro (same prefix family as H2D)
    "O2D": "09400A",  # H2D Pro
    "O1C": "09400A",  # H2C
    "O1C2": "09400A",  # H2C (dual nozzle variant)
    "O1S": "09400A",  # H2S
}

# Reverse mapping: display name → SSDP model code (for auto-inheriting from printer model)
DISPLAY_NAME_TO_MODEL_CODE = {v: k for k, v in VIRTUAL_PRINTER_MODELS.items()}

# Default model
DEFAULT_VIRTUAL_PRINTER_MODEL = "BL-P001"  # X1C

# Bound on per-instance ``_slicer_print_options`` cache size. The slicer's
# project_file MQTT command stashes one dict per filename; the
# corresponding ``_add_to_print_queue`` pop only fires when the file
# upload completes. Failed / cancelled / non-3MF uploads orphan their
# stash. The bound triggers FIFO eviction in ``on_print_command`` once
# the dict fills, so a long-running VP can't leak unbounded state.
_SLICER_OPTIONS_CACHE_LIMIT = 128

# How long ``_add_to_print_queue`` waits for the slicer's MQTT
# ``project_file`` after the FTP upload completes (#1780 round 3).
# Bambu Studio sends FTP first, then MQTT immediately after — but on
# wireless / loaded setups the MQTT command can land 2+ s after FTP,
# which used to time the wait out and silently drop ``nozzle_mapping``
# + the other slicer-driven flags. The bumped window covers the
# observed worst case in the field; the late-MQTT fallback in
# ``on_print_command`` covers the rest.
_SLICER_OPTIONS_WAIT_TIMEOUT = 5.0

# How long ``on_print_command`` will retroactively stamp slicer fields
# onto a recently-committed queue item when the MQTT print command
# arrives after ``_SLICER_OPTIONS_WAIT_TIMEOUT`` expired. Covers
# extra-late MQTT (slow wireless slicer, NIC drop+retry) and the
# scheduler tick interval before dispatch picks the item up.
_RECENT_QUEUE_ITEM_TTL = 30.0


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
        gcode_injection: bool = False,
        bind_ip: str = "",
        remote_interface_ip: str = "",
        tailscale_disabled: bool = True,
        base_dir: Path,
        session_factory: Callable | None = None,
        printer_manager: "PrinterManager | None" = None,
    ):
        self.id = vp_id
        self.name = name
        # Normalize on construction so the rest of the code only compares
        # canonical values, even when a legacy DB row hasn't been migrated
        # yet (e.g. fresh-from-disk during the boot window before the
        # one-shot migration in `core/database.py` has executed).
        self.mode = normalize_vp_mode(mode) or VP_MODE_ARCHIVE
        self.model = model
        self.access_code = access_code
        self.serial_suffix = serial_suffix
        self.target_printer_ip = target_printer_ip
        self.target_printer_serial = target_printer_serial
        self.target_printer_id = target_printer_id
        self.auto_dispatch = auto_dispatch
        self.queue_force_color_match = queue_force_color_match
        self.gcode_injection = gcode_injection
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

        # Slicer-side print options captured from the MQTT `project_file`
        # command, keyed by filename. Used by `_add_to_print_queue` so the
        # queue item inherits the user's slicer-chosen timelapse / bed_leveling
        # / flow_cali / vibration_cali / layer_inspect / use_ams toggles rather
        # than falling back to the global `default_*` settings (#1403). FTP
        # completes a few hundred ms before the slicer's MQTT `project_file`
        # arrives, so the queue-add path waits briefly on the event below
        # before reading the dict. Events are popped along with the options
        # so the dict stays bounded.
        self._slicer_print_options: dict[str, dict] = {}
        self._slicer_print_options_events: dict[str, asyncio.Event] = {}

        # Queue items recently committed by `_add_to_print_queue`, keyed by
        # FTP filename. Used by `on_print_command` to retroactively stamp the
        # slicer's nozzle_mapping (and the other slicer-driven flags) onto a
        # queue item when the MQTT `project_file` arrives after the queue-add
        # wait timed out — the #1780 round-3 race. Value is
        # (queue_item_ids, monotonic_committed_at); entries older than
        # `_RECENT_QUEUE_ITEM_TTL` are evicted opportunistically on each
        # queue-add.
        self._recent_queue_items: dict[str, tuple[list[int], float]] = {}

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

        # Pending timer that re-fires gcode_state=FINISH after a project_file
        # ack. See ``_schedule_finish_release`` for the #1658 rationale.
        self._finish_release_task: asyncio.Task | None = None

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

        # Accept both canonical (`archive`/`queue`) and legacy
        # (`immediate`/`print_queue`) wire values so a stale row that hasn't
        # been migrated yet still dispatches correctly. Migration in
        # `core/database.py` rewrites existing rows once at boot.
        mode = normalize_vp_mode(self.mode)
        if mode == VP_MODE_ARCHIVE:
            await self._archive_file(file_path, source_ip)
        elif mode == VP_MODE_QUEUE:
            await self._add_to_print_queue(file_path, source_ip)
        else:
            await self._queue_file(file_path, source_ip)

        # Signal job completion to the slicer. Send-flow slicers don't watch the
        # post-upload state and would be happy with anything; the Print flow
        # (intended for proxy-mode VPs, but users sometimes click it against
        # queue/immediate/review modes too — #1280) watches the gcode_state
        # cycle and only releases its in-flight-job lock when it sees FINISH.
        # Going PREPARE → IDLE wedges the slicer's UI at "Downloading...(0%)"
        # and blocks the next dispatch with "busy with another print job".
        # PREPARE → FINISH satisfies both flows. prepare_percent=100 also
        # unfreezes the slicer's "Downloading X%" progress bar which it ticks
        # against the same field during the upload window.
        if self._mqtt and file_path.suffix.lower() == ".3mf":
            self._mqtt.set_gcode_state("FINISH", filename=file_path.name, prepare_percent="100")
            # FINISH is the terminal state for the upload cycle per #1280
            # (commit 0d6171dc). The Print-flow slicer's in-flight-job lock
            # releases on FINISH; resetting to IDLE 2 s later would re-confuse
            # the slicer that just unwedged. Earlier audit suggesting the
            # IDLE reset was wrong — staying at FINISH is the designed
            # behaviour. The next upload's PREPARE→FINISH cycle starts fresh.

    async def on_print_command(self, filename: str, data: dict) -> None:
        """Handle print command from MQTT.

        Captures the slicer's project_file options (`timelapse`, `bed_leveling`,
        `flow_cali`, `vibration_cali`, `layer_inspect`, `use_ams`, plus the
        H2C rack-pick `nozzle_mapping`) so the VP-queue path can inherit them
        when adding the item to the queue, rather than falling back to the
        global default settings (#1403, #1780).
        Only queue mode consumes the capture; archive / review / proxy
        modes ignore the print command, so we skip the stash there to keep
        the dict from accumulating one entry per print over the VP's
        uptime.

        Also schedules the #1658 follow-up that re-fires gcode_state=FINISH a
        moment after the synthetic project_file ack — for every non-proxy
        mode — so the slicer's "Downloading" UI releases on the slicer's
        FTP-first-then-MQTT send order.

        ``filename`` is the slicer's ``subtask_name`` (bare model name, no
        extension) — used verbatim for `_schedule_finish_release` because
        push_status echoes it back to the slicer as gcode_file / subtask_name.
        The queue-side stash key is derived from ``data["file"]`` (the FTP
        filename with extension) so `_add_to_print_queue`'s
        ``file_path.name`` lookup matches; falls back to ``filename`` when
        ``data["file"]`` is absent (legacy slicers / non-3MF uploads).
        Stash/lookup mismatch was the #1780 root cause — every captured field
        silently fell back to settings defaults on every Bambu Studio "Send".
        """
        logger.info("[VP %s] Print command for: %s", self.name, filename)
        mode = normalize_vp_mode(self.mode)
        if mode != VP_MODE_PROXY and filename and self._mqtt is not None:
            self._schedule_finish_release(filename)
        if mode != VP_MODE_QUEUE:
            return
        # Stash key must match `_add_to_print_queue`'s lookup, which uses
        # `file_path.name` (FTP filename WITH extension). The slicer's
        # `subtask_name` (== this method's `filename` arg) is the bare model
        # name, no extension — using it as the stash key was the #1780 root
        # cause.
        stash_key = data.get("file") or filename
        # Drop the oldest stash if the cache is growing — happens when the
        # slicer sends project_file for a filename whose FTP upload was
        # rejected / cancelled / non-3MF, so _add_to_print_queue's pop
        # never fires. With no bound, a long-running VP accumulates one
        # dict per such mismatch.
        if len(self._slicer_print_options) >= _SLICER_OPTIONS_CACHE_LIMIT:
            try:
                stale_key = next(iter(self._slicer_print_options))
                self._slicer_print_options.pop(stale_key, None)
                self._slicer_print_options_events.pop(stale_key, None)
                logger.debug("[VP %s] Evicted stale slicer options for %s", self.name, stale_key)
            except StopIteration:
                pass
        self._slicer_print_options[stash_key] = dict(data)
        event = self._slicer_print_options_events.get(stash_key)
        if event:
            event.set()
            return
        # No consumer waiting: `_add_to_print_queue` either already gave up
        # (wait_for timed out) or hasn't started yet (FTP still uploading).
        # If a queue item was committed within the last
        # `_RECENT_QUEUE_ITEM_TTL`, the wait timed out and the row holds
        # settings defaults instead of the slicer's pick — retroactively
        # stamp the slicer-driven fields so the dispatcher honours the
        # user's choice. Covers the #1780 round-3 race where Bambu Studio's
        # MQTT lands just past the bumped wait ceiling.
        await self._restamp_recent_queue_item(stash_key, data)

    async def _restamp_recent_queue_item(self, stash_key: str, data: dict) -> None:
        """Patch slicer-driven fields onto a queue item the MQTT command missed.

        ``_add_to_print_queue`` waits up to ``_SLICER_OPTIONS_WAIT_TIMEOUT``
        for the slicer's MQTT ``project_file`` before committing the queue
        item. If the MQTT command arrives after that window — observed in
        the field at ~2.1 s on H2C / wireless setups (#1780 round 3) — the
        row was already written with settings defaults. This method runs
        on the late MQTT path: it looks up the most recent queue items
        committed for this filename and patches in the slicer's
        ``nozzle_mapping`` + workflow flags, but only while the items are
        still ``pending`` (scheduler hasn't dispatched them yet).
        """
        if not self._session_factory:
            return
        entry = self._recent_queue_items.get(stash_key)
        if entry is None:
            return
        queue_item_ids, committed_at = entry
        if time.monotonic() - committed_at > _RECENT_QUEUE_ITEM_TTL:
            self._recent_queue_items.pop(stash_key, None)
            return

        import json

        # Mirror the field set `_add_to_print_queue` reads off slicer_opts.
        # MQTT uses `bed_leveling` (single L); the column is `bed_levelling`.
        # `nozzles_info` is intentionally not stamped — column kept for
        # legacy rows but never written; see PrintQueueItem.nozzles_info.
        patch: dict = {}
        for mqtt_field, column in (
            ("bed_leveling", "bed_levelling"),
            ("flow_cali", "flow_cali"),
            ("vibration_cali", "vibration_cali"),
            ("layer_inspect", "layer_inspect"),
            ("timelapse", "timelapse"),
            ("use_ams", "use_ams"),
        ):
            if mqtt_field in data:
                patch[column] = bool(data[mqtt_field])

        raw = data.get("nozzle_mapping")
        if raw is not None:
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning(
                        "[VP %s] Late MQTT nozzle_mapping is unparseable JSON, dropping: %r",
                        self.name,
                        raw,
                    )
                    raw = None
            if raw is not None:
                patch["nozzle_mapping"] = json.dumps(raw)

        if not patch:
            self._recent_queue_items.pop(stash_key, None)
            return

        from sqlalchemy import select, update

        from backend.app.models.print_queue import PrintQueueItem

        try:
            async with self._session_factory() as db:
                # Only stamp items still pending; once the scheduler has
                # picked the row up we can't safely race the dispatcher.
                result = await db.execute(
                    select(PrintQueueItem.id).where(
                        PrintQueueItem.id.in_(queue_item_ids),
                        PrintQueueItem.status == "pending",
                    )
                )
                eligible_ids = [row[0] for row in result.all()]
                if not eligible_ids:
                    self._recent_queue_items.pop(stash_key, None)
                    return
                await db.execute(update(PrintQueueItem).where(PrintQueueItem.id.in_(eligible_ids)).values(**patch))
                await db.commit()
                logger.info(
                    "[VP %s] Late slicer MQTT for %s — retroactively stamped %s onto queue item(s) %s",
                    self.name,
                    stash_key,
                    sorted(patch.keys()),
                    eligible_ids,
                )
        except Exception as e:
            logger.error(
                "[VP %s] Failed to retroactively stamp queue item(s) %s for %s: %s",
                self.name,
                queue_item_ids,
                stash_key,
                e,
            )
        finally:
            self._recent_queue_items.pop(stash_key, None)

    def _schedule_finish_release(self, filename: str, delay: float = 1.5) -> None:
        """Re-set gcode_state=FINISH on the VP after the project_file ack.

        #1280 set FINISH after the FTP upload completes — that was correct
        for the slicer flow at the time (MQTT project_file → FTP → done).
        Bambu Studio 2.7.x flipped the order to FTP → FTP → MQTT project_file,
        which means ``_send_print_response`` runs *after* the FINISH set in
        ``on_file_received`` and overwrites the state back to PREPARE. The
        slicer's 1 Hz status stream then carries PREPARE forever and the
        send modal sits at "Downloading" until the VP is restarted (#1658).

        Re-firing FINISH after a short delay closes the gap: the slicer sees
        the synthetic PREPARE in the project_file ack (and likely one PREPARE
        push on the 1 Hz cycle), then the next push carries FINISH and the
        modal releases. Proxy mode is exempt — there the real printer drives
        the state through the bridge and a synthetic FINISH would clobber a
        real PREPARE/RUNNING transition coming back from the printer.

        Cancels any in-flight timer before scheduling a new one so a slicer
        that fires project_file twice in quick succession only ends in one
        FINISH.
        """
        if self._mqtt is None:
            return
        if self._finish_release_task is not None and not self._finish_release_task.done():
            self._finish_release_task.cancel()
        self._finish_release_task = asyncio.create_task(
            self._delayed_finish_release(filename, delay),
            name=f"vp-{self.id}-finish-release",
        )

    async def _delayed_finish_release(self, filename: str, delay: float) -> None:
        """Sleep, then set gcode_state=FINISH. Used by ``_schedule_finish_release``."""
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if self._mqtt is None:
            return
        self._mqtt.set_gcode_state("FINISH", filename=filename, prepare_percent="100")
        logger.debug("[VP %s] Re-set gcode_state=FINISH after project_file ack (%s)", self.name, filename)

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

        archived = False
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
                    await self._broadcast_archive_created(archive)
                    archived = True
                else:
                    logger.error("Failed to archive file: %s", file_path.name)
        except Exception as e:
            logger.error("Error archiving file: %s", e)
        finally:
            # Always release the in-flight marker and delete the temp file —
            # previously the failure paths only logged and the next upload of
            # the same name was silently rejected with "already uploading",
            # the upload_dir filled up indefinitely, and the slicer received
            # a clean 226 even though no archive existed (#audit-R2-1).
            self._pending_files.pop(file_path.name, None)
            if archived:
                try:
                    file_path.unlink()
                except OSError:
                    pass
            else:
                # Drop the failed temp file so it doesn't accumulate.
                try:
                    file_path.unlink(missing_ok=True)
                except OSError:
                    pass

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
        except Exception as e:
            logger.error("Error queueing file: %s", e)
            # Queue insert failed — drop the temp file so it doesn't
            # accumulate. The file is unreachable without the DB row.
            try:
                file_path.unlink(missing_ok=True)
            except OSError:
                pass
        finally:
            # Always release the in-flight marker so concurrent uploads
            # with the same filename aren't spuriously rejected after
            # a queue failure.
            self._pending_files.pop(file_path.name, None)

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

        # Wait briefly for the slicer's MQTT `project_file` command so the
        # queue item can inherit the slicer-side print options the user
        # picked (timelapse, bed_leveling, etc). Slicers send the FTP upload
        # first and the MQTT command immediately after, so the typical lag
        # is a few hundred ms. The window is generous enough to absorb
        # wireless / loaded-Pi jitter without making every VP-queue add
        # visibly slow — observed worst case in #1780 round 3 was 2.085 s,
        # the previous 2.0 s ceiling. Falls back to the global default_*
        # settings if MQTT doesn't arrive in time (legacy behaviour for
        # users on a slicer that doesn't send a print command). #1403.
        # The wait is skipped when there's no MQTT server attached — covers
        # unit tests that invoke `_add_to_print_queue` directly without
        # going through `on_print_command`, so they don't pay the wait tax.
        slicer_opts = self._slicer_print_options.pop(file_path.name, None)
        if slicer_opts is None and self._mqtt is not None:
            event = asyncio.Event()
            self._slicer_print_options_events[file_path.name] = event
            try:
                await asyncio.wait_for(event.wait(), timeout=_SLICER_OPTIONS_WAIT_TIMEOUT)
                slicer_opts = self._slicer_print_options.pop(file_path.name, None)
            except asyncio.TimeoutError:
                slicer_opts = None
            finally:
                self._slicer_print_options_events.pop(file_path.name, None)
        # If the cache still misses, queued workflow flags / nozzle pick will
        # silently fall back to settings defaults. Surface the missed key so a
        # future stash/lookup mismatch (the #1780 root cause) is obvious in
        # the log instead of needing a wire capture to diagnose.
        if slicer_opts is None:
            logger.debug(
                "[VP %s] No slicer options cached for %r (cache keys: %s); "
                "workflow flags + nozzle pick will fall back to settings defaults.",
                self.name,
                file_path.name,
                sorted(self._slicer_print_options.keys()),
            )

        try:
            import json

            from backend.app.api.routes.settings import get_setting
            from backend.app.models.print_queue import PrintQueueItem
            from backend.app.services.archive import ArchiveService
            from backend.app.services.filament_requirements import extract_filament_requirements

            async with self._session_factory() as db:
                name_source = await get_setting(db, "virtual_printer_archive_name_source")
                prefer_filename = name_source == "filename"

                # Read workflow defaults from settings. Without this the
                # PrintQueueItem below would fall back to the column-level
                # defaults and ignore the user's workflow preferences (#1235).
                # Fallbacks match AppSettings defaults in schemas/settings.py.
                # The slicer-side options captured above (if any) take
                # precedence per-field over these defaults.
                def _bool_setting(value: str | None, default: bool) -> bool:
                    return value.lower() == "true" if value is not None else default

                def _slicer_or(field_mqtt: str, settings_default: bool) -> bool:
                    """Slicer's MQTT value if present, else the settings default.

                    Slicer payloads carry both bool and int (0/1) shapes
                    depending on firmware family — coerce via bool() so
                    `0`/`False` and `1`/`True` both work.
                    """
                    if slicer_opts is not None and field_mqtt in slicer_opts:
                        return bool(slicer_opts[field_mqtt])
                    return settings_default

                # Note the MQTT field names differ from Bambuddy's column
                # names: MQTT uses `bed_leveling` (single L) while the
                # column / settings key use `bed_levelling` (double L).
                bed_levelling = _slicer_or(
                    "bed_leveling", _bool_setting(await get_setting(db, "default_bed_levelling"), True)
                )
                flow_cali = _slicer_or("flow_cali", _bool_setting(await get_setting(db, "default_flow_cali"), False))
                vibration_cali = _slicer_or(
                    "vibration_cali", _bool_setting(await get_setting(db, "default_vibration_cali"), True)
                )
                layer_inspect = _slicer_or(
                    "layer_inspect", _bool_setting(await get_setting(db, "default_layer_inspect"), False)
                )
                timelapse = _slicer_or("timelapse", _bool_setting(await get_setting(db, "default_timelapse"), False))

                # H2C dual-nozzle-rack slicer-pick preservation (#1780).
                # BambuStudio's project_file MQTT command for rack-swap models
                # (O1C2 today) carries `nozzle_mapping` — a per-filament array
                # of physical nozzle position IDs (`list[int]`). Forward it
                # verbatim onto the queue item so the dispatcher can replay it
                # in its own project_file command. Without this the H2C
                # firmware falls back to "last matching nozzle" auto-pick and
                # ignores the user's Bambu Studio choice. Every other model
                # has it absent from slicer_opts, so the capture is a
                # transparent no-op there. (`nozzles_info` was also captured
                # in the original fix but BambuStudio never actually sends it
                # — verified via wire capture on H2C — so only `nozzle_mapping`
                # is forwarded now.)
                nozzle_mapping_json: str | None = None
                if slicer_opts is not None:
                    raw = slicer_opts.get("nozzle_mapping")
                    if raw is not None:
                        # BambuStudio's NetworkAgent embeds this as parsed
                        # JSON in the project_file body (matching the
                        # ams_mapping shape Bambuddy already consumes as
                        # list[int]). Accept a JSON-encoded string defensively
                        # in case any path arrives stringified.
                        if isinstance(raw, str):
                            try:
                                raw = json.loads(raw)
                            except json.JSONDecodeError:
                                logger.warning(
                                    "[VP %s] Slicer nozzle_mapping is unparseable JSON, dropping: %r",
                                    self.name,
                                    raw,
                                )
                                raw = None
                        if raw is not None:
                            nozzle_mapping_json = json.dumps(raw)

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
                    # #1733: multi-plate "Send All" uploads ship every plate in
                    # one 3MF — `slice_info.config` lists each `<plate>` with
                    # its own index. Enqueue one PrintQueueItem per plate so
                    # the scheduler runs each separately. Single-plate "Send"
                    # comes through as `[N]` (one plate index) so the loop
                    # below runs once and the existing behaviour is preserved.
                    plate_ids = self._extract_plate_ids(file_path)

                    # Pick a base position the same way the manual /print-queue/
                    # POST does, then hand consecutive positions to each plate
                    # so a Send All keeps plate-order execution inside the
                    # queue (#1733). Previously hardcoded to 1, which created
                    # duplicate position=1 rows on every VP upload and made
                    # queue execution order non-deterministic for any non-
                    # empty queue.
                    from sqlalchemy import func, select as _sql_select

                    queue_scope = _sql_select(func.max(PrintQueueItem.position)).where(
                        PrintQueueItem.status == "pending"
                    )
                    if self.target_printer_id is not None:
                        queue_scope = queue_scope.where(PrintQueueItem.printer_id == self.target_printer_id)
                    else:
                        queue_scope = queue_scope.where(PrintQueueItem.printer_id.is_(None))
                    try:
                        max_pos_raw = (await db.execute(queue_scope)).scalar()
                        max_pos = int(max_pos_raw) if max_pos_raw is not None else 0
                    except (TypeError, ValueError):
                        max_pos = 0

                    # Parse per-plate filament requirements (#1188). Each plate
                    # has its own filament set in `slice_info.config`, so the
                    # `required_filament_types` / `filament_overrides` columns
                    # on each queue item reflect THAT plate, not the file's
                    # first plate. Scoping was already plate-aware via #1697 —
                    # the `extract_filament_requirements(path, plate_id)` filter
                    # returns just the plate's filaments. required_filament_types
                    # is populated unconditionally — it's cheap, lets the
                    # scheduler reject obvious mis-matches even without
                    # force_color_match. filament_overrides only carries
                    # force_color_match=True when the per-VP setting is on, so
                    # upgraders keep the old behaviour by default.
                    queue_item_ids: list[int] = []
                    for offset, plate_id in enumerate(plate_ids, start=1):
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
                            position=max_pos + offset,
                            status="pending",
                            manual_start=not self.auto_dispatch,
                            required_filament_types=required_filament_types_json,
                            filament_overrides=filament_overrides_json,
                            bed_levelling=bed_levelling,
                            flow_cali=flow_cali,
                            vibration_cali=vibration_cali,
                            layer_inspect=layer_inspect,
                            timelapse=timelapse,
                            # Per-VP opt-in for auto-print G-code injection (#1516).
                            # Default off; when on, the scheduler still no-ops unless
                            # gcode_snippets are configured for the target model, so it's
                            # effectively "inject when enabled AND snippets exist".
                            gcode_injection=self.gcode_injection,
                            # H2C rack-swap slicer pick (#1780). Captured above;
                            # stamped on every plate so a multi-plate Send All keeps
                            # the same nozzle pick across plates rather than only the
                            # first one (mirrors the #1697 / #1188 per-plate loop fix).
                            nozzle_mapping=nozzle_mapping_json,
                        )
                        db.add(queue_item)
                        await db.flush()  # populate queue_item.id before logging
                        queue_item_ids.append(queue_item.id)
                    await db.commit()
                    # Track the freshly-committed queue items so
                    # `on_print_command` can retroactively stamp slicer-side
                    # fields if the MQTT `project_file` lands AFTER the
                    # `_SLICER_OPTIONS_WAIT_TIMEOUT` window expired — the
                    # #1780 round-3 race. Eviction of stale entries here
                    # keeps the dict bounded; the queue path is the only
                    # writer, so doing it on commit is enough.
                    now = time.monotonic()
                    cutoff = now - _RECENT_QUEUE_ITEM_TTL
                    self._recent_queue_items = {k: v for k, v in self._recent_queue_items.items() if v[1] > cutoff}
                    self._recent_queue_items[file_path.name] = (list(queue_item_ids), now)
                    # Last-chance check: MQTT for this filename could have
                    # arrived during ANY await between the initial pop and
                    # now — wait_for itself, archive_print, db.flush,
                    # db.commit. In all those cases `on_print_command`
                    # stashed its data but neither the event-signal path nor
                    # the retroactive `_recent_queue_items` path was in
                    # place to consume it. Pop any late stash and apply
                    # inline so the late MQTT never leaks past the queue-add.
                    late_opts = self._slicer_print_options.pop(file_path.name, None)
                    if late_opts is not None:
                        logger.info(
                            "[VP %s] Late slicer MQTT detected for %s during queue-add — "
                            "applying inline (race vs commit/archive/flush yield)",
                            self.name,
                            file_path.name,
                        )
                        await self._restamp_recent_queue_item(file_path.name, late_opts)
                    if len(queue_item_ids) == 1:
                        logger.info("[VP %s] Added to queue: %s", self.name, queue_item_ids[0])
                    else:
                        logger.info(
                            "[VP %s] Added %d queue items for multi-plate upload (plates %s): %s",
                            self.name,
                            len(queue_item_ids),
                            plate_ids,
                            queue_item_ids,
                        )
                    await self._broadcast_archive_created(archive)
                else:
                    logger.error("Failed to archive file: %s", file_path.name)
        except Exception as e:
            logger.error("Error adding to print queue: %s", e)
        finally:
            # Always release the marker and clean the temp file. Without this
            # the same-name STOR guard would block the next upload and the
            # upload_dir would accumulate failed temp files forever
            # (#audit-R2-1).
            self._pending_files.pop(file_path.name, None)
            try:
                file_path.unlink(missing_ok=True)
            except OSError:
                pass

    async def _broadcast_archive_created(self, archive) -> None:
        """Notify connected clients that a new archive exists.

        Real-printer prints get this from main.py's MQTT print_start handler;
        VP-uploaded prints need their own broadcast or the Archives page stays
        stale until the user switches tabs (#1282).
        """
        try:
            from backend.app.core.websocket import ws_manager

            await ws_manager.send_archive_created(
                {
                    "id": archive.id,
                    "printer_id": archive.printer_id,
                    "filename": archive.filename,
                    "print_name": archive.print_name,
                    "status": archive.status,
                }
            )
        except Exception as e:
            logger.debug("[VP %s] archive_created broadcast failed: %s", self.name, e)

    @staticmethod
    def _extract_plate_ids(file_path: Path) -> list[int]:
        """Extract every plate index from a 3MF's slice_info.config.

        A multi-plate "Send All" from BambuStudio / OrcaSlicer uploads a
        single 3MF containing every plate the user selected. Each plate
        has its own ``<plate>`` block with a ``<metadata key="index"
        value="N"/>`` child and its own ``Metadata/plate_N.gcode`` payload
        inside the same zip. Returning the full ordered list lets the VP
        queue path create one queue item per plate (`_add_to_print_queue`
        loops over the result), so "Send All" of a 3-plate file produces
        3 queue items sharing the same archive — one per plate to print.

        Single-plate "Send" hits the same code path and returns ``[N]``
        for whichever plate the user selected; the loop runs once and the
        existing single-plate behaviour is preserved.

        Returns ``[1]`` when the 3MF is missing ``slice_info.config``,
        unparseable, or contains no plate-index metadata — the original
        single-plate fallback. Production logs at debug so a non-3MF
        upload doesn't spam, but the trail survives for support bundles.
        """
        try:
            import xml.etree.ElementTree as ET
            import zipfile

            with zipfile.ZipFile(file_path, "r") as zf:
                if "Metadata/slice_info.config" in zf.namelist():
                    content = zf.read("Metadata/slice_info.config").decode()
                    root = ET.fromstring(content)  # noqa: S314  # nosec B314
                    plate_ids: list[int] = []
                    for plate in root.findall(".//plate"):
                        for meta in plate.findall("metadata"):
                            if meta.get("key") == "index" and meta.get("value"):
                                try:
                                    plate_ids.append(int(meta.get("value")))
                                except ValueError:
                                    continue
                                break
                    if plate_ids:
                        return plate_ids
        except Exception as e:
            logger.debug("[VP] _extract_plate_ids failed for %s: %s", file_path.name, e)
        return [1]

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

        # FTP server. Each VP gets a non-overlapping passive-mode port slice
        # derived from its DB id so bridge-mode Docker users only have to
        # expose a narrow range (#1646). Default slice is 10 ports per VP;
        # see ftp_server.compute_passive_port_slice for the wrap-around
        # behaviour on installs with very high VP ids.
        passive_port_min, passive_port_max = compute_passive_port_slice(self.id)
        self._ftp = VirtualPrinterFTPServer(
            upload_dir=self.upload_dir,
            access_code=self.access_code,
            cert_path=cert_path,
            key_path=key_path,
            on_file_received=self.on_file_received,
            bind_address=bind_addr,
            vp_name=self.name,
            passive_port_min=passive_port_min,
            passive_port_max=passive_port_max,
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

        # SSDP server — advertise_addr is the remote_interface_ip (Tailscale
        # IP, when chosen from the bind_ip dropdown) or the bind_ip. SSDP
        # Location accepts IPs only; FQDNs go in through bind_ip selection
        # at the printer-IP level and resolve before reaching the SSDP
        # advertisement.
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

        # Wait briefly for every child service to actually finish binding its
        # socket so ``is_running`` doesn't lie. Without this barrier a caller
        # racing the start (e.g. the diagnostic route) would see is_running=True
        # while ports were still in the gap between task creation and the
        # ``asyncio.start_server`` returning. Bounded timeout — if a child
        # hangs we log it and move on; the existing task tracking still
        # catches the failure on the next iteration.
        ready_targets = [
            ("FTP", self._ftp.ready),
            ("MQTT", self._mqtt.ready),
            ("Bind", self._bind.ready),
            ("SSDP", self._ssdp.ready),
        ]
        try:
            await asyncio.wait_for(
                asyncio.gather(*(e.wait() for _, e in ready_targets)),
                timeout=5.0,
            )
        except TimeoutError:
            not_ready = [name for name, e in ready_targets if not e.is_set()]
            logger.warning(
                "[VP %s] Sub-service(s) didn't bind within 5s: %s — continuing anyway",
                self.name,
                ", ".join(not_ready) or "(none)",
            )

        logger.info("[VP %s] Server-mode services started on %s", self.name, bind_addr)

    async def stop_server(self) -> None:
        """Stop server-mode services."""
        if self._finish_release_task is not None and not self._finish_release_task.done():
            self._finish_release_task.cancel()
            self._finish_release_task = None
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
        # Serialize sync_from_db so concurrent PUT /vp/{id} calls can't
        # race the start/stop sequence and leave duplicate sub-services
        # bound to the same port. The lock is fine-grained enough that
        # a single VP update completes in well under a second; if the
        # user holds the lock with a long-running start they intended
        # to anyway.
        self._sync_lock = asyncio.Lock()

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

    def get_ca_certificate_info(self) -> dict:
        """Return the shared virtual-printer CA certificate for slicer-trust import.

        The CA is shared by every VP (one import covers all of them). It is
        generated on demand here if no VP has triggered cert generation yet,
        so the "copy/download certificate" UI works even before the first VP
        is enabled.
        """
        certs_dir = self._base_dir / "certs"
        cert_service = CertificateService(cert_dir=certs_dir, shared_ca_dir=certs_dir)
        return cert_service.get_ca_certificate_info()

    @property
    def is_enabled(self) -> bool:
        """Check if any virtual printer is running."""
        return len(self._instances) > 0

    async def sync_from_db(self) -> None:
        """Load all VPs from DB, reconcile running state.

        Serialised by ``self._sync_lock`` — concurrent PUT /vp/{id} routes
        all call into this method; without the lock the start / stop
        sequence races and can leave duplicate sub-services bound to the
        same port or orphan still-running tasks.
        """
        if not self._session_factory:
            logger.warning("Cannot sync virtual printers: no session factory")
            return

        async with self._sync_lock:
            await self._sync_from_db_locked()

    async def _sync_from_db_locked(self) -> None:
        """Inner sync body — caller holds ``self._sync_lock``."""
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

            # Proxy mode: detect target printer IP / serial changes from the
            # DB lookup above. Without this branch a DHCP renewal that gives
            # the target printer a new IP would leave the running proxy
            # forwarding to the stale IP until the user manually toggles the
            # VP. The same shape covers a target-side serial change.
            proxy_target_changed = False
            if vp.mode == "proxy":
                fresh = proxy_ips.get(vp.id)
                if fresh is not None:
                    fresh_ip, fresh_serial = fresh
                    if (
                        getattr(instance, "target_printer_ip", None) != fresh_ip
                        or getattr(instance, "target_printer_serial", None) != fresh_serial
                    ):
                        proxy_target_changed = True

            # Normalize the DB value before comparing — a legacy `immediate`
            # row read before the migration window finishes would otherwise
            # trip the "changed" branch and bounce every VP at boot.
            db_mode = normalize_vp_mode(vp.mode)
            changed = (
                instance.mode != db_mode
                or instance.model != (vp.model or DEFAULT_VIRTUAL_PRINTER_MODEL)
                or instance.access_code != (vp.access_code or "")
                or instance.bind_ip != (vp.bind_ip or "")
                or instance.remote_interface_ip != (vp.remote_interface_ip or "")
                or instance.target_printer_id != vp.target_printer_id
                or instance.auto_dispatch != vp.auto_dispatch
                # Queue-mode behaviour toggle — without it the running
                # instance silently keeps the old value until process
                # restart (#1552 follow-up family).
                or instance.queue_force_color_match != vp.queue_force_color_match
                or instance.gcode_injection != vp.gcode_injection
                or proxy_target_changed
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
                    gcode_injection=vp.gcode_injection,
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
            "mode": VP_MODE_ARCHIVE,
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
        mode: str = VP_MODE_ARCHIVE,
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
