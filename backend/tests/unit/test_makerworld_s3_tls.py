"""Tests for the S3 presigned-download path in ``services/makerworld.py``.

MakerWorld hands back an AWS presigned URL for the 3MF, and we fetch that one
with ``urllib.request`` rather than httpx — httpx re-encodes the query string
and invalidates the S3 signature. That choice silently changed the trust
store: urllib verifies against the OS CA store, httpx against the bundled
``certifi`` bundle. On Windows the two disagree and the download dies with
``CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`` (#2562).

These tests pin the fix (the opener carries a certifi-backed TLS context) and
the two properties the fix must not break: the no-redirect SSRF guard, and the
URL reaching the transport byte-for-byte.
"""

from __future__ import annotations

import ssl
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import certifi
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from backend.app.services import makerworld as mw

# A presigned URL in the shape Bambu Cloud actually mints: the signature is
# computed over these exact query-string bytes, so any re-encoding breaks it.
S3_URL = (
    "https://s3.us-west-2.amazonaws.com/bbl-prod/models/benchy.3mf"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
    "&X-Amz-Credential=AKIA%2F20260714%2Fus-west-2%2Fs3%2Faws4_request"
    "&X-Amz-Date=20260714T070000Z&X-Amz-Expires=300"
    "&X-Amz-Signature=abc123&X-Amz-SignedHeaders=host"
)


def _write_test_ca(path) -> str:
    """Write a throwaway self-signed CA to ``path`` and return its CN.

    Lets a test assert the opener's TLS context was loaded from *certifi's*
    bundle specifically, rather than from the OS store or any other source:
    we point ``certifi.where()`` at this file and then check the context
    trusts exactly this one cert.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    common_name = "Bambuddy Test Root CA"
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return common_name


class _FakeResponse:
    """Stand-in for the ``http.client.HTTPResponse`` urllib hands back."""

    def __init__(self, body: bytes, status: int = 200):
        self.status = status
        self._body = body
        self._offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size: int) -> bytes:
        chunk = self._body[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class _OpenerCapture:
    """Captures the handlers ``build_opener`` was called with, and the Request
    the resulting opener was asked to open."""

    def __init__(self, response: _FakeResponse | None = None, raises: BaseException | None = None):
        self.handlers: tuple = ()
        self.request = None
        self._response = response or _FakeResponse(b"3MF")
        self._raises = raises

    def build_opener(self, *handlers):
        self.handlers = handlers
        opener = MagicMock()
        opener.open = self._open
        return opener

    def _open(self, request, timeout=None):
        self.request = request
        if self._raises is not None:
            raise self._raises
        return self._response

    def https_handler(self):
        for handler in self.handlers:
            if isinstance(handler, mw_https_handler_type()):
                return handler
        return None


def mw_https_handler_type():
    from urllib.request import HTTPSHandler

    return HTTPSHandler


def _patched_opener(capture: _OpenerCapture):
    """``_download_s3_urllib`` imports ``build_opener`` from ``urllib.request``
    at call time, so patching the module attribute is enough."""
    return patch("urllib.request.build_opener", side_effect=capture.build_opener)


class TestS3TrustStore:
    """The regression under test: urllib must not fall back to the OS CA store."""

    @pytest.mark.asyncio
    async def test_opener_gets_an_https_handler(self):
        """Without an explicit HTTPSHandler, urllib builds its own from the OS
        trust store — which is exactly what fails on Windows (#2562)."""
        capture = _OpenerCapture()
        with _patched_opener(capture):
            await mw._download_s3_urllib(S3_URL, "benchy.3mf")

        handler = capture.https_handler()
        assert handler is not None, "opener was built without an HTTPSHandler — falls back to the OS trust store"
        assert isinstance(handler._context, ssl.SSLContext)

    @pytest.mark.asyncio
    async def test_context_is_loaded_from_certifi(self, tmp_path, monkeypatch):
        """Point certifi at a bundle holding one throwaway root; the opener's
        context must trust exactly that root and nothing else. Proves the CAs
        come from certifi rather than the system store."""
        ca_pem = tmp_path / "test-cacert.pem"
        common_name = _write_test_ca(ca_pem)
        monkeypatch.setattr(mw.certifi, "where", lambda: str(ca_pem))

        capture = _OpenerCapture()
        with _patched_opener(capture):
            await mw._download_s3_urllib(S3_URL, "benchy.3mf")

        loaded = capture.https_handler()._context.get_ca_certs()
        assert len(loaded) == 1, f"expected only the certifi bundle's cert, got {len(loaded)}"
        subject_values = [value for rdn in loaded[0]["subject"] for _, value in rdn]
        assert common_name in subject_values

    @pytest.mark.asyncio
    async def test_context_verifies_and_checks_hostname(self):
        """certifi swaps the CA source, not the verification policy — a context
        with verification off would 'fix' #2562 by disabling TLS security."""
        capture = _OpenerCapture()
        with _patched_opener(capture):
            await mw._download_s3_urllib(S3_URL, "benchy.3mf")

        context = capture.https_handler()._context
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True

    def test_real_context_trusts_the_certifi_bundle(self):
        """Sanity-check the un-mocked helper against the shipped bundle: it must
        load a real-world number of roots, not an empty set."""
        context = mw._s3_ssl_context()
        assert len(context.get_ca_certs()) == len(ssl.create_default_context(cafile=certifi.where()).get_ca_certs())
        assert len(context.get_ca_certs()) > 50


class TestS3DownloadUnchanged:
    """Properties the TLS fix must not regress."""

    @pytest.mark.asyncio
    async def test_redirects_are_still_refused(self):
        """The host allowlist is only enforced on the initial URL, so following
        a 302 off S3 would bypass it. The no-redirect handler must survive."""
        capture = _OpenerCapture()
        with _patched_opener(capture):
            await mw._download_s3_urllib(S3_URL, "benchy.3mf")

        from urllib.request import HTTPRedirectHandler

        # build_opener takes handler classes *or* instances; the redirect
        # blocker is passed as a class, so normalise before probing it.
        blockers = []
        for handler in capture.handlers:
            instance = handler() if isinstance(handler, type) else handler
            if isinstance(instance, HTTPRedirectHandler):
                if instance.redirect_request(None, None, None, None, None) is None:
                    blockers.append(instance)
        assert blockers, "no redirect-blocking handler passed to build_opener"

    @pytest.mark.asyncio
    async def test_url_reaches_the_transport_verbatim(self):
        """The whole reason this path uses urllib: S3 signs the exact
        query-string bytes. Any normalisation yields SignatureDoesNotMatch."""
        capture = _OpenerCapture()
        with _patched_opener(capture):
            await mw._download_s3_urllib(S3_URL, "benchy.3mf")

        assert capture.request.full_url == S3_URL

    @pytest.mark.asyncio
    async def test_returns_body_and_filename(self):
        capture = _OpenerCapture(response=_FakeResponse(b"PK\x03\x04payload"))
        with _patched_opener(capture):
            data, filename = await mw._download_s3_urllib(S3_URL, "benchy.3mf")

        assert data == b"PK\x03\x04payload"
        assert filename == "benchy.3mf"

    @pytest.mark.asyncio
    async def test_non_200_raises_unavailable(self):
        capture = _OpenerCapture(response=_FakeResponse(b"", status=403))
        with _patched_opener(capture), pytest.raises(mw.MakerWorldUnavailableError, match="HTTP 403"):
            await mw._download_s3_urllib(S3_URL, "benchy.3mf")

    @pytest.mark.asyncio
    async def test_size_cap_enforced(self, monkeypatch):
        monkeypatch.setattr(mw, "_MAX_3MF_BYTES", 1024)
        capture = _OpenerCapture(response=_FakeResponse(b"x" * 4096))
        with _patched_opener(capture), pytest.raises(mw.MakerWorldUnavailableError, match="exceeds"):
            await mw._download_s3_urllib(S3_URL, "benchy.3mf")

    @pytest.mark.asyncio
    async def test_tls_failure_still_surfaces_as_s3_download_failed(self):
        """If verification fails for a genuine reason (expired cert, MITM proxy),
        the user must still get the actionable wrapped error — the fix removes
        the spurious failures, it doesn't swallow the real ones."""
        verify_error = ssl.SSLCertVerificationError("certificate verify failed: unable to get local issuer certificate")
        capture = _OpenerCapture(raises=verify_error)
        with _patched_opener(capture), pytest.raises(mw.MakerWorldUnavailableError, match="S3 download failed"):
            await mw._download_s3_urllib(S3_URL, "benchy.3mf")
