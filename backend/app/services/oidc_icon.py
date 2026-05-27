"""OIDC provider icon fetcher (#1333).

Server-side proxy that fetches an admin-supplied icon URL and returns
``(bytes, content_type, etag)``. The bytes are cached in the
``oidc_providers.icon_data`` BLOB column so the SPA can serve them from
``/api/v1/auth/oidc/providers/{id}/icon`` (same-origin) — avoiding any
loosening of the strict ``img-src 'self' data: blob:`` CSP.

Pattern mirrors ``services/makerworld.fetch_thumbnail``:
- ``follow_redirects=False`` so the SSRF host allowlist (here: assert_safe_public_https_url)
  isn't bypassed by a 302 to a private address.
- MIME whitelist (PNG/JPEG/WebP/GIF). SVG is rejected in v1 — XML payloads
  carry too many corner cases (xlink, external refs) for an MVP.
- ``application/octet-stream`` is accepted only if the URL path ends in a
  whitelisted image extension; the response Content-Type alone is not
  trusted because some CDNs serve images as octet-stream.
- 1 MB hard cap (typical OIDC icons are 5-50 KB; 1 MB is generous).
- 10s timeout, matching the OIDC discovery/JWKS timeouts in routes/mfa.py.
"""

from __future__ import annotations

import hashlib
import logging
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


_MAX_ICON_BYTES = 1 * 1024 * 1024  # 1 MB
_FETCH_TIMEOUT_SECONDS = 10.0

# Content-Type whitelist. SVG is intentionally omitted — see module docstring.
_ALLOWED_MIME_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
    }
)

# Extension → MIME fallback for ``application/octet-stream`` responses.
_EXT_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


class OIDCIconError(Exception):
    """Base class for icon-fetch failures."""


class OIDCIconUrlError(OIDCIconError):
    """The URL is invalid or rejected by the SSRF guard.

    Maps to a 400 Bad Request when surfaced at the API layer.
    """


class OIDCIconUnavailableError(OIDCIconError):
    """The fetch reached the upstream but the response was unusable.

    Network timeouts, non-200 status, wrong content-type, oversized payload,
    redirects (we never follow), etc.  Maps to a 400 at the API layer because
    the admin's input (the URL) is what's at fault.
    """


def _resolve_content_type(upstream_type: str, url_path: str) -> str:
    """Map an upstream Content-Type to a whitelisted MIME, or raise.

    Three-step derivation:
    1. Trust upstream ``image/*`` if it's in the allowlist.
    2. Fall back to URL extension if upstream returned
       ``application/octet-stream`` (some CDNs do this with
       ``Content-Disposition: attachment; filename="…png"``).
    3. Distinct error when the header is missing entirely (#1333 review)
       — empty quotes in a generic "unsupported content-type: ''" message
       was user-hostile.

    Extracted from ``fetch_icon`` so the dispatch logic is unit-testable
    without spinning up the streaming-mock harness.
    """
    if not upstream_type:
        raise OIDCIconUnavailableError("Icon URL response is missing a Content-Type header")
    if upstream_type in _ALLOWED_MIME_TYPES:
        return upstream_type
    if upstream_type == "application/octet-stream":
        path_lower = url_path.lower()
        for ext, mime in _EXT_TO_MIME.items():
            if path_lower.endswith(ext):
                return mime
        raise OIDCIconUnavailableError("Icon URL returned application/octet-stream with no image extension")
    raise OIDCIconUnavailableError(
        f"Icon URL returned unsupported content-type: {upstream_type!r} "
        "(allowed: image/png, image/jpeg, image/webp, image/gif)"
    )


async def fetch_icon(url: str) -> tuple[bytes, str, str]:
    """Fetch ``url`` and return ``(bytes, content_type, etag)``.

    Streams the response body and aborts as soon as ``_MAX_ICON_BYTES`` is
    exceeded — never buffers more than one chunk past the cap, so a hostile
    or misconfigured IdP serving a 500 MB payload cannot OOM the server.

    Raises:
        OIDCIconUrlError: URL parsing/scheme issue OR ``httpx.InvalidURL``
            (validator should have caught these earlier; this is a
            defence-in-depth check).
        OIDCIconUnavailableError: upstream issue — timeout, non-200,
            redirect, wrong content-type, oversized payload, empty body.
    """
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise OIDCIconUrlError(f"Invalid icon URL: {exc}") from exc

    if parsed.scheme.lower() != "https":
        # Pydantic validator + assert_safe_public_https_url catch this earlier,
        # but the service is the last defence — refuse non-HTTPS even if a
        # future code path bypassed the validators.
        raise OIDCIconUrlError("Icon URL must use https://")

    try:
        async with (
            httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SECONDS) as client,
            client.stream("GET", url, follow_redirects=False) as response,
        ):
            if response.status_code != 200:
                # Any non-200 — including 301/302 redirects (we set follow_redirects=False
                # so the SSRF guard on the original URL isn't bypassed by a redirect
                # to a private address).
                raise OIDCIconUnavailableError(
                    f"Icon URL returned HTTP {response.status_code} "
                    "(redirects are not followed; the URL must respond with the image directly)"
                )

            upstream_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            content_type = _resolve_content_type(upstream_type, parsed.path)

            # Stream with early-exit at the size cap. Read in chunks so a
            # hostile 500 MB body never gets allocated whole — we raise
            # immediately when the running total crosses the cap.
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > _MAX_ICON_BYTES:
                    raise OIDCIconUnavailableError(f"Icon exceeds {_MAX_ICON_BYTES // 1024} KB cap")
                chunks.append(chunk)
            payload = b"".join(chunks)
    except httpx.TimeoutException as exc:
        raise OIDCIconUnavailableError(f"Icon fetch timed out: {exc}") from exc
    except httpx.InvalidURL as exc:
        # ``httpx.InvalidURL`` is a sibling of ``httpx.HTTPError`` (verified:
        # MRO is ``InvalidURL → Exception``, no HTTPError in between). Fires
        # at send-time for URLs that ``urlparse`` accepts but httpx refuses —
        # typically null bytes or control chars. Map to URL-error path so
        # the admin sees a 400, not a 500.
        raise OIDCIconUrlError(f"Invalid icon URL: {exc}") from exc
    except httpx.HTTPError as exc:
        raise OIDCIconUnavailableError(f"Icon fetch failed: {exc}") from exc

    if not payload:
        raise OIDCIconUnavailableError("Icon URL returned an empty body")

    # SHA-256 hex is deterministic — identical bytes always yield the same
    # ETag so revalidation via If-None-Match works across server restarts.
    etag = hashlib.sha256(payload).hexdigest()
    return payload, content_type, etag
