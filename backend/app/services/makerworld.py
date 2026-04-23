"""MakerWorld API service.

Thin async client for MakerWorld's ``/api/v1/design-service/*`` endpoints.
Lets Bambuddy resolve a MakerWorld URL, enumerate plate/profile metadata, and
download the 3MF bundle so users can import and print MakerWorld models
without leaving the app.

The endpoints and header set were reverse-engineered from the
`kloshi-io/makerworld-api-reverse` TypeScript project (Apache-2.0) and
cross-validated against live MakerWorld traffic. Authenticated calls reuse
Bambuddy's existing Bambu Cloud bearer token (same SSO backend — no separate
OAuth flow needed).

Only interoperability — not affiliated with or endorsed by MakerWorld or
Bambu Lab, and not intended to circumvent any access control.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


# API base: ``api.bambulab.com/v1/design-service`` — the same Bambu Cloud
# backend that the MakerWorld web UI talks to, but not behind Cloudflare
# (the website ``makerworld.com`` is, and plain httpx requests there get
# fingerprinted as bot traffic and served "Please log in"). Confirmed by
# Pr0zak/YASTL#51 and verified with direct curl.
MAKERWORLD_API_BASE = "https://api.bambulab.com/v1/design-service"
MAKERWORLD_HOST = "makerworld.com"  # Used only for URL parsing (input validation)
MAKERWORLD_CDN_HOSTS = ("makerworld.bblmw.com", "public-cdn.bblmw.com")

# Hosts that the iot-service download endpoint may return presigned URLs
# for. Besides MakerWorld's own CDN, Bambu Cloud also issues AWS S3
# presigned URLs (e.g. ``s3.us-west-2.amazonaws.com``) — confirmed by
# Pr0zak/YASTL#52. The suffix check matches any regional S3 endpoint.
_ALLOWED_DOWNLOAD_SUFFIXES = (".amazonaws.com",)

# Browser-like headers. ``api.bambulab.com`` accepts minimal headers cleanly;
# the Referer is kept so MakerWorld origin checks don't fail anywhere the
# same client hits ``makerworld.com``.
_CLIENT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:147.0) Gecko/20100101 Firefox/147.0",
    "Accept": "text/html,application/json,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://makerworld.com/",
}

_MODEL_ID_RE = re.compile(r"/models/(\d+)")
_PROFILE_ID_RE = re.compile(r"#profileId[-=](\d+)")
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

_shared_http_client: httpx.AsyncClient | None = None


def set_shared_http_client(client: httpx.AsyncClient | None) -> None:
    """Register an app-scoped ``httpx.AsyncClient`` for service reuse.

    Same pattern as ``bambu_cloud.set_shared_http_client`` — lets the FastAPI
    lifespan share one connection pool across per-request service instances.
    """
    global _shared_http_client
    _shared_http_client = client


class MakerWorldError(Exception):
    """Base exception for MakerWorld API errors."""


class MakerWorldAuthError(MakerWorldError):
    """Raised when the endpoint requires a Bambu Cloud token and we don't have
    one (or the one we sent was rejected). True auth failure."""


class MakerWorldForbiddenError(MakerWorldError):
    """Raised when MakerWorld refuses access despite valid authentication —
    content-gated (points required, purchase required, region restricted,
    early-access, etc.). The message includes MakerWorld's own reason text
    when provided."""


class MakerWorldNotFoundError(MakerWorldError):
    """Raised when a design / profile / instance doesn't exist."""


class MakerWorldUnavailableError(MakerWorldError):
    """Raised on 5xx, network errors, or malformed payloads."""


class MakerWorldUrlError(MakerWorldError):
    """Raised when a URL isn't a makerworld.com model page."""


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
    from urllib.request import HTTPRedirectHandler, Request, build_opener

    # Don't follow redirects: the host allowlist above is only enforced on
    # the initial URL. A 302 from S3 to any other host would otherwise
    # transparently bypass the allowlist — so insist S3 resolve directly.
    class _NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):  # type: ignore[override]
            return None

    opener = build_opener(_NoRedirect)

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


class MakerWorldService:
    """Per-request MakerWorld API client.

    Mirrors ``BambuCloudService``'s construction pattern so callers can
    instantiate per request, reuse the shared connection pool in production,
    inject a client in tests, and close the client only if they own it.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        auth_token: str | None = None,
    ):
        if client is not None:
            self._client = client
            self._owns_client = False
        elif _shared_http_client is not None:
            self._client = _shared_http_client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(timeout=30.0)
            self._owns_client = True
        self._auth_token = auth_token

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        headers = dict(_CLIENT_HEADERS)
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        return headers

    async def _get_json(self, path: str) -> dict[str, Any]:
        """GET ``{MAKERWORLD_API_BASE}{path}`` returning the decoded JSON body.

        Raises ``MakerWorld{Auth,Forbidden,NotFound,Unavailable}Error`` based
        on status. Retries once on 418 (Cloudflare bot-detection) with a
        short backoff — that flagging is often request-scoped and clears on
        a subsequent call; hammering beyond one retry provokes a stronger
        block, so we stop there and surface a useful error.
        """
        url = f"{MAKERWORLD_API_BASE}{path}"

        for attempt in range(2):
            try:
                response = await self._client.get(url, headers=self._headers(), timeout=30.0)
            except httpx.TimeoutException as exc:
                raise MakerWorldUnavailableError(f"MakerWorld request timed out: {exc}") from exc
            except httpx.HTTPError as exc:
                raise MakerWorldUnavailableError(f"MakerWorld request failed: {exc}") from exc

            if response.status_code == 418 and attempt == 0:
                logger.info("MakerWorld returned 418 for %s; retrying once after backoff", path)
                await asyncio.sleep(1.5)
                continue
            break

        # 401: genuine auth failure — token expired, malformed, not accepted.
        # 403: MakerWorld accepted the token but refuses the specific resource
        # — usually content gating (points-redeemable, purchase-required,
        # region-restricted, early-access). These must surface differently
        # because the UI remedy is completely different: 401 → re-login,
        # 403 → user has to go to MakerWorld and meet the access requirement.
        if response.status_code == 401:
            upstream = _extract_upstream_error(response)
            raise MakerWorldAuthError(upstream or f"MakerWorld rejected the Bambu Cloud token for {path}")
        if response.status_code == 403:
            upstream = _extract_upstream_error(response)
            raise MakerWorldForbiddenError(
                upstream
                or f"MakerWorld refused access to {path} — the model may require purchase, points redemption, or be region-restricted"
            )
        if response.status_code == 404:
            raise MakerWorldNotFoundError(f"MakerWorld resource not found: {path}")
        if response.status_code == 418:
            # MakerWorld's anti-abuse layer challenges the source IP with a
            # CAPTCHA (``{"captchaId":"...","error":"We need to confirm..."}``).
            # This is application-level, not Cloudflare-edge, and clears
            # on its own within 1–4 hours of quiet traffic. There's no
            # server-side solve — CAPTCHAs are intentionally unsolvable
            # without a real browser. Surface the upstream message so the
            # user can recognise it and reach for the "Open on MakerWorld"
            # fallback instead of thinking the feature is broken.
            upstream = _extract_upstream_error(response)
            if upstream and "robot" in upstream.lower():
                raise MakerWorldUnavailableError(
                    f"MakerWorld is challenging this IP with a CAPTCHA ({upstream}). "
                    "This usually clears within a few hours. In the meantime, use "
                    "'Open on MakerWorld' below to download the 3MF manually."
                )
            raise MakerWorldUnavailableError(
                f"MakerWorld blocked the request (HTTP 418) for {path}. "
                "Try again in a few minutes, or use 'Open on MakerWorld' to import manually."
            )
        if response.status_code == 429:
            raise MakerWorldUnavailableError(
                f"MakerWorld rate-limited the request (HTTP 429) for {path}. Try again shortly."
            )
        if response.status_code >= 500:
            raise MakerWorldUnavailableError(f"MakerWorld server error (HTTP {response.status_code}) for {path}")
        if response.status_code != 200:
            raise MakerWorldUnavailableError(f"MakerWorld unexpected status {response.status_code} for {path}")

        try:
            data = response.json()
        except ValueError as exc:
            raise MakerWorldUnavailableError(f"MakerWorld returned non-JSON for {path}") from exc

        if not isinstance(data, dict):
            raise MakerWorldUnavailableError(
                f"MakerWorld returned unexpected JSON shape for {path}: {type(data).__name__}"
            )
        return data

    # ------------------------------------------------------------------ URL parse

    @staticmethod
    def parse_url(url: str) -> tuple[int, int | None]:
        """Extract ``(model_id, profile_id_or_None)`` from a MakerWorld URL.

        Accepts any of:
          - ``https://makerworld.com/en/models/1400373``
          - ``https://makerworld.com/en/models/1400373-slug-with-dashes``
          - ``https://makerworld.com/en/models/1400373#profileId-1452154``
          - ``makerworld.com/models/1400373`` (scheme optional)

        Rejects non-makerworld hosts.
        """
        if not url or not isinstance(url, str):
            raise MakerWorldUrlError("URL is empty or not a string")
        candidate = url.strip()
        if "://" not in candidate:
            candidate = "https://" + candidate
        try:
            parsed = urlparse(candidate)
        except ValueError as exc:
            raise MakerWorldUrlError(f"Could not parse URL: {exc}") from exc

        host = (parsed.hostname or "").lower()
        if host != MAKERWORLD_HOST and not host.endswith("." + MAKERWORLD_HOST):
            raise MakerWorldUrlError(f"Not a MakerWorld URL (host={host!r}); expected makerworld.com")

        model_match = _MODEL_ID_RE.search(parsed.path)
        if not model_match:
            raise MakerWorldUrlError("URL does not contain a /models/{id} segment")
        model_id = int(model_match.group(1))

        profile_id: int | None = None
        if parsed.fragment:
            profile_match = _PROFILE_ID_RE.search("#" + parsed.fragment)
            if profile_match:
                profile_id = int(profile_match.group(1))

        return model_id, profile_id

    # ---------------------------------------------------------------- endpoints

    async def get_design(self, model_id: int) -> dict[str, Any]:
        """Fetch full model metadata. Works anonymously.

        Returns the MakerWorld ``design`` object — title, summary, creator,
        license, tags, coverUrl, instances[] with profileId+cover per plate,
        categories, etc.
        """
        return await self._get_json(f"/design/{int(model_id)}")

    async def get_design_instances(self, model_id: int) -> dict[str, Any]:
        """Fetch list of profiles/instances for a model. Works anonymously.

        Returns ``{"total": N, "hits": [{id, profileId, title, cover,
        instanceCreator, instanceFilaments, needAms, ...}, ...]}``.
        """
        return await self._get_json(f"/design/{int(model_id)}/instances")

    async def get_profile(self, profile_id: int) -> dict[str, Any]:
        """Fetch a single profile's summary (designId/modelId/title/cover/
        instanceId). Works anonymously.
        """
        return await self._get_json(f"/profile/{int(profile_id)}")

    async def get_profile_download(self, profile_id: int, model_id: str) -> dict[str, Any]:
        """Fetch the signed 3MF download URL for a specific MakerWorld profile.

        Note on ``model_id`` — this is MakerWorld's internal alphanumeric
        identifier (e.g. ``"US2bb73b106683e5"``), **not** the integer
        ``designId`` that appears in the ``/models/{N}`` URL. Callers must
        fetch the design first (``get_design(design_id)``) and pass the
        ``modelId`` field from the response.


        Returns ``{"url": "https://makerworld.bblmw.com/...?at=<unix>
        &exp=<unix>&key=<hmac>&uid=<int>", ...}``. URL is short-lived (~5
        min); download immediately.

        Hits ``api.bambulab.com/v1/iot-service/api/user/profile/{profileId}
        ?model_id={modelId}`` with the stored Bambu Cloud bearer. This is the
        endpoint Pr0zak/YASTL#51 reverse-engineered — it lives on the
        ``api.bambulab.com`` backend (not Cloudflare-protected
        ``makerworld.com``), accepts the same long-lived bearer users already
        sign in with, and mints the signed CDN URL that the browser would
        otherwise fetch via session cookies. This is the only known non-
        cookie path to a download URL, after ruling out ``/design-service/``
        endpoints on ``makerworld.com`` (cookie-gated) and the now-dead
        ``/instance/{id}/f3mf?type=download`` shape.
        """
        if not self._auth_token:
            raise MakerWorldAuthError("Downloading files from MakerWorld requires a Bambu Cloud login")

        url = f"https://api.bambulab.com/v1/iot-service/api/user/profile/{int(profile_id)}"
        headers = dict(_CLIENT_HEADERS)
        headers["Authorization"] = f"Bearer {self._auth_token}"

        try:
            response = await self._client.get(
                url,
                headers=headers,
                params={"model_id": str(model_id)},
                timeout=30.0,
            )
        except httpx.TimeoutException as exc:
            raise MakerWorldUnavailableError(f"Bambu Lab API request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise MakerWorldUnavailableError(f"Bambu Lab API request failed: {exc}") from exc

        if response.status_code == 401:
            upstream = _extract_upstream_error(response)
            raise MakerWorldAuthError(
                upstream or "Bambu Lab rejected the token — sign in again in Settings → Bambu Cloud"
            )
        if response.status_code == 403:
            upstream = _extract_upstream_error(response)
            raise MakerWorldForbiddenError(upstream or f"Bambu Lab refused access to profile {profile_id}")
        if response.status_code == 404:
            raise MakerWorldNotFoundError(f"MakerWorld profile not found: {profile_id}")
        if response.status_code != 200:
            raise MakerWorldUnavailableError(
                f"Bambu Lab API unexpected status {response.status_code} for profile {profile_id}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise MakerWorldUnavailableError(f"Bambu Lab API returned non-JSON for profile {profile_id}") from exc
        if not isinstance(data, dict):
            raise MakerWorldUnavailableError(f"Bambu Lab API returned unexpected JSON shape for profile {profile_id}")
        return data

    async def download_3mf(self, signed_url: str) -> tuple[bytes, str]:
        """Fetch the 3MF bytes from a signed MakerWorld CDN URL.

        Validates that the URL's host is one of the known MakerWorld CDN hosts
        (SSRF guard — pattern matches ``_spoolman_helpers.assert_safe_spoolman_url``).
        Enforces a 200 MB cap so a single bad response can't exhaust disk.

        Returns ``(file_bytes, suggested_filename)``.
        """
        try:
            parsed = urlparse(signed_url)
        except ValueError as exc:
            raise MakerWorldUrlError(f"Invalid download URL: {exc}") from exc

        host = (parsed.hostname or "").lower()
        is_allowed = host in MAKERWORLD_CDN_HOSTS or any(host.endswith(suffix) for suffix in _ALLOWED_DOWNLOAD_SUFFIXES)
        if not is_allowed:
            raise MakerWorldUrlError(f"Refusing to download from non-MakerWorld host: {host!r}")

        # Filename fallback from the signed path (before query string)
        path_tail = parsed.path.rsplit("/", 1)[-1] or "model.3mf"

        # Presigned S3 URLs (``s3.<region>.amazonaws.com``) compute the
        # signature over exact query-string bytes. Both httpx and curl_cffi
        # re-serialize the URL through ``urllib.parse.urlencode`` which
        # normalises encodings — breaks the signature and yields HTTP 400
        # ``SignatureDoesNotMatch`` (confirmed, and matches Pr0zak/YASTL#52's
        # analysis). ``urllib.request`` transmits the URL verbatim, so we
        # use it for S3 hosts and keep httpx for MakerWorld's own CDN.
        if host.endswith(".amazonaws.com"):
            return await _download_s3_urllib(signed_url, path_tail)

        # The signed URL's query-string IS the credential — don't send the
        # Bambu Cloud bearer to the CDN too. Strips Authorization/x-bbl-* and
        # keeps only User-Agent, matching what ``_download_s3_urllib`` does.
        cdn_headers = {"User-Agent": _CLIENT_HEADERS["User-Agent"]}
        try:
            async with self._client.stream(
                "GET", signed_url, headers=cdn_headers, timeout=60.0, follow_redirects=False
            ) as response:
                if response.status_code != 200:
                    raise MakerWorldUnavailableError(f"3MF download returned HTTP {response.status_code}")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > _MAX_3MF_BYTES:
                        raise MakerWorldUnavailableError(f"3MF exceeds {_MAX_3MF_BYTES // (1024 * 1024)} MB cap")
                    chunks.append(chunk)
                return b"".join(chunks), path_tail
        except httpx.TimeoutException as exc:
            raise MakerWorldUnavailableError(f"3MF download timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise MakerWorldUnavailableError(f"3MF download failed: {exc}") from exc

    async def fetch_thumbnail(self, url: str) -> tuple[bytes, str]:
        """Fetch a MakerWorld CDN image (thumbnail / cover / plate preview).

        Used by the ``/makerworld/thumbnail`` proxy so the frontend doesn't
        have to hotlink MakerWorld's CDN directly — avoids loosening the
        SPA's ``img-src`` CSP and keeps users' IP addresses out of
        MakerWorld's access logs.

        Validates that the URL's host is one of the known MakerWorld CDN
        hosts (SSRF guard — same allowlist as :meth:`download_3mf`). Caps
        payload at 5 MB. Returns ``(bytes, content_type)``; content type
        defaults to ``image/jpeg`` if the upstream didn't set one.
        """
        try:
            parsed = urlparse(url)
        except ValueError as exc:
            raise MakerWorldUrlError(f"Invalid thumbnail URL: {exc}") from exc

        host = (parsed.hostname or "").lower()
        if host not in MAKERWORLD_CDN_HOSTS:
            raise MakerWorldUrlError(f"Refusing to fetch thumbnail from non-MakerWorld host: {host!r}")

        # ``follow_redirects=False``: the host allowlist above is only
        # meaningful on the initial URL. A 302 from the CDN to any other host
        # would otherwise be followed transparently (including RFC1918 /
        # metadata endpoints), so we insist upstream resolve the asset
        # directly. A redirect response surfaces as ``MakerWorldUnavailable``
        # below.
        try:
            response = await self._client.get(url, headers=self._headers(), timeout=20.0, follow_redirects=False)
        except httpx.TimeoutException as exc:
            raise MakerWorldUnavailableError(f"Thumbnail request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise MakerWorldUnavailableError(f"Thumbnail request failed: {exc}") from exc

        if response.status_code != 200:
            raise MakerWorldUnavailableError(f"Thumbnail fetch returned HTTP {response.status_code}")

        # MakerWorld's CDN serves real PNG/JPG files with
        # ``Content-Type: application/octet-stream`` (they use
        # ``Content-Disposition: attachment; filename="...png"`` instead). So
        # we can't just trust the header — derive the MIME from the URL's
        # file extension and only fall back to the header if the URL doesn't
        # carry one. Reject text/* / json outright regardless of extension
        # so an upstream error page can't slip through as "image/png".
        upstream_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if upstream_type in _REFUSED_THUMBNAIL_MIMES:
            raise MakerWorldUnavailableError(f"Thumbnail upstream returned non-image content-type: {upstream_type!r}")

        path_lower = parsed.path.lower()
        ext_mime: str | None = None
        for ext, mime in _IMAGE_EXT_TO_MIME.items():
            if path_lower.endswith(ext):
                ext_mime = mime
                break

        if upstream_type.startswith("image/"):
            content_type = upstream_type
        elif ext_mime is not None:
            content_type = ext_mime
        else:
            # No image extension and no image/* content-type — can't confidently
            # serve this as an image, so refuse.
            raise MakerWorldUnavailableError(
                f"Thumbnail upstream returned {upstream_type!r} and URL has no image extension"
            )

        payload = response.content
        if len(payload) > _MAX_THUMBNAIL_BYTES:
            raise MakerWorldUnavailableError(f"Thumbnail exceeds {_MAX_THUMBNAIL_BYTES // (1024 * 1024)} MB cap")
        return payload, content_type
