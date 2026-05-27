"""E2E test for issue #1285: SSO user can re-login after admin deletion.

Reproduces the exact symptom from the issue: a user logs in via OIDC
(auto_create_users=True), gets created, is then deleted by the admin, and
attempts to log in again. With the fix in delete_user (UserOIDCLink cleanup)
+ the orphan-cleanup migration, the second OIDC callback must trigger
auto_create_users and produce a fresh user — instead of redirecting to
"account_inactive" because of the orphan link.
"""

from __future__ import annotations

import base64
import secrets
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.auth_ephemeral import AuthEphemeralToken
from backend.app.models.oidc_provider import UserOIDCLink
from backend.app.models.user import User


def _make_rsa_key():
    """Throwaway RSA + JWKS for the mocked IdP."""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    pub = priv.public_key().public_numbers()

    def _b64url(n: int, length: int) -> str:
        return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()

    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": "test-kid-1",
                "n": _b64url(pub.n, 256),
                "e": _b64url(pub.e, 3),
            }
        ]
    }
    return pem, jwks


class _MockResp:
    def __init__(self, data):
        self._data = data
        self.status_code = 200
        self.is_success = True
        self.text = str(data)

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


def _mock_httpx_factory(discovery_doc, jwks_data, token_response):
    class _MockHttpxClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, **kwargs):
            if "jwks" in url:
                return _MockResp(jwks_data)
            return _MockResp(discovery_doc)

        async def post(self, url, **kwargs):
            return _MockResp(token_response)

    return _MockHttpxClient


async def _trigger_oidc_callback(
    async_client: AsyncClient,
    db_session: AsyncSession,
    provider_id: int,
    issuer: str,
    client_id: str,
    private_pem: bytes,
    jwks_data: dict,
    *,
    sub: str,
    email: str,
) -> str:
    """Run a full mocked OIDC callback and return the resulting access token."""
    nonce = secrets.token_urlsafe(16)
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(48)

    now = int(time.time())
    id_token = pyjwt.encode(
        {
            "sub": sub,
            "iss": issuer,
            "aud": client_id,
            "nonce": nonce,
            "email": email,
            "email_verified": True,
            "iat": now,
            "exp": now + 300,
        },
        private_pem,
        algorithm="RS256",
        headers={"kid": "test-kid-1"},
    )

    db_session.add(
        AuthEphemeralToken(
            token=state,
            token_type="oidc_state",
            provider_id=provider_id,
            nonce=nonce,
            code_verifier=code_verifier,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
    )
    await db_session.commit()

    discovery = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/auth",
        "token_endpoint": f"{issuer}/token",
        "jwks_uri": f"{issuer}/.well-known/jwks.json",
    }
    token_response = {
        "access_token": "mock-access",
        "token_type": "Bearer",
        "id_token": id_token,
    }

    with patch(
        "backend.app.api.routes.mfa.httpx.AsyncClient",
        _mock_httpx_factory(discovery, jwks_data, token_response),
    ):
        callback_resp = await async_client.get(
            f"/api/v1/auth/oidc/callback?code=test-code&state={state}",
            follow_redirects=False,
        )

    assert callback_resp.status_code == 302, callback_resp.text
    location = callback_resp.headers.get("location", "")
    assert "oidc_token=" in location, f"Expected oidc_token in redirect, got: {location}"

    exchange_token = location.split("oidc_token=")[1].split("&")[0]
    exchange_resp = await async_client.post(
        "/api/v1/auth/oidc/exchange",
        json={"oidc_token": exchange_token},
    )
    assert exchange_resp.status_code == 200, exchange_resp.text
    return exchange_resp.json()["access_token"]


class TestOidcReloginAfterDelete:
    """Issue #1285: SSO user must be recreatable after admin deletion."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_relogin_after_delete_recreates_user_via_auto_create(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """User created via OIDC → deleted by admin → second OIDC login creates a new user.

        Without the delete_user UserOIDCLink-cleanup fix, the second callback finds
        the orphan link, fails to load the now-deleted user, and redirects to
        ``account_inactive`` — never reaching auto_create_users.
        """
        private_pem, jwks = _make_rsa_key()
        issuer = "https://idp.relogin-test.example.com"
        client_id = "relogin-test-client"
        sub = "oidc-sub-relogin-1285"
        email = "relogin@example.com"

        # Admin setup + create OIDC provider
        await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "reloginadm",
                "admin_password": "AdminPass1!",
            },
        )
        login_resp = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "reloginadm", "password": "AdminPass1!"},
        )
        admin_token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        create_resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "ReloginIdP",
                "issuer_url": issuer,
                "client_id": client_id,
                "client_secret": "test-secret",
                "scopes": "openid email profile",
                "is_enabled": True,
                "auto_create_users": True,
            },
            headers=headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        provider_id = create_resp.json()["id"]

        # ── First OIDC login: creates user + link ──
        await _trigger_oidc_callback(
            async_client,
            db_session,
            provider_id,
            issuer,
            client_id,
            private_pem,
            jwks,
            sub=sub,
            email=email,
        )

        await db_session.commit()
        first_user_row = await db_session.execute(select(User).where(User.email == email))
        first_user = first_user_row.scalar_one()
        first_user_id = first_user.id
        first_user_created_at = first_user.created_at

        first_link_row = await db_session.execute(select(UserOIDCLink).where(UserOIDCLink.provider_user_id == sub))
        assert first_link_row.scalar_one().user_id == first_user_id

        # ── Admin deletes the user ──
        del_resp = await async_client.delete(
            f"/api/v1/users/{first_user_id}",
            headers=headers,
        )
        assert del_resp.status_code == 204, del_resp.text

        await db_session.commit()
        # With the fix the orphan link is gone too — verifying because that
        # is exactly the precondition for auto_create to fire on retry.
        link_after_delete = await db_session.execute(select(UserOIDCLink).where(UserOIDCLink.provider_user_id == sub))
        assert link_after_delete.scalar_one_or_none() is None, (
            "Orphan UserOIDCLink left after delete — would block re-login per #1285"
        )
        # And the user row itself is gone (#1285 prerequisite).
        user_after_delete = await db_session.execute(select(User).where(User.email == email))
        assert user_after_delete.scalar_one_or_none() is None

        # ── Second OIDC login with the same sub: auto_create must run again ──
        # The helper already asserts a 302 with oidc_token=… — that alone proves
        # auto_create fired (otherwise the callback would have redirected to
        # /?oidc_error=account_inactive and the helper would have failed).
        await _trigger_oidc_callback(
            async_client,
            db_session,
            provider_id,
            issuer,
            client_id,
            private_pem,
            jwks,
            sub=sub,
            email=email,
        )

        await db_session.commit()
        second_row = await db_session.execute(select(User).where(User.email == email))
        second_user = second_row.scalar_one()
        # SQLite recycles primary-key ids when AUTOINCREMENT is not declared, so
        # comparing ids is not a reliable freshness signal across delete+recreate.
        # The decisive proof: a new user row was created (post-delete) and a
        # fresh link points at it. created_at must not be earlier than the
        # original — equality is acceptable on fast machines where seconds match.
        assert second_user.created_at >= first_user_created_at, (
            f"Re-created user has earlier created_at ({second_user.created_at}) "
            f"than the deleted original ({first_user_created_at}) — bug regression"
        )

        # And a fresh link for the new user
        link_after = await db_session.execute(select(UserOIDCLink).where(UserOIDCLink.provider_user_id == sub))
        assert link_after.scalar_one().user_id == second_user.id
