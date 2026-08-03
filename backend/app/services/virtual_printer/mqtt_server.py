"""MQTT broker for virtual printer.

Implements an MQTT broker that accepts connections from slicers,
authenticates with the configured access code, and logs print commands.
"""

import asyncio
import copy
import hmac
import json
import logging
import socket
import ssl
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from backend.app.services.virtual_printer._debug import append_event, dump_wire

if TYPE_CHECKING:
    from backend.app.services.virtual_printer.mqtt_bridge import MQTTBridge

logger = logging.getLogger(__name__)

# Default MQTT port for Bambu printers (MQTT over TLS)
MQTT_PORT = 8883

# Per-IP MQTT auth rate-limit. 5 failures within 60 s blocks further attempts
# for the remainder of the window. Bambu printers themselves don't rate-limit,
# but they're not exposed past the LAN edge; Bambuddy's VPs sometimes are
# (Tailscale, port-forwarded), so an 8-char access code without any
# brute-force friction is too weak. The window auto-recovers — no manual
# unblock — so a legitimate user who fat-fingered their access code 5 times
# only waits up to 60 s.
_AUTH_RATE_LIMIT_MAX_ATTEMPTS = 5
_AUTH_RATE_LIMIT_WINDOW_SECONDS = 60.0

# Pending-request map bound. Each entry maps a slicer command's
# sequence_id to its originating client_id so the bridge response can be
# routed back to just that client. Bounded so a slicer that issues
# commands without ever consuming responses can't leak memory.
_PENDING_REQUEST_MAX_ENTRIES = 256

# Target-printer gcode_states for which the VP mirrors live print progress to
# the slicer (#1887). BambuStudio and OrcaSlicer gate BOTH the Device-tab
# progress panel and the Send button on the same predicate —
# `MachineObject::is_in_printing()`, i.e. gcode_state in
# {RUNNING, PAUSE, SLICING, PREPARE} — so reporting the real state verbatim
# would show progress at the cost of blocking Send for as long as the printer
# prints. That is exactly the #1558 regression. `FINISH` is the one state that
# renders the progress panel (`is_in_printing() || print_status == "FINISH"` in
# StatusPanel::update_subtask) while leaving Send enabled, so the mirror keeps
# reporting FINISH and only fills in the numbers underneath it.
_MIRRORED_PRINT_STATES = frozenset({"RUNNING", "PAUSE"})

# How long after the last upload-state transition the VP keeps echoing the
# slicer's own filename back at it before switching the report over to whatever
# the target printer is really printing. The slicer releases its in-flight-job
# lock when it sees gcode_state=FINISH carrying the subtask_name it uploaded
# (#1280 / #1658); swapping in the printer's filename while that handshake is
# still in flight wedges the send modal at "Downloading". 5 s covers the 1.5 s
# `_schedule_finish_release` timer plus several 1 Hz pushes.
_UPLOAD_SETTLE_SECONDS = 5.0

# Model code → product_name for version response (must match what slicer expects)
MODEL_PRODUCT_NAMES = {
    "BL-P001": "X1 Carbon",
    "BL-P002": "X1",
    "C13": "X1E",
    "N6": "X2D",
    "N9": "A2L",
    "C11": "P1P",
    "C12": "P1S",
    "N7": "P2S",
    "N2S": "A1",
    "N1": "A1 mini",
    "O1D": "H2D",
    "O1C": "H2C",
    "O1C2": "H2C",
    "O1S": "H2S",
}


class VirtualPrinterMQTTServer:
    """MQTT broker that accepts connections from slicers.

    This is a minimal MQTT broker implementation that:
    - Accepts TLS connections on port 8883
    - Authenticates with username 'bblp' and the configured access code
    - Receives print commands on device/{serial}/request
    - Can publish status on device/{serial}/report
    """

    def __init__(
        self,
        serial: str,
        access_code: str,
        cert_path: Path,
        key_path: Path,
        port: int = MQTT_PORT,
        on_print_command: Callable[[str, dict], None] | None = None,
    ):
        """Initialize the MQTT server.

        Args:
            serial: Virtual printer serial number
            access_code: Password for authentication
            cert_path: Path to TLS certificate
            key_path: Path to TLS private key
            port: Port to listen on (default 8883)
            on_print_command: Callback when print command received (filename, data)
        """
        self.serial = serial
        self.access_code = access_code
        self.cert_path = cert_path
        self.key_path = key_path
        self.port = port
        self.on_print_command = on_print_command
        self._running = False
        self._broker = None
        self._broker_task = None

    async def start(self) -> None:
        """Start the MQTT broker."""
        if self._running:
            return

        # Try to import amqtt
        try:
            from amqtt.broker import Broker
        except ImportError:
            logger.error("amqtt not installed. Run: pip install amqtt")
            return

        logger.info("Starting virtual printer MQTT broker on port %s", self.port)

        # Build broker configuration
        config = {
            "listeners": {
                "default": {
                    "type": "tcp",
                    "bind": f"0.0.0.0:{self.port}",
                    "ssl": "on",
                    "certfile": str(self.cert_path),
                    "keyfile": str(self.key_path),
                },
            },
            "auth": {
                "allow-anonymous": False,
                "plugins": ["auth_custom"],
            },
            "topic-check": {
                "enabled": False,  # Allow any topic
            },
        }

        try:
            self._running = True

            # Create and start broker
            self._broker = Broker(config)

            # Register custom auth plugin
            self._broker.plugins_manager.plugins_handlers["auth_custom"] = self._authenticate

            # Start the broker
            await self._broker.start()
            logger.info("MQTT broker started on port %s", self.port)

            # Keep running
            while self._running:
                await asyncio.sleep(1)

        except OSError as e:
            if e.errno == 98:  # Address already in use
                logger.error("MQTT port %s is already in use", self.port)
            else:
                logger.error("MQTT broker error: %s", e)
        except asyncio.CancelledError:
            logger.debug("MQTT broker task cancelled")
        except Exception as e:
            logger.error("MQTT broker error: %s", e)
        finally:
            await self.stop()

    async def _authenticate(self, session) -> bool:
        """Authenticate MQTT connection.

        Args:
            session: MQTT session with username/password

        Returns:
            True if authentication successful
        """
        username = getattr(session, "username", None)
        password = getattr(session, "password", None)

        # Bambu slicers use 'bblp' as username and access code as password
        if username == "bblp" and password == self.access_code:
            logger.debug("MQTT client authenticated from %s", session.remote_address)
            return True

        logger.warning("MQTT auth failed for user '%s' from %s", username, session.remote_address)
        return False

    async def stop(self) -> None:
        """Stop the MQTT broker."""
        logger.info("Stopping MQTT broker")
        self._running = False

        if self._broker:
            try:
                await self._broker.shutdown()
            except OSError as e:
                logger.debug("Error shutting down MQTT broker: %s", e)
            self._broker = None


class SimpleMQTTServer:
    """Simplified MQTT server using raw sockets.

    This is a fallback implementation that handles basic MQTT protocol
    without requiring the amqtt library. It's less feature-complete but
    more lightweight.
    """

    def __init__(
        self,
        serial: str,
        access_code: str,
        cert_path: Path,
        key_path: Path,
        port: int = MQTT_PORT,
        on_print_command: Callable[[str, dict], None] | None = None,
        model: str = "",
        bind_address: str = "0.0.0.0",  # nosec B104
        vp_name: str = "",
    ):
        self.serial = serial
        self.access_code = access_code
        self.model = model
        self.cert_path = cert_path
        self.key_path = key_path
        self.port = port
        self.on_print_command = on_print_command
        self.bind_address = bind_address
        self.vp_name = vp_name
        self._log_prefix = f"[{vp_name}] " if vp_name else ""
        self._running = False
        # Set after the socket is bound — see ftp_server.py for rationale.
        self.ready = asyncio.Event()
        self._server = None
        self._clients: dict[str, asyncio.StreamWriter] = {}
        # Per-client "effective serial" — the serial the slicer actually uses in
        # device/{serial}/report|request topics. Populated from the first
        # SUBSCRIBE/PUBLISH we see on a connection. This lets the VP respond on
        # the topic the slicer is listening on even when it disagrees with
        # self.serial (e.g. a stale Orca config that was bound to an older VP
        # serial, or a printer entry that was re-pointed at the VP IP without
        # updating the serial).
        self._client_serials: dict[str, str] = {}
        self._status_push_task: asyncio.Task | None = None
        self._sequence_id = 0

        # Dynamic state for status reports
        self._gcode_state = "IDLE"
        self._current_file = ""
        self._prepare_percent = "0"
        # Monotonic timestamp of the last upload-state transition, so the live-
        # progress mirror can tell whether the slicer is still waiting on its
        # own upload handshake. Starts at -inf: a VP that has never seen an
        # upload has no handshake to protect and can mirror immediately.
        self._state_changed_at = float("-inf")

        # MQTT bridge for non-proxy modes — set by VirtualPrinterInstance after start().
        # When the bridge is_active, real printer pushes are fanned out to slicers and
        # the synthetic 1s push is suspended. When the target printer goes offline the
        # synthetic fallback resumes automatically.
        self._bridge: MQTTBridge | None = None

        # Per-source-IP failed-auth tracker for rate-limiting / lockout.
        # Maps IP → list[monotonic timestamp] of recent failures within the
        # window. Pruned on every check so it doesn't grow unbounded.
        self._auth_failures: dict[str, list[float]] = {}

        # Maps sequence_id → originating client_id for slicer-initiated
        # commands forwarded to the real printer. Used in
        # ``push_raw_to_clients`` to route the printer's response only
        # back to the requesting slicer instead of fanning out to all
        # connected clients (which leaks slicer A's responses to slicer
        # B in multi-slicer setups). FIFO-bounded; if a response never
        # arrives the entry ages out instead of leaking.
        self._pending_requests: dict[str, str] = {}

    async def start(self) -> None:
        """Start the MQTT server."""
        if self._running:
            return

        logger.info("Starting simple MQTT server on port %s", self.port)

        # Create SSL context with Bambu-compatible settings
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(str(self.cert_path), str(self.key_path))
        # Match Bambu printer behavior - accept any client
        ssl_context.verify_mode = ssl.CERT_NONE
        # Allow TLS 1.2 for broader compatibility (some slicers may not support 1.3)
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        # Match real Bambu printer cipher behaviour: include the plain-RSA
        # AES-GCM suites the slicer expects. On hardened distros
        # (Fedora / RHEL with `update-crypto-policies`, hardened Alpine builds)
        # OpenSSL's `DEFAULT` list strips these suites, leaving no overlap
        # with the slicer's MQTT-over-TLS ClientHello — handshake fails
        # immediately and the slicer reports a connect error before any MQTT
        # CONNECT can be sent (#1610 audit). Same shape as the #620 fix.
        ssl_context.set_ciphers("DEFAULT:AES256-GCM-SHA384:AES128-GCM-SHA256")
        # Disable hostname checking
        ssl_context.check_hostname = False

        # Log certificate info
        import subprocess

        try:
            result = subprocess.run(
                ["openssl", "x509", "-in", str(self.cert_path), "-noout", "-subject", "-issuer"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            logger.info("MQTT SSL cert info: %s", result.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass  # Certificate info is for debug logging only; not critical

        logger.info("MQTT SSL context: TLS 1.2+, cert=%s", self.cert_path)

        try:
            self._running = True

            # Wrapper to log ALL connection attempts including SSL errors
            async def connection_handler(reader, writer):
                try:
                    addr = writer.get_extra_info("peername")
                    ssl_obj = writer.get_extra_info("ssl_object")
                    if ssl_obj:
                        logger.info(
                            f"{self._log_prefix}MQTT TLS connection from {addr} - cipher={ssl_obj.cipher()}, version={ssl_obj.version()}"
                        )
                    else:
                        logger.info("%sMQTT connection from %s (no TLS?)", self._log_prefix, addr)
                    await self._handle_client(reader, writer)
                except ssl.SSLError as e:
                    logger.error("MQTT SSL error: %s", e)
                except Exception as e:
                    logger.error("MQTT connection handler error: %s", e)

            self._server = await asyncio.start_server(
                connection_handler,
                self.bind_address,
                self.port,
                ssl=ssl_context,
            )
            self.ready.set()

            logger.info("Simple MQTT server listening on port %s", self.port)

            # Start periodic status push task
            self._status_push_task = asyncio.create_task(self._periodic_status_push())

            async with self._server:
                await self._server.serve_forever()

        except OSError as e:
            if e.errno == 98:  # Address already in use
                logger.error("MQTT port %s is already in use", self.port)
            else:
                logger.error("MQTT server error: %s", e)
        except asyncio.CancelledError:
            logger.debug("MQTT server task cancelled")
        except Exception as e:
            logger.error("MQTT server error: %s", e)
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the MQTT server."""
        logger.info("Stopping simple MQTT server")
        self._running = False
        self.ready.clear()

        # Stop periodic status push
        if self._status_push_task:
            self._status_push_task.cancel()
            try:
                await self._status_push_task
            except asyncio.CancelledError:
                pass  # Expected when stopping the periodic status push task
            self._status_push_task = None

        # Close all client connections (iterate over copy to avoid modification during iteration)
        for _client_id, writer in list(self._clients.items()):
            try:
                writer.close()
                await writer.wait_closed()
            except OSError:
                pass  # Best-effort client connection cleanup; client may have disconnected
        self._clients.clear()
        self._client_serials.clear()

        if self._server:
            try:
                self._server.close()
                await self._server.wait_closed()
            except OSError:
                pass  # Best-effort server shutdown; port may already be released
            self._server = None

    @staticmethod
    def _extract_serial_from_topic(topic: str) -> str | None:
        """Pull the serial out of a `device/{serial}/report|request` topic.

        Returns None if the topic doesn't match that shape — callers fall back
        to self.serial in that case.
        """
        if not topic.startswith("device/"):
            return None
        rest = topic[len("device/") :]
        # Expect "{serial}/report" or "{serial}/request" (possibly with suffixes).
        slash = rest.find("/")
        if slash <= 0:
            return None
        return rest[:slash]

    def set_bridge(self, bridge: "MQTTBridge | None") -> None:
        """Attach (or detach) the MQTT bridge that mirrors the target printer."""
        self._bridge = bridge

    async def _periodic_status_push(self) -> None:
        """Send periodic status updates to all connected clients (1 Hz, exact pre-bridge behaviour)."""
        logger.info("Starting periodic status push task")
        # Per-client push counters reset every 60 ticks. Lets us confirm from
        # logs whether the 1Hz push is actually reaching a specific slicer
        # connection (#1548 keepalive follow-up: keepalive parser shipped but
        # OrcaSlicer still disconnects on idle, and the periodic push is
        # otherwise silent at INFO level so it can't be observed in the
        # support bundle). One log line per minute per active connection —
        # nothing when no slicer is attached.
        push_counts: dict[str, int] = {}
        ticks_since_summary = 0
        while self._running:
            try:
                await asyncio.sleep(1)  # Push every 1 second like real printers
                ticks_since_summary += 1

                disconnected = []
                for client_id, writer in list(self._clients.items()):
                    try:
                        if writer.is_closing():
                            disconnected.append(client_id)
                            continue
                        serial = self._client_serials.get(client_id, self.serial)
                        # log_event=False: the 1Hz cached push is already
                        # captured by ``dump_wire`` snapshot mode (see
                        # _debug.py); appending it to the cmd.jsonl would
                        # flood the file ~60 lines/min per VP.
                        await self._send_status_report(writer, serial=serial, log_event=False)
                        push_counts[client_id] = push_counts.get(client_id, 0) + 1
                    except OSError as e:
                        logger.debug("Failed to push status to %s: %s", client_id, e)
                        disconnected.append(client_id)

                # Remove disconnected clients
                for client_id in disconnected:
                    self._clients.pop(client_id, None)
                    self._client_serials.pop(client_id, None)
                    push_counts.pop(client_id, None)

                if ticks_since_summary >= 60:
                    for cid, count in push_counts.items():
                        logger.info(
                            "%s1Hz status push: %d pushes/min to %s",
                            self._log_prefix,
                            count,
                            cid,
                        )
                    push_counts.clear()
                    ticks_since_summary = 0

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Periodic status push error: %s", e)

        logger.info("Periodic status push task stopped")

    async def push_raw_to_clients(self, topic: str, payload: bytes) -> None:
        """Publish a pre-serialized MQTT payload on `topic` to connected slicers.

        Called by MQTTBridge from the asyncio loop (scheduled via
        run_coroutine_threadsafe from paho's network thread).

        Routes the response only back to the originating slicer if the
        payload's sequence_id was previously recorded via
        ``_record_pending_request``. Falls back to fan-out for
        printer-initiated pushes (push_status etc.) and for sequence_ids
        we never saw (covers a slicer that subscribes mid-flight to a
        topic for which an earlier request is still in flight).
        """
        topic_bytes = topic.encode("utf-8")
        # MQTT remaining-length: 2-byte topic length prefix + topic + message body.
        remaining = 2 + len(topic_bytes) + len(payload)
        packet = bytearray([0x30])  # PUBLISH, QoS 0
        while True:
            byte = remaining % 128
            remaining //= 128
            if remaining > 0:
                byte |= 0x80
            packet.append(byte)
            if remaining == 0:
                break
        packet.extend([len(topic_bytes) >> 8, len(topic_bytes) & 0xFF])
        packet.extend(topic_bytes)
        packet.extend(payload)
        frame = bytes(packet)

        target_client_id = self._lookup_pending_request_client(payload)

        disconnected = []
        for client_id, writer in list(self._clients.items()):
            if target_client_id is not None and client_id != target_client_id:
                continue
            try:
                if writer.is_closing():
                    disconnected.append(client_id)
                    continue
                writer.write(frame)
                try:
                    await asyncio.wait_for(writer.drain(), timeout=5)
                except TimeoutError:
                    logger.debug("MQTT drain timeout pushing bridge frame to %s", client_id)
            except OSError as e:
                logger.debug("Failed to push bridge frame to %s: %s", client_id, e)
                disconnected.append(client_id)

        for client_id in disconnected:
            self._clients.pop(client_id, None)
            self._client_serials.pop(client_id, None)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Handle an MQTT client connection."""
        addr = writer.get_extra_info("peername")
        client_id = f"{addr[0]}:{addr[1]}" if addr else "unknown"
        logger.info("%sMQTT client connected: %s", self._log_prefix, client_id)

        authenticated = False
        # Per-packet read timeout. Before CONNECT we default to 60 s so a
        # client that opens TCP but never sends anything still gets reaped.
        # After CONNECT we drop the application-level read timeout entirely
        # and rely on TCP keepalive (SO_KEEPALIVE) to detect dead connections
        # — this matches real Bambu firmware, which does not enforce MQTT
        # spec §4.4's 1.5× idle disconnect (#1548 round 2). OrcaSlicer's
        # MQTT client on some platforms does not emit PINGREQ at all on idle
        # connections; the same install that stays connected to a real P1S
        # indefinitely was disconnecting from us at keepalive×1.5.
        read_timeout: float | None = 60.0

        try:
            while self._running:
                # Read MQTT fixed header
                try:
                    header = await asyncio.wait_for(reader.read(1), timeout=read_timeout)
                except TimeoutError:
                    break

                if not header:
                    break

                packet_type = (header[0] & 0xF0) >> 4

                # Read remaining length
                remaining_length = await self._read_remaining_length(reader)
                if remaining_length is None:
                    break

                # Read payload
                payload = await reader.read(remaining_length) if remaining_length > 0 else b""

                # Handle packet types
                if packet_type == 1:  # CONNECT
                    source_ip = addr[0] if addr else "unknown"
                    if self._is_auth_rate_limited(source_ip):
                        logger.warning(
                            "%sMQTT auth rate-limited from %s (>=%d failures in %ds)",
                            self._log_prefix,
                            source_ip,
                            _AUTH_RATE_LIMIT_MAX_ATTEMPTS,
                            int(_AUTH_RATE_LIMIT_WINDOW_SECONDS),
                        )
                        writer.write(bytes([0x20, 0x02, 0x00, 0x05]))  # Not authorized
                        await writer.drain()
                        break
                    authenticated, keep_alive = await self._handle_connect(payload, writer)
                    if not authenticated:
                        self._record_auth_failure(source_ip)
                        break
                    self._clear_auth_failures(source_ip)
                    # Drop the application-level read timeout; rely on
                    # SO_KEEPALIVE below for dead-connection detection.
                    # Real Bambu firmware does the same — accept any
                    # negotiated keepalive but never enforce §4.4's 1.5×
                    # disconnect on the otherwise-idle MQTT session
                    # (#1548 round 2). keep_alive is logged for support
                    # bundles but no longer drives a disconnect.
                    read_timeout = None
                    logger.info(
                        "%sMQTT client %s authenticated (negotiated keepalive=%ds, idle disconnect disabled)",
                        self._log_prefix,
                        client_id,
                        keep_alive,
                    )
                    # Enable TCP keepalive so a hard network drop is detected
                    # by the OS within a few minutes rather than waiting for
                    # the next outbound write to ECONNRESET.
                    #
                    # Also tighten the Linux keepalive schedule. Defaults are
                    # tcp_keepalive_time=7200 s (2 h before first probe),
                    # tcp_keepalive_intvl=75, tcp_keepalive_probes=9 — so a
                    # macOS client that goes to sleep silently is only
                    # detected as dead ~2 h 11 min later, and until then the
                    # push loop keeps stalling on drain-timeouts to the
                    # zombie socket. #1872: a P1S sleep/wake left the pre-
                    # sleep session in _clients for 5+ min with no eviction
                    # signal. New settings (idle=60 s, interval=15 s,
                    # count=4) detect a dead peer in ~2 min. `getattr` guards
                    # keep this cross-platform — macOS has TCP_KEEPINTVL but
                    # not TCP_KEEPIDLE (uses TCP_KEEPALIVE); other platforms
                    # silently skip.
                    sock = writer.get_extra_info("socket")
                    if sock is not None:
                        try:
                            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                        except OSError as e:
                            logger.debug("%sFailed to set SO_KEEPALIVE on %s: %s", self._log_prefix, client_id, e)
                        for opt_name, opt_value in (
                            ("TCP_KEEPIDLE", 60),
                            ("TCP_KEEPINTVL", 15),
                            ("TCP_KEEPCNT", 4),
                        ):
                            opt = getattr(socket, opt_name, None)
                            if opt is None:
                                continue
                            try:
                                sock.setsockopt(socket.IPPROTO_TCP, opt, opt_value)
                            except OSError as e:
                                logger.debug(
                                    "%sFailed to set %s=%s on %s: %s",
                                    self._log_prefix,
                                    opt_name,
                                    opt_value,
                                    client_id,
                                    e,
                                )
                    # Register client for periodic status pushes; start with
                    # self.serial as the fallback until we learn the slicer's
                    # preferred serial from the first SUBSCRIBE/PUBLISH.
                    self._clients[client_id] = writer
                    self._client_serials[client_id] = self.serial
                elif packet_type == 3:  # PUBLISH
                    if authenticated:
                        await self._handle_publish(header[0], payload, writer, client_id)
                elif packet_type == 8:  # SUBSCRIBE
                    if authenticated:
                        await self._handle_subscribe(payload, writer, client_id)
                elif packet_type == 12:  # PINGREQ
                    # Send PINGRESP
                    writer.write(bytes([0xD0, 0x00]))
                    await writer.drain()
                elif packet_type == 14:  # DISCONNECT
                    break

        except asyncio.CancelledError:
            pass  # Expected when server is shutting down and cancels client tasks
        except Exception as e:
            # Outer handler — inner handlers already absorb expected parser
            # / IO failures at debug. Anything reaching here is unexpected
            # and would otherwise silently drop the slicer connection with
            # no actionable signal in production logs (defaults are INFO+).
            logger.warning("%sMQTT client session error from %s: %s", self._log_prefix, client_id, e)
        finally:
            logger.debug("MQTT client disconnected: %s", client_id)
            self._clients.pop(client_id, None)
            self._client_serials.pop(client_id, None)
            try:
                writer.close()
                await writer.wait_closed()
            except OSError:
                pass  # Best-effort socket cleanup on client disconnect

    async def _read_remaining_length(self, reader: asyncio.StreamReader) -> int | None:
        """Read MQTT remaining length (variable byte integer)."""
        multiplier = 1
        value = 0

        for _ in range(4):
            try:
                byte = await reader.read(1)
                if not byte:
                    return None
                encoded = byte[0]
                value += (encoded & 127) * multiplier
                if (encoded & 128) == 0:
                    return value
                multiplier *= 128
            except OSError:
                return None

        return None

    def _record_pending_request(self, data: dict, client_id: str) -> None:
        """Stash sequence_id → client_id for any nested block with a sequence_id.

        Slicer commands typically wrap their seq id in ``{"print": {...}}`` or
        ``{"info": {...}}`` / ``{"system": {...}}`` etc. Walks top-level dict
        values once to find the seq id; if absent (some commands omit it) we
        skip — the response will fall through to broadcast which is fine for
        unsolicited pushes.
        """
        for block in data.values():
            if isinstance(block, dict):
                seq = block.get("sequence_id")
                if seq is not None:
                    key = str(seq)
                    # Evict oldest entry when over the cap. Python dicts
                    # preserve insertion order so iter(self._pending_requests)
                    # yields the oldest key first.
                    while len(self._pending_requests) >= _PENDING_REQUEST_MAX_ENTRIES:
                        oldest = next(iter(self._pending_requests))
                        self._pending_requests.pop(oldest, None)
                    self._pending_requests[key] = client_id
                    return

    def _lookup_pending_request_client(self, payload: bytes) -> str | None:
        """Parse a bridge-forwarded MQTT payload and return the originating
        client_id if its sequence_id was recorded.

        Returns ``None`` for printer-initiated pushes (no recorded seq id) so
        push_raw_to_clients falls back to broadcast — required for push_status
        and the other unsolicited pushes that every connected slicer expects.
        """
        try:
            parsed = json.loads(payload)
        except (ValueError, TypeError):
            return None
        if not isinstance(parsed, dict):
            return None
        for block in parsed.values():
            if isinstance(block, dict):
                seq = block.get("sequence_id")
                if seq is not None:
                    return self._pending_requests.pop(str(seq), None)
        return None

    def _is_auth_rate_limited(self, source_ip: str) -> bool:
        """Return True if ``source_ip`` has hit the per-IP failure cap.

        Prunes timestamps older than the window so the dict doesn't grow
        unbounded. Uses ``time.monotonic()`` for a wall-clock-jump-immune
        clock that's safe to call from any context (sync or async).
        """
        import time as _time

        now = _time.monotonic()
        window_start = now - _AUTH_RATE_LIMIT_WINDOW_SECONDS
        recent = [t for t in self._auth_failures.get(source_ip, []) if t >= window_start]
        if recent:
            self._auth_failures[source_ip] = recent
        else:
            self._auth_failures.pop(source_ip, None)
        return len(recent) >= _AUTH_RATE_LIMIT_MAX_ATTEMPTS

    def _record_auth_failure(self, source_ip: str) -> None:
        """Append a timestamp for ``source_ip``'s failed auth attempt."""
        import time as _time

        now = _time.monotonic()
        self._auth_failures.setdefault(source_ip, []).append(now)

    def _clear_auth_failures(self, source_ip: str) -> None:
        """Reset ``source_ip``'s failure history after a successful auth."""
        self._auth_failures.pop(source_ip, None)

    async def _handle_connect(self, payload: bytes, writer: asyncio.StreamWriter) -> tuple[bool, int]:
        """Handle MQTT CONNECT packet.

        Returns ``(authenticated, keep_alive_seconds)`` — the second element
        is the value the client advertised in CONNECT, so the caller's
        read-loop can honour it instead of the hardcoded default. ``0``
        means the client opted out of keepalive (#1548).
        """
        try:
            # Parse CONNECT packet
            # Skip protocol name length and name
            idx = 0
            proto_len = (payload[idx] << 8) | payload[idx + 1]
            idx += 2 + proto_len

            # Skip protocol level and connect flags
            # connect_flags = payload[idx + 1]
            idx += 2

            # Keepalive (2-byte big-endian, seconds). Honoured by the read
            # loop in `_handle_client` per MQTT spec §3.1.2.10 / §4.4 —
            # before #1548 we ignored this and used a hardcoded 60 s, which
            # closed OrcaSlicer's idle connection at exactly the negotiated
            # keepalive boundary instead of the spec-mandated 1.5×.
            keep_alive = (payload[idx] << 8) | payload[idx + 1]
            idx += 2

            # Read client ID
            client_id_len = (payload[idx] << 8) | payload[idx + 1]
            idx += 2
            # client_id = payload[idx : idx + client_id_len].decode("utf-8")
            idx += client_id_len

            # Read username
            username_len = (payload[idx] << 8) | payload[idx + 1]
            idx += 2
            username = payload[idx : idx + username_len].decode("utf-8")
            idx += username_len

            # Read password
            password_len = (payload[idx] << 8) | payload[idx + 1]
            idx += 2
            password = payload[idx : idx + password_len].decode("utf-8")

            # Authenticate. ``hmac.compare_digest`` is constant-time to keep
            # the auth check from leaking the access code via response timing
            # under network jitter — LAN-only threat is marginal, but it's
            # the standard fix and costs nothing.
            if username == "bblp" and hmac.compare_digest(password, self.access_code):
                # Send CONNACK with success
                writer.write(bytes([0x20, 0x02, 0x00, 0x00]))
                await writer.drain()
                logger.info("%sMQTT client authenticated successfully", self._log_prefix)

                # Send immediate status report after auth - slicer expects this
                await self._send_status_report(writer)
                return True, keep_alive
            else:
                # Send CONNACK with auth failure
                writer.write(bytes([0x20, 0x02, 0x00, 0x05]))  # Not authorized
                await writer.drain()
                logger.warning("%sMQTT auth failed for user '%s' (access code mismatch)", self._log_prefix, username)
                return False, 0

        except (IndexError, ValueError) as e:
            logger.debug("MQTT CONNECT parse error: %s", e)
            # Send CONNACK with error
            writer.write(bytes([0x20, 0x02, 0x00, 0x02]))  # Protocol error
            await writer.drain()
            return False, 0

    async def _handle_subscribe(self, payload: bytes, writer: asyncio.StreamWriter, client_id: str) -> None:
        """Handle MQTT SUBSCRIBE packet."""
        try:
            # Parse packet ID
            packet_id = (payload[0] << 8) | payload[1]

            # Parse topic filters (just acknowledge them)
            idx = 2
            granted_qos = []
            learned_serial: str | None = None
            while idx < len(payload):
                topic_len = (payload[idx] << 8) | payload[idx + 1]
                idx += 2
                topic = payload[idx : idx + topic_len].decode("utf-8")
                idx += topic_len
                requested_qos = payload[idx]
                idx += 1

                logger.info("%sMQTT subscribe: %s QoS=%s", self._log_prefix, topic, requested_qos)
                granted_qos.append(min(requested_qos, 1))  # Grant up to QoS 1

                # Remember the serial the slicer is listening on so status/version
                # responses go to a topic it actually subscribed to.
                if learned_serial is None:
                    extracted = self._extract_serial_from_topic(topic)
                    if extracted:
                        learned_serial = extracted

            if learned_serial and learned_serial != self._client_serials.get(client_id):
                if learned_serial != self.serial:
                    logger.info(
                        "%sMQTT client subscribed with serial %s (VP serial is %s) — adapting responses",
                        self._log_prefix,
                        learned_serial,
                        self.serial,
                    )
                self._client_serials[client_id] = learned_serial

            # Send SUBACK
            suback = bytes([0x90, 2 + len(granted_qos), packet_id >> 8, packet_id & 0xFF])
            suback += bytes(granted_qos)
            writer.write(suback)
            await writer.drain()

            # Send initial status report after subscribe on the client's subscribed topic
            await self._send_status_report(writer, serial=self._client_serials.get(client_id, self.serial))

        except (IndexError, ValueError, OSError) as e:
            logger.debug("MQTT SUBSCRIBE error: %s", e)

    async def _send_status_report(
        self, writer: asyncio.StreamWriter, serial: str | None = None, log_event: bool = True
    ) -> None:
        """Send a status report to the slicer after connection.

        When a bridge is active and has cached the real printer's latest
        push_status, send a copy of the real push with only the upload-state-
        machine fields we own (gcode_state, gcode_file, prepare_percent,
        subtask_name) overridden. BambuStudio's Send pre-flight checks the
        push_status shape against what it expects from the printer model, and
        the synthetic stub introduced fields the real H2D doesn't have (storage,
        the wrong chamber_temper shape, etc.) which trip the check.

        While the target printer is actually printing and the VP has no upload
        handshake of its own in flight, the live-progress fields are mirrored
        through instead of zeroed, under a forced gcode_state=FINISH — see
        ``_mirroring_live_progress`` for why that specific state (#1887).
        """
        try:
            self._sequence_id += 1

            cached = self._bridge.get_latest_print_state() if self._bridge is not None else None
            if isinstance(cached, dict):
                # Real-printer-shaped response. Copy the cache, then replace the
                # protocol / upload-state fields with values under our control.
                # Deep copy — current mutations are top-level only, but a future
                # override that writes into a nested dict (e.g. ``online``,
                # ``upgrade_state``, ``ipcam``) would otherwise corrupt the
                # bridge cache and be read by every subsequent subscriber until
                # the next real-printer push lands. Cost is one allocation per
                # status report; the cached dict is already short-lived.
                print_block = copy.deepcopy(cached)
                print_block["sequence_id"] = str(self._sequence_id)
                print_block["command"] = "push_status"
                print_block["msg"] = 0
                mirroring = self._mirroring_live_progress(cached)
                if mirroring:
                    # gcode_file / subtask_name / the progress fields stay as the
                    # printer reported them — the slicer renders what is really
                    # on the bed. FINISH keeps the Send button enabled.
                    print_block["gcode_state"] = "FINISH"
                    print_block["gcode_file_prepare_percent"] = "100"
                    print_block.setdefault("subtask_name", "")
                else:
                    print_block["gcode_state"] = self._gcode_state
                    print_block["gcode_file"] = self._current_file
                    print_block["gcode_file_prepare_percent"] = self._prepare_percent
                    if self._current_file:
                        print_block["subtask_name"] = self._current_file.replace(".3mf", "")
                    else:
                        # Don't override real subtask_name with empty if no upload pending.
                        print_block.setdefault("subtask_name", "")
                # Storage-availability indicators the slicer's "Send" pre-flight reads
                # (#1228). P1S/A1-class firmware doesn't always include these in
                # push_status (no SD card inserted, older field shapes), and BambuStudio
                # rejects the send pre-flight with the generic "storage needs to be
                # inserted before send to printer" error before even attempting FTP.
                # For VP usage the slicer uploads via FTPS to Bambuddy's filesystem —
                # the printer's actual SD/storage state is irrelevant on that path.
                # Force "available" indicators so the pre-flight passes regardless of
                # what the real printer reports. Restores the 0.2.3.2 synthetic-stub
                # behaviour for these fields without losing the live AMS / k-profile /
                # camera mirror cached-as-base provides.
                print_block["home_flag"] = print_block.get("home_flag", 0) | 0x100  # bit 8 = HAS_SDCARD_NORMAL
                print_block["sdcard"] = True
                print_block.setdefault("storage", {"free": 1_000_000_000, "total": 32_000_000_000})
                # Live-progress fields the slicer's Send pre-flight reads
                # (#1558). When the VP reports itself idle, these have to read
                # idle too: the cached push_status carries the printer's real
                # values, and a report that says gcode_state=IDLE while
                # mc_percent>0 / stg_cur>0 is internally contradictory — the
                # slicer takes it as busy and blocks Send. Same shape as #1228
                # (storage). When the mirror is on, gcode_state=FINISH agrees
                # with a non-zero progress set, so they pass through instead.
                if not mirroring:
                    print_block["mc_print_stage"] = ""
                    print_block["mc_percent"] = 0
                    print_block["mc_remaining_time"] = 0
                    print_block["stg"] = []
                    print_block["stg_cur"] = 0
                    print_block["layer_num"] = 0
                    print_block["total_layer_num"] = 0
                # print_error is never mirrored: StatusPanel raises a modal error
                # dialog for a non-zero code, and the VP is not the machine that
                # threw it — the user's own printer card in Bambuddy reports the
                # fault. Zero it in both branches.
                print_block["print_error"] = 0
                status = {"print": print_block}
                dump_wire(self.vp_name, "out", status)
                await self._publish_to_report(writer, status, serial or self.serial, log_event=log_event)
                return

            # No bridge / no cache yet — fall back to the synthetic stub.
            status = {
                "print": {
                    "sequence_id": str(self._sequence_id),
                    "command": "push_status",
                    "msg": 0,
                    "gcode_state": self._gcode_state,
                    "gcode_file": self._current_file,
                    "gcode_file_prepare_percent": self._prepare_percent,
                    "subtask_name": self._current_file.replace(".3mf", "") if self._current_file else "",
                    "mc_print_stage": "",
                    "mc_percent": 0,
                    "mc_remaining_time": 0,
                    "wifi_signal": "-44dBm",
                    "print_error": 0,
                    "print_type": "",
                    "bed_temper": 25.0,
                    "bed_target_temper": 0.0,
                    "nozzle_temper": 25.0,
                    "nozzle_target_temper": 0.0,
                    "chamber_temper": 25.0,
                    "cooling_fan_speed": "0",
                    "big_fan1_speed": "0",
                    "big_fan2_speed": "0",
                    "heatbreak_fan_speed": "0",
                    "spd_lvl": 1,
                    "spd_mag": 100,
                    "stg": [],
                    "stg_cur": 0,
                    "layer_num": 0,
                    "total_layer_num": 0,
                    "home_flag": 256,  # Bit 8 = SD card present (HAS_SDCARD_NORMAL)
                    "hw_switch_state": 0,
                    "online": {"ahb": False, "rfid": False, "version": 7},
                    "ams_status": 0,
                    "sdcard": True,
                    "storage": {"free": 1000000000, "total": 32000000000},
                    "upgrade_state": {
                        "sequence_id": 0,
                        "progress": "",
                        "status": "",
                        "consistency_request": False,
                        "dis_state": 0,
                        "err_code": 0,
                        "force_upgrade": False,
                        "message": "",
                        "module": "",
                        "new_version_state": 2,
                        "new_ver_list": [],
                        "ota_new_version_number": "",
                        "ahb_new_version_number": "",
                    },
                    "ipcam": {
                        "ipcam_dev": "1",
                        "ipcam_record": "enable",
                        "timelapse": "disable",
                        "resolution": "1080p",
                        "mode_bits": 0,
                    },
                    "xcam": {
                        "allow_skip_parts": False,
                        "buildplate_marker_detector": True,
                        "first_layer_inspector": True,
                        "halt_print_sensitivity": "medium",
                        "print_halt": True,
                        "printing_monitor": True,
                        "spaghetti_detector": True,
                    },
                    "lights_report": [{"node": "chamber_light", "mode": "on"}],
                    "nozzle_diameter": "0.4",
                    "nozzle_type": "hardened_steel",
                }
            }

            await self._publish_to_report(writer, status, serial or self.serial, log_event=log_event)

        except OSError as e:
            logger.error("Failed to send status report: %s", e)

    async def _send_version_response(
        self, writer: asyncio.StreamWriter, sequence_id: str, serial: str | None = None
    ) -> None:
        """Send version info response to the slicer."""
        try:
            product_name = MODEL_PRODUCT_NAMES.get(self.model, self.model or "X1 Carbon")
            # The serial is embedded inside the module[].sn fields *and* used as
            # the report topic. Use the client's effective serial so the slicer
            # sees internal/topic consistency even when it differs from self.serial.
            serial = serial or self.serial

            # Build version response matching OrcaSlicer expectations
            # Required fields per module: name, product_name, sw_ver, sw_new_ver, sn, hw_ver, flag
            version_info = {
                "info": {
                    "command": "get_version",
                    "sequence_id": sequence_id,
                    "module": [
                        {
                            "name": "ota",
                            "product_name": product_name,
                            "sw_ver": "01.07.00.00",
                            "sw_new_ver": "",
                            "hw_ver": "OTA",
                            "sn": serial,
                            "flag": 0,
                        },
                        {
                            "name": "esp32",
                            "product_name": product_name,
                            "sw_ver": "01.07.22.25",
                            "sw_new_ver": "",
                            "hw_ver": "AP05",
                            "sn": serial,
                            "flag": 0,
                        },
                        {
                            "name": "rv1126",
                            "product_name": product_name,
                            "sw_ver": "00.00.27.38",
                            "sw_new_ver": "",
                            "hw_ver": "AP05",
                            "sn": serial,
                            "flag": 0,
                        },
                        {
                            "name": "th",
                            "product_name": product_name,
                            "sw_ver": "00.00.04.00",
                            "sw_new_ver": "",
                            "hw_ver": "TH07",
                            "sn": serial,
                            "flag": 0,
                        },
                        {
                            "name": "mc",
                            "product_name": product_name,
                            "sw_ver": "00.00.10.00",
                            "sw_new_ver": "",
                            "hw_ver": "MC07",
                            "sn": serial,
                            "flag": 0,
                        },
                    ],
                }
            }

            # Overlay real version modules from the bridge cache when available
            # (specifically the AMS modules ams/0, n3f/0, n3s/128 etc. that
            # BambuStudio's Prepare tab uses to identify AMS hardware — without
            # them every AMS unit shows as "unknown" in the Prepare panel).
            if self._bridge is not None:
                cached_modules = self._bridge.get_latest_version_modules()
                if isinstance(cached_modules, list) and cached_modules:
                    version_info["info"]["module"] = cached_modules

            await self._publish_to_report(writer, version_info, serial)
            logger.info("Sent version response (product_name=%s)", product_name)

        except OSError as e:
            logger.error("Failed to send version response: %s", e)

    def set_gcode_state(self, state: str, filename: str = "", prepare_percent: str = "0") -> None:
        """Update the gcode state reported to connected slicers.

        Called by the manager to reflect FTP upload progress/completion.
        """
        self._gcode_state = state
        self._current_file = filename
        self._prepare_percent = prepare_percent
        self._state_changed_at = time.monotonic()

    def _mirroring_live_progress(self, cached: dict) -> bool:
        """True when the report should carry the target printer's live progress.

        The slicers gate the Device-tab progress panel and the Send button on
        the same predicate (``MachineObject::is_in_printing()``), so the VP
        cannot report the printer's real gcode_state without also telling the
        slicer it is too busy to accept a job — which is the whole point of a
        non-proxy VP, and was the #1558 regression. Reporting FINISH instead
        renders the panel (StatusPanel checks ``is_in_printing() ||
        print_status == "FINISH"``) and leaves Send enabled, so the mirror is
        FINISH plus the printer's real numbers.

        Two things suppress it:

        * The VP's own upload state machine owns the report while a job is
          being handed over (PREPARE), and for a short settle window after —
          the slicer only releases its in-flight-job lock once it sees FINISH
          carrying the ``subtask_name`` it just uploaded (#1280 / #1658), and
          swapping in the printer's filename mid-handshake wedges the send
          modal at "Downloading".
        * The printer isn't printing, in which case there is no progress to
          show and the VP's own state is the honest thing to report.
        """
        if self._gcode_state == "PREPARE":
            return False
        if time.monotonic() - self._state_changed_at < _UPLOAD_SETTLE_SECONDS:
            return False
        return str(cached.get("gcode_state") or "").upper() in _MIRRORED_PRINT_STATES

    async def _publish_to_report(
        self, writer: asyncio.StreamWriter, payload: dict, serial: str = "", log_event: bool = True
    ) -> None:
        """Publish a message on the device report topic.

        Real Bambu printers wire-format push_status JSON with 4-space indentation
        (32254 bytes for an idle H2D push vs 14268 bytes compact). BambuStudio's
        Send pre-flight rejects compact JSON — without matching the on-wire
        format the slicer never proceeds to FTP upload.

        ``log_event=True`` records the publish in ``vp_wire/<vp>_cmd.jsonl``
        under the ``bridge_to_slicer`` direction so #1622-style triages can
        diff the bridge's own outbound replies (info.get_version answer,
        project_file ack, on-demand pushall response) against the real
        printer's ``printer_to_slicer`` forwards. The 1Hz periodic push
        sets ``log_event=False`` because dump_wire's overwrite-snapshot
        already covers cache shape and a per-second JSONL line would dwarf
        the actual command events.
        """
        topic = f"device/{serial or self.serial}/report"
        message = json.dumps(payload, indent=4)

        topic_bytes = topic.encode("utf-8")
        message_bytes = message.encode("utf-8")

        remaining = 2 + len(topic_bytes) + len(message_bytes)
        packet = bytes([0x30])  # PUBLISH, QoS 0

        while remaining > 0:
            byte = remaining % 128
            remaining //= 128
            if remaining > 0:
                byte |= 0x80
            packet += bytes([byte])

        packet += bytes([len(topic_bytes) >> 8, len(topic_bytes) & 0xFF])
        packet += topic_bytes
        packet += message_bytes

        if log_event:
            # Env-flagged command trace (#1622): captures bridge-synthesised
            # replies (info.get_version, project_file ack, on-demand pushall
            # response) AFTER the payload is finalised but before it hits
            # the wire — so the cmd.jsonl reflects exactly what the slicer
            # parses. Pair with the slicer_to_bridge events from
            # _handle_publish and the printer_to_slicer fan-outs from
            # mqtt_bridge.
            append_event(self.vp_name, "bridge_to_slicer", topic, payload)

        writer.write(packet)
        # Timeout the drain to prevent blocking the event loop if the
        # MQTT client stops reading (e.g. slicer busy with FTP upload,
        # macOS suspends the client mid-session — #1872).
        #
        # On timeout, close the writer and raise BrokenPipeError so the
        # push-loop's ``except OSError`` at ``_periodic_status_push``
        # evicts the client from ``self._clients`` on this same tick.
        # Before this, timeouts logged at DEBUG and returned silently,
        # so the zombie writer sat in ``self._clients`` until SO_KEEPALIVE
        # detected the dead peer (~2 h on Linux defaults). That kept the
        # push loop spending 5 s per iteration on the stalled client and
        # left the slicer's UI unaware the session was gone.
        try:
            await asyncio.wait_for(writer.drain(), timeout=5)
        except TimeoutError as e:
            logger.info(
                "%sMQTT drain timeout for %s — closing stalled writer",
                self._log_prefix,
                topic,
            )
            try:
                writer.close()
            except Exception:
                pass  # best-effort — writer may already be broken
            raise BrokenPipeError(f"drain timeout on {topic}") from e

    async def _send_print_response(
        self, writer: asyncio.StreamWriter, sequence_id: str, filename: str, serial: str | None = None
    ) -> None:
        """Send project_file acknowledgment matching real Bambu printer behavior."""
        # Update state so periodic status pushes reflect preparation. Goes
        # through set_gcode_state so the live-progress mirror sees the
        # transition and holds off until the upload handshake has settled.
        self.set_gcode_state("PREPARE", filename=filename, prepare_percent="0")

        try:
            # Send command acknowledgment — slicer expects to see
            # command: "project_file" echoed back before starting FTP upload
            subtask_name = filename.replace(".3mf", "") if filename else ""
            response = {
                "print": {
                    "command": "project_file",
                    "sequence_id": sequence_id,
                    "param": "Metadata/plate_1.gcode",
                    "subtask_name": subtask_name,
                    "gcode_state": "PREPARE",
                    "gcode_file": filename,
                    "gcode_file_prepare_percent": "0",
                    "result": "SUCCESS",
                    "msg": 0,
                }
            }
            await self._publish_to_report(writer, response, serial or self.serial)
            logger.info("Sent project_file acknowledgment for %s", filename)
        except OSError as e:
            logger.error("Failed to send print response: %s", e)

    async def _handle_publish(self, header: int, payload: bytes, writer: asyncio.StreamWriter, client_id: str) -> None:
        """Handle MQTT PUBLISH packet."""
        try:
            # Parse topic
            idx = 0
            topic_len = (payload[idx] << 8) | payload[idx + 1]
            idx += 2
            topic = payload[idx : idx + topic_len].decode("utf-8")
            idx += topic_len

            # Check for packet ID (QoS > 0)
            qos = (header & 0x06) >> 1
            if qos > 0:
                # packet_id = (payload[idx] << 8) | payload[idx + 1]
                idx += 2

            # Parse message
            message = payload[idx:].decode("utf-8")

            logger.info("MQTT publish to %s: %s...", topic, message[:100])

            # Only handle publishes on *some* device/.../request topic. The
            # serial is taken from the topic rather than compared against
            # self.serial: the client is already authenticated via the access
            # code, and Orca/BambuStudio may have a cached serial that differs
            # from the VP's computed self.serial (#927). Use the topic's serial
            # for all responses so they land on the topic the slicer subscribed
            # to.
            if not topic.startswith("device/") or "/request" not in topic:
                return

            client_serial = self._extract_serial_from_topic(topic) or self.serial
            if client_serial and client_serial != self._client_serials.get(client_id):
                if client_serial != self.serial:
                    logger.info(
                        "%sMQTT client publishing with serial %s (VP serial is %s) — adapting responses",
                        self._log_prefix,
                        client_serial,
                        self.serial,
                    )
                self._client_serials[client_id] = client_serial

            try:
                # Some slicer builds (observed with OrcaSlicer on Linux, #927)
                # include the C-string null terminator in the MQTT payload
                # length, so the decoded message ends with \x00. Real brokers
                # pass the bytes through; strict json.loads raises "Extra data"
                # and every pushall/get_version/project_file silently dropped.
                data = json.loads(message.rstrip("\x00 \r\n\t"))
            except json.JSONDecodeError as e:
                logger.debug(
                    "MQTT publish JSON decode failed: %s (payload=%r)",
                    e,
                    message[:200],
                )
                return

            # Env-flagged command trace (#1622): every slicer-originated publish
            # gets a line in vp_wire/<vp>_cmd.jsonl alongside the printer-side
            # responses captured in mqtt_bridge. Off by default.
            append_event(self.vp_name, "slicer_to_bridge", topic, data)

            # The synthetic flow below is the original (pre-bridge) behaviour and is
            # what the proven-working FTP "Send" depends on. Do NOT replace any
            # synthetic response with a forward — only ADD forwarding alongside,
            # at the bottom, for commands the synthetic flow doesn't handle
            # (AMS write / xcam / system / etc., which need to actually reach
            # the real printer).

            handled_locally = False

            # Handle pushing command (status request)
            if "pushing" in data:
                pushing_data = data["pushing"]
                command = pushing_data.get("command", "")
                logger.info("MQTT pushing command: %s", command)

                if command == "pushall":
                    logger.info("Sending status report in response to pushall")
                    await self._send_status_report(writer, serial=client_serial)
                    handled_locally = True
                elif command == "start":
                    logger.info("Starting status push stream")
                    await self._send_status_report(writer, serial=client_serial)
                    handled_locally = True

            # Handle info commands (get_version, etc.)
            if "info" in data:
                info_data = data["info"]
                command = info_data.get("command", "")
                sequence_id = info_data.get("sequence_id", "0")
                logger.info("MQTT info command: %s", command)

                if command == "get_version":
                    await self._send_version_response(writer, sequence_id, serial=client_serial)
                    handled_locally = True

            # Handle print commands
            if "print" in data:
                print_data = data["print"]
                command = print_data.get("command", "")
                filename = print_data.get("subtask_name", "")
                sequence_id = print_data.get("sequence_id", "0")

                logger.info("MQTT print command: %s for %s", command, filename)

                if command in ("project_file", "gcode_file"):
                    # File lives on Bambuddy, not the printer — synthetic only.
                    file_3mf = print_data.get("file", filename)
                    await self._send_print_response(writer, sequence_id, file_3mf, serial=client_serial)
                    if self.on_print_command:
                        # `filename` is the slicer's `subtask_name` (bare model
                        # name, no extension). Pass it through verbatim — the
                        # `_schedule_finish_release` chain echoes it back as
                        # gcode_file + subtask_name in push_status, and the
                        # slicer matches against its own subtask_name there.
                        # The FTP filename (with extension) is in print_data
                        # under "file" for the queue-stash side to use as its
                        # own key matching `_add_to_print_queue`'s lookup.
                        await self._notify_print_command(filename, print_data)
                    handled_locally = True

            # Forward anything the synthetic flow didn't handle to the real
            # printer. AMS load / dry / xcam / system / extrusion_cali_get etc.
            if not handled_locally and self._bridge is not None and self._bridge.is_active:
                # Remember which client originated this command so the
                # printer's response goes back only to them (not fanned
                # out to every connected slicer).
                self._record_pending_request(data, client_id)
                self._bridge.forward_to_printer(data)

        except (IndexError, ValueError, OSError) as e:
            logger.debug("MQTT PUBLISH error: %s", e)

    async def _notify_print_command(self, filename: str, data: dict) -> None:
        """Notify callback of print command."""
        if self.on_print_command:
            try:
                result = self.on_print_command(filename, data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error("Print command callback error: %s", e)
