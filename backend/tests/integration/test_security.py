"""Security tests for the 8 coverage gaps identified in the maintainer review.

Gap 1: encryption.py has zero tests
Gap 2: JWT revocation (revoke_jti, is_jti_revoked, _is_token_fresh) untested
Gap 3: OIDC exchange token replay untested
Gap 4: OIDC email_verified claim handling untested
Gap 5: Email OTP max-attempts invalidation untested
Gap 6: OIDC callback error redirects (SSRF protection) undertested
Gap 7: Login rate limiting untested
Gap 8: challenge_id cookie binding untested
"""

from __future__ import annotations

import base64
import secrets
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.auth_ephemeral import AuthEphemeralToken
from backend.app.models.user import User

AUTH_SETUP_URL = "/api/v1/auth/setup"
LOGIN_URL = "/api/v1/auth/login"
LOGOUT_URL = "/api/v1/auth/logout"
ME_URL = "/api/v1/auth/me"


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _setup_and_login(client: AsyncClient, username: str, password: str) -> str:
    await client.post(
        AUTH_SETUP_URL,
        json={"auth_enabled": True, "admin_username": username, "admin_password": password},
    )
    resp = await client.post(LOGIN_URL, json={"username": username, "password": password})
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _make_test_rsa_key():
    def _b64url(n: int, length: int) -> str:
        return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    pub_numbers = private_key.public_key().public_numbers()
    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": "test-kid-1",
                "n": _b64url(pub_numbers.n, 256),
                "e": _b64url(pub_numbers.e, 3),
            }
        ]
    }
    return private_pem, jwks


# ===========================================================================
# Gap 1: encryption.py unit tests
# ===========================================================================


class TestEncryption:
    """encrypt/decrypt round-trips, plaintext passthrough, RuntimeError on missing key."""

    def test_encrypt_decrypt_roundtrip_with_key(self):
        from cryptography.fernet import Fernet

        test_key = Fernet.generate_key().decode()

        import backend.app.core.encryption as enc_mod

        original = enc_mod._fernet_instance
        original_warn = enc_mod._warn_shown
        try:
            enc_mod._fernet_instance = None
            enc_mod._warn_shown = False
            with patch.dict("os.environ", {"MFA_ENCRYPTION_KEY": test_key}):
                ciphertext = enc_mod.mfa_encrypt("my-totp-secret")
                assert ciphertext.startswith("fernet:")
                assert enc_mod.mfa_decrypt(ciphertext) == "my-totp-secret"
        finally:
            enc_mod._fernet_instance = original
            enc_mod._warn_shown = original_warn

    def test_plaintext_passthrough_without_key(self):
        import backend.app.core.encryption as enc_mod

        original = enc_mod._fernet_instance
        original_warn = enc_mod._warn_shown
        try:
            enc_mod._fernet_instance = None
            enc_mod._warn_shown = False
            with patch.dict("os.environ", {}, clear=True):
                env = {k: v for k, v in __import__("os").environ.items() if k != "MFA_ENCRYPTION_KEY"}
                with patch.dict("os.environ", env, clear=True):
                    result = enc_mod.mfa_encrypt("plaintext-secret")
                    assert result == "plaintext-secret"
                    assert enc_mod.mfa_decrypt("plaintext-secret") == "plaintext-secret"
        finally:
            enc_mod._fernet_instance = original
            enc_mod._warn_shown = original_warn

    def test_decrypt_raises_runtime_error_without_key_for_encrypted_value(self):
        import backend.app.core.encryption as enc_mod

        original = enc_mod._fernet_instance
        original_warn = enc_mod._warn_shown
        try:
            enc_mod._fernet_instance = None
            enc_mod._warn_shown = False
            # A value with the fernet: prefix but no key configured
            with patch.dict("os.environ", {}, clear=True):
                env = {k: v for k, v in __import__("os").environ.items() if k != "MFA_ENCRYPTION_KEY"}
                with patch.dict("os.environ", env, clear=True):
                    with pytest.raises(RuntimeError, match="MFA_ENCRYPTION_KEY must be set"):
                        enc_mod.mfa_decrypt("fernet:gAAAAA-fake-ciphertext")
        finally:
            enc_mod._fernet_instance = original
            enc_mod._warn_shown = original_warn


# ===========================================================================
# Gap 2: JWT revocation — revoke_jti, is_jti_revoked, _is_token_fresh, /me
# ===========================================================================


class TestJWTRevocation:
    """JWT revocation and token freshness checks."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_revoke_jti_and_is_jti_revoked(self, async_client: AsyncClient, db_session: AsyncSession):
        """revoke_jti stores the JTI; is_jti_revoked returns True afterwards."""
        from backend.app.core.auth import is_jti_revoked, revoke_jti

        test_jti = secrets.token_urlsafe(16)
        expires = datetime.now(timezone.utc) + timedelta(hours=1)

        assert not await is_jti_revoked(test_jti)
        await revoke_jti(test_jti, expires, username="testuser")
        assert await is_jti_revoked(test_jti)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_revoke_jti_idempotent(self, async_client: AsyncClient):
        """Double-revocation of the same JTI should not raise."""
        from backend.app.core.auth import is_jti_revoked, revoke_jti

        jti = secrets.token_urlsafe(16)
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        await revoke_jti(jti, expires)
        await revoke_jti(jti, expires)  # must not raise
        assert await is_jti_revoked(jti)

    def test_is_token_fresh_rejects_none_iat(self):
        """_is_token_fresh returns False when iat is None (I1 hard cutoff)."""
        from backend.app.core.auth import _is_token_fresh

        user = MagicMock()
        user.password_changed_at = None
        assert _is_token_fresh(None, user) is False

    def test_is_token_fresh_rejects_token_before_password_change(self):
        """_is_token_fresh returns False when iat predates password_changed_at."""
        from backend.app.core.auth import _is_token_fresh

        now = datetime.now(timezone.utc)
        user = MagicMock()
        user.password_changed_at = now
        old_iat = (now - timedelta(hours=1)).timestamp()
        assert _is_token_fresh(old_iat, user) is False

    def test_is_token_fresh_accepts_token_after_password_change(self):
        """_is_token_fresh returns True when iat is after password_changed_at."""
        from backend.app.core.auth import _is_token_fresh

        now = datetime.now(timezone.utc)
        user = MagicMock()
        user.password_changed_at = now - timedelta(hours=1)
        recent_iat = now.timestamp()
        assert _is_token_fresh(recent_iat, user) is True

    def test_is_token_fresh_returns_true_when_no_password_change(self):
        """_is_token_fresh returns True when password_changed_at is None (I2 migration not yet run)."""
        from backend.app.core.auth import _is_token_fresh

        user = MagicMock()
        user.password_changed_at = None
        assert _is_token_fresh(time.time(), user) is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_me_endpoint_rejects_token_after_logout(self, async_client: AsyncClient):
        """After logout, the bearer token must be rejected by /me (B1 + revocation)."""
        token = await _setup_and_login(async_client, "sec_logout_me", "sec_logout_me1")

        # Token works before logout
        me_resp = await async_client.get(ME_URL, headers=_auth_header(token))
        assert me_resp.status_code == 200

        # Logout
        logout_resp = await async_client.post(LOGOUT_URL, headers=_auth_header(token))
        assert logout_resp.status_code == 200

        # Token must now be rejected
        me_after = await async_client.get(ME_URL, headers=_auth_header(token))
        assert me_after.status_code == 401


# ===========================================================================
# Gap 3: OIDC exchange token replay
# ===========================================================================


class TestOIDCExchangeReplay:
    """A single-use OIDC exchange token cannot be redeemed twice."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_exchange_token_is_single_use(self, async_client: AsyncClient, db_session: AsyncSession):
        """The second call to /oidc/exchange with the same token returns 401."""
        exchange_token = secrets.token_urlsafe(32)
        db_session.add(
            AuthEphemeralToken(
                token=exchange_token,
                token_type="oidc_exchange",
                username="oidc_replay_user",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
        )
        await db_session.commit()

        # Seed the user so the exchange can resolve it
        from backend.app.core.database import async_session, seed_default_groups
        from backend.app.core.auth import get_password_hash

        async with async_session() as db:
            result = await db.execute(
                __import__("sqlalchemy").select(User).where(User.username == "oidc_replay_user")
            )
            if result.scalar_one_or_none() is None:
                db.add(User(
                    username="oidc_replay_user",
                    password_hash=get_password_hash("pw"),
                    is_active=True,
                ))
                await db.commit()

        first = await async_client.post("/api/v1/auth/oidc/exchange", json={"oidc_token": exchange_token})
        assert first.status_code == 200

        second = await async_client.post("/api/v1/auth/oidc/exchange", json={"oidc_token": exchange_token})
        assert second.status_code == 401


# ===========================================================================
# Gap 4: OIDC email_verified claim handling
# ===========================================================================


class TestOIDCEmailVerified:
    """email_verified: False/absent must not link OIDC identity to an existing email."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unverified_email_does_not_link_to_existing_user(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """If email_verified is False, the OIDC callback must not auto-link by email."""
        private_pem, jwks_data = _make_test_rsa_key()
        issuer = "https://idp.evtest.example.com"
        client_id = "ev-client"
        nonce = secrets.token_urlsafe(16)
        now = int(time.time())

        id_token = pyjwt.encode(
            {
                "sub": "ev-sub-new",
                "iss": issuer,
                "aud": client_id,
                "nonce": nonce,
                "email": "existing@example.com",
                "email_verified": False,  # <-- must be ignored
                "iat": now,
                "exp": now + 300,
            },
            private_pem,
            algorithm="RS256",
            headers={"kid": "test-kid-1"},
        )

        admin_token = await _setup_and_login(async_client, "ev_admin", "ev_admin1")

        # Create existing user with the same email (use strong password for validator)
        create_user_resp = await async_client.post(
            "/api/v1/users",
            json={"username": "existing_email_user", "password": "Str0ng!Pass", "email": "existing@example.com"},
            headers=_auth_header(admin_token),
        )
        assert create_user_resp.status_code in (200, 201), create_user_resp.json()

        # Create OIDC provider
        create_resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "EV-IdP",
                "issuer_url": issuer,
                "client_id": client_id,
                "client_secret": "secret",
                "scopes": "openid email",
                "is_enabled": True,
                "auto_create_users": True,
            },
            headers=_auth_header(admin_token),
        )
        assert create_resp.status_code == 201
        provider_id = create_resp.json()["id"]

        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(48)
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

        discovery_doc = {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/auth",
            "token_endpoint": f"{issuer}/token",
            "jwks_uri": f"{issuer}/.well-known/jwks.json",
        }

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

        class _MockHttpxClientEV:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

            async def get(self, url, **kwargs):
                if "jwks" in url:
                    return _MockResp(jwks_data)
                return _MockResp(discovery_doc)

            async def post(self, url, **kwargs):
                return _MockResp({"access_token": "mock", "token_type": "Bearer", "id_token": id_token})

        with patch("backend.app.api.routes.mfa.httpx.AsyncClient", _MockHttpxClientEV):
            callback_resp = await async_client.get(
                f"/api/v1/auth/oidc/callback?code=test-code&state={state}",
                follow_redirects=False,
            )

        # Callback must NOT link to the existing_email_user — a new user is created
        # instead (because the email claim was ignored due to email_verified=False).
        # Either a new user is provisioned (redirect with oidc_token) or the callback
        # fails.  In either case, the existing user must not have an OIDC link.
        from sqlalchemy import select as sa_select
        from backend.app.models.oidc_provider import UserOIDCLink

        link_result = await db_session.execute(
            sa_select(UserOIDCLink)
            .join(User, UserOIDCLink.user_id == User.id)
            .where(User.email == "existing@example.com")
        )
        link = link_result.scalar_one_or_none()
        assert link is None, "Existing user must not be auto-linked when email_verified is False"


# ===========================================================================
# Gap 5: Email OTP max-attempts invalidation
# ===========================================================================


class TestEmailOTPMaxAttempts:
    """After MAX_ATTEMPTS wrong codes, the OTP is permanently invalidated."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_email_otp_invalidated_after_max_attempts(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        from passlib.context import CryptContext
        from sqlalchemy import select as sa_select

        from backend.app.models.user_otp_code import UserOTPCode

        _pwd_ctx = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

        admin_token = await _setup_and_login(async_client, "otp_max_admin", "otp_max_admin1")

        # Enable email OTP for admin user
        result = await db_session.execute(sa_select(User).where(User.username == "otp_max_admin"))
        user = result.scalar_one()
        user.email = "otpmax@example.com"
        await db_session.commit()

        setup_code = "123456"
        from backend.app.models.auth_ephemeral import AuthEphemeralToken as AET

        setup_token = secrets.token_urlsafe(32)
        db_session.add(
            AET(
                token=setup_token,
                token_type="email_otp_setup",
                username="otp_max_admin",
                nonce=_pwd_ctx.hash(setup_code),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
        )
        await db_session.commit()
        await async_client.post(
            "/api/v1/auth/2fa/email/enable/confirm",
            json={"setup_token": setup_token, "code": setup_code},
            headers=_auth_header(admin_token),
        )

        # Login to get pre_auth_token
        login_resp = await async_client.post(LOGIN_URL, json={"username": "otp_max_admin", "password": "otp_max_admin1"})
        pre_auth_token = login_resp.json()["pre_auth_token"]

        # Insert an OTP record directly (bypassing SMTP)
        real_code = "654321"
        otp = UserOTPCode(
            user_id=user.id,
            code_hash=_pwd_ctx.hash(real_code),
            attempts=0,
            used=False,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        db_session.add(otp)
        await db_session.commit()

        # Submit MAX_ATTEMPTS wrong codes
        from backend.app.api.routes.mfa import MAX_2FA_ATTEMPTS

        for _ in range(MAX_2FA_ATTEMPTS):
            r = await async_client.post(
                "/api/v1/auth/2fa/verify",
                json={"pre_auth_token": pre_auth_token, "code": "000000", "method": "email"},
            )
            # Each attempt must fail with 401
            assert r.status_code == 401

        # After max attempts, the correct code is also rejected (either OTP
        # invalidated → 401, or rate limit hit → 429). Either means locked out.
        final = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth_token, "code": real_code, "method": "email"},
        )
        assert final.status_code in (401, 429), f"Expected lockout, got {final.status_code}: {final.json()}"


# ===========================================================================
# Gap 6: OIDC callback SSRF protection — invalid authorization_endpoint scheme
# ===========================================================================


class TestOIDCSSRFProtection:
    """authorization_endpoint with non-http(s) scheme must be rejected."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_invalid_authorization_endpoint_scheme_rejected(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        issuer = "https://idp.ssrf.example.com"
        client_id = "ssrf-client"
        nonce = secrets.token_urlsafe(16)

        admin_token = await _setup_and_login(async_client, "ssrf_admin", "ssrf_admin1")
        create_resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "SSRF-IdP",
                "issuer_url": issuer,
                "client_id": client_id,
                "client_secret": "secret",
                "scopes": "openid",
                "is_enabled": True,
                "auto_create_users": False,
            },
            headers=_auth_header(admin_token),
        )
        assert create_resp.status_code == 201
        provider_id = create_resp.json()["id"]

        # Discovery doc returns a javascript: authorization_endpoint
        malicious_discovery = {
            "issuer": issuer,
            "authorization_endpoint": "javascript:alert(1)",  # <-- malicious
            "token_endpoint": f"{issuer}/token",
            "jwks_uri": f"{issuer}/.well-known/jwks.json",
        }

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

        class _MockHttpxClientSSRF:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

            async def get(self, url, **kwargs):
                return _MockResp(malicious_discovery)

            async def post(self, url, **kwargs):
                return _MockResp({})

        with patch("backend.app.api.routes.mfa.httpx.AsyncClient", _MockHttpxClientSSRF):
            # oidc_authorize uses a path parameter, not query param
            authorize_resp = await async_client.get(
                f"/api/v1/auth/oidc/authorize/{provider_id}",
                follow_redirects=False,
            )

        # Must be rejected with 502 — B2 guard rejects invalid authorization_endpoint scheme
        assert authorize_resp.status_code == 502, authorize_resp.json()
        detail = authorize_resp.json().get("detail", "").lower()
        assert "authorization_endpoint" in detail or "invalid" in detail


# ===========================================================================
# Gap 7: Login rate limiting
# ===========================================================================


class TestLoginRateLimiting:
    """10+ failed logins for the same username must return 429."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_excessive_failed_logins_return_429(self, async_client: AsyncClient):
        from backend.app.api.routes.mfa import MAX_LOGIN_ATTEMPTS

        # Setup auth but do NOT log in
        await async_client.post(
            AUTH_SETUP_URL,
            json={"auth_enabled": True, "admin_username": "ratelimit_user", "admin_password": "ratelimit_pw1"},
        )

        status_codes = []
        for _ in range(MAX_LOGIN_ATTEMPTS + 2):
            resp = await async_client.post(
                LOGIN_URL,
                json={"username": "ratelimit_user", "password": "wrong_password"},
            )
            status_codes.append(resp.status_code)

        # The last attempts must be 429 (Too Many Requests)
        assert status_codes[-1] == 429, f"Expected 429 after {MAX_LOGIN_ATTEMPTS} failures, got: {status_codes}"


# ===========================================================================
# Gap 8: challenge_id cookie binding
# ===========================================================================


class TestChallengeIdCookieBinding:
    """A pre-auth token stolen from session A cannot be used from session B."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_pre_auth_token_rejected_without_matching_cookie(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        import pyotp

        from passlib.context import CryptContext

        _pwd_ctx = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

        # Set up user with TOTP
        admin_token = await _setup_and_login(async_client, "cookie_bind_user", "cookie_bind_pw1")

        secret = pyotp.random_base32()
        totp_obj = pyotp.TOTP(secret)
        from backend.app.models.user_totp import UserTOTP
        from sqlalchemy import select as sa_select

        result = await db_session.execute(sa_select(User).where(User.username == "cookie_bind_user"))
        user = result.scalar_one()
        db_session.add(UserTOTP(user_id=user.id, secret=secret, is_enabled=True, backup_codes=[]))
        await db_session.commit()

        # Login from "session A" — gets a pre_auth_token and a 2fa_challenge cookie
        login_resp = await async_client.post(
            LOGIN_URL, json={"username": "cookie_bind_user", "password": "cookie_bind_pw1"}
        )
        assert login_resp.status_code == 200
        assert login_resp.json()["requires_2fa"] is True
        pre_auth_token = login_resp.json()["pre_auth_token"]
        # The async_client jar now holds the 2fa_challenge cookie for session A

        # Simulate session B by creating a new client WITHOUT the cookie
        from httpx import ASGITransport, AsyncClient as FreshClient
        from backend.app.main import app

        async with FreshClient(transport=ASGITransport(app=app), base_url="http://test") as session_b:
            # Attempt to use session A's pre_auth_token from session B (no cookie)
            verify_resp = await session_b.post(
                "/api/v1/auth/2fa/verify",
                json={
                    "pre_auth_token": pre_auth_token,
                    "code": totp_obj.now(),
                    "method": "totp",
                },
            )
            # Must be rejected — pre_auth_token is bound to session A's cookie
            assert verify_resp.status_code == 401, (
                f"Expected 401 for token replay from cookieless session, got {verify_resp.status_code}: "
                f"{verify_resp.json()}"
            )
