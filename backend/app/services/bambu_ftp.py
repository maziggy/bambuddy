import ssl
import socket
import asyncio
import logging
from ftplib import FTP_TLS, FTP
from pathlib import Path
from io import BytesIO

logger = logging.getLogger(__name__)


class ImplicitFTP_TLS(FTP_TLS):
    """FTP_TLS subclass for implicit FTPS (port 990) with session reuse."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sock = None
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    def connect(self, host='', port=990, timeout=-999, source_address=None):
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
        self.sock = socket.create_connection(
            (self.host, self.port),
            self.timeout,
            source_address=self.source_address
        )
        self.sock = self.ssl_context.wrap_socket(self.sock, server_hostname=self.host)
        self.af = self.sock.family
        self.file = self.sock.makefile('r', encoding=self.encoding)
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
                session=self.sock.session  # Reuse session!
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
            self._ftp = ImplicitFTP_TLS()
            self._ftp.connect(self.ip_address, self.FTP_PORT, timeout=10)
            self._ftp.login("bblp", self.access_code)
            self._ftp.prot_p()
            self._ftp.set_pasv(True)
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
                    files.append({
                        "name": name,
                        "is_directory": is_dir,
                        "size": size,
                    })
        except Exception:
            pass

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
            logger.info(f"Successfully downloaded {remote_path} to {local_path}")
            return True
        except Exception as e:
            logger.debug(f"Failed to download {remote_path}: {e}")
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
            return False

        try:
            with open(local_path, "rb") as f:
                self._ftp.storbinary(f"STOR {remote_path}", f)
            return True
        except Exception:
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
            # Response format: "213 <bytes available>"
            if response.startswith("213"):
                parts = response.split()
                if len(parts) >= 2:
                    result["free_bytes"] = int(parts[1])
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
) -> bool:
    """Async wrapper for downloading a file."""
    loop = asyncio.get_event_loop()

    def _download():
        client = BambuFTPClient(ip_address, access_code)
        if client.connect():
            try:
                return client.download_to_file(remote_path, local_path)
            finally:
                client.disconnect()
        return False

    return await loop.run_in_executor(None, _download)


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
            for remote_path in remote_paths:
                if client.download_to_file(remote_path, local_path):
                    return True
            return False
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
        client = BambuFTPClient(ip_address, access_code)
        if client.connect():
            try:
                return client.upload_file(local_path, remote_path)
            finally:
                client.disconnect()
        return False

    return await loop.run_in_executor(None, _upload)


async def list_files_async(
    ip_address: str,
    access_code: str,
    path: str = "/",
) -> list[dict]:
    """Async wrapper for listing files."""
    loop = asyncio.get_event_loop()

    def _list():
        client = BambuFTPClient(ip_address, access_code)
        if client.connect():
            try:
                return client.list_files(path)
            finally:
                client.disconnect()
        return []

    return await loop.run_in_executor(None, _list)


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
