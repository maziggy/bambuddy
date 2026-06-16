"""Unit tests for backend.app.services.oidc_icon.fetch_icon (#1333).

Uses ``patch("backend.app.services.oidc_icon.httpx.AsyncClient", ...)`` —
the same mocking pattern the project uses in ``test_mfa_api.py`` for OIDC
discovery/JWKS calls. Streaming-mock helper lives in
``backend/tests/_fixtures/oidc_icon.py``.
"""

import hashlib
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from backend.app.services.oidc_icon import (
    OIDCIconUnavailableError,
    OIDCIconUrlError,
    _resolve_content_type,
    fetch_icon,
)
from backend.tests._fixtures.oidc_icon import (
    PNG_BYTES,
    PNG_ETAG,
    build_streaming_icon_mock,
)

# ─── _resolve_content_type — pure helper, tested directly ────────────────


class TestResolveContentType:
    @pytest.mark.parametrize(
        "mime",
        ["image/png", "image/jpeg", "image/webp", "image/gif"],
    )
    def test_accepts_whitelisted_mime(self, mime):
        assert _resolve_content_type(mime, "/icon") == mime

    def test_octet_stream_with_png_extension(self):
        assert _resolve_content_type("application/octet-stream", "/path/icon.png") == "image/png"

    def test_octet_stream_with_jpeg_extension(self):
        assert _resolve_content_type("application/octet-stream", "/icon.jpeg") == "image/jpeg"

    def test_octet_stream_without_extension_raises(self):
        with pytest.raises(OIDCIconUnavailableError, match="no image extension"):
            _resolve_content_type("application/octet-stream", "/icon")

    def test_missing_content_type_distinct_message(self):
        # N6: empty string → distinct "missing Content-Type" message,
        # not user-hostile "unsupported content-type: ''".
        with pytest.raises(OIDCIconUnavailableError, match="missing a Content-Type header"):
            _resolve_content_type("", "/icon.png")

    @pytest.mark.parametrize(
        "mime",
        ["image/svg+xml", "text/html", "application/json", "application/pdf", "text/plain"],
    )
    def test_disallowed_mime_raises_with_value(self, mime):
        with pytest.raises(OIDCIconUnavailableError, match="content-type"):
            _resolve_content_type(mime, "/icon.png")


# ─── fetch_icon — happy paths (streaming) ─────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mime",
    ["image/png", "image/jpeg", "image/webp", "image/gif"],
)
async def test_accepts_whitelisted_mime(mime):
    mock_cls, _ = build_streaming_icon_mock(content_type=mime)
    with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
        payload, ct, etag = await fetch_icon("https://example.com/icon")
    assert payload == PNG_BYTES
    assert ct == mime
    assert etag == PNG_ETAG


@pytest.mark.asyncio
async def test_etag_is_deterministic_sha256():
    mock_cls, _ = build_streaming_icon_mock()
    with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
        _, _, etag_a = await fetch_icon("https://example.com/a.png")
        _, _, etag_b = await fetch_icon("https://example.com/b.png")
    assert etag_a == etag_b  # same bytes → same etag
    assert etag_a == hashlib.sha256(PNG_BYTES).hexdigest()


@pytest.mark.asyncio
async def test_octet_stream_with_png_extension_accepted():
    mock_cls, _ = build_streaming_icon_mock(content_type="application/octet-stream")
    with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
        _, ct, _ = await fetch_icon("https://cdn.example.com/path/icon.png")
    assert ct == "image/png"


# ─── fetch_icon — rejects: scheme ────────────────────────────────────────


@pytest.mark.asyncio
async def test_rejects_non_https():
    with pytest.raises(OIDCIconUrlError, match="https"):
        await fetch_icon("http://example.com/icon.png")


# ─── fetch_icon — rejects: HTTP status codes ─────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [301, 302, 307, 308, 404, 500, 502])
async def test_rejects_non_200(status_code):
    mock_cls, _ = build_streaming_icon_mock(status_code=status_code)
    with (
        patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls),
        pytest.raises(OIDCIconUnavailableError, match=f"HTTP {status_code}"),
    ):
        await fetch_icon("https://example.com/icon")


# ─── fetch_icon — rejects: content types ─────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mime",
    [
        "image/svg+xml",  # SVG explicitly excluded in v1
        "text/html",
        "application/json",
        "application/pdf",
        "text/plain",
    ],
)
async def test_rejects_disallowed_mime(mime):
    mock_cls, _ = build_streaming_icon_mock(content_type=mime)
    with (
        patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls),
        pytest.raises(OIDCIconUnavailableError, match="content-type"),
    ):
        await fetch_icon("https://example.com/icon")


@pytest.mark.asyncio
async def test_rejects_octet_stream_without_image_extension():
    mock_cls, _ = build_streaming_icon_mock(content_type="application/octet-stream")
    with (
        patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls),
        pytest.raises(OIDCIconUnavailableError, match="no image extension"),
    ):
        await fetch_icon("https://example.com/icon")


@pytest.mark.asyncio
async def test_rejects_missing_content_type_header():
    # N6: distinct message when upstream omits Content-Type entirely.
    mock_cls, _ = build_streaming_icon_mock(content_type=None)
    with (
        patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls),
        pytest.raises(OIDCIconUnavailableError, match="missing a Content-Type"),
    ):
        await fetch_icon("https://example.com/icon.png")


# ─── fetch_icon — rejects: payload size (streaming early-exit) ───────────


@pytest.mark.asyncio
async def test_rejects_oversized_payload_via_streaming_early_exit():
    """I4: size-cap fires DURING streaming, not after full buffer.

    The 2 MB payload is emitted in 4 KB chunks. The cap (1 MB) is crossed
    around chunk 256; fetch_icon must raise BEFORE the remaining ~256
    chunks are buffered. We don't observe the early-exit timing
    directly — we just confirm the right exception with the right
    message is raised; the streaming-mock structure guarantees the
    code path went through aiter_bytes().
    """
    too_big = b"\x89PNG" + b"\x00" * (2 * 1024 * 1024)  # 2 MB > 1 MB cap
    mock_cls, _ = build_streaming_icon_mock(body=too_big, chunk_size=4096)
    with (
        patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls),
        pytest.raises(OIDCIconUnavailableError, match="cap"),
    ):
        await fetch_icon("https://example.com/icon.png")


@pytest.mark.asyncio
async def test_streaming_size_cap_aborts_at_first_chunk_past_limit():
    """Stronger guarantee: when the very first chunk exceeds the cap,
    we abort on that chunk — no further iteration."""
    chunks_seen = 0

    async def _hostile_aiter_bytes():
        nonlocal chunks_seen
        # First chunk: 2 MB in one go — already over the 1 MB cap.
        chunks_seen += 1
        yield b"\x00" * (2 * 1024 * 1024)
        # This second chunk must NEVER be reached.
        chunks_seen += 1
        yield b"\x00" * 100

    response = SimpleNamespace(
        status_code=200,
        headers={"content-type": "image/png"},
        aiter_bytes=_hostile_aiter_bytes,
    )

    class _StreamCtx:
        async def __aenter__(self):
            return response

        async def __aexit__(self, *_exc):
            return False

    class _MockHttpxClient:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        def stream(self, *_a, **_kw):
            return _StreamCtx()

    with (
        patch("backend.app.services.oidc_icon.httpx.AsyncClient", _MockHttpxClient),
        pytest.raises(OIDCIconUnavailableError, match="cap"),
    ):
        await fetch_icon("https://example.com/icon.png")
    assert chunks_seen == 1, "size-cap must abort on first oversized chunk"


@pytest.mark.asyncio
async def test_rejects_empty_body():
    mock_cls, _ = build_streaming_icon_mock(body=b"")
    with (
        patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls),
        pytest.raises(OIDCIconUnavailableError, match="empty"),
    ):
        await fetch_icon("https://example.com/icon.png")


# ─── fetch_icon — network errors ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_raises_unavailable():
    class _TimingOutClient:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        def stream(self, *_a, **_kw):
            class _Ctx:
                async def __aenter__(_self):
                    raise httpx.TimeoutException("timed out")

                async def __aexit__(_self, *_exc):
                    return False

            return _Ctx()

    with (
        patch("backend.app.services.oidc_icon.httpx.AsyncClient", _TimingOutClient),
        pytest.raises(OIDCIconUnavailableError, match="timed out"),
    ):
        await fetch_icon("https://example.com/icon")


@pytest.mark.asyncio
async def test_connection_error_raises_unavailable():
    class _ErrClient:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        def stream(self, *_a, **_kw):
            class _Ctx:
                async def __aenter__(_self):
                    raise httpx.ConnectError("connection refused")

                async def __aexit__(_self, *_exc):
                    return False

            return _Ctx()

    with (
        patch("backend.app.services.oidc_icon.httpx.AsyncClient", _ErrClient),
        pytest.raises(OIDCIconUnavailableError, match="failed"),
    ):
        await fetch_icon("https://example.com/icon")


# ─── C1: httpx.InvalidURL → OIDCIconUrlError (not a 500) ─────────────────


@pytest.mark.asyncio
async def test_invalid_url_raises_url_error():
    """C1: httpx.InvalidURL is NOT a subclass of httpx.HTTPError. Must be
    caught explicitly and mapped to OIDCIconUrlError → 400, not 500."""

    class _InvalidUrlClient:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        def stream(self, *_a, **_kw):
            class _Ctx:
                async def __aenter__(_self):
                    raise httpx.InvalidURL("Invalid non-printable ASCII character in URL")

                async def __aexit__(_self, *_exc):
                    return False

            return _Ctx()

    with (
        patch("backend.app.services.oidc_icon.httpx.AsyncClient", _InvalidUrlClient),
        pytest.raises(OIDCIconUrlError, match="Invalid icon URL"),
    ):
        await fetch_icon("https://example.com/icon")


# ─── follow_redirects=False is non-negotiable ────────────────────────────


@pytest.mark.asyncio
async def test_passes_follow_redirects_false():
    """Defence-in-depth: verify we explicitly pass follow_redirects=False so
    an upstream 302 cannot bypass the SSRF host check on the initial URL."""
    mock_cls, stream_recorder = build_streaming_icon_mock()
    with patch("backend.app.services.oidc_icon.httpx.AsyncClient", mock_cls):
        await fetch_icon("https://example.com/icon.png")
    stream_recorder.assert_called_once()
    _args, kwargs = stream_recorder.call_args
    assert kwargs.get("follow_redirects") is False
