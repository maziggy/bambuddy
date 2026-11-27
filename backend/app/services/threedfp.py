"""3D Filament Profiles client (#2981).

Connecting uses the OAuth 2.0 device authorization grant (RFC 8628) with the
public client ``bambuddy``: no secret, no redirect URL. Bambuddy asks for a
device code, the user approves its ``user_code`` on 3dfilamentprofiles.com, and
Bambuddy polls the token endpoint until the token arrives. The token never
expires on a timer and there is no refresh token; it stops working when the
user disconnects it or leaves it unused for 90 days.

What a token can do depends on the account's plan. Every account can look up a
filament by barcode or by id (a free account 100 requests a month); spool sync
needs an account with API access. ``GET /me`` reports the plan, the scopes that
work right now and the quota, and costs nothing.

Two error shapes: the OAuth endpoints answer the RFC form ``{"error": "code"}``,
every other endpoint ``{"error": {"code": ..., "message": ...}}``.

Set ``THREEDFP_BASE_URL`` to point Bambuddy at a test server.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from backend.app.core.config import APP_VERSION

logger = logging.getLogger(__name__)

THREEDFP_API_BASE = os.environ.get("THREEDFP_BASE_URL", "https://3dfilamentprofiles.com/api/v1").rstrip("/")
CLIENT_ID = "bambuddy"
# All three, whatever the plan: a free account that later gets API access can
# then sync without connecting again. What works now is /me's active_scopes.
SCOPE = "filaments:read spools:read spools:write"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
USER_AGENT = f"Bambuddy/{APP_VERSION}"

# A filament record changes rarely, and a free account has 100 requests a
# month, so a record is kept for a day (3DFP's own suggestion).
FILAMENT_CACHE_TTL = 24 * 3600
_FILAMENT_CACHE_MAX = 500
_filament_cache: dict[int, tuple[float, dict[str, Any]]] = {}


class DevicePoll:
    """Outcomes of one token poll. PENDING and SLOW_DOWN mean poll again (after
    widening the interval by 5 s on SLOW_DOWN); DENIED and EXPIRED end the
    attempt."""

    PENDING = "authorization_pending"
    SLOW_DOWN = "slow_down"
    DENIED = "access_denied"
    EXPIRED = "expired_token"
    COMPLETE = "complete"

    ONGOING = frozenset({PENDING, SLOW_DOWN})
    TERMINAL = frozenset({DENIED, EXPIRED})


class ThreeDfpError(Exception):
    """A failed call. ``status`` is the HTTP status (0 when the request never got
    an answer) and ``code`` the error code 3DFP gave, if any."""

    def __init__(self, message: str, status: int = 0, code: str | None = None, retry_after: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.retry_after = retry_after

    @property
    def token_is_dead(self) -> bool:
        """True only for 3DFP's own answer that the token is gone (disconnected,
        unused for 90 days, or wrong). Any other 401 -- a proxy, a blip -- is
        not reason enough to forget a working connection (#2562)."""
        return self.status == 401 and self.code == "invalid_key"


_shared_http_client: httpx.AsyncClient | None = None


def set_shared_http_client(client: httpx.AsyncClient | None) -> None:
    """Register the app's shared ``httpx.AsyncClient`` (see main.py)."""
    global _shared_http_client
    _shared_http_client = client


def _oauth_error(resp: httpx.Response) -> str | None:
    try:
        body = resp.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, str) else None


def _api_error(resp: httpx.Response) -> ThreeDfpError:
    code = message = None
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        code = body["error"].get("code")
        message = body["error"].get("message")
    return ThreeDfpError(
        f"3D Filament Profiles answered {resp.status_code}: {message or code or resp.reason_phrase}",
        status=resp.status_code,
        code=code if isinstance(code, str) else None,
        retry_after=resp.headers.get("Retry-After"),
    )


class ThreeDfpClient:
    """One request's client. ``token`` is the stored token, or None while
    connecting."""

    def __init__(self, token: str | None = None, client: httpx.AsyncClient | None = None):
        self.token = token
        if client is not None:
            self._client, self._owns_client = client, False
        elif _shared_http_client is not None:
            self._client, self._owns_client = _shared_http_client, False
        else:
            self._client, self._owns_client = httpx.AsyncClient(timeout=15.0), True

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> ThreeDfpClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def _post_form(self, path: str, form: dict[str, str]) -> httpx.Response:
        try:
            return await self._client.post(
                f"{THREEDFP_API_BASE}{path}", data=form, headers={"User-Agent": USER_AGENT}, timeout=15.0
            )
        except httpx.HTTPError as e:
            raise ThreeDfpError(f"Could not reach 3D Filament Profiles: {e}") from e

    # --- Connecting --------------------------------------------------------

    async def request_device_code(self, device_name: str) -> dict[str, Any]:
        """Start connecting. Returns device_code (keep it server-side), user_code,
        verification_uri, verification_uri_complete, expires_in and interval."""
        resp = await self._post_form(
            "/oauth/device/code", {"client_id": CLIENT_ID, "scope": SCOPE, "device_name": device_name[:100]}
        )
        if resp.status_code >= 400:
            raise ThreeDfpError(
                f"3D Filament Profiles refused to start connecting ({resp.status_code}): {_oauth_error(resp)}",
                status=resp.status_code,
                code=_oauth_error(resp),
            )
        data = resp.json()
        if not isinstance(data, dict) or not data.get("device_code") or not data.get("user_code"):
            raise ThreeDfpError("3D Filament Profiles returned an incomplete device code")
        return data

    async def poll_token(self, device_code: str) -> str:
        """Poll the token endpoint once. Returns a :class:`DevicePoll` value and
        sets ``self.token`` on COMPLETE. A reply that is none of the RFC answers
        raises, rather than ending a pairing the user may still approve."""
        resp = await self._post_form(
            "/oauth/token", {"grant_type": DEVICE_GRANT, "device_code": device_code, "client_id": CLIENT_ID}
        )
        if resp.status_code == 200:
            token = resp.json().get("access_token")
            if not isinstance(token, str) or not token:
                raise ThreeDfpError("3D Filament Profiles returned no access token")
            self.token = token
            return DevicePoll.COMPLETE
        error = _oauth_error(resp)
        if resp.status_code == 400 and error in (*DevicePoll.ONGOING, *DevicePoll.TERMINAL):
            return error
        raise ThreeDfpError(f"Token poll failed ({resp.status_code}): {error}", status=resp.status_code, code=error)

    async def revoke(self) -> None:
        """Disconnect the token on 3DFP's side. Best effort: it always answers
        200, and a token Bambuddy forgets is unusable to it either way."""
        if not self.token:
            return
        try:
            await self._post_form("/oauth/revoke", {"token": self.token, "client_id": CLIENT_ID})
        except ThreeDfpError as e:
            logger.info("Could not revoke the 3D Filament Profiles token: %s", e)

    # --- API ---------------------------------------------------------------

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self.token:
            raise ThreeDfpError("Not connected to 3D Filament Profiles", status=401, code="invalid_key")
        try:
            resp = await self._client.get(
                f"{THREEDFP_API_BASE}{path}",
                params=params,
                headers={
                    "User-Agent": USER_AGENT,
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/json",
                },
                timeout=15.0,
            )
        except httpx.HTTPError as e:
            raise ThreeDfpError(f"Could not reach 3D Filament Profiles: {e}") from e
        if resp.status_code >= 400:
            raise _api_error(resp)
        return resp.json()

    async def me(self) -> dict[str, Any]:
        """The account, plan, active scopes, quota and upgrade link. Free of quota."""
        return await self._get("/me")

    async def lookup_gtin(self, gtin: str) -> list[dict[str, Any]]:
        """Filament summaries matching a barcode (8 to 14 digits)."""
        body = await self._get("/filaments", {"gtin": gtin})
        data = body.get("data") if isinstance(body, dict) else None
        return [f for f in data if isinstance(f, dict)] if isinstance(data, list) else []

    async def get_filament(self, filament_id: int) -> dict[str, Any]:
        """The full filament record with temperatures, weights and store links.

        Cached for a day, shared by every user: it is the public catalogue entry,
        the same whoever asks. A merged filament comes back under its surviving
        id, cached under both.
        """
        now = time.monotonic()
        hit = _filament_cache.get(filament_id)
        if hit and hit[0] > now:
            return hit[1]
        record = await self._get(f"/filaments/{filament_id}")
        if not isinstance(record, dict) or not isinstance(record.get("id"), int):
            raise ThreeDfpError("3D Filament Profiles returned a malformed filament")
        if len(_filament_cache) >= _FILAMENT_CACHE_MAX:
            for key in sorted(_filament_cache, key=lambda k: _filament_cache[k][0])[: _FILAMENT_CACHE_MAX // 10]:
                _filament_cache.pop(key, None)
        expires = now + FILAMENT_CACHE_TTL
        _filament_cache[filament_id] = (expires, record)
        _filament_cache[record["id"]] = (expires, record)
        return record


def clear_filament_cache() -> None:
    _filament_cache.clear()
