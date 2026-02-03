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
import ssl
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


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
        """
        self.name = name
        self.listen_port = listen_port
        self.target_host = target_host
        self.target_port = target_port
        self.server_cert_path = server_cert_path
        self.server_key_path = server_key_path
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect

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
            f"Starting {self.name} TLS proxy: 0.0.0.0:{self.listen_port} → {self.target_host}:{self.target_port}"
        )

        try:
            self._running = True

            # Create SSL contexts
            self._server_ssl_context = self._create_server_ssl_context()
            self._client_ssl_context = self._create_client_ssl_context()

            # Start server with TLS
            self._server = await asyncio.start_server(
                self._handle_client,
                "0.0.0.0",
                self.listen_port,
                ssl=self._server_ssl_context,
            )

            logger.info(f"{self.name} TLS proxy listening on port {self.listen_port}")

            async with self._server:
                await self._server.serve_forever()

        except OSError as e:
            if e.errno == 98:  # Address already in use
                logger.error(f"{self.name} proxy port {self.listen_port} is already in use")
            else:
                logger.error(f"{self.name} proxy error: {e}")
        except asyncio.CancelledError:
            logger.debug(f"{self.name} proxy task cancelled")
        except Exception as e:
            logger.error(f"{self.name} proxy error: {e}")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the TLS proxy server."""
        logger.info(f"Stopping {self.name} proxy")
        self._running = False

        # Cancel all active connection tasks
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
            except Exception as e:
                logger.debug(f"Error closing {self.name} proxy server: {e}")
            self._server = None

    async def _handle_client(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a new client connection by proxying to target."""
        peername = client_writer.get_extra_info("peername")
        client_id = f"{peername[0]}:{peername[1]}" if peername else "unknown"

        logger.info(f"{self.name} proxy: client connected from {client_id}")

        if self.on_connect:
            try:
                self.on_connect(client_id)
            except Exception:
                pass

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
            logger.info(f"{self.name} proxy: connected to printer {self.target_host}:{self.target_port}")
        except TimeoutError:
            logger.error(f"{self.name} proxy: timeout connecting to {self.target_host}:{self.target_port}")
            client_writer.close()
            await client_writer.wait_closed()
            return
        except ssl.SSLError as e:
            logger.error(f"{self.name} proxy: SSL error connecting to {self.target_host}:{self.target_port}: {e}")
            client_writer.close()
            await client_writer.wait_closed()
            return
        except Exception as e:
            logger.error(f"{self.name} proxy: failed to connect to {self.target_host}:{self.target_port}: {e}")
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
                    pass

        except Exception as e:
            logger.debug(f"{self.name} proxy connection error: {e}")
        finally:
            # Clean up
            self._active_connections.pop(client_id, None)

            for writer in [client_writer, printer_writer]:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

            logger.info(f"{self.name} proxy: client {client_id} disconnected")

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
                logger.debug(f"{self.name} proxy {direction}: {len(data)} bytes")

        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            logger.debug(f"{self.name} proxy {direction}: connection reset")
        except BrokenPipeError:
            logger.debug(f"{self.name} proxy {direction}: broken pipe")
        except Exception as e:
            logger.debug(f"{self.name} proxy {direction} error: {e}")

        logger.debug(f"{self.name} proxy {direction}: total {total_bytes} bytes")


class SlicerProxyManager:
    """Manages FTP and MQTT TLS proxies for a single printer target."""

    # Bambu printer ports
    PRINTER_FTP_PORT = 990
    PRINTER_MQTT_PORT = 8883

    # Local listen ports (same as virtual printer)
    LOCAL_FTP_PORT = 9990
    LOCAL_MQTT_PORT = 8883

    def __init__(
        self,
        target_host: str,
        cert_path: Path,
        key_path: Path,
        on_activity: Callable[[str, str], None] | None = None,
    ):
        """Initialize the slicer proxy manager.

        Args:
            target_host: Target printer IP address
            cert_path: Path to server certificate
            key_path: Path to server private key
            on_activity: Optional callback for activity logging (name, message)
        """
        self.target_host = target_host
        self.cert_path = cert_path
        self.key_path = key_path
        self.on_activity = on_activity

        self._ftp_proxy: TLSProxy | None = None
        self._mqtt_proxy: TLSProxy | None = None
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """Start FTP and MQTT TLS proxies."""
        logger.info(f"Starting slicer TLS proxy to {self.target_host}")

        # Create proxies with TLS
        self._ftp_proxy = TLSProxy(
            name="FTP",
            listen_port=self.LOCAL_FTP_PORT,
            target_host=self.target_host,
            target_port=self.PRINTER_FTP_PORT,
            server_cert_path=self.cert_path,
            server_key_path=self.key_path,
            on_connect=lambda cid: self._log_activity("FTP", f"connected: {cid}"),
            on_disconnect=lambda cid: self._log_activity("FTP", f"disconnected: {cid}"),
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
        )

        # Start as background tasks
        async def run_with_logging(proxy: TLSProxy) -> None:
            try:
                await proxy.start()
            except Exception as e:
                logger.error(f"Slicer proxy {proxy.name} failed: {e}")

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

        logger.info(f"Slicer TLS proxy started for {self.target_host}")

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
                pass

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
            "ftp_connections": (len(self._ftp_proxy._active_connections) if self._ftp_proxy else 0),
            "mqtt_connections": (len(self._mqtt_proxy._active_connections) if self._mqtt_proxy else 0),
        }
