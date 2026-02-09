"""Mock implicit FTPS server for testing BambuFTPClient.

Built on pyftpdlib with implicit TLS support to match Bambu printer behavior.
Supports failure injection, custom AVBL command, and filesystem inspection.
"""

import logging
import os
import threading
import time

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import TLS_FTPHandler
from pyftpdlib.servers import FTPServer


class ImplicitTLS_FTPHandler(TLS_FTPHandler):
    """FTP handler that wraps the socket in TLS before sending the 220 banner.

    This implements implicit FTPS (port 990 style) where the TLS handshake
    happens immediately on connect, before any FTP protocol exchange.
    pyftpdlib only natively supports explicit FTPS (AUTH TLS after connect).
    """

    # Per-class failure injection map: command -> (code, message, remaining_count)
    # -1 remaining_count = permanent failure
    _failure_map: dict = {}

    # AVBL command response (bytes available)
    _avbl_bytes: int = 1073741824  # 1 GB default

    # Register AVBL as a recognized FTP command (pyftpdlib requires this)
    proto_cmds = {
        **TLS_FTPHandler.proto_cmds,
        "AVBL": {
            "perm": None,
            "auth": True,
            "arg": None,
            "help": "Syntax: AVBL (get available bytes).",
        },
    }

    def handle(self):
        """Wrap socket in TLS immediately, then send 220 banner."""
        self.secure_connection(self.get_ssl_context())
        super().handle()

    def ftp_PROT(self, line):
        """Override PROT to auto-set _pbsz for implicit FTPS.

        In implicit FTPS the connection is already TLS-secured, so requiring
        a separate PBSZ command is unnecessary. Python's ftplib prot_c()
        doesn't send PBSZ first (unlike prot_p()), causing 503 errors.
        Real Bambu printers don't enforce this for implicit FTPS either.
        """
        self._pbsz = True
        return super().ftp_PROT(line)

    def _check_failure(self, command: str, line: str):
        """Check if a failure is injected for this command.

        Returns True if a failure response was sent, False otherwise.
        """
        if command in self._failure_map:
            code, message, remaining = self._failure_map[command]
            if remaining != 0:
                if remaining > 0:
                    self._failure_map[command] = (code, message, remaining - 1)
                    if remaining - 1 == 0:
                        del self._failure_map[command]
                self.respond(f"{code} {message}")
                return True
        return False

    def ftp_AVBL(self, line):
        """Handle custom AVBL command (available bytes on storage)."""
        self.respond(f"213 {self._avbl_bytes}")

    def ftp_RETR(self, file):
        if self._check_failure("RETR", file):
            return
        return super().ftp_RETR(file)

    def ftp_STOR(self, file):
        if self._check_failure("STOR", file):
            return
        return super().ftp_STOR(file)

    def ftp_DELE(self, line):
        if self._check_failure("DELE", line):
            return
        return super().ftp_DELE(line)

    def ftp_CWD(self, path):
        if self._check_failure("CWD", path):
            return
        return super().ftp_CWD(path)

    def ftp_LIST(self, path=""):
        if self._check_failure("LIST", path):
            return
        return super().ftp_LIST(path)

    def ftp_SIZE(self, path):
        if self._check_failure("SIZE", path):
            return
        # Override to allow SIZE in ASCII mode (real Bambu printers allow it,
        # and BambuFTPClient.get_file_size() doesn't set TYPE I first)
        if not self.fs.isfile(self.fs.realpath(path)):
            self.respond(f"550 {self.fs.fs2ftp(path)} is not retrievable.")
            return
        try:
            size = self.run_as_current_user(self.fs.getsize, path)
        except OSError as err:
            self.respond(f"550 {err}.")
        else:
            self.respond(f"213 {size}")

    def ftp_PASS(self, line):
        if self._check_failure("PASS", line):
            return
        return super().ftp_PASS(line)


class MockBambuFTPServer:
    """Manages a mock implicit FTPS server in a background thread.

    Simulates a Bambu printer FTP server with:
    - Implicit TLS (like real printers on port 990)
    - Standard Bambu directory structure
    - AVBL command support
    - Per-command failure injection for testing error paths
    """

    def __init__(
        self,
        host: str,
        port: int,
        root_dir: str,
        cert_path: str,
        key_path: str,
        access_code: str = "12345678",
    ):
        self.host = host
        self.port = port
        self.root_dir = root_dir
        self.cert_path = cert_path
        self.key_path = key_path
        self.access_code = access_code
        self._server: FTPServer | None = None
        self._thread: threading.Thread | None = None
        # Create a unique handler class per instance so _failure_map is isolated
        self._handler_class = type(
            "TestFTPHandler",
            (ImplicitTLS_FTPHandler,),
            {
                "_failure_map": {},
                "_avbl_bytes": 1073741824,
            },
        )

    def start(self):
        """Start the FTP server in a background daemon thread."""
        authorizer = DummyAuthorizer()
        authorizer.add_user("bblp", self.access_code, self.root_dir, perm="elradfmwMT")

        handler = self._handler_class
        handler.authorizer = authorizer
        handler.certfile = self.cert_path
        handler.keyfile = self.key_path
        handler.passive_ports = range(60000, 60101)
        handler.tls_control_required = False
        handler.tls_data_required = False
        # Reset ssl_context so it picks up our cert/key
        handler.ssl_context = None

        # Suppress pyftpdlib's noisy logging (startup/shutdown banners)
        # to avoid "I/O operation on closed file" errors when xdist
        # workers tear down while the daemon thread is still logging.
        logging.getLogger("pyftpdlib").setLevel(logging.CRITICAL)

        self._server = FTPServer((self.host, self.port), handler)
        self._server.max_cons = 10
        self._server.max_cons_per_ip = 5

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        # Brief wait for server to be ready
        time.sleep(0.1)

    def stop(self):
        """Stop the FTP server and wait for thread to exit."""
        if self._server:
            self._server.close_all()
        if self._thread:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    def inject_failure(self, command: str, code: int, message: str, count: int = -1):
        """Inject a failure response for a specific FTP command.

        Args:
            command: FTP command name (RETR, STOR, DELE, CWD, LIST, SIZE, PASS)
            code: FTP response code (e.g. 550, 553)
            message: Response message
            count: Number of times to fail (-1 = permanent)
        """
        self._handler_class._failure_map[command] = (code, message, count)

    def clear_failures(self):
        """Remove all injected failures."""
        self._handler_class._failure_map.clear()

    def set_avbl_bytes(self, n: int):
        """Set the response value for the AVBL command."""
        self._handler_class._avbl_bytes = n

    def add_file(self, relative_path: str, content: bytes = b""):
        """Add a file to the server's filesystem."""
        full_path = os.path.join(self.root_dir, relative_path.lstrip("/"))
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(content)

    def add_directory(self, relative_path: str):
        """Create a directory in the server's filesystem."""
        full_path = os.path.join(self.root_dir, relative_path.lstrip("/"))
        os.makedirs(full_path, exist_ok=True)

    def file_exists(self, relative_path: str) -> bool:
        """Check if a file exists on the server."""
        full_path = os.path.join(self.root_dir, relative_path.lstrip("/"))
        return os.path.isfile(full_path)

    def read_file(self, relative_path: str) -> bytes:
        """Read file content from the server's filesystem."""
        full_path = os.path.join(self.root_dir, relative_path.lstrip("/"))
        with open(full_path, "rb") as f:
            return f.read()
