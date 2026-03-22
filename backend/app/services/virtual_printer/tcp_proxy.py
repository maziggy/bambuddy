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
import random
import re
import ssl
import subprocess
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


class _SessionReuseSSLContext:
    """Proxy around SSLContext that injects a TLS session into wrap_bio().

    vsFTPd (used by some Bambu printers like X1C) requires TLS session reuse
    on FTP data channels — the data connection must reuse the TLS session from
    the control channel. Without this, the printer rejects the data connection
    with "522 SSL connection failed: session reuse required".

    asyncio's open_connection() calls SSLContext.wrap_bio() internally but
    doesn't expose a session parameter. This wrapper intercepts wrap_bio()
    to inject the saved control-channel session, enabling session reuse.
    """

    def __init__(self, ctx: ssl.SSLContext, session: ssl.SSLSession) -> None:
        object.__setattr__(self, "_ctx", ctx)
        object.__setattr__(self, "_session", session)

    def __getattr__(self, name: str) -> object:
        return getattr(self._ctx, name)

    def wrap_bio(
        self,
        incoming: ssl.MemoryBIO,
        outgoing: ssl.MemoryBIO,
        server_side: bool = False,
        server_hostname: str | None = None,
        **kwargs: object,
    ) -> ssl.SSLObject:
        return self._ctx.wrap_bio(
            incoming,
            outgoing,
            server_side=server_side,
            server_hostname=server_hostname,
            session=self._session,
            **kwargs,
        )


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


class FTPTLSProxy(TLSProxy):
    """FTP-aware TLS proxy that handles passive data connections.

    Extends TLSProxy to intercept PASV/EPSV responses on the FTP control
    channel, dynamically create TLS data proxies on local ports, and rewrite
    the responses so the slicer connects to the proxy instead of the printer.

    Without this, FTP passive data connections bypass the proxy and go directly
    to the printer, which fails when the slicer can't reach the printer's IP.
    """

    PASV_PORT_MIN = 50000
    PASV_PORT_MAX = 50100

    async def stop(self) -> None:
        """Stop proxy and clean up data connection servers."""
        # Close all data servers first
        for server in list(self._data_servers):
            try:
                server.close()
                await server.wait_closed()
            except OSError:
                pass  # Best-effort cleanup of data proxy servers
        self._data_servers.clear()
        await super().stop()

    async def start(self) -> None:
        """Start the FTP TLS proxy."""
        self._data_servers: list[asyncio.Server] = []
        await super().start()

    async def _handle_client(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        """Handle FTP client with PASV/EPSV-aware response forwarding."""
        peername = client_writer.get_extra_info("peername")
        client_id = f"{peername[0]}:{peername[1]}" if peername else "unknown"

        logger.info("%s proxy: client connected from %s", self.name, client_id)

        if self.on_connect:
            try:
                self.on_connect(client_id)
            except Exception:
                pass  # Ignore connect callback errors; connection proceeds regardless

        # Determine our local IP from the control connection socket
        sockname = client_writer.get_extra_info("sockname")
        local_ip = sockname[0] if sockname else "0.0.0.0"  # nosec B104
        if local_ip in ("0.0.0.0", "::"):  # nosec B104
            local_ip = "127.0.0.1"

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

        # Capture the TLS session from the control channel for data channel
        # reuse. vsFTPd (X1C) requires require_ssl_reuse — the data connection
        # must present the same TLS session as the control channel.
        ctrl_ssl_object = printer_writer.get_extra_info("ssl_object")
        ctrl_tls_session = ctrl_ssl_object.session if ctrl_ssl_object else None
        if ctrl_tls_session:
            logger.debug("%s proxy: captured TLS session for data channel reuse", self.name)

        # Track data channel protection level per session.
        # PROT C = cleartext data, PROT P = TLS data.
        # Default to cleartext — many Bambu printers (A1, H2D) use PROT C.
        # If the slicer sends PROT P, we switch to TLS for data connections.
        session_state: dict[str, str | ssl.SSLSession] = {"prot": "C"}
        if ctrl_tls_session:
            session_state["tls_session"] = ctrl_tls_session

        # Client→Printer: intercept EPSV and replace with PASV
        # EPSV responses only contain a port (no IP), so the slicer reuses
        # the control connection IP. If that IP is the real printer (via
        # iptables REDIRECT), the data connection bypasses the proxy.
        # PASV responses include an explicit IP that we can rewrite.
        client_to_printer = asyncio.create_task(
            self._forward_ftp_commands(client_reader, printer_writer, f"{client_id}→printer", session_state),
            name=f"{self.name}_c2p_{client_id}",
        )
        # Printer→Client: intercept PASV/EPSV responses
        printer_to_client = asyncio.create_task(
            self._forward_ftp_control(printer_reader, client_writer, f"printer→{client_id}", local_ip, session_state),
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
                    pass  # Expected when cancelling the other forwarding direction

        except Exception as e:
            logger.debug("%s proxy connection error: %s", self.name, e)
        finally:
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

    async def _forward_ftp_commands(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        direction: str,
        session_state: dict[str, str | ssl.SSLSession],
    ) -> None:
        """Forward FTP client commands, replacing EPSV with PASV.

        EPSV responses only contain a port number — the client reuses the
        control connection IP for data.  When the control IP is the real
        printer (due to iptables REDIRECT), EPSV data connections bypass
        the proxy.  PASV responses include an explicit IP that the proxy
        can rewrite to its own address.

        Also tracks PROT P/C commands to know whether data connections
        should use TLS or cleartext.
        """
        buffer = b""
        total_bytes = 0
        try:
            while self._running:
                data = await reader.read(65536)
                if not data:
                    break

                total_bytes += len(data)
                buffer += data
                output = b""

                while b"\r\n" in buffer:
                    idx = buffer.index(b"\r\n")
                    line = buffer[:idx]
                    buffer = buffer[idx + 2 :]

                    cmd_upper = line.strip().upper()

                    # Track PROT level for data channel encryption
                    if cmd_upper == b"PROT P":
                        session_state["prot"] = "P"
                        logger.info("FTP data protection: PROT P (TLS)")
                    elif cmd_upper == b"PROT C":
                        session_state["prot"] = "C"
                        logger.info("FTP data protection: PROT C (cleartext)")

                    output += line + b"\r\n"

                if output:
                    writer.write(output)
                    await writer.drain()

                logger.debug("%s proxy %s: %s bytes", self.name, direction, len(data))

        except asyncio.CancelledError:
            pass  # Expected when the other forwarding direction closes first
        except ConnectionResetError:
            logger.debug("%s proxy %s: connection reset", self.name, direction)
        except BrokenPipeError:
            logger.debug("%s proxy %s: broken pipe", self.name, direction)
        except OSError as e:
            logger.debug("%s proxy %s error: %s", self.name, direction, e)

        if buffer:
            try:
                writer.write(buffer)
                await writer.drain()
            except OSError:
                pass  # Best-effort flush of remaining FTP command data

        logger.debug("%s proxy %s: total %s bytes", self.name, direction, total_bytes)

    async def _forward_ftp_control(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        direction: str,
        local_ip: str,
        session_state: dict[str, str | ssl.SSLSession],
    ) -> None:
        """Forward FTP control channel responses, rewriting PASV/EPSV.

        FTP control channel is line-based (\\r\\n terminated). We buffer data
        and process complete lines, intercepting 227 (PASV) and 229 (EPSV)
        responses to create local data proxies.
        """
        buffer = b""
        total_bytes = 0

        try:
            while self._running:
                data = await reader.read(65536)
                if not data:
                    break

                total_bytes += len(data)
                buffer += data
                output = b""

                # Process all complete lines
                while b"\r\n" in buffer:
                    idx = buffer.index(b"\r\n")
                    line = buffer[:idx]
                    buffer = buffer[idx + 2 :]

                    rewritten = await self._maybe_rewrite_pasv(line, local_ip, session_state)
                    output += rewritten + b"\r\n"

                if output:
                    writer.write(output)
                    await writer.drain()

                logger.debug("%s proxy %s: %s bytes", self.name, direction, len(data))

        except asyncio.CancelledError:
            pass  # Expected when the other forwarding direction closes first
        except ConnectionResetError:
            logger.debug("%s proxy %s: connection reset", self.name, direction)
        except BrokenPipeError:
            logger.debug("%s proxy %s: broken pipe", self.name, direction)
        except OSError as e:
            logger.debug("%s proxy %s error: %s", self.name, direction, e)

        # Flush any remaining buffered data
        if buffer:
            try:
                writer.write(buffer)
                await writer.drain()
            except OSError:
                pass  # Best-effort flush of remaining FTP control data

        logger.debug("%s proxy %s: total %s bytes", self.name, direction, total_bytes)

    async def _maybe_rewrite_pasv(
        self, line: bytes, local_ip: str, session_state: dict[str, str | ssl.SSLSession]
    ) -> bytes:
        """Rewrite PASV/EPSV response to point to a local data proxy."""
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError:
            return line

        # 227 Entering Passive Mode (h1,h2,h3,h4,p1,p2)
        if text.startswith("227 "):
            match = re.search(r"\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", text)
            if match:
                h1, h2, h3, h4, p1, p2 = (int(x) for x in match.groups())
                printer_ip = f"{h1}.{h2}.{h3}.{h4}"
                printer_port = p1 * 256 + p2

                local_port = await self._create_data_proxy(printer_ip, printer_port, session_state)
                if local_port:
                    ip_parts = local_ip.split(".")
                    lp1 = local_port // 256
                    lp2 = local_port % 256
                    rewritten = (
                        f"227 Entering Passive Mode "
                        f"({ip_parts[0]},{ip_parts[1]},{ip_parts[2]},{ip_parts[3]},{lp1},{lp2})"
                    )
                    logger.info("FTP PASV rewrite: %s:%s → %s:%s", printer_ip, printer_port, local_ip, local_port)
                    return rewritten.encode("utf-8")
                else:
                    logger.error("FTP PASV: failed to create data proxy for %s:%s", printer_ip, printer_port)
            else:
                logger.warning("FTP PASV: 227 response didn't match expected format: %s", text[:100])

        # 229 Entering Extended Passive Mode (|||port|)
        elif text.startswith("229 "):
            match = re.search(r"\(\|\|\|(\d+)\|\)", text)
            if match:
                printer_port = int(match.group(1))

                local_port = await self._create_data_proxy(self.target_host, printer_port, session_state)
                if local_port:
                    rewritten = f"229 Entering Extended Passive Mode (|||{local_port}|)"
                    logger.info("FTP EPSV rewrite: port %s → %s", printer_port, local_port)
                    return rewritten.encode("utf-8")
                else:
                    logger.error("FTP EPSV: failed to create data proxy for port %s", printer_port)
            else:
                logger.warning("FTP EPSV: 229 response didn't match expected format: %s", text[:100])

        return line

    async def _create_data_proxy(
        self, printer_ip: str, printer_port: int, session_state: dict[str, str | ssl.SSLSession]
    ) -> int | None:
        """Create a one-shot proxy for an FTP data connection.

        Prefers the printer's original passive port so the port number stays
        the same in the rewritten PASV/EPSV response.  This is critical when
        the slicer's FTP bounce-attack protection overrides the IP in the PASV
        response: the slicer connects to <control_IP>:<port>, and if iptables
        REDIRECT maps that port to the local machine, the data proxy must be
        listening on the *same* port number.

        Falls back to a random port if the original is unavailable.

        Uses TLS or cleartext based on the session's PROT level:
        - PROT P: TLS on both slicer and printer data connections
        - PROT C: cleartext on both sides (common for A1/H2D printers)

        Returns the local port number, or None if binding failed.
        """
        use_tls = session_state.get("prot") == "P"
        logger.info(
            "FTP data proxy: creating data proxy for %s:%s (printer-side %s)",
            printer_ip,
            printer_port,
            "TLS" if use_tls else "cleartext",
        )

        # Get control channel TLS session for data channel reuse
        tls_session = session_state.get("tls_session") if use_tls else None

        # Try the printer's original port first — this ensures the port
        # matches even when bounce protection or iptables REDIRECT is in play.
        try:
            await self._start_data_proxy_server(printer_port, printer_ip, printer_port, use_tls, tls_session)
            logger.info("FTP data proxy: using printer's port %s", printer_port)
            return printer_port
        except OSError as e:
            logger.debug(
                "FTP data proxy: printer port %s unavailable (%s), trying random",
                printer_port,
                e,
            )

        for _attempt in range(10):
            port = random.randint(self.PASV_PORT_MIN, self.PASV_PORT_MAX)
            try:
                await self._start_data_proxy_server(port, printer_ip, printer_port, use_tls, tls_session)
                logger.info("FTP data proxy: using random port %s", port)
                return port
            except OSError:
                continue

        logger.error("Failed to bind FTP data proxy port after 10 attempts")
        return None

    async def _start_data_proxy_server(
        self,
        port: int,
        printer_ip: str,
        printer_port: int,
        use_tls: bool,
        tls_session: ssl.SSLSession | None = None,
    ) -> None:
        """Start a one-shot server for one FTP data connection.

        When the slicer connects, immediately connects to the printer's data
        port and buffers any slicer data until the printer connection is ready.
        This handles zero-byte uploads (verify_job) where the slicer closes
        the data channel before a naive proxy would finish its TLS handshake.

        The slicer-side listener is ALWAYS cleartext.  Even when the slicer
        sends PROT P on the control channel, Bambu Studio does not perform
        a TLS handshake on the data connection — it relies on the implicit
        FTPS control channel for authentication and sends data unencrypted.

        The printer-side outbound connection follows the PROT level:
        - PROT P (use_tls=True): TLS to the printer's data port
        - PROT C (use_tls=False): cleartext to the printer's data port

        This mirrors the control channel's TLS-termination architecture.

        Raises OSError if the port is already in use.
        """
        connected = asyncio.Event()
        server_holder: list[asyncio.Server] = []

        # Slicer side: ALWAYS cleartext — Bambu Studio does not do TLS on
        # the data channel even after sending PROT P.
        # Printer side: TLS if PROT P, cleartext if PROT C.
        # For TLS data connections, wrap the SSL context to reuse the
        # control channel's TLS session if available. vsFTPd (X1C) requires
        # require_ssl_reuse — without this, data connections are rejected
        # with "522 SSL connection failed: session reuse required".
        if use_tls and tls_session:
            client_ssl = _SessionReuseSSLContext(self._client_ssl_context, tls_session)
            logger.debug("FTP data proxy: using TLS session reuse for port %s", port)
        else:
            client_ssl = self._client_ssl_context if use_tls else None

        # Slicer side is ALWAYS cleartext — Bambu Studio does not do TLS on
        # the data channel even after PROT P (confirmed for both H2D and X1C).
        printer_mode = "TLS" if use_tls else "cleartext"

        async def handle_data(
            client_reader: asyncio.StreamReader,
            client_writer: asyncio.StreamWriter,
        ) -> None:
            """Handle one FTP data connection, then close the server."""
            peername = client_writer.get_extra_info("peername")
            data_client = f"{peername[0]}:{peername[1]}" if peername else "unknown"
            logger.info(
                "FTP data proxy port %s (slicer=cleartext, printer=%s): client connected from %s, bridging to %s:%s",
                port,
                printer_mode,
                data_client,
                printer_ip,
                printer_port,
            )
            connected.set()
            # One-shot: close server after accepting first connection
            if server_holder:
                server_holder[0].close()

            printer_writer = None
            try:
                # Buffer any slicer data while connecting to printer.
                # This handles the race where the slicer sends data (or closes
                # for zero-byte files) before the TLS handshake completes.
                slicer_buffer = bytearray()
                slicer_eof = False

                async def buffer_slicer():
                    nonlocal slicer_eof
                    while True:
                        chunk = await client_reader.read(65536)
                        if not chunk:
                            slicer_eof = True
                            return
                        slicer_buffer.extend(chunk)

                buffer_task = asyncio.create_task(buffer_slicer())

                # Connect to printer's data port
                printer_reader, printer_writer = await asyncio.wait_for(
                    asyncio.open_connection(printer_ip, printer_port, ssl=client_ssl),
                    timeout=10.0,
                )
                logger.info(
                    "FTP data proxy port %s (printer=%s): connected to printer %s:%s",
                    port,
                    printer_mode,
                    printer_ip,
                    printer_port,
                )

                # Stop buffering
                buffer_task.cancel()
                try:
                    await buffer_task
                except asyncio.CancelledError:
                    pass

                # Flush buffered slicer data to printer
                logger.info(
                    "FTP data proxy port %s: buffer=%s bytes, slicer_eof=%s",
                    port,
                    len(slicer_buffer),
                    slicer_eof,
                )
                if slicer_buffer:
                    printer_writer.write(bytes(slicer_buffer))
                    await printer_writer.drain()

                # Forward remaining slicer data to printer, then close the
                # printer side to signal upload complete.
                #
                # Bambu Studio does NOT close the FTP data channel after sending
                # STOR data — it keeps the connection open and waits for the
                # printer to close its side + send 226 on the control channel.
                # A naive bidirectional proxy deadlocks here because the proxy
                # waits for the slicer EOF that never comes.
                #
                # Fix: read slicer data with an idle timeout. Once data has been
                # received and the slicer goes quiet, close the printer side so
                # the printer can send 226. For RETR (download), the printer
                # sends data and closes — the slicer reads until EOF — so this
                # unidirectional approach works for both directions.
                total_c2p = len(slicer_buffer)
                if not slicer_eof:
                    # Read remaining slicer data with idle detection.
                    # Must be short — Bambu Studio expects 226 almost instantly
                    # after sending data. Too long and the slicer times out.
                    idle_timeout = 0.3
                    while True:
                        try:
                            chunk = await asyncio.wait_for(client_reader.read(65536), timeout=idle_timeout)
                        except TimeoutError:
                            if total_c2p > 0:
                                # Slicer sent data then went idle — upload done
                                logger.debug(
                                    "FTP data proxy port %s: slicer idle after %s bytes, closing printer side",
                                    port,
                                    total_c2p,
                                )
                                break
                            continue  # No data yet, keep waiting
                        if not chunk:
                            break  # Slicer closed
                        printer_writer.write(chunk)
                        await printer_writer.drain()
                        total_c2p += len(chunk)

                logger.debug("FTP proxy data_c2p: total %s bytes", total_c2p)

                # Close printer side to signal upload complete.
                # For TLS, close() sends close_notify which the printer treats
                # as end-of-data. The printer then sends 226 on the control
                # channel. For RETR, this is a no-op since the printer closes
                # first and we'd have exited the loop above via EOF.
                try:
                    printer_writer.close()
                    await printer_writer.wait_closed()
                except OSError:
                    pass

                # Wait for 226 response to propagate through the FTP control
                # channel before closing the slicer's data channel.
                #
                # Without this delay, the data channel FIN arrives at the
                # slicer before the 226 response on the control channel.
                # BambuStudio reacts to the data channel FIN within <1ms
                # by sending QUIT + closing the control channel — before
                # 226 arrives (~2-3ms network RTT). This causes verify_job
                # to be treated as failed and shows the login modal.
                #
                # In a direct connection, the printer sends 226 AND closes
                # the data channel simultaneously, so the slicer gets both
                # at once. The delay here emulates that timing.
                if total_c2p > 0:
                    await asyncio.sleep(0.5)

            except Exception as e:
                logger.error("FTP data proxy port %s: error: %s", port, e)
            finally:
                for w in [client_writer, printer_writer]:
                    if w:
                        try:
                            w.close()
                            await w.wait_closed()
                        except OSError:
                            pass  # Best-effort data connection cleanup
                logger.info("FTP data proxy port %s: connection closed", port)

        server = await asyncio.start_server(
            handle_data,
            "0.0.0.0",  # nosec B104
            port,
            # No TLS on slicer side — Bambu Studio doesn't do TLS on data
            # channel even after PROT P (confirmed by connection hang test).
        )
        server_holder.append(server)
        self._data_servers.append(server)

        # Auto-close after 60s if no connection arrives
        async def auto_close() -> None:
            try:
                await asyncio.wait_for(connected.wait(), timeout=60.0)
            except TimeoutError:
                logger.debug("FTP data proxy on port %s timed out, closing", port)
                try:
                    server.close()
                    await server.wait_closed()
                except OSError:
                    pass  # Best-effort timeout cleanup
            finally:
                if server in self._data_servers:
                    self._data_servers.remove(server)

        asyncio.create_task(auto_close(), name=f"ftp_data_timeout_{port}")

        logger.debug("FTP data proxy: port %s → %s:%s", port, printer_ip, printer_port)


class SlicerProxyManager:
    """Manages FTP and MQTT TLS proxies for a single printer target."""

    # Bambu printer ports
    PRINTER_FTP_PORT = 990
    PRINTER_MQTT_PORT = 8883
    PRINTER_FILE_TRANSFER_PORT = 6000
    PRINTER_RTSP_PORT = 322  # X1/H2/P2 series camera (A1/P1 use port 6000)
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
        self._bind_proxies: list[TCPProxy] = []
        self._bind_server = None
        self._probe_servers: list[asyncio.Server] = []
        self._tasks: list[asyncio.Task] = []

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

        # FTP control — raw TCP pass-through (end-to-end TLS with printer)
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

        if self._bind_server:
            await self._bind_server.stop()
            self._bind_server = None

        for bp in self._bind_proxies:
            await bp.stop()
        self._bind_proxies = []

        for dp in self._ftp_data_proxies:
            await dp.stop()
        self._ftp_data_proxies = []

        for srv in self._probe_servers:
            srv.close()
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
            "ftp_port": self.LOCAL_FTP_PORT,
            "mqtt_port": self.LOCAL_MQTT_PORT,
            "bind_ports": self.PRINTER_BIND_PORTS,
            "ftp_connections": (len(self._ftp_proxy._active_connections) if self._ftp_proxy else 0),
            "mqtt_connections": (len(self._mqtt_proxy._active_connections) if self._mqtt_proxy else 0),
            "bind_connections": sum(len(bp._active_connections) for bp in self._bind_proxies),
        }
