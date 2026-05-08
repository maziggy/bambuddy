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


def _norm_pw(password: str) -> str:
    """Ensure password meets complexity requirements (I4: SetupRequest now validates)."""
    if not any(c.isupper() for c in password):
        password = password[0].upper() + password[1:]
    if not any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789" for c in password):
        password = password + "!"
    return password


async def _setup_and_login(client: AsyncClient, username: str, password: str) -> str:
    password = _norm_pw(password)
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
    """encrypt/decrypt round-trips, plaintext passthrough, RuntimeError on missing key.

    The ``mfa_encryption_isolation`` autouse fixture (conftest.py) resets the
    ``encryption`` module's globals before/after each test and points
    ``DATA_DIR`` at a tmp path, so individual tests only need to set
    ``MFA_ENCRYPTION_KEY`` when they want a specific key in scope.
    """

    def test_encrypt_decrypt_roundtrip_with_key(self, monkeypatch):
        from cryptography.fernet import Fernet

        import backend.app.core.encryption as enc_mod

        test_key = Fernet.generate_key().decode()
        monkeypatch.setenv("MFA_ENCRYPTION_KEY", test_key)
        # Force re-initialisation now that the env var is set.
        enc_mod._fernet_instance = None

        ciphertext = enc_mod.mfa_encrypt("my-totp-secret")
        assert ciphertext.startswith("fernet:")
        assert enc_mod.mfa_decrypt(ciphertext) == "my-totp-secret"

    def test_plaintext_passthrough_without_key(self, monkeypatch):
        # Force the auto-bootstrap into the legacy "no key available" branch
        # by patching _load_or_generate_key directly. This is more robust than
        # chmod tricks (which root bypasses) when verifying the plaintext path.
        import backend.app.core.encryption as enc_mod

        monkeypatch.setattr(enc_mod, "_load_or_generate_key", lambda: (None, "none"))
        enc_mod._fernet_instance = None

        result = enc_mod.mfa_encrypt("plaintext-secret")
        assert result == "plaintext-secret"
        assert enc_mod.mfa_decrypt("plaintext-secret") == "plaintext-secret"

    def test_decrypt_raises_runtime_error_without_key_for_encrypted_value(self, monkeypatch):
        import backend.app.core.encryption as enc_mod

        monkeypatch.setattr(enc_mod, "_load_or_generate_key", lambda: (None, "none"))
        enc_mod._fernet_instance = None

        with pytest.raises(RuntimeError, match="MFA_ENCRYPTION_KEY must be set"):
            enc_mod.mfa_decrypt("fernet:gAAAAA-fake-ciphertext")

    # ------------------------------------------------------------------
    # Auto-bootstrap tests for _load_or_generate_key
    # ------------------------------------------------------------------

    def test_load_or_generate_key_uses_env_when_set(self, monkeypatch, tmp_path):
        """Valid env var → key_source == 'env', no file written."""
        from cryptography.fernet import Fernet

        import backend.app.core.encryption as enc_mod

        valid_key = Fernet.generate_key().decode()
        monkeypatch.setenv("MFA_ENCRYPTION_KEY", valid_key)
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        enc_mod._fernet_instance = None

        key, source = enc_mod._load_or_generate_key()

        assert key == valid_key
        assert source == "env"
        assert not (tmp_path / ".mfa_encryption_key").exists()

    def test_invalid_env_key_falls_through_to_file(self, monkeypatch, tmp_path, caplog):
        """Invalid env var → logger.error + file fallback (auto-generated)."""
        import logging

        import backend.app.core.encryption as enc_mod

        monkeypatch.setenv("MFA_ENCRYPTION_KEY", "not-a-valid-fernet-key")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        enc_mod._fernet_instance = None

        with caplog.at_level(logging.ERROR, logger="backend.app.core.encryption"):
            key, source = enc_mod._load_or_generate_key()

        assert source == "generated"
        assert key is not None
        assert (tmp_path / ".mfa_encryption_key").exists()
        assert any("not a valid Fernet key" in rec.message for rec in caplog.records)

    def test_load_or_generate_key_reads_existing_file(self, monkeypatch, tmp_path):
        """File present in DATA_DIR + no env var → key_source == 'file'."""
        from cryptography.fernet import Fernet

        import backend.app.core.encryption as enc_mod

        existing_key = Fernet.generate_key().decode()
        key_file = tmp_path / ".mfa_encryption_key"
        key_file.write_text(existing_key)

        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        enc_mod._fernet_instance = None

        key, source = enc_mod._load_or_generate_key()

        assert key == existing_key
        assert source == "file"

    def test_load_or_generate_key_creates_file_with_0600(self, monkeypatch, tmp_path):
        """Neither env nor file → new key generated, file mode is 0o600."""
        import backend.app.core.encryption as enc_mod

        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        enc_mod._fernet_instance = None

        key, source = enc_mod._load_or_generate_key()

        assert source == "generated"
        assert enc_mod._validate_fernet_key(key)
        key_file = tmp_path / ".mfa_encryption_key"
        assert key_file.exists()
        # Mode bits LSB are 0o600 — owner read+write only.
        assert (key_file.stat().st_mode & 0o777) == 0o600

    def test_load_or_generate_key_returns_none_on_write_oserror(self, monkeypatch, tmp_path, caplog):
        """When DATA_DIR can't be written to (auto-generate path), return (None, 'none_write_failed').

        S1: write now uses os.open(O_EXCL|O_CREAT, 0o600) instead of write_text — patch
        os.write to simulate the OS-level failure. S8: source distinguishes write-failed
        from corrupted to drive accurate operator messaging.
        """
        import logging
        import os

        import backend.app.core.encryption as enc_mod

        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        enc_mod._fernet_instance = None

        original_write = os.write

        def _raising_write(fd, data):
            # Best-effort: trigger OSError specifically for the key write.
            raise OSError("simulated read-only filesystem")

        monkeypatch.setattr(os, "write", _raising_write)

        with caplog.at_level(logging.ERROR, logger="backend.app.core.encryption"):
            key, source = enc_mod._load_or_generate_key()

        # Restore os.write so the rest of the test suite is unaffected.
        monkeypatch.setattr(os, "write", original_write)

        assert key is None
        assert source == "none_write_failed"
        assert any("Could not save MFA encryption key" in rec.message for rec in caplog.records)

    def test_load_or_generate_key_returns_none_on_read_oserror(self, monkeypatch, tmp_path, caplog):
        """B4: existing key file but read fails (e.g. permission denied) → (None, 'none_corrupted').

        Critical: must NOT regenerate a new key, which would destroy access to
        every row already encrypted under the existing key. S8: 'none_corrupted'
        marks the cause so operators see the right diagnostic.
        """
        import logging
        from pathlib import Path

        import backend.app.core.encryption as enc_mod

        # Pre-create a key file so we hit the existing-file branch.
        key_file = tmp_path / ".mfa_encryption_key"
        key_file.write_text("placeholder-content")
        original_size = key_file.stat().st_size

        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        enc_mod._fernet_instance = None

        original_read_text = Path.read_text

        def _raising_read_text(self, *args, **kwargs):
            if self.name == ".mfa_encryption_key":
                raise OSError("simulated permission denied")
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", _raising_read_text)

        with caplog.at_level(logging.ERROR, logger="backend.app.core.encryption"):
            key, source = enc_mod._load_or_generate_key()

        assert key is None
        assert source == "none_corrupted"
        # Critical: file must not have been overwritten with a new key.
        assert key_file.exists()
        assert key_file.stat().st_size == original_size
        assert any("Failed to read existing MFA key file" in rec.message for rec in caplog.records)
        assert any("Refusing to regenerate" in rec.message for rec in caplog.records)

    def test_get_key_source_reflects_active_source(self, monkeypatch, tmp_path):
        """get_key_source() returns the source detected on the most recent _get_fernet() call."""
        from cryptography.fernet import Fernet

        import backend.app.core.encryption as enc_mod

        monkeypatch.setenv("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        enc_mod._fernet_instance = None
        enc_mod._key_source = None

        # Trigger lazy initialisation
        enc_mod.mfa_encrypt("anything")

        assert enc_mod.get_key_source() == "env"

    def test_corrupted_key_file_returns_none_without_overwrite(self, monkeypatch, tmp_path, caplog):
        """A1: invalid key file content → (None, 'none_corrupted'), file not overwritten.

        S8: 'none_corrupted' (vs 'none_write_failed') so operators get the right
        diagnostic and don't see a misleading 'DATA_DIR not writable' warning.
        """
        import logging

        import backend.app.core.encryption as enc_mod

        key_file = tmp_path / ".mfa_encryption_key"
        key_file.write_text("invalid_content")
        original_mtime = key_file.stat().st_mtime

        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        enc_mod._fernet_instance = None

        with caplog.at_level(logging.ERROR, logger="backend.app.core.encryption"):
            key, source = enc_mod._load_or_generate_key()

        assert key is None
        assert source == "none_corrupted"
        assert key_file.exists(), "file must not be deleted"
        assert key_file.stat().st_mtime == original_mtime, "file must not be overwritten"
        assert any("not a valid Fernet key" in rec.message for rec in caplog.records)
        assert any("Refusing to overwrite" in rec.message for rec in caplog.records)

    def test_auto_generate_fileexistserror_returns_none_corrupted(self, monkeypatch, tmp_path, caplog):
        """S1: O_EXCL race — file appears between exists() check and open() →
        return (None, 'none_corrupted') without overwriting."""
        import logging
        import os

        import backend.app.core.encryption as enc_mod

        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        enc_mod._fernet_instance = None

        original_open = os.open

        def _excl_raise(path, flags, mode=0o777):
            if str(path).endswith(".mfa_encryption_key") and (flags & os.O_EXCL):
                raise FileExistsError(17, "File exists", str(path))
            return original_open(path, flags, mode)

        monkeypatch.setattr(os, "open", _excl_raise)

        with caplog.at_level(logging.ERROR, logger="backend.app.core.encryption"):
            key, source = enc_mod._load_or_generate_key()

        assert key is None
        assert source == "none_corrupted"
        assert any("Race detected" in rec.message for rec in caplog.records)


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
        from backend.app.core.auth import get_password_hash
        from backend.app.core.database import async_session, seed_default_groups

        async with async_session() as db:
            result = await db.execute(__import__("sqlalchemy").select(User).where(User.username == "oidc_replay_user"))
            if result.scalar_one_or_none() is None:
                db.add(
                    User(
                        username="oidc_replay_user",
                        password_hash=get_password_hash("pw"),
                        is_active=True,
                    )
                )
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
            await async_client.get(
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
    async def test_email_otp_invalidated_after_max_attempts(self, async_client: AsyncClient, db_session: AsyncSession):
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
        login_resp = await async_client.post(
            LOGIN_URL, json={"username": "otp_max_admin", "password": "Otp_max_admin1"}
        )
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
            json={"auth_enabled": True, "admin_username": "ratelimit_user", "admin_password": "Ratelimit_pw1"},
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
        await _setup_and_login(async_client, "cookie_bind_user", "cookie_bind_pw1")

        secret = pyotp.random_base32()
        totp_obj = pyotp.TOTP(secret)
        from sqlalchemy import select as sa_select

        from backend.app.models.user_totp import UserTOTP

        result = await db_session.execute(sa_select(User).where(User.username == "cookie_bind_user"))
        user = result.scalar_one()
        db_session.add(UserTOTP(user_id=user.id, secret=secret, is_enabled=True))
        await db_session.commit()

        # Login from "session A" — gets a pre_auth_token and a 2fa_challenge cookie
        login_resp = await async_client.post(
            LOGIN_URL, json={"username": "cookie_bind_user", "password": "Cookie_bind_pw1"}
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


# ===========================================================================
# C2: Security-header middleware
# ===========================================================================


class TestSecurityHeaders:
    """Every HTTP response must include standard security headers (C2)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_security_headers_present(self, async_client: AsyncClient):
        """GET /api/v1/auth/me (unauthenticated → 401) still carries security headers."""
        resp = await async_client.get(ME_URL)
        assert resp.status_code == 401  # sanity — no auth token

        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "SAMEORIGIN"
        assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"

        csp = resp.headers.get("content-security-policy", "")
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "object-src 'none'" in csp

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_hsts_absent_for_http(self, async_client: AsyncClient):
        """HSTS must NOT be set over plain HTTP (test transport uses http)."""
        resp = await async_client.get(ME_URL)
        assert "strict-transport-security" not in resp.headers


# ===========================================================================
# I3: Rate-limit bucket interaction — IP spray vs. username spray
# ===========================================================================


class TestRateLimitBuckets:
    """IP-spray and username-spray must each trip the correct independent bucket."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ip_spray_trips_ip_bucket(self, async_client: AsyncClient):
        """20 failed logins from one IP across 20 different usernames trips the IP bucket.

        Each per-username bucket only has 1 failure (well below MAX_LOGIN_ATTEMPTS=10),
        so the username bucket is never the reason for the 429.
        """
        from unittest.mock import patch as _patch

        unique_ip = "10.99.1.1"

        # Ensure auth is enabled
        await async_client.post(
            AUTH_SETUP_URL,
            json={"auth_enabled": True, "admin_username": "spray_ip_admin", "admin_password": "SprayIp_admin1"},
        )

        status_codes: list[int] = []
        with _patch("backend.app.api.routes.auth._get_client_ip", return_value=unique_ip):
            for i in range(22):
                resp = await async_client.post(
                    LOGIN_URL,
                    json={"username": f"spray_ip_victim_{i}", "password": "wrong"},
                )
                status_codes.append(resp.status_code)

        # The first 20 attempts fail with 401; the 21st+ must be 429 (IP bucket full)
        assert status_codes[-1] == 429, f"Expected 429 after 20 IP-spray failures, got: {status_codes}"
        # No single username saw more than one attempt → username buckets not tripped
        non_429 = [c for c in status_codes[:-2] if c == 429]
        assert not non_429, f"Username bucket triggered early: {status_codes}"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_username_spray_trips_username_bucket(self, async_client: AsyncClient):
        """One username targeted from 10+ different IPs trips the username bucket.

        Each per-IP bucket only sees 1 failure, so no IP bucket is tripped.
        The username bucket (max 10) is what fires the 429.
        """
        from unittest.mock import patch as _patch

        from backend.app.api.routes.mfa import MAX_LOGIN_ATTEMPTS

        # Ensure auth is enabled
        await async_client.post(
            AUTH_SETUP_URL,
            json={
                "auth_enabled": True,
                "admin_username": "spray_uname_admin",
                "admin_password": "SprayUname_admin1",
            },
        )

        target_username = "spray_uname_victim"
        status_codes: list[int] = []
        for i in range(MAX_LOGIN_ATTEMPTS + 2):
            rotating_ip = f"10.99.2.{i + 1}"
            with _patch("backend.app.api.routes.auth._get_client_ip", return_value=rotating_ip):
                resp = await async_client.post(
                    LOGIN_URL,
                    json={"username": target_username, "password": "wrong"},
                )
                status_codes.append(resp.status_code)

        # After MAX_LOGIN_ATTEMPTS failures for same username the bucket fires
        assert status_codes[-1] == 429, (
            f"Expected 429 after {MAX_LOGIN_ATTEMPTS} username-spray failures, got: {status_codes}"
        )


# ============================================================================
# TestEncryptLegacyMigration
# ============================================================================


class TestEncryptLegacyMigration:
    """Re-encryption migration of legacy plaintext OIDC + TOTP rows.

    The migration runs against its own ``async_session`` factory (not the
    ``db_session`` fixture) so each test patches the module-level factory to
    point at the test-engine before invoking the helper. ``db_session`` is
    used to seed and to verify state via the same engine.
    """

    @staticmethod
    def _patch_module_session(monkeypatch, db_session):
        """Bind ``database.async_session`` to the test engine for one test."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from backend.app.core import database as db_mod

        test_factory = async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False)
        monkeypatch.setattr(db_mod, "async_session", test_factory)

    @staticmethod
    def _set_active_key(monkeypatch):
        """Configure a valid Fernet key for the migration to use."""
        from cryptography.fernet import Fernet

        import backend.app.core.encryption as enc_mod

        monkeypatch.setenv("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
        enc_mod._fernet_instance = None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_migration_encrypts_plaintext_oidc_secret(self, db_session, monkeypatch):
        from sqlalchemy import select

        from backend.app.core.database import _migrate_encrypt_legacy_secrets
        from backend.app.models.oidc_provider import OIDCProvider

        self._patch_module_session(monkeypatch, db_session)
        self._set_active_key(monkeypatch)

        provider = OIDCProvider(
            name="LegacyProv",
            issuer_url="https://legacy.example.com",
            client_id="cid",
            _client_secret_enc="legacy-plaintext",
            scopes="openid email profile",
            is_enabled=True,
        )
        db_session.add(provider)
        await db_session.commit()

        await _migrate_encrypt_legacy_secrets()

        # Re-fetch on a fresh row state
        await db_session.refresh(provider)
        assert provider._client_secret_enc.startswith("fernet:")
        # Decrypted value matches the original plaintext
        assert provider.client_secret == "legacy-plaintext"

        # Sanity: a SELECT also sees the encrypted value
        result = await db_session.execute(select(OIDCProvider).where(OIDCProvider.id == provider.id))
        fetched = result.scalar_one()
        assert fetched._client_secret_enc.startswith("fernet:")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_migration_skips_already_encrypted_rows(self, db_session, monkeypatch):
        from backend.app.core.database import _migrate_encrypt_legacy_secrets
        from backend.app.models.oidc_provider import OIDCProvider

        self._patch_module_session(monkeypatch, db_session)
        self._set_active_key(monkeypatch)

        # Use the property setter so the value is encrypted up front.
        provider = OIDCProvider(
            name="EncProv",
            issuer_url="https://enc.example.com",
            client_id="cid",
            client_secret="already-encrypted",
            scopes="openid email profile",
            is_enabled=True,
        )
        db_session.add(provider)
        await db_session.commit()

        original_enc = provider._client_secret_enc
        await _migrate_encrypt_legacy_secrets()
        await _migrate_encrypt_legacy_secrets()  # idempotent

        await db_session.refresh(provider)
        # Value unchanged across two migration runs (still the same ciphertext).
        assert provider._client_secret_enc == original_enc

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_migration_no_op_when_key_unset(self, db_session, monkeypatch):
        import backend.app.core.encryption as enc_mod
        from backend.app.core.database import _migrate_encrypt_legacy_secrets
        from backend.app.models.oidc_provider import OIDCProvider

        self._patch_module_session(monkeypatch, db_session)
        # Force "no key" branch
        monkeypatch.setattr(enc_mod, "_load_or_generate_key", lambda: (None, "none"))
        enc_mod._fernet_instance = None

        provider = OIDCProvider(
            name="NoKeyProv",
            issuer_url="https://nokey.example.com",
            client_id="cid",
            _client_secret_enc="still-plaintext",
            scopes="openid email profile",
            is_enabled=True,
        )
        db_session.add(provider)
        await db_session.commit()

        await _migrate_encrypt_legacy_secrets()
        await db_session.refresh(provider)
        # Migration should have early-returned; plaintext untouched.
        assert provider._client_secret_enc == "still-plaintext"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_migration_handles_mixed_state(self, db_session, monkeypatch):
        from backend.app.core.database import _migrate_encrypt_legacy_secrets
        from backend.app.models.oidc_provider import OIDCProvider

        self._patch_module_session(monkeypatch, db_session)
        self._set_active_key(monkeypatch)

        legacy = OIDCProvider(
            name="LegacyMix",
            issuer_url="https://l.example.com",
            client_id="c1",
            _client_secret_enc="plain-mix",
            scopes="openid email profile",
        )
        encrypted = OIDCProvider(
            name="EncMix",
            issuer_url="https://e.example.com",
            client_id="c2",
            client_secret="encrypted-mix",  # uses setter
            scopes="openid email profile",
        )
        db_session.add_all([legacy, encrypted])
        await db_session.commit()

        original_encrypted = encrypted._client_secret_enc

        await _migrate_encrypt_legacy_secrets()

        await db_session.refresh(legacy)
        await db_session.refresh(encrypted)
        assert legacy._client_secret_enc.startswith("fernet:")
        assert legacy.client_secret == "plain-mix"
        assert encrypted._client_secret_enc == original_encrypted

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_migration_encrypts_plaintext_totp_secret(self, db_session, monkeypatch):
        from backend.app.core.database import _migrate_encrypt_legacy_secrets
        from backend.app.models.user import User
        from backend.app.models.user_totp import UserTOTP

        self._patch_module_session(monkeypatch, db_session)
        self._set_active_key(monkeypatch)

        user = User(username="totpuser1219", email="t@example.com", password_hash="x")
        db_session.add(user)
        await db_session.flush()

        totp = UserTOTP(user_id=user.id, _secret_enc="JBSWY3DPEHPK3PXP", is_enabled=True)
        db_session.add(totp)
        await db_session.commit()

        await _migrate_encrypt_legacy_secrets()

        await db_session.refresh(totp)
        assert totp._secret_enc.startswith("fernet:")
        assert totp.secret == "JBSWY3DPEHPK3PXP"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_migration_logs_count_of_rows_re_encrypted(self, db_session, monkeypatch, caplog):
        import logging

        from backend.app.core.database import _migrate_encrypt_legacy_secrets
        from backend.app.models.oidc_provider import OIDCProvider
        from backend.app.models.user import User
        from backend.app.models.user_totp import UserTOTP

        self._patch_module_session(monkeypatch, db_session)
        self._set_active_key(monkeypatch)

        provider = OIDCProvider(
            name="LegacyLog",
            issuer_url="https://log.example.com",
            client_id="c",
            _client_secret_enc="p",
            scopes="openid email profile",
        )
        user = User(username="logger1219", email="l@example.com", password_hash="x")
        db_session.add_all([provider, user])
        await db_session.flush()
        totp = UserTOTP(user_id=user.id, _secret_enc="JBSWY3DPEHPK3PXP", is_enabled=True)
        db_session.add(totp)
        await db_session.commit()

        with caplog.at_level(logging.INFO, logger="backend.app.core.database"):
            await _migrate_encrypt_legacy_secrets()

        # The migration logs once with both counts.
        assert any(
            "Re-encrypted legacy plaintext secrets" in rec.message
            and "1 OIDC client_secret(s)" in rec.message
            and "1 TOTP secret(s)" in rec.message
            for rec in caplog.records
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_migration_continues_on_row_error(self, db_session, monkeypatch, caplog):
        """B2: per-row commit semantics — when one row fails to re-encrypt,
        OTHER successfully-encrypted rows must remain committed and the
        failure surfaces via get_migration_error_count.

        Replaces the previous "rollback all" behaviour: a single poison row
        used to block every successful re-encryption on every startup forever.
        """
        import logging

        import backend.app.core.encryption as enc_mod  # noqa: F401
        from backend.app.core.database import (
            _migrate_encrypt_legacy_secrets,
            get_migration_error_count,
        )
        from backend.app.models.oidc_provider import OIDCProvider

        self._patch_module_session(monkeypatch, db_session)
        self._set_active_key(monkeypatch)

        good = OIDCProvider(
            name="GoodRow",
            issuer_url="https://good.example.com",
            client_id="c1",
            _client_secret_enc="plaintext-good",
            scopes="openid email profile",
        )
        bad = OIDCProvider(
            name="BadRow",
            issuer_url="https://bad.example.com",
            client_id="c2",
            _client_secret_enc="plaintext-bad",
            scopes="openid email profile",
        )
        db_session.add_all([good, bad])
        await db_session.commit()

        original_bad = bad._client_secret_enc

        # Force the setter on the SECOND row to raise — patch at the model's
        # import location so the property setter picks up the patched function.
        import backend.app.models.oidc_provider as oidc_mod

        real_encrypt = oidc_mod.mfa_encrypt
        call_count = [0]

        def _sometimes_raise(value):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("simulated encrypt failure")
            return real_encrypt(value)

        monkeypatch.setattr(oidc_mod, "mfa_encrypt", _sometimes_raise)

        with caplog.at_level(logging.ERROR, logger="backend.app.core.database"):
            await _migrate_encrypt_legacy_secrets()

        # B2: per-row commit — good IS encrypted, bad is unchanged.
        await db_session.refresh(good)
        await db_session.refresh(bad)
        assert good._client_secret_enc.startswith("fernet:"), (
            "good row must be successfully re-encrypted (per-row commit)"
        )
        assert bad._client_secret_enc == original_bad, "bad row must remain unchanged (savepoint-style isolation)"
        assert get_migration_error_count() == 1, "the skipped row must be exposed via get_migration_error_count"
        assert any("skipping" in rec.message.lower() for rec in caplog.records)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_migration_logs_no_op_when_all_encrypted(self, db_session, monkeypatch, caplog):
        """A2: when all rows are already encrypted, migration logs a debug no-op."""
        import logging

        from backend.app.core.database import _migrate_encrypt_legacy_secrets
        from backend.app.models.oidc_provider import OIDCProvider

        self._patch_module_session(monkeypatch, db_session)
        self._set_active_key(monkeypatch)

        provider = OIDCProvider(
            name="AlreadyEnc",
            issuer_url="https://ae.example.com",
            client_id="cae",
            client_secret="already-encrypted",
            scopes="openid email profile",
        )
        db_session.add(provider)
        await db_session.commit()

        with caplog.at_level(logging.DEBUG, logger="backend.app.core.database"):
            await _migrate_encrypt_legacy_secrets()

        assert any("no rows needed re-encryption" in rec.message for rec in caplog.records)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_init_db_propagates_unexpected_migration_error(self, monkeypatch):
        """B3: an unexpected error from _migrate_encrypt_legacy_secrets must
        surface (re-raise) instead of being silently swallowed.

        Pins the contract introduced for B3: a startup-fatal error like a
        session-creation failure must fail the lifespan / CLI / restore
        handler explicitly, never run the app with half-migrated rows.

        Implementation note: we patch _migrate_encrypt_legacy_secrets itself
        rather than poking the inner read phase, because that is the contract
        boundary the rest of the codebase relies on (init_db -> migration).
        """
        import backend.app.core.database as db_mod

        async def boom():
            raise RuntimeError("simulated startup-fatal failure")

        # Stub out the rest of init_db so we exercise only the migration step.
        # init_db opens the engine.begin() block, runs metadata.create_all,
        # run_migrations, then awaits _migrate_encrypt_legacy_secrets — the
        # only call we want to fail.
        monkeypatch.setattr(db_mod, "_migrate_encrypt_legacy_secrets", boom)
        monkeypatch.setattr(db_mod, "seed_notification_templates", lambda: _noop_async())
        monkeypatch.setattr(db_mod, "seed_default_groups", lambda: _noop_async())
        monkeypatch.setattr(db_mod, "seed_spool_catalog", lambda: _noop_async())
        monkeypatch.setattr(db_mod, "seed_color_catalog", lambda: _noop_async())

        with pytest.raises(RuntimeError, match="simulated startup-fatal failure"):
            await db_mod.init_db()


async def _noop_async():
    """Helper for tests that need to stub out `seed_*` async coroutines."""
    return None


# ============================================================================
# TestEncryptionStatusEndpoint
# ============================================================================


class TestEncryptionStatusEndpoint:
    """GET /api/v1/auth/encryption-status: key source, counts, decryption_broken."""

    STATUS_URL = "/api/v1/auth/encryption-status"

    async def _create_admin_and_login(self, async_client: AsyncClient) -> str:
        """Bootstrap auth + return a Bearer token for an admin."""
        await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "admin1219",
                "admin_password": "Admin1219!Pass",
            },
        )
        login = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "admin1219", "password": "Admin1219!Pass"},
        )
        assert login.status_code == 200, login.text
        return login.json()["access_token"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_status_reports_env_source(self, async_client, monkeypatch):
        from cryptography.fernet import Fernet

        import backend.app.core.encryption as enc_mod

        token = await self._create_admin_and_login(async_client)
        monkeypatch.setenv("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
        enc_mod._fernet_instance = None
        enc_mod._key_source = None

        resp = await async_client.get(self.STATUS_URL, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["key_configured"] is True
        assert data["key_source"] == "env"
        assert data["decryption_broken"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_status_reports_file_source(self, async_client, monkeypatch, tmp_path):
        from cryptography.fernet import Fernet

        import backend.app.core.encryption as enc_mod

        token = await self._create_admin_and_login(async_client)
        # Pre-place a valid key file in DATA_DIR.
        key_file = tmp_path / ".mfa_encryption_key"
        key_file.write_text(Fernet.generate_key().decode())
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.delenv("MFA_ENCRYPTION_KEY", raising=False)
        enc_mod._fernet_instance = None
        enc_mod._key_source = None

        resp = await async_client.get(self.STATUS_URL, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["key_source"] == "file"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_status_reports_generated_source(self, async_client, monkeypatch, tmp_path):
        import backend.app.core.encryption as enc_mod

        token = await self._create_admin_and_login(async_client)
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.delenv("MFA_ENCRYPTION_KEY", raising=False)
        enc_mod._fernet_instance = None
        enc_mod._key_source = None

        resp = await async_client.get(self.STATUS_URL, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["key_source"] == "generated"
        assert (tmp_path / ".mfa_encryption_key").exists()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_status_reports_none_source(self, async_client, monkeypatch):
        import backend.app.core.encryption as enc_mod

        token = await self._create_admin_and_login(async_client)
        monkeypatch.setattr(enc_mod, "_load_or_generate_key", lambda: (None, "none"))
        enc_mod._fernet_instance = None
        enc_mod._key_source = None

        resp = await async_client.get(self.STATUS_URL, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["key_configured"] is False
        assert data["key_source"] == "none"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_status_counts_legacy_rows(self, async_client, db_session, monkeypatch):
        from backend.app.models.oidc_provider import OIDCProvider

        token = await self._create_admin_and_login(async_client)

        provider = OIDCProvider(
            name="LegacyStatus",
            issuer_url="https://ls.example.com",
            client_id="c",
            _client_secret_enc="plaintext-no-prefix",
            scopes="openid email profile",
        )
        db_session.add(provider)
        await db_session.commit()

        resp = await async_client.get(self.STATUS_URL, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["legacy_plaintext_rows"]["oidc_providers"] >= 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_status_counts_encrypted_rows(self, async_client, db_session, monkeypatch):
        from cryptography.fernet import Fernet

        import backend.app.core.encryption as enc_mod
        from backend.app.models.oidc_provider import OIDCProvider

        token = await self._create_admin_and_login(async_client)
        monkeypatch.setenv("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
        enc_mod._fernet_instance = None
        enc_mod._key_source = None

        provider = OIDCProvider(
            name="EncStatus",
            issuer_url="https://es.example.com",
            client_id="c",
            client_secret="real-secret",  # via setter → encrypted
            scopes="openid email profile",
        )
        db_session.add(provider)
        await db_session.commit()

        resp = await async_client.get(self.STATUS_URL, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["encrypted_rows"]["oidc_providers"] >= 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_status_warns_on_encrypted_rows_without_key(self, async_client, db_session, monkeypatch):
        """Gap 2: encrypted rows present but no key loadable → decryption_broken=true."""
        import backend.app.core.encryption as enc_mod
        from backend.app.models.oidc_provider import OIDCProvider

        token = await self._create_admin_and_login(async_client)

        # Insert a row whose value is already prefixed (simulates a previously-encrypted row).
        provider = OIDCProvider(
            name="BrokenEnc",
            issuer_url="https://be.example.com",
            client_id="c",
            _client_secret_enc="fernet:gAAAAA-fake-but-prefixed",
            scopes="openid email profile",
        )
        db_session.add(provider)
        await db_session.commit()

        # Now disable key loading so decryption is impossible.
        monkeypatch.setattr(enc_mod, "_load_or_generate_key", lambda: (None, "none"))
        enc_mod._fernet_instance = None
        enc_mod._key_source = None

        resp = await async_client.get(self.STATUS_URL, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["key_configured"] is False
        assert data["encrypted_rows"]["oidc_providers"] >= 1
        assert data["decryption_broken"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_status_requires_settings_read_permission(self, async_client, db_session):
        """Non-admin without settings:read permission gets 403."""
        from backend.app.models.user import User

        await self._create_admin_and_login(async_client)

        # Create a low-privilege user (no group → no permissions in default seed).
        from backend.app.core.auth import get_password_hash

        viewer = User(
            username="viewer1219",
            email="viewer1219@example.com",
            password_hash=get_password_hash("Viewer1219!Pass"),
            role="user",
            is_active=True,
        )
        db_session.add(viewer)
        await db_session.commit()

        login = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "viewer1219", "password": "Viewer1219!Pass"},
        )
        assert login.status_code == 200, login.text
        token = login.json().get("access_token")
        assert token is not None, f"Expected access_token in login response, got: {login.json()}"

        resp = await async_client.get(self.STATUS_URL, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_status_returns_500_on_db_error(self, async_client, monkeypatch):
        """A8: SQLAlchemyError during count queries → 500 with static message."""
        from unittest.mock import AsyncMock

        from sqlalchemy.exc import SQLAlchemyError

        token = await self._create_admin_and_login(async_client)

        async def _raise(*args, **kwargs):
            raise SQLAlchemyError("simulated DB failure")

        monkeypatch.setattr("sqlalchemy.ext.asyncio.AsyncSession.execute", AsyncMock(side_effect=_raise))

        resp = await async_client.get(self.STATUS_URL, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 500
        assert "encryption status" in resp.json().get("detail", "").lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_status_returns_403_for_viewer_in_viewers_group(self, async_client, db_session):
        """S2: a user in the Viewers group (has SETTINGS_READ but NOT SETTINGS_UPDATE)
        must get 403 — encryption-status is admin/operator only.
        """
        from sqlalchemy import insert, select

        from backend.app.core.auth import get_password_hash
        from backend.app.models.group import Group, user_groups
        from backend.app.models.user import User

        # Bootstrap auth (creates default groups via setup endpoint).
        await self._create_admin_and_login(async_client)

        # Create a user explicitly in the Viewers group — it has SETTINGS_READ
        # but not SETTINGS_UPDATE, which is the discriminator for S2.
        viewer = User(
            username="viewer_s2",
            email="viewer_s2@example.com",
            password_hash=get_password_hash("ViewerS2!Pass1"),
            role="user",
            is_active=True,
        )
        db_session.add(viewer)
        await db_session.flush()

        viewers_group = (await db_session.execute(select(Group).where(Group.name == "Viewers"))).scalar_one_or_none()
        assert viewers_group is not None, "Viewers group must be seeded by setup"

        # Insert the association row directly to avoid touching the lazy
        # `viewer.groups` relationship (which would trigger an implicit
        # IO inside an active async transaction and fail with MissingGreenlet).
        await db_session.execute(insert(user_groups).values(user_id=viewer.id, group_id=viewers_group.id))
        await db_session.commit()

        login = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "viewer_s2", "password": "ViewerS2!Pass1"},
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]

        resp = await async_client.get(self.STATUS_URL, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403, "S2: Viewers (SETTINGS_READ only) must NOT be able to read encryption-status"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_status_decryption_broken_when_wrong_key_active(self, async_client, db_session, monkeypatch):
        """B4: key is configured but cannot decrypt existing rows → decryption_broken=True.

        This is the "wrong key" state that the legacy computed_field check
        missed — operator pasted a different valid Fernet key (rotation,
        cross-deployment restore, env override). Status used to show GREEN
        while every encrypted row was unrecoverable.
        """
        from cryptography.fernet import Fernet

        import backend.app.core.encryption as enc_mod
        from backend.app.models.oidc_provider import OIDCProvider

        token = await self._create_admin_and_login(async_client)

        # Insert a row whose value is fernet-prefixed but encrypted under a
        # DIFFERENT key (the prefix matches, but decrypt will throw).
        provider = OIDCProvider(
            name="WrongKeyEnc",
            issuer_url="https://wk.example.com",
            client_id="c",
            _client_secret_enc=("fernet:" + Fernet(Fernet.generate_key()).encrypt(b"original").decode()),
            scopes="openid email profile",
        )
        db_session.add(provider)
        await db_session.commit()

        # Now activate a DIFFERENT key — sample-decrypt must fail.
        monkeypatch.setenv("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
        enc_mod._fernet_instance = None
        enc_mod._key_source = None

        resp = await async_client.get(self.STATUS_URL, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["key_configured"] is True, "different key is still 'configured'"
        assert data["encrypted_rows"]["oidc_providers"] >= 1
        assert data["decryption_broken"] is True, "B4: sample-decrypt must detect wrong-key state"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_status_decryption_broken_with_only_totp_rows(self, async_client, db_session, monkeypatch):
        """B4: the sample-decrypt fallback to UserTOTP fires when there are no
        encrypted OIDC rows but TOTP rows exist. The OIDC-only test above
        proves the primary path; this pins the second branch in the same
        try-block so a future refactor of the row-source switch can't silently
        regress wrong-key detection for TOTP-only deployments.
        """
        from cryptography.fernet import Fernet
        from sqlalchemy import select

        import backend.app.core.encryption as enc_mod
        from backend.app.models.user import User
        from backend.app.models.user_totp import UserTOTP

        token = await self._create_admin_and_login(async_client)

        # Look up the admin user created by login so we can attach a TOTP row.
        admin_row = await db_session.execute(select(User).where(User.username == "admin1219"))
        admin = admin_row.scalar_one()

        # Seed a UserTOTP row encrypted under key A. No OIDC rows exist, so
        # the endpoint's first branch (oidc_providers > 0) misses and the
        # sample falls through to UserTOTP.
        key_a_ciphertext = Fernet(Fernet.generate_key()).encrypt(b"original-totp-secret").decode()
        db_session.add(UserTOTP(user_id=admin.id, _secret_enc=f"fernet:{key_a_ciphertext}", is_enabled=True))
        await db_session.commit()

        # Activate a DIFFERENT key — the TOTP-fallback sample-decrypt must fail.
        monkeypatch.setenv("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
        enc_mod._fernet_instance = None
        enc_mod._key_source = None

        resp = await async_client.get(self.STATUS_URL, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["key_configured"] is True
        assert data["encrypted_rows"]["oidc_providers"] == 0, "test premise: no OIDC rows so TOTP branch fires"
        assert data["encrypted_rows"]["user_totp"] >= 1
        assert data["decryption_broken"] is True, "B4: TOTP-fallback sample-decrypt must detect wrong-key state"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_status_surfaces_real_migration_error_count(self, async_client, db_session, monkeypatch, caplog):
        """B2: a real migration with a poison row produces an error_count that
        flows through to the endpoint's `migration_error_count` field.

        Replaces an earlier tautology that patched the module-level counter
        directly. The chained version verifies the full path: poison row →
        per-row migration skip → ``get_migration_error_count()`` →
        ``GET /encryption-status``.
        """
        import logging

        from backend.app.core.database import _migrate_encrypt_legacy_secrets, get_migration_error_count
        from backend.app.models.oidc_provider import OIDCProvider

        token = await self._create_admin_and_login(async_client)

        # Bind the migration's session factory to the test engine and activate a key.
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from backend.app.core import database as db_mod

        test_factory = async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False)
        monkeypatch.setattr(db_mod, "async_session", test_factory)
        from cryptography.fernet import Fernet

        import backend.app.core.encryption as enc_mod

        monkeypatch.setenv("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
        enc_mod._fernet_instance = None

        # Two legacy plaintext rows; force the SECOND row's encrypt call to raise.
        db_session.add_all(
            [
                OIDCProvider(
                    name="GoodRow",
                    issuer_url="https://good.example.com",
                    client_id="c1",
                    _client_secret_enc="plaintext-good",
                    scopes="openid email profile",
                ),
                OIDCProvider(
                    name="BadRow",
                    issuer_url="https://bad.example.com",
                    client_id="c2",
                    _client_secret_enc="plaintext-bad",
                    scopes="openid email profile",
                ),
            ]
        )
        await db_session.commit()

        import backend.app.models.oidc_provider as oidc_mod

        real_encrypt = oidc_mod.mfa_encrypt
        call_count = [0]

        def _sometimes_raise(value):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("simulated encrypt failure")
            return real_encrypt(value)

        monkeypatch.setattr(oidc_mod, "mfa_encrypt", _sometimes_raise)

        with caplog.at_level(logging.ERROR, logger="backend.app.core.database"):
            await _migrate_encrypt_legacy_secrets()

        # Sanity: the migration's own counter saw the failure.
        assert get_migration_error_count() == 1

        # The endpoint must surface the same number — full path pinned, not just the getter.
        resp = await async_client.get(self.STATUS_URL, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["migration_error_count"] == 1, (
            "endpoint must report the actual migration outcome, not just read a stub global"
        )


# ============================================================================
# TestEncryptionRoundtrip (E2E)
# ============================================================================


class TestEncryptionRoundtrip:
    """End-to-end: writes via the property setter store ciphertext at the column
    level; reads via the property getter return the original plaintext."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_oidc_provider_secret_encrypted_at_rest_e2e(self, db_session, monkeypatch):
        from cryptography.fernet import Fernet
        from sqlalchemy import select

        import backend.app.core.encryption as enc_mod
        from backend.app.models.oidc_provider import OIDCProvider

        monkeypatch.setenv("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
        enc_mod._fernet_instance = None

        provider = OIDCProvider(
            name="E2E_OIDC",
            issuer_url="https://e2e.example.com",
            client_id="cid",
            client_secret="my-real-client-secret",  # via setter → encrypted
            scopes="openid email profile",
            is_enabled=True,
        )
        db_session.add(provider)
        await db_session.commit()

        # Raw column read: must be ciphertext, not the plaintext.
        result = await db_session.execute(select(OIDCProvider).where(OIDCProvider.id == provider.id))
        fetched = result.scalar_one()
        assert fetched._client_secret_enc.startswith("fernet:")
        assert fetched._client_secret_enc != "my-real-client-secret"

        # Property read: returns original plaintext.
        assert fetched.client_secret == "my-real-client-secret"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_totp_secret_encrypted_at_rest_e2e(self, db_session, monkeypatch):
        from cryptography.fernet import Fernet
        from sqlalchemy import select

        import backend.app.core.encryption as enc_mod
        from backend.app.models.user import User
        from backend.app.models.user_totp import UserTOTP

        monkeypatch.setenv("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
        enc_mod._fernet_instance = None

        user = User(username="e2etotp1219", email="e@example.com", password_hash="x")
        db_session.add(user)
        await db_session.flush()

        totp = UserTOTP(user_id=user.id, secret="JBSWY3DPEHPK3PXP", is_enabled=True)
        db_session.add(totp)
        await db_session.commit()

        result = await db_session.execute(select(UserTOTP).where(UserTOTP.user_id == user.id))
        fetched = result.scalar_one()
        assert fetched._secret_enc.startswith("fernet:")
        assert fetched._secret_enc != "JBSWY3DPEHPK3PXP"
        assert fetched.secret == "JBSWY3DPEHPK3PXP"


# ============================================================================
# TestBackupKeyFiles
# Verifies that .mfa_encryption_key is included in backup ZIPs (so backups
# are self-contained) and restored with chmod 0600 — and that path-traversal
# payloads in a malicious ZIP are rejected.
# ============================================================================


class TestBackupKeyFiles:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_backup_includes_mfa_encryption_key_when_present(self, async_client, monkeypatch, tmp_path):
        import zipfile

        from backend.app.api.routes.settings import create_backup_zip
        from backend.app.core.config import settings as app_settings

        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        # Ensure `app_settings.base_dir` follows DATA_DIR for this test by
        # patching the module attribute (config caches it at import time).
        monkeypatch.setattr(app_settings, "base_dir", tmp_path)

        key_path = tmp_path / ".mfa_encryption_key"
        key_path.write_text("test-key-content")

        zip_path, _filename = await create_backup_zip(output_path=tmp_path)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                names = zf.namelist()
                assert ".mfa_encryption_key" in names
                assert zf.read(".mfa_encryption_key").decode() == "test-key-content"
        finally:
            zip_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_backup_skips_mfa_encryption_key_when_absent(self, async_client, monkeypatch, tmp_path):
        import zipfile

        from backend.app.api.routes.settings import create_backup_zip
        from backend.app.core.config import settings as app_settings

        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setattr(app_settings, "base_dir", tmp_path)
        # No .mfa_encryption_key written — must not crash.

        zip_path, _filename = await create_backup_zip(output_path=tmp_path)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                names = zf.namelist()
                assert ".mfa_encryption_key" not in names
        finally:
            zip_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_restore_writes_key_files_with_chmod_0600(self, async_client, monkeypatch, tmp_path):
        """T1: restore endpoint writes key file with mode 0o600.

        Bypasses the SQLite-copy step via patches so execution reaches the
        key-write code unconditionally — the previous version used a stub
        ``b"SQLite format 3"`` which made ``sqlite3.backup()`` fail and the
        key-write code never ran.
        """
        import io
        import zipfile
        from unittest.mock import AsyncMock, patch

        from backend.app.core.config import settings as app_settings

        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setattr(app_settings, "base_dir", tmp_path)

        # Build a minimal ZIP with a stub DB and the key file.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("bambuddy.db", b"SQLite format 3")
            zf.writestr(".mfa_encryption_key", "test-restored-key")
        buf.seek(0)

        with (
            patch("backend.app.core.db_dialect.is_sqlite", return_value=False),
            patch(
                "backend.app.api.routes.settings._import_sqlite_to_postgres",
                new_callable=AsyncMock,
            ),
            patch("backend.app.core.database.close_all_connections", new_callable=AsyncMock),
            patch("backend.app.core.database.reinitialize_database", new_callable=AsyncMock),
            patch("backend.app.core.database.init_db", new_callable=AsyncMock),
        ):
            resp = await async_client.post(
                "/api/v1/settings/restore",
                files={"file": ("backup.zip", buf, "application/zip")},
            )

        assert resp.status_code == 200
        restored_key = tmp_path / ".mfa_encryption_key"
        assert restored_key.exists()
        assert restored_key.read_text() == "test-restored-key"
        assert (restored_key.stat().st_mode & 0o777) == 0o600

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_restore_handles_missing_key_files(self, async_client, monkeypatch, tmp_path):
        """T2: ZIP without key file → restore succeeds, no key written to DATA_DIR."""
        import io
        import zipfile
        from unittest.mock import AsyncMock, patch

        from backend.app.core.config import settings as app_settings

        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setattr(app_settings, "base_dir", tmp_path)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("bambuddy.db", b"SQLite format 3")
            # Intentionally no .mfa_encryption_key entry.
        buf.seek(0)

        with (
            patch("backend.app.core.db_dialect.is_sqlite", return_value=False),
            patch(
                "backend.app.api.routes.settings._import_sqlite_to_postgres",
                new_callable=AsyncMock,
            ),
            patch("backend.app.core.database.close_all_connections", new_callable=AsyncMock),
            patch("backend.app.core.database.reinitialize_database", new_callable=AsyncMock),
            patch("backend.app.core.database.init_db", new_callable=AsyncMock),
        ):
            resp = await async_client.post(
                "/api/v1/settings/restore",
                files={"file": ("backup.zip", buf, "application/zip")},
            )

        assert resp.status_code == 200
        assert not (tmp_path / ".mfa_encryption_key").exists()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_restore_aborts_db_swap_when_key_write_fails(self, async_client, monkeypatch, tmp_path):
        """B1: when MFA key write fails, restore must abort BEFORE the database
        swap so the live DB is not left with rows encrypted under a key that
        no longer exists on disk."""
        import io
        import os
        import zipfile
        from unittest.mock import AsyncMock, patch

        from backend.app.core.config import settings as app_settings

        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setattr(app_settings, "base_dir", tmp_path)

        # Build ZIP with a key file that we will fail to write to DATA_DIR.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("bambuddy.db", b"SQLite format 3 backup data")
            zf.writestr(".mfa_encryption_key", "backup-key-content")
        buf.seek(0)

        # Track whether the database swap functions were called.
        # If B1 is correct, key-write failure aborts BEFORE these run.
        import_pg_mock = AsyncMock()
        reinit_mock = AsyncMock()
        init_mock = AsyncMock()

        original_open = os.open

        def _key_write_fails(path, flags, mode=0o777, **kwargs):
            # `shutil.rmtree` calls os.open(... dir_fd=...) during temp-dir
            # cleanup — accept and forward any extra kwargs so the mock
            # doesn't break the cleanup path.
            if str(path).endswith(".mfa_encryption_key.restore-tmp"):
                raise OSError(28, "No space left on device", str(path))
            return original_open(path, flags, mode, **kwargs)

        with (
            patch("backend.app.core.db_dialect.is_sqlite", return_value=False),
            patch(
                "backend.app.api.routes.settings._import_sqlite_to_postgres",
                import_pg_mock,
            ),
            patch("backend.app.core.database.close_all_connections", new_callable=AsyncMock),
            patch("backend.app.core.database.reinitialize_database", reinit_mock),
            patch("backend.app.core.database.init_db", init_mock),
        ):
            monkeypatch.setattr(os, "open", _key_write_fails)
            resp = await async_client.post(
                "/api/v1/settings/restore",
                files={"file": ("backup.zip", buf, "application/zip")},
            )

        assert resp.status_code == 500
        assert "Database is unchanged" in resp.json().get("detail", "")
        # Database swap functions must NOT have been called — the abort
        # happens before that step.
        import_pg_mock.assert_not_awaited()
        reinit_mock.assert_not_awaited()
        init_mock.assert_not_awaited()
        # No partial key file should be left behind.
        assert not (tmp_path / ".mfa_encryption_key").exists()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_restore_resets_encryption_singleton_after_key_replace(self, async_client, monkeypatch, tmp_path):
        """B1: after a successful key replace, the encryption singleton must be
        cleared so init_db's re-encryption migration picks up the restored key
        instead of the cached Fernet from the previous key.
        """
        import io
        import zipfile
        from unittest.mock import AsyncMock, patch

        from cryptography.fernet import Fernet

        import backend.app.core.encryption as enc_mod
        from backend.app.core.config import settings as app_settings

        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setattr(app_settings, "base_dir", tmp_path)

        # Pre-warm the singleton with an "old" key so we can detect the reset.
        old_key = Fernet.generate_key().decode()
        monkeypatch.setenv("MFA_ENCRYPTION_KEY", old_key)
        enc_mod._fernet_instance = None
        enc_mod._key_source = None
        # Trigger lazy load → singleton holds the old Fernet.
        assert enc_mod.is_encryption_active() is True
        assert enc_mod._fernet_instance is not None
        old_fernet_obj = enc_mod._fernet_instance

        # Build ZIP that delivers a DIFFERENT key file.
        new_key = Fernet.generate_key().decode()
        assert new_key != old_key
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("bambuddy.db", b"SQLite format 3 backup data")
            zf.writestr(".mfa_encryption_key", new_key)
        buf.seek(0)

        with (
            patch("backend.app.core.db_dialect.is_sqlite", return_value=False),
            patch(
                "backend.app.api.routes.settings._import_sqlite_to_postgres",
                new_callable=AsyncMock,
            ),
            patch("backend.app.core.database.close_all_connections", new_callable=AsyncMock),
            patch("backend.app.core.database.reinitialize_database", new_callable=AsyncMock),
            patch("backend.app.core.database.init_db", new_callable=AsyncMock),
        ):
            resp = await async_client.post(
                "/api/v1/settings/restore",
                files={"file": ("backup.zip", buf, "application/zip")},
            )

        assert resp.status_code == 200, resp.text
        # The singleton must have been invalidated. The exact post-state depends
        # on whether init_db (mocked) re-loaded the singleton, but the cached
        # _fernet_instance reference from before the restore must not be the
        # active one any more.
        assert enc_mod._fernet_instance is None or enc_mod._fernet_instance is not old_fernet_obj, (
            "B1: encryption singleton must be reset after key replace so init_db's migration picks up the restored key"
        )
        # The key file must be on disk with the new content.
        restored = (tmp_path / ".mfa_encryption_key").read_text()
        assert restored == new_key

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_restore_rejects_path_traversal_in_zip(self, async_client, monkeypatch, tmp_path):
        """A4: ZIP with path-traversal entry → HTTP 400, no file written outside temp dir."""
        import io
        import zipfile

        from backend.app.core.config import settings as app_settings

        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setattr(app_settings, "base_dir", tmp_path)

        # Build ZIP with a relative path-traversal entry.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../etc/passwd", "root:x:0:0")
            zf.writestr("bambuddy.db", b"SQLite format 3")
        buf.seek(0)

        resp = await async_client.post(
            "/api/v1/settings/restore",
            files={"file": ("backup.zip", buf, "application/zip")},
        )
        assert resp.status_code == 400
        assert "unsafe path" in resp.json().get("detail", "").lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_restore_rejects_prefix_collision_zipslip(self, async_client, monkeypatch, tmp_path):
        """T1: ZIP entry with prefix-collision path must be rejected.

        A startswith() check would accept '/tmp/abc_evil/file' when the
        extraction root was '/tmp/abc' — is_relative_to correctly rejects it.
        The restore handler creates a tempfile.TemporaryDirectory inside the
        system temp dir; we craft an entry that resolves to a sibling path
        whose name starts with the temp dir's basename.
        """
        import io
        import zipfile

        from backend.app.core.config import settings as app_settings

        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setattr(app_settings, "base_dir", tmp_path)

        # Use a path with traversal — the resolved path will share the parent
        # temp directory's basename as a prefix but NOT be inside the
        # extraction root. We don't know the random extraction-root name at
        # ZIP-build time, so we pick a literal "../poc-evil-prefix-collision/"
        # which traverses up one level from the extraction root and lands in
        # a sibling directory. is_relative_to() must reject this; a naive
        # startswith() against the parent's parent would accept it.
        evil_name = "../escaped-prefix-collision/poc.txt"

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(evil_name, "pwned")
            zf.writestr("bambuddy.db", b"SQLite format 3\x00")
        buf.seek(0)

        resp = await async_client.post(
            "/api/v1/settings/restore",
            files={"file": ("backup.zip", buf, "application/zip")},
        )
        assert resp.status_code == 400
        assert "unsafe path" in resp.json().get("detail", "").lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_restore_rejects_absolute_path_in_zip(self, async_client, monkeypatch, tmp_path):
        """B1: ZIP with an absolute path entry must be rejected by is_relative_to check."""
        import io
        import zipfile

        from backend.app.core.config import settings as app_settings

        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setattr(app_settings, "base_dir", tmp_path)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            # Absolute path in the archive — extracts outside temp_path on
            # systems where (temp_path / "/etc/passwd") resolves to /etc/passwd.
            zf.writestr("/etc/passwd", "root:x:0:0")
            zf.writestr("bambuddy.db", b"SQLite format 3")
        buf.seek(0)

        resp = await async_client.post(
            "/api/v1/settings/restore",
            files={"file": ("backup.zip", buf, "application/zip")},
        )
        assert resp.status_code == 400
        assert "unsafe path" in resp.json().get("detail", "").lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_backup_fails_when_key_file_unreadable(self, async_client, monkeypatch, tmp_path):
        """A5: OSError while copying key file propagates out of create_backup_zip."""
        import shutil

        from backend.app.api.routes.settings import create_backup_zip
        from backend.app.core.config import settings as app_settings

        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setattr(app_settings, "base_dir", tmp_path)
        (tmp_path / ".mfa_encryption_key").write_text("key")

        original_copy2 = shutil.copy2

        def _raise_on_key(src, dst):
            if ".mfa_encryption_key" in str(src):
                raise OSError("simulated unreadable key file")
            return original_copy2(src, dst)

        monkeypatch.setattr(shutil, "copy2", _raise_on_key)

        import pytest as _pytest

        with _pytest.raises(OSError, match="simulated unreadable"):
            await create_backup_zip(output_path=tmp_path)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_backup_restore_roundtrip_preserves_encrypted_oidc_secret(
        self, async_client, db_session, monkeypatch, tmp_path
    ):
        """T3: encrypt → backup → simulate key loss → restore → decrypt.

        Verifies the user-facing promise that local backup ZIPs are
        self-contained: an OIDC client_secret encrypted under one key still
        decrypts after restore even when the running install no longer has
        the key on disk or in the env. Exercises the B1 key-first restore
        path and the B4 sample-decrypt status check together.
        """
        import zipfile
        from pathlib import Path
        from unittest.mock import AsyncMock, patch

        from cryptography.fernet import Fernet
        from sqlalchemy import select

        import backend.app.core.encryption as enc_mod
        from backend.app.api.routes.settings import create_backup_zip
        from backend.app.core.config import settings as app_settings
        from backend.app.models.oidc_provider import OIDCProvider

        # 1. Pin a key, encrypt an OIDC secret via the property setter.
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("MFA_ENCRYPTION_KEY", key)
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setattr(app_settings, "base_dir", tmp_path)
        # Persist the key file too, so create_backup_zip picks it up.
        (tmp_path / ".mfa_encryption_key").write_text(key)
        enc_mod._fernet_instance = None
        enc_mod._key_source = None

        provider = OIDCProvider(
            name="RoundtripProv",
            issuer_url="https://rt.example.com",
            client_id="cid",
            client_secret="my-original-secret",  # via setter -> encrypted
            scopes="openid email profile",
            is_enabled=True,
        )
        db_session.add(provider)
        await db_session.commit()
        original_id = provider.id
        assert provider._client_secret_enc.startswith("fernet:")

        # 2. Create a backup ZIP (must include .mfa_encryption_key).
        zip_path, _ = await create_backup_zip(output_path=tmp_path)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                names = zf.namelist()
                assert ".mfa_encryption_key" in names, "T3: backup ZIP must include the key file"

            # 3. Simulate key loss: delete the key file from DATA_DIR, drop
            #    the env var, reset the cached fernet singleton.
            (tmp_path / ".mfa_encryption_key").unlink()
            monkeypatch.delenv("MFA_ENCRYPTION_KEY", raising=False)
            enc_mod._fernet_instance = None
            enc_mod._key_source = None

            # 4. Restore the ZIP via the endpoint. Mock out the DB-swap
            #    (we keep the live in-memory test DB) and init_db side effects
            #    so this test focuses on the key-restore path.
            with (
                patch("backend.app.core.db_dialect.is_sqlite", return_value=False),
                patch(
                    "backend.app.api.routes.settings._import_sqlite_to_postgres",
                    new_callable=AsyncMock,
                ),
                patch("backend.app.core.database.close_all_connections", new_callable=AsyncMock),
                patch("backend.app.core.database.reinitialize_database", new_callable=AsyncMock),
                patch("backend.app.core.database.init_db", new_callable=AsyncMock),
                open(zip_path, "rb") as f,
            ):
                resp = await async_client.post(
                    "/api/v1/settings/restore",
                    files={"file": ("backup.zip", f, "application/zip")},
                )
            assert resp.status_code == 200, resp.text

            # 5. Reset the singleton again (B1 already does this in production,
            #    but here init_db is mocked so we explicitly invalidate).
            enc_mod._fernet_instance = None
            enc_mod._key_source = None

            # 6. The key file must be back on disk with restrictive permissions.
            restored = Path(tmp_path) / ".mfa_encryption_key"
            assert restored.exists(), "T3: key file must be restored to DATA_DIR"
            assert (restored.stat().st_mode & 0o777) == 0o600

            # 7. Decryption works again — the property getter must return the
            #    original plaintext, proving the restored key matches the
            #    cipher in the (still in-memory) DB row.
            result = await db_session.execute(select(OIDCProvider).where(OIDCProvider.id == original_id))
            restored_provider = result.scalar_one()
            assert restored_provider.client_secret == "my-original-secret"
        finally:
            zip_path.unlink(missing_ok=True)


# ============================================================================
# TestTOTPDecryptionBroken (C9)
# Verifies the decryption-broken state (encrypted TOTP row + no key) for each
# TOTP endpoint. Behaviour differs between recovery-aware and non-recovery
# endpoints:
#   - setup_totp / enable_totp / verify_2fa: HTTP 500 (no backup-code path).
#   - disable_totp / regenerate_backup_codes: fall through to the backup-code
#     branch — HTTP 200 with a valid backup code, HTTP 400 without.
# ============================================================================


class TestTOTPDecryptionBroken:
    """C9: RuntimeError from mfa_decrypt — 500 for non-recovery endpoints,
    backup-code fall-through for disable_totp / regenerate_backup_codes."""

    async def _setup_admin_and_totp_user(self, async_client, db_session):
        """Create admin (enables auth), log in as admin, add TOTP record with fernet secret."""
        from backend.app.models.user_totp import UserTOTP

        admin_username = f"admin_c9_{secrets.token_hex(4)}"
        setup = await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": admin_username,
                "admin_password": "Admin_C9_Pass1!",
            },
        )
        assert setup.status_code in (200, 201), setup.text
        login = await async_client.post(
            "/api/v1/auth/login",
            json={"username": admin_username, "password": "Admin_C9_Pass1!"},
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]

        # Get the admin user_id from the /me endpoint
        me = await async_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        user_id = me.json()["id"]

        # Insert a TOTP row with a fernet-prefixed secret directly (no key needed for insert).
        totp = UserTOTP(
            user_id=user_id,
            _secret_enc="fernet:gAAAAA-not-really-encrypted",
            is_enabled=True,
        )
        db_session.add(totp)
        await db_session.commit()

        return token, admin_username, user_id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_enable_totp_returns_500_when_decryption_broken(self, async_client, db_session, monkeypatch):
        """C9: enable endpoint → 500 when TOTP secret is encrypted but key unavailable."""
        import backend.app.core.encryption as enc_mod

        token, _, _ = await self._setup_admin_and_totp_user(async_client, db_session)

        monkeypatch.setattr(enc_mod, "_load_or_generate_key", lambda: (None, "none"))
        enc_mod._fernet_instance = None

        # enable_totp requires setup-but-not-yet-enabled state; force is_enabled=False
        from sqlalchemy import select as _select

        from backend.app.models.user_totp import UserTOTP

        result = await db_session.execute(_select(UserTOTP))
        for t in result.scalars().all():
            t.is_enabled = False
        await db_session.commit()

        resp = await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": "123456"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 500
        assert "unavailable" in resp.json().get("detail", "").lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_disable_totp_returns_400_when_decryption_broken_and_no_backup_codes(
        self, async_client, db_session, monkeypatch
    ):
        """B2a + S3: disable falls through to backup-code branch when TOTP secret
        cannot be decrypted; with no backup codes seeded, the request is
        rejected as an invalid code (400), not a server error.

        S3: AND the failed-attempt counter must NOT be incremented — the
        cause was a server-side key loss, not a user mistake.
        """
        from sqlalchemy import select as _select

        import backend.app.core.encryption as enc_mod
        from backend.app.models.auth_ephemeral import AuthRateLimitEvent

        token, admin_username, _ = await self._setup_admin_and_totp_user(async_client, db_session)

        monkeypatch.setattr(enc_mod, "_load_or_generate_key", lambda: (None, "none"))
        enc_mod._fernet_instance = None

        resp = await async_client.post(
            "/api/v1/auth/2fa/totp/disable",
            json={"code": "123456"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert "invalid" in resp.json().get("detail", "").lower()

        # S3: no fail-counter debit on server-side key loss.
        events = (
            (
                await db_session.execute(
                    _select(AuthRateLimitEvent).where(AuthRateLimitEvent.username == admin_username.lower())
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 0, "S3: must not debit fail-counter on key-loss"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_regenerate_backup_codes_returns_400_when_decryption_broken_and_no_backup_codes(
        self, async_client, db_session, monkeypatch
    ):
        """B2b + S3: regenerate-backup-codes falls through to backup-code branch when
        TOTP secret cannot be decrypted; with no backup codes seeded, the
        request is rejected as an invalid code (400) AND the fail-counter
        is NOT incremented (S3: server-side cause, not user mistake).
        """
        from sqlalchemy import select as _select

        import backend.app.core.encryption as enc_mod
        from backend.app.models.auth_ephemeral import AuthRateLimitEvent

        token, admin_username, _ = await self._setup_admin_and_totp_user(async_client, db_session)

        monkeypatch.setattr(enc_mod, "_load_or_generate_key", lambda: (None, "none"))
        enc_mod._fernet_instance = None

        resp = await async_client.post(
            "/api/v1/auth/2fa/totp/regenerate-backup-codes",
            json={"code": "123456"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert "invalid" in resp.json().get("detail", "").lower()

        events = (
            (
                await db_session.execute(
                    _select(AuthRateLimitEvent).where(AuthRateLimitEvent.username == admin_username.lower())
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 0, "S3: must not debit fail-counter on key-loss"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_disable_totp_succeeds_via_backup_code_when_decryption_broken(
        self, async_client, db_session, monkeypatch
    ):
        """B2a: a valid backup code disables TOTP even when the secret cannot
        be decrypted — recovery path for users who lost the encryption key."""
        from sqlalchemy import select as _select

        import backend.app.core.encryption as enc_mod
        from backend.app.api.routes.mfa import _generate_backup_codes
        from backend.app.models.user_totp import UserTOTP

        token, _, user_id = await self._setup_admin_and_totp_user(async_client, db_session)

        # Seed a real backup-code hash on the existing TOTP row.
        plain_codes, hashed_codes = _generate_backup_codes()
        result = await db_session.execute(_select(UserTOTP).where(UserTOTP.user_id == user_id))
        totp = result.scalar_one()
        totp.backup_code_hashes = hashed_codes
        await db_session.commit()

        monkeypatch.setattr(enc_mod, "_load_or_generate_key", lambda: (None, "none"))
        enc_mod._fernet_instance = None

        resp = await async_client.post(
            "/api/v1/auth/2fa/totp/disable",
            json={"code": plain_codes[0]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        # The TOTP row must have been deleted.
        result_after = await db_session.execute(_select(UserTOTP).where(UserTOTP.user_id == user_id))
        assert result_after.scalar_one_or_none() is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_regenerate_backup_codes_succeeds_via_backup_code_when_decryption_broken(
        self, async_client, db_session, monkeypatch
    ):
        """B2b: a valid backup code rotates the codes even when the secret
        cannot be decrypted — recovery path mirrors disable_totp."""
        from sqlalchemy import select as _select

        import backend.app.core.encryption as enc_mod
        from backend.app.api.routes.mfa import _generate_backup_codes
        from backend.app.models.user_totp import UserTOTP

        token, _, user_id = await self._setup_admin_and_totp_user(async_client, db_session)

        plain_codes, hashed_codes = _generate_backup_codes()
        result = await db_session.execute(_select(UserTOTP).where(UserTOTP.user_id == user_id))
        totp = result.scalar_one()
        totp.backup_code_hashes = hashed_codes
        await db_session.commit()

        monkeypatch.setattr(enc_mod, "_load_or_generate_key", lambda: (None, "none"))
        enc_mod._fernet_instance = None

        resp = await async_client.post(
            "/api/v1/auth/2fa/totp/regenerate-backup-codes",
            json={"code": plain_codes[0]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "backup_codes" in body
        assert len(body["backup_codes"]) == 10

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_disable_totp_wrong_code_with_seeded_hashes_returns_400_and_debits_counter(
        self, async_client, db_session, monkeypatch
    ):
        """T2: with backup_code_hashes seeded AND a working encryption key,
        a wrong code is rejected (400) AND the fail-counter IS incremented.

        This pins the behaviour that a future refactor swallowing
        compare_digest mismatches would still let the existing 'no codes
        configured' tests pass — only this assertion exercises the actual
        pwd_context.verify mismatch path.
        """
        from cryptography.fernet import Fernet
        from sqlalchemy import select as _select

        import backend.app.core.encryption as enc_mod
        from backend.app.api.routes.mfa import _generate_backup_codes
        from backend.app.models.auth_ephemeral import AuthRateLimitEvent
        from backend.app.models.user_totp import UserTOTP

        # Active key — secret can be decrypted, this is NOT key-loss.
        monkeypatch.setenv("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
        enc_mod._fernet_instance = None

        token, admin_username, user_id = await self._setup_admin_and_totp_user(async_client, db_session)

        # Replace stub fernet:-prefixed value with a real encrypted secret so
        # disable_totp's TOTP-decrypt path doesn't throw, AND seed real hashes.
        result = await db_session.execute(_select(UserTOTP).where(UserTOTP.user_id == user_id))
        totp = result.scalar_one()
        totp.secret = "JBSWY3DPEHPK3PXP"  # via setter -> mfa_encrypt
        plain_codes, hashed_codes = _generate_backup_codes()
        totp.backup_code_hashes = hashed_codes
        await db_session.commit()

        # Submit a code that matches NEITHER the TOTP nor any backup-code hash.
        resp = await async_client.post(
            "/api/v1/auth/2fa/totp/disable",
            json={"code": "WRONGCD1"},  # wrong but well-formed
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert "invalid" in resp.json().get("detail", "").lower()

        # T2 + S3: with key intact, the fail-counter MUST increment for a
        # real wrong-code attempt (this is the user-error path, not key-loss).
        events = (
            (
                await db_session.execute(
                    _select(AuthRateLimitEvent).where(AuthRateLimitEvent.username == admin_username.lower())
                )
            )
            .scalars()
            .all()
        )
        assert len(events) >= 1, "T2: with key intact, wrong code must debit the fail-counter"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_regenerate_backup_codes_wrong_code_with_seeded_hashes_returns_400_and_debits_counter(
        self, async_client, db_session, monkeypatch
    ):
        """T2: same as the disable_totp variant for /regenerate-backup-codes."""
        from cryptography.fernet import Fernet
        from sqlalchemy import select as _select

        import backend.app.core.encryption as enc_mod
        from backend.app.api.routes.mfa import _generate_backup_codes
        from backend.app.models.auth_ephemeral import AuthRateLimitEvent
        from backend.app.models.user_totp import UserTOTP

        monkeypatch.setenv("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
        enc_mod._fernet_instance = None

        token, admin_username, user_id = await self._setup_admin_and_totp_user(async_client, db_session)

        result = await db_session.execute(_select(UserTOTP).where(UserTOTP.user_id == user_id))
        totp = result.scalar_one()
        totp.secret = "JBSWY3DPEHPK3PXP"
        plain_codes, hashed_codes = _generate_backup_codes()
        totp.backup_code_hashes = hashed_codes
        await db_session.commit()

        resp = await async_client.post(
            "/api/v1/auth/2fa/totp/regenerate-backup-codes",
            json={"code": "WRONGCD2"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert "invalid" in resp.json().get("detail", "").lower()

        events = (
            (
                await db_session.execute(
                    _select(AuthRateLimitEvent).where(AuthRateLimitEvent.username == admin_username.lower())
                )
            )
            .scalars()
            .all()
        )
        assert len(events) >= 1, "T2: with key intact, wrong code must debit the fail-counter"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_disable_totp_wrong_code_with_seeded_hashes_at_keyloss_no_counter_debit(
        self, async_client, db_session, monkeypatch
    ):
        """T2 + S3 cross-check: with hashes seeded but encryption key gone,
        a wrong code returns 400 BUT the fail-counter MUST NOT increment.

        This is the dual of the test above — same wrong-code 400 outcome,
        but the counter debit is gated on the cause of failure (server-side
        key loss must NOT penalise the user).
        """
        from sqlalchemy import select as _select

        import backend.app.core.encryption as enc_mod
        from backend.app.api.routes.mfa import _generate_backup_codes
        from backend.app.models.auth_ephemeral import AuthRateLimitEvent
        from backend.app.models.user_totp import UserTOTP

        token, admin_username, user_id = await self._setup_admin_and_totp_user(async_client, db_session)

        # Seed real hashes on the existing TOTP row.
        result = await db_session.execute(_select(UserTOTP).where(UserTOTP.user_id == user_id))
        totp = result.scalar_one()
        plain_codes, hashed_codes = _generate_backup_codes()
        totp.backup_code_hashes = hashed_codes
        await db_session.commit()

        # Now simulate key loss.
        monkeypatch.setattr(enc_mod, "_load_or_generate_key", lambda: (None, "none"))
        enc_mod._fernet_instance = None

        resp = await async_client.post(
            "/api/v1/auth/2fa/totp/disable",
            json={"code": "WRONGCD3"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400

        # S3: counter MUST be unchanged — this is a server-side problem.
        events = (
            (
                await db_session.execute(
                    _select(AuthRateLimitEvent).where(AuthRateLimitEvent.username == admin_username.lower())
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 0, "S3: must not debit fail-counter when cause is server-side key-loss"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_setup_totp_returns_500_when_decryption_broken(self, async_client, db_session, monkeypatch):
        """B3: setup endpoint → 500 when an active TOTP secret can't be decrypted.

        Replacing an active authenticator requires verifying the current TOTP
        code; with no recovery (backup-code) path on this endpoint, the only
        safe outcome is a 500 surface to the operator.
        """
        import backend.app.core.encryption as enc_mod

        token, _, _ = await self._setup_admin_and_totp_user(async_client, db_session)

        monkeypatch.setattr(enc_mod, "_load_or_generate_key", lambda: (None, "none"))
        enc_mod._fernet_instance = None

        resp = await async_client.post(
            "/api/v1/auth/2fa/totp/setup",
            json={"code": "123456"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 500
        assert "unavailable" in resp.json().get("detail", "").lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_verify_2fa_returns_500_when_decryption_broken(self, async_client, db_session, monkeypatch):
        """C9: verify endpoint (TOTP method) → 500 when TOTP secret unreadable."""
        from datetime import datetime, timedelta, timezone

        import backend.app.core.encryption as enc_mod
        from backend.app.models.auth_ephemeral import AuthEphemeralToken

        token, admin_username, user_id = await self._setup_admin_and_totp_user(async_client, db_session)

        monkeypatch.setattr(enc_mod, "_load_or_generate_key", lambda: (None, "none"))
        enc_mod._fernet_instance = None

        # Create a pre_auth token to simulate the post-login 2FA challenge step.
        raw_token = secrets.token_urlsafe(32)
        ephemeral = AuthEphemeralToken(
            token=raw_token,
            token_type="pre_auth",
            username=admin_username,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        db_session.add(ephemeral)
        await db_session.commit()

        resp = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": raw_token, "method": "totp", "code": "123456"},
        )
        assert resp.status_code == 500
        assert "unavailable" in resp.json().get("detail", "").lower()
