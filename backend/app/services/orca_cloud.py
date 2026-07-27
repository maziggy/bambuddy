"""
Orca Cloud API Service

Handles pairing and profile sync with the Orca Cloud external-app surface.

Auth shape: OAuth 2.0 Device Authorization Grant (RFC 8628). Bambuddy is a
public client (``client_id`` only, no secret) — there is no redirect URL, so
the flow works from a LAN IP, ``localhost``, or behind a reverse proxy. The
user approves a short ``user_code`` in their Orca Cloud settings; Bambuddy
polls the token endpoint until a token pair is issued.

    POST /oauth/device/code   -> {device_code, user_code, verification_uri,
                                  verification_uri_complete, expires_in, interval}
    POST /oauth/token         -> poll with grant_type=device_code, then later
                                  refresh with grant_type=refresh_token

Token shape: opaque ``oc_ext_`` access token (24h) + single-use rotating
``oc_ext_rt_`` refresh token (90-day, renewed on each rotation). Reuse of a
consumed refresh token beyond a ~60s server-side grace window revokes the
whole pairing, so the route layer MUST persist the new pair atomically with
consuming the old one. Within the grace window a lost refresh race is a no-op
(each racer gets its own fresh pair), so single-flighting is hygiene, not a
correctness requirement.

API surface: ``oc_ext_`` tokens authorize ONLY the ``/api/v1/external/*``
endpoints (introspection + ``/external/sync/*``). The first-party
``/api/v1/sync/*`` surface used by the old Supabase flow is NOT reachable with
these tokens.

Cloudflare fronts ``api.orcaslicer.com`` and blocks unusual User-Agents
(``python-urllib`` gets a ``403 "error code: 1010"``); an honest
``Bambuddy/<version>`` UA clears it. No TLS-fingerprint matching needed.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Endpoints + client identity (env-overridable so staging can be targeted
# without a code change). Defaults point at production.
# ---------------------------------------------------------------------------

_DEFAULT_API_BASE = "https://api.orcaslicer.com"

# Base for both the OAuth endpoints (/oauth/*) and the external API
# (/api/v1/external/*). Override with ORCA_CLOUD_API_BASE to point at
# staging (https://staging-api.orcaslicer.com) during testing.
ORCA_API_BASE = os.environ.get("ORCA_CLOUD_API_BASE", _DEFAULT_API_BASE).rstrip("/")

# Public client id registered with the Orca Cloud team (see the External App
# Pairing developer guide). Not a secret — it appears in browser-visible
# requests — but it must accompany every /oauth/device/code and /oauth/token
# call (incl. refreshes) or the server returns ``invalid_client``. Overridable
# only for the (unlikely) case of a separate staging registration.
ORCA_CLIENT_ID = os.environ.get("ORCA_CLOUD_CLIENT_ID", "oc_app_e873d49ce7dbcc7dca8ba386")

# Scope requested at pairing time. Bambuddy currently only READS the user's
# Orca Cloud profiles (list + view), so we request the minimum — read-only.
# ``sync:read`` grants pull + versions; bump to ``sync:write`` here if/when a
# push-to-cloud feature lands (which forces existing users to re-pair, since
# the granted scope is baked into the issued token).
ORCA_SCOPE = os.environ.get("ORCA_CLOUD_SCOPE", "sync:read")

# Honest client identity. Same posture as the Bambu Cloud client: identifies
# Bambuddy without impersonating Orca's desktop client. Also the thing that
# clears Cloudflare's User-Agent gate in front of the API.
_USER_AGENT = "Bambuddy/1.0 (+https://github.com/maziggy/bambuddy)"

# Refresh the access token when it has less than this much life left, so a
# slow downstream API call doesn't expire the token mid-flight.
_REFRESH_LEEWAY = timedelta(minutes=5)

# How long a device-code pairing attempt stays valid before the user must
# restart. The server also enforces this (``expires_in`` on the device-code
# response is 600s); we mirror it client-side so we stop polling a dead code.
DEVICE_CODE_TTL = timedelta(minutes=10)


# ---------------------------------------------------------------------------
# Device-poll outcomes
# ---------------------------------------------------------------------------


class DevicePoll:
    """String outcomes of one :meth:`OrcaCloudService.poll_token` attempt.

    ``PENDING`` / ``SLOW_DOWN`` are non-terminal (keep polling; on SLOW_DOWN
    widen the interval). ``DENIED`` / ``EXPIRED`` are terminal — the pairing
    attempt is dead and the user must restart. ``COMPLETE`` means tokens were
    issued and applied to the service."""

    PENDING = "authorization_pending"
    SLOW_DOWN = "slow_down"
    DENIED = "access_denied"
    EXPIRED = "expired_token"
    COMPLETE = "complete"

    #: Non-terminal — the frontend should poll again.
    ONGOING = frozenset({PENDING, SLOW_DOWN})
    #: Terminal failure — the frontend should restart the flow.
    TERMINAL = frozenset({DENIED, EXPIRED})


class OrcaCloudError(Exception):
    """Base exception for Orca Cloud errors (network / unexpected server)."""

    pass


class OrcaCloudAuthError(OrcaCloudError):
    """Authentication / token-related errors. The caller should typically
    prompt the user to reconnect — neither a fresh access token nor a refresh
    will recover without re-pairing."""

    pass


_shared_http_client: httpx.AsyncClient | None = None


def set_shared_http_client(client: httpx.AsyncClient | None) -> None:
    """Register an app-scoped ``httpx.AsyncClient`` so per-request
    ``OrcaCloudService`` instances can reuse its connection pool. Mirrors the
    pattern used by :mod:`backend.app.services.bambu_cloud`."""
    global _shared_http_client
    _shared_http_client = client


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------


class OrcaCloudService:
    """Stateful per-request client for the Orca Cloud external API.

    Instantiated by the route layer, populated with a stored token via
    :meth:`set_tokens`, then used to call the sync endpoints. Token rotation
    on refresh is the route layer's responsibility (see :meth:`refresh` —
    mutates ``self`` and returns the new pair, but does NOT persist).
    """

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.token_expiry: datetime | None = None
        # Mirror the bambu_cloud pattern for client ownership: prefer injected
        # client (tests), fall back to app-scoped shared client (production),
        # else create our own so ad-hoc scripts still work.
        if client is not None:
            self._client = client
            self._owns_client = False
        elif _shared_http_client is not None:
            self._client = _shared_http_client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(timeout=30.0)
            self._owns_client = True

    @property
    def is_authenticated(self) -> bool:
        """True iff we have an access token that won't expire within
        :data:`_REFRESH_LEEWAY`. The leeway prevents a slow API call from
        timing out mid-flight on a token that was nominally still valid."""
        if not self.access_token:
            return False
        if self.token_expiry is None:
            # No expiry recorded — pessimistically treat as expired so the
            # caller refreshes before use.
            return False
        return datetime.now(timezone.utc) + _REFRESH_LEEWAY < self.token_expiry

    def set_tokens(
        self,
        access_token: str | None,
        refresh_token: str | None,
        expires_at: datetime | None,
    ) -> None:
        """Hydrate the service from stored credentials."""
        self.access_token = access_token
        self.refresh_token = refresh_token
        # Normalize to timezone-aware UTC so subsequent comparisons against
        # ``datetime.now(timezone.utc)`` are well-defined. asyncpg returns
        # naive datetimes from a ``TIMESTAMP WITHOUT TIME ZONE`` column —
        # we treat naive values as UTC since that's how we stored them.
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        self.token_expiry = expires_at

    def clear_tokens(self) -> None:
        """Forget all credentials. Used on logout and after auth failures."""
        self.access_token = None
        self.refresh_token = None
        self.token_expiry = None

    def _api_headers(self) -> dict[str, str]:
        """Headers for calls to the external API. Requires a bearer token —
        callers should ensure the service is authenticated first."""
        if not self.access_token:
            raise OrcaCloudAuthError("Orca Cloud API requires an access token")
        return {
            "User-Agent": _USER_AGENT,
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }

    # ------------------------------------------------------------------
    # Device authorization grant (RFC 8628)
    # ------------------------------------------------------------------

    async def request_device_code(
        self,
        scope: str = ORCA_SCOPE,
        instance_url: str | None = None,
        instance_label: str | None = None,
    ) -> dict[str, Any]:
        """Start a pairing attempt. Returns the raw device-code response
        (``device_code``, ``user_code``, ``verification_uri``,
        ``verification_uri_complete``, ``expires_in``, ``interval``).

        ``instance_url`` / ``instance_label`` are display-only fields shown on
        the user's approval card (anti-phishing context). The ``device_code``
        is a secret the caller must keep server-side; only ``user_code`` and
        the verification URIs are safe to show the user."""
        url = f"{ORCA_API_BASE}/oauth/device/code"
        form: dict[str, str] = {"client_id": ORCA_CLIENT_ID, "scope": scope}
        if instance_url:
            form["instance_url"] = instance_url
        if instance_label:
            form["instance_label"] = instance_label
        try:
            resp = await self._client.post(url, data=form, headers={"User-Agent": _USER_AGENT})
        except httpx.HTTPError as e:
            raise OrcaCloudError(f"Network error requesting Orca Cloud device code: {e}") from e

        if resp.status_code >= 400:
            detail = _describe_token_error(resp)
            # invalid_client means our client_id is wrong / unregistered — an
            # operator misconfiguration, not something the user can fix.
            if resp.status_code in (400, 401, 403):
                raise OrcaCloudAuthError(f"Orca Cloud rejected the device-code request: {detail}")
            raise OrcaCloudError(f"Orca Cloud device-code request failed ({resp.status_code}): {detail}")
        return resp.json()

    async def poll_token(self, device_code: str) -> tuple[str, dict[str, Any] | None]:
        """Poll the token endpoint once for a pending device-code grant.

        Returns ``(status, data)`` where ``status`` is a :class:`DevicePoll`
        value. On :data:`DevicePoll.COMPLETE` the service is mutated with the
        new tokens and ``data`` is the raw token response (so the caller can
        persist it); otherwise ``data`` is ``None``.

        Raises :class:`OrcaCloudError` only for genuinely unexpected responses
        (5xx, network, or an unrecognized error code) — the four RFC error
        codes are returned as statuses, not raised, because they're normal
        control flow for a polling loop."""
        url = f"{ORCA_API_BASE}/oauth/token"
        form = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": ORCA_CLIENT_ID,
        }
        try:
            resp = await self._client.post(url, data=form, headers={"User-Agent": _USER_AGENT})
        except httpx.HTTPError as e:
            raise OrcaCloudError(f"Network error polling Orca Cloud token endpoint: {e}") from e

        if resp.status_code < 400:
            data = resp.json()
            self._apply_token_response(data)
            return DevicePoll.COMPLETE, data

        # RFC 8628 error bodies: {"error": "authorization_pending" | ...}.
        error = _error_code(resp)
        if error == "authorization_pending":
            return DevicePoll.PENDING, None
        if error == "slow_down":
            return DevicePoll.SLOW_DOWN, None
        if error == "access_denied":
            return DevicePoll.DENIED, None
        # expired_token and invalid_grant both mean "this device code is dead,
        # start over" — collapse them to a single terminal EXPIRED status.
        if error in ("expired_token", "invalid_grant"):
            return DevicePoll.EXPIRED, None
        raise OrcaCloudError(f"Orca Cloud token poll failed ({resp.status_code}): {_describe_token_error(resp)}")

    async def refresh(self) -> dict[str, Any]:
        """Use the stored refresh token to obtain a fresh access/refresh pair.

        Refresh tokens are single-use — the old one is consumed the moment
        this succeeds. The caller MUST persist the new pair atomically; a
        crash between this return and the DB write strands the user (though
        Orca's ~60s grace window means a *replay* of the old token within that
        window still yields a working pair rather than revoking). Returns the
        raw token-response dict so the caller has the full new pair."""
        if not self.refresh_token:
            raise OrcaCloudAuthError("Cannot refresh: no refresh token stored")

        url = f"{ORCA_API_BASE}/oauth/token"
        form = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": ORCA_CLIENT_ID,
        }
        try:
            resp = await self._client.post(url, data=form, headers={"User-Agent": _USER_AGENT})
        except httpx.HTTPError as e:
            raise OrcaCloudError(f"Network error during Orca Cloud refresh: {e}") from e

        if resp.status_code >= 400:
            detail = _describe_token_error(resp)
            # 400 invalid_grant on refresh = expired / already-used / the user
            # disconnected us. Unrecoverable — clear and force a re-pair.
            if resp.status_code in (400, 401, 403):
                self.clear_tokens()
                raise OrcaCloudAuthError(f"Orca Cloud refresh rejected: {detail}")
            raise OrcaCloudError(f"Orca Cloud refresh failed ({resp.status_code}): {detail}")

        data = resp.json()
        self._apply_token_response(data)
        return data

    def _apply_token_response(self, data: dict[str, Any]) -> None:
        """Update ``self.access_token`` / ``self.refresh_token`` /
        ``self.token_expiry`` from a token-response payload. Caller is still
        responsible for persisting the values to the DB."""
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        expires_in = data.get("expires_in")
        if not access:
            raise OrcaCloudAuthError("Orca Cloud token response missing access_token")
        self.access_token = access
        # The token endpoint always rotates the refresh token; if a response
        # omits one we keep the previous value to avoid stranding the session,
        # but that shouldn't happen in practice.
        if refresh:
            self.refresh_token = refresh
        if isinstance(expires_in, (int, float)) and expires_in > 0:
            self.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        else:
            self.token_expiry = None

    # ------------------------------------------------------------------
    # External API
    # ------------------------------------------------------------------

    async def introspect(self) -> dict[str, Any]:
        """Return the pairing's introspection record (``user_id``,
        ``client_id``, ``connection_id``, ``scope``, ``expires_at``). Used
        after pairing to record the user's id for display in Bambuddy's UI."""
        url = f"{ORCA_API_BASE}/api/v1/external-apps/me"
        try:
            resp = await self._client.get(url, headers=self._api_headers())
        except httpx.HTTPError as e:
            raise OrcaCloudError(f"Network error fetching Orca Cloud introspection: {e}") from e
        if resp.status_code == 401:
            raise OrcaCloudAuthError("Orca Cloud introspection unauthorized — token expired or revoked")
        if resp.status_code >= 400:
            raise OrcaCloudError(f"Orca Cloud introspection failed ({resp.status_code}): {resp.text[:200]}")
        return resp.json()

    async def list_profiles(self) -> list[dict[str, Any]]:
        """Return the user's Orca Cloud profiles as a flat list of profile
        entries (``{id, name, content, updated_time, created_time}``) —
        forwarded verbatim; callers pick the fields they need.

        Uses ``GET /api/v1/external/sync/pull`` with NO ``?cursor=`` parameter,
        the documented "full snapshot" bootstrap. Sending ``cursor=0`` instead
        trips ``410 cursor_too_old`` (the sync log doesn't reach back to the
        Unix epoch). The pull response is ``{next_cursor, upserts, deletes}``;
        we return ``upserts`` and ignore the rest (no prior client state to
        invalidate on a read-only list)."""
        url = f"{ORCA_API_BASE}/api/v1/external/sync/pull"
        try:
            resp = await self._client.get(url, headers=self._api_headers())
        except httpx.HTTPError as e:
            raise OrcaCloudError(f"Network error listing Orca Cloud profiles: {e}") from e
        if resp.status_code == 401:
            raise OrcaCloudAuthError("Orca Cloud profile list unauthorized — token expired or revoked")
        if resp.status_code == 410:
            # cursor_too_old on a no-cursor request would be surprising, but
            # surface it clearly rather than as an opaque 502.
            raise OrcaCloudError("Orca Cloud sync cursor too old — a full resync is required")
        if resp.status_code >= 400:
            raise OrcaCloudError(f"Orca Cloud profile list failed ({resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        if isinstance(data, dict):
            upserts = data.get("upserts")
            if isinstance(upserts, list):
                return upserts
            # Tolerate a flat-list shape if Orca ever rolls one out here.
            for key in ("profiles", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        if isinstance(data, list):
            return data
        logger.warning("Orca Cloud /external/sync/pull returned unexpected shape: %r", type(data).__name__)
        return []

    async def get_profile(self, profile_id: str) -> dict[str, Any]:
        """Fetch a single profile's full content. The external sync API has no
        per-profile GET, so we list and filter. For the realistic profile
        counts this is fine; if it becomes a hot path we'll add caching at the
        route layer rather than hammer the pull endpoint."""
        profiles = await self.list_profiles()
        for profile in profiles:
            if str(profile.get("id")) == str(profile_id):
                return profile
        raise OrcaCloudError(f"Orca Cloud profile {profile_id!r} not found (scanned {len(profiles)} profiles)")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Release the underlying httpx client iff we own it. No-op if we're
        using an injected or app-shared client (those are managed elsewhere)."""
        if self._owns_client:
            await self._client.aclose()


def _error_code(resp: httpx.Response) -> str | None:
    """Extract the RFC-style ``error`` code from a token-endpoint error body,
    or ``None`` if the body doesn't parse as ``{"error": "..."}``."""
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, str) and err:
            return err
    return None


def _describe_token_error(resp: httpx.Response) -> str:
    """Best-effort extraction of a user-facing message from a token-endpoint
    error response. Tries JSON fields in order; falls back to the raw body
    (truncated) if nothing parses."""
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return (resp.text or "<empty body>")[:200]
    if not isinstance(data, dict):
        return str(data)[:200]
    for key in ("error_description", "msg", "error", "message"):
        val = data.get(key)
        if isinstance(val, str) and val:
            return val
    return str(data)[:200]
