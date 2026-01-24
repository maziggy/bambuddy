import asyncio
import ftplib
import logging
import os
import socket
import ssl
from collections.abc import Awaitable, Callable
from ftplib import FTP, FTP_TLS
from io import BytesIO
from pathlib import Path
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ImplicitFTP_TLS(FTP_TLS):
    """FTP_TLS subclass for implicit FTPS (port 990) with model-specific SSL handling.

    X1C/P1S printers (vsFTPd) require SSL with session reuse on the data channel.
    A1/A1 Mini printers have issues with SSL on the data channel entirely and
    timeout waiting for transfer completion. Set skip_session_reuse=True for A1
    printers to skip SSL on the data channel (control channel remains encrypted).
    """

    def __init__(self, *args, skip_session_reuse: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self._sock = None
        self.skip_session_reuse = skip_session_reuse
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    def connect(self, host="", port=990, timeout=-999, source_address=None):
        """Connect to host, wrapping socket in TLS immediately (implicit FTPS)."""
        if host:
            self.host = host
        if port > 0:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        if source_address:
            self.source_address = source_address

        # Create and wrap socket immediately (implicit TLS)
        self.sock = socket.create_connection((self.host, self.port), self.timeout, source_address=self.source_address)
        self.sock = self.ssl_context.wrap_socket(self.sock, server_hostname=self.host)
        self.af = self.sock.family
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome

    def ntransfercmd(self, cmd, rest=None):
        """Override to wrap data connection in SSL for X1C/P1S only.

        X1C/P1S printers (vsFTPd) require SSL session reuse on the data channel.
        A1/A1 Mini printers have issues with SSL on the data channel entirely -
        they timeout waiting for the transfer completion response. For A1, we
        skip SSL wrapping on the data channel (control channel remains encrypted).
        """
        conn, size = FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p and not self.skip_session_reuse:
            # X1C/P1S: Wrap data channel with SSL session reuse (required by vsFTPd)
            conn = self.ssl_context.wrap_socket(
                conn,
                server_hostname=self.host,
                session=self.sock.session,
            )
        # A1/A1 Mini (skip_session_reuse=True): Don't wrap data channel in SSL
        # The control channel remains encrypted via implicit FTPS
        return conn, size


class BambuFTPClient:
    """FTP client for retrieving files from Bambu Lab printers."""

    FTP_PORT = 990
    DEFAULT_TIMEOUT = 30  # Default timeout in seconds (increased for A1 printers)
    # Models that need SSL session reuse disabled (A1 series has FTP issues with session reuse)
    SKIP_SESSION_REUSE_MODELS = ("A1", "A1 Mini", "P1S", "P1P", "P2S")

    def __init__(
        self,
        ip_address: str,
        access_code: str,
        timeout: float | None = None,
        printer_model: str | None = None,
    ):
        self.ip_address = ip_address
        self.access_code = access_code
        self.timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT
        self.printer_model = printer_model
        self._ftp: ImplicitFTP_TLS | None = None

    def _should_skip_session_reuse(self) -> bool:
        """Check if this printer model needs SSL session reuse disabled."""
        if not self.printer_model:
            return False
        return self.printer_model in self.SKIP_SESSION_REUSE_MODELS

    def connect(self) -> bool:
        """Connect to the printer FTP server (implicit FTPS on port 990)."""
        try:
            skip_reuse = self._should_skip_session_reuse()
            logger.debug(
                f"FTP connecting to {self.ip_address}:{self.FTP_PORT} "
                f"(timeout={self.timeout}s, model={self.printer_model}, skip_session_reuse={skip_reuse})"
            )
            self._ftp = ImplicitFTP_TLS()
            self._ftp.connect(self.ip_address, self.FTP_PORT, timeout=self.timeout)
            logger.debug("FTP connected, logging in as bblp")
            self._ftp.login("bblp", self.access_code)
            logger.debug("FTP logged in, setting prot_p and passive mode")
            self._ftp.prot_p()
            self._ftp.set_pasv(True)
            logger.info(f"FTP connected successfully to {self.ip_address}")
            return True
        except Exception as e:
            logger.warning(f"FTP connection failed to {self.ip_address}: {e}")
            self._ftp = None
            return False

    def disconnect(self):
        """Disconnect from the FTP server."""
        if self._ftp:
            try:
                self._ftp.quit()
            except Exception:
                pass
            self._ftp = None

    def list_files(self, path: str = "/") -> list[dict]:
        """List files in a directory."""
        if not self._ftp:
            return []

        files = []
        try:
            self._ftp.cwd(path)
            items = []
            self._ftp.retrlines("LIST", items.append)

            for item in items:
                parts = item.split()
                if len(parts) >= 9:
                    name = " ".join(parts[8:])
                    is_dir = item.startswith("d")
                    size = int(parts[4]) if not is_dir else 0

                    # Parse modification time from FTP listing
                    # Format: "Nov 30 10:15" or "Nov 30  2024"
                    mtime = None
                    try:
                        from datetime import datetime

                        month = parts[5]
                        day = parts[6]
                        time_or_year = parts[7]

                        # Determine if it's time (HH:MM) or year
                        if ":" in time_or_year:
                            # Recent file: "Nov 30 10:15" - assume current year
                            year = datetime.now().year
                            time_str = f"{month} {day} {year} {time_or_year}"
                            mtime = datetime.strptime(time_str, "%b %d %Y %H:%M")
                            # If parsed date is in the future, use last year
                            if mtime > datetime.now():
                                mtime = mtime.replace(year=year - 1)
                        else:
                            # Older file: "Nov 30 2024" - no time, just date
                            time_str = f"{month} {day} {time_or_year}"
                            mtime = datetime.strptime(time_str, "%b %d %Y")
                    except (ValueError, IndexError):
                        pass

                    file_entry = {
                        "name": name,
                        "is_directory": is_dir,
                        "size": size,
                        "path": f"{path.rstrip('/')}/{name}",
                    }
                    if mtime:
                        file_entry["mtime"] = mtime
                    files.append(file_entry)
            logger.debug(f"Listed {len(files)} files in {path}")
        except Exception as e:
            logger.info(f"FTP list_files failed for {path}: {e}")

        return files

    def download_file(self, remote_path: str) -> bytes | None:
        """Download a file from the printer."""
        if not self._ftp:
            return None

        try:
            buffer = BytesIO()
            self._ftp.retrbinary(f"RETR {remote_path}", buffer.write)
            return buffer.getvalue()
        except Exception:
            return None

    def download_to_file(self, remote_path: str, local_path: Path) -> bool:
        """Download a file from the printer to local filesystem."""
        if not self._ftp:
            logger.warning("download_to_file called but FTP not connected")
            return False

        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            with open(local_path, "wb") as f:
                self._ftp.retrbinary(f"RETR {remote_path}", f.write)
                f.flush()
                os.fsync(f.fileno())
            file_size = local_path.stat().st_size if local_path.exists() else 0
            logger.info(f"Successfully downloaded {remote_path} to {local_path} ({file_size} bytes)")
            return True
        except Exception as e:
            # Log at INFO level so we can see failures in normal logs
            logger.info(f"FTP download failed for {remote_path}: {e}")
            # Clean up partial file if it exists
            if local_path.exists():
                try:
                    local_path.unlink()
                except Exception:
                    pass
            return False

    def upload_file(
        self,
        local_path: Path,
        remote_path: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> bool:
        """Upload a file to the printer with optional progress callback."""
        if not self._ftp:
            logger.warning("upload_file: FTP not connected")
            return False

        try:
            file_size = local_path.stat().st_size if local_path.exists() else 0
            logger.info(f"FTP uploading {local_path} ({file_size} bytes) to {remote_path}")

            uploaded = 0

            def on_block(block: bytes):
                nonlocal uploaded
                uploaded += len(block)
                if progress_callback:
                    progress_callback(uploaded, file_size)

            with open(local_path, "rb") as f:
                if self._should_skip_session_reuse():
                    ftplib._SSLSocket = None

                self._ftp.storbinary(f"STOR {remote_path}", f, callback=on_block)
            logger.info(f"FTP upload complete: {remote_path}")
            return True
        except Exception as e:
            logger.error(f"FTP upload failed for {remote_path}: {e}")
            return False

    def upload_bytes(self, data: bytes, remote_path: str) -> bool:
        """Upload bytes to the printer."""
        if not self._ftp:
            return False

        try:
            buffer = BytesIO(data)
            self._ftp.storbinary(f"STOR {remote_path}", buffer)
            return True
        except Exception:
            return False

    def delete_file(self, remote_path: str) -> bool:
        """Delete a file from the printer."""
        if not self._ftp:
            return False

        try:
            self._ftp.delete(remote_path)
            return True
        except Exception as e:
            logger.warning(f"Failed to delete {remote_path}: {e}")
            return False

    def get_file_size(self, remote_path: str) -> int | None:
        """Get the size of a file."""
        if not self._ftp:
            return None

        try:
            return self._ftp.size(remote_path)
        except Exception:
            return None

    def get_storage_info(self) -> dict | None:
        """Get storage information from the printer."""
        if not self._ftp:
            return None

        result = {}

        # Try AVBL command (available space) - some FTP servers support this
        try:
            response = self._ftp.sendcmd("AVBL")
            logger.debug(f"AVBL response: {response}")
            # Response format: "213 <bytes available>"
            if response.startswith("213"):
                parts = response.split()
                if len(parts) >= 2:
                    result["free_bytes"] = int(parts[1])
        except Exception as e:
            logger.debug(f"AVBL command not supported: {e}")
            # Try STAT command as fallback
            try:
                response = self._ftp.sendcmd("STAT")
                logger.debug(f"STAT response: {response}")
            except Exception:
                pass

        # Calculate used space by listing root directories
        try:
            total_used = 0
            dirs_to_scan = ["/cache", "/timelapse", "/model", "/data", "/data/Metadata"]

            for dir_path in dirs_to_scan:
                try:
                    self._ftp.cwd(dir_path)
                    items = []
                    self._ftp.retrlines("LIST", items.append)

                    for item in items:
                        parts = item.split()
                        if len(parts) >= 5 and not item.startswith("d"):
                            try:
                                total_used += int(parts[4])
                            except ValueError:
                                pass
                except Exception:
                    pass

            result["used_bytes"] = total_used
        except Exception:
            pass

        return result if result else None


async def download_file_async(
    ip_address: str,
    access_code: str,
    remote_path: str,
    local_path: Path,
    timeout: float = 60.0,
    socket_timeout: float | None = None,
    printer_model: str | None = None,
) -> bool:
    """Async wrapper for downloading a file with timeout.

    Args:
        ip_address: Printer IP address
        access_code: Printer access code
        remote_path: Remote file path on printer
        local_path: Local path to save file
        timeout: Overall operation timeout (asyncio)
        socket_timeout: FTP socket timeout for slow connections (e.g., A1 printers)
        printer_model: Printer model for A1-specific workarounds
    """
    loop = asyncio.get_event_loop()

    def _download():
        client = BambuFTPClient(ip_address, access_code, timeout=socket_timeout, printer_model=printer_model)
        if client.connect():
            try:
                return client.download_to_file(remote_path, local_path)
            finally:
                client.disconnect()
        return False

    try:
        return await asyncio.wait_for(loop.run_in_executor(None, _download), timeout=timeout)
    except TimeoutError:
        logger.warning(f"FTP download timed out after {timeout}s for {remote_path}")
        return False


async def download_file_try_paths_async(
    ip_address: str,
    access_code: str,
    remote_paths: list[str],
    local_path: Path,
    socket_timeout: float | None = None,
    printer_model: str | None = None,
) -> bool:
    """Try downloading a file from multiple paths using a single connection.

    Args:
        socket_timeout: FTP socket timeout for slow connections (e.g., A1 printers)
        printer_model: Printer model for A1-specific workarounds
    """
    loop = asyncio.get_event_loop()

    def _download():
        client = BambuFTPClient(ip_address, access_code, timeout=socket_timeout, printer_model=printer_model)
        if not client.connect():
            return False

        try:
            return any(client.download_to_file(remote_path, local_path) for remote_path in remote_paths)
        finally:
            client.disconnect()

    return await loop.run_in_executor(None, _download)


async def upload_file_async(
    ip_address: str,
    access_code: str,
    local_path: Path,
    remote_path: str,
    timeout: float = 600.0,
    progress_callback: Callable[[int, int], None] | None = None,
    socket_timeout: float | None = None,
    printer_model: str | None = None,
) -> bool:
    """Async wrapper for uploading a file with timeout and progress callback.

    Args:
        ip_address: Printer IP address
        access_code: Printer access code
        local_path: Local file path to upload
        remote_path: Remote path on printer
        timeout: Overall operation timeout (asyncio)
        progress_callback: Optional callback for progress updates
        socket_timeout: FTP socket timeout for slow connections (e.g., A1 printers)
        printer_model: Printer model for A1-specific workarounds
    """
    loop = asyncio.get_event_loop()

    def _upload():
        logger.info(
            f"FTP connecting to {ip_address} for upload (model={printer_model}, socket_timeout={socket_timeout}s)..."
        )
        client = BambuFTPClient(ip_address, access_code, timeout=socket_timeout, printer_model=printer_model)
        if client.connect():
            logger.info(f"FTP connected to {ip_address}")
            try:
                return client.upload_file(local_path, remote_path, progress_callback)
            finally:
                client.disconnect()
        logger.warning(f"FTP connection failed to {ip_address}")
        return False

    try:
        return await asyncio.wait_for(loop.run_in_executor(None, _upload), timeout=timeout)
    except TimeoutError:
        logger.warning(f"FTP upload timed out after {timeout}s for {remote_path}")
        return False


async def list_files_async(
    ip_address: str,
    access_code: str,
    path: str = "/",
    timeout: float = 30.0,
    socket_timeout: float | None = None,
    printer_model: str | None = None,
) -> list[dict]:
    """Async wrapper for listing files with timeout.

    Args:
        socket_timeout: FTP socket timeout for slow connections (e.g., A1 printers)
        printer_model: Printer model for A1-specific workarounds
    """
    loop = asyncio.get_event_loop()

    def _list():
        client = BambuFTPClient(ip_address, access_code, timeout=socket_timeout, printer_model=printer_model)
        if client.connect():
            try:
                return client.list_files(path)
            finally:
                client.disconnect()
        return []

    try:
        return await asyncio.wait_for(loop.run_in_executor(None, _list), timeout=timeout)
    except TimeoutError:
        logger.warning(f"FTP list_files timed out after {timeout}s for {path}")
        return []


async def delete_file_async(
    ip_address: str,
    access_code: str,
    remote_path: str,
    socket_timeout: float | None = None,
    printer_model: str | None = None,
) -> bool:
    """Async wrapper for deleting a file.

    Args:
        socket_timeout: FTP socket timeout for slow connections (e.g., A1 printers)
        printer_model: Printer model for A1-specific workarounds
    """
    loop = asyncio.get_event_loop()

    def _delete():
        client = BambuFTPClient(ip_address, access_code, timeout=socket_timeout, printer_model=printer_model)
        if client.connect():
            try:
                return client.delete_file(remote_path)
            finally:
                client.disconnect()
        return False

    return await loop.run_in_executor(None, _delete)


async def download_file_bytes_async(
    ip_address: str,
    access_code: str,
    remote_path: str,
    socket_timeout: float | None = None,
    printer_model: str | None = None,
) -> bytes | None:
    """Async wrapper for downloading file as bytes.

    Args:
        socket_timeout: FTP socket timeout for slow connections (e.g., A1 printers)
        printer_model: Printer model for A1-specific workarounds
    """
    loop = asyncio.get_event_loop()

    def _download():
        client = BambuFTPClient(ip_address, access_code, timeout=socket_timeout, printer_model=printer_model)
        if client.connect():
            try:
                return client.download_file(remote_path)
            finally:
                client.disconnect()
        return None

    return await loop.run_in_executor(None, _download)


async def get_storage_info_async(
    ip_address: str,
    access_code: str,
    socket_timeout: float | None = None,
    printer_model: str | None = None,
) -> dict | None:
    """Async wrapper for getting storage info.

    Args:
        socket_timeout: FTP socket timeout for slow connections (e.g., A1 printers)
        printer_model: Printer model for A1-specific workarounds
    """
    loop = asyncio.get_event_loop()

    def _get_storage():
        client = BambuFTPClient(ip_address, access_code, timeout=socket_timeout, printer_model=printer_model)
        if client.connect():
            try:
                return client.get_storage_info()
            finally:
                client.disconnect()
        return None

    return await loop.run_in_executor(None, _get_storage)


async def get_ftp_retry_settings() -> tuple[bool, int, float, float]:
    """Get FTP retry settings from database.

    Returns:
        Tuple of (retry_enabled, retry_count, retry_delay, timeout)
    """
    from backend.app.api.routes.settings import get_setting
    from backend.app.core.database import async_session

    async with async_session() as db:
        enabled = (await get_setting(db, "ftp_retry_enabled") or "true") == "true"
        count = int(await get_setting(db, "ftp_retry_count") or "3")
        delay = float(await get_setting(db, "ftp_retry_delay") or "2")
        timeout = float(await get_setting(db, "ftp_timeout") or "30")
    return enabled, count, delay, timeout


async def with_ftp_retry(
    operation: Callable[..., Awaitable[T]],
    *args,
    max_retries: int = 3,
    retry_delay: float = 2.0,
    operation_name: str = "FTP operation",
    **kwargs,
) -> T | None:
    """Execute FTP operation with retry logic.

    Args:
        operation: Async function to execute
        *args: Positional arguments for the operation
        max_retries: Number of retry attempts (default: 3)
        retry_delay: Seconds to wait between retries (default: 2.0)
        operation_name: Name for logging purposes
        **kwargs: Keyword arguments for the operation

    Returns:
        Result of the operation, or None if all attempts fail
    """
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            result = await operation(*args, **kwargs)
            # Check for "falsy" success indicators
            if result not in (False, None, []):
                if attempt > 0:
                    logger.info(f"{operation_name} succeeded on attempt {attempt + 1}/{max_retries + 1}")
                return result
            # Operation returned failure indicator
            if attempt > 0:
                logger.info(f"{operation_name} attempt {attempt + 1}/{max_retries + 1} returned failure")
        except Exception as e:
            last_error = e
            logger.warning(f"{operation_name} attempt {attempt + 1}/{max_retries + 1} failed: {e}")

        # Don't wait after the last attempt
        if attempt < max_retries:
            logger.info(f"{operation_name} will retry in {retry_delay}s...")
            await asyncio.sleep(retry_delay)

    logger.error(f"{operation_name} failed after {max_retries + 1} attempts")
    if last_error:
        logger.debug(f"Last error: {last_error}")
    return None
