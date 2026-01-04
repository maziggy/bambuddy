import asyncio
import logging
import socket
import ssl
from ftplib import FTP, FTP_TLS
from io import BytesIO
from pathlib import Path

logger = logging.getLogger(__name__)


class ImplicitFTP_TLS(FTP_TLS):
    """FTP_TLS subclass for implicit FTPS (port 990) with session reuse."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sock = None
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
        """Override to reuse SSL session for data connection (required by vsFTPd)."""
        conn, size = FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            # Reuse the SSL session from the control connection
            conn = self.ssl_context.wrap_socket(
                conn,
                server_hostname=self.host,
                session=self.sock.session,  # Reuse session!
            )
        return conn, size


class BambuFTPClient:
    """FTP client for retrieving files from Bambu Lab printers."""

    FTP_PORT = 990

    def __init__(self, ip_address: str, access_code: str):
        self.ip_address = ip_address
        self.access_code = access_code
        self._ftp: ImplicitFTP_TLS | None = None

    def connect(self) -> bool:
        """Connect to the printer FTP server (implicit FTPS on port 990)."""
        try:
            logger.debug(f"FTP connecting to {self.ip_address}:{self.FTP_PORT}")
            self._ftp = ImplicitFTP_TLS()
            self._ftp.connect(self.ip_address, self.FTP_PORT, timeout=10)
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

    def upload_file(self, local_path: Path, remote_path: str) -> bool:
        """Upload a file to the printer."""
        if not self._ftp:
            logger.warning("upload_file: FTP not connected")
            return False

        try:
            file_size = local_path.stat().st_size if local_path.exists() else 0
            logger.info(f"FTP uploading {local_path} ({file_size} bytes) to {remote_path}")
            with open(local_path, "rb") as f:
                self._ftp.storbinary(f"STOR {remote_path}", f)
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
            dirs_to_scan = ["/cache", "/timelapse", "/model"]

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
) -> bool:
    """Async wrapper for downloading a file with timeout."""
    loop = asyncio.get_event_loop()

    def _download():
        client = BambuFTPClient(ip_address, access_code)
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
) -> bool:
    """Try downloading a file from multiple paths using a single connection."""
    loop = asyncio.get_event_loop()

    def _download():
        client = BambuFTPClient(ip_address, access_code)
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
) -> bool:
    """Async wrapper for uploading a file."""
    loop = asyncio.get_event_loop()

    def _upload():
        logger.info(f"FTP connecting to {ip_address} for upload...")
        client = BambuFTPClient(ip_address, access_code)
        if client.connect():
            logger.info(f"FTP connected to {ip_address}")
            try:
                return client.upload_file(local_path, remote_path)
            finally:
                client.disconnect()
        logger.warning(f"FTP connection failed to {ip_address}")
        return False

    return await loop.run_in_executor(None, _upload)


async def list_files_async(
    ip_address: str,
    access_code: str,
    path: str = "/",
    timeout: float = 30.0,
) -> list[dict]:
    """Async wrapper for listing files with timeout."""
    loop = asyncio.get_event_loop()

    def _list():
        client = BambuFTPClient(ip_address, access_code)
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
) -> bool:
    """Async wrapper for deleting a file."""
    loop = asyncio.get_event_loop()

    def _delete():
        client = BambuFTPClient(ip_address, access_code)
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
) -> bytes | None:
    """Async wrapper for downloading file as bytes."""
    loop = asyncio.get_event_loop()

    def _download():
        client = BambuFTPClient(ip_address, access_code)
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
) -> dict | None:
    """Async wrapper for getting storage info."""
    loop = asyncio.get_event_loop()

    def _get_storage():
        client = BambuFTPClient(ip_address, access_code)
        if client.connect():
            try:
                return client.get_storage_info()
            finally:
                client.disconnect()
        return None

    return await loop.run_in_executor(None, _get_storage)
