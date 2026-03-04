import asyncio
import ftplib  # nosec B402
import logging
import os
import socket
import ssl
from collections.abc import Awaitable, Callable
from ftplib import FTP, FTP_TLS  # nosec B402
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
    # Default timeout in seconds (increased for A1 printers)
    DEFAULT_TIMEOUT = 30
    # Models that may need SSL mode fallback (try prot_p first, fall back to prot_c)
    # These models have varying FTP SSL behavior depending on firmware version
    A1_MODELS = ("A1", "A1 Mini")
    # Chunk size for manual upload transfer (1MB)
    # Larger chunks reduce overhead and work better with A1 printers
    CHUNK_SIZE = 1024 * 1024
    # Per-chunk data socket timeout during upload.
    UPLOAD_CHUNK_TIMEOUT = 120

    # Cache for working FTP modes per printer IP
    # Maps IP -> "prot_p" or "prot_c"
    _mode_cache: dict[str, str] = {}

    def __init__(
        self,
        ip_address: str,
        access_code: str,
        timeout: float | None = None,
        printer_model: str | None = None,
        force_prot_c: bool = False,
    ):
        self.ip_address = ip_address
        self.access_code = access_code
        self.timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT
        self.printer_model = printer_model
        self.force_prot_c = force_prot_c
        self._ftp: ImplicitFTP_TLS | None = None

    def _is_a1_model(self) -> bool:
        """Check if this is an A1 series printer."""
        if not self.printer_model:
            return False
        return self.printer_model in self.A1_MODELS

    def _get_cached_mode(self) -> str | None:
        """Get cached FTP mode for this printer."""
        return self._mode_cache.get(self.ip_address)

    @classmethod
    def cache_mode(cls, ip_address: str, mode: str):
        """Cache the working FTP mode for a printer."""
        cls._mode_cache[ip_address] = mode
        logger.info("FTP mode cached for %s: %s", ip_address, mode)

    def _should_use_prot_c(self) -> bool:
        """Determine if we should use prot_c (clear) mode."""
        # If explicitly forced, use prot_c
        if self.force_prot_c:
            return True
        # Check cache first
        cached = self._get_cached_mode()
        if cached:
            return cached == "prot_c"
        # Default: try prot_p first (will fall back if needed)
        return False

    def connect(self) -> bool:
        """Connect to the printer FTP server (implicit FTPS on port 990)."""
        try:
            use_prot_c = self._should_use_prot_c()
            logger.debug(
                f"FTP connecting to {self.ip_address}:{self.FTP_PORT} "
                f"(timeout={self.timeout}s, model={self.printer_model}, prot_c={use_prot_c})"
            )
            self._ftp = ImplicitFTP_TLS(skip_session_reuse=use_prot_c)
            self._ftp.connect(self.ip_address, self.FTP_PORT, timeout=self.timeout)
            logger.debug("FTP connected, logging in as bblp")
            self._ftp.login("bblp", self.access_code)
            if use_prot_c:
                # Use clear (unencrypted) data channel
                logger.debug("FTP logged in, setting prot_c (clear) and passive mode")
                self._ftp.prot_c()
            else:
                # Use protected (encrypted) data channel with session reuse
                logger.debug("FTP logged in, setting prot_p (protected) and passive mode")
                self._ftp.prot_p()
            self._ftp.set_pasv(True)
            # Log welcome message for debugging
            if hasattr(self._ftp, "welcome") and self._ftp.welcome:
                logger.debug("FTP server welcome: %s", self._ftp.welcome)
            logger.info(
                f"FTP connected successfully to {self.ip_address} (model={self.printer_model}, prot_c={use_prot_c})"
            )
            return True
        except ftplib.error_perm as e:
            logger.warning("FTP connection permission error to %s: %s", self.ip_address, e)
            self._ftp = None
            return False
        except TimeoutError as e:
            logger.warning("FTP connection timed out to %s: %s", self.ip_address, e)
            self._ftp = None
            return False
        except ssl.SSLError as e:
            logger.warning("FTP SSL error connecting to %s: %s", self.ip_address, e)
            self._ftp = None
            return False
        except (OSError, ftplib.Error) as e:
            logger.warning("FTP connection failed to %s: %s (type: %s)", self.ip_address, e, type(e).__name__)
            self._ftp = None
            return False

    def disconnect(self):
        """Disconnect from the FTP server."""
        if self._ftp:
            try:
                self._ftp.quit()
            except (OSError, ftplib.Error, EOFError):
                pass  # Best-effort FTP cleanup; connection may already be closed
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
                        pass  # Non-critical: mtime parsing is best-effort; file entry works without it

                    file_entry = {
                        "name": name,
                        "is_directory": is_dir,
                        "size": size,
                        "path": f"{path.rstrip('/')}/{name}",
                    }
                    if mtime:
                        file_entry["mtime"] = mtime
                    files.append(file_entry)
            logger.debug("Listed %s files in %s", len(files), path)
        except (OSError, ftplib.Error) as e:
            logger.info("FTP list_files failed for %s: %s", path, e)

        return files

    def download_file(self, remote_path: str) -> bytes | None:
        """Download a file from the printer."""
        if not self._ftp:
            return None

        try:
            buffer = BytesIO()
            self._ftp.retrbinary(f"RETR {remote_path}", buffer.write)
            return buffer.getvalue()
        except (OSError, ftplib.Error):
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
            if file_size == 0:
                logger.warning("FTP download returned 0 bytes for %s", remote_path)
                if local_path.exists():
                    local_path.unlink()
                return False
            logger.info("Successfully downloaded %s to %s (%s bytes)", remote_path, local_path, file_size)
            return True
        except (OSError, ftplib.Error) as e:
            # Log at INFO level so we can see failures in normal logs
            logger.info("FTP download failed for %s: %s", remote_path, e)
            # Clean up partial file if it exists
            if local_path.exists():
                try:
                    local_path.unlink()
                except OSError:
                    pass  # Best-effort partial file cleanup; not critical if removal fails
            return False

    def diagnose_storage(self) -> dict:
        """Run storage diagnostics and return results. For debugging upload issues."""
        results = {
            "connected": self._ftp is not None,
            "can_list_root": False,
            "root_files": [],
            "can_list_cache": False,
            "storage_info": None,
            "pwd": None,
            "errors": [],
        }

        if not self._ftp:
            results["errors"].append("FTP not connected")
            return results

        # Try to get current directory
        try:
            results["pwd"] = self._ftp.pwd()
            logger.debug("FTP current directory: %s", results["pwd"])
        except (OSError, ftplib.Error) as e:
            results["errors"].append(f"PWD failed: {e}")
            logger.debug("FTP PWD failed: %s", e)

        # Try to list root directory
        try:
            self._ftp.cwd("/")
            items = []
            self._ftp.retrlines("LIST", items.append)
            results["can_list_root"] = True
            results["root_files"] = items[:10]  # First 10 entries
            logger.debug("FTP root listing (%s items): %s", len(items), items[:5])
        except (OSError, ftplib.Error) as e:
            results["errors"].append(f"LIST / failed: {e}")
            logger.debug("FTP LIST / failed: %s", e)

        # Try to list /cache (should exist on all printers)
        try:
            self._ftp.cwd("/cache")
            items = []
            self._ftp.retrlines("LIST", items.append)
            results["can_list_cache"] = True
            logger.debug("FTP /cache listing: %s items", len(items))
        except (OSError, ftplib.Error) as e:
            results["errors"].append(f"LIST /cache failed: {e}")
            logger.debug("FTP LIST /cache failed: %s", e)

        # Try to get storage info
        try:
            results["storage_info"] = self.get_storage_info()
            logger.debug("FTP storage info: %s", results["storage_info"])
        except (OSError, ftplib.Error) as e:
            results["errors"].append(f"Storage info failed: {e}")

        return results

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
            logger.info("FTP uploading %s (%s bytes) to %s", local_path, file_size, remote_path)

            uploaded = 0
            callback_exception: Exception | None = None

            # Use manual transfer instead of storbinary() for A1 compatibility
            # A1 printers have issues with storbinary's voidresp() hanging after transfer
            with open(local_path, "rb") as f:
                logger.debug("FTP STOR command starting for %s", remote_path)
                conn = self._ftp.transfercmd(f"STOR {remote_path}")

                # Set explicit socket options for reliable transfer
                conn.setblocking(True)
                conn.settimeout(self.UPLOAD_CHUNK_TIMEOUT)

                try:
                    while True:
                        chunk = f.read(self.CHUNK_SIZE)
                        if not chunk:
                            logger.debug("FTP upload: final chunk reached")
                            break

                        conn.sendall(chunk)
                        uploaded += len(chunk)
                        logger.debug("FTP upload progress: %s/%s bytes", uploaded, file_size)

                        if progress_callback:
                            try:
                                progress_callback(uploaded, file_size)
                            except Exception as e:
                                callback_exception = e
                                logger.info(
                                    "FTP upload callback requested stop for %s at %s/%s bytes: %s",
                                    remote_path,
                                    uploaded,
                                    file_size,
                                    e,
                                )
                                break

                except OSError as e:
                    logger.error("FTP connection lost during upload: %s", e)
                    raise
                finally:
                    try:
                        conn.close()
                    except OSError:
                        pass

            # Skip voidresp() for A1 models — they hang after transfercmd uploads
            if self.printer_model not in self.A1_MODELS:
                try:
                    self._ftp.voidresp()
                except (OSError, ftplib.Error) as e:
                    # Data transfer already completed — voidresp() failure is just a noisy
                    # 226 acknowledgment issue, not an actual upload failure. Log and continue.
                    logger.warning("FTP upload response for %s was not clean (data already sent): %s", remote_path, e)

            if callback_exception is not None:
                cleanup_ok = False
                try:
                    cleanup_ok = self.delete_file(remote_path)
                except Exception as cleanup_error:
                    logger.warning("FTP cancel cleanup failed for %s: %s", remote_path, cleanup_error)

                if cleanup_ok:
                    logger.info("FTP cancel cleanup succeeded for %s", remote_path)
                    raise callback_exception

                raise RuntimeError(
                    f"Upload cancelled but failed to remove partial file {remote_path} from printer"
                ) from callback_exception

            logger.info("FTP upload complete: %s", remote_path)
            return True
        except ftplib.error_perm as e:
            # Permanent FTP error (4xx/5xx response)
            error_code = str(e)[:3] if str(e) else "unknown"
            logger.error("FTP upload failed for %s: %s (error code: %s)", remote_path, e, error_code)
            if error_code == "553":
                logger.error(
                    "FTP 553 error - Could not create file. Possible causes: "
                    "1) No SD card inserted, 2) SD card full, 3) SD card not formatted correctly (needs FAT32/exFAT), "
                    "4) Printer busy/not ready, 5) File path issue"
                )
            elif error_code == "550":
                logger.error("FTP 550 error - File/directory not found or permission denied")
            elif error_code == "552":
                logger.error("FTP 552 error - Storage quota exceeded (SD card full?)")
            return False
        except (OSError, ftplib.Error) as e:
            logger.error("FTP upload failed for %s: %s (type: %s)", remote_path, e, type(e).__name__)
            return False

    def upload_bytes(self, data: bytes, remote_path: str) -> bool:
        """Upload bytes to the printer."""
        if not self._ftp:
            return False

        try:
            # Use manual transfer instead of storbinary() for A1 compatibility
            conn = self._ftp.transfercmd(f"STOR {remote_path}")
            conn.setblocking(True)
            conn.settimeout(self.UPLOAD_CHUNK_TIMEOUT)

            try:
                # Send data in chunks
                offset = 0
                while offset < len(data):
                    chunk = data[offset : offset + self.CHUNK_SIZE]
                    conn.sendall(chunk)
                    offset += len(chunk)
            except OSError as e:
                logger.error("FTP connection lost during upload_bytes: %s", e)
                raise
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
            return True
        except (OSError, ftplib.Error):
            return False

    def delete_file(self, remote_path: str) -> bool:
        """Delete a file from the printer."""
        if not self._ftp:
            return False

        try:
            self._ftp.delete(remote_path)
            return True
        except (OSError, ftplib.Error) as e:
            logger.warning("Failed to delete %s: %s", remote_path, e)
            return False

    def get_file_size(self, remote_path: str) -> int | None:
        """Get the size of a file."""
        if not self._ftp:
            return None

        try:
            return self._ftp.size(remote_path)
        except (OSError, ftplib.Error):
            return None

    def get_storage_info(self) -> dict | None:
        """Get storage information from the printer."""
        if not self._ftp:
            return None

        result = {}

        # Try AVBL command (available space) - some FTP servers support this
        try:
            response = self._ftp.sendcmd("AVBL")
            logger.debug("AVBL response: %s", response)
            # Response format: "213 <bytes available>"
            if response.startswith("213"):
                parts = response.split()
                if len(parts) >= 2:
                    result["free_bytes"] = int(parts[1])
        except (OSError, ftplib.Error) as e:
            logger.debug("AVBL command not supported: %s", e)
            # Try STAT command as fallback
            try:
                response = self._ftp.sendcmd("STAT")
                logger.debug("STAT response: %s", response)
            except (OSError, ftplib.Error):
                pass  # Both AVBL and STAT unsupported; storage info will rely on directory scan

        # Calculate used space by listing root directories
        try:
            total_used = 0
            dirs_to_scan = ["/cache", "/timelapse", "/model", "/data", "/data/Metadata", "/"]

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
                                pass  # Skip entries with non-numeric size fields
                except (OSError, ftplib.Error):
                    pass  # Directory may not exist on this printer model; skip it

            result["used_bytes"] = total_used
        except (OSError, ftplib.Error):
            pass  # Storage scan failed; return whatever info was collected above

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

    For A1/A1 Mini printers, automatically tries prot_p first, then falls back
    to prot_c if the download fails. The working mode is cached for future operations.

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
    is_a1 = printer_model in BambuFTPClient.A1_MODELS if printer_model else False

    def _download(force_prot_c: bool = False) -> bool:
        mode_str = "prot_c" if force_prot_c else "prot_p"
        client = BambuFTPClient(
            ip_address, access_code, timeout=socket_timeout, printer_model=printer_model, force_prot_c=force_prot_c
        )
        if client.connect():
            try:
                result = client.download_to_file(remote_path, local_path)
                if result:
                    # Cache the working mode
                    BambuFTPClient.cache_mode(ip_address, mode_str)
                return result
            finally:
                client.disconnect()
        return False

    try:
        # Check if we have a cached mode for this printer
        cached_mode = BambuFTPClient._mode_cache.get(ip_address)

        if cached_mode:
            # Use cached mode
            force_prot_c = cached_mode == "prot_c"
            return await asyncio.wait_for(loop.run_in_executor(None, lambda: _download(force_prot_c)), timeout=timeout)

        # No cached mode - try prot_p first
        result = await asyncio.wait_for(loop.run_in_executor(None, lambda: _download(False)), timeout=timeout)

        if result:
            return True

        # Download failed - for A1 models, try prot_c fallback
        if is_a1:
            logger.info("FTP download failed with prot_p for A1 model, trying prot_c fallback...")
            result = await asyncio.wait_for(loop.run_in_executor(None, lambda: _download(True)), timeout=timeout)
            return result

        return False

    except TimeoutError:
        logger.warning("FTP download timed out after %ss for %s", timeout, remote_path)
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

    For A1/A1 Mini printers, automatically tries prot_p first, then falls back
    to prot_c if the upload fails. The working mode is cached for future uploads.

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
    is_a1 = printer_model in BambuFTPClient.A1_MODELS if printer_model else False

    def _upload(force_prot_c: bool = False) -> bool:
        mode_str = "prot_c" if force_prot_c else "prot_p"
        logger.info(
            f"FTP connecting to {ip_address} for upload (model={printer_model}, "
            f"mode={mode_str}, socket_timeout={socket_timeout}s)..."
        )
        client = BambuFTPClient(
            ip_address, access_code, timeout=socket_timeout, printer_model=printer_model, force_prot_c=force_prot_c
        )
        if client.connect():
            logger.info("FTP connected to %s", ip_address)
            try:
                result = client.upload_file(local_path, remote_path, progress_callback)
                if result:
                    # Cache the working mode
                    BambuFTPClient.cache_mode(ip_address, mode_str)
                return result
            finally:
                client.disconnect()
        logger.warning("FTP connection failed to %s", ip_address)
        return False

    try:
        # Check if we have a cached mode for this printer
        cached_mode = BambuFTPClient._mode_cache.get(ip_address)

        if cached_mode:
            # Use cached mode
            force_prot_c = cached_mode == "prot_c"
            return await asyncio.wait_for(loop.run_in_executor(None, lambda: _upload(force_prot_c)), timeout=timeout)

        # No cached mode - try prot_p first
        result = await asyncio.wait_for(loop.run_in_executor(None, lambda: _upload(False)), timeout=timeout)

        if result:
            return True

        # Upload failed - for A1 models, try prot_c fallback
        if is_a1:
            logger.info("FTP upload failed with prot_p for A1 model, trying prot_c fallback...")
            result = await asyncio.wait_for(loop.run_in_executor(None, lambda: _upload(True)), timeout=timeout)
            return result

        return False

    except TimeoutError:
        logger.warning("FTP upload timed out after %ss for %s", timeout, remote_path)
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
        logger.warning("FTP list_files timed out after %ss for %s", timeout, path)
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
    non_retry_exceptions: tuple[type[BaseException], ...] = (),
    **kwargs,
) -> T | None:
    """Execute FTP operation with retry logic.

    Args:
        operation: Async function to execute
        *args: Positional arguments for the operation
        max_retries: Number of retry attempts (default: 3)
        retry_delay: Seconds to wait between retries (default: 2.0)
        operation_name: Name for logging purposes
        non_retry_exceptions: Exception types that should immediately abort retries
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
                    logger.info("%s succeeded on attempt %s/%s", operation_name, attempt + 1, max_retries + 1)
                return result
            # Operation returned failure indicator
            if attempt > 0:
                logger.info("%s attempt %s/%s returned failure", operation_name, attempt + 1, max_retries + 1)
        except Exception as e:
            if non_retry_exceptions and isinstance(e, non_retry_exceptions):
                raise
            last_error = e
            logger.warning("%s attempt %s/%s failed: %s", operation_name, attempt + 1, max_retries + 1, e)

        # Don't wait after the last attempt
        if attempt < max_retries:
            logger.info("%s will retry in %ss...", operation_name, retry_delay)
            await asyncio.sleep(retry_delay)

    logger.error("%s failed after %s attempts", operation_name, max_retries + 1)
    if last_error:
        logger.debug("Last error: %s", last_error)
    return None
