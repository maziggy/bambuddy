"""Implicit FTPS server for receiving 3MF uploads from slicers.

Implements an implicit FTPS server (TLS from byte 0) that accepts file uploads
from Bambu Studio and OrcaSlicer, matching the real Bambu printer behavior.

Unlike explicit FTPS (AUTH TLS), implicit FTPS wraps the connection in TLS
immediately upon connection, before any FTP commands are exchanged.
"""

import asyncio
import hmac
import logging
import os
import random
import ssl
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# Default FTP port for Bambu printers (implicit FTPS).
# Must be 990 (same as real printers) to avoid iptables REDIRECT,
# which rewrites the destination IP to the incoming interface's primary
# address — breaking multi-VP setups with different bind IPs.
# Requires CAP_NET_BIND_SERVICE or root.
FTP_PORT = 990

# Hard cap on a single upload. 4 GiB covers the largest realistic
# multi-plate .gcode.3mf and rejects runaway / malicious clients before
# they can exhaust the disk or OOM the host. STOR still buffers the
# whole file in memory before write_bytes — peak RSS ~2x file size during
# the b''.join — so the cap also caps that peak. If real users hit it
# with a legitimate file, raise here.
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024  # 4 GiB


class FTPSession:
    """Handles a single FTP client session."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        upload_dir: Path,
        access_code: str,
        ssl_context: ssl.SSLContext,
        on_file_received: Callable[[Path, str], None] | None,
        passive_port_range: tuple[int, int] = (50000, 50100),
        pasv_address: str = "",
        bind_address: str = "0.0.0.0",  # nosec B104
        vp_name: str = "",
    ):
        self.reader = reader
        self.writer = writer
        self.upload_dir = upload_dir
        self.access_code = access_code
        self.ssl_context = ssl_context
        self.on_file_received = on_file_received
        self.passive_port_range = passive_port_range
        self.pasv_address = pasv_address
        self.bind_address = bind_address
        self.vp_name = vp_name
        self._log_prefix = f"[{vp_name}] " if vp_name else ""

        self.authenticated = False
        self.username: str | None = None
        self.current_dir = upload_dir
        self.transfer_type = "A"  # ASCII by default
        self.data_server: asyncio.Server | None = None
        self.data_port: int | None = None

        # For data transfer coordination
        self._data_reader: asyncio.StreamReader | None = None
        self._data_writer: asyncio.StreamWriter | None = None
        self._data_connected = asyncio.Event()
        self._transfer_done = asyncio.Event()

        peername = writer.get_extra_info("peername")
        self.remote_ip = peername[0] if peername else "unknown"

    async def send(self, code: int, message: str) -> None:
        """Send an FTP response."""
        response = f"{code} {message}\r\n"
        logger.debug("%sFTP -> %s: %s", self._log_prefix, self.remote_ip, response.strip())
        self.writer.write(response.encode("utf-8"))
        await self.writer.drain()

    async def handle(self) -> None:
        """Handle the FTP session."""
        try:
            # Send welcome banner
            await self.send(220, "Bambuddy Virtual Printer FTP ready")

            while True:
                try:
                    line = await asyncio.wait_for(
                        self.reader.readline(),
                        timeout=300,  # 5 minute timeout
                    )
                except TimeoutError:
                    logger.debug("%sFTP session timeout from %s", self._log_prefix, self.remote_ip)
                    break

                if not line:
                    break

                try:
                    command_line = line.decode("utf-8").strip()
                except UnicodeDecodeError:
                    command_line = line.decode("latin-1").strip()

                if not command_line:
                    continue

                # Never log passwords
                if command_line.upper().startswith("PASS"):
                    logger.debug("%sFTP <- %s: PASS ********", self._log_prefix, self.remote_ip)
                else:
                    logger.debug("%sFTP <- %s: %s", self._log_prefix, self.remote_ip, command_line)

                # Parse command and argument
                parts = command_line.split(" ", 1)
                cmd = parts[0].upper()
                arg = parts[1] if len(parts) > 1 else ""

                # Dispatch command
                handler = getattr(self, f"cmd_{cmd}", None)
                if handler:
                    await handler(arg)
                else:
                    logger.debug("%sFTP command not implemented: %s", self._log_prefix, cmd)
                    await self.send(502, f"Command {cmd} not implemented")

        except asyncio.CancelledError:
            logger.info("%sFTP session cancelled from %s", self._log_prefix, self.remote_ip)
        except Exception as e:
            logger.error("%sFTP session error from %s: %s", self._log_prefix, self.remote_ip, e)
        finally:
            logger.info("%sFTP session ended from %s", self._log_prefix, self.remote_ip)
            await self._cleanup()

    async def _cleanup(self) -> None:
        """Clean up session resources."""
        # Release any waiting data connection callback
        self._transfer_done.set()

        if self.data_server:
            self.data_server.close()
            try:
                await self.data_server.wait_closed()
            except OSError:
                pass  # Best-effort data server cleanup; may already be closed
            self.data_server = None

        try:
            self.writer.close()
            await self.writer.wait_closed()
        except OSError:
            pass  # Best-effort control connection cleanup; client may have disconnected

    # FTP Commands

    async def cmd_USER(self, arg: str) -> None:
        """Handle USER command."""
        self.username = arg
        if arg.lower() == "bblp":
            await self.send(331, "Password required")
        else:
            await self.send(530, "Invalid user")

    async def cmd_PASS(self, arg: str) -> None:
        """Handle PASS command."""
        if self.username and self.username.lower() == "bblp":
            # ``hmac.compare_digest`` is constant-time — keeps the auth check
            # from leaking the access code via response timing under network
            # jitter. LAN-only threat is marginal; this is the standard fix.
            if hmac.compare_digest(arg, self.access_code):
                self.authenticated = True
                await self.send(230, "Login successful")
                logger.info("%sFTP login from %s", self._log_prefix, self.remote_ip)
            else:
                await self.send(530, "Login incorrect")
                logger.warning("%sFTP failed login from %s (access code mismatch)", self._log_prefix, self.remote_ip)
        else:
            await self.send(503, "Login with USER first")

    async def cmd_SYST(self, arg: str) -> None:
        """Handle SYST command."""
        await self.send(215, "UNIX Type: L8")

    async def cmd_FEAT(self, arg: str) -> None:
        """Handle FEAT command."""
        features = [
            "211-Features:",
            " PASV",
            " EPSV",
            " UTF8",
            " SIZE",
            "211 End",
        ]
        for line in features[:-1]:
            self.writer.write(f"{line}\r\n".encode())
        await self.writer.drain()
        self.writer.write(f"{features[-1]}\r\n".encode())
        await self.writer.drain()

    async def cmd_PWD(self, arg: str) -> None:
        """Handle PWD command."""
        if not self.authenticated:
            await self.send(530, "Not logged in")
            return
        await self.send(257, '"/" is current directory')

    async def cmd_CWD(self, arg: str) -> None:
        """Handle CWD command."""
        if not self.authenticated:
            await self.send(530, "Not logged in")
            return
        # Accept any directory change (we use a flat structure)
        await self.send(250, "Directory changed")

    async def cmd_TYPE(self, arg: str) -> None:
        """Handle TYPE command."""
        if not self.authenticated:
            await self.send(530, "Not logged in")
            return
        if arg.upper() in ("A", "I"):
            self.transfer_type = arg.upper()
            type_name = "ASCII" if arg.upper() == "A" else "Binary"
            await self.send(200, f"Type set to {type_name}")
        else:
            await self.send(504, "Type not supported")

    async def _bind_passive_port(self) -> bool:
        """Try to bind a passive data port with retries.

        Returns True if a port was successfully bound, False otherwise.
        Sets self.data_server and self.data_port on success.
        """
        port_min, port_max = self.passive_port_range
        for attempt in range(10):
            port = random.randint(port_min, port_max)
            try:
                self.data_server = await asyncio.start_server(
                    self._handle_data_connection,
                    self.bind_address,
                    port,
                    ssl=self.ssl_context,
                )
                self.data_port = port
                return True
            except OSError:
                logger.debug("FTP passive port %s in use, retrying (%s/10)", port, attempt + 1)
        return False

    async def cmd_EPSV(self, arg: str) -> None:
        """Handle EPSV command - Extended Passive Mode (IPv6 compatible)."""
        if not self.authenticated:
            await self.send(530, "Not logged in")
            return

        # Close any existing data connection/server
        await self._close_data_connection()

        # Reset connection state for the new transfer
        self._data_connected.clear()
        self._data_reader = None
        self._data_writer = None
        self._transfer_done = asyncio.Event()

        if await self._bind_passive_port():
            # EPSV response format: 229 Entering Extended Passive Mode (|||port|)
            await self.send(229, f"Entering Extended Passive Mode (|||{self.data_port}|)")
            logger.info("FTP EPSV listening on port %s", self.data_port)
        else:
            logger.error("Failed to bind any passive port for EPSV")
            await self.send(425, "Cannot open data connection")

    async def cmd_PASV(self, arg: str) -> None:
        """Handle PASV command - set up passive data connection."""
        if not self.authenticated:
            await self.send(530, "Not logged in")
            return

        # Close any existing data connection/server
        await self._close_data_connection()

        # Reset connection state for the new transfer
        self._data_connected.clear()
        self._data_reader = None
        self._data_writer = None
        self._transfer_done = asyncio.Event()

        if await self._bind_passive_port():
            # Determine the IP to advertise in PASV response
            if self.pasv_address:
                # Explicit override (e.g., for Docker bridge mode behind NAT)
                ip = self.pasv_address
            else:
                # Use the local IP of the control connection
                sockname = self.writer.get_extra_info("sockname")
                ip = sockname[0] if sockname else "127.0.0.1"
                # 0.0.0.0 is not routable — fall back to control connection IP
                if ip == "0.0.0.0":  # nosec B104
                    ip = "127.0.0.1"

            # Format IP and port for PASV response
            ip_parts = ip.split(".")
            port_hi = self.data_port // 256
            port_lo = self.data_port % 256

            await self.send(
                227,
                f"Entering Passive Mode ({ip_parts[0]},{ip_parts[1]},{ip_parts[2]},{ip_parts[3]},{port_hi},{port_lo})",
            )
            logger.info("FTP PASV listening on %s:%s", ip, self.data_port)
        else:
            logger.error("Failed to bind any passive port for PASV")
            await self.send(425, "Cannot open data connection")

    async def _handle_data_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Handle incoming data connection (used by PASV/EPSV).

        This callback stays alive until the transfer completes to ensure the
        asyncio task holds strong references to the reader/writer throughout
        the data transfer.  If the callback returned immediately, the task
        would complete and the StreamReaderProtocol could release its strong
        reader reference, potentially destabilising the connection.
        """
        # Reject duplicate connections — only one data connection per transfer
        if self._data_reader is not None:
            logger.warning("FTP rejecting duplicate data connection from %s", self.remote_ip)
            try:
                writer.close()
                await writer.wait_closed()
            except OSError:
                pass
            return

        # Log TLS details for debugging
        ssl_obj = writer.get_extra_info("ssl_object")
        if ssl_obj:
            logger.info(
                f"FTP data TLS from {self.remote_ip}: cipher={ssl_obj.cipher()}, "
                f"version={ssl_obj.version()}, session_reused={ssl_obj.session_reused}"
            )
        else:
            logger.warning("FTP data connection from %s has no SSL!", self.remote_ip)

        logger.info("FTP data connection established from %s", self.remote_ip)
        self._data_reader = reader
        self._data_writer = writer

        # Stop accepting further connections on the passive port
        if self.data_server:
            self.data_server.close()

        self._data_connected.set()

        # Keep this callback alive until the transfer command (STOR/RETR)
        # finishes. This ensures the asyncio server-handler task holds strong
        # references to reader/writer for the entire transfer lifetime.
        await self._transfer_done.wait()

    async def _close_data_connection(self) -> None:
        """Close the data connection and server."""
        had_connection = self._data_writer is not None or self.data_server is not None

        # Signal the _handle_data_connection callback to return, allowing
        # its asyncio task to complete cleanly.
        self._transfer_done.set()

        if self._data_writer:
            try:
                self._data_writer.close()
                await self._data_writer.wait_closed()
            except OSError:
                pass  # Best-effort data writer cleanup; peer may have closed already
            self._data_writer = None
            self._data_reader = None

        if self.data_server:
            try:
                self.data_server.close()
                await self.data_server.wait_closed()
            except OSError:
                pass  # Best-effort data server shutdown; port may already be released
            self.data_server = None

        # Only delay if we actually closed something
        if had_connection:
            await asyncio.sleep(0.1)

    async def cmd_STOR(self, arg: str) -> None:
        """Handle STOR command - receive file upload.

        Streams each chunk directly to disk inside the receive loop instead
        of buffering the whole file in a ``list[bytes]`` and joining at the
        end. Wire protocol unchanged — same 150/226/426 sequence, same
        single-write target path (no ``.part`` or atomic rename), no new
        verbs, no concurrency guard. The visible behaviour difference is
        that the destination file grows progressively during upload rather
        than appearing all-at-once on completion; slicers don't LIST during
        STOR, so this isn't observable. Peak RSS for a multi-GB upload
        drops from ~2× file size to one chunk (64 KiB).
        ``MAX_UPLOAD_BYTES`` cap kept — purely server-internal DoS guard.
        """
        if not self.authenticated:
            await self.send(530, "Not logged in")
            return

        if not self.data_server and not self._data_connected.is_set():
            await self.send(425, "Use PASV first")
            return

        filename = Path(arg).name  # Sanitize filename
        file_path = (
            self.upload_dir / filename
        )  # SEC-PATH-OK: filename = Path(arg).name strips every path component above

        logger.info("FTP receiving file: %s from %s", filename, self.remote_ip)

        await self.send(150, f"Opening data connection for {filename}")

        # Wait for data connection to be established (client connects after 150)
        try:
            await asyncio.wait_for(self._data_connected.wait(), timeout=30)
        except TimeoutError:
            logger.error("FTP data connection timeout - client didn't connect")
            await self.send(425, "Data connection timeout")
            await self._close_data_connection()
            return

        if not self._data_reader:
            await self.send(425, "Data connection failed")
            await self._close_data_connection()
            return

        # Receive + stream to disk
        total_received = 0
        write_failed: Exception | None = None
        try:
            with file_path.open("wb") as f:
                while True:
                    chunk = await asyncio.wait_for(self._data_reader.read(65536), timeout=60)
                    if not chunk:
                        break
                    total_received += len(chunk)
                    if total_received > MAX_UPLOAD_BYTES:
                        raise OSError(f"upload exceeded size cap ({total_received} > {MAX_UPLOAD_BYTES} bytes)")
                    f.write(chunk)
                    logger.debug("FTP received chunk: %s bytes (total: %s)", len(chunk), total_received)
        except TimeoutError:
            logger.error("FTP data transfer timeout after %s bytes for %s", total_received, filename)
            write_failed = TimeoutError("Transfer timeout")
        except Exception as e:
            logger.error(
                "FTP data transfer error after %s bytes for %s: %s(%s)",
                total_received,
                filename,
                type(e).__name__,
                e,
            )
            write_failed = e

        # Close data connection
        await self._close_data_connection()

        if write_failed is not None:
            # Drop the partial file so it doesn't masquerade as a complete
            # upload — buffer-then-write never had a partial-file footprint.
            try:
                file_path.unlink(missing_ok=True)
            except OSError:
                pass
            await self.send(426, f"Transfer failed: {write_failed}")
            return

        # Confirm + notify
        logger.info("FTP saved file: %s (%s bytes)", file_path, total_received)
        await self.send(226, "Transfer complete")

        if self.on_file_received:
            try:
                result = self.on_file_received(file_path, self.remote_ip)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error("File received callback error: %s", e)

    async def cmd_SIZE(self, arg: str) -> None:
        """Handle SIZE command."""
        if not self.authenticated:
            await self.send(530, "Not logged in")
            return
        # We don't store files for SIZE queries
        await self.send(550, "File not found")

    async def cmd_QUIT(self, arg: str) -> None:
        """Handle QUIT command."""
        await self.send(221, "Goodbye")
        raise asyncio.CancelledError()

    async def cmd_NOOP(self, arg: str) -> None:
        """Handle NOOP command."""
        await self.send(200, "OK")

    async def cmd_OPTS(self, arg: str) -> None:
        """Handle OPTS command."""
        if arg.upper().startswith("UTF8"):
            await self.send(200, "UTF8 mode enabled")
        else:
            await self.send(501, "Option not supported")

    async def cmd_PBSZ(self, arg: str) -> None:
        """Handle PBSZ (Protection Buffer Size) command.

        Required for FTP security extensions. With TLS, buffer size is 0.
        """
        await self.send(200, "PBSZ=0")

    async def cmd_PROT(self, arg: str) -> None:
        """Handle PROT (Data Channel Protection Level) command.

        P = Private (encrypted), which we always use with implicit FTPS.
        """
        if arg.upper() == "P":
            await self.send(200, "Protection level set to Private")
        elif arg.upper() == "C":
            # Clear (unprotected) - we don't support this
            await self.send(536, "Protection level C not supported")
        else:
            await self.send(504, f"Protection level {arg} not supported")

    async def cmd_MKD(self, arg: str) -> None:
        """Handle MKD (Make Directory) command."""
        if not self.authenticated:
            await self.send(530, "Not logged in")
            return
        # We don't really create directories, just pretend it works
        await self.send(257, f'"{arg}" directory created')

    async def cmd_LIST(self, arg: str) -> None:
        """Handle LIST command - list directory contents.

        Intentionally answers 150 + 226 without opening the passive data
        channel. Bambuddy is an upload-only VP — no slicer in capture logs
        actually issues LIST during the project_file flow, so the
        no-data-conn ack is what every observed slicer accepts. A previous
        audit recommended opening + closing the data conn for protocol
        purity; reverted because (a) the bug was theoretical, (b) slicer
        compatibility matters more than RFC purity here, and (c) adding
        NLST/MLSD alongside changes the "supported verbs" surface in a way
        we cannot regression-test without every supported slicer build.
        """
        if not self.authenticated:
            await self.send(530, "Not logged in")
            return
        # We don't support listing, return empty
        await self.send(150, "Opening data connection")
        await self.send(226, "Transfer complete")


class VirtualPrinterFTPServer:
    """Implicit FTPS server that accepts uploads from slicers."""

    # Passive-mode data port range. Widened from 50000-50100 (101 ports) to
    # 50000-51000 (1001 ports) so concurrent transfers across multiple VPs
    # — particularly when a VP falls back to bind 0.0.0.0 (manager.py picks
    # this when bind_ip is unset) — don't collide. With 101 ports and 10
    # random pick attempts per session, birthday-style collisions hit
    # under load; 1001 ports gives multi-VP setups headroom.
    PASSIVE_PORT_MIN = 50000
    PASSIVE_PORT_MAX = 51000

    def __init__(
        self,
        upload_dir: Path,
        access_code: str,
        cert_path: Path,
        key_path: Path,
        port: int = FTP_PORT,
        on_file_received: Callable[[Path, str], None] | None = None,
        bind_address: str = "0.0.0.0",  # nosec B104
        vp_name: str = "",
    ):
        """Initialize the FTPS server.

        Args:
            upload_dir: Directory to store uploaded files
            access_code: Password for authentication (bblp user)
            cert_path: Path to TLS certificate file
            key_path: Path to TLS private key file
            port: Port to listen on (default 990)
            on_file_received: Callback when file upload completes (path, source_ip)
            bind_address: IP address to bind to (default 0.0.0.0)
            vp_name: Virtual printer name for log identification
        """
        self.upload_dir = upload_dir
        self.access_code = access_code
        self.cert_path = cert_path
        self.key_path = key_path
        self.port = port
        self.on_file_received = on_file_received
        self.bind_address = bind_address
        self.vp_name = vp_name
        self._server: asyncio.Server | None = None
        self._running = False
        # Set after the socket is bound and the server is accepting connections,
        # so VirtualPrinterInstance.start_server can wait for readiness before
        # reporting is_running=True. Without this, a caller racing the start
        # could probe the port and see "connection refused" while is_running
        # already says yes.
        self.ready = asyncio.Event()
        self._ssl_context: ssl.SSLContext | None = None
        self._active_sessions: list[asyncio.Task] = []
        # Override PASV response IP for Docker bridge mode / NAT environments
        self._pasv_address = os.environ.get("VIRTUAL_PRINTER_PASV_ADDRESS", "")

    async def start(self) -> None:
        """Start the implicit FTPS server."""
        if self._running:
            return

        logger.info("[%s] Starting virtual printer implicit FTPS on %s:%s", self.vp_name, self.bind_address, self.port)

        # Ensure upload directory exists
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        cache_dir = self.upload_dir / "cache"
        cache_dir.mkdir(exist_ok=True)

        # Create SSL context for implicit FTPS (TLS from byte 0).
        # Pinned to TLS 1.2 only. Allowing 1.3 broke BambuStudio mid-upload
        # in the field (session_reused=True on data channel via PSK + libcurl
        # CURLE_PARTIAL_FILE / RST after ~80 KiB; "server did not report OK,
        # got 426"). Real Bambu printers also serve their FTPS at 1.2 only,
        # and the slicer expects to match that. A future slicer drop of 1.2
        # is a problem to solve when it actually happens; until then 1.2 is
        # mandatory for compat.
        self._ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ssl_context.load_cert_chain(str(self.cert_path), str(self.key_path))
        self._ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        self._ssl_context.maximum_version = ssl.TLSVersion.TLSv1_2

        # Use standard TLS settings for compatibility
        self._ssl_context.set_ciphers("HIGH:!aNULL:!MD5:!RC4")

        logger.info("FTP SSL context created with standard settings")

        try:
            # Create server with SSL - TLS handshake happens before any FTP data
            self._server = await asyncio.start_server(
                self._handle_client,
                self.bind_address,
                self.port,
                ssl=self._ssl_context,  # This makes it implicit FTPS!
            )
            self._running = True
            self.ready.set()

            logger.info("Implicit FTPS server started on port %s", self.port)
            logger.info(
                "FTP passive data port range: %s-%s",
                self.PASSIVE_PORT_MIN,
                self.PASSIVE_PORT_MAX,
            )
            if self._pasv_address:
                logger.info("FTP PASV address override: %s", self._pasv_address)

            async with self._server:
                await self._server.serve_forever()

        except OSError as e:
            if e.errno == 98:  # Address already in use
                logger.error("FTP port %s is already in use", self.port)
            else:
                logger.error("FTP server error: %s", e)
        except asyncio.CancelledError:
            logger.debug("FTP server task cancelled")
        except Exception as e:
            logger.error("FTP server error: %s", e)
        finally:
            await self.stop()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Handle a new FTP client connection."""
        peername = writer.get_extra_info("peername")
        log_prefix = f"[{self.vp_name}] " if self.vp_name else ""
        logger.info("%sFTP connection from %s", log_prefix, peername)

        session = FTPSession(
            reader=reader,
            writer=writer,
            upload_dir=self.upload_dir,
            access_code=self.access_code,
            ssl_context=self._ssl_context,
            on_file_received=self.on_file_received,
            passive_port_range=(self.PASSIVE_PORT_MIN, self.PASSIVE_PORT_MAX),
            pasv_address=self._pasv_address,
            bind_address=self.bind_address,
            vp_name=self.vp_name,
        )

        # Track the session task so we can cancel it on stop
        task = asyncio.current_task()
        if task:
            self._active_sessions.append(task)
        try:
            await session.handle()
        finally:
            if task and task in self._active_sessions:
                self._active_sessions.remove(task)

    async def stop(self) -> None:
        """Stop the FTPS server."""
        logger.info("Stopping FTP server")
        self._running = False
        self.ready.clear()

        # Cancel all active sessions and AWAIT cancellation. Previously
        # this slept 0.1 s and called it good — a session mid-write,
        # mid-TLS handshake, or holding a 60 s data-read could easily
        # outlive that and then ``_server.close()`` would run while the
        # underlying sockets were still in use.
        for task in self._active_sessions[:]:
            task.cancel()
        if self._active_sessions:
            await asyncio.gather(*self._active_sessions, return_exceptions=True)

        self._active_sessions.clear()

        if self._server:
            try:
                self._server.close()
                await self._server.wait_closed()
            except OSError as e:
                logger.debug("Error closing FTP server: %s", e)
            self._server = None
