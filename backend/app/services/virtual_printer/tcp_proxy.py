"""Proxy for slicer-to-printer communication.

This module provides both transparent TCP proxying and TLS-terminating
proxying for forwarding data between a slicer and a real Bambu printer,
enabling remote printing over any network connection.

Most protocols (FTP, FileTransfer, Camera) use transparent TCP proxying —
raw bytes are forwarded without decryption, preserving end-to-end TLS
between slicer and printer. Only MQTT is TLS-terminated so Bambuddy can
rewrite the printer's real IP with the proxy's bind IP in MQTT payloads.
"""

# ruff: noqa: N801

import asyncio
import logging
import re
import ssl
import subprocess
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


def detect_port_redirect(port: int) -> int | None:
    """Detect if iptables redirects a port to another port.

    When iptables NAT REDIRECT rules exist (e.g. port redirects), connections
    to the original port never reach our socket because iptables intercepts
    them in PREROUTING. We must listen on the redirect target instead.

    Returns the redirect target port, or None if no redirect is active.
    """
    # Method 1: Read persistent rules file (doesn't require root)
    for rules_path in ("/etc/iptables/rules.v4", "/etc/iptables.rules"):
        try:
            with open(rules_path) as f:
                content = f.read()
            match = re.search(rf"--dport {port}\b.*?--to-ports\s+(\d+)", content)
            if match:
                target = int(match.group(1))
                if target != port:
                    return target
        except (FileNotFoundError, PermissionError, OSError):
            continue

    # Method 2: Query live iptables rules (may require root)
    try:
        result = subprocess.run(  # noqa: S603, S607
            ["iptables-save", "-t", "nat"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            match = re.search(rf"--dport {port}\b.*?--to-ports\s+(\d+)", result.stdout)
            if match:
                target = int(match.group(1))
                if target != port:
                    return target
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    return None


class TLSProxy:
    """TLS terminating proxy that forwards data between client and target.

    This proxy terminates TLS on both ends, allowing the slicer to connect
    to Bambuddy's certificate while Bambuddy connects to the real printer.
    """

    def __init__(
        self,
        name: str,
        listen_port: int,
        target_host: str,
        target_port: int,
        server_cert_path: Path,
        server_key_path: Path,
        on_connect: Callable[[str], None] | None = None,
        on_disconnect: Callable[[str], None] | None = None,
        bind_address: str = "0.0.0.0",  # nosec B104
        rewrite_ip: tuple[str, str] | None = None,
    ):
        """Initialize the TLS proxy.

        Args:
            name: Friendly name for logging (e.g., "FTP", "MQTT")
            listen_port: Port to listen on for incoming connections
            target_host: Target printer IP/hostname
            target_port: Target printer port
            server_cert_path: Path to server certificate (for accepting slicer connections)
            server_key_path: Path to server private key
            on_connect: Optional callback when client connects (receives client_id)
            on_disconnect: Optional callback when client disconnects (receives client_id)
            bind_address: IP address to bind to (default: all interfaces)
            rewrite_ip: Optional (old_ip, new_ip) tuple — replaces occurrences of
                the printer's real IP with the proxy's bind IP in printer→client data.
                This prevents the slicer from discovering the printer's real IP
                in MQTT payloads (ip_addr, rtsp_url, etc.) and bypassing the proxy.
        """
        self.name = name
        self.listen_port = listen_port
        self.target_host = target_host
        self.target_port = target_port
        self.server_cert_path = server_cert_path
        self.server_key_path = server_key_path
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.bind_address = bind_address

        # IP rewriting for printer→client direction
        if rewrite_ip:
            self._rewrite_old = rewrite_ip[0].encode("utf-8")
            self._rewrite_new = rewrite_ip[1].encode("utf-8")
            # Also rewrite the integer IP in net.info[].ip fields.
            # Bambu printers encode their IP as a little-endian uint32 integer
            # in the JSON payload. BambuStudio reads this to set dev_ip.
            self._rewrite_old_int = self._ip_to_le_int_bytes(rewrite_ip[0])
            self._rewrite_new_int = self._ip_to_le_int_bytes(rewrite_ip[1])
        else:
            self._rewrite_old = None
            self._rewrite_new = None
            self._rewrite_old_int = None
            self._rewrite_new_int = None

        self._server: asyncio.Server | None = None
        self._running = False
        self._active_connections: dict[str, tuple[asyncio.Task, asyncio.Task]] = {}
        self._server_ssl_context: ssl.SSLContext | None = None
        self._client_ssl_context: ssl.SSLContext | None = None

    @staticmethod
    def _ip_to_le_int_bytes(ip: str) -> bytes:
        """Convert an IP address to its little-endian integer JSON representation.

        E.g. "192.168.255.16" → b"285190336" (the integer as a decimal string,
        as it appears in Bambu MQTT JSON payloads in the net.info[].ip field).
        """
        import struct as _struct

        parts = ip.split(".")
        packed = bytes(int(p) for p in parts)
        le_int = _struct.unpack("<I", packed)[0]
        return str(le_int).encode("utf-8")

    def _create_server_ssl_context(self) -> ssl.SSLContext:
        """Create SSL context for accepting client (slicer) connections."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.server_cert_path, self.server_key_path)
        # Allow older TLS versions for compatibility with slicers
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        # Don't require client certificates
        ctx.verify_mode = ssl.CERT_NONE
        # Match real Bambu printer cipher behaviour on the slicer-facing side
        # as well (the #620 fix only patched the printer-facing client context
        # below). On hardened distros where OpenSSL `DEFAULT` strips the
        # plain-RSA AES-GCM suites, the slicer's ClientHello has no overlap
        # with our server's offered suites and the handshake fails before any
        # data flows (#1610 audit).
        ctx.set_ciphers("DEFAULT:AES256-GCM-SHA384:AES128-GCM-SHA256")
        return ctx

    def _create_client_ssl_context(self) -> ssl.SSLContext:
        """Create SSL context for connecting to printer."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # Don't verify printer's certificate (self-signed)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        # Bambu printers use plain RSA key exchange (no ECDHE/DHE),
        # which modern OpenSSL 3.x defaults exclude. Add them back.
        ctx.set_ciphers("DEFAULT:AES256-GCM-SHA384:AES128-GCM-SHA256")
        return ctx

    async def start(self) -> None:
        """Start the TLS proxy server."""
        if self._running:
            return

        logger.info(
            f"Starting {self.name} TLS proxy: {self.bind_address}:{self.listen_port} → {self.target_host}:{self.target_port}"
        )

        try:
            self._running = True

            # Create SSL contexts
            self._server_ssl_context = self._create_server_ssl_context()
            self._client_ssl_context = self._create_client_ssl_context()

            # Start server with TLS
            self._server = await asyncio.start_server(
                self._handle_client,
                self.bind_address,
                self.listen_port,
                ssl=self._server_ssl_context,
            )

            logger.info("%s TLS proxy listening on port %s", self.name, self.listen_port)

            async with self._server:
                await self._server.serve_forever()

        except OSError as e:
            if e.errno == 98:  # Address already in use
                logger.error("%s proxy port %s is already in use", self.name, self.listen_port)
            elif e.errno == 13:  # Permission denied
                logger.error(
                    "%s proxy: cannot bind to port %s (permission denied). "
                    "Port %s requires root or CAP_NET_BIND_SERVICE. "
                    "Docker: add 'cap_add: [NET_BIND_SERVICE]' to docker-compose.yml. "
                    "Native: use 'sudo setcap cap_net_bind_service=+ep $(which python3)' "
                    "or redirect with iptables.",
                    self.name,
                    self.listen_port,
                    self.listen_port,
                )
            else:
                logger.error("%s proxy error: %s", self.name, e)
        except asyncio.CancelledError:
            logger.debug("%s proxy task cancelled", self.name)
        except Exception as e:
            logger.error("%s proxy error: %s", self.name, e)
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the TLS proxy server."""
        logger.info("Stopping %s proxy", self.name)
        self._running = False

        # Cancel all active connection tasks
        for client_id, (task1, task2) in list(self._active_connections.items()):
            task1.cancel()
            task2.cancel()
            if self.on_disconnect:
                try:
                    self.on_disconnect(client_id)
                except Exception:
                    pass  # Ignore disconnect callback errors during shutdown

        self._active_connections.clear()

        if self._server:
            try:
                self._server.close()
                await self._server.wait_closed()
            except OSError as e:
                logger.debug("Error closing %s proxy server: %s", self.name, e)
            self._server = None

    async def _handle_client(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a new client connection by proxying to target."""
        peername = client_writer.get_extra_info("peername")
        client_id = f"{peername[0]}:{peername[1]}" if peername else "unknown"

        logger.info("%s proxy: client connected from %s", self.name, client_id)

        if self.on_connect:
            try:
                self.on_connect(client_id)
            except Exception:
                pass  # Ignore connect callback errors; connection proceeds regardless

        # Connect to target printer with TLS
        try:
            printer_reader, printer_writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self.target_host,
                    self.target_port,
                    ssl=self._client_ssl_context,
                ),
                timeout=10.0,
            )
            logger.info("%s proxy: connected to printer %s:%s", self.name, self.target_host, self.target_port)
        except TimeoutError:
            logger.error("%s proxy: timeout connecting to %s:%s", self.name, self.target_host, self.target_port)
            client_writer.close()
            await client_writer.wait_closed()
            return
        except ssl.SSLError as e:
            logger.error(
                "%s proxy: SSL error connecting to %s:%s: %s", self.name, self.target_host, self.target_port, e
            )
            client_writer.close()
            await client_writer.wait_closed()
            return
        except OSError as e:
            logger.error("%s proxy: failed to connect to %s:%s: %s", self.name, self.target_host, self.target_port, e)
            client_writer.close()
            await client_writer.wait_closed()
            return

        # Create bidirectional forwarding tasks
        client_to_printer = asyncio.create_task(
            self._forward(client_reader, printer_writer, f"{client_id}→printer"),
            name=f"{self.name}_c2p_{client_id}",
        )
        printer_to_client = asyncio.create_task(
            self._forward(printer_reader, client_writer, f"printer→{client_id}", rewrite_ip=True),
            name=f"{self.name}_p2c_{client_id}",
        )

        self._active_connections[client_id] = (client_to_printer, printer_to_client)

        try:
            # Wait for either direction to complete (connection closed)
            done, pending = await asyncio.wait(
                [client_to_printer, printer_to_client],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel the other direction
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass  # Expected when cancelling the other forwarding direction

        except Exception as e:
            logger.debug("%s proxy connection error: %s", self.name, e)
        finally:
            # Clean up
            self._active_connections.pop(client_id, None)

            for writer in [client_writer, printer_writer]:
                try:
                    writer.close()
                    await writer.wait_closed()
                except OSError:
                    pass  # Best-effort connection cleanup; peer may have disconnected

            logger.info("%s proxy: client %s disconnected", self.name, client_id)

            if self.on_disconnect:
                try:
                    self.on_disconnect(client_id)
                except Exception:
                    pass  # Ignore disconnect callback errors; cleanup continues

    @staticmethod
    def _rewrite_mqtt_ip(
        data: bytes,
        old_ip: bytes,
        new_ip: bytes,
        buffer: bytearray,
        extra_replacements: list[tuple[bytes, bytes]] | None = None,
    ) -> tuple[bytes, bytearray]:
        """Rewrite IP addresses inside MQTT packets, preserving packet framing.

        MQTT packets have a variable-length header encoding the remaining
        packet length.  A naive bytes.replace() would corrupt this framing
        when old_ip and new_ip differ in length.

        This method parses individual MQTT packets out of the data stream,
        performs the replacement only on PUBLISH payloads, and re-encodes
        the remaining-length field to match the new size.

        Incomplete packets are buffered and returned for the next call.

        Args:
            extra_replacements: Additional (old, new) byte pairs to replace
                (e.g. the integer IP representation in net.info[].ip).

        Returns (output_data, remaining_buffer).
        """
        buffer.extend(data)

        # Check if any replacement target exists in the buffer
        has_target = old_ip in buffer
        if not has_target and extra_replacements:
            has_target = any(old in buffer for old, _new in extra_replacements)

        if not has_target:
            # Fast path: no IP in buffer, but we still need to check for
            # incomplete packets at the end that might contain a partial IP.
            # For safety, try to parse and emit only complete packets.
            result = bytearray()
            pos = 0
            length = len(buffer)

            while pos < length:
                packet_start = pos
                if pos + 1 >= length:
                    break
                pos += 1  # header byte

                # Parse remaining length
                remaining_length = 0
                multiplier = 1
                length_bytes = 0
                while pos < length:
                    encoded_byte = buffer[pos]
                    pos += 1
                    remaining_length += (encoded_byte & 0x7F) * multiplier
                    multiplier *= 128
                    length_bytes += 1
                    if (encoded_byte & 0x80) == 0:
                        break
                    if length_bytes >= 4:
                        break

                if pos + remaining_length > length:
                    # Incomplete — keep in buffer
                    new_buffer = bytearray(buffer[packet_start:])
                    return bytes(result), new_buffer

                pos += remaining_length
                result.extend(buffer[packet_start:pos])

            # All complete
            buffer.clear()
            return bytes(result) if result else bytes(data), buffer

        # Buffer contains old_ip — parse packets and rewrite
        result = bytearray()
        pos = 0
        length = len(buffer)

        while pos < length:
            packet_start = pos

            if pos >= length:
                break
            header_byte = buffer[pos]
            pos += 1

            # Remaining length: variable-length encoding (1-4 bytes)
            remaining_length = 0
            multiplier = 1
            length_bytes = 0
            while pos < length:
                encoded_byte = buffer[pos]
                pos += 1
                remaining_length += (encoded_byte & 0x7F) * multiplier
                multiplier *= 128
                length_bytes += 1
                if (encoded_byte & 0x80) == 0:
                    break
                if length_bytes >= 4:
                    break

            # Check if we have enough data for the full packet
            if pos + remaining_length > length:
                # Incomplete packet — keep in buffer for next call
                new_buffer = bytearray(buffer[packet_start:])
                return bytes(result), new_buffer

            packet_type = (header_byte >> 4) & 0x0F
            packet_body = buffer[pos : pos + remaining_length]
            pos += remaining_length

            # Only rewrite PUBLISH packets (type 3)
            needs_rewrite = packet_type == 3 and (
                old_ip in packet_body
                or (extra_replacements and any(old in packet_body for old, _new in extra_replacements))
            )
            if needs_rewrite:
                new_body = bytes(packet_body).replace(old_ip, new_ip)
                if extra_replacements:
                    for old_val, new_val in extra_replacements:
                        new_body = new_body.replace(old_val, new_val)

                # Re-encode: header byte + new remaining length + new body
                result.append(header_byte)

                # Encode remaining length (MQTT variable-length encoding)
                new_remaining = len(new_body)
                while True:
                    encoded_byte = new_remaining % 128
                    new_remaining //= 128
                    if new_remaining > 0:
                        encoded_byte |= 0x80
                    result.append(encoded_byte)
                    if new_remaining == 0:
                        break

                result.extend(new_body)
            else:
                # Pass through unchanged
                result.extend(buffer[packet_start:pos])

        buffer.clear()
        return bytes(result), buffer

    async def _forward(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        direction: str,
        rewrite_ip: bool = False,
    ) -> None:
        """Forward data from reader to writer.

        Args:
            reader: Source stream (already TLS-decrypted)
            writer: Destination stream (will be TLS-encrypted by the stream)
            direction: Description for logging (e.g., "client→printer")
            rewrite_ip: If True and rewrite_ip was configured, replace the
                printer's real IP with the proxy's bind IP in the data.
        """
        do_rewrite = rewrite_ip and self._rewrite_old is not None
        rewrite_buffer = bytearray() if do_rewrite else None
        rewrite_logged = False
        total_bytes = 0
        try:
            while self._running:
                # Read chunk - use reasonable buffer size
                data = await reader.read(65536)
                if not data:
                    # Connection closed
                    break

                # Rewrite printer IP → proxy IP in MQTT PUBLISH payloads
                # to prevent the slicer from bypassing the proxy.
                if do_rewrite:
                    extra = [(self._rewrite_old_int, self._rewrite_new_int)] if self._rewrite_old_int else None
                    data, rewrite_buffer = self._rewrite_mqtt_ip(
                        data,
                        self._rewrite_old,
                        self._rewrite_new,
                        rewrite_buffer,
                        extra_replacements=extra,
                    )
                    if not rewrite_logged and data:
                        if self._rewrite_old in data:
                            logger.warning(
                                "%s proxy IP rewrite FAILED — %s still present after rewrite!",
                                self.name,
                                self._rewrite_old.decode(),
                            )
                        else:
                            logger.info(
                                "%s proxy IP rewrite active: %s → %s",
                                self.name,
                                self._rewrite_old.decode(),
                                self._rewrite_new.decode(),
                            )
                        rewrite_logged = True
                    if not data:
                        continue  # All data buffered, waiting for more

                # Forward to destination
                writer.write(data)
                await writer.drain()

                total_bytes += len(data)

        except asyncio.CancelledError:
            pass  # Expected when the other forwarding direction closes first
        except ConnectionResetError:
            logger.debug("%s proxy %s: connection reset", self.name, direction)
        except BrokenPipeError:
            logger.debug("%s proxy %s: broken pipe", self.name, direction)
        except OSError as e:
            logger.debug("%s proxy %s error: %s", self.name, direction, e)

        logger.debug("%s proxy %s: total %s bytes", self.name, direction, total_bytes)


class TCPProxy:
    """Raw TCP proxy that forwards data without TLS termination.

    Used for protocols where the printer doesn't use TLS (e.g., port 3000
    binding/authentication protocol).
    """

    def __init__(
        self,
        name: str,
        listen_port: int,
        target_host: str,
        target_port: int,
        on_connect: Callable[[str], None] | None = None,
        on_disconnect: Callable[[str], None] | None = None,
        bind_address: str = "0.0.0.0",  # nosec B104
    ):
        self.name = name
        self.listen_port = listen_port
        self.target_host = target_host
        self.target_port = target_port
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.bind_address = bind_address

        self._server: asyncio.Server | None = None
        self._running = False
        self._active_connections: dict[str, tuple[asyncio.Task, asyncio.Task]] = {}

    async def start(self) -> None:
        """Start the TCP proxy server."""
        if self._running:
            return

        logger.info(
            "Starting %s TCP proxy: %s:%s → %s:%s",
            self.name,
            self.bind_address,
            self.listen_port,
            self.target_host,
            self.target_port,
        )

        try:
            self._running = True

            self._server = await asyncio.start_server(
                self._handle_client,
                self.bind_address,
                self.listen_port,
            )

            logger.info("%s TCP proxy listening on port %s", self.name, self.listen_port)

            async with self._server:
                await self._server.serve_forever()

        except OSError as e:
            if e.errno == 98:  # Address already in use
                logger.error("%s proxy port %s is already in use", self.name, self.listen_port)
            else:
                logger.error("%s proxy error: %s", self.name, e)
        except asyncio.CancelledError:
            logger.debug("%s proxy task cancelled", self.name)
        except Exception as e:
            logger.error("%s proxy error: %s", self.name, e)
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the TCP proxy server."""
        logger.info("Stopping %s proxy", self.name)
        self._running = False

        for client_id, (task1, task2) in list(self._active_connections.items()):
            task1.cancel()
            task2.cancel()
            if self.on_disconnect:
                try:
                    self.on_disconnect(client_id)
                except Exception:
                    pass

        self._active_connections.clear()

        if self._server:
            try:
                self._server.close()
                await self._server.wait_closed()
            except OSError as e:
                logger.debug("Error closing %s proxy server: %s", self.name, e)
            self._server = None

    async def _handle_client(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a new client connection by proxying to target."""
        peername = client_writer.get_extra_info("peername")
        client_id = f"{peername[0]}:{peername[1]}" if peername else "unknown"

        logger.info("%s proxy: client connected from %s", self.name, client_id)

        if self.on_connect:
            try:
                self.on_connect(client_id)
            except Exception:
                pass

        try:
            printer_reader, printer_writer = await asyncio.wait_for(
                asyncio.open_connection(self.target_host, self.target_port),
                timeout=10.0,
            )
            logger.info("%s proxy: connected to printer %s:%s", self.name, self.target_host, self.target_port)
        except TimeoutError:
            logger.error("%s proxy: timeout connecting to %s:%s", self.name, self.target_host, self.target_port)
            client_writer.close()
            await client_writer.wait_closed()
            return
        except OSError as e:
            logger.error("%s proxy: failed to connect to %s:%s: %s", self.name, self.target_host, self.target_port, e)
            client_writer.close()
            await client_writer.wait_closed()
            return

        client_to_printer = asyncio.create_task(
            self._forward(client_reader, printer_writer, f"{client_id}→printer"),
            name=f"{self.name}_c2p_{client_id}",
        )
        printer_to_client = asyncio.create_task(
            self._forward(printer_reader, client_writer, f"printer→{client_id}"),
            name=f"{self.name}_p2c_{client_id}",
        )

        self._active_connections[client_id] = (client_to_printer, printer_to_client)

        try:
            done, pending = await asyncio.wait(
                [client_to_printer, printer_to_client],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        except Exception as e:
            logger.debug("%s proxy connection error: %s", self.name, e)
        finally:
            self._active_connections.pop(client_id, None)

            for writer in [client_writer, printer_writer]:
                try:
                    writer.close()
                    await writer.wait_closed()
                except OSError:
                    pass

            logger.info("%s proxy: client %s disconnected", self.name, client_id)

            if self.on_disconnect:
                try:
                    self.on_disconnect(client_id)
                except Exception:
                    pass

    async def _forward(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        direction: str,
    ) -> None:
        """Forward data from reader to writer."""
        total_bytes = 0
        try:
            while self._running:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
                total_bytes += len(data)
                logger.debug("%s proxy %s: %s bytes", self.name, direction, len(data))
        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            logger.debug("%s proxy %s: connection reset", self.name, direction)
        except BrokenPipeError:
            logger.debug("%s proxy %s: broken pipe", self.name, direction)
        except OSError as e:
            logger.debug("%s proxy %s error: %s", self.name, direction, e)

        logger.debug("%s proxy %s: total %s bytes", self.name, direction, total_bytes)


class SlicerProxyManager:
    """Manages FTP and MQTT TLS proxies for a single printer target."""

    # Bambu printer ports
    PRINTER_FTP_PORT = 990
    PRINTER_MQTT_PORT = 8883
    PRINTER_FILE_TRANSFER_PORT = 6000
    PRINTER_RTSP_PORT = 322  # X1/H2/P2 series camera (A1/P1 use port 6000)
    # Undocumented proprietary ports used by some models (A1, P1S, etc.)
    # BambuStudio requires port 2024 for printing; OrcaSlicer also needs 2025.
    PRINTER_AUX_PORTS = [2024, 2025, 2026]
    PRINTER_BIND_PORTS = [3000, 3002]

    # Local listen ports - must match what Bambu Studio expects
    # Note: Port 990 requires root or CAP_NET_BIND_SERVICE capability
    LOCAL_FTP_PORT = 990
    LOCAL_MQTT_PORT = 8883

    def __init__(
        self,
        target_host: str,
        cert_path: Path,
        key_path: Path,
        on_activity: Callable[[str, str], None] | None = None,
        bind_address: str = "0.0.0.0",  # nosec B104
        bind_identity: dict[str, str] | None = None,
    ):
        """Initialize the slicer proxy manager.

        Args:
            target_host: Target printer IP address
            cert_path: Path to server certificate
            key_path: Path to server private key
            on_activity: Optional callback for activity logging (name, message)
            bind_address: IP address to bind proxy listeners to
            bind_identity: Optional dict with keys (serial, model, name, version)
                for the bind/detect response. When provided, the proxy responds
                to detect requests itself instead of forwarding to the printer.
                This ensures the slicer sees the VP identity, not the real printer.
        """
        self.target_host = target_host
        self.cert_path = cert_path
        self.key_path = key_path
        self.on_activity = on_activity
        self.bind_address = bind_address
        self.bind_identity = bind_identity

        self._ftp_proxy: TCPProxy | None = None
        self._mqtt_proxy: TLSProxy | None = None
        self._file_transfer_proxy: TCPProxy | None = None
        self._rtsp_proxy: TCPProxy | None = None
        self._aux_proxies: list[TCPProxy] = []
        self._bind_proxies: list[TCPProxy] = []
        self._bind_server = None
        self._probe_servers: list[asyncio.Server] = []
        self._tasks: list[asyncio.Task] = []
        # Pre-bind the lifetime-coupled collections so ``stop()`` works if
        # called before ``start()`` finishes (rapid mode-switch races).
        # Previously ``_ftp_data_proxies`` was first assigned inside ``start()``
        # at line ~1520, so an early stop hit AttributeError and left
        # sockets stranded.
        self._ftp_data_proxies: list[TCPProxy] = []
        # Actual FTP listen port — class constant by default; ``start()``
        # overwrites with the redirect target when iptables-redirect is
        # active. ``get_status()`` reads this so diagnostics probe the
        # port that actually has a listener, not the static LOCAL_FTP_PORT.
        self._actual_ftp_port: int = self.LOCAL_FTP_PORT

    # FTP passive data port range — Bambu printers typically use ports in
    # this range for EPSV/PASV data connections. We pre-listen on all of
    # them so EPSV works transparently without decrypting FTP control.
    FTP_DATA_PORT_MIN = 50000
    FTP_DATA_PORT_MAX = 50100

    async def start(self) -> None:
        """Start proxy services.

        Uses transparent TCP proxying for most protocols (FTP, FileTransfer,
        Camera) — raw bytes are forwarded without TLS termination, so the
        slicer gets the printer's real TLS certificate end-to-end.

        Only MQTT is TLS-terminated because we must decrypt the payload to
        rewrite the printer's real IP with the proxy's bind IP.
        """
        logger.info("Starting slicer proxy to %s (transparent mode)", self.target_host)

        # Detect iptables port redirect for FTP
        ftp_listen_port = self.LOCAL_FTP_PORT
        redirect_target = detect_port_redirect(self.LOCAL_FTP_PORT)
        if redirect_target:
            logger.info(
                "Detected iptables redirect: port %d → %d. FTP proxy will listen on %d.",
                self.LOCAL_FTP_PORT,
                redirect_target,
                redirect_target,
            )
            ftp_listen_port = redirect_target
        # Cache the actual listen port for get_status() / diagnostic so the
        # port_ftps check probes the port that actually has a socket.
        self._actual_ftp_port = ftp_listen_port

        # FTP control — raw TCP pass-through (end-to-end TLS with printer).
        # A TLS 1.3 → 1.2 ClientHello-rewrite was attempted to work around
        # BambuStudio's libcurl bug on the X1C FTPS data channel (PSK
        # session-resumption + CURLE_PARTIAL_FILE). Reverted because the
        # rewrite broke the control-channel TLS handshake itself: replacing
        # 0x0304 with a duplicate 0x0303 in supported_versions while leaving
        # the TLS-1.3-only extensions (key_share, psk_key_exchange_modes,
        # signature_algorithms_cert) in place produced a malformed ClientHello
        # that the printer or slicer rejected, and the connection closed
        # before any data channel was opened. A proper fix needs full TLS
        # bumping (terminate + re-establish) with packet-capture work
        # that's out of scope for now. X1C proxy-mode FTP uploads remain
        # broken — users with X1C should use the non-proxy modes (immediate
        # / review / print_queue) which work end-to-end via the VP's own
        # FTP server on TLS 1.2.
        self._ftp_proxy = TCPProxy(
            name="FTP",
            listen_port=ftp_listen_port,
            target_host=self.target_host,
            target_port=self.PRINTER_FTP_PORT,
            on_connect=lambda cid: self._log_activity("FTP", f"connected: {cid}"),
            on_disconnect=lambda cid: self._log_activity("FTP", f"disconnected: {cid}"),
            bind_address=self.bind_address,
        )

        # FTP data ports — pre-listen on the entire passive port range.
        # Since FTP control is encrypted end-to-end, we can't read EPSV
        # responses to know which port the printer chose. Instead, we
        # listen on every port in the range and forward to the same port
        # on the printer. The slicer connects to bind_ip:PORT (from EPSV)
        # and we transparently relay to printer_ip:PORT.
        self._ftp_data_proxies: list[TCPProxy] = []
        for port in range(self.FTP_DATA_PORT_MIN, self.FTP_DATA_PORT_MAX + 1):
            dp = TCPProxy(
                name=f"FTP-Data-{port}",
                listen_port=port,
                target_host=self.target_host,
                target_port=port,
                bind_address=self.bind_address,
            )
            self._ftp_data_proxies.append(dp)

        # MQTT — TLS-terminating proxy (must decrypt to rewrite IP addresses)
        self._mqtt_proxy = TLSProxy(
            name="MQTT",
            listen_port=self.LOCAL_MQTT_PORT,
            target_host=self.target_host,
            target_port=self.PRINTER_MQTT_PORT,
            server_cert_path=self.cert_path,
            server_key_path=self.key_path,
            on_connect=lambda cid: self._log_activity("MQTT", f"connected: {cid}"),
            on_disconnect=lambda cid: self._log_activity("MQTT", f"disconnected: {cid}"),
            bind_address=self.bind_address,
            rewrite_ip=(self.target_host, self.bind_address) if self.bind_address != "0.0.0.0" else None,  # nosec B104
        )

        # File transfer — raw TCP pass-through (port 6000)
        self._file_transfer_proxy = TCPProxy(
            name="FileTransfer",
            listen_port=self.PRINTER_FILE_TRANSFER_PORT,
            target_host=self.target_host,
            target_port=self.PRINTER_FILE_TRANSFER_PORT,
            on_connect=lambda cid: self._log_activity("FileTransfer", f"connected: {cid}"),
            on_disconnect=lambda cid: self._log_activity("FileTransfer", f"disconnected: {cid}"),
            bind_address=self.bind_address,
        )

        # RTSP camera — raw TCP pass-through (port 322)
        self._rtsp_proxy = TCPProxy(
            name="RTSP",
            listen_port=self.PRINTER_RTSP_PORT,
            target_host=self.target_host,
            target_port=self.PRINTER_RTSP_PORT,
            on_connect=lambda cid: self._log_activity("RTSP", f"connected: {cid}"),
            on_disconnect=lambda cid: self._log_activity("RTSP", f"disconnected: {cid}"),
            bind_address=self.bind_address,
        )

        # Auxiliary ports (2024-2026) — raw TCP pass-through for undocumented
        # proprietary services. Required by BambuStudio/OrcaSlicer for some
        # models (A1, P1S). Silently ignored if the printer doesn't listen.
        for aux_port in self.PRINTER_AUX_PORTS:
            self._aux_proxies.append(
                TCPProxy(
                    name=f"Aux-{aux_port}",
                    listen_port=aux_port,
                    target_host=self.target_host,
                    target_port=aux_port,
                    on_connect=lambda cid, p=aux_port: self._log_activity(f"Aux-{p}", f"connected: {cid}"),
                    on_disconnect=lambda cid, p=aux_port: self._log_activity(f"Aux-{p}", f"disconnected: {cid}"),
                    bind_address=self.bind_address,
                )
            )

        # Bind/auth — respond with VP identity instead of proxying to printer.
        # The detect response contains the printer name, serial, model, and
        # bind status. Proxying it would leak the real printer's identity and
        # cause the slicer to treat it as a different device.
        if self.bind_identity:
            from backend.app.services.virtual_printer.bind_server import BindServer

            self._bind_server = BindServer(
                serial=self.bind_identity["serial"],
                model=self.bind_identity["model"],
                name=self.bind_identity["name"],
                version=self.bind_identity.get("version", "01.00.00.00"),
                bind_address=self.bind_address,
                cert_path=self.cert_path,
                key_path=self.key_path,
            )
        else:
            # Fallback: proxy bind requests to the real printer
            for bind_port in self.PRINTER_BIND_PORTS:
                if bind_port == 3002:
                    proxy = TLSProxy(
                        name="Bind-TLS",
                        listen_port=bind_port,
                        target_host=self.target_host,
                        target_port=bind_port,
                        server_cert_path=self.cert_path,
                        server_key_path=self.key_path,
                        on_connect=lambda cid: self._log_activity("Bind", f"connected: {cid}"),
                        on_disconnect=lambda cid: self._log_activity("Bind", f"disconnected: {cid}"),
                        bind_address=self.bind_address,
                    )
                else:
                    proxy = TCPProxy(
                        name="Bind",
                        listen_port=bind_port,
                        target_host=self.target_host,
                        target_port=bind_port,
                        on_connect=lambda cid: self._log_activity("Bind", f"connected: {cid}"),
                        on_disconnect=lambda cid: self._log_activity("Bind", f"disconnected: {cid}"),
                        bind_address=self.bind_address,
                    )
                self._bind_proxies.append(proxy)

        # Start as background tasks
        async def run_with_logging(proxy: TLSProxy | TCPProxy) -> None:
            try:
                await proxy.start()
            except Exception as e:
                logger.error("Slicer proxy %s failed: %s", proxy.name, e)

        self._tasks = [
            asyncio.create_task(
                run_with_logging(self._ftp_proxy),
                name="slicer_proxy_ftp",
            ),
            asyncio.create_task(
                run_with_logging(self._mqtt_proxy),
                name="slicer_proxy_mqtt",
            ),
            asyncio.create_task(
                run_with_logging(self._file_transfer_proxy),
                name="slicer_proxy_file_transfer",
            ),
            asyncio.create_task(
                run_with_logging(self._rtsp_proxy),
                name="slicer_proxy_rtsp",
            ),
        ]
        for ap in self._aux_proxies:
            self._tasks.append(
                asyncio.create_task(
                    run_with_logging(ap),
                    name=f"slicer_proxy_aux_{ap.listen_port}",
                )
            )
        if self._bind_server:
            self._tasks.append(
                asyncio.create_task(
                    run_with_logging(self._bind_server),
                    name="slicer_proxy_bind_server",
                )
            )
        for bp in self._bind_proxies:
            self._tasks.append(
                asyncio.create_task(
                    run_with_logging(bp),
                    name=f"slicer_proxy_bind_{bp.listen_port}",
                )
            )
        # FTP data port proxies (50000-50100)
        for dp in self._ftp_data_proxies:
            self._tasks.append(
                asyncio.create_task(
                    run_with_logging(dp),
                    name=f"slicer_proxy_ftp_data_{dp.listen_port}",
                )
            )

        # Diagnostic probe: listen on common un-proxied ports to detect
        # if the slicer tries to reach a service we don't handle.
        if self.bind_address and self.bind_address != "0.0.0.0":  # nosec B104
            for probe_port in (21, 80, 443):
                try:
                    srv = await asyncio.start_server(
                        lambda r, w, p=probe_port: self._probe_handler(r, w, p),
                        self.bind_address,
                        probe_port,
                    )
                    self._probe_servers.append(srv)
                except OSError:
                    pass  # Port in use or no permission — skip
            if self._probe_servers:
                probed = [s.sockets[0].getsockname()[1] for s in self._probe_servers if s.sockets]
                logger.info("Proxy diagnostic: probing un-proxied ports %s on %s", probed, self.bind_address)

        logger.info(
            "Slicer proxy started for %s (transparent TCP + MQTT TLS, %d FTP data ports)",
            self.target_host,
            len(self._ftp_data_proxies),
        )

        # Wait for tasks to complete (they run until cancelled)
        # This keeps the start() coroutine alive so the parent task doesn't complete
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            logger.debug("Slicer proxy start cancelled")

    async def stop(self) -> None:
        """Stop all proxies."""
        logger.info("Stopping slicer proxy")

        # Stop proxies
        if self._ftp_proxy:
            await self._ftp_proxy.stop()
            self._ftp_proxy = None

        if self._mqtt_proxy:
            await self._mqtt_proxy.stop()
            self._mqtt_proxy = None

        if self._file_transfer_proxy:
            await self._file_transfer_proxy.stop()
            self._file_transfer_proxy = None

        if self._rtsp_proxy:
            await self._rtsp_proxy.stop()
            self._rtsp_proxy = None

        for ap in self._aux_proxies:
            await ap.stop()
        self._aux_proxies = []

        if self._bind_server:
            await self._bind_server.stop()
            self._bind_server = None

        for bp in self._bind_proxies:
            await bp.stop()
        self._bind_proxies = []

        for dp in self._ftp_data_proxies:
            await dp.stop()
        self._ftp_data_proxies = []

        # Probe servers need wait_closed — without it the OS releases the
        # bind socket asynchronously and a rapid stop+start cycle (e.g.
        # config-change-driven mode switch) can race "address already in
        # use" on the probe ports.
        for srv in self._probe_servers:
            try:
                srv.close()
                await srv.wait_closed()
            except OSError:
                pass  # Best-effort — port may already be released
        self._probe_servers = []

        # Cancel tasks
        for task in self._tasks:
            task.cancel()

        if self._tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._tasks, return_exceptions=True),
                    timeout=2.0,
                )
            except TimeoutError:
                logger.debug("Some proxy tasks didn't stop in time")

        self._tasks = []
        logger.info("Slicer proxy stopped")

    async def _probe_handler(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, port: int) -> None:
        """Log unexpected connections on un-proxied ports for diagnostics."""
        peername = writer.get_extra_info("peername")
        client = f"{peername[0]}:{peername[1]}" if peername else "unknown"
        logger.warning(
            "PROBE: slicer connected to un-proxied port %d from %s — this port may need proxying",
            port,
            client,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

    def _log_activity(self, name: str, message: str) -> None:
        """Log activity via callback if configured."""
        if self.on_activity:
            try:
                self.on_activity(name, message)
            except Exception:
                pass  # Ignore activity callback errors; logging is non-critical

    @property
    def is_running(self) -> bool:
        """Check if proxies are running."""
        return len(self._tasks) > 0 and all(not t.done() for t in self._tasks)

    def get_status(self) -> dict:
        """Get proxy status."""
        return {
            "running": self.is_running,
            "target_host": self.target_host,
            # ``_actual_ftp_port`` reflects the iptables-redirected listen
            # port when the docker-host deployment uses
            # ``iptables -t nat -A PREROUTING ... REDIRECT --to-port`` to
            # let non-root containers serve on the printer's 990. Returning
            # the class constant here made the diagnostic probe a port
            # nothing was listening on and report a false fail on every
            # working redirect deployment.
            "ftp_port": self._actual_ftp_port,
            "mqtt_port": self.LOCAL_MQTT_PORT,
            "bind_ports": self.PRINTER_BIND_PORTS,
            "ftp_connections": (len(self._ftp_proxy._active_connections) if self._ftp_proxy else 0),
            "mqtt_connections": (len(self._mqtt_proxy._active_connections) if self._mqtt_proxy else 0),
            "bind_connections": sum(len(bp._active_connections) for bp in self._bind_proxies),
        }
