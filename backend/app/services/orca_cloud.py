"""
Orca Cloud API Service

Handles authentication and profile sync with the Orca Cloud (Supabase-backed).

Auth shape: PKCE flow against ``auth.orcaslicer.com`` with the in-source public
publishable key. Bambuddy generates the verifier/challenge/state, redirects the
user's browser to Supabase's ``/auth/v1/authorize`` endpoint with
``redirect_to=http://localhost:41172/callback``, and the user pastes the
callback URL back into Bambuddy (the loopback URL is the only ``redirect_to``
Orca's Supabase project actually honors as of v2.4.0-alpha — see
OrcaSlicer/OrcaSlicer#14028 for the open feature request asking SoftFever to
broaden this).

Token shape: short-lived access JWT (1h) + rotating single-use refresh token.
Every refresh issues a new pair and invalidates the old one — the route layer
is responsible for atomically swapping the stored pair on each refresh, or a
mid-refresh crash strands the user.

Cloudflare protects ``api.orcaslicer.com`` with a User-Agent gate; sending an
honest ``Bambuddy/<version>`` UA clears it. No TLS-fingerprint matching needed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Auth + API endpoints — extracted verbatim from OrcaCloudServiceAgent.cpp
# v2.4.0-alpha. The "publishable" key is documented in-source as a public
# client identifier (Supabase anon-key pattern); embedding it in our client
# is by-design and not a secret leak.
ORCA_AUTH_BASE = "https://auth.orcaslicer.com"
ORCA_API_BASE = "https://api.orcaslicer.com"
ORCA_ANON_KEY = "sb_publishable_lvVe_whOi80SU9BPSxM1kA_tbt9AbR_"

# Loopback redirect from OrcaCloudServiceAgent.cpp. Supabase's redirect_to
# allowlist on Orca's project only honors localhost URIs — anything else
# silently falls through to the project Site URL after the OAuth dance.
ORCA_REDIRECT_URI = "http://localhost:41172/callback"

# Honest client identity. Same posture as Bambu Cloud: identifies Bambuddy
# without impersonating Orca's desktop client (which would be CWE-style
# falsified-identity and was the exact thing called out in Bambu Lab's May 2026
# blog post about cloud-access etiquette).
_USER_AGENT = "Bambuddy/1.0 (+https://github.com/maziggy/bambuddy)"

# Refresh access tokens when they have less than this much life left, on the
# theory that a slow downstream API call shouldn't expire the token mid-flight.
_REFRESH_LEEWAY = timedelta(minutes=5)

# PKCE handshake state TTL. If the user clicks "Connect" then walks away,
# the stored verifier+state is invalid after this window — they have to
# restart. 10 minutes is the OAuth norm for desktop-app PKCE flows.
PENDING_PKCE_TTL = timedelta(minutes=10)


class OrcaCloudError(Exception):
    """Base exception for Orca Cloud errors."""

    pass


class OrcaCloudAuthError(OrcaCloudError):
    """Authentication / token-related errors. Caller should typically prompt
    the user to reconnect — neither a fresh access token nor a refresh will
    recover without re-authentication."""

    pass


_shared_http_client: httpx.AsyncClient | None = None


def set_shared_http_client(client: httpx.AsyncClient | None) -> None:
    """Register an app-scoped ``httpx.AsyncClient`` so per-request
    ``OrcaCloudService`` instances can reuse its connection pool. Mirrors the
    pattern used by :mod:`backend.app.services.bambu_cloud`."""
    global _shared_http_client
    _shared_http_client = client


# ---------------------------------------------------------------------------
# PKCE helpers (free functions — no service-instance state needed)
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    """RFC 7636-style base64url encoding, no padding."""
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def generate_pkce() -> tuple[str, str, str]:
    """Generate a fresh ``(verifier, challenge, state)`` triple for one PKCE
    handshake. The verifier is the secret kept by Bambuddy until the code
    exchange; the challenge is sent to Supabase as ``code_challenge``; the
    state is the CSRF nonce we'll verify against the callback.

    Verifier = 32 random bytes (43 base64url chars), within RFC 7636's
    43-128 char range. Challenge = ``base64url(sha256(verifier))``.
    """
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = _b64url(secrets.token_bytes(16))
    return verifier, challenge, state


def build_authorize_url(challenge: str, provider: str = "google") -> str:
    """Construct the URL the user's browser should visit to start the OAuth
    handshake.

    Notably **does not** pass a ``state`` query parameter. Supabase's GoTrue
    uses its own internal state encoding to remember which ``redirect_to``
    belongs to which OAuth session; a client-passed ``state`` overwrites
    that, GoTrue can no longer decode the redirect_to from Google's
    callback, and silently falls back to the project Site URL — which is
    exactly the bug that broke the live test against our deployed integration.

    CSRF is still protected by the PKCE flow itself: the server-side
    ``code_verifier`` is single-use and bound to the user's session, so an
    attacker with a code-only URL can't complete the exchange.
    """
    from urllib.parse import urlencode

    qs = urlencode(
        {
            "provider": provider,
            "redirect_to": ORCA_REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{ORCA_AUTH_BASE}/auth/v1/authorize?{qs}"


def parse_callback_url(callback_url: str) -> tuple[str | None, str | None]:
    """Extract ``(code, state)`` from a pasted callback URL. Both query string
    and fragment are checked — some Supabase configurations put PKCE codes in
    the fragment rather than the query string. Returns ``(None, None)`` if
    nothing parses out; the route layer surfaces the user-facing error."""
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(callback_url.strip())
    qsd = parse_qs(parsed.query)
    code = qsd.get("code", [""])[0] or None
    state = qsd.get("state", [""])[0] or None
    if not code:
        frag = parse_qs(parsed.fragment)
        code = frag.get("code", [""])[0] or None
        state = state or (frag.get("state", [""])[0] or None)
    return code, state


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------


class OrcaCloudService:
    """Stateful per-request client for the Orca Cloud API.

    Instantiated by the route layer, populated with a stored token via
    :meth:`set_tokens`, then used to call the sync endpoints. Token rotation
    on refresh is the route layer's responsibility (see
    :meth:`refresh` — returns the new pair, doesn't persist).
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

    def _auth_headers(self) -> dict[str, str]:
        """Headers for calls to ``auth.orcaslicer.com``. Always includes the
        apikey; the ``Authorization`` header is added only if we already have
        an access token (used by ``/logout``, not by token exchange)."""
        headers = {
            "User-Agent": _USER_AGENT,
            "apikey": ORCA_ANON_KEY,
            "Content-Type": "application/json",
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _api_headers(self) -> dict[str, str]:
        """Headers for calls to ``api.orcaslicer.com``. Requires a bearer
        token — callers should ensure the service is authenticated first."""
        if not self.access_token:
            raise OrcaCloudAuthError("Orca Cloud API requires an access token")
        return {
            "User-Agent": _USER_AGENT,
            "apikey": ORCA_ANON_KEY,
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }

    # ------------------------------------------------------------------
    # Token lifecycle
    # ------------------------------------------------------------------

    async def password_login(self, email: str, password: str) -> dict[str, Any]:
        """Direct email+password login via ``/auth/v1/token?grant_type=password``.

        Whether this works depends on the Supabase project's auth config —
        Orca's web sign-in offers email/password as one option, but their
        desktop client refuses ``{username, password}`` payloads with
        ``"Username/password login is disabled. Use the Orca cloud PKCE
        flow."`` (the SDK enforces PKCE regardless of what the backend
        allows). The actual server behaviour is what matters for Bambuddy
        — we POST the credentials and surface whatever response we get;
        an ``OrcaCloudAuthError`` with the verbatim Supabase error message
        is the right signal for callers to fall back to an OAuth provider.
        """
        url = f"{ORCA_AUTH_BASE}/auth/v1/token?grant_type=password"
        payload = {"email": email, "password": password}
        try:
            resp = await self._client.post(
                url,
                json=payload,
                headers={
                    "User-Agent": _USER_AGENT,
                    "apikey": ORCA_ANON_KEY,
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as e:
            raise OrcaCloudError(f"Network error during Orca Cloud password login: {e}") from e

        if resp.status_code >= 400:
            detail = _describe_token_error(resp)
            if resp.status_code in (400, 401, 403, 422):
                raise OrcaCloudAuthError(f"Orca Cloud password login rejected: {detail}")
            raise OrcaCloudError(f"Orca Cloud password login failed ({resp.status_code}): {detail}")

        data = resp.json()
        self._apply_token_response(data)
        return data

    async def exchange_code(self, auth_code: str, code_verifier: str) -> dict[str, Any]:
        """Exchange a PKCE auth code for tokens. Mutates ``self`` so the
        service is ready for API calls. Returns the raw Supabase token
        response so the route layer can persist the new credentials."""
        url = f"{ORCA_AUTH_BASE}/auth/v1/token?grant_type=pkce"
        payload = {"auth_code": auth_code, "code_verifier": code_verifier}
        try:
            resp = await self._client.post(
                url,
                json=payload,
                headers={
                    "User-Agent": _USER_AGENT,
                    "apikey": ORCA_ANON_KEY,
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as e:
            raise OrcaCloudError(f"Network error during Orca Cloud token exchange: {e}") from e

        if resp.status_code >= 400:
            # Supabase returns ``{"error":"...", "error_description":"..."}``
            # on most failures and ``{"msg":"..."}`` on a few. Surface
            # whatever we can find.
            detail = _describe_token_error(resp)
            if resp.status_code in (400, 401, 403):
                raise OrcaCloudAuthError(f"Orca Cloud token exchange rejected: {detail}")
            raise OrcaCloudError(f"Orca Cloud token exchange failed ({resp.status_code}): {detail}")

        data = resp.json()
        self._apply_token_response(data)
        return data

    async def refresh(self) -> dict[str, Any]:
        """Use the stored refresh token to obtain a fresh access/refresh pair.

        Supabase issues single-use refresh tokens — the old refresh token is
        invalidated the moment this call succeeds. The caller MUST persist the
        new pair atomically with consuming the old one; otherwise a crash
        between this return and the DB write strands the user. Returns the
        raw token-response dict so the caller has the full new pair.
        """
        if not self.refresh_token:
            raise OrcaCloudAuthError("Cannot refresh: no refresh token stored")

        url = f"{ORCA_AUTH_BASE}/auth/v1/token?grant_type=refresh_token"
        payload = {"refresh_token": self.refresh_token}
        try:
            resp = await self._client.post(
                url,
                json=payload,
                headers={
                    "User-Agent": _USER_AGENT,
                    "apikey": ORCA_ANON_KEY,
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as e:
            raise OrcaCloudError(f"Network error during Orca Cloud refresh: {e}") from e

        if resp.status_code >= 400:
            detail = _describe_token_error(resp)
            # 400/401 typically means "refresh token rotated or revoked" —
            # the user has to reconnect. Don't try to recover here.
            if resp.status_code in (400, 401, 403):
                self.clear_tokens()
                raise OrcaCloudAuthError(f"Orca Cloud refresh rejected: {detail}")
            raise OrcaCloudError(f"Orca Cloud refresh failed ({resp.status_code}): {detail}")

        data = resp.json()
        self._apply_token_response(data)
        return data

    def _apply_token_response(self, data: dict[str, Any]) -> None:
        """Update ``self.access_token`` / ``self.refresh_token`` /
        ``self.token_expiry`` from a Supabase token-response payload. Caller
        is still responsible for persisting the values to the DB."""
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        expires_in = data.get("expires_in")
        if not access:
            raise OrcaCloudAuthError("Orca Cloud token response missing access_token")
        self.access_token = access
        # Supabase always rotates refresh tokens on /token calls; if the
        # response omits one we keep the previous value to avoid stranding
        # the session, but that shouldn't happen in practice.
        if refresh:
            self.refresh_token = refresh
        if isinstance(expires_in, (int, float)) and expires_in > 0:
            self.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        else:
            self.token_expiry = None

    # ------------------------------------------------------------------
    # Sync API
    # ------------------------------------------------------------------

    async def get_user_info(self) -> dict[str, Any]:
        """Return Supabase's user record for the current token (id, email,
        metadata, ...). Used after token exchange to record the user's email
        for display in Bambuddy's UI."""
        url = f"{ORCA_AUTH_BASE}/auth/v1/user"
        try:
            resp = await self._client.get(url, headers=self._auth_headers())
        except httpx.HTTPError as e:
            raise OrcaCloudError(f"Network error fetching Orca Cloud user info: {e}") from e
        if resp.status_code == 401:
            raise OrcaCloudAuthError("Orca Cloud user fetch unauthorized — token expired or revoked")
        if resp.status_code >= 400:
            raise OrcaCloudError(f"Orca Cloud user fetch failed ({resp.status_code}): {resp.text[:200]}")
        return resp.json()

    async def list_profiles(self) -> list[dict[str, Any]]:
        """Return the user's Orca Cloud profiles as a flat list of
        ``ProfileUpsert`` entries (``{id, name, content, updated_time,
        created_time}``) — forwarded verbatim; callers pick the fields they
        need.

        Uses ``GET /api/v1/sync/pull`` with NO ``?cursor=`` parameter, which
        is the same "first-sync bootstrap" path OrcaSlicer's own client
        uses (``OrcaCloudServiceAgent.cpp::sync_pull``):

            std::string path = ORCA_SYNC_PULL_PATH;
            if (sync_state.last_sync_timestamp != 0) {
                path += "?cursor=" + std::to_string(sync_state.last_sync_timestamp);
            }
            ...
            // Handle 410 Gone — cursor too old, need full resync
            if (http_code == 410) {
                clear_sync_state();
                path = ORCA_SYNC_PULL_PATH;  // retry without cursor
                ...
            }

        Sending ``cursor=0`` explicitly trips ``410 cursor_too_old`` — the
        server-side sync log doesn't reach back to the Unix epoch. Omitting
        the parameter entirely is the documented "give me the full snapshot"
        semantic. The previously-attempted ``/api/v1/sync/profiles`` is
        declared as a constant in Orca's source but isn't deployed on the
        production cloud (returns 404).

        The pull response is a ``SyncPullResponse`` (``{next_cursor, upserts,
        deletes}``); we extract ``upserts`` and ignore ``deletes`` (no prior
        state on the client side to invalidate).
        """
        url = f"{ORCA_API_BASE}/api/v1/sync/pull"
        try:
            resp = await self._client.get(url, headers=self._api_headers())
        except httpx.HTTPError as e:
            raise OrcaCloudError(f"Network error listing Orca Cloud profiles: {e}") from e
        if resp.status_code == 401:
            raise OrcaCloudAuthError("Orca Cloud profile list unauthorized — token expired or revoked")
        if resp.status_code >= 400:
            raise OrcaCloudError(f"Orca Cloud profile list failed ({resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        if isinstance(data, dict):
            upserts = data.get("upserts")
            if isinstance(upserts, list):
                return upserts
            # Tolerate the shape we'd see if Orca ever rolls out a flat-list
            # endpoint at this path — forward whatever array is on the dict.
            for key in ("profiles", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        if isinstance(data, list):
            return data
        logger.warning("Orca Cloud /sync/pull returned unexpected shape: %r", type(data).__name__)
        return []

    async def get_profile(self, profile_id: str) -> dict[str, Any]:
        """Fetch a single profile's full content. Orca's sync API doesn't
        expose a per-profile GET, so we list and filter. For small profile
        counts (the realistic case) this is fine; if it becomes a hot path
        we'll add client-side caching at the route layer rather than hammer
        the list endpoint.
        """
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


def _describe_token_error(resp: httpx.Response) -> str:
    """Best-effort extraction of a user-facing message from a Supabase token
    endpoint error response. Tries JSON fields in order; falls back to the
    raw body (truncated) if nothing parses."""
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
