"""MakerWorld API service.

Thin async client for MakerWorld's ``/api/v1/design-service/*`` endpoints.
Lets Bambuddy resolve a MakerWorld URL, enumerate plate/profile metadata, and
download the 3MF bundle so users can import and print MakerWorld models
without leaving the app.

The endpoints and header set were reverse-engineered from the
`kloshi-io/makerworld-api-reverse` TypeScript project (Apache-2.0) and
cross-validated against live MakerWorld traffic. Authenticated calls reuse
Bambuddy's existing Bambu Cloud bearer token (same SSO backend — no separate
OAuth flow needed; see ``model_providers/makerworld/auth.py``).

Implements the :class:`ProviderService` interface — the route layer drives it
through ``resolve`` / ``get_download`` / ``download`` so the same flow can be
reused for future providers.

Only interoperability — not affiliated with or endorsed by MakerWorld or
Bambu Lab, and not intended to circumvent any access control.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any
from urllib.parse import urlparse

import httpx

from backend.app.services.bambu_cloud import is_captcha_challenge, is_expiry_401
from backend.app.services.model_providers.base import (
    ProviderDownload,
    ProviderDownloadInfo,
    ProviderResolvedModel,
    ProviderResourceRef,
    ProviderService,
    ProviderStatus,
)
from backend.app.services.model_providers.makerworld.auth import is_cloud_token_invalid
from backend.app.services.model_providers.makerworld.errors import (
    MakerWorldAuthError,
    MakerWorldForbiddenError,
    MakerWorldNotFoundError,
    MakerWorldUnavailableError,
    MakerWorldUrlError,
)
from backend.app.services.model_providers.makerworld.http import (
    _ALLOWED_DOWNLOAD_SUFFIXES,
    _CLIENT_HEADERS,
    _IMAGE_EXT_TO_MIME,
    _MAX_3MF_BYTES,
    _MAX_THUMBNAIL_BYTES,
    _REFUSED_THUMBNAIL_MIMES,
    MAKERWORLD_API_BASE,
    MAKERWORLD_CDN_HOSTS,
    _download_s3_urllib,
    _extract_upstream_error,
)

logger = logging.getLogger(__name__)

_shared_http_client: httpx.AsyncClient | None = None


def set_shared_http_client(client: httpx.AsyncClient | None) -> None:
    """Register an app-scoped ``httpx.AsyncClient`` for service reuse.

    Same pattern as ``bambu_cloud.set_shared_http_client`` — lets the FastAPI
    lifespan share one connection pool across per-request service instances.
    Must live in the same module as the service class so ``__init__`` reads
    the live value rather than an import-time snapshot.
    """
    global _shared_http_client
    _shared_http_client = client


# Shown whenever Bambu rejects the stored bearer. Bambu's own 401 body is
# ``{"code":4,"error":"Please login.","message":""}`` and we used to forward that
# string verbatim, which surfaced as a "Please login." toast on a UI that was
# simultaneously reporting the user as connected — maximally confusing, and it
# named no page to go to. Say what happened and where to fix it. Bambu Cloud
# sign-in lives on the Profiles page (ProfilesPage.tsx, "Cloud Profiles" tab);
# there is no Settings → Bambu Cloud page, which is what the old fallback text
# told people to look for.
_SIGN_IN_EXPIRED_MESSAGE = (
    "Your Bambu Cloud sign-in has expired. Open the Profiles page and sign in to Bambu Cloud again."
)


class MakerWorldService(ProviderService):
    """Per-request MakerWorld API client.

    Mirrors ``BambuCloudService``'s construction pattern so callers can
    instantiate per request, reuse the shared connection pool in production,
    inject a client in tests, and close the client only if they own it.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        auth_token: str | None = None,
        user: Any | None = None,
        on_auth_failure: Callable[[], Awaitable[None]] | None = None,
        thumbnail_hosts: tuple[str, ...] = MAKERWORLD_CDN_HOSTS,
    ):
        # Fired when Bambu rejects the stored token (401). MakerWorld runs on the
        # same Bambu Cloud bearer as everything else, so a rejection here means
        # the credential is dead app-wide — see ``build_authenticated_cloud``.
        self._on_auth_failure = on_auth_failure
        self._auth_failure_reported = False
        # SSRF allowlist for the thumbnail proxy. Defaults to MakerWorld's CDN
        # hosts; ``MakerWorldProvider.build_service`` passes
        # ``ModelProvider.thumbnail_hosts()`` so the guard is driven by the
        # provider descriptor rather than enforced by coincidence (interface
        # contract on ``ProviderService.fetch_thumbnail``).
        self._thumbnail_hosts = tuple(thumbnail_hosts)
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
        self._user = user

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _note_auth_failure(self, response: httpx.Response) -> None:
        """Durably record a dead credential — only for Bambu's genuine expiry 401.

        A MakerWorld 401 without the ``{"code":4,"error":"Please login."}``
        signature is endpoint- or edge-specific noise, not an expired token;
        invalidating on it would sign the user out of the whole cloud
        integration on a single stray rejection (the #2562 follow-up
        regression). Best-effort, once per service instance.
        """
        if not is_expiry_401(response):
            logger.info("MakerWorld returned 401 without the expiry signature — not signing the stored token out")
            return
        if self._on_auth_failure is None or self._auth_failure_reported:
            return
        self._auth_failure_reported = True
        try:
            await self._on_auth_failure()
        except Exception:
            logger.exception("Failed to record Bambu Cloud auth failure from MakerWorld")

    def _headers(self) -> dict[str, str]:
        headers = dict(_CLIENT_HEADERS)
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        return headers

    # ------------------------------------------------------------- interface

    async def get_status(self, db: Any) -> ProviderStatus:
        """Whether the caller can download: needs a stored, non-rejected Bambu
        Cloud token. ``auth_error`` names the expired state explicitly so the
        UI can say "your sign-in expired" rather than a bare "sign in"."""
        has_token = bool(self._auth_token)
        expired = has_token and await is_cloud_token_invalid(db, self._user)
        return ProviderStatus(
            authenticated=has_token,
            can_download=has_token and not expired,
            auth_error=_SIGN_IN_EXPIRED_MESSAGE if expired else None,
        )

    async def resolve(self, ref: ProviderResourceRef) -> ProviderResolvedModel:
        """Fetch full model metadata + the plate list, merging per-instance
        printer compatibility so the frontend can show "sliced for A1 / also
        compatible with H2D, P1S" before the user picks a plate."""
        model_id = int(ref.external_id)
        design = await self.get_design(model_id)
        instances_envelope = await self.get_design_instances(model_id)

        # MakerWorld's instances payload is ``{"total": N, "hits": [...]}``;
        # normalise the null case to an empty list so the frontend doesn't
        # have to handle null vs [] both ways.
        instances = instances_envelope.get("hits") or []
        if not isinstance(instances, list):
            instances = []

        # /instances/hits omits the per-instance printer compatibility info
        # that /design.instances[].extention.modelInfo carries. Merge it in.
        design_instances = design.get("instances") or []
        if isinstance(design_instances, list):
            compat_by_id = {}
            for di in design_instances:
                if not isinstance(di, dict):
                    continue
                iid = di.get("id")
                if iid is None:
                    continue
                ext = (di.get("extention") or {}).get("modelInfo") or {}
                compat_by_id[iid] = {
                    "compatibility": ext.get("compatibility"),
                    "otherCompatibility": ext.get("otherCompatibility"),
                }
            for inst in instances:
                if not isinstance(inst, dict):
                    continue
                iid = inst.get("id")
                extra = compat_by_id.get(iid)
                if extra:
                    inst["compatibility"] = extra["compatibility"]
                    inst["otherCompatibility"] = extra["otherCompatibility"]

        return ProviderResolvedModel(ref=ref, design=design, instances=instances)

    async def get_download(self, ref: ProviderResourceRef) -> ProviderDownloadInfo:
        """Resolve the signed 3MF download for a specific MakerWorld profile.

        Handles the provider-specific dance: the iot-service endpoint needs
        the *alphanumeric* ``modelId`` (e.g. ``"US2bb73b106683e5"``) from the
        design, not the integer design id, and picks a default profile when
        the caller didn't specify one. Enriches ``ref.sub_id`` with the actual
        profile used so the route can build the per-plate dedupe key.
        """
        model_id = int(ref.external_id)
        design = await self.get_design(model_id)

        alphanumeric_model_id = design.get("modelId")
        if not isinstance(alphanumeric_model_id, str) or not alphanumeric_model_id:
            raise MakerWorldUnavailableError("MakerWorld design metadata missing the modelId field")

        profile_id = int(ref.sub_id) if ref.sub_id else None
        if profile_id is None:
            for instance in design.get("instances") or []:
                pid = instance.get("profileId")
                if isinstance(pid, int) and pid > 0:
                    profile_id = pid
                    break
            if profile_id is None:
                envelope = await self.get_design_instances(model_id)
                for hit in envelope.get("hits") or []:
                    pid = hit.get("profileId")
                    if isinstance(pid, int) and pid > 0:
                        profile_id = pid
                        break
            if profile_id is None:
                raise MakerWorldUnavailableError("MakerWorld returned no instances for this model")

        manifest = await self.get_profile_download(profile_id, alphanumeric_model_id)

        signed_url = manifest.get("url")
        if not signed_url or not isinstance(signed_url, str):
            raise MakerWorldUnavailableError("MakerWorld did not return a download URL")

        # Raw upstream name — the route layer basenames / percent-decodes it
        # as defence-in-depth before persisting.
        raw_name = manifest.get("name")
        suggested_filename = raw_name if isinstance(raw_name, str) and raw_name.strip() else ""

        return ProviderDownloadInfo(
            ref=replace(ref, sub_id=str(profile_id)),
            url=signed_url,
            suggested_filename=suggested_filename,
        )

    async def download(self, info: ProviderDownloadInfo) -> ProviderDownload:
        """Fetch the 3MF bytes for a signed URL, returning ``(bytes, filename)``."""
        file_bytes, download_filename = await self.download_3mf(info.url)
        return ProviderDownload(file_bytes=file_bytes, filename=download_filename)

    # ---------------------------------------------------------------- endpoints

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
            if self._auth_token:
                # We sent a token and Bambu refused it — the credential is dead,
                # not merely absent. Record that before raising so the rest of the
                # app stops claiming the user is connected.
                await self._note_auth_failure(response)
                raise MakerWorldAuthError(_SIGN_IN_EXPIRED_MESSAGE)
            raise MakerWorldAuthError(f"Signing in to Bambu Cloud is required for {path}")
        if response.status_code == 403:
            upstream = _extract_upstream_error(response)
            raise MakerWorldForbiddenError(
                upstream
                or f"MakerWorld refused access to {path} — the model may require purchase, points redemption, or be region-restricted"
            )
        if response.status_code == 404:
            raise MakerWorldNotFoundError(f"MakerWorld resource not found: {path}")
        if response.status_code == 418:
            # Bambu's anti-abuse layer challenges the source IP with a CAPTCHA
            # (``{"captchaId":"...","error":"We need to confirm..."}``). This is
            # application-level, not Cloudflare-edge, and clears on its own
            # within 1–4 hours of quiet traffic. There's no server-side solve —
            # CAPTCHAs are intentionally unsolvable without a real browser.
            # Surface the upstream message so the user can recognise it and
            # reach for the "Open on MakerWorld" fallback instead of thinking
            # the feature is broken.
            #
            # The same challenge also lands on the Bambu Cloud sign-in endpoint,
            # so the shape test lives in ``bambu_cloud`` and is shared (#2790).
            # It used to be a bare "robot" substring check on the error text,
            # which missed a challenge worded any other way.
            if is_captcha_challenge(response):
                upstream = _extract_upstream_error(response)
                detail = f" ({upstream})" if upstream else ""
                raise MakerWorldUnavailableError(
                    f"MakerWorld is challenging this IP with a CAPTCHA{detail}. "
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
            await self._note_auth_failure(response)
            raise MakerWorldAuthError(_SIGN_IN_EXPIRED_MESSAGE)
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
        if host not in self._thumbnail_hosts:
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
