"""Test fixtures for FTP service tests.

Provides a real implicit FTPS server (via mock_ftp_server) and client factory
for integration-style testing of BambuFTPClient against a live server.

The server fixture is class-scoped to avoid the overhead of starting a new
TLS server for every test (~67 TLS handshakes → ~9 per class).
"""

import os
import shutil
import socket
from unittest.mock import patch

import pytest

from backend.app.services.bambu_ftp import BambuFTPClient
from backend.app.services.virtual_printer.certificate import CertificateService
from backend.tests.unit.services.mock_ftp_server import MockBambuFTPServer

BAMBU_DIRS = ("cache", "timelapse", "model", "data", "data/Metadata")


@pytest.fixture(scope="session")
def ftp_certs(tmp_path_factory):
    """Generate self-signed TLS certificates once per test session."""
    cert_dir = tmp_path_factory.mktemp("ftp_certs")
    svc = CertificateService(cert_dir, serial="TEST_FTP_SERVER")
    cert_path, key_path = svc.generate_certificates()
    return str(cert_path), str(key_path)


def _find_free_port() -> int:
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="class")
def ftp_root(tmp_path_factory):
    """Create temp directory with standard Bambu printer directory structure."""
    root = tmp_path_factory.mktemp("ftp_root")
    for d in BAMBU_DIRS:
        (root / d).mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture(scope="class")
def ftp_server(ftp_certs, ftp_root):
    """Start a mock implicit FTPS server, yield it, stop on cleanup."""
    cert_path, key_path = ftp_certs
    port = _find_free_port()
    server = MockBambuFTPServer(
        host="127.0.0.1",
        port=port,
        root_dir=str(ftp_root),
        cert_path=cert_path,
        key_path=key_path,
        access_code="12345678",
    )
    server.start()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def _ftp_test_cleanup(request):
    """Reset server state between tests within a class.

    Clears injected failures and restores the Bambu directory structure
    so each test starts with a clean filesystem.  Skips cleanup for test
    classes that don't use the class-scoped ftp_server (e.g.
    TestDisconnectServerGone).
    """
    yield
    # Only clean up if this test class uses the class-scoped fixtures
    ftp_root = request.node.funcargs.get("ftp_root")
    if ftp_root is None:
        return
    server = request.node.funcargs.get("ftp_server")
    if server is not None:
        server.clear_failures()
    # Restore clean directory structure
    root = str(ftp_root)
    for entry in os.listdir(root):
        path = os.path.join(root, entry)
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    for d in BAMBU_DIRS:
        os.makedirs(os.path.join(root, d), exist_ok=True)


@pytest.fixture()
def ftp_client_factory(ftp_server):
    """Factory that creates BambuFTPClient instances pointed at the mock server."""

    def _make_client(
        printer_model: str = "X1C",
        force_prot_c: bool = False,
        access_code: str = "12345678",
        timeout: float = 10.0,
    ) -> BambuFTPClient:
        client = BambuFTPClient(
            ip_address="127.0.0.1",
            access_code=access_code,
            timeout=timeout,
            printer_model=printer_model,
            force_prot_c=force_prot_c,
        )
        # Override port to point at mock server
        client.FTP_PORT = ftp_server.port
        return client

    return _make_client


@pytest.fixture(autouse=True)
def clear_ftp_mode_cache():
    """Clear BambuFTPClient mode cache before and after each test."""
    BambuFTPClient._mode_cache.clear()
    yield
    BambuFTPClient._mode_cache.clear()


@pytest.fixture()
def patch_ftp_port(ftp_server):
    """Patch FTP_PORT at class level for async wrapper tests.

    Async wrappers create their own BambuFTPClient instances internally,
    so we need to patch the class-level default port.
    """
    with patch.object(BambuFTPClient, "FTP_PORT", ftp_server.port):
        yield ftp_server
