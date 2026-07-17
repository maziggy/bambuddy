"""Tests for the Orca Cloud device-pairing service — device-code request,
token poll (the four RFC 8628 outcomes + success), single-use refresh
rotation, external-API headers (bearer, no apikey), and profile pull."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from backend.app.services.orca_cloud import (
    ORCA_CLIENT_ID,
    DevicePoll,
    OrcaCloudAuthError,
    OrcaCloudError,
    OrcaCloudService,
)


def _mock_response(
    *,
    status_code: int = 200,
    json_data: dict | list | None = None,
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


# ---------------------------------------------------------------------------
# Device-code request
# ---------------------------------------------------------------------------


class TestRequestDeviceCode:
    @pytest.mark.asyncio
    async def test_success_returns_device_code_payload(self, svc):
        resp = _mock_response(
            json_data={
                "device_code": "DEV-1",
                "user_code": "ABCD-EF12",
                "verification_uri": "https://cloud.orcaslicer.com/app/settings",
                "verification_uri_complete": "https://cloud.orcaslicer.com/app/settings?user_code=ABCD-EF12",
                "expires_in": 600,
                "interval": 5,
            }
        )
        svc._client.post = AsyncMock(return_value=resp)

        data = await svc.request_device_code()

        assert data["user_code"] == "ABCD-EF12"
        assert data["device_code"] == "DEV-1"

    @pytest.mark.asyncio
    async def test_sends_client_id_scope_and_user_agent(self, svc):
        """The device-code request is form-encoded (NOT JSON) with our public
        client_id and requested scope, and carries the Cloudflare-clearing
        User-Agent. No ``apikey`` header (that was the old Supabase flow)."""
        resp = _mock_response(json_data={"device_code": "D", "user_code": "U", "interval": 5, "expires_in": 600})
        svc._client.post = AsyncMock(return_value=resp)

        await svc.request_device_code()

        _args, kwargs = svc._client.post.call_args
        assert kwargs["data"]["client_id"] == ORCA_CLIENT_ID
        assert kwargs["data"]["scope"]  # a scope is always sent
        assert kwargs["headers"]["User-Agent"].startswith("Bambuddy/")
        assert "apikey" not in kwargs["headers"]

    @pytest.mark.asyncio
    async def test_forwards_instance_fields_when_given(self, svc):
        resp = _mock_response(json_data={"device_code": "D", "user_code": "U", "interval": 5, "expires_in": 600})
        svc._client.post = AsyncMock(return_value=resp)

        await svc.request_device_code(instance_url="http://192.168.1.50:8080", instance_label="Garage")

        _args, kwargs = svc._client.post.call_args
        assert kwargs["data"]["instance_url"] == "http://192.168.1.50:8080"
        assert kwargs["data"]["instance_label"] == "Garage"

    @pytest.mark.asyncio
    async def test_invalid_client_raises_auth_error(self, svc):
        """A wrong/unregistered client_id returns ``invalid_client`` — an
        operator misconfiguration surfaced as an auth error so the route can
        map it distinctly from a transient outage."""
        resp = _mock_response(status_code=400, json_data={"error": "invalid_client"})
        svc._client.post = AsyncMock(return_value=resp)
        with pytest.raises(OrcaCloudAuthError, match="invalid_client"):
            await svc.request_device_code()

    @pytest.mark.asyncio
    async def test_network_error_wraps(self, svc):
        svc._client.post = AsyncMock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(OrcaCloudError):
            await svc.request_device_code()


# ---------------------------------------------------------------------------
# Token poll (RFC 8628 device_code grant)
# ---------------------------------------------------------------------------


class TestPollToken:
    @pytest.mark.asyncio
    async def test_success_applies_tokens_and_returns_complete(self, svc):
        resp = _mock_response(
            json_data={
                "access_token": "oc_ext_A",
                "refresh_token": "oc_ext_rt_R",
                "expires_in": 86400,
                "token_type": "Bearer",
            }
        )
        svc._client.post = AsyncMock(return_value=resp)

        status, data = await svc.poll_token("DEV-1")

        assert status == DevicePoll.COMPLETE
        assert data["access_token"] == "oc_ext_A"
        assert svc.access_token == "oc_ext_A"
        assert svc.refresh_token == "oc_ext_rt_R"
        assert svc.token_expiry is not None

    @pytest.mark.asyncio
    async def test_sends_device_code_grant_and_client_id(self, svc):
        resp = _mock_response(json_data={"access_token": "A", "refresh_token": "R", "expires_in": 86400})
        svc._client.post = AsyncMock(return_value=resp)

        await svc.poll_token("DEV-1")

        _args, kwargs = svc._client.post.call_args
        assert kwargs["data"]["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"
        assert kwargs["data"]["device_code"] == "DEV-1"
        assert kwargs["data"]["client_id"] == ORCA_CLIENT_ID

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error_code,expected",
        [
            ("authorization_pending", DevicePoll.PENDING),
            ("slow_down", DevicePoll.SLOW_DOWN),
            ("access_denied", DevicePoll.DENIED),
            ("expired_token", DevicePoll.EXPIRED),
            ("invalid_grant", DevicePoll.EXPIRED),  # collapsed to EXPIRED
        ],
    )
    async def test_rfc_error_codes_map_to_statuses(self, svc, error_code, expected):
        """The four RFC error codes (plus invalid_grant) are normal polling
        control flow — returned as statuses, never raised."""
        resp = _mock_response(status_code=400, json_data={"error": error_code})
        svc._client.post = AsyncMock(return_value=resp)

        status, data = await svc.poll_token("DEV-1")

        assert status == expected
        assert data is None

    @pytest.mark.asyncio
    async def test_unknown_error_raises(self, svc):
        """An unrecognized error body is a real problem, not a poll state —
        raise so it doesn't silently masquerade as 'still pending' forever."""
        resp = _mock_response(status_code=400, json_data={"error": "teapot"})
        svc._client.post = AsyncMock(return_value=resp)
        with pytest.raises(OrcaCloudError):
            await svc.poll_token("DEV-1")

    @pytest.mark.asyncio
    async def test_network_error_wraps(self, svc):
        svc._client.post = AsyncMock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(OrcaCloudError):
            await svc.poll_token("DEV-1")


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


class TestRefresh:
    @pytest.mark.asyncio
    async def test_rotates_refresh_token(self, svc):
        """Refresh tokens are single-use — every successful refresh returns a
        NEW pair. Keeping the old refresh token would 400 the next refresh."""
        svc.refresh_token = "oc_ext_rt_1"
        resp = _mock_response(
            json_data={"access_token": "oc_ext_2", "refresh_token": "oc_ext_rt_2", "expires_in": 86400}
        )
        svc._client.post = AsyncMock(return_value=resp)

        await svc.refresh()

        assert svc.access_token == "oc_ext_2"
        assert svc.refresh_token == "oc_ext_rt_2"

    @pytest.mark.asyncio
    async def test_sends_refresh_grant_and_client_id(self, svc):
        svc.refresh_token = "oc_ext_rt_1"
        resp = _mock_response(json_data={"access_token": "A", "refresh_token": "R", "expires_in": 86400})
        svc._client.post = AsyncMock(return_value=resp)

        await svc.refresh()

        _args, kwargs = svc._client.post.call_args
        assert kwargs["data"]["grant_type"] == "refresh_token"
        assert kwargs["data"]["refresh_token"] == "oc_ext_rt_1"
        assert kwargs["data"]["client_id"] == ORCA_CLIENT_ID

    @pytest.mark.asyncio
    async def test_no_refresh_token_raises_auth_error(self, svc):
        svc.refresh_token = None
        with pytest.raises(OrcaCloudAuthError):
            await svc.refresh()

    @pytest.mark.asyncio
    async def test_rejected_refresh_clears_tokens(self, svc):
        """A rejected refresh (revoked / already-used / disconnected) is
        unrecoverable — clear the stale credentials so the UI flips to
        disconnected rather than retrying forever."""
        svc.access_token = "OLD"
        svc.refresh_token = "oc_ext_rt_old"
        svc.token_expiry = datetime.now(timezone.utc)
        resp = _mock_response(status_code=400, json_data={"error": "invalid_grant"})
        svc._client.post = AsyncMock(return_value=resp)

        with pytest.raises(OrcaCloudAuthError):
            await svc.refresh()

        assert svc.access_token is None
        assert svc.refresh_token is None
        assert svc.token_expiry is None


# ---------------------------------------------------------------------------
# is_authenticated
# ---------------------------------------------------------------------------


class TestIsAuthenticated:
    def test_no_token_means_not_authenticated(self, svc):
        assert svc.is_authenticated is False

    def test_no_expiry_means_not_authenticated(self, svc):
        svc.access_token = "A"
        svc.token_expiry = None
        assert svc.is_authenticated is False

    def test_within_refresh_leeway_is_not_authenticated(self, svc):
        svc.access_token = "A"
        svc.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=2)
        assert svc.is_authenticated is False

    def test_with_comfortable_expiry_is_authenticated(self, svc):
        svc.access_token = "A"
        svc.token_expiry = datetime.now(timezone.utc) + timedelta(hours=12)
        assert svc.is_authenticated is True


# ---------------------------------------------------------------------------
# External-API headers
# ---------------------------------------------------------------------------


class TestApiHeaders:
    def test_api_headers_include_bearer_and_ua_no_apikey(self, svc):
        """External API auth is a plain bearer token — the old Supabase
        ``apikey`` header must NOT be sent (the ``oc_ext_`` token is the whole
        credential)."""
        svc.access_token = "oc_ext_123"
        headers = svc._api_headers()
        assert headers["Authorization"] == "Bearer oc_ext_123"
        assert headers["User-Agent"].startswith("Bambuddy/")
        assert "apikey" not in headers

    def test_api_headers_without_token_raises(self, svc):
        svc.access_token = None
        with pytest.raises(OrcaCloudAuthError):
            svc._api_headers()


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


class TestIntrospect:
    @pytest.mark.asyncio
    async def test_returns_record(self, svc):
        svc.access_token = "oc_ext_A"
        svc._client.get = AsyncMock(
            return_value=_mock_response(
                json_data={"user_id": "u-1", "client_id": ORCA_CLIENT_ID, "connection_id": "c-1"}
            )
        )
        info = await svc.introspect()
        assert info["user_id"] == "u-1"

    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self, svc):
        svc.access_token = "oc_ext_A"
        svc._client.get = AsyncMock(return_value=_mock_response(status_code=401, text_body="unauthorized"))
        with pytest.raises(OrcaCloudAuthError):
            await svc.introspect()


# ---------------------------------------------------------------------------
# Profile pull
# ---------------------------------------------------------------------------


class TestListProfiles:
    @pytest.mark.asyncio
    async def test_pull_response_upserts_extracted(self, svc):
        svc.access_token = "oc_ext_A"
        svc.token_expiry = datetime.now(timezone.utc) + timedelta(hours=12)
        svc._client.get = AsyncMock(
            return_value=_mock_response(
                json_data={
                    "next_cursor": 12345,
                    "upserts": [
                        {"id": "a", "name": "A", "content": {"x": 1}},
                        {"id": "b", "name": "B", "content": {"x": 2}},
                    ],
                    "deletes": ["zzz"],
                }
            )
        )
        result = await svc.list_profiles()
        assert [p["id"] for p in result] == ["a", "b"]

    @pytest.mark.asyncio
    async def test_pull_hits_external_path_without_cursor(self, svc):
        """Regression guard: the list must hit the EXTERNAL sync path
        (``/api/v1/external/sync/pull``, not the first-party ``/api/v1/sync``)
        with no ``?cursor=`` — ``cursor=0`` trips ``410 cursor_too_old``."""
        svc.access_token = "oc_ext_A"
        svc.token_expiry = datetime.now(timezone.utc) + timedelta(hours=12)
        svc._client.get = AsyncMock(return_value=_mock_response(json_data={"upserts": [], "deletes": []}))
        await svc.list_profiles()
        called_url = svc._client.get.call_args.args[0]
        assert called_url.endswith("/api/v1/external/sync/pull")
        assert "cursor" not in called_url
        assert "params" not in svc._client.get.call_args.kwargs

    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self, svc):
        svc.access_token = "oc_ext_A"
        svc.token_expiry = datetime.now(timezone.utc) + timedelta(hours=12)
        svc._client.get = AsyncMock(return_value=_mock_response(status_code=401, text_body="nope"))
        with pytest.raises(OrcaCloudAuthError):
            await svc.list_profiles()

    @pytest.mark.asyncio
    async def test_410_cursor_too_old_raises(self, svc):
        svc.access_token = "oc_ext_A"
        svc.token_expiry = datetime.now(timezone.utc) + timedelta(hours=12)
        svc._client.get = AsyncMock(return_value=_mock_response(status_code=410, json_data={"error": "cursor_too_old"}))
        with pytest.raises(OrcaCloudError, match="cursor too old"):
            await svc.list_profiles()

    @pytest.mark.asyncio
    async def test_bare_list_response_tolerated(self, svc):
        svc.access_token = "oc_ext_A"
        svc.token_expiry = datetime.now(timezone.utc) + timedelta(hours=12)
        svc._client.get = AsyncMock(return_value=_mock_response(json_data=[{"id": "a", "name": "A"}]))
        assert [p["id"] for p in await svc.list_profiles()] == ["a"]


class TestGetProfile:
    @pytest.mark.asyncio
    async def test_returns_matching_profile_with_content(self, svc):
        svc.access_token = "oc_ext_A"
        svc.token_expiry = datetime.now(timezone.utc) + timedelta(hours=12)
        svc._client.get = AsyncMock(
            return_value=_mock_response(
                json_data={
                    "upserts": [
                        {"id": "a", "name": "A", "content": {"foo": 1}},
                        {"id": "target", "name": "Target", "content": {"hit": True}},
                    ],
                    "deletes": [],
                }
            )
        )
        profile = await svc.get_profile("target")
        assert profile["id"] == "target"
        assert profile["content"] == {"hit": True}

    @pytest.mark.asyncio
    async def test_not_found_raises(self, svc):
        svc.access_token = "oc_ext_A"
        svc.token_expiry = datetime.now(timezone.utc) + timedelta(hours=12)
        svc._client.get = AsyncMock(
            return_value=_mock_response(json_data={"upserts": [{"id": "a", "name": "A"}], "deletes": []})
        )
        with pytest.raises(OrcaCloudError, match="not found"):
            await svc.get_profile("missing")
