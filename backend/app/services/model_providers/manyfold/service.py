"""Manyfold API client (#1471).

Talks to a self-hosted Manyfold install through its v0 API: list and search
models, read a model's files, download one file and fetch a model's preview
image. Bambuddy signs in with an OAuth application's client ID and secret
(the client-credentials flow) and asks only for the ``public read`` scopes,
which cover everything here; it never writes to Manyfold.

Manyfold limits the token endpoint to 10 requests in 3 minutes, so the token
is cached until shortly before it expires (two hours by default) and shared
by every request.

Request URLs are always built from the configured base URL and validated ids.
The ``@id`` and ``contentUrl`` links in Manyfold's responses are read for the
ids and the file name they carry, never fetched as given.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from backend.app.api.routes._url_safety import assert_safe_lan_service_url
from backend.app.services.model_providers.base import (
    ProviderAuthError,
    ProviderDownload,
    ProviderDownloadInfo,
    ProviderError,
    ProviderForbiddenError,
    ProviderNotFoundError,
    ProviderResolvedModel,
    ProviderResourceRef,
    ProviderService,
    ProviderStatus,
    ProviderUnavailableError,
)
from backend.app.services.model_providers.manyfold.config import ManyfoldConfig

logger = logging.getLogger(__name__)

API_MEDIA_TYPE = "application/vnd.manyfold.v0+json"
SCOPES = "public read"

# Manyfold ids are short public ids such as "x7q3k2pa".
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MODEL_ID_IN_URL = re.compile(r"/models/([A-Za-z0-9_-]{1,64})(?:/|$)")
_FILE_ID_IN_URL = re.compile(r"/model_files/([A-Za-z0-9_-]{1,64})(?:[/.?]|$)")

# What Bambuddy can slice or print, by Manyfold's MIME type and by extension.
IMPORTABLE_MIME_TYPES = frozenset({"model/3mf", "model/stl", "model/step"})
IMPORTABLE_EXTENSIONS = (".3mf", ".stl", ".step", ".stp")

MAX_FILE_BYTES = 200 * 1024 * 1024
_MAX_REDIRECTS = 5
MAX_PREVIEW_BYTES = 10 * 1024 * 1024

# Renew this long before Manyfold's own expiry, so a request never starts
# with a token that dies on the way.
_TOKEN_MARGIN_SECONDS = 120

_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

# (base url, client id, secret digest) -> (token, monotonic expiry)
_token_cache: dict[tuple[str, str, str], tuple[str, float]] = {}
# Credentials Manyfold just refused, and until when that answer is reused.
# Without it, every request of a page or a bulk import would try to sign in
# again with the same wrong secret and use up Manyfold's 10 sign-ins in 3
# minutes, so the corrected secret would then be refused as well.
_refused: dict[tuple[str, str, str], tuple[ProviderError, float]] = {}
_REFUSED_FOR_SECONDS = 30.0
# One sign-in at a time, so a page of previews opening at once asks for one
# token rather than a dozen. Bound to the running loop, recreated if it changes.
_token_lock: asyncio.Lock | None = None
_token_lock_loop: asyncio.AbstractEventLoop | None = None


def _sign_in_lock() -> asyncio.Lock:
    global _token_lock, _token_lock_loop
    loop = asyncio.get_running_loop()
    if _token_lock is None or _token_lock_loop is not loop:
        _token_lock = asyncio.Lock()
        _token_lock_loop = loop
    return _token_lock


class ManyfoldError(ProviderError):
    """Base exception for Manyfold API errors.

    ``code`` names the failure for the frontend, which shows its own
    translated text for it; the message is the English fallback.
    """

    default_code = "manyfold_failed"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code or self.default_code


class ManyfoldAuthError(ProviderAuthError, ManyfoldError):
    """Manyfold refused the configured credentials, or none are configured."""

    default_code = "manyfold_credentials"


class ManyfoldForbiddenError(ProviderForbiddenError, ManyfoldError):
    """Manyfold accepted the token but refused this request."""

    default_code = "manyfold_forbidden"


class ManyfoldNotFoundError(ProviderNotFoundError, ManyfoldError):
    """The model or file doesn't exist, or isn't visible to the application's owner."""

    default_code = "manyfold_not_found"


class ManyfoldUnavailableError(ProviderUnavailableError, ManyfoldError):
    """Manyfold is unreachable, failing, or sent something unexpected."""

    default_code = "manyfold_failed"


def clear_token_cache() -> None:
    """Forget cached tokens and refusals, e.g. after the credentials were changed."""
    _token_cache.clear()
    _refused.clear()


def valid_id(value: str) -> str:
    """Return ``value`` if it is a well-formed Manyfold id, else raise."""
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ManyfoldNotFoundError("Not a valid Manyfold id")
    return value


def _id_from(url: Any, pattern: re.Pattern[str]) -> str | None:
    if not isinstance(url, str):
        return None
    match = pattern.search(urlsplit(url).path)
    return match.group(1) if match else None


def _image_type(head: bytes) -> str | None:
    for signature, mime in _IMAGE_SIGNATURES:
        if head.startswith(signature):
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def is_importable_filename(filename: str) -> bool:
    return filename.lower().endswith(IMPORTABLE_EXTENSIONS)


class ManyfoldService(ProviderService):
    """Per-request Manyfold client."""

    def __init__(self, config: ManyfoldConfig, *, client: httpx.AsyncClient | None = None):
        self._config = config
        self._base = config.url.rstrip("/")
        self._host = (urlsplit(self._base).hostname or "").lower()
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(timeout=30.0)
            self._owns_client = True

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ---- sign-in -------------------------------------------------------

    def _cache_key(self) -> tuple[str, str, str]:
        digest = hashlib.sha256(self._config.client_secret.encode()).hexdigest()
        return (self._base, self._config.client_id, digest)

    async def _token(self, *, renew: bool = False) -> str:
        if not self._config.configured:
            raise ManyfoldAuthError(
                "Manyfold is not set up. Enter its URL, client ID and secret first.", "manyfold_not_configured"
            )
        key = self._cache_key()
        async with _sign_in_lock():
            cached = _token_cache.get(key)
            if cached and not renew and cached[1] > time.monotonic():
                return cached[0]
            refused = _refused.get(key)
            if refused and refused[1] > time.monotonic():
                earlier = refused[0]
                raise type(earlier)(str(earlier), getattr(earlier, "code", None))
            try:
                return await self._sign_in(key)
            except ManyfoldAuthError as exc:
                _refused[key] = (exc, time.monotonic() + _REFUSED_FOR_SECONDS)
                raise

    async def _sign_in(self, key: tuple[str, str, str]) -> str:
        """Ask Manyfold for a token; the caller holds the sign-in lock."""
        try:
            response = await self._client.post(
                f"{self._base}/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._config.client_id,
                    "client_secret": self._config.client_secret,
                    "scope": SCOPES,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise ManyfoldUnavailableError(
                f"Could not reach Manyfold at {self._base}: {exc}", "manyfold_unreachable"
            ) from exc
        if response.status_code == 429:
            raise ManyfoldUnavailableError(
                "Manyfold is limiting sign-ins. Try again in a few minutes.", "manyfold_rate_limited"
            )
        body = self._json_or_none(response)
        error = body.get("error") if isinstance(body, dict) else None
        if error == "invalid_scope":
            raise ManyfoldAuthError(
                "The Manyfold application lacks the 'read' scope. Edit it in Manyfold under Settings -> API.",
                "manyfold_scope",
            )
        if response.status_code in (400, 401) or error in ("invalid_client", "unauthorized_client"):
            raise ManyfoldAuthError("Manyfold did not accept the client ID or secret.")
        if response.status_code != 200 or not isinstance(body, dict):
            raise ManyfoldUnavailableError(f"Manyfold sign-in failed with HTTP {response.status_code}")
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise ManyfoldUnavailableError("Manyfold sign-in returned no token")
        granted = str(body.get("scope") or "").split()
        if granted and "read" not in granted:
            raise ManyfoldAuthError(
                "The Manyfold application lacks the 'read' scope. Edit it in Manyfold under Settings -> API.",
                "manyfold_scope",
            )
        try:
            lifetime = float(body.get("expires_in") or 7200)
        except (TypeError, ValueError):
            lifetime = 7200.0
        _token_cache[key] = (token, time.monotonic() + max(0.0, lifetime - _TOKEN_MARGIN_SECONDS))
        return token

    @staticmethod
    def _json_or_none(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None

    # ---- requests ------------------------------------------------------

    def _raise_for(self, response: httpx.Response, what: str) -> None:
        status = response.status_code
        if status == 401:
            raise ManyfoldAuthError("Manyfold did not accept Bambuddy's sign-in.")
        if status == 403:
            raise ManyfoldForbiddenError(
                f"Manyfold refused access to {what}. Check that the application has the 'read' scope "
                "and that its owner can see this model."
            )
        if status == 404:
            raise ManyfoldNotFoundError(f"Manyfold could not find {what}")
        if status >= 400:
            raise ManyfoldUnavailableError(f"Manyfold answered HTTP {status} for {what}")

    async def _send(
        self,
        url: str,
        *,
        accept: str | None = None,
        params: dict[str, Any] | None = None,
        follow_redirects: bool = False,
    ) -> httpx.Response:
        """GET with the token, renewing it once if Manyfold refuses it.

        The response is streamed; the caller reads and closes it.

        Redirects (Manyfold may hand files over to object storage) are
        followed here rather than by httpx, so every hop passes the same
        LAN-service check as the configured URL, and the token is only sent
        to Manyfold's own host.
        """
        headers = {"Accept": accept} if accept else {}
        for attempt in range(2):
            headers["Authorization"] = f"Bearer {await self._token(renew=attempt > 0)}"
            try:
                request = self._client.build_request("GET", url, headers=headers, params=params)
                response = await self._client.send(request, stream=True)
                hops = 0
                while follow_redirects and response.is_redirect and response.next_request is not None:
                    hops += 1
                    following = response.next_request
                    await response.aclose()
                    if hops > _MAX_REDIRECTS:
                        raise ManyfoldUnavailableError("Manyfold redirected too often")
                    try:
                        assert_safe_lan_service_url(str(following.url), label="Manyfold redirect")
                    except ValueError as exc:
                        raise ManyfoldUnavailableError(f"Refusing Manyfold's redirect: {exc}") from exc
                    if (following.url.host or "").lower() != self._host:
                        following.headers.pop("Authorization", None)
                    response = await self._client.send(following, stream=True)
            except httpx.HTTPError as exc:
                raise ManyfoldUnavailableError(f"Could not reach Manyfold: {exc}", "manyfold_unreachable") from exc
            # Only Manyfold's own 401 means the token was refused; object
            # storage behind a redirect never saw it.
            if response.status_code == 401 and attempt == 0 and (response.url.host or "").lower() == self._host:
                await response.aclose()
                continue
            return response
        return response  # pragma: no cover - the loop always returns

    async def _get_json(self, path: str, what: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._send(f"{self._base}{path}", accept=API_MEDIA_TYPE, params=params)
        try:
            await response.aread()
        finally:
            await response.aclose()
        self._raise_for(response, what)
        body = self._json_or_none(response)
        if not isinstance(body, dict):
            raise ManyfoldUnavailableError(f"Manyfold sent an unexpected answer for {what}")
        return body

    async def _read_capped(self, response: httpx.Response, cap: int, what: str) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > cap:
                raise ManyfoldUnavailableError(f"{what} is larger than {cap // (1024 * 1024)} MB", "manyfold_too_large")
            chunks.append(chunk)
        return b"".join(chunks)

    # ---- models --------------------------------------------------------

    async def list_models(self, *, query: str = "", page: int = 1) -> dict[str, Any]:
        params: dict[str, Any] = {"page": max(1, page)}
        if query.strip():
            params["q"] = query.strip()
        body = await self._get_json("/models", "the model list", params)
        members = body.get("member") if isinstance(body.get("member"), list) else []
        models = []
        for member in members:
            if not isinstance(member, dict):
                continue
            model_id = _id_from(member.get("@id"), _MODEL_ID_IN_URL)
            if model_id:
                models.append({"id": model_id, "name": str(member.get("name") or model_id)})
        view = body.get("view") if isinstance(body.get("view"), dict) else {}
        total = body.get("totalItems")
        return {
            "total": total if isinstance(total, int) else len(models),
            "page": params["page"],
            "has_next": bool(view.get("next")),
            "has_previous": bool(view.get("previous")),
            "models": models,
        }

    async def get_model(self, model_id: str) -> dict[str, Any]:
        model_id = valid_id(model_id)
        body = await self._get_json(f"/models/{model_id}", "this model")
        files = []
        for part in body.get("hasPart") or []:
            if not isinstance(part, dict):
                continue
            file_id = _id_from(part.get("@id"), _FILE_ID_IN_URL)
            if not file_id:
                continue
            mime = str(part.get("encodingFormat") or "")
            files.append(
                {
                    "id": file_id,
                    "name": str(part.get("name") or file_id),
                    "mime": mime,
                    "importable": mime in IMPORTABLE_MIME_TYPES,
                }
            )
        license_info = body.get("spdx:license")
        keywords = body.get("keywords")
        preview = body.get("preview_file")
        return {
            "id": model_id,
            "name": str(body.get("name") or model_id),
            "caption": body.get("caption") if isinstance(body.get("caption"), str) else None,
            "description": body.get("description") if isinstance(body.get("description"), str) else None,
            "license": license_info.get("licenseId") if isinstance(license_info, dict) else None,
            "tags": [str(tag) for tag in keywords] if isinstance(keywords, list) else [],
            "url": f"{self._base}/models/{model_id}",
            "preview_file_id": _id_from(preview.get("@id"), _FILE_ID_IN_URL) if isinstance(preview, dict) else None,
            "files": files,
        }

    async def get_file(self, model_id: str, file_id: str) -> dict[str, Any]:
        """One file's details, with the raw-download path Manyfold gives for it."""
        model_id, file_id = valid_id(model_id), valid_id(file_id)
        body = await self._get_json(f"/models/{model_id}/model_files/{file_id}", "this file")
        content_path = urlsplit(str(body.get("contentUrl") or "")).path
        marker = f"/models/{model_id}/raw/"
        position = content_path.find(marker)
        tail = content_path[position + len(marker) :] if position >= 0 else ""
        segments = unquote(tail).split("/")
        if not tail or any(segment in ("", ".", "..") for segment in segments):
            raise ManyfoldUnavailableError("Manyfold sent no usable download link for this file")
        size = body.get("contentSize")
        return {
            "id": file_id,
            "model_id": model_id,
            "name": str(body.get("name") or file_id),
            "filename": os.path.basename(unquote(tail)),
            "mime": str(body.get("encodingFormat") or ""),
            "size": size if isinstance(size, int) else None,
            "raw_path": f"/models/{model_id}/raw/{tail}",
        }

    async def check(self) -> int:
        """Sign in and read the first page of models; returns the model count."""
        return int((await self.list_models())["total"])

    # ---- files and previews ------------------------------------------

    async def download_file(self, file: dict[str, Any]) -> bytes:
        response = await self._send(f"{self._base}{file['raw_path']}", follow_redirects=True)
        try:
            self._raise_for(response, "this file")
            return await self._read_capped(response, MAX_FILE_BYTES, "The file")
        finally:
            await response.aclose()

    async def fetch_preview(self, model_id: str) -> tuple[bytes, str]:
        """The model's preview as an image, from Manyfold's own derivatives.

        An image preview comes as Manyfold's small "preview" copy, a 3D file
        as its rendered picture when Manyfold made one. Without a picture
        Manyfold sends the original file instead, so anything that doesn't
        start like an image is refused after its first bytes.
        """
        model = await self.get_model(model_id)
        file_id = model["preview_file_id"]
        if not file_id:
            raise ManyfoldNotFoundError("This model has no preview")
        file = await self.get_file(model["id"], file_id)
        extension = os.path.splitext(file["filename"])[1].lower()
        mime = file["mime"]
        if mime.startswith("image/"):
            derivative = "preview"
        elif mime.startswith("model/"):
            derivative = "render"
        else:
            raise ManyfoldNotFoundError("This model has no preview")
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", extension):
            raise ManyfoldNotFoundError("This model has no preview")
        response = await self._send(
            f"{self._base}/models/{model['id']}/model_files/{file_id}{extension}",
            params={"derivative": derivative},
            follow_redirects=True,
        )
        try:
            self._raise_for(response, "the preview")
            data = bytearray()
            content_type: str | None = None
            async for chunk in response.aiter_bytes():
                data += chunk
                if content_type is None and len(data) >= 12:
                    content_type = _image_type(bytes(data[:12]))
                    if content_type is None:
                        raise ManyfoldNotFoundError("This model has no preview")
                if len(data) > MAX_PREVIEW_BYTES:
                    raise ManyfoldNotFoundError("This model's preview is too large")
            content_type = content_type or _image_type(bytes(data[:12]))
            if content_type is None:
                raise ManyfoldNotFoundError("This model has no preview")
            return bytes(data), content_type
        finally:
            await response.aclose()

    # ---- ProviderService -----------------------------------------------

    async def get_status(self, db) -> ProviderStatus:
        configured = self._config.configured
        return ProviderStatus(authenticated=configured, can_download=configured)

    async def resolve(self, ref: ProviderResourceRef) -> ProviderResolvedModel:
        model = await self.get_model(ref.external_id)
        return ProviderResolvedModel(ref=ref, design=model, instances=model["files"])

    async def get_download(self, ref: ProviderResourceRef) -> ProviderDownloadInfo:
        if not ref.sub_id:
            raise ManyfoldNotFoundError("Name the file to download")
        file = await self.get_file(ref.external_id, ref.sub_id)
        return ProviderDownloadInfo(ref=ref, url=f"{self._base}{file['raw_path']}", suggested_filename=file["filename"])

    async def download(self, info: ProviderDownloadInfo) -> ProviderDownload:
        if (urlsplit(info.url).hostname or "").lower() != self._host or not info.url.startswith(self._base + "/"):
            raise ManyfoldUnavailableError("Refusing to download from outside the Manyfold install")
        data = await self.download_file({"raw_path": info.url[len(self._base) :]})
        return ProviderDownload(file_bytes=data, filename=info.suggested_filename)

    async def fetch_thumbnail(self, url: str) -> tuple[bytes, str]:
        model_id = _id_from(url, _MODEL_ID_IN_URL)
        if (urlsplit(url).hostname or "").lower() != self._host or not model_id:
            raise ManyfoldUnavailableError("Refusing to fetch an image from outside the Manyfold install")
        return await self.fetch_preview(model_id)
