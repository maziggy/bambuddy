"""TLS proxy for slicer-to-printer communication.

This module provides a TLS terminating proxy that forwards data between
a slicer and a real Bambu printer, enabling remote printing over
any network connection.

Unlike a transparent TCP proxy, this terminates TLS on both ends:
- Slicer connects to Bambuddy using Bambuddy's certificate
- Bambuddy connects to printer using printer's certificate
- Data is decrypted, forwarded, and re-encrypted
"""

import asyncio
import logging
import random
import re
import ssl
import subprocess
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


def detect_port_redirect(port: int) -> int | None:
    """Detect if iptables redirects a port to another port.

    When iptables NAT REDIRECT rules exist (e.g. 990→9990), connections
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

        self._server: asyncio.Server | None = None
        self._running = False
        self._active_connections: dict[str, tuple[asyncio.Task, asyncio.Task]] = {}
        self._server_ssl_context: ssl.SSLContext | None = None
        self._client_ssl_context: ssl.SSLContext | None = None

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
            self._forward(printer_reader, client_writer, f"printer→{client_id}"),
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

    async def _forward(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        direction: str,
    ) -> None:
        """Forward data from reader to writer.

        Args:
            reader: Source stream (already TLS-decrypted)
            writer: Destination stream (will be TLS-encrypted by the stream)
            direction: Description for logging (e.g., "client→printer")
        """
        total_bytes = 0
        try:
            while self._running:
                # Read chunk - use reasonable buffer size
                data = await reader.read(65536)
                if not data:
                    # Connection closed
                    break

                # Forward to destination
                writer.write(data)
                await writer.drain()

                total_bytes += len(data)
                logger.debug("%s proxy %s: %s bytes", self.name, direction, len(data))

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

    Used for protocols where the printer doesn't use TLS (e.g., port 3002
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

        # Track data channel protection level per session.
        # PROT C = cleartext data, PROT P = TLS data.
        # Default to cleartext — many Bambu printers (A1, H2D) use PROT C.
        # If the slicer sends PROT P, we switch to TLS for data connections.
        session_state: dict[str, str] = {"prot": "C"}

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
        session_state: dict[str, str],
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

                    # Replace EPSV with PASV so response includes an IP
                    if cmd_upper == b"EPSV":
                        line = b"PASV"
                        logger.info("FTP command rewrite: EPSV → PASV")

                    # Track PROT level for data channel encryption
                    elif cmd_upper == b"PROT P":
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
        session_state: dict[str, str],
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

    async def _maybe_rewrite_pasv(self, line: bytes, local_ip: str, session_state: dict[str, str]) -> bytes:
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

    async def _create_data_proxy(self, printer_ip: str, printer_port: int, session_state: dict[str, str]) -> int | None:
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

        # Try the printer's original port first — this ensures the port
        # matches even when bounce protection or iptables REDIRECT is in play.
        try:
            await self._start_data_proxy_server(printer_port, printer_ip, printer_port, use_tls)
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
                await self._start_data_proxy_server(port, printer_ip, printer_port, use_tls)
                logger.info("FTP data proxy: using random port %s", port)
                return port
            except OSError:
                continue

        logger.error("Failed to bind FTP data proxy port after 10 attempts")
        return None

    async def _start_data_proxy_server(self, port: int, printer_ip: str, printer_port: int, use_tls: bool) -> None:
        """Start a one-shot server for one FTP data connection.

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
        client_ssl = self._client_ssl_context if use_tls else None
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
                # Connect to printer's data port
                printer_reader, printer_writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        printer_ip,
                        printer_port,
                        ssl=client_ssl,
                    ),
                    timeout=10.0,
                )
                logger.info(
                    "FTP data proxy port %s (printer=%s): connected to printer %s:%s",
                    port,
                    printer_mode,
                    printer_ip,
                    printer_port,
                )

                # Bidirectional data forwarding
                c2p = asyncio.create_task(self._forward(client_reader, printer_writer, "data_c2p"))
                p2c = asyncio.create_task(self._forward(printer_reader, client_writer, "data_p2c"))

                done, pending = await asyncio.wait([c2p, p2c], return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass  # Expected when other data direction closes
            except TimeoutError:
                logger.error("FTP data proxy port %s: timeout connecting to printer", port)
            except ssl.SSLError as e:
                logger.error("FTP data proxy port %s: SSL error to printer: %s", port, e)
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
            # channel even after PROT P. The proxy terminates TLS only on
            # the printer side (inside handle_data).
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
    ):
        """Initialize the slicer proxy manager.

        Args:
            target_host: Target printer IP address
            cert_path: Path to server certificate
            key_path: Path to server private key
            on_activity: Optional callback for activity logging (name, message)
            bind_address: IP address to bind proxy listeners to
        """
        self.target_host = target_host
        self.cert_path = cert_path
        self.key_path = key_path
        self.on_activity = on_activity
        self.bind_address = bind_address

        self._ftp_proxy: TLSProxy | None = None
        self._mqtt_proxy: TLSProxy | None = None
        self._bind_proxies: list[TCPProxy] = []
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """Start FTP and MQTT TLS proxies."""
        logger.info("Starting slicer TLS proxy to %s", self.target_host)

        # Detect iptables port redirect (e.g. 990→9990 for non-root installs).
        # If active, connections to port 990 get intercepted by iptables PREROUTING
        # and sent to the redirect target — our socket on 990 never sees them.
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

        # Create FTP proxy with PASV/EPSV awareness for data connections
        self._ftp_proxy = FTPTLSProxy(
            name="FTP",
            listen_port=ftp_listen_port,
            target_host=self.target_host,
            target_port=self.PRINTER_FTP_PORT,
            server_cert_path=self.cert_path,
            server_key_path=self.key_path,
            on_connect=lambda cid: self._log_activity("FTP", f"connected: {cid}"),
            on_disconnect=lambda cid: self._log_activity("FTP", f"disconnected: {cid}"),
            bind_address=self.bind_address,
        )

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
        )

        # Bind/auth proxy (ports 3000 + 3002) - raw TCP, no TLS
        # Different BambuStudio versions use different ports
        for bind_port in self.PRINTER_BIND_PORTS:
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
        async def run_with_logging(proxy: TLSProxy) -> None:
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
        ]
        for bp in self._bind_proxies:
            self._tasks.append(
                asyncio.create_task(
                    run_with_logging(bp),
                    name=f"slicer_proxy_bind_{bp.listen_port}",
                )
            )

        logger.info("Slicer TLS proxy started for %s", self.target_host)

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

        for bp in self._bind_proxies:
            await bp.stop()
        self._bind_proxies = []

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
