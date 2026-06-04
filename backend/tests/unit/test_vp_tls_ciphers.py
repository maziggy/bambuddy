"""Regression tests for #1610: every slicer-facing VP TLS context must
explicitly include the plain-RSA AES-GCM cipher suites that real Bambu
printers (and therefore the BambuStudio / OrcaSlicer client paths) expect.

Real Bambu printers offer only ``AES256-GCM-SHA384`` / ``AES128-GCM-SHA256``
(plain RSA key exchange) on their TLS endpoints. Slicers built against
that surface assume the server side will accept those suites. On
distributions whose OpenSSL ``DEFAULT`` cipher list has been narrowed by a
system crypto policy (Fedora / RHEL ``update-crypto-policies``, hardened
Alpine builds), Python's stock ``SSLContext`` ends up offering only
ECDHE/DHE — no overlap with the slicer's ClientHello, the handshake
aborts, and the slicer reports a generic ``code=-1`` connect error.

The #620 patch fixed this for the printer-facing CLIENT context in
``tcp_proxy.py::_create_client_ssl_context``. #1610 audited the remaining
slicer-facing surface and applied the same explicit cipher pin to every
context that accepts a slicer connection:

* ``bind_server.py::_create_tls_context``      — port 3002 (bind/detect)
* ``mqtt_server.py`` (inline in ``start``)     — port 8883 (MQTT-over-TLS)
* ``tcp_proxy.py::_create_server_ssl_context`` — proxy-mode 3002
* ``ftp_server.py`` (inline in ``start``)      — port 990 (FTPS)

If any of these regress to a context that no longer offers
``AES256-GCM-SHA384`` / ``AES128-GCM-SHA256``, users on hardened distros
will hit the #1610 / #620 cipher-mismatch failure mode.
"""

import ssl
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.services.virtual_printer.bind_server import BindServer
from backend.app.services.virtual_printer.certificate import CertificateService
from backend.app.services.virtual_printer.ftp_server import VirtualPrinterFTPServer
from backend.app.services.virtual_printer.mqtt_server import SimpleMQTTServer
from backend.app.services.virtual_printer.tcp_proxy import TLSProxy

REQUIRED_CIPHERS = ("AES256-GCM-SHA384", "AES128-GCM-SHA256")


def _cert_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Generate a real self-signed CA + per-VP cert pair via the production
    CertificateService. Returns ``(cert_path, key_path)`` suitable for
    ``load_cert_chain`` calls inside the VP services under test."""
    svc = CertificateService(cert_dir=tmp_path, serial="01P00A391800001")
    return svc.ensure_certificates()


def _assert_required_ciphers(ctx: ssl.SSLContext, where: str) -> None:
    """Fail with a useful diagnostic if either required cipher is missing."""
    offered = {c["name"] for c in ctx.get_ciphers()}
    missing = [c for c in REQUIRED_CIPHERS if c not in offered]
    assert not missing, (
        f"{where}: missing plain-RSA AES-GCM cipher(s) {missing}. "
        f"Real Bambu printers / slicers require these on the slicer-facing "
        f"TLS surface — see #1610. Offered ciphers: {sorted(offered)}"
    )


class TestBindServerTlsCiphers:
    def test_create_tls_context_offers_plain_rsa_aes_gcm(self, tmp_path):
        cert_path, key_path = _cert_pair(tmp_path)
        server = BindServer(
            serial="01P00A391800001",
            model="C12",
            name="vp",
            version="01.07.00.00",
            bind_address="127.0.0.1",
            cert_path=cert_path,
            key_path=key_path,
        )
        ctx = server._create_tls_context()
        assert ctx is not None
        _assert_required_ciphers(ctx, "bind_server._create_tls_context")


class TestTlsProxyServerCiphers:
    """Slicer-facing side of proxy mode — was not patched by the #620 fix."""

    def test_create_server_ssl_context_offers_plain_rsa_aes_gcm(self, tmp_path):
        cert_path, key_path = _cert_pair(tmp_path)
        proxy = TLSProxy(
            name="Bind-TLS",
            listen_port=3002,
            target_host="127.0.0.1",
            target_port=3002,
            server_cert_path=str(cert_path),
            server_key_path=str(key_path),
            on_connect=lambda cid: None,
            on_disconnect=lambda cid: None,
            bind_address="127.0.0.1",
        )
        ctx = proxy._create_server_ssl_context()
        _assert_required_ciphers(ctx, "tcp_proxy._create_server_ssl_context")

    def test_create_client_ssl_context_still_offers_plain_rsa_aes_gcm(self, tmp_path):
        """The original #620 fix must remain in place."""
        cert_path, key_path = _cert_pair(tmp_path)
        proxy = TLSProxy(
            name="Bind-TLS",
            listen_port=3002,
            target_host="127.0.0.1",
            target_port=3002,
            server_cert_path=str(cert_path),
            server_key_path=str(key_path),
            on_connect=lambda cid: None,
            on_disconnect=lambda cid: None,
            bind_address="127.0.0.1",
        )
        ctx = proxy._create_client_ssl_context()
        _assert_required_ciphers(ctx, "tcp_proxy._create_client_ssl_context")


class TestMqttServerTlsCiphers:
    """MQTT server builds its SSLContext inline in start(); intercept the
    ``asyncio.start_server`` call so the test doesn't actually bind a port."""

    @pytest.mark.asyncio
    async def test_start_configures_plain_rsa_aes_gcm(self, tmp_path):
        cert_path, key_path = _cert_pair(tmp_path)
        server = SimpleMQTTServer(
            serial="01P00A391800001",
            access_code="deadbeef",
            cert_path=cert_path,
            key_path=key_path,
            model="C12",
            bind_address="127.0.0.1",
        )

        captured: dict[str, ssl.SSLContext] = {}

        async def _capture(*_args, ssl=None, **_kwargs):
            captured["ctx"] = ssl

            class _FakeServer:
                sockets = []

                def close(self):
                    pass

                async def wait_closed(self):
                    pass

                def is_serving(self):
                    return False

            return _FakeServer()

        with patch("asyncio.start_server", _capture):
            await server.start()
        try:
            assert "ctx" in captured, "asyncio.start_server was not invoked"
            _assert_required_ciphers(captured["ctx"], "mqtt_server.start")
        finally:
            await server.stop()


class TestFtpServerTlsCiphers:
    """FTP server builds its SSLContext inline in start()."""

    @pytest.mark.asyncio
    async def test_start_configures_plain_rsa_aes_gcm(self, tmp_path):
        cert_path, key_path = _cert_pair(tmp_path)
        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        server = VirtualPrinterFTPServer(
            upload_dir=upload_dir,
            access_code="deadbeef",
            cert_path=cert_path,
            key_path=key_path,
            bind_address="127.0.0.1",
        )

        captured: dict[str, ssl.SSLContext] = {}

        async def _capture(*_args, ssl=None, **_kwargs):
            captured["ctx"] = ssl

            class _FakeServer:
                sockets = []

                def close(self):
                    pass

                async def wait_closed(self):
                    pass

                def is_serving(self):
                    return False

                async def serve_forever(self):
                    pass

            return _FakeServer()

        with patch("asyncio.start_server", _capture):
            await server.start()
        try:
            assert "ctx" in captured, "asyncio.start_server was not invoked"
            _assert_required_ciphers(captured["ctx"], "ftp_server.start")
        finally:
            await server.stop()
