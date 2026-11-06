"""The Home Assistant connection test names a certificate problem (#3300).

httpx raises an untrusted certificate as ``ConnectError``, so after moving HA
to https the test said "Could not connect to Home Assistant" for an HA that was
up and answering. These run against real local servers on 127.0.0.1.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from backend.app.services.homeassistant import HomeAssistantService

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest.fixture(autouse=True)
def _certifi_trust_store(monkeypatch):
    """httpx reads SSL_CERT_FILE / SSL_CERT_DIR before certifi (USE_SYSTEM_TRUST_STORE sets one)."""
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)


def _cert(subject_cn: str, key, issuer_cn: str, issuer_key, san: x509.GeneralName, ca: bool, expired: bool = False):
    now = datetime.now(timezone.utc)
    return (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_cn)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_cn)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=60 if expired else 1))
        .not_valid_after(now - timedelta(days=1) if expired else now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([san]), critical=False)
        # Python 3.13 verifies strictly, which wants key usage and both key identifiers.
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=ca,
                crl_sign=ca,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False)
        .sign(issuer_key, hashes.SHA256())
    )


def _pem(path, cert, key=None):
    data = cert.public_bytes(serialization.Encoding.PEM)
    if key is not None:
        data += key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    path.write_bytes(data)
    return str(path)


async def _https_server(certfile: str):
    """An HA stand-in that answers /api/ over TLS with the given certificate."""
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(certfile)

    async def handle(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
            body = b'{"message": "API running."}'
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body)
            )
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError, ssl.SSLError):
            pass
        finally:
            writer.close()

    return await asyncio.start_server(handle, "127.0.0.1", 0, ssl=ctx)


def _port(server) -> int:
    return server.sockets[0].getsockname()[1]


async def test_a_self_signed_certificate_is_named(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    cert = _cert("homeassistant", key, "homeassistant", key, x509.IPAddress(ipaddress.ip_address("127.0.0.1")), True)
    server = await _https_server(_pem(tmp_path / "ha.pem", cert, key))
    async with server:
        result = await HomeAssistantService().test_connection(f"https://127.0.0.1:{_port(server)}", "token")

    assert result["success"] is False
    assert result["error"].startswith("Home Assistant's certificate is not trusted (self-signed certificate).")
    assert "add it to Bambuddy's trust store" in result["error"]


async def test_a_certificate_for_another_hostname_is_named(tmp_path):
    """A trusted certificate reached by IP address, as with Let's Encrypt on a LAN."""
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = _cert("Test CA", ca_key, "Test CA", ca_key, x509.DNSName("ca.test"), True)
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = _cert("ha.example.org", leaf_key, "Test CA", ca_key, x509.DNSName("ha.example.org"), False)
    server = await _https_server(_pem(tmp_path / "leaf.pem", leaf, leaf_key))
    ca_file = _pem(tmp_path / "ca.pem", ca)
    async with server:
        # httpx builds its trust store from certifi.where().
        with patch("certifi.where", return_value=ca_file):
            trusted = await HomeAssistantService().test_connection(f"https://127.0.0.1:{_port(server)}", "token")

    assert trusted["success"] is False
    assert trusted["error"].startswith(
        "Home Assistant's certificate is not trusted (IP address mismatch, certificate is not valid for '127.0.0.1')."
    )
    assert trusted["error"].endswith(
        "Enter the hostname the certificate was issued for, not an IP address or another name."
    )


async def test_an_expired_certificate_is_named(tmp_path):
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = _cert("Test CA", ca_key, "Test CA", ca_key, x509.DNSName("ca.test"), True)
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    ip = x509.IPAddress(ipaddress.ip_address("127.0.0.1"))
    leaf = _cert("homeassistant", leaf_key, "Test CA", ca_key, ip, False, expired=True)
    server = await _https_server(_pem(tmp_path / "leaf.pem", leaf, leaf_key))
    ca_file = _pem(tmp_path / "ca.pem", ca)
    async with server:
        with patch("certifi.where", return_value=ca_file):
            result = await HomeAssistantService().test_connection(f"https://127.0.0.1:{_port(server)}", "token")

    assert result["error"] == (
        "Home Assistant's certificate is not trusted (certificate has expired). "
        "Renew the certificate Home Assistant serves."
    )


async def test_https_to_a_plain_http_port_is_named():
    async def handle(reader, writer):
        await reader.read(1024)
        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    async with server:
        result = await HomeAssistantService().test_connection(f"https://127.0.0.1:{_port(server)}", "token")

    assert result["success"] is False
    assert result["error"].startswith("The secure connection to Home Assistant failed (")
    assert result["error"].endswith("Check that Home Assistant serves https at this address and port.")


async def test_nothing_listening_still_says_could_not_connect():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    result = await HomeAssistantService().test_connection(f"https://127.0.0.1:{port}", "token")

    assert result == {"success": False, "message": None, "error": "Could not connect to Home Assistant"}
