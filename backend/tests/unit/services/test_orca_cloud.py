"""Tests for the Orca Cloud service — PKCE generation, authorize URL shape,
token exchange / refresh round-trip, single-use refresh token rotation,
and Cloudflare-cleaning User-Agent header."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from backend.app.services import orca_cloud
from backend.app.services.orca_cloud import (
    ORCA_ANON_KEY,
    ORCA_AUTH_BASE,
    ORCA_REDIRECT_URI,
    OrcaCloudAuthError,
    OrcaCloudError,
    OrcaCloudService,
    build_authorize_url,
    generate_pkce,
    parse_callback_url,
)

# ---------------------------------------------------------------------------
# PKCE primitives
# ---------------------------------------------------------------------------


class TestPkce:
    def test_challenge_is_sha256_of_verifier(self):
        """The challenge must be base64url(sha256(verifier)) — this is the
        RFC 7636 invariant Supabase will check on the exchange step. A bug
        here means the exchange always fails with code_verifier mismatch."""
        verifier, challenge, _state = generate_pkce()
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        assert challenge == expected

    def test_verifier_length_in_rfc_range(self):
        verifier, _challenge, _state = generate_pkce()
        # 32 random bytes -> 43 chars after base64url-no-pad; RFC 7636
        # requires 43-128.
        assert 43 <= len(verifier) <= 128

    def test_state_is_unique_per_call(self):
        """Two consecutive calls must not share state — otherwise a stolen
        state from one flow could be replayed against another in-flight one."""
        _, _, s1 = generate_pkce()
        _, _, s2 = generate_pkce()
        assert s1 != s2

    def test_characters_are_url_safe(self):
        """Both verifier and challenge must be URL-safe base64 (no padding,
        no + or /) so they can be sent as query-string values without
        re-encoding."""
        verifier, challenge, state = generate_pkce()
        for value in (verifier, challenge, state):
            assert all(c.isalnum() or c in ("-", "_") for c in value), value


class TestAuthorizeUrl:
    def test_url_targets_authorize_endpoint(self):
        url = build_authorize_url("CHALLENGE")
        assert url.startswith(f"{ORCA_AUTH_BASE}/auth/v1/authorize?")

    def test_url_contains_required_pkce_params(self):
        """The four PKCE params Supabase needs at authorize time. Missing any
        of these = Supabase 400s the request before redirecting to Google."""
        url = build_authorize_url("CHALLENGE")
        params = parse_qs(urlparse(url).query)
        assert params["provider"] == ["google"]
        assert params["redirect_to"] == [ORCA_REDIRECT_URI]
        assert params["code_challenge"] == ["CHALLENGE"]
        assert params["code_challenge_method"] == ["S256"]

    def test_url_does_not_pass_state(self):
        """Regression guard against re-introducing the bug we hit in the
        first deployed integration: passing ``state`` to GoTrue's authorize
        endpoint silently overrides its internal redirect_to tracking, so
        the user lands at the project Site URL instead of our localhost
        callback. CSRF is protected by PKCE alone — verifier is server-side
        and single-use."""
        url = build_authorize_url("CHALLENGE")
        params = parse_qs(urlparse(url).query)
        assert "state" not in params


class TestParseCallback:
    def test_extracts_code_and_state_from_query(self):
        code, state = parse_callback_url("http://localhost:41172/callback?code=ABC&state=XYZ")
        assert code == "ABC"
        assert state == "XYZ"

    def test_falls_back_to_fragment(self):
        """Some Supabase configurations put PKCE codes in the URL fragment
        rather than the query (depends on response_mode setting). Both must
        be handled or some users get a confusing 'no code in URL' error."""
        code, state = parse_callback_url("http://localhost:41172/callback#code=ABC&state=XYZ")
        assert code == "ABC"
        assert state == "XYZ"

    def test_returns_none_when_no_code(self):
        code, state = parse_callback_url("http://localhost:41172/callback?error=denied")
        assert code is None
        assert state is None

    def test_handles_whitespace_padding(self):
        """Users paste from address bars and sometimes accidentally include
        a leading/trailing space — the parser must be forgiving."""
        code, _state = parse_callback_url("  http://localhost:41172/callback?code=ABC&state=XYZ  ")
        assert code == "ABC"


# ---------------------------------------------------------------------------
# Token exchange + refresh
# ---------------------------------------------------------------------------


def _mock_response(
    *,
    status_code: int = 200,
    json_data: dict | None = None,
    text_body: str = "",
) -> MagicMock:
    """Build an httpx-like response mock with the only attributes the
    service touches: ``status_code``, ``.json()``, ``.text``."""
    resp = MagicMock(spec=["status_code", "json", "text"])
    resp.status_code = status_code
    if json_data is not None:
        resp.json.return_value = json_data
        resp.text = json.dumps(json_data)
    else:
        resp.json.side_effect = ValueError("not json")
        resp.text = text_body
    return resp


@pytest.fixture
def svc() -> OrcaCloudService:
    return OrcaCloudService(client=MagicMock(spec=httpx.AsyncClient))


class TestExchangeCode:
    @pytest.mark.asyncio
    async def test_success_populates_tokens_and_expiry(self, svc):
        token_resp = _mock_response(
            json_data={
                "access_token": "ACCESS-1",
                "refresh_token": "REFRESH-1",
                "expires_in": 3600,
                "token_type": "bearer",
            }
        )
        svc._client.post = AsyncMock(return_value=token_resp)

        await svc.exchange_code("CODE", "VERIFIER")

        assert svc.access_token == "ACCESS-1"
        assert svc.refresh_token == "REFRESH-1"
        assert svc.token_expiry is not None
        # Expiry should be approximately now + 3600s (within a 60s window).
        delta = svc.token_expiry - datetime.now(timezone.utc)
        assert timedelta(seconds=3540) <= delta <= timedelta(seconds=3660)

    @pytest.mark.asyncio
    async def test_sends_apikey_and_user_agent_headers(self, svc):
        """Two load-bearing headers: the publishable apikey (Supabase
        requires it) and a non-default User-Agent (Cloudflare 1010s
        ``Python-urllib/X.Y`` so an honest ``Bambuddy/<v>`` UA is needed)."""
        token_resp = _mock_response(json_data={"access_token": "A", "refresh_token": "R", "expires_in": 3600})
        svc._client.post = AsyncMock(return_value=token_resp)

        await svc.exchange_code("CODE", "VERIFIER")

        _args, kwargs = svc._client.post.call_args
        headers = kwargs["headers"]
        assert headers["apikey"] == ORCA_ANON_KEY
        assert headers["User-Agent"].startswith("Bambuddy/")
        assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_400_raises_auth_error_not_generic(self, svc):
        """400 from Supabase usually means a bad verifier or stale code —
        the user has to restart sign-in. Raising auth-specific exception
        lets the route map to a sensible 400 with a 'click Connect again'
        message rather than a generic 502."""
        err_resp = _mock_response(
            status_code=400,
            json_data={"error": "invalid_grant", "error_description": "code expired"},
        )
        svc._client.post = AsyncMock(return_value=err_resp)

        with pytest.raises(OrcaCloudAuthError) as exc:
            await svc.exchange_code("CODE", "VERIFIER")
        assert "code expired" in str(exc.value)

    @pytest.mark.asyncio
    async def test_network_error_wraps_as_orca_error(self, svc):
        svc._client.post = AsyncMock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(OrcaCloudError):
            await svc.exchange_code("CODE", "VERIFIER")


class TestPasswordLogin:
    @pytest.mark.asyncio
    async def test_success_populates_tokens(self, svc):
        resp = _mock_response(
            json_data={
                "access_token": "PWD-A",
                "refresh_token": "PWD-R",
                "expires_in": 3600,
            }
        )
        svc._client.post = AsyncMock(return_value=resp)

        await svc.password_login("user@example.com", "secret")

        assert svc.access_token == "PWD-A"
        assert svc.refresh_token == "PWD-R"

    @pytest.mark.asyncio
    async def test_disabled_provider_raises_auth_error_not_generic(self, svc):
        """Whether Orca's Supabase project accepts password grant is config-
        dependent. When it doesn't (their desktop SDK refuses passwords by
        design, the backend may follow suit), the failure mode is a 400 /
        422 with an error like ``email_provider_disabled``. The caller maps
        ``OrcaCloudAuthError`` to a 400 with a "use OAuth instead" hint —
        a 502 would imply Orca is down, which would be wrong UX."""
        err = _mock_response(
            status_code=422,
            json_data={"error": "email_provider_disabled", "error_description": "Email logins are disabled"},
        )
        svc._client.post = AsyncMock(return_value=err)
        with pytest.raises(OrcaCloudAuthError, match="Email logins are disabled"):
            await svc.password_login("user@example.com", "secret")

    @pytest.mark.asyncio
    async def test_invalid_credentials_raises_auth_error(self, svc):
        err = _mock_response(
            status_code=400,
            json_data={"error": "invalid_grant", "error_description": "Invalid login credentials"},
        )
        svc._client.post = AsyncMock(return_value=err)
        with pytest.raises(OrcaCloudAuthError, match="Invalid login credentials"):
            await svc.password_login("user@example.com", "wrong")


class TestRefresh:
    @pytest.mark.asyncio
    async def test_rotates_refresh_token(self, svc):
        """Supabase refresh tokens are single-use — every successful refresh
        returns a NEW refresh token and invalidates the old. If the service
        kept the old one, the next refresh would 400 and the user would be
        force-logged-out."""
        svc.refresh_token = "REFRESH-1"
        resp = _mock_response(
            json_data={
                "access_token": "ACCESS-2",
                "refresh_token": "REFRESH-2",
                "expires_in": 3600,
            }
        )
        svc._client.post = AsyncMock(return_value=resp)

        await svc.refresh()

        assert svc.access_token == "ACCESS-2"
        assert svc.refresh_token == "REFRESH-2"

    @pytest.mark.asyncio
    async def test_no_refresh_token_raises_auth_error(self, svc):
        svc.refresh_token = None
        with pytest.raises(OrcaCloudAuthError):
            await svc.refresh()

    @pytest.mark.asyncio
    async def test_rejected_refresh_clears_tokens(self, svc):
        """If Supabase rejects the refresh token (revoked / rotated out from
        under us / hit by a token-replay defense), the service must clear
        the now-useless stored credentials so the UI can flip to the
        disconnected state rather than retrying forever."""
        svc.access_token = "OLD-ACCESS"
        svc.refresh_token = "OLD-REFRESH"
        svc.token_expiry = datetime.now(timezone.utc)
        err = _mock_response(
            status_code=401,
            json_data={"error": "invalid_grant", "error_description": "refresh token rotated"},
        )
        svc._client.post = AsyncMock(return_value=err)

        with pytest.raises(OrcaCloudAuthError):
            await svc.refresh()

        assert svc.access_token is None
        assert svc.refresh_token is None
        assert svc.token_expiry is None


class TestIsAuthenticated:
    def test_no_token_means_not_authenticated(self, svc):
        assert svc.is_authenticated is False

    def test_no_expiry_means_not_authenticated(self, svc):
        """Pessimistic default: if we don't know when the token expires,
        treat it as expired so the next API call triggers a refresh
        rather than fails halfway through."""
        svc.access_token = "ACCESS"
        svc.token_expiry = None
        assert svc.is_authenticated is False

    def test_within_refresh_leeway_is_not_authenticated(self, svc):
        """The 5-minute leeway prevents a long-running API call from timing
        out mid-flight on a token that was technically still valid when the
        call started."""
        svc.access_token = "ACCESS"
        svc.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=2)
        assert svc.is_authenticated is False

    def test_with_comfortable_expiry_is_authenticated(self, svc):
        svc.access_token = "ACCESS"
        svc.token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        assert svc.is_authenticated is True


class TestApiHeaders:
    def test_api_headers_include_apikey_and_bearer(self, svc):
        svc.access_token = "ACCESS-123"
        headers = svc._api_headers()
        assert headers["apikey"] == ORCA_ANON_KEY
        assert headers["Authorization"] == "Bearer ACCESS-123"
        assert headers["User-Agent"].startswith("Bambuddy/")

    def test_api_headers_without_token_raises(self, svc):
        svc.access_token = None
        with pytest.raises(OrcaCloudAuthError):
            svc._api_headers()


class TestListProfiles:
    @pytest.mark.asyncio
    async def test_pull_response_upserts_extracted(self, svc):
        """The bare-cursor /sync/pull returns a ``SyncPullResponse`` shape;
        we extract the ``upserts`` list and ignore ``next_cursor`` / ``deletes``
        (no prior client state to invalidate)."""
        svc.access_token = "ACCESS"
        svc.token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        svc._client.get = AsyncMock(
            return_value=_mock_response(
                json_data={
                    "next_cursor": 12345,
                    "upserts": [
                        {"id": "a", "name": "A", "content": {"x": 1}},
                        {"id": "b", "name": "B", "content": {"x": 2}},
                    ],
                    "deletes": ["zzz"],
                },
            )
        )
        result = await svc.list_profiles()
        assert [p["id"] for p in result] == ["a", "b"]

    @pytest.mark.asyncio
    async def test_pull_hits_path_without_cursor(self, svc):
        """Regression guard: ``cursor=0`` trips ``410 cursor_too_old`` on
        the production endpoint. The first-sync bootstrap must hit
        ``/api/v1/sync/pull`` with no ``?cursor=`` parameter — same behaviour
        as OrcaSlicer's own client."""
        svc.access_token = "ACCESS"
        svc.token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        svc._client.get = AsyncMock(
            return_value=_mock_response(json_data={"upserts": [], "deletes": []}),
        )
        await svc.list_profiles()
        called_url = svc._client.get.call_args.args[0]
        assert called_url.endswith("/api/v1/sync/pull")
        assert "cursor" not in called_url
        # And no ``params`` kwarg either, which would be a second way to
        # smuggle the cursor in.
        assert "params" not in svc._client.get.call_args.kwargs

    @pytest.mark.asyncio
    async def test_bare_list_response_tolerated(self, svc):
        """If the server ever rolls out a flat-list response shape, we
        forward it verbatim rather than logging-and-empty."""
        svc.access_token = "ACCESS"
        svc.token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        svc._client.get = AsyncMock(
            return_value=_mock_response(json_data=[{"id": "a", "name": "A"}]),
        )
        assert [p["id"] for p in await svc.list_profiles()] == ["a"]


class TestGetProfile:
    @pytest.mark.asyncio
    async def test_returns_matching_profile_with_content(self, svc):
        """``get_profile`` lists then filters since Orca has no dedicated
        per-profile GET — verify the matched entry returns with full
        content, not stripped to metadata."""
        svc.access_token = "ACCESS"
        svc.token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        svc._client.get = AsyncMock(
            return_value=_mock_response(
                json_data={
                    "upserts": [
                        {"id": "a", "name": "A", "content": {"foo": 1}},
                        {"id": "target", "name": "Target", "content": {"hit": True}},
                    ],
                    "deletes": [],
                },
            )
        )

        profile = await svc.get_profile("target")

        assert profile["id"] == "target"
        assert profile["content"] == {"hit": True}

    @pytest.mark.asyncio
    async def test_not_found_raises(self, svc):
        svc.access_token = "ACCESS"
        svc.token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        svc._client.get = AsyncMock(
            return_value=_mock_response(
                json_data={"upserts": [{"id": "a", "name": "A"}], "deletes": []},
            ),
        )
        with pytest.raises(OrcaCloudError, match="not found"):
            await svc.get_profile("missing")
