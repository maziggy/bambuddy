"""MakerWorld HTTP layer.

Constants and the low-level transport helpers for the MakerWorld / Bambu Lab
APIs: the S3 presigned-download path that must reach the transport
byte-for-byte, upstream error extraction, and the CDN SSRF guard helpers used
by :class:`MakerWorldService`.

The app-scoped shared ``httpx`` client lives with its consumer instead (see
``service.set_shared_http_client``) — same-module so the service reads the
live value, matching ``bambu_cloud`` / ``orca_cloud`` / ``slicer_api``.
"""

from __future__ import annotations

import asyncio
import ssl

import certifi
import httpx

from backend.app.services.model_providers.makerworld.errors import MakerWorldUnavailableError

# API base: ``api.bambulab.com/v1/design-service`` — the same Bambu Cloud
# backend that the MakerWorld web UI talks to, but not behind Cloudflare
# (the website ``makerworld.com`` is, and plain httpx requests there get
# fingerprinted as bot traffic and served "Please log in").
MAKERWORLD_API_BASE = "https://api.bambulab.com/v1/design-service"

# Hosts that the iot-service download endpoint may return presigned URLs
# for. Besides MakerWorld's own CDN, Bambu Cloud also issues AWS S3
# presigned URLs (e.g. ``s3.us-west-2.amazonaws.com``). The suffix check
# matches any regional S3 endpoint.
_ALLOWED_DOWNLOAD_SUFFIXES = (".amazonaws.com",)

# The SSRF allowlist for MakerWorld CDN traffic — both the thumbnail proxy
# (exact host match, surfaced through ``MakerWorldProvider.thumbnail_hosts``)
# and the 3MF download path. Lives here with the other transport guards so
# the whole allowlist is in one place.
MAKERWORLD_CDN_HOSTS = ("makerworld.bblmw.com", "public-cdn.bblmw.com")

# Client identity sent to MakerWorld / api.bambulab.com. We identify honestly
# as Bambuddy with a source URL so Bambu can distinguish our traffic from
# impersonators — the opposite of what the OrcaSlicer fork was called out for
# in the May 2026 Bambu Lab blog post on cloud access. The Referer is kept
# because MakerWorld's CSRF / origin-check middleware uses it on some
# endpoints — that's distinct from client impersonation.
_CLIENT_HEADERS = {
    "User-Agent": "Bambuddy/1.0 (+https://github.com/maziggy/bambuddy)",
    "Accept": "text/html,application/json,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://makerworld.com/",
}

_MAX_3MF_BYTES = 200 * 1024 * 1024  # 200 MB hard cap
_MAX_THUMBNAIL_BYTES = 10 * 1024 * 1024  # 10 MB hard cap — MakerWorld's "thumbnails" can be 2–3 MB source images

_IMAGE_EXT_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}
# Content types we refuse even if the URL extension looks image-y — prevents
# forwarding an upstream error page or JSON blob with image framing.
_REFUSED_THUMBNAIL_MIMES = ("text/html", "text/plain", "application/json")


def _s3_ssl_context() -> ssl.SSLContext:
    """Build the TLS context used for the S3 presigned download (#2562).

    ``urllib.request`` verifies against the *OS* trust store, while httpx —
    every other network call in Bambuddy — verifies against the bundled
    ``certifi`` CA bundle. On Windows those two disagree: Python's
    ``ssl.load_default_certs()`` only enumerates the roots already cached in
    the Windows ROOT store, and Windows populates that store lazily via
    CryptoAPI's auto-update, which Python never triggers. If the Amazon root
    signing the S3 chain isn't cached on that machine yet, verification fails
    with ``unable to get local issuer certificate`` — even though the
    api.bambulab.com calls that preceded it (httpx) succeeded.

    Pinning urllib to certifi makes the S3 hop trust exactly what the rest of
    the app already trusts. Built per call rather than at import so a certifi
    refresh doesn't require a restart; construction is cheap relative to the
    download that follows.
    """
    return ssl.create_default_context(cafile=certifi.where())


async def _download_s3_urllib(url: str, filename_fallback: str) -> tuple[bytes, str]:
    """Fetch an AWS S3 presigned URL without touching the query string.

    ``urllib.request`` passes the URL to the transport verbatim — which is
    essential for S3 presigned URLs where the signature is computed over
    the exact query-string bytes. httpx's ``URL`` class and curl_cffi's
    libcurl layer both normalise encodings and produce
    ``SignatureDoesNotMatch`` 400s from S3.

    Runs the blocking urllib call in a thread executor so we don't stall
    the event loop.
    """
    from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

    # Don't follow redirects: the host allowlist is only enforced on
    # the initial URL. A 302 from S3 to any other host would otherwise
    # transparently bypass the allowlist — so insist S3 resolve directly.
    class _NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):  # type: ignore[override]
            return None

    # HTTPSHandler swaps only the TLS context — the URL still reaches the
    # transport verbatim, which is what the S3 signature depends on.
    opener = build_opener(_NoRedirect, HTTPSHandler(context=_s3_ssl_context()))

    def _blocking_fetch() -> bytes:
        req = Request(url, headers={"User-Agent": _CLIENT_HEADERS["User-Agent"]})
        with opener.open(req, timeout=60.0) as resp:
            if resp.status != 200:
                raise MakerWorldUnavailableError(f"3MF download returned HTTP {resp.status}")
            data = b""
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                data += chunk
                if len(data) > _MAX_3MF_BYTES:
                    raise MakerWorldUnavailableError(f"3MF exceeds {_MAX_3MF_BYTES // (1024 * 1024)} MB cap")
            return data

    try:
        data = await asyncio.to_thread(_blocking_fetch)
    except MakerWorldUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 — urllib throws a zoo of exceptions
        raise MakerWorldUnavailableError(f"S3 download failed: {exc}") from exc
    return data, filename_fallback


def _extract_upstream_error(response: httpx.Response) -> str | None:
    """Pull MakerWorld's own error text out of a 4xx/5xx response body.

    MakerWorld returns ``{"code": N, "error": "text"}`` on auth/perm failures
    and sometimes ``{"message": "..."}`` on other errors. Returns ``None`` if
    the body isn't JSON or doesn't have a recognised error field — callers
    should fall back to a generic message in that case.
    """
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    for key in ("error", "message", "detail"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
