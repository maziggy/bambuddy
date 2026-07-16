"""Tests for Bambu Cloud sign-in expiry detection.

Bambu's access token is opaque — no readable expiry — and Bambuddy does not
persist the refresh token, so the only authority on whether a stored token still
works is Bambu itself. Bambuddy used to pretend otherwise: ``set_token()``
stamped ``token_expiry = now + 30 days`` *every time a stored token was loaded*,
which reset the expiry check on every request and made ``is_authenticated``
incapable of ever returning False. ``/cloud/status`` therefore reported
"connected" indefinitely while every cloud call 401'd, and the user was shown
Bambu's own ``{"error": "Please login."}`` as a toast — on a UI that was
simultaneously telling them they were signed in.

These tests pin: the expiry is no longer invented, a 401 is recorded durably,
a Bambu outage does not masquerade as an expired sign-in, and a fresh login
clears the flag.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from backend.app.api.routes.cloud import (
    CLOUD_EMAIL_KEY,
    CLOUD_REGION_KEY,
    CLOUD_TOKEN_INVALID_KEY,
    CLOUD_TOKEN_KEY,
    clear_token,
    is_cloud_token_invalid,
    store_token,
)
from backend.app.models.settings import Settings
from backend.app.services import bambu_cloud as bc
from backend.app.services.bambu_cloud import BambuCloudService


@pytest.fixture(autouse=True)
def _clear_validation_cache():
    """The validation verdict cache is module-level; don't leak across tests."""
    bc.invalidate_validation_cache()
    yield
    bc.invalidate_validation_cache()


def _service(status_code: int = 200, *, on_auth_failure=None, raises: Exception | None = None):
    svc = BambuCloudService(client=MagicMock(spec=httpx.AsyncClient), on_auth_failure=on_auth_failure)
    resp = MagicMock()
    resp.status_code = status_code
    svc._client.get = AsyncMock(side_effect=raises) if raises else AsyncMock(return_value=resp)
    return svc


class TestNoInventedExpiry:
    def test_set_token_records_no_expiry(self):
        """The bug in one line: this used to be ``now + 30 days``, re-derived on
        every request from a token of entirely unknown age."""
        svc = _service()
        svc.set_token("stored-token-of-unknown-age")
        assert svc.token_expiry is None

    def test_is_authenticated_means_loaded_not_accepted(self):
        """It still answers True for a loaded token — that is all it ever knew.
        The point is that nobody may now read it as "Bambu accepts this"."""
        svc = _service()
        assert svc.is_authenticated is False
        svc.set_token("stored-token")
        assert svc.is_authenticated is True


class TestValidateToken:
    @pytest.mark.asyncio
    async def test_accepted_token_returns_true(self):
        svc = _service(200)
        svc.set_token("good-token")
        assert await svc.validate_token() is True

    @pytest.mark.asyncio
    async def test_rejected_token_returns_false(self):
        svc = _service(401)
        svc.set_token("dead-token")
        assert await svc.validate_token() is False

    @pytest.mark.asyncio
    async def test_no_token_is_not_authenticated(self):
        svc = _service(200)
        assert await svc.validate_token() is False

    @pytest.mark.asyncio
    async def test_network_failure_is_unknown_not_invalid(self):
        """A Bambu outage must never present as "your sign-in expired" — that
        would sign every user out of a perfectly good session."""
        svc = _service(raises=httpx.ConnectError("no route to host"))
        svc.set_token("good-token")
        assert await svc.validate_token() is None

    @pytest.mark.asyncio
    async def test_server_error_is_unknown_not_invalid(self):
        svc = _service(503)
        svc.set_token("good-token")
        assert await svc.validate_token() is None

    @pytest.mark.asyncio
    async def test_cloudflare_challenge_is_unknown_not_invalid(self):
        """418/403 from Bambu's anti-abuse edge means the *request* was refused,
        not the token. Declaring the credential dead there would log users out
        whenever Cloudflare gets suspicious of their IP."""
        svc = _service(418)
        svc.set_token("good-token")
        assert await svc.validate_token() is None

    @pytest.mark.asyncio
    async def test_verdict_is_cached(self):
        """/cloud/status is polled by several components; without the cache each
        render would put a Bambu round-trip in front of the settings page."""
        svc = _service(200)
        svc.set_token("good-token")
        assert await svc.validate_token() is True
        assert await svc.validate_token() is True
        assert svc._client.get.await_count == 1

    @pytest.mark.asyncio
    async def test_cache_is_keyed_per_token(self):
        svc = _service(200)
        svc.set_token("token-a")
        assert await svc.validate_token() is True

        other = _service(401)
        other.set_token("token-b")
        assert await other.validate_token() is False, "a different token must not inherit the cached verdict"

    @pytest.mark.asyncio
    async def test_login_drops_a_cached_rejection(self):
        """Re-login must not leave the user staring at "sign-in expired" for the
        rest of the cache TTL."""
        svc = _service(401)
        svc.set_token("tok")
        assert await svc.validate_token() is False

        fresh = _service(200)
        fresh._set_tokens({"accessToken": "tok"})  # same string, freshly minted upstream
        assert await fresh.validate_token() is True


class TestAuthFailureCallback:
    @pytest.mark.asyncio
    async def test_401_fires_the_callback(self):
        calls: list[int] = []

        async def _cb() -> None:
            calls.append(1)

        svc = _service(401, on_auth_failure=_cb)
        svc.set_token("dead-token")
        await svc.validate_token()
        assert calls == [1]

    @pytest.mark.asyncio
    async def test_reported_once_per_service(self):
        """A route that makes several cloud calls must not write the flag once
        per call."""
        calls: list[int] = []

        async def _cb() -> None:
            calls.append(1)

        svc = _service(401, on_auth_failure=_cb)
        svc.set_token("dead-token")
        resp = MagicMock()
        resp.status_code = 401
        await svc._note_response(resp)
        await svc._note_response(resp)
        await svc._note_response(resp)
        assert calls == [1]

    @pytest.mark.asyncio
    async def test_success_does_not_fire_the_callback(self):
        calls: list[int] = []

        async def _cb() -> None:
            calls.append(1)

        svc = _service(200, on_auth_failure=_cb)
        svc.set_token("good-token")
        await svc.validate_token()
        assert calls == []

    @pytest.mark.asyncio
    async def test_callback_failure_does_not_mask_the_401(self):
        """Recording the dead credential is bookkeeping. If it throws, the caller
        must still get the auth failure it was actually waiting for."""

        async def _cb() -> None:
            raise RuntimeError("database is on fire")

        svc = _service(401, on_auth_failure=_cb)
        svc.set_token("dead-token")
        assert await svc.validate_token() is False


class TestPersistedFlag:
    """Auth-disabled deployments keep cloud credentials in the Settings table."""

    @pytest.mark.asyncio
    async def test_absent_by_default(self, db_session):
        assert await is_cloud_token_invalid(db_session, None) is False

    @pytest.mark.asyncio
    async def test_set_flag_is_read_back(self, db_session):
        db_session.add(Settings(key=CLOUD_TOKEN_INVALID_KEY, value=datetime.now(timezone.utc).isoformat()))
        await db_session.commit()
        assert await is_cloud_token_invalid(db_session, None) is True

    @pytest.mark.asyncio
    async def test_fresh_login_clears_the_flag(self, db_session):
        """Otherwise the new sign-in is reported as expired the instant it's stored."""
        db_session.add(Settings(key=CLOUD_TOKEN_INVALID_KEY, value="2026-07-14T07:00:00+00:00"))
        await db_session.commit()

        await store_token(db_session, "brand-new-token", "user@example.com", "global", None)

        assert await is_cloud_token_invalid(db_session, None) is False

    @pytest.mark.asyncio
    async def test_logout_clears_the_flag(self, db_session):
        for key, value in [
            (CLOUD_TOKEN_KEY, "dead"),
            (CLOUD_EMAIL_KEY, "user@example.com"),
            (CLOUD_REGION_KEY, "global"),
            (CLOUD_TOKEN_INVALID_KEY, "2026-07-14T07:00:00+00:00"),
        ]:
            db_session.add(Settings(key=key, value=value))
        await db_session.commit()

        await clear_token(db_session, None)

        assert await is_cloud_token_invalid(db_session, None) is False


class TestStatusRoute:
    """The endpoint that was lying. ``GET /cloud/status`` drives the "Connected
    as ..." bar on the Profiles page and the green dot in Settings."""

    async def _store(self, db_session, *, invalid: bool = False):
        db_session.add(Settings(key=CLOUD_TOKEN_KEY, value="stored-token"))
        db_session.add(Settings(key=CLOUD_EMAIL_KEY, value="user@example.com"))
        db_session.add(Settings(key=CLOUD_REGION_KEY, value="global"))
        if invalid:
            db_session.add(Settings(key=CLOUD_TOKEN_INVALID_KEY, value="2026-07-14T07:00:00+00:00"))
        await db_session.commit()

    @pytest.mark.asyncio
    async def test_no_token_is_not_expired(self, async_client, db_session):
        body = (await async_client.get("/api/v1/cloud/status")).json()
        assert body["is_authenticated"] is False
        assert body["sign_in_expired"] is False

    @pytest.mark.asyncio
    async def test_token_bambu_rejects_reports_expired(self, async_client, db_session, monkeypatch):
        """The whole bug: a stored token Bambu no longer accepts used to come back
        as ``is_authenticated: true``, forever."""
        await self._store(db_session)
        monkeypatch.setattr(BambuCloudService, "validate_token", AsyncMock(return_value=False))

        body = (await async_client.get("/api/v1/cloud/status")).json()

        assert body["is_authenticated"] is False
        assert body["sign_in_expired"] is True
        assert body["email"] is None

    @pytest.mark.asyncio
    async def test_token_bambu_accepts_reports_connected(self, async_client, db_session, monkeypatch):
        await self._store(db_session)
        monkeypatch.setattr(BambuCloudService, "validate_token", AsyncMock(return_value=True))

        body = (await async_client.get("/api/v1/cloud/status")).json()

        assert body["is_authenticated"] is True
        assert body["sign_in_expired"] is False
        assert body["email"] == "user@example.com"

    @pytest.mark.asyncio
    async def test_bambu_unreachable_keeps_the_user_signed_in(self, async_client, db_session, monkeypatch):
        """Unknown is not invalid. A Bambu outage must not log the whole install
        out of the cloud."""
        await self._store(db_session)
        monkeypatch.setattr(BambuCloudService, "validate_token", AsyncMock(return_value=None))

        body = (await async_client.get("/api/v1/cloud/status")).json()

        assert body["is_authenticated"] is True
        assert body["sign_in_expired"] is False

    @pytest.mark.asyncio
    async def test_bambu_unreachable_does_not_resurrect_a_known_dead_token(self, async_client, db_session, monkeypatch):
        """...but "unknown" must fall back to what we last knew, not to True."""
        await self._store(db_session, invalid=True)
        monkeypatch.setattr(BambuCloudService, "validate_token", AsyncMock(return_value=None))

        body = (await async_client.get("/api/v1/cloud/status")).json()

        assert body["is_authenticated"] is False
        assert body["sign_in_expired"] is True

    @pytest.mark.asyncio
    async def test_known_dead_token_does_not_re_ask_bambu(self, async_client, db_session, monkeypatch):
        """Only a new login can revive it, and that clears the flag — so polling
        Bambu on every status call would be pure waste."""
        await self._store(db_session, invalid=True)
        validate = AsyncMock(return_value=False)
        monkeypatch.setattr(BambuCloudService, "validate_token", validate)

        await async_client.get("/api/v1/cloud/status")

        validate.assert_not_awaited()
