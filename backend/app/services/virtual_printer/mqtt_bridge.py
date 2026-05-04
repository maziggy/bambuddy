"""MQTT bridge for non-proxy virtual printers.

Mirrors the target printer's state to slicers connected to a virtual printer
without opening a second MQTT session on the printer (reuses Bambuddy's
existing subscription — firmware inflight budget unaffected, see PR #1164).

Architecture (cached-as-base, not a separate fan-out stream):

  - **push_status** snapshots from the printer are CACHED here. The VP's
    `SimpleMQTTServer._send_status_report` consults that cache and sends
    a near-byte-identical copy of the real push to the slicer (with
    sequence_id / gcode_state / etc. overridden). Single source of truth
    keeps BambuStudio's Send pre-flight happy.
  - **info.get_version** responses are also cached so the synthetic version
    response can include the real AMS module list (n3f/n3s/ams entries).
    Without this BambuStudio's Prepare tab labels every AMS as "unknown".
  - **Other command responses** (extrusion_cali_get, AMS write acks,
    xcam responses, …) are fanned out raw to the slicer — they carry
    sequence_ids the slicer is waiting on; the slicer matches and ignores
    unrelated ones.

Identity rewriting at cache time:

  - `upgrade_state.sn` (and any other nested dict's `sn` matching the real
    serial) → VP serial
  - `net.info[*].ip` little-endian uint32 → VP bind IP. BambuStudio reads
    this as the FTP destination IP. Without this the slicer FTPs straight
    to the real printer and bypasses Bambuddy.
  - `ipcam.rtsp_url` is left unchanged: BambuStudio overrides the URL host
    with the device IP it bound to (the VP), so the slicer hits the VP's
    own RTSPS proxy on port 322.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.services.bambu_mqtt import BambuMQTTClient
    from backend.app.services.printer_manager import PrinterManager
    from backend.app.services.virtual_printer.mqtt_server import SimpleMQTTServer

logger = logging.getLogger(__name__)

REFRESH_INTERVAL_SECONDS = 30.0


def _ip_to_uint32_le(ip_str: str) -> int:
    """Encode dotted-quad IPv4 as little-endian uint32 (Bambu MQTT's `net.info[].ip` shape)."""
    parts = [int(x) for x in ip_str.split(".")]
    if len(parts) != 4 or any(p < 0 or p > 255 for p in parts):
        raise ValueError(f"invalid IPv4: {ip_str!r}")
    return parts[0] | (parts[1] << 8) | (parts[2] << 16) | (parts[3] << 24)


class MQTTBridge:
    """Per-VP MQTT fan-out between a real printer and slicers connected to a VP."""

    def __init__(
        self,
        *,
        vp_id: int,
        vp_name: str,
        vp_serial: str,
        target_printer_id: int,
        mqtt_server: SimpleMQTTServer,
        printer_manager: PrinterManager,
    ):
        self.vp_id = vp_id
        self.vp_name = vp_name
        self.vp_serial = vp_serial
        self.target_printer_id = target_printer_id
        self._mqtt_server = mqtt_server
        self._printer_manager = printer_manager
        self._target_client: BambuMQTTClient | None = None
        self._target_serial: str | None = None
        self._target_ip_uint32_le: int | None = None
        self._vp_ip_uint32_le: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._refresh_task: asyncio.Task | None = None
        self._stopping = False
        self._latest_print_state: dict | None = None
        self._latest_version_modules: list | None = None

    @property
    def is_active(self) -> bool:
        """True iff a target client is bound and currently connected."""
        client = self._target_client
        return bool(client is not None and getattr(client, "state", None) and client.state.connected)

    async def start(self) -> None:
        """Bind to the target printer (if connected) and start the refresh loop."""
        self._loop = asyncio.get_running_loop()
        self._stopping = False
        self._resolve_client()
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        """Detach from the target printer and stop the refresh loop."""
        self._stopping = True
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None
        self._unbind_client()
        self._loop = None

    async def _refresh_loop(self) -> None:
        """Re-resolve the target client periodically — paho clients can be replaced.

        BambuMQTTClient is destroyed and recreated on PrinterManager.connect_printer
        (e.g. printer config update). Without periodic refresh the bridge would lose
        fan-out after such a churn until the VP itself restarts.
        """
        try:
            while not self._stopping:
                await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
                self._resolve_client()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[%s] MQTT bridge refresh loop crashed", self.vp_name)

    def _resolve_client(self) -> None:
        """Look up the current client for target_printer_id and rebind if it changed."""
        try:
            current = self._printer_manager.get_client(self.target_printer_id)
        except Exception:
            logger.exception("[%s] MQTT bridge: get_client failed", self.vp_name)
            return

        if current is self._target_client:
            return

        # Client identity changed — unregister from the old, register on the new.
        self._unbind_client()
        if current is None:
            return

        try:
            current.register_raw_message_handler(self._on_printer_raw)
        except Exception:
            logger.exception("[%s] MQTT bridge: register_raw_message_handler failed", self.vp_name)
            return

        self._target_client = current
        self._target_serial = getattr(current, "serial_number", None)

        # Cache printer IP and VP bind IP encoded as little-endian uint32, so we
        # can rewrite `net.info[*].ip` in cached push_status. BambuStudio reads
        # that field for the FTP destination IP — without rewriting, the slicer
        # bypasses the VP and FTPs straight to the real printer.
        target_ip = getattr(current, "ip_address", None)
        vp_ip = getattr(self._mqtt_server, "bind_address", None)
        if target_ip and vp_ip and vp_ip not in ("0.0.0.0", "", None):  # nosec B104
            try:
                self._target_ip_uint32_le = _ip_to_uint32_le(target_ip)
                self._vp_ip_uint32_le = _ip_to_uint32_le(vp_ip)
            except ValueError:
                self._target_ip_uint32_le = None
                self._vp_ip_uint32_le = None

        logger.info(
            "[%s] MQTT bridge bound to printer %s (serial=%s)",
            self.vp_name,
            self.target_printer_id,
            self._target_serial,
        )

        # Trigger a fresh get_version + pushall against the printer so the bridge
        # cache populates immediately. Bambuddy itself queries these on connect,
        # but that fires before the bridge attaches as a raw-message consumer,
        # so without this nudge the cache stays empty until the next periodic
        # query (which can be minutes away).
        request_fn = getattr(current, "_request_version", None)
        if callable(request_fn):
            try:
                request_fn()
            except Exception:
                logger.exception("[%s] MQTT bridge: _request_version failed", self.vp_name)
        request_status_fn = getattr(current, "request_status_update", None)
        if callable(request_status_fn):
            try:
                request_status_fn()
            except Exception:
                logger.exception("[%s] MQTT bridge: request_status_update failed", self.vp_name)

    def _unbind_client(self) -> None:
        if self._target_client is None:
            return
        try:
            self._target_client.unregister_raw_message_handler(self._on_printer_raw)
        except Exception:
            logger.exception("[%s] MQTT bridge: unregister_raw_message_handler failed", self.vp_name)
        logger.info("[%s] MQTT bridge unbound from printer %s", self.vp_name, self.target_printer_id)
        self._target_client = None
        self._target_serial = None

    def _on_printer_raw(self, topic: str, payload: bytes) -> None:
        """Paho-thread callback — cache the latest push_status for synthetic replay.

        Instead of fanning out a second stream of MQTT messages to the slicer
        (which trips BambuStudio's Send pre-flight consistency checks), we cache
        the latest real printer push_status here. The VP's existing 1 Hz
        synthetic push (which is what Send is built around) consults this cache
        and replaces its stub fields with real values when available.
        """
        if self._stopping:
            return
        target_serial = self._target_serial
        if not target_serial:
            return
        prefix = f"device/{target_serial}/"
        if not topic.startswith(prefix):
            return
        suffix = topic[len(prefix) :]
        if not suffix.startswith("report"):
            return
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return

        # Race-free by construction: `json.loads` returns a fresh dict tree per
        # call so paho-thread mutations below cannot collide with prior cached
        # state held by the asyncio thread. `_send_status_report`'s shallow
        # `dict(cached)` is also safe because nothing else writes to the cached
        # tree after assignment. The defensive deep-copy on store below removes
        # any future risk if a maintainer later re-enters the cached dict to
        # mutate it.

        # push_status snapshots → cache the print dict for the periodic 1 Hz
        # cached-as-base delivery. We do NOT fan these out separately (the
        # 1 Hz cached-as-base IS the slicer-facing push_status stream).
        print_data = data.get("print")
        if isinstance(print_data, dict) and print_data.get("command") == "push_status":
            for value in print_data.values():
                if isinstance(value, dict) and value.get("sn") == target_serial:
                    value["sn"] = self.vp_serial
            # Note: `ipcam.rtsp_url` carries the real printer's IP. We pass it
            # through unchanged — the slicer uses it to fetch the live camera
            # stream directly from the printer. On the same LAN this works as
            # long as the slicer's stored access code matches the printer's
            # (i.e. configure the VP with the same access code as its target).
            # Rewrite real printer IP → VP bind IP in `net.info[*].ip` so the
            # slicer's FTP destination resolves to the VP, not the real printer.
            if self._target_ip_uint32_le is not None and self._vp_ip_uint32_le is not None:
                net = print_data.get("net")
                if isinstance(net, dict):
                    info = net.get("info")
                    if isinstance(info, list):
                        for entry in info:
                            if isinstance(entry, dict) and entry.get("ip") == self._target_ip_uint32_le:
                                entry["ip"] = self._vp_ip_uint32_le
            # Defensive deep copy on store so the cache is fully decoupled from
            # the freshly-parsed tree and from any reader's reference.
            self._latest_print_state = copy.deepcopy(print_data)
            return

        # info.get_version responses → cache the module list so the synthetic
        # version response can include the real AMS modules.
        info_data = data.get("info")
        if isinstance(info_data, dict) and info_data.get("command") == "get_version":
            modules = info_data.get("module")
            if isinstance(modules, list):
                rewritten: list = []
                for module in modules:
                    if isinstance(module, dict):
                        module = dict(module)
                        if module.get("sn") == target_serial:
                            module["sn"] = self.vp_serial
                    rewritten.append(module)
                self._latest_version_modules = rewritten
            # Don't fan out get_version — the slicer's request (when it issues
            # one) is intercepted locally and answered from the cached modules.
            return

        # Everything else (extrusion_cali_get response, AMS write acks, xcam
        # responses, …): fan out to the slicer. These are responses to commands
        # the slicer (or Bambuddy) issued; the slicer matches by sequence_id and
        # ignores responses to commands it didn't send. Without this, slicer-
        # initiated queries like extrusion_cali_get hang forever and BambuStudio
        # blocks Send waiting for the response.
        loop = self._loop
        if loop is None:
            return
        target_bytes = target_serial.encode("ascii")
        if target_bytes in payload:
            payload = payload.replace(target_bytes, self.vp_serial.encode("ascii"))
        vp_topic = f"device/{self.vp_serial}/{suffix}"
        try:
            asyncio.run_coroutine_threadsafe(
                self._mqtt_server.push_raw_to_clients(vp_topic, payload),
                loop,
            )
        except RuntimeError:
            pass

    def get_latest_print_state(self) -> dict | None:
        """Return the most recent real printer push_status `print` dict, or None."""
        return self._latest_print_state

    def get_latest_version_modules(self) -> list | None:
        """Return the most recent real printer get_version `module` list, or None."""
        return self._latest_version_modules

    def forward_to_printer(self, payload: dict) -> bool:
        """Publish a slicer-originated command to the real printer's request topic.

        Returns False if no printer client is currently bound.
        """
        client = self._target_client
        target_serial = self._target_serial
        if client is None or target_serial is None:
            logger.debug(
                "[%s] forward_to_printer dropped (printer %s not bound): %s",
                self.vp_name,
                self.target_printer_id,
                list(payload.keys()),
            )
            return False
        topic = f"device/{target_serial}/request"
        try:
            return client.publish_raw(topic, json.dumps(payload), qos=1)
        except Exception:
            logger.exception("[%s] forward_to_printer publish failed", self.vp_name)
            return False
