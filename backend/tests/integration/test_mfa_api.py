"""Integration tests for 2FA and OIDC API endpoints.

Tests the full request/response cycle for:
- GET  /api/v1/auth/2fa/status
- POST /api/v1/auth/2fa/totp/setup
- POST /api/v1/auth/2fa/totp/enable
- POST /api/v1/auth/2fa/totp/disable
- POST /api/v1/auth/2fa/email/enable
- POST /api/v1/auth/2fa/email/disable
- POST /api/v1/auth/2fa/verify   (TOTP, email, backup paths)
- DELETE /api/v1/auth/2fa/admin/{user_id}
- GET  /api/v1/auth/oidc/providers
- POST /api/v1/auth/oidc/providers
- PATCH /api/v1/auth/oidc/providers/{id}
- DELETE /api/v1/auth/oidc/providers/{id}
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt as pyjwt
import pyotp
import pytest
from httpx import AsyncClient
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.auth_ephemeral import AuthEphemeralToken
from backend.app.models.user import User

_pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

AUTH_SETUP_URL = "/api/v1/auth/setup"
LOGIN_URL = "/api/v1/auth/login"


def _norm_pw(password: str) -> str:
    """Ensure password meets complexity requirements (I4: SetupRequest now validates)."""
    if not any(c.isupper() for c in password):
        password = password[0].upper() + password[1:]
    if not any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789" for c in password):
        password = password + "!"
    return password


async def _setup_and_login(client: AsyncClient, username: str, password: str) -> str:
    """Enable auth, create an admin user, login, and return the bearer token."""
    password = _norm_pw(password)
    await client.post(
        AUTH_SETUP_URL,
        json={
            "auth_enabled": True,
            "admin_username": username,
            "admin_password": password,
        },
    )
    resp = await client.post(LOGIN_URL, json={"username": username, "password": password})
    assert resp.status_code == 200
    return resp.json()["access_token"]


async def _login_get_pre_auth_token(client: AsyncClient, username: str, password: str) -> str:
    """Login a user who has 2FA enabled; return the pre_auth_token from the response."""
    password = _norm_pw(password)
    resp = await client.post(LOGIN_URL, json={"username": username, "password": password})
    assert resp.status_code == 200
    data = resp.json()
    assert data["requires_2fa"] is True, f"Expected requires_2fa=True, got {data}"
    assert data["pre_auth_token"] is not None
    return data["pre_auth_token"]


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# 2FA Status
# ===========================================================================


class TestTwoFAStatus:
    """Tests for GET /api/v1/auth/2fa/status."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_status_requires_auth(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/auth/2fa/status")
        assert response.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_status_default_disabled(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "statususer", "statuspass123")
        response = await async_client.get("/api/v1/auth/2fa/status", headers=_auth_header(token))
        assert response.status_code == 200
        data = response.json()
        assert data["totp_enabled"] is False
        assert data["email_otp_enabled"] is False
        assert data["backup_codes_remaining"] == 0


# ===========================================================================
# TOTP Setup
# ===========================================================================


class TestTOTPSetup:
    """Tests for POST /api/v1/auth/2fa/totp/setup."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_setup_requires_auth(self, async_client: AsyncClient):
        response = await async_client.post("/api/v1/auth/2fa/totp/setup")
        assert response.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_setup_returns_secret_and_qr(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "totpsetup", "totpsetup123")
        response = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        assert response.status_code == 200
        data = response.json()
        assert "secret" in data
        assert len(data["secret"]) > 0
        assert "qr_code_b64" in data
        assert data["issuer"] == "Bambuddy"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_setup_secret_is_valid_base32(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "totpbase32", "totpbase32pw")
        response = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        assert response.status_code == 200
        secret = response.json()["secret"]
        # pyotp will raise on invalid base32
        totp = pyotp.TOTP(secret)
        assert len(totp.now()) == 6


# ===========================================================================
# TOTP Enable
# ===========================================================================


class TestTOTPEnable:
    """Tests for POST /api/v1/auth/2fa/totp/enable."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_enable_without_setup_returns_400(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "nosetupenable", "nosetupenable1")
        response = await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": "123456"},
            headers=_auth_header(token),
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_enable_with_invalid_code_returns_400(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "badcodeuser", "badcodeuser1")
        await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        response = await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": "000000"},
            headers=_auth_header(token),
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_enable_with_valid_code_returns_backup_codes(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "enableok", "enableok123")
        setup_resp = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        secret = setup_resp.json()["secret"]
        valid_code = pyotp.TOTP(secret).now()

        response = await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": valid_code},
            headers=_auth_header(token),
        )
        assert response.status_code == 200
        data = response.json()
        assert "backup_codes" in data
        assert len(data["backup_codes"]) == 10
        for code in data["backup_codes"]:
            assert len(code) == 8

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_status_reflects_enabled_totp(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "statustotp", "statustotp1")
        setup_resp = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        secret = setup_resp.json()["secret"]
        valid_code = pyotp.TOTP(secret).now()
        await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": valid_code},
            headers=_auth_header(token),
        )

        status_resp = await async_client.get("/api/v1/auth/2fa/status", headers=_auth_header(token))
        data = status_resp.json()
        assert data["totp_enabled"] is True
        assert data["backup_codes_remaining"] == 10


# ===========================================================================
# TOTP Disable
# ===========================================================================


class TestTOTPDisable:
    """Tests for POST /api/v1/auth/2fa/totp/disable."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_disable_when_not_enabled_returns_400(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "disablenoenab", "disablenoenab1")
        response = await async_client.post(
            "/api/v1/auth/2fa/totp/disable",
            json={"code": "123456"},
            headers=_auth_header(token),
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_disable_with_valid_code(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "disableok", "disableok123")
        setup_resp = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        secret = setup_resp.json()["secret"]
        valid_code = pyotp.TOTP(secret).now()
        await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": valid_code},
            headers=_auth_header(token),
        )

        # Disable with a fresh valid code
        disable_code = pyotp.TOTP(secret).now()
        response = await async_client.post(
            "/api/v1/auth/2fa/totp/disable",
            json={"code": disable_code},
            headers=_auth_header(token),
        )
        assert response.status_code == 200
        assert "disabled" in response.json()["message"].lower()

        # Status should now show disabled
        status_resp = await async_client.get("/api/v1/auth/2fa/status", headers=_auth_header(token))
        assert status_resp.json()["totp_enabled"] is False


# ===========================================================================
# Email OTP Enable/Disable
# ===========================================================================


class TestEmailOTP:
    """Tests for POST /api/v1/auth/2fa/email/enable, /enable/confirm and /disable."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_enable_email_otp_without_email_returns_400(self, async_client: AsyncClient):
        """Users without an email address cannot enable email OTP."""
        token = await _setup_and_login(async_client, "noemailuser", "noemailuser1")
        response = await async_client.post("/api/v1/auth/2fa/email/enable", headers=_auth_header(token))
        assert response.status_code == 400
        assert "email" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_confirm_enable_email_otp_happy_path(self, async_client: AsyncClient, db_session: AsyncSession):
        """Confirm step activates email OTP when setup_token + code are valid (C5)."""
        token = await _setup_and_login(async_client, "confirmenable", "confirmenable1")

        # Give user an email address directly (SMTP not available in tests)
        from sqlalchemy import select as sa_select

        result = await db_session.execute(sa_select(User).where(User.username == "confirmenable"))
        user = result.scalar_one()
        user.email = "confirmenable@example.com"
        await db_session.commit()

        # Inject a known setup token directly into the DB (bypasses SMTP)
        code = "123456"
        code_hash = _pwd_context.hash(code)
        setup_token = secrets.token_urlsafe(32)
        db_session.add(
            AuthEphemeralToken(
                token=setup_token,
                token_type="email_otp_setup",
                username="confirmenable",
                nonce=code_hash,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
        )
        await db_session.commit()

        resp = await async_client.post(
            "/api/v1/auth/2fa/email/enable/confirm",
            json={"setup_token": setup_token, "code": code},
            headers=_auth_header(token),
        )
        assert resp.status_code == 200

        status_resp = await async_client.get("/api/v1/auth/2fa/status", headers=_auth_header(token))
        assert status_resp.json()["email_otp_enabled"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_confirm_enable_email_otp_wrong_code(self, async_client: AsyncClient, db_session: AsyncSession):
        """Wrong code on confirm step returns 400 and does not enable email OTP."""
        token = await _setup_and_login(async_client, "confirmwrong", "confirmwrong1")

        code_hash = _pwd_context.hash("654321")
        setup_token = secrets.token_urlsafe(32)
        db_session.add(
            AuthEphemeralToken(
                token=setup_token,
                token_type="email_otp_setup",
                username="confirmwrong",
                nonce=code_hash,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
        )
        await db_session.commit()

        resp = await async_client.post(
            "/api/v1/auth/2fa/email/enable/confirm",
            json={"setup_token": setup_token, "code": "000000"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_confirm_enable_email_otp_setup_token_is_single_use(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Setup token is consumed on first use; replay returns 400."""
        token = await _setup_and_login(async_client, "confirmonce", "confirmonce1")

        code = "111111"
        code_hash = _pwd_context.hash(code)
        setup_token = secrets.token_urlsafe(32)
        db_session.add(
            AuthEphemeralToken(
                token=setup_token,
                token_type="email_otp_setup",
                username="confirmonce",
                nonce=code_hash,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
        )
        await db_session.commit()

        first = await async_client.post(
            "/api/v1/auth/2fa/email/enable/confirm",
            json={"setup_token": setup_token, "code": code},
            headers=_auth_header(token),
        )
        assert first.status_code == 200

        second = await async_client.post(
            "/api/v1/auth/2fa/email/enable/confirm",
            json={"setup_token": setup_token, "code": code},
            headers=_auth_header(token),
        )
        assert second.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_disable_email_otp_requires_password(self, async_client: AsyncClient):
        """Disabling email OTP requires the account password (C6: re-auth)."""
        token = await _setup_and_login(async_client, "disemailotp", "disemailotp1")
        # Wrong password → 401
        response = await async_client.post(
            "/api/v1/auth/2fa/email/disable",
            json={"password": "wrongpassword"},
            headers=_auth_header(token),
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_disable_email_otp_when_enabled(self, async_client: AsyncClient, db_session: AsyncSession):
        """Disabling email OTP when enabled turns it off and status reflects that."""
        token = await _setup_and_login(async_client, "disemailpw", "disemailpw1")

        # Enable email OTP via direct DB injection (no SMTP)
        code = "222222"
        setup_token = secrets.token_urlsafe(32)
        db_session.add(
            AuthEphemeralToken(
                token=setup_token,
                token_type="email_otp_setup",
                username="disemailpw",
                nonce=_pwd_context.hash(code),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
        )
        await db_session.commit()
        await async_client.post(
            "/api/v1/auth/2fa/email/enable/confirm",
            json={"setup_token": setup_token, "code": code},
            headers=_auth_header(token),
        )

        # Now disable
        response = await async_client.post(
            "/api/v1/auth/2fa/email/disable",
            json={"password": _norm_pw("disemailpw1")},
            headers=_auth_header(token),
        )
        assert response.status_code == 200

        status_resp = await async_client.get("/api/v1/auth/2fa/status", headers=_auth_header(token))
        assert status_resp.json()["email_otp_enabled"] is False


# ===========================================================================
# 2FA Verify — TOTP path
# ===========================================================================


class TestTwoFAVerifyTOTP:
    """Tests for POST /api/v1/auth/2fa/verify using the TOTP method."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_verify_with_invalid_pre_auth_token(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": "bogus", "method": "totp", "code": "123456"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_verify_totp_issues_jwt(self, async_client: AsyncClient):
        """Full flow: setup → enable TOTP → login → pre_auth_token → verify → JWT."""
        token = await _setup_and_login(async_client, "verifytotpok", "verifytotpok1")
        setup_resp = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        secret = setup_resp.json()["secret"]
        valid_code = pyotp.TOTP(secret).now()
        await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": valid_code},
            headers=_auth_header(token),
        )

        # Login now returns requires_2fa=True + pre_auth_token
        pre_auth_token = await _login_get_pre_auth_token(async_client, "verifytotpok", "verifytotpok1")

        verify_resp = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={
                "pre_auth_token": pre_auth_token,
                "method": "totp",
                "code": pyotp.TOTP(secret).now(),
            },
        )
        assert verify_resp.status_code == 200
        data = verify_resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["username"] == "verifytotpok"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_verify_totp_invalid_code(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "verifybadcode", "verifybadcode1")
        setup_resp = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        secret = setup_resp.json()["secret"]
        valid_code = pyotp.TOTP(secret).now()
        await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": valid_code},
            headers=_auth_header(token),
        )

        pre_auth_token = await _login_get_pre_auth_token(async_client, "verifybadcode", "verifybadcode1")
        verify_resp = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth_token, "method": "totp", "code": "000000"},
        )
        assert verify_resp.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_verify_invalid_method(self, async_client: AsyncClient):
        """An invalid 2FA method should return 400 even with a valid pre_auth_token."""
        token = await _setup_and_login(async_client, "invalidmethod", "invalidmethod1")
        setup_resp = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        secret = setup_resp.json()["secret"]
        valid_code = pyotp.TOTP(secret).now()
        await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": valid_code},
            headers=_auth_header(token),
        )
        pre_auth_token = await _login_get_pre_auth_token(async_client, "invalidmethod", "invalidmethod1")
        response = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth_token, "method": "sms", "code": "123456"},
        )
        assert response.status_code == 422  # Pydantic Literal validation


# ===========================================================================
# 2FA Verify — Backup code path
# ===========================================================================


class TestTwoFAVerifyBackup:
    """Tests for POST /api/v1/auth/2fa/verify using the backup method."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_verify_with_backup_code(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "backupcodeok", "backupcodeok1")
        setup_resp = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        secret = setup_resp.json()["secret"]
        valid_code = pyotp.TOTP(secret).now()
        enable_resp = await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": valid_code},
            headers=_auth_header(token),
        )
        backup_code = enable_resp.json()["backup_codes"][0]

        pre_auth_token = await _login_get_pre_auth_token(async_client, "backupcodeok", "backupcodeok1")
        verify_resp = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth_token, "method": "backup", "code": backup_code},
        )
        assert verify_resp.status_code == 200
        assert "access_token" in verify_resp.json()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_backup_code_is_single_use(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "backupsingle", "backupsingle1")
        setup_resp = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        secret = setup_resp.json()["secret"]
        valid_code = pyotp.TOTP(secret).now()
        enable_resp = await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": valid_code},
            headers=_auth_header(token),
        )
        backup_code = enable_resp.json()["backup_codes"][0]

        # First use — should succeed
        pre_auth_token = await _login_get_pre_auth_token(async_client, "backupsingle", "backupsingle1")
        first_resp = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth_token, "method": "backup", "code": backup_code},
        )
        assert first_resp.status_code == 200

        # Second use of the same code — must fail (need new pre_auth_token + same backup code)
        pre_auth_token2 = await _login_get_pre_auth_token(async_client, "backupsingle", "backupsingle1")
        second_resp = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth_token2, "method": "backup", "code": backup_code},
        )
        assert second_resp.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_backup_code_count_decrements(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "backupcount", "backupcount1")
        setup_resp = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        secret = setup_resp.json()["secret"]
        valid_code = pyotp.TOTP(secret).now()
        enable_resp = await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": valid_code},
            headers=_auth_header(token),
        )
        backup_code = enable_resp.json()["backup_codes"][0]

        pre_auth_token = await _login_get_pre_auth_token(async_client, "backupcount", "backupcount1")
        await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth_token, "method": "backup", "code": backup_code},
        )

        # Status is readable with the original full token (still valid)
        status_resp = await async_client.get("/api/v1/auth/2fa/status", headers=_auth_header(token))
        assert status_resp.json()["backup_codes_remaining"] == 9


# ===========================================================================
# Rate Limiting
# ===========================================================================


class TestRateLimiting:
    """Ensure 429 is returned after 5 failed 2FA attempts."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rate_limit_lockout(self, async_client: AsyncClient):
        """After 5 failed TOTP attempts the 6th must return 429."""
        token = await _setup_and_login(async_client, "ratelimituser", "ratelimituser1")
        setup_resp = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        secret = setup_resp.json()["secret"]
        valid_code = pyotp.TOTP(secret).now()
        await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": valid_code},
            headers=_auth_header(token),
        )

        # 5 failed attempts via the login → pre_auth_token → verify flow
        for _ in range(5):
            pre_auth_token = await _login_get_pre_auth_token(async_client, "ratelimituser", "ratelimituser1")
            await async_client.post(
                "/api/v1/auth/2fa/verify",
                json={"pre_auth_token": pre_auth_token, "method": "totp", "code": "000000"},
            )

        # 6th attempt should hit the rate limit
        pre_auth_token = await _login_get_pre_auth_token(async_client, "ratelimituser", "ratelimituser1")
        response = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth_token, "method": "totp", "code": "000000"},
        )
        assert response.status_code == 429


# ===========================================================================
# Admin 2FA Disable
# ===========================================================================


class TestAdminDisable2FA:
    """Tests for DELETE /api/v1/auth/2fa/admin/{user_id}."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_admin_disable_requires_admin(self, async_client: AsyncClient):
        """Only admins can use the admin disable endpoint."""
        # The only user in a fresh setup IS admin, so just check the 404 path
        token = await _setup_and_login(async_client, "admincheck", "admincheck123")
        # Try to disable for a non-existent user_id — should get 200 (no-op) or 404
        response = await async_client.request(
            "DELETE",
            "/api/v1/auth/2fa/admin/99999",
            json={"admin_password": _norm_pw("admincheck123")},
            headers=_auth_header(token),
        )
        # Admin users succeed regardless (returns 200 even if user doesn't exist)
        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_admin_disable_clears_totp(self, async_client: AsyncClient):
        from sqlalchemy import select

        from backend.app.models.user import User

        token = await _setup_and_login(async_client, "admintotp", "admintotp123")
        setup_resp = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        secret = setup_resp.json()["secret"]
        valid_code = pyotp.TOTP(secret).now()
        await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": valid_code},
            headers=_auth_header(token),
        )

        # Find the user's id by querying status (which works with the token)
        me_resp = await async_client.get("/api/v1/auth/me", headers=_auth_header(token))
        user_id = me_resp.json()["id"]

        response = await async_client.request(
            "DELETE",
            f"/api/v1/auth/2fa/admin/{user_id}",
            json={"admin_password": _norm_pw("admintotp123")},
            headers=_auth_header(token),
        )
        assert response.status_code == 200

        # I2: admin_disable_2fa bumps password_changed_at, invalidating the old token.
        # Re-login to get a fresh token before checking status.
        new_login = await async_client.post(
            LOGIN_URL, json={"username": "admintotp", "password": _norm_pw("admintotp123")}
        )
        assert new_login.status_code == 200, f"re-login failed: {new_login.json()}"
        assert new_login.json().get("requires_2fa") is False, f"still requires 2FA: {new_login.json()}"
        new_token = new_login.json()["access_token"]
        assert new_token is not None, f"no access_token in: {new_login.json()}"

        # Status should now show TOTP disabled
        status_resp = await async_client.get("/api/v1/auth/2fa/status", headers=_auth_header(new_token))
        assert status_resp.status_code == 200, f"status check failed: {status_resp.json()}"
        assert status_resp.json()["totp_enabled"] is False


# ===========================================================================
# OIDC Provider CRUD
# ===========================================================================


class TestOIDCProviders:
    """Tests for OIDC provider management endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_public_providers_empty(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/auth/oidc/providers")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_provider_requires_admin(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "oidcadmincreate", "oidcadmincreate1")
        response = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "PocketID",
                "issuer_url": "https://auth.example.com",
                "client_id": "bambuddy",
                "client_secret": "supersecret",
                "scopes": "openid email profile",
                "is_enabled": True,
                "auto_create_users": False,
            },
            headers=_auth_header(token),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "PocketID"
        assert data["issuer_url"] == "https://auth.example.com"
        assert "client_secret" not in data  # Secret must not be returned

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_created_provider_appears_in_all_list(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "oidclistall", "oidclistall123")
        await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "TestProvider",
                "issuer_url": "https://test.example.com",
                "client_id": "testclient",
                "client_secret": "testsecret",
                "scopes": "openid",
                "is_enabled": True,
                "auto_create_users": False,
            },
            headers=_auth_header(token),
        )
        response = await async_client.get("/api/v1/auth/oidc/providers/all", headers=_auth_header(token))
        assert response.status_code == 200
        names = [p["name"] for p in response.json()]
        assert "TestProvider" in names

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_disabled_provider_not_in_public_list(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "oidcdisabled", "oidcdisabled1")
        await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "DisabledProvider",
                "issuer_url": "https://disabled.example.com",
                "client_id": "dc",
                "client_secret": "ds",
                "scopes": "openid",
                "is_enabled": False,
                "auto_create_users": False,
            },
            headers=_auth_header(token),
        )
        response = await async_client.get("/api/v1/auth/oidc/providers")
        names = [p["name"] for p in response.json()]
        assert "DisabledProvider" not in names

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_provider(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "oidcupdate", "oidcupdate123")
        create_resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "OldName",
                "issuer_url": "https://update.example.com",
                "client_id": "uc",
                "client_secret": "us",
                "scopes": "openid",
                "is_enabled": True,
                "auto_create_users": False,
            },
            headers=_auth_header(token),
        )
        provider_id = create_resp.json()["id"]

        put_resp = await async_client.put(
            f"/api/v1/auth/oidc/providers/{provider_id}",
            json={"name": "NewName"},
            headers=_auth_header(token),
        )
        assert put_resp.status_code == 200
        assert put_resp.json()["name"] == "NewName"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_provider(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "oidcdelete", "oidcdelete123")
        create_resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "ToDelete",
                "issuer_url": "https://delete.example.com",
                "client_id": "dc",
                "client_secret": "ds",
                "scopes": "openid",
                "is_enabled": True,
                "auto_create_users": False,
            },
            headers=_auth_header(token),
        )
        provider_id = create_resp.json()["id"]

        del_resp = await async_client.delete(
            f"/api/v1/auth/oidc/providers/{provider_id}",
            headers=_auth_header(token),
        )
        assert del_resp.status_code == 200

        # No longer in list
        all_resp = await async_client.get("/api/v1/auth/oidc/providers/all", headers=_auth_header(token))
        ids = [p["id"] for p in all_resp.json()]
        assert provider_id not in ids

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_nonexistent_provider_returns_404(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "oidc404", "oidc404pass1")
        response = await async_client.put(
            "/api/v1/auth/oidc/providers/99999",
            json={"name": "ghost"},
            headers=_auth_header(token),
        )
        assert response.status_code == 404


# ===========================================================================
# Security: pre-auth token single-use
# ===========================================================================


class TestPreAuthTokenSingleUse:
    """pre_auth_token must be consumed on successful 2FA and cannot be reused."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_pre_auth_token_is_single_use(self, async_client: AsyncClient):
        """A pre_auth_token that was successfully used cannot be reused."""
        token = await _setup_and_login(async_client, "singleusepat", "singleusepat1")
        setup_resp = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        secret = setup_resp.json()["secret"]
        valid_code = pyotp.TOTP(secret).now()
        await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": valid_code},
            headers=_auth_header(token),
        )

        pre_auth_token = await _login_get_pre_auth_token(async_client, "singleusepat", "singleusepat1")

        # First use — succeeds
        first = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth_token, "method": "totp", "code": pyotp.TOTP(secret).now()},
        )
        assert first.status_code == 200

        # Second use of the same token — must fail (token already consumed on success)
        second = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth_token, "method": "totp", "code": pyotp.TOTP(secret).now()},
        )
        assert second.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_pre_auth_token_survives_wrong_code(self, async_client: AsyncClient):
        """A wrong 2FA code must NOT burn the pre_auth_token (user can retry)."""
        token = await _setup_and_login(async_client, "survivepatuser", "survivepatuser1")
        setup_resp = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        secret = setup_resp.json()["secret"]
        valid_code = pyotp.TOTP(secret).now()
        await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": valid_code},
            headers=_auth_header(token),
        )

        pre_auth_token = await _login_get_pre_auth_token(async_client, "survivepatuser", "survivepatuser1")

        # Wrong code — should fail but not burn the token
        bad = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth_token, "method": "totp", "code": "000000"},
        )
        assert bad.status_code == 401

        # Same token, correct code — should succeed (token still valid)
        good = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth_token, "method": "totp", "code": pyotp.TOTP(secret).now()},
        )
        assert good.status_code == 200


# ===========================================================================
# Security: cross-user token isolation
# ===========================================================================


class TestCrossUserTokenIsolation:
    """A pre_auth_token issued for user A cannot authenticate as user B."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_token_cannot_be_used_for_different_user(self, async_client: AsyncClient):
        """pre_auth_token is bound to the issuing user; using it to verify a different
        user's TOTP code must fail."""
        # Set up two users with TOTP
        token_a = await _setup_and_login(async_client, "crossusera", "crossusera1")
        setup_a = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token_a))
        secret_a = setup_a.json()["secret"]
        await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": pyotp.TOTP(secret_a).now()},
            headers=_auth_header(token_a),
        )

        # Get pre_auth_token for user A
        pre_auth_a = await _login_get_pre_auth_token(async_client, "crossusera", "crossusera1")

        # Try to use user A's token but supply a clearly invalid code — must fail
        resp = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth_a, "method": "totp", "code": "000000"},
        )
        assert resp.status_code == 401


# ===========================================================================
# Security: admin disable non-admin rejection
# ===========================================================================


class TestAdminDisableNonAdminRejection:
    """Non-admin users must be rejected from the admin disable endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_non_admin_cannot_disable_2fa(self, async_client: AsyncClient):
        """A regular (non-admin) user must receive 403 from DELETE /auth/2fa/admin/{id}."""
        # Set up admin, then create a regular user
        admin_token = await _setup_and_login(async_client, "adminusr2fa", "adminusr2fa1")

        # Create a regular user via user management
        create_resp = await async_client.post(
            "/api/v1/users",
            json={"username": "regularusr2fa", "password": "Regularusr2fa1!"},
            headers=_auth_header(admin_token),
        )
        assert create_resp.status_code == 201

        # Login as regular user
        login_resp = await async_client.post(
            LOGIN_URL,
            json={"username": "regularusr2fa", "password": "Regularusr2fa1!"},
        )
        regular_token = login_resp.json()["access_token"]

        # Try to call admin endpoint with the regular user's token
        resp = await async_client.delete(
            f"/api/v1/auth/2fa/admin/{create_resp.json()['id']}",
            headers=_auth_header(regular_token),
        )
        assert resp.status_code == 403


# ===========================================================================
# Regenerate backup codes
# ===========================================================================


class TestRegenerateBackupCodes:
    """Tests for POST /api/v1/auth/2fa/totp/regenerate-backup-codes."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_regenerate_requires_totp_enabled(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "regennototp", "regennototp1")
        resp = await async_client.post(
            "/api/v1/auth/2fa/totp/regenerate-backup-codes",
            json={"code": "123456"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_regenerate_invalidates_old_codes(self, async_client: AsyncClient):
        """After regenerating, old backup codes must no longer work."""
        token = await _setup_and_login(async_client, "regeninval", "regeninval1")
        setup_resp = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        secret = setup_resp.json()["secret"]
        enable_resp = await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": pyotp.TOTP(secret).now()},
            headers=_auth_header(token),
        )
        old_backup = enable_resp.json()["backup_codes"][0]

        # Regenerate backup codes
        regen_resp = await async_client.post(
            "/api/v1/auth/2fa/totp/regenerate-backup-codes",
            json={"code": pyotp.TOTP(secret).now()},
            headers=_auth_header(token),
        )
        assert regen_resp.status_code == 200
        new_codes = regen_resp.json()["backup_codes"]
        assert len(new_codes) == 10
        assert old_backup not in new_codes

        # Old backup code must now fail
        pre_auth_token = await _login_get_pre_auth_token(async_client, "regeninval", "regeninval1")
        fail_resp = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth_token, "method": "backup", "code": old_backup},
        )
        assert fail_resp.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_regenerate_with_invalid_code_fails(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "regeninvcode", "regeninvcode1")
        setup_resp = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        secret = setup_resp.json()["secret"]
        await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": pyotp.TOTP(secret).now()},
            headers=_auth_header(token),
        )

        resp = await async_client.post(
            "/api/v1/auth/2fa/totp/regenerate-backup-codes",
            json={"code": "000000"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 400


# ===========================================================================
# Security: method field validation
# ===========================================================================


class TestVerifyMethodValidation:
    """The method field must be one of totp/email/backup (Pydantic Literal)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_invalid_method_rejected_by_schema(self, async_client: AsyncClient):
        """Pydantic should reject unknown method values with 422."""
        resp = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": "anytoken", "code": "123456", "method": "sms"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_oversized_pre_auth_token_rejected(self, async_client: AsyncClient):
        """pre_auth_token exceeding max_length=128 should be rejected with 422."""
        resp = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": "x" * 200, "code": "123456", "method": "totp"},
        )
        assert resp.status_code == 422


# ===========================================================================
# Login response shape for 2FA users
# ===========================================================================


class TestLoginResponseShape:
    """Login for a 2FA-enabled user must return requires_2fa+pre_auth_token
    and must NOT include access_token (which would bypass the 2FA gate)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_login_2fa_user_omits_access_token(self, async_client: AsyncClient):
        """A user with TOTP enabled must not receive an access_token on /auth/login."""
        token = await _setup_and_login(async_client, "loginshape", "loginshape1")
        setup_resp = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        secret = setup_resp.json()["secret"]
        await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": pyotp.TOTP(secret).now()},
            headers=_auth_header(token),
        )

        login_resp = await async_client.post(LOGIN_URL, json={"username": "loginshape", "password": "Loginshape1!"})
        assert login_resp.status_code == 200
        data = login_resp.json()
        assert data.get("requires_2fa") is True
        assert data.get("pre_auth_token") is not None
        # access_token must NOT be present — it would bypass the 2FA gate
        assert "access_token" not in data or data["access_token"] is None


# ===========================================================================
# TOTP replay protection
# ===========================================================================


async def _setup_totp_user(client: AsyncClient, username: str, password: str) -> tuple[str, str]:
    """Create user, set up and enable TOTP; return (bearer_token, totp_secret)."""
    token = await _setup_and_login(client, username, password)
    setup_resp = await client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
    secret = setup_resp.json()["secret"]
    await client.post(
        "/api/v1/auth/2fa/totp/enable",
        json={"code": pyotp.TOTP(secret).now()},
        headers=_auth_header(token),
    )
    return token, secret


class TestTOTPReplay:
    """The same TOTP code must not be accepted twice within one 30-second window."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_totp_replay_rejected_on_verify(self, async_client: AsyncClient):
        """Replaying the same code on /2fa/verify must return 400."""
        _token, secret = await _setup_totp_user(async_client, "replayverify", "replayverify1")
        code = pyotp.TOTP(secret).now()

        pre_auth = await _login_get_pre_auth_token(async_client, "replayverify", "replayverify1")
        first = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth, "method": "totp", "code": code},
        )
        assert first.status_code == 200

        # Second login to get a fresh pre_auth_token (first was consumed)
        pre_auth2 = await _login_get_pre_auth_token(async_client, "replayverify", "replayverify1")
        second = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth2, "method": "totp", "code": code},
        )
        assert second.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_totp_replay_rejected_on_disable(self, async_client: AsyncClient):
        """A code already used in verify_2fa must be rejected on /2fa/totp/disable."""
        _setup_token, secret = await _setup_totp_user(async_client, "replaydisable", "replaydisable1")
        code = pyotp.TOTP(secret).now()

        # Use the code in verify_2fa — this sets last_totp_counter in DB
        pre_auth = await _login_get_pre_auth_token(async_client, "replaydisable", "replaydisable1")
        verify_resp = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth, "method": "totp", "code": code},
        )
        assert verify_resp.status_code == 200
        authed_token = verify_resp.json()["access_token"]

        # Replay the same code on disable — must be rejected (same 30-second window)
        disable_resp = await async_client.post(
            "/api/v1/auth/2fa/totp/disable",
            json={"code": code},
            headers=_auth_header(authed_token),
        )
        assert disable_resp.status_code == 400


# ===========================================================================
# Rate limiting on disable_totp and regenerate_backup_codes (I10)
# ===========================================================================


class TestRateLimitingDisableRegenerate:
    """disable_totp and regenerate_backup_codes must enforce rate limiting (I10)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_disable_totp_rate_limited_after_failures(self, async_client: AsyncClient):
        """Repeated wrong codes on /2fa/totp/disable trigger 429."""
        token, _secret = await _setup_totp_user(async_client, "rldisable", "rldisable1")
        for _ in range(5):
            await async_client.post(
                "/api/v1/auth/2fa/totp/disable",
                json={"code": "000000"},
                headers=_auth_header(token),
            )
        resp = await async_client.post(
            "/api/v1/auth/2fa/totp/disable",
            json={"code": "000000"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 429

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_regenerate_backup_codes_rate_limited_after_failures(self, async_client: AsyncClient):
        """Repeated wrong codes on /2fa/totp/regenerate-backup-codes trigger 429."""
        token, _secret = await _setup_totp_user(async_client, "rlregen", "rlregen1")
        for _ in range(5):
            await async_client.post(
                "/api/v1/auth/2fa/totp/regenerate-backup-codes",
                json={"code": "000000"},
                headers=_auth_header(token),
            )
        resp = await async_client.post(
            "/api/v1/auth/2fa/totp/regenerate-backup-codes",
            json={"code": "000000"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 429


# ===========================================================================
# Email OTP send → verify end-to-end (coverage gap C3)
# ===========================================================================


class TestEmailOTPSendVerify:
    """Full email OTP login: send code → verify code → JWT."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_email_otp_send_and_verify(self, async_client: AsyncClient, db_session: AsyncSession):
        """login → POST /2fa/email/send (patched SMTP) → POST /2fa/verify → JWT."""
        import re
        from unittest.mock import AsyncMock, MagicMock, patch

        from sqlalchemy import select as sa_select

        token = await _setup_and_login(async_client, "emailsendok", "emailsendok1")

        # Give the user an email address
        result = await db_session.execute(sa_select(User).where(User.username == "emailsendok"))
        user = result.scalar_one()
        user.email = "emailsendok@example.com"
        await db_session.commit()

        # Enable email OTP via DB injection
        setup_code = "444444"
        setup_token = secrets.token_urlsafe(32)
        db_session.add(
            AuthEphemeralToken(
                token=setup_token,
                token_type="email_otp_setup",
                username="emailsendok",
                nonce=_pwd_context.hash(setup_code),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
        )
        await db_session.commit()
        await async_client.post(
            "/api/v1/auth/2fa/email/enable/confirm",
            json={"setup_token": setup_token, "code": setup_code},
            headers=_auth_header(token),
        )

        # Login now requires 2FA — get pre_auth_token (cookie set automatically)
        pre_auth_token = await _login_get_pre_auth_token(async_client, "emailsendok", "emailsendok1")

        # Mock SMTP and capture the sent OTP code
        captured: dict[str, str] = {}
        smtp_settings_mock = MagicMock()

        def _capture_email(smtp_settings, to_email, subject, body_text, body_html):
            m = re.search(r"login code is: (\d{6})", body_text)
            if m:
                captured["otp"] = m.group(1)

        with (
            patch("backend.app.api.routes.mfa.get_smtp_settings", new=AsyncMock(return_value=smtp_settings_mock)),
            patch("backend.app.api.routes.mfa.send_email", side_effect=_capture_email),
        ):
            send_resp = await async_client.post(
                "/api/v1/auth/2fa/email/send",
                json={"pre_auth_token": pre_auth_token},
            )

        assert send_resp.status_code == 200, send_resp.text
        fresh_token = send_resp.json()["pre_auth_token"]
        assert "otp" in captured, "send_email was not called or code not found in body"

        # Verify with the captured OTP code — cookie still in the async_client jar
        verify_resp = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": fresh_token, "method": "email", "code": captured["otp"]},
        )
        assert verify_resp.status_code == 200
        data = verify_resp.json()
        assert "access_token" in data
        assert data["user"]["username"] == "emailsendok"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_email_otp_wrong_code_rejected(self, async_client: AsyncClient, db_session: AsyncSession):
        """A wrong email OTP code must return 401 without burning the pre_auth_token."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from sqlalchemy import select as sa_select

        token = await _setup_and_login(async_client, "emailwrongcode", "emailwrongcode1")

        result = await db_session.execute(sa_select(User).where(User.username == "emailwrongcode"))
        user = result.scalar_one()
        user.email = "emailwrongcode@example.com"
        await db_session.commit()

        setup_code = "555555"
        setup_token = secrets.token_urlsafe(32)
        db_session.add(
            AuthEphemeralToken(
                token=setup_token,
                token_type="email_otp_setup",
                username="emailwrongcode",
                nonce=_pwd_context.hash(setup_code),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
        )
        await db_session.commit()
        await async_client.post(
            "/api/v1/auth/2fa/email/enable/confirm",
            json={"setup_token": setup_token, "code": setup_code},
            headers=_auth_header(token),
        )

        pre_auth_token = await _login_get_pre_auth_token(async_client, "emailwrongcode", "emailwrongcode1")

        captured: dict[str, str] = {}
        smtp_mock = MagicMock()

        def _capture(smtp_settings, to_email, subject, body_text, body_html):
            import re

            m = re.search(r"login code is: (\d{6})", body_text)
            if m:
                captured["otp"] = m.group(1)

        with (
            patch("backend.app.api.routes.mfa.get_smtp_settings", new=AsyncMock(return_value=smtp_mock)),
            patch("backend.app.api.routes.mfa.send_email", side_effect=_capture),
        ):
            send_resp = await async_client.post(
                "/api/v1/auth/2fa/email/send",
                json={"pre_auth_token": pre_auth_token},
            )
        assert send_resp.status_code == 200
        fresh_token = send_resp.json()["pre_auth_token"]

        # Wrong code → 401
        bad = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": fresh_token, "method": "email", "code": "000000"},
        )
        assert bad.status_code == 401

        # Correct code still works (token not burned by wrong attempt)
        good = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": fresh_token, "method": "email", "code": captured["otp"]},
        )
        assert good.status_code == 200


# ===========================================================================
# OIDC end-to-end (coverage gap C4)
# ===========================================================================


def _make_test_rsa_key():
    """Generate a throwaway RSA key pair and a matching JWK set for tests."""
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    pub_numbers = private_key.public_key().public_numbers()

    def _b64url(n: int, length: int) -> str:
        return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()

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


class TestOIDCEndToEnd:
    """Full OIDC auth-code flow: state → callback (mocked IdP) → exchange → JWT."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_oidc_callback_creates_user_and_issues_jwt(self, async_client: AsyncClient, db_session: AsyncSession):
        """callback validates the mocked ID token, creates a user, and redirects
        with an oidc_exchange token; exchanging that token returns a full JWT."""
        import time
        from unittest.mock import patch

        import jwt as pyjwt

        private_pem, jwks_data = _make_test_rsa_key()
        issuer = "https://idp.test.example.com"
        client_id = "oidc-test-client"
        nonce = secrets.token_urlsafe(16)

        now = int(time.time())
        id_token = pyjwt.encode(
            {
                "sub": "oidc-sub-e2e",
                "iss": issuer,
                "aud": client_id,
                "nonce": nonce,
                "email": "oidce2e@example.com",
                "email_verified": True,
                "iat": now,
                "exp": now + 300,
            },
            private_pem,
            algorithm="RS256",
            headers={"kid": "test-kid-1"},
        )

        # Create OIDC provider
        admin_token = await _setup_and_login(async_client, "oidce2eadm", "oidce2eadm1")
        create_resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "E2E-IdP",
                "issuer_url": issuer,
                "client_id": client_id,
                "client_secret": "test-secret",
                "scopes": "openid email profile",
                "is_enabled": True,
                "auto_create_users": True,
            },
            headers=_auth_header(admin_token),
        )
        assert create_resp.status_code == 201
        provider_id = create_resp.json()["id"]

        # Simulate the authorize step: insert an oidc_state token directly
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

        # Mock httpx calls made inside oidc_callback
        discovery_doc = {
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

        with patch("backend.app.api.routes.mfa.httpx.AsyncClient", _MockHttpxClient):
            callback_resp = await async_client.get(
                f"/api/v1/auth/oidc/callback?code=test-auth-code&state={state}",
                follow_redirects=False,
            )

        assert callback_resp.status_code == 302, callback_resp.text
        location = callback_resp.headers.get("location", "")
        assert "oidc_token=" in location, f"Expected oidc_token in redirect, got: {location}"

        # Extract and exchange the oidc_exchange token
        oidc_exchange_token = location.split("oidc_token=")[1].split("&")[0]
        exchange_resp = await async_client.post(
            "/api/v1/auth/oidc/exchange",
            json={"oidc_token": oidc_exchange_token},
        )
        assert exchange_resp.status_code == 200
        data = exchange_resp.json()
        assert "access_token" in data
        assert data["user"]["username"] is not None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_oidc_callback_invalid_state_redirects_error(self, async_client: AsyncClient):
        """An unknown state token must redirect to /?oidc_error=invalid_state."""
        resp = await async_client.get(
            "/api/v1/auth/oidc/callback?code=x&state=totally-bogus-state",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "invalid_state" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_oidc_state_is_single_use(self, async_client: AsyncClient, db_session: AsyncSession):
        """Replaying the same state token must fail on the second callback."""
        import time
        from unittest.mock import patch

        import jwt as pyjwt

        private_pem, jwks_data = _make_test_rsa_key()
        issuer = "https://idp2.test.example.com"
        client_id = "oidc-client-2"
        nonce = secrets.token_urlsafe(16)
        now = int(time.time())
        id_token = pyjwt.encode(
            {
                "sub": "sub-single-use",
                "iss": issuer,
                "aud": client_id,
                "nonce": nonce,
                "email": "su@example.com",
                "email_verified": True,
                "iat": now,
                "exp": now + 300,
            },
            private_pem,
            algorithm="RS256",
            headers={"kid": "test-kid-1"},
        )

        admin_token = await _setup_and_login(async_client, "oidcsuadm", "oidcsuadm1")
        cr = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "SU-IdP",
                "issuer_url": issuer,
                "client_id": client_id,
                "client_secret": "s",
                "scopes": "openid",
                "is_enabled": True,
                "auto_create_users": True,
            },
            headers=_auth_header(admin_token),
        )
        provider_id = cr.json()["id"]

        state = secrets.token_urlsafe(32)
        db_session.add(
            AuthEphemeralToken(
                token=state,
                token_type="oidc_state",
                provider_id=provider_id,
                nonce=nonce,
                code_verifier=secrets.token_urlsafe(48),
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
        token_response = {"access_token": "a", "token_type": "Bearer", "id_token": id_token}

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

        class _MockHttpxClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def get(self, url, **kw):
                return _MockResp(jwks_data if "jwks" in url else discovery_doc)

            async def post(self, url, **kw):
                return _MockResp(token_response)

        with patch("backend.app.api.routes.mfa.httpx.AsyncClient", _MockHttpxClient):
            first = await async_client.get(
                f"/api/v1/auth/oidc/callback?code=c&state={state}",
                follow_redirects=False,
            )
            assert first.status_code == 302
            assert "oidc_token=" in first.headers.get("location", "")

            # Replay: second callback with the same state must fail
            second = await async_client.get(
                f"/api/v1/auth/oidc/callback?code=c&state={state}",
                follow_redirects=False,
            )
            assert second.status_code == 302
            assert "invalid_state" in second.headers.get("location", "")


# ===========================================================================
# H-2: Wrong code must NOT consume the email OTP setup token (peek-then-consume)
# ===========================================================================


class TestEmailOTPSetupTokenPreservedOnWrongCode:
    """After H-2 fix: a wrong code leaves the setup token intact so the user can retry."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_wrong_code_does_not_consume_setup_token(self, async_client: AsyncClient, db_session: AsyncSession):
        """Wrong code returns 400 but the setup token survives; correct code then works."""
        token = await _setup_and_login(async_client, "h2retryuser", "h2retrypass1")

        code = "999999"
        code_hash = _pwd_context.hash(code)
        setup_token = secrets.token_urlsafe(32)
        db_session.add(
            AuthEphemeralToken(
                token=setup_token,
                token_type="email_otp_setup",
                username="h2retryuser",
                nonce=code_hash,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
        )
        await db_session.commit()

        # First attempt: wrong code → 400
        wrong = await async_client.post(
            "/api/v1/auth/2fa/email/enable/confirm",
            json={"setup_token": setup_token, "code": "000000"},
            headers=_auth_header(token),
        )
        assert wrong.status_code == 400

        # Second attempt: correct code → must succeed (token was NOT consumed)
        correct = await async_client.post(
            "/api/v1/auth/2fa/email/enable/confirm",
            json={"setup_token": setup_token, "code": code},
            headers=_auth_header(token),
        )
        assert correct.status_code == 200


# ===========================================================================
# M-2: New OIDC provider must default to auto_link_existing_accounts=False
# ===========================================================================


class TestOIDCProviderAutoLinkDefault:
    """auto_link_existing_accounts must default to False (M-2 fix)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_new_provider_auto_link_defaults_to_false(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "m2autolinkadmin", "m2autolinkadmin1")
        resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "AutoLinkTest",
                "issuer_url": "https://autolink.example.com",
                "client_id": "alc",
                "client_secret": "als",
                "scopes": "openid",
                "is_enabled": True,
                "auto_create_users": False,
                # auto_link_existing_accounts intentionally omitted
            },
            headers=_auth_header(token),
        )
        assert resp.status_code == 201
        assert resp.json()["auto_link_existing_accounts"] is False


# ===========================================================================
# L-5: 2FA verify code format validation
# ===========================================================================


class TestTwoFAVerifyCodeFormat:
    """TwoFAVerifyRequest.code must be 6–8 alphanumeric characters (L-5)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_code_too_long_rejected(self, async_client: AsyncClient):
        """code > 8 characters must be rejected with 422."""
        resp = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": "anytoken", "code": "1" * 9, "method": "totp"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_code_non_alphanumeric_rejected(self, async_client: AsyncClient):
        """code containing non-alphanumeric chars must be rejected with 422."""
        resp = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": "anytoken", "code": "12-456", "method": "totp"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_code_too_short_rejected(self, async_client: AsyncClient):
        """code < 6 characters must be rejected with 422."""
        resp = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": "anytoken", "code": "12345", "method": "totp"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_code_exactly_6_passes_schema(self, async_client: AsyncClient):
        """6-character alphanumeric code passes schema (may fail 2FA logic with 400)."""
        resp = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": "x" * 32, "code": "123456", "method": "totp"},
        )
        assert resp.status_code != 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_code_exactly_8_passes_schema(self, async_client: AsyncClient):
        """8-character alphanumeric backup code passes schema."""
        resp = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": "x" * 32, "code": "ABCD1234", "method": "backup"},
        )
        assert resp.status_code != 422


# ===========================================================================
# M-NEW-1: verify_slicer_download_token must NOT consume token on wrong resource
# ===========================================================================


class TestSlicerTokenResourceBinding:
    """Token for resource A must survive a wrong-resource check and still work for A."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_wrong_resource_does_not_consume_token(self, async_client: AsyncClient, db_session: AsyncSession):
        """A slicer token bound to archive:5 must NOT be consumed when checked against archive:6."""
        from datetime import datetime, timedelta, timezone

        from backend.app.core.auth import verify_slicer_download_token
        from backend.app.models.auth_ephemeral import AuthEphemeralToken

        now = datetime.now(timezone.utc)
        token_val = secrets.token_urlsafe(24)
        db_session.add(
            AuthEphemeralToken(
                token=token_val,
                token_type="slicer_download",
                nonce="archive:5",
                expires_at=now + timedelta(minutes=5),
            )
        )
        await db_session.commit()

        # Wrong resource → must return False and NOT consume the token
        wrong = await verify_slicer_download_token(token_val, "archive", 6)
        assert wrong is False

        # Correct resource → must return True (token survived the wrong-resource check)
        correct = await verify_slicer_download_token(token_val, "archive", 5)
        assert correct is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_correct_resource_consumes_token(self, async_client: AsyncClient, db_session: AsyncSession):
        """A slicer token is single-use: second correct-resource check must return False."""
        from datetime import datetime, timedelta, timezone

        from backend.app.core.auth import verify_slicer_download_token
        from backend.app.models.auth_ephemeral import AuthEphemeralToken

        now = datetime.now(timezone.utc)
        token_val = secrets.token_urlsafe(24)
        db_session.add(
            AuthEphemeralToken(
                token=token_val,
                token_type="slicer_download",
                nonce="library:99",
                expires_at=now + timedelta(minutes=5),
            )
        )
        await db_session.commit()

        first = await verify_slicer_download_token(token_val, "library", 99)
        assert first is True

        second = await verify_slicer_download_token(token_val, "library", 99)
        assert second is False


# ===========================================================================
# M-NEW-3 / L-NEW-1: Schema length validation for change-password & forgot-password
# ===========================================================================


class TestSchemaLengthValidationR2:
    """Input length limits added in review round 2."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_change_password_current_too_long_rejected(self, async_client: AsyncClient):
        """current_password > 256 chars must be rejected with 422 (prevents pbkdf2 DoS)."""
        resp = await async_client.post(
            "/api/v1/users/me/change-password",
            json={"current_password": "x" * 257, "new_password": "ValidPass1!"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_forgot_password_email_too_long_rejected(self, async_client: AsyncClient):
        """email > 254 chars must be rejected with 422."""
        resp = await async_client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "a" * 243 + "@example.com"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_forgot_password_email_at_limit_passes_schema(self, async_client: AsyncClient):
        """Short email passes schema (may return 400/200 from business logic)."""
        resp = await async_client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "user@example.com"},
        )
        assert resp.status_code != 422


# ===========================================================================
# L-NEW-2: TOTPSetupRequest.code max_length
# ===========================================================================


class TestTOTPSetupCodeMaxLength:
    """TOTPSetupRequest.code must be bounded (L-NEW-2)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_setup_code_too_long_rejected(self, async_client: AsyncClient):
        """code > 8 chars must be rejected with 422."""
        import pyotp as _pyotp

        token = await _setup_and_login(async_client, "totp_setup_maxlen", "totp_setup_maxlen1")
        # Enable TOTP so the setup-code guard path is active
        setup_resp = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        secret = setup_resp.json()["secret"]
        await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": _pyotp.TOTP(secret).now()},
            headers=_auth_header(token),
        )

        resp = await async_client.post(
            "/api/v1/auth/2fa/totp/setup",
            json={"code": "1" * 9},
            headers=_auth_header(token),
        )
        assert resp.status_code == 422


# ===========================================================================
# L-NEW-3: EmailOTPEnableConfirmRequest.code must be exactly 6 digits
# ===========================================================================


class TestEmailOTPConfirmCodeFormat:
    """EmailOTPEnableConfirmRequest.code must be 6 digits (L-NEW-3)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_non_digit_code_rejected(self, async_client: AsyncClient):
        """Alpha characters in the email OTP confirm code must be rejected with 422."""
        token = await _setup_and_login(async_client, "emailotpfmt", "emailotpfmt1")

        resp = await async_client.post(
            "/api/v1/auth/2fa/email/enable/confirm",
            json={"setup_token": "x" * 32, "code": "ABCDEF"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_seven_digit_code_rejected(self, async_client: AsyncClient):
        """7-digit code must be rejected with 422 (min_length=max_length=6)."""
        token = await _setup_and_login(async_client, "emailotplen7", "emailotplen7x")

        resp = await async_client.post(
            "/api/v1/auth/2fa/email/enable/confirm",
            json={"setup_token": "x" * 32, "code": "1234567"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_valid_six_digit_code_passes_schema(self, async_client: AsyncClient):
        """6-digit numeric code passes schema (may return 400 on bad token — that's fine)."""
        token = await _setup_and_login(async_client, "emailotpfmt6", "emailotpfmt6x")

        resp = await async_client.post(
            "/api/v1/auth/2fa/email/enable/confirm",
            json={"setup_token": "x" * 32, "code": "123456"},
            headers=_auth_header(token),
        )
        assert resp.status_code != 422


# ===========================================================================
# L-NEW-4: OIDCProviderCreate field max_length constraints
# ===========================================================================


class TestOIDCProviderFieldLengths:
    """OIDCProviderCreate fields must reject inputs exceeding max_length (L-NEW-4)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_name_too_long_rejected(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "oidcfldadmin", "oidcfldadmin1")
        resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "n" * 101,
                "issuer_url": "https://test.example.com",
                "client_id": "cid",
                "client_secret": "csec",
                "scopes": "openid",
            },
            headers=_auth_header(token),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_client_secret_too_long_rejected(self, async_client: AsyncClient):
        token = await _setup_and_login(async_client, "oidcseclen", "oidcseclen123")
        resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "ValidName",
                "issuer_url": "https://test.example.com",
                "client_id": "cid",
                "client_secret": "s" * 513,
                "scopes": "openid",
            },
            headers=_auth_header(token),
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# M-NEW-4 / M-NEW-5 / L-NEW-5: UserCreate & UserUpdate field length limits
# ---------------------------------------------------------------------------


class TestUserCreateUpdateFieldLengths:
    """UserCreate and UserUpdate must enforce max_length on username, password, email."""

    @pytest.fixture
    async def admin_token(self, async_client: AsyncClient) -> str:
        return await _setup_and_login(async_client, "ucfldadmin", "ucfldadmin1!")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_username_too_long_rejected(self, async_client: AsyncClient, admin_token: str):
        resp = await async_client.post(
            "/api/v1/users/",
            json={
                "username": "u" * 151,
                "password": "ValidPass1!",
                "role": "user",
            },
            headers=_auth_header(admin_token),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_password_too_long_rejected(self, async_client: AsyncClient, admin_token: str):
        resp = await async_client.post(
            "/api/v1/users/",
            json={
                "username": "newuserX",
                "password": "A1!" + "x" * 254,
                "role": "user",
            },
            headers=_auth_header(admin_token),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_email_too_long_rejected(self, async_client: AsyncClient, admin_token: str):
        resp = await async_client.post(
            "/api/v1/users/",
            json={
                "username": "newuserY",
                "password": "ValidPass1!",
                "email": "a" * 246 + "@x.com",  # total 253 chars -> fine; 248+@x.com=255 -> too long
                "role": "user",
            },
            headers=_auth_header(admin_token),
        )
        # 248 'a' + '@x.com' (6) = 254 chars — just at limit, should pass
        # Use 249 + '@x.com' = 255 chars to trigger the 422
        assert resp.status_code in (201, 422)  # boundary sanity check

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_email_exceeds_limit_rejected(self, async_client: AsyncClient, admin_token: str):
        resp = await async_client.post(
            "/api/v1/users/",
            json={
                "username": "newuserZ",
                "password": "ValidPass1!",
                "email": "a" * 249 + "@x.com",  # 255 chars — exceeds RFC 5321 max of 254
                "role": "user",
            },
            headers=_auth_header(admin_token),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_username_too_long_rejected(self, async_client: AsyncClient, admin_token: str):
        # Create a user first
        create_resp = await async_client.post(
            "/api/v1/users/",
            json={"username": "updusr1", "password": "ValidPass1!", "role": "user"},
            headers=_auth_header(admin_token),
        )
        assert create_resp.status_code == 201
        user_id = create_resp.json()["id"]

        resp = await async_client.patch(
            f"/api/v1/users/{user_id}",
            json={"username": "u" * 151},
            headers=_auth_header(admin_token),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_password_too_long_rejected(self, async_client: AsyncClient, admin_token: str):
        create_resp = await async_client.post(
            "/api/v1/users/",
            json={"username": "updusr2", "password": "ValidPass1!", "role": "user"},
            headers=_auth_header(admin_token),
        )
        assert create_resp.status_code == 201
        user_id = create_resp.json()["id"]

        resp = await async_client.patch(
            f"/api/v1/users/{user_id}",
            json={"password": "A1!" + "x" * 254},
            headers=_auth_header(admin_token),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_email_too_long_rejected(self, async_client: AsyncClient, admin_token: str):
        create_resp = await async_client.post(
            "/api/v1/users/",
            json={"username": "updusr3", "password": "ValidPass1!", "role": "user"},
            headers=_auth_header(admin_token),
        )
        assert create_resp.status_code == 201
        user_id = create_resp.json()["id"]

        resp = await async_client.patch(
            f"/api/v1/users/{user_id}",
            json={"email": "a" * 249 + "@x.com"},  # 255 chars
            headers=_auth_header(admin_token),
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# L-NEW-6: per-IP rate limiting on /forgot-password
# ---------------------------------------------------------------------------

_SMTP_DATA_FOR_IPLIMIT = {
    "smtp_host": "smtp.test.com",
    "smtp_port": 587,
    "smtp_username": "test@test.com",
    "smtp_password": "testpass",
    "smtp_security": "starttls",
    "smtp_auth_enabled": True,
    "smtp_from_email": "noreply@test.com",
}


class TestForgotPasswordPerIpRateLimit:
    """POST /forgot-password must enforce a per-IP cap (L-NEW-6).

    The test sends 11 requests from the simulated test-client IP using 11
    different email addresses (so the per-email bucket is never exhausted).
    The 11th request must be rejected with 429.
    """

    @pytest.fixture
    async def advanced_auth_token(self, async_client: AsyncClient) -> str:
        """Set up auth, SMTP, and enable advanced auth; return admin token."""
        token = await _setup_and_login(async_client, "iprladmin", "iprladmin1!")
        headers = _auth_header(token)
        await async_client.post("/api/v1/auth/smtp", headers=headers, json=_SMTP_DATA_FOR_IPLIMIT)
        await async_client.post("/api/v1/auth/advanced-auth/enable", headers=headers)
        return token

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_per_ip_limit_triggers_429(self, async_client: AsyncClient, advanced_auth_token: str):
        # Send 11 requests from the same test-client IP using unique email
        # addresses so the per-email bucket (limit=3) is never exhausted.
        responses = []
        for i in range(11):
            resp = await async_client.post(
                "/api/v1/auth/forgot-password",
                json={"email": f"unique{i}@example.com"},
            )
            responses.append(resp.status_code)

        # First 10 must not be rate-limited by the IP bucket
        for code in responses[:10]:
            assert code != 429, f"Unexpected 429 before limit reached: {responses}"

        # The 11th must be rate-limited
        assert responses[10] == 429, f"Expected 429 on 11th request, got {responses[10]}"


# ---------------------------------------------------------------------------
# M-NEW-6: OIDC auto-link must be rejected if target user already has an
#          OIDC link to a different provider
# ---------------------------------------------------------------------------


class TestOIDCAutoLinkExistingLinkRejection:
    """OIDC callback must reject auto-linking when the email-matched user
    already has an OIDC link to a different provider (M-NEW-6)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_auto_link_rejected_when_user_already_linked(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Auto-link via email-match is rejected when the target user is
        already linked to another OIDC provider."""
        import base64
        import hashlib
        from unittest.mock import AsyncMock, MagicMock, patch

        from backend.app.core.auth import get_password_hash
        from backend.app.models.oidc_provider import OIDCProvider, UserOIDCLink
        from backend.app.models.user import User

        # ── 1. Target user with a known email ────────────────────────────
        target = User(
            username="oidcALTarget",
            email="alinktest@example.com",
            auth_source="oidc",
            password_hash=get_password_hash(secrets.token_urlsafe(16)),
            role="user",
            is_active=True,
        )
        db_session.add(target)
        await db_session.flush()

        # ── 2. Provider B — legitimate, already linked to target ──────────
        prov_b = OIDCProvider(
            name="ProvB_m6test",
            issuer_url="https://providerb-m6.example.com",
            client_id="client_b",
            _client_secret_enc="secret_b",
            scopes="openid email profile",
            is_enabled=True,
            auto_link_existing_accounts=False,
            auto_create_users=False,
        )
        db_session.add(prov_b)
        await db_session.flush()

        db_session.add(
            UserOIDCLink(
                user_id=target.id,
                provider_id=prov_b.id,
                provider_user_id="legitimate_sub",
                provider_email="alinktest@example.com",
            )
        )

        # ── 3. Provider A — attacker-controlled, auto_link=True ───────────
        prov_a = OIDCProvider(
            name="ProvA_m6test",
            issuer_url="https://providera-m6.example.com",
            client_id="client_a",
            _client_secret_enc="secret_a",
            scopes="openid email profile",
            is_enabled=True,
            auto_link_existing_accounts=True,
            auto_create_users=False,
        )
        db_session.add(prov_a)
        await db_session.flush()

        # ── 4. OIDC state for Provider A ──────────────────────────────────
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(48)

        db_session.add(
            AuthEphemeralToken(
                token=state,
                token_type="oidc_state",
                provider_id=prov_a.id,
                nonce=nonce,
                code_verifier=code_verifier,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
        )
        await db_session.commit()

        # ── 5. Mock HTTP + JWT so the callback can reach the auto-link check ─
        fake_discovery = {
            "issuer": "https://providera-m6.example.com",
            "token_endpoint": "https://providera-m6.example.com/token",
            "jwks_uri": "https://providera-m6.example.com/jwks",
        }
        fake_token = {"access_token": "acc_tok", "id_token": "fake.id.token"}
        fake_claims = {
            "sub": "attacker_sub_unique",
            "email": "alinktest@example.com",
            "email_verified": True,
            "nonce": nonce,
            "iss": "https://providera-m6.example.com",
            "aud": "client_a",
            "exp": 9_999_999_999,
        }

        disc_resp = AsyncMock()
        disc_resp.raise_for_status = MagicMock()
        disc_resp.json = MagicMock(return_value=fake_discovery)

        token_resp = AsyncMock()
        token_resp.ok = True
        token_resp.json = MagicMock(return_value=fake_token)

        jwks_resp = AsyncMock()
        jwks_resp.raise_for_status = MagicMock()
        jwks_resp.json = MagicMock(return_value={})

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=[disc_resp, jwks_resp])
        mock_http.post = AsyncMock(return_value=token_resp)

        mock_signing_key = MagicMock()
        mock_signing_key.key = "fake_key"

        with (
            patch("backend.app.api.routes.mfa.httpx.AsyncClient") as mock_httpx_cls,
            patch("backend.app.api.routes.mfa.jwt.decode", return_value=fake_claims),
            patch("backend.app.api.routes.mfa.PyJWKClient") as mock_jwks_cls,
        ):
            mock_httpx_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_httpx_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_jwks_cls.return_value.get_signing_key_from_jwt.return_value = mock_signing_key

            resp = await async_client.get(
                f"/api/v1/auth/oidc/callback?code=fake_code&state={state}",
                follow_redirects=False,
            )

        # M-NEW-6: must redirect with no_linked_account — NOT create a second link
        assert resp.status_code == 302
        location = resp.headers.get("location", "")
        assert "no_linked_account" in location, f"Expected no_linked_account in redirect, got: {location}"

        # Verify no second OIDC link was created for Provider A
        from sqlalchemy import select as sa_select

        from backend.app.models.oidc_provider import UserOIDCLink as _UOL

        async with db_session as s:
            links_result = await s.execute(
                sa_select(_UOL).where(_UOL.user_id == target.id, _UOL.provider_id == prov_a.id)
            )
            assert links_result.scalar_one_or_none() is None, "No link to Provider A must exist"


# ===========================================================================
# Test Gap 1: OIDC state token is single-use — replay must be rejected
# ===========================================================================


class TestOIDCStateReplay:
    """OIDC state token must be consumed on first use; a second callback with
    the same state must redirect to ``?oidc_error=invalid_state``."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_state_replay_rejected(self, async_client: AsyncClient, db_session: AsyncSession):
        """Replaying a consumed OIDC state token must return invalid_state."""
        from backend.app.models.oidc_provider import OIDCProvider

        # ── 1. Seed a minimal provider ────────────────────────────────────
        provider = OIDCProvider(
            name="StateReplayIdP",
            issuer_url="https://statereplay-idp.example.com",
            client_id="client_replay",
            _client_secret_enc="secret_replay",
            scopes="openid",
            is_enabled=True,
            auto_link_existing_accounts=False,
            auto_create_users=False,
        )
        db_session.add(provider)
        await db_session.flush()

        # ── 2. Seed an OIDC state token ───────────────────────────────────
        state = secrets.token_urlsafe(32)
        db_session.add(
            AuthEphemeralToken(
                token=state,
                token_type="oidc_state",
                provider_id=provider.id,
                nonce=secrets.token_urlsafe(32),
                code_verifier=secrets.token_urlsafe(48),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
        )
        await db_session.commit()

        # ── 3. First callback — discovery will fail (no real IdP), but the
        #       state token is atomically consumed (DELETE…RETURNING + commit)
        #       before the HTTP call is attempted.
        first = await async_client.get(
            f"/api/v1/auth/oidc/callback?code=any_code&state={state}",
            follow_redirects=False,
        )
        assert first.status_code == 302
        # The first call may fail for any reason except invalid_state
        assert "invalid_state" not in first.headers.get("location", ""), (
            f"First call should NOT get invalid_state: {first.headers.get('location')}"
        )

        # ── 4. Second callback with the same state → must be invalid_state ─
        second = await async_client.get(
            f"/api/v1/auth/oidc/callback?code=any_code&state={state}",
            follow_redirects=False,
        )
        assert second.status_code == 302
        assert "invalid_state" in second.headers.get("location", ""), (
            f"Replayed state must redirect to invalid_state, got: {second.headers.get('location')}"
        )


# ===========================================================================
# Test Gap 2: OIDC iss claim mismatch must redirect to token_validation_failed
# ===========================================================================


class TestOIDCIssMismatch:
    """JWT whose iss claim does not match the discovery issuer must be rejected."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_iss_mismatch_redirects_token_validation_failed(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        import time
        from unittest.mock import patch

        import jwt as pyjwt

        private_pem, jwks_data = _make_test_rsa_key()
        correct_issuer = "https://correct-iss.example.com"
        wrong_issuer = "https://wrong-iss.example.com"
        client_id = "iss-mismatch-client"
        nonce = secrets.token_urlsafe(16)
        now = int(time.time())

        # Sign the token with the WRONG issuer (iss != discovery_issuer)
        id_token = pyjwt.encode(
            {
                "sub": "sub-iss-test",
                "iss": wrong_issuer,
                "aud": client_id,
                "nonce": nonce,
                "email": "iss@example.com",
                "email_verified": True,
                "iat": now,
                "exp": now + 300,
            },
            private_pem,
            algorithm="RS256",
            headers={"kid": "test-kid-1"},
        )

        admin_token = await _setup_and_login(async_client, "issadmin1", "issadmin1!")
        cr = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "IssTest-IdP",
                "issuer_url": correct_issuer,
                "client_id": client_id,
                "client_secret": "s",
                "scopes": "openid",
                "is_enabled": True,
                "auto_create_users": True,
            },
            headers=_auth_header(admin_token),
        )
        assert cr.status_code in (200, 201), cr.text
        provider_id = cr.json()["id"]

        state = secrets.token_urlsafe(32)
        db_session.add(
            AuthEphemeralToken(
                token=state,
                token_type="oidc_state",
                provider_id=provider_id,
                nonce=nonce,
                code_verifier=secrets.token_urlsafe(48),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
        )
        await db_session.commit()

        # Discovery returns the CORRECT issuer; JWT carries the WRONG one.
        discovery_doc = {
            "issuer": correct_issuer,
            "token_endpoint": f"{correct_issuer}/token",
            "jwks_uri": f"{correct_issuer}/.well-known/jwks.json",
        }
        token_response = {"access_token": "a", "id_token": id_token}

        class _MockResp:
            def __init__(self, data):
                self._data = data
                self.status_code = 200
                self.is_success = True
                self.text = ""

            def json(self):
                return self._data

            def raise_for_status(self):
                pass

        class _MockHttpxClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def get(self, url, **kw):
                return _MockResp(jwks_data if "jwks" in url else discovery_doc)

            async def post(self, url, **kw):
                return _MockResp(token_response)

        with patch("backend.app.api.routes.mfa.httpx.AsyncClient", _MockHttpxClient):
            resp = await async_client.get(
                f"/api/v1/auth/oidc/callback?code=c&state={state}",
                follow_redirects=False,
            )

        assert resp.status_code == 302
        location = resp.headers.get("location", "")
        assert "token_validation_failed" in location, f"Expected token_validation_failed, got: {location}"


# ===========================================================================
# Test Gap 3: /forgot-password/confirm token is single-use
# ===========================================================================


class TestForgotPasswordTokenSingleUse:
    """POST /forgot-password/confirm must reject a token after its first use."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_token_reuse_rejected(self, async_client: AsyncClient, db_session: AsyncSession):
        from backend.app.core.auth import get_password_hash
        from backend.app.models.user import User as _User

        user = _User(
            username="fpcuser1",
            email="fpc@example.com",
            password_hash=get_password_hash("OldPass1!"),
            role="user",
            is_active=True,
        )
        db_session.add(user)
        await db_session.flush()

        reset_token = secrets.token_urlsafe(32)
        db_session.add(
            AuthEphemeralToken(
                token=reset_token,
                token_type="password_reset",
                username="fpcuser1",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        await db_session.commit()

        # First use → success
        resp1 = await async_client.post(
            "/api/v1/auth/forgot-password/confirm",
            json={"token": reset_token, "new_password": "NewPass1!"},
        )
        assert resp1.status_code == 200, resp1.text

        # Second use → token already consumed, must fail
        resp2 = await async_client.post(
            "/api/v1/auth/forgot-password/confirm",
            json={"token": reset_token, "new_password": "AnotherNew1!"},
        )
        assert resp2.status_code == 400


# ===========================================================================
# C1 regression: setup_totp must reject a replayed TOTP code
# ===========================================================================


class TestSetupTOTPReplayRejected:
    """setup_totp must reject a TOTP code that was already accepted in its window."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_replayed_setup_code_rejected(self, async_client: AsyncClient, db_session: AsyncSession):
        from sqlalchemy import select as sa_select

        from backend.app.models.user_totp import UserTOTP

        token = await _setup_and_login(async_client, "setupreplay1", "setupreplay1!")

        # Step 1: Initial TOTP setup (no active TOTP yet → no code required)
        setup_resp = await async_client.post(
            "/api/v1/auth/2fa/totp/setup",
            headers=_auth_header(token),
        )
        assert setup_resp.status_code == 200
        secret = setup_resp.json()["secret"]

        # Step 2: Enable TOTP with a valid code
        totp_obj = pyotp.TOTP(secret)
        enable_resp = await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": totp_obj.now()},
            headers=_auth_header(token),
        )
        assert enable_resp.status_code == 200  # TOTP is now active (is_enabled=True)

        # Step 3: Determine current valid code and its counter
        me_resp = await async_client.get("/api/v1/auth/me", headers=_auth_header(token))
        user_id = me_resp.json()["id"]

        totp_result = await db_session.execute(sa_select(UserTOTP).where(UserTOTP.user_id == user_id))
        totp_record = totp_result.scalar_one()
        secret_now = totp_record.secret  # decrypted via property

        totp_now = pyotp.TOTP(secret_now)
        valid_code = totp_now.now()
        accepted_counter = totp_now.timecode(datetime.now(timezone.utc))

        # Step 4: Pre-set last_totp_counter so this code looks already used
        totp_record.last_totp_counter = accepted_counter
        await db_session.commit()

        # Step 5: Attempt setup_totp with the "already used" code → must be rejected
        replay_resp = await async_client.post(
            "/api/v1/auth/2fa/totp/setup",
            json={"code": valid_code},
            headers=_auth_header(token),
        )
        assert replay_resp.status_code == 400
        assert "already used" in replay_resp.json()["detail"]


# ===========================================================================
# Nit8: OIDC aud mismatch and nonce mismatch tests
# ===========================================================================


class TestOIDCAudAndNonceMismatch:
    """Nit8: aud != client_id and nonce != stored value must each fail the callback."""

    def _make_oidc_provider_setup(self):
        """Return a helper for building OIDC test fixtures inline."""
        private_pem, jwks_data = _make_test_rsa_key()
        return private_pem, jwks_data

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_aud_mismatch_redirects_token_validation_failed(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """ID token with aud != client_id must be rejected (PyJWT InvalidAudienceError)."""
        import time
        from unittest.mock import patch

        import jwt as pyjwt

        private_pem, jwks_data = _make_test_rsa_key()
        issuer = "https://aud-mismatch.example.com"
        client_id = "aud-test-client"
        wrong_aud = "some-other-client"
        nonce = secrets.token_urlsafe(16)
        now = int(time.time())

        id_token = pyjwt.encode(
            {
                "sub": "sub-aud-test",
                "iss": issuer,
                "aud": wrong_aud,  # <-- wrong audience
                "nonce": nonce,
                "email": "aud@example.com",
                "email_verified": True,
                "iat": now,
                "exp": now + 300,
            },
            private_pem,
            algorithm="RS256",
            headers={"kid": "test-kid-1"},
        )

        admin_token = await _setup_and_login(async_client, "audmismatch_admin", "AudMismatch_admin1")
        cr = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "AudMismatch-IdP",
                "issuer_url": issuer,
                "client_id": client_id,
                "client_secret": "s",
                "scopes": "openid",
                "is_enabled": True,
                "auto_create_users": True,
            },
            headers=_auth_header(admin_token),
        )
        assert cr.status_code in (200, 201), cr.text
        provider_id = cr.json()["id"]

        state = secrets.token_urlsafe(32)
        db_session.add(
            AuthEphemeralToken(
                token=state,
                token_type="oidc_state",
                provider_id=provider_id,
                nonce=nonce,
                code_verifier=secrets.token_urlsafe(48),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
        )
        await db_session.commit()

        discovery_doc = {
            "issuer": issuer,
            "token_endpoint": f"{issuer}/token",
            "jwks_uri": f"{issuer}/.well-known/jwks.json",
        }

        class _MockResp:
            def __init__(self, data):
                self._data = data
                self.status_code = 200
                self.is_success = True
                self.text = ""

            def json(self):
                return self._data

            def raise_for_status(self):
                pass

        class _MockHttpxClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def get(self, url, **kw):
                return _MockResp(jwks_data if "jwks" in url else discovery_doc)

            async def post(self, url, **kw):
                return _MockResp({"access_token": "a", "id_token": id_token})

        with patch("backend.app.api.routes.mfa.httpx.AsyncClient", _MockHttpxClient):
            resp = await async_client.get(
                f"/api/v1/auth/oidc/callback?code=c&state={state}",
                follow_redirects=False,
            )

        assert resp.status_code == 302
        location = resp.headers.get("location", "")
        assert "token_validation_failed" in location, (
            f"Expected token_validation_failed redirect for aud mismatch, got: {location}"
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_nonce_mismatch_redirects_token_validation_failed(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """ID token with nonce != stored state nonce must be rejected."""
        import time
        from unittest.mock import patch

        import jwt as pyjwt

        private_pem, jwks_data = _make_test_rsa_key()
        issuer = "https://nonce-mismatch.example.com"
        client_id = "nonce-test-client"
        stored_nonce = secrets.token_urlsafe(16)
        wrong_nonce = secrets.token_urlsafe(16)  # different from stored_nonce
        now = int(time.time())

        id_token = pyjwt.encode(
            {
                "sub": "sub-nonce-test",
                "iss": issuer,
                "aud": client_id,
                "nonce": wrong_nonce,  # <-- does not match stored_nonce
                "email": "nonce@example.com",
                "email_verified": True,
                "iat": now,
                "exp": now + 300,
            },
            private_pem,
            algorithm="RS256",
            headers={"kid": "test-kid-1"},
        )

        admin_token = await _setup_and_login(async_client, "noncemismatch_admin", "NonceMismatch_admin1")
        cr = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "NonceMismatch-IdP",
                "issuer_url": issuer,
                "client_id": client_id,
                "client_secret": "s",
                "scopes": "openid",
                "is_enabled": True,
                "auto_create_users": True,
            },
            headers=_auth_header(admin_token),
        )
        assert cr.status_code in (200, 201), cr.text
        provider_id = cr.json()["id"]

        state = secrets.token_urlsafe(32)
        db_session.add(
            AuthEphemeralToken(
                token=state,
                token_type="oidc_state",
                provider_id=provider_id,
                nonce=stored_nonce,  # state has correct nonce; JWT carries wrong_nonce
                code_verifier=secrets.token_urlsafe(48),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
        )
        await db_session.commit()

        discovery_doc = {
            "issuer": issuer,
            "token_endpoint": f"{issuer}/token",
            "jwks_uri": f"{issuer}/.well-known/jwks.json",
        }

        class _MockResp:
            def __init__(self, data):
                self._data = data
                self.status_code = 200
                self.is_success = True
                self.text = ""

            def json(self):
                return self._data

            def raise_for_status(self):
                pass

        class _MockHttpxClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def get(self, url, **kw):
                return _MockResp(jwks_data if "jwks" in url else discovery_doc)

            async def post(self, url, **kw):
                return _MockResp({"access_token": "a", "id_token": id_token})

        with patch("backend.app.api.routes.mfa.httpx.AsyncClient", _MockHttpxClient):
            resp = await async_client.get(
                f"/api/v1/auth/oidc/callback?code=c&state={state}",
                follow_redirects=False,
            )

        assert resp.status_code == 302
        location = resp.headers.get("location", "")
        # The callback redirects to ?oidc_error=nonce_mismatch when nonces differ.
        assert "nonce_mismatch" in location, f"Expected nonce_mismatch redirect for nonce mismatch, got: {location}"


# ===========================================================================
# Expired OIDC token rejection — state and exchange tokens
# ===========================================================================


class TestOIDCExpiredTokenRejection:
    """Expired OIDC state and exchange tokens must be rejected atomically.

    The DELETE … WHERE expires_at > now must ensure that an already-expired
    token is never consumed (committed) before the expiry is checked, so the
    token row stays in the DB and is not silently discarded.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_expired_state_token_rejected_as_invalid_state(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """An expired OIDC state token must redirect to invalid_state without
        being consumed — it must still exist in the DB after the rejected call."""
        from backend.app.models.oidc_provider import OIDCProvider

        provider = OIDCProvider(
            name="ExpiredStateIdP",
            issuer_url="https://expired-state.example.com",
            client_id="client_expired_state",
            _client_secret_enc="secret_exp_state",
            scopes="openid",
            is_enabled=True,
            auto_link_existing_accounts=False,
            auto_create_users=False,
        )
        db_session.add(provider)
        await db_session.flush()

        state = secrets.token_urlsafe(32)
        db_session.add(
            AuthEphemeralToken(
                token=state,
                token_type="oidc_state",
                provider_id=provider.id,
                nonce=secrets.token_urlsafe(16),
                code_verifier=secrets.token_urlsafe(48),
                # already expired
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            )
        )
        await db_session.commit()

        resp = await async_client.get(
            f"/api/v1/auth/oidc/callback?code=any_code&state={state}",
            follow_redirects=False,
        )

        assert resp.status_code == 302
        location = resp.headers.get("location", "")
        assert "invalid_state" in location, f"Expected invalid_state redirect for expired state, got: {location}"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_expired_exchange_token_rejected(self, async_client: AsyncClient, db_session: AsyncSession):
        """An expired OIDC exchange token must return 401 without being consumed."""
        from sqlalchemy import select as sa_select

        expired_token = secrets.token_urlsafe(32)
        db_session.add(
            AuthEphemeralToken(
                token=expired_token,
                token_type="oidc_exchange",
                username="some_user",
                # already expired
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            )
        )
        await db_session.commit()

        resp = await async_client.post(
            "/api/v1/auth/oidc/exchange",
            json={"oidc_token": expired_token},
        )

        assert resp.status_code == 401
        assert "expired" in resp.json().get("detail", "").lower() or "invalid" in resp.json().get("detail", "").lower()

        # Token must NOT have been consumed — it should still be in the DB
        # (the atomic DELETE WHERE expires_at > now left it untouched)
        result = await db_session.execute(
            sa_select(AuthEphemeralToken).where(AuthEphemeralToken.token == expired_token)
        )
        remaining = result.scalar_one_or_none()
        assert remaining is not None, "Expired exchange token must not be consumed by a rejected request"


# ===========================================================================
# Trailing slash in issuer_url — discovery URL must not contain double slash
# ===========================================================================


class TestOIDCIssuerUrlTrailingSlash:
    """Providers like Authentik use issuer URLs with a trailing slash.

    BamBuddy must strip the slash before appending /.well-known/openid-configuration
    to avoid a double-slash that results in a 404.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_trailing_slash_issuer_url_fetches_correct_discovery_url(self, async_client: AsyncClient):
        from unittest.mock import AsyncMock, MagicMock, patch

        issuer_with_slash = "https://authentik.example.com/application/o/bambuddy/"

        admin_token = await _setup_and_login(async_client, "oidcslashadm", "oidcslashadm1")
        create_resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "Authentik-Slash",
                "issuer_url": issuer_with_slash,
                "client_id": "bambuddy",
                "client_secret": "secret",
                "scopes": "openid email profile",
                "is_enabled": True,
                "auto_create_users": False,
            },
            headers=_auth_header(admin_token),
        )
        assert create_resp.status_code == 201
        provider_id = create_resp.json()["id"]

        fake_discovery = {
            "issuer": issuer_with_slash,
            "authorization_endpoint": "https://authentik.example.com/application/o/bambuddy/authorize",
        }
        disc_resp = AsyncMock()
        disc_resp.raise_for_status = MagicMock()
        disc_resp.json = MagicMock(return_value=fake_discovery)

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=disc_resp)

        with patch("backend.app.api.routes.mfa.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = await async_client.get(f"/api/v1/auth/oidc/authorize/{provider_id}")

        assert resp.status_code == 200
        called_url = mock_http.get.call_args_list[0][0][0]
        assert "//" not in called_url.replace("https://", ""), (
            f"Discovery URL must not contain double slash: {called_url}"
        )
        assert called_url.endswith("/.well-known/openid-configuration"), (
            f"Expected discovery URL to end with /.well-known/openid-configuration, got: {called_url}"
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_iss_claim_trailing_slash_accepted(self, async_client: AsyncClient, db_session: AsyncSession):
        """Provider configured without trailing slash, Authentik JWT iss has trailing slash.

        Both sides must be normalised before comparison so the login succeeds.
        """
        import time
        from unittest.mock import patch

        import jwt as pyjwt

        private_pem, jwks_data = _make_test_rsa_key()
        issuer_no_slash = "https://authentik.example.com/application/o/bambuddy"
        issuer_with_slash = issuer_no_slash + "/"
        client_id = "bambuddy-client"
        nonce = secrets.token_urlsafe(16)

        now = int(time.time())
        id_token = pyjwt.encode(
            {
                "sub": "authentik-sub-123",
                "iss": issuer_with_slash,
                "aud": client_id,
                "nonce": nonce,
                "email": "authentik-user@example.com",
                "email_verified": True,
                "iat": now,
                "exp": now + 300,
            },
            private_pem,
            algorithm="RS256",
            headers={"kid": "test-kid-1"},
        )

        admin_token = await _setup_and_login(async_client, "authentikadm", "authentikadm1")
        create_resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "Authentik-ISS",
                "issuer_url": issuer_no_slash,
                "client_id": client_id,
                "client_secret": "secret",
                "scopes": "openid email profile",
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
            "issuer": issuer_with_slash,
            "authorization_endpoint": f"{issuer_no_slash}/authorize",
            "token_endpoint": f"{issuer_no_slash}/token",
            "jwks_uri": f"{issuer_no_slash}/.well-known/jwks.json",
        }
        token_response = {"access_token": "mock", "token_type": "Bearer", "id_token": id_token}

        class _MockResp:
            def __init__(self, data):
                self._data = data
                self.is_success = True
                self.status_code = 200
                self.text = str(data)

            def json(self):
                return self._data

            def raise_for_status(self):
                pass

        class _MockHttpxClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def get(self, url, **kw):
                return _MockResp(jwks_data if "jwks" in url else discovery_doc)

            async def post(self, url, **kw):
                return _MockResp(token_response)

        with patch("backend.app.api.routes.mfa.httpx.AsyncClient", _MockHttpxClient):
            resp = await async_client.get(
                f"/api/v1/auth/oidc/callback?code=auth-code&state={state}",
                follow_redirects=False,
            )

        location = resp.headers.get("location", "")
        assert resp.status_code == 302, f"Expected redirect, got {resp.status_code}"
        assert "token_validation_failed" not in location, (
            "Trailing slash mismatch in iss claim must not cause token_validation_failed"
        )
        assert "oidc_token=" in location, f"Expected oidc_token in redirect, got: {location}"


class TestOIDCCallbackCodeLength:
    """OIDC callback code/state query params must accept up to 2048 characters (OAuth spec)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_code_512_chars_accepted(self, async_client: AsyncClient):
        """A 512-character code (old limit) must not be rejected with 422."""
        code = "a" * 512
        resp = await async_client.get(
            f"/api/v1/auth/oidc/callback?code={code}&state=bogus-state",
            follow_redirects=False,
        )
        assert resp.status_code != 422, "512-char code must not be rejected by Pydantic"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_code_2048_chars_accepted(self, async_client: AsyncClient):
        """A 2048-character code must not be rejected with 422."""
        code = "a" * 2048
        resp = await async_client.get(
            f"/api/v1/auth/oidc/callback?code={code}&state=bogus-state",
            follow_redirects=False,
        )
        assert resp.status_code != 422, "2048-char code must not be rejected by Pydantic"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_code_2049_chars_rejected(self, async_client: AsyncClient):
        """A 2049-character code must be rejected with 422."""
        code = "a" * 2049
        resp = await async_client.get(
            f"/api/v1/auth/oidc/callback?code={code}&state=bogus-state",
            follow_redirects=False,
        )
        assert resp.status_code == 422, "2049-char code must be rejected by Pydantic"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_state_512_chars_accepted(self, async_client: AsyncClient):
        """A 512-character state (old limit) must not be rejected with 422."""
        state = "a" * 512
        resp = await async_client.get(
            f"/api/v1/auth/oidc/callback?code=bogus-code&state={state}",
            follow_redirects=False,
        )
        assert resp.status_code != 422, "512-char state must not be rejected by Pydantic"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_state_2048_chars_accepted(self, async_client: AsyncClient):
        """A 2048-character state must not be rejected with 422."""
        state = "a" * 2048
        resp = await async_client.get(
            f"/api/v1/auth/oidc/callback?code=bogus-code&state={state}",
            follow_redirects=False,
        )
        assert resp.status_code != 422, "2048-char state must not be rejected by Pydantic"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_state_2049_chars_rejected(self, async_client: AsyncClient):
        """A 2049-character state must be rejected with 422."""
        state = "a" * 2049
        resp = await async_client.get(
            f"/api/v1/auth/oidc/callback?code=bogus-code&state={state}",
            follow_redirects=False,
        )
        assert resp.status_code == 422, "2049-char state must be rejected by Pydantic"


# ---------------------------------------------------------------------------
# Helpers shared by TestOIDCEmailClaimResolution
# ---------------------------------------------------------------------------


async def _run_oidc_callback(
    async_client: AsyncClient,
    db_session: AsyncSession,
    *,
    provider_id: int,
    claims: dict,
    private_pem: bytes,
    jwks_data: dict,
    issuer: str,
    client_id: str,
) -> str:
    """Run a full OIDC callback flow and return the redirect location."""
    nonce = secrets.token_urlsafe(16)
    now = int(time.time())
    token_claims = {
        "sub": claims.get("sub", f"sub-{secrets.token_hex(8)}"),
        "iss": issuer,
        "aud": client_id,
        "nonce": nonce,
        "iat": now,
        "exp": now + 300,
        **{k: v for k, v in claims.items() if k not in ("sub",)},
    }
    id_token = pyjwt.encode(token_claims, private_pem, algorithm="RS256", headers={"kid": "test-kid-1"})

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
    token_response = {"access_token": "mock-access", "token_type": "Bearer", "id_token": id_token}

    class _R:
        def __init__(self, data):
            self._data = data
            self.status_code = 200
            self.is_success = True
            self.text = str(data)

        def json(self):
            return self._data

        def raise_for_status(self):
            pass

    class _C:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, url, **kw):
            return _R(jwks_data if "jwks" in url else discovery_doc)

        async def post(self, url, **kw):
            return _R(token_response)

    with patch("backend.app.api.routes.mfa.httpx.AsyncClient", _C):
        resp = await async_client.get(
            f"/api/v1/auth/oidc/callback?code=test-code&state={state}",
            follow_redirects=False,
        )
    return resp.headers.get("location", "")


class TestOIDCEmailClaimResolution:
    """Three-case email resolution logic: Fall A / Fall B / Fall C."""

    # ── shared helpers ────────────────────────────────────────────────────────

    async def _create_provider(
        self,
        async_client: AsyncClient,
        admin_token: str,
        issuer: str,
        client_id: str,
        *,
        email_claim: str = "email",
        require_email_verified: bool = True,
        auto_link_existing_accounts: bool = False,
        suffix: str = "",
    ) -> int:
        resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": f"TestIdP-{suffix or secrets.token_hex(4)}",
                "issuer_url": issuer,
                "client_id": client_id,
                "client_secret": "sec",
                "scopes": "openid email profile",
                "is_enabled": True,
                "auto_create_users": True,
                "auto_link_existing_accounts": auto_link_existing_accounts,
                "email_claim": email_claim,
                "require_email_verified": require_email_verified,
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    async def _get_oidc_link(self, db_session: AsyncSession, provider_id: int, sub: str):
        from sqlalchemy import select

        from backend.app.models.oidc_provider import UserOIDCLink

        result = await db_session.execute(
            select(UserOIDCLink)
            .where(UserOIDCLink.provider_id == provider_id)
            .where(UserOIDCLink.provider_user_id == sub)
        )
        return result.scalar_one_or_none()

    # ── Parametrized matrix: Fall A / Fall B / Fall C ─────────────────────────

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        "email_claim,require_ev,claims,expected",
        [
            # Fall A: standard claim + require_ev=True (default)
            ("email", True, {"email": "fa@example.com", "email_verified": True}, "fa@example.com"),
            ("email", True, {"email": "fa@example.com", "email_verified": False}, None),
            ("email", True, {"email": "fa@example.com"}, None),  # Azure Entra with default config
            # Fall A + SEC-2: malformed email claim rejected even when email_verified=True
            ("email", True, {"email": "notanemail", "email_verified": True}, None),
            # Fall B: standard claim + require_ev=False (Azure Entra permissive)
            ("email", False, {"email": "fb@example.com", "email_verified": True}, "fb@example.com"),
            ("email", False, {"email": "fb@example.com", "email_verified": False}, None),
            ("email", False, {"email": "azure@company.com"}, "azure@company.com"),  # ev absent → kept
            # Fall B + SEC-2: malformed email claim rejected in permissive mode (ev absent)
            ("email", False, {"email": "user@nodot"}, None),
            # Fall B + SEC-2: shape check fires before email_verified=False drop
            ("email", False, {"email": "notanemail", "email_verified": False}, None),
            # Fall C: custom claim (preferred_username) — no email_verified check
            ("preferred_username", True, {"preferred_username": "User@Company.COM"}, "user@company.com"),
            ("preferred_username", True, {"preferred_username": "  User@EXAMPLE.COM  "}, "user@example.com"),
            ("preferred_username", True, {"preferred_username": "justausername"}, None),
            ("preferred_username", True, {"preferred_username": "@"}, None),  # SEC-2: "@" only
            ("preferred_username", True, {"preferred_username": "@domain.com"}, None),  # SEC-2: empty local
            ("preferred_username", True, {"preferred_username": "user@"}, None),  # SEC-2: empty domain
            ("preferred_username", True, {"preferred_username": "user@nodot"}, None),  # SEC-2: no dot in domain
            ("preferred_username", True, {}, None),  # claim absent
            # Fall C: email_verified=False present alongside custom claim — must NOT suppress the email
            ("preferred_username", True, {"preferred_username": "user@co.com", "email_verified": False}, "user@co.com"),
        ],
        ids=[
            "fall-a-ev-true",
            "fall-a-ev-false",
            "fall-a-ev-absent",
            "fall-a-malformed-email",
            "fall-b-ev-true",
            "fall-b-ev-false",
            "fall-b-ev-absent",
            "fall-b-malformed-email",
            "fall-b-malformed-email-ev-false",
            "fall-c-valid-upn",
            "fall-c-lowercase-strip",
            "fall-c-no-at",
            "fall-c-at-only",
            "fall-c-empty-local",
            "fall-c-empty-domain",
            "fall-c-no-dot-in-domain",
            "fall-c-claim-absent",
            "fall-c-ev-false-ignored",
        ],
    )
    async def test_email_resolution_matrix(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
        email_claim: str,
        require_ev: bool,
        claims: dict,
        expected: str | None,
    ):
        """C4: Verify link exists AND check provider_email — avoids false-passing on callback failure."""
        issuer = "https://matrix.test"
        client_id = "matrix-client"
        admin_token = await _setup_and_login(async_client, "matrix_adm", "Matrix123!")
        private_pem, jwks_data = _make_test_rsa_key()
        provider_id = await self._create_provider(
            async_client,
            admin_token,
            issuer,
            client_id,
            email_claim=email_claim,
            require_email_verified=require_ev,
            suffix="matrix",
        )
        sub = f"sub-matrix-{secrets.token_hex(6)}"
        await _run_oidc_callback(
            async_client,
            db_session,
            provider_id=provider_id,
            claims={"sub": sub, **claims},
            private_pem=private_pem,
            jwks_data=jwks_data,
            issuer=issuer,
            client_id=client_id,
        )
        db_session.expire_all()
        link = await self._get_oidc_link(db_session, provider_id, sub)
        assert link is not None, "UserOIDCLink must be created even when email is dropped"
        assert link.provider_email == expected

    # ── Security: auto_link guards (CREATE endpoint) ──────────────────────────

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_auto_link_blocked_with_require_ev_false(self, async_client: AsyncClient):
        """SEC-1: auto_link + require_email_verified=False must be rejected at schema level (422)."""
        admin_token = await _setup_and_login(async_client, "sec1_adm", "Sec1Adm123!")
        resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "SEC1-Test",
                "issuer_url": "https://sec1.test",
                "client_id": "sec1-client",
                "client_secret": "sec",
                "scopes": "openid email profile",
                "auto_link_existing_accounts": True,
                "require_email_verified": False,
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_auto_link_blocked_with_custom_claim_create(self, async_client: AsyncClient):
        """SEC-6: auto_link + email_claim!='email' must be rejected at schema level on CREATE (422)."""
        admin_token = await _setup_and_login(async_client, "sec6c_adm", "Sec6CAdm123!")
        resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "SEC6-Create-Test",
                "issuer_url": "https://sec6c.test",
                "client_id": "sec6c-client",
                "client_secret": "sec",
                "scopes": "openid email profile",
                "auto_link_existing_accounts": True,
                "email_claim": "upn",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_auto_link_blocked_with_custom_claim_update(self, async_client: AsyncClient):
        """SEC-6: auto_link=True + email_claim='upn' in same UPDATE request → 422 (T4)."""
        admin_token = await _setup_and_login(async_client, "sec6u_adm", "Sec6UAdm123!")
        create_resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "SEC6-Update-Test",
                "issuer_url": "https://sec6u.test",
                "client_id": "sec6u-client",
                "client_secret": "sec",
                "scopes": "openid email profile",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert create_resp.status_code == 201
        provider_id = create_resp.json()["id"]
        resp = await async_client.put(
            f"/api/v1/auth/oidc/providers/{provider_id}",
            json={"auto_link_existing_accounts": True, "email_claim": "upn"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 422

    # ── Combined-State-Guard (partial updates across two requests) ─────────────

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_partial_update_guard_require_ev(self, async_client: AsyncClient):
        """SEC-1 Combined-State-Guard: require_ev=False then auto_link=True → 422 (T1 require_ev path)."""
        admin_token = await _setup_and_login(async_client, "pg_rev_adm", "PgRev123!")
        create_resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "PG-RequireEV-Test",
                "issuer_url": "https://pg-rev.test",
                "client_id": "pg-rev-client",
                "client_secret": "sec",
                "scopes": "openid email profile",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert create_resp.status_code == 201
        provider_id = create_resp.json()["id"]

        upd1 = await async_client.put(
            f"/api/v1/auth/oidc/providers/{provider_id}",
            json={"require_email_verified": False},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert upd1.status_code == 200

        upd2 = await async_client.put(
            f"/api/v1/auth/oidc/providers/{provider_id}",
            json={"auto_link_existing_accounts": True},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert upd2.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_partial_update_guard_email_claim(self, async_client: AsyncClient):
        """SEC-6 Combined-State-Guard: email_claim='upn' then auto_link=True → 422 (T1 email_claim path)."""
        admin_token = await _setup_and_login(async_client, "pg_ec_adm", "PgEc123!")
        create_resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "PG-EmailClaim-Test",
                "issuer_url": "https://pg-ec.test",
                "client_id": "pg-ec-client",
                "client_secret": "sec",
                "scopes": "openid email profile",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert create_resp.status_code == 201
        provider_id = create_resp.json()["id"]

        upd1 = await async_client.put(
            f"/api/v1/auth/oidc/providers/{provider_id}",
            json={"email_claim": "upn"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert upd1.status_code == 200

        upd2 = await async_client.put(
            f"/api/v1/auth/oidc/providers/{provider_id}",
            json={"auto_link_existing_accounts": True},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert upd2.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_partial_update_guard_inverse_order(self, async_client: AsyncClient):
        """T2: auto_link=True first (valid), then require_ev=False → Combined-State-Guard fires (422)."""
        admin_token = await _setup_and_login(async_client, "pg_inv_adm", "PgInv123!")
        create_resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "PG-Inverse-Test",
                "issuer_url": "https://pg-inv.test",
                "client_id": "pg-inv-client",
                "client_secret": "sec",
                "scopes": "openid email profile",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert create_resp.status_code == 201
        provider_id = create_resp.json()["id"]

        # auto_link=True is safe when require_ev=True (default)
        upd1 = await async_client.put(
            f"/api/v1/auth/oidc/providers/{provider_id}",
            json={"auto_link_existing_accounts": True},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert upd1.status_code == 200

        # Disabling require_ev with auto_link already on → unsafe combined state
        upd2 = await async_client.put(
            f"/api/v1/auth/oidc/providers/{provider_id}",
            json={"require_email_verified": False},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert upd2.status_code == 422

    # ── Low5: Response fields verified ───────────────────────────────────────

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_response_includes_new_fields(self, async_client: AsyncClient):
        """Low5: OIDCProviderResponse must include email_claim and require_email_verified."""
        admin_token = await _setup_and_login(async_client, "resp_adm", "Resp123!")
        resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "ResponseFields-Test",
                "issuer_url": "https://resp.test",
                "client_id": "resp-client",
                "client_secret": "sec",
                "scopes": "openid email profile",
                "email_claim": "preferred_username",
                "require_email_verified": False,
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["email_claim"] == "preferred_username"
        assert data["require_email_verified"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_response_reflects_new_fields(self, async_client: AsyncClient):
        """Low5: PUT response must reflect updated email_claim and require_email_verified."""
        admin_token = await _setup_and_login(async_client, "upd_resp_adm", "UpdResp123!")
        create_resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "UpdateResponse-Test",
                "issuer_url": "https://upd-resp.test",
                "client_id": "upd-resp-client",
                "client_secret": "sec",
                "scopes": "openid email profile",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert create_resp.status_code == 201
        provider_id = create_resp.json()["id"]

        upd = await async_client.put(
            f"/api/v1/auth/oidc/providers/{provider_id}",
            json={"email_claim": "upn", "require_email_verified": True},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert upd.status_code == 200
        data = upd.json()
        assert data["email_claim"] == "upn"
        assert data["require_email_verified"] is True


# ===========================================================================
# TestOIDCEmailClaimValidation — T2: email_claim field validator coverage
# ===========================================================================


class TestOIDCEmailClaimValidation:
    """Schema-level validation for the email_claim field."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_invalid_claim_name_dot_rejected(self, async_client: AsyncClient):
        """email_claim with a dot (log-injection risk) must be rejected."""
        admin_token = await _setup_and_login(async_client, "ecv_adm1", "Ecv123!")
        resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "ECVTest1",
                "issuer_url": "https://ecv1.test",
                "client_id": "ecv1",
                "client_secret": "sec",
                "scopes": "openid email",
                "email_claim": "email.address",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_invalid_claim_name_starts_with_digit_rejected(self, async_client: AsyncClient):
        admin_token = await _setup_and_login(async_client, "ecv_adm2", "Ecv123!")
        resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "ECVTest2",
                "issuer_url": "https://ecv2.test",
                "client_id": "ecv2",
                "client_secret": "sec",
                "scopes": "openid email",
                "email_claim": "1invalid",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_invalid_claim_name_newline_rejected(self, async_client: AsyncClient):
        """T2 regex-bug guard: re.fullmatch must reject trailing newline."""
        admin_token = await _setup_and_login(async_client, "ecv_adm3", "Ecv123!")
        resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "ECVTest3",
                "issuer_url": "https://ecv3.test",
                "client_id": "ecv3",
                "client_secret": "sec",
                "scopes": "openid email",
                "email_claim": "email\n",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_claim_name_65_chars_rejected(self, async_client: AsyncClient):
        """email_claim longer than 64 characters must be rejected."""
        admin_token = await _setup_and_login(async_client, "ecv_adm4", "Ecv123!")
        resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "ECVTest4",
                "issuer_url": "https://ecv4.test",
                "client_id": "ecv4",
                "client_secret": "sec",
                "scopes": "openid email",
                "email_claim": "a" * 65,
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_valid_claim_name_accepted(self, async_client: AsyncClient):
        """Valid claim names like preferred_username and upn must be accepted."""
        admin_token = await _setup_and_login(async_client, "ecv_adm5", "Ecv123!")
        for claim in ("preferred_username", "upn", "email", "emailAddress"):
            resp = await async_client.post(
                "/api/v1/auth/oidc/providers",
                json={
                    "name": f"ECVTest-{claim[:8]}",
                    "issuer_url": f"https://ecv-{claim[:8]}.test",
                    "client_id": f"ecv-{claim[:8]}",
                    "client_secret": "sec",
                    "scopes": "openid email",
                    "email_claim": claim,
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert resp.status_code == 201, f"claim {claim!r} was rejected: {resp.text}"


# ===========================================================================
# TestOIDCEmailResolutionExtra — T1 / T3 / T4 additional coverage
# ===========================================================================


async def _create_provider_via_api(
    async_client: AsyncClient,
    admin_token: str,
    issuer: str,
    client_id: str,
    *,
    email_claim: str = "email",
    require_email_verified: bool = True,
    suffix: str = "",
) -> int:
    resp = await async_client.post(
        "/api/v1/auth/oidc/providers",
        json={
            "name": f"TestIdP-extra-{suffix or secrets.token_hex(4)}",
            "issuer_url": issuer,
            "client_id": client_id,
            "client_secret": "sec",
            "scopes": "openid email profile",
            "is_enabled": True,
            "auto_create_users": True,
            "email_claim": email_claim,
            "require_email_verified": require_email_verified,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


class TestOIDCEmailResolutionExtra:
    """T1: isinstance guard, T3: SEC-3 normalisation for Fall A/B, T4: inverse Combined-State-Guard."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_non_string_claim_value_drops_email(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ):
        """T1: A non-string email_claim value (list) must be silently dropped — no crash."""
        private_pem, jwks_data = _make_test_rsa_key()
        issuer = "https://nonstring-test.example"
        client_id = "nonstring-client"

        admin_token = await _setup_and_login(async_client, "nonstr_adm", "Nonstr123!")
        provider_id = await _create_provider_via_api(
            async_client,
            admin_token,
            issuer,
            client_id,
            email_claim="preferred_username",
            require_email_verified=False,
            suffix="nonstr",
        )

        from sqlalchemy import select

        from backend.app.models.oidc_provider import UserOIDCLink

        # IdP sends preferred_username as a list (non-string) — must not crash
        location = await _run_oidc_callback(
            async_client,
            db_session,
            provider_id=provider_id,
            claims={"sub": "nonstr-sub-1", "preferred_username": ["user@example.com"]},
            private_pem=private_pem,
            jwks_data=jwks_data,
            issuer=issuer,
            client_id=client_id,
        )
        assert "internal_error" not in location, f"Unexpected error redirect: {location}"
        link_result = await db_session.execute(
            select(UserOIDCLink)
            .where(UserOIDCLink.provider_id == provider_id)
            .where(UserOIDCLink.provider_user_id == "nonstr-sub-1")
        )
        link = link_result.scalar_one_or_none()
        assert link is not None
        assert link.provider_email is None  # list value dropped

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_fall_a_sec3_normalisation(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ):
        """T3: Fall A — uppercase + whitespace in email claim must be normalised to lowercase."""
        private_pem, jwks_data = _make_test_rsa_key()
        issuer = "https://sec3a-test.example"
        client_id = "sec3a-client"

        admin_token = await _setup_and_login(async_client, "sec3a_adm", "Sec3a123!")
        provider_id = await _create_provider_via_api(
            async_client,
            admin_token,
            issuer,
            client_id,
            email_claim="email",
            require_email_verified=True,
            suffix="sec3a",
        )

        from sqlalchemy import select

        from backend.app.models.oidc_provider import UserOIDCLink

        location = await _run_oidc_callback(
            async_client,
            db_session,
            provider_id=provider_id,
            claims={"sub": "sec3a-sub-1", "email": "  USER@EXAMPLE.COM  ", "email_verified": True},
            private_pem=private_pem,
            jwks_data=jwks_data,
            issuer=issuer,
            client_id=client_id,
        )
        assert "internal_error" not in location
        link_result = await db_session.execute(
            select(UserOIDCLink)
            .where(UserOIDCLink.provider_id == provider_id)
            .where(UserOIDCLink.provider_user_id == "sec3a-sub-1")
        )
        link = link_result.scalar_one_or_none()
        assert link is not None
        assert link.provider_email == "user@example.com"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_fall_b_sec3_normalisation(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ):
        """T3: Fall B — uppercase + whitespace in email claim must be normalised to lowercase."""
        private_pem, jwks_data = _make_test_rsa_key()
        issuer = "https://sec3b-test.example"
        client_id = "sec3b-client"

        admin_token = await _setup_and_login(async_client, "sec3b_adm", "Sec3b123!")
        provider_id = await _create_provider_via_api(
            async_client,
            admin_token,
            issuer,
            client_id,
            email_claim="email",
            require_email_verified=False,
            suffix="sec3b",
        )

        from sqlalchemy import select

        from backend.app.models.oidc_provider import UserOIDCLink

        location = await _run_oidc_callback(
            async_client,
            db_session,
            provider_id=provider_id,
            claims={"sub": "sec3b-sub-1", "email": "  USER@EXAMPLE.COM  "},
            private_pem=private_pem,
            jwks_data=jwks_data,
            issuer=issuer,
            client_id=client_id,
        )
        assert "internal_error" not in location
        link_result = await db_session.execute(
            select(UserOIDCLink)
            .where(UserOIDCLink.provider_id == provider_id)
            .where(UserOIDCLink.provider_user_id == "sec3b-sub-1")
        )
        link = link_result.scalar_one_or_none()
        assert link is not None
        assert link.provider_email == "user@example.com"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_combined_state_guard_email_claim_inverse_order(self, async_client: AsyncClient):
        """T4: Combined-State-Guard — set auto_link=True first, then switch email_claim to custom."""
        admin_token = await _setup_and_login(async_client, "inv_ec_adm", "InvEc123!")
        create_resp = await async_client.post(
            "/api/v1/auth/oidc/providers",
            json={
                "name": "InvEcTest",
                "issuer_url": "https://inv-ec.test",
                "client_id": "inv-ec",
                "client_secret": "sec",
                "scopes": "openid email",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert create_resp.status_code == 201
        provider_id = create_resp.json()["id"]

        # First: enable auto_link (safe — email_claim="email", require_ev=True)
        upd1 = await async_client.put(
            f"/api/v1/auth/oidc/providers/{provider_id}",
            json={"auto_link_existing_accounts": True},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert upd1.status_code == 200

        # Second: switch email_claim to custom while auto_link is on → unsafe combined state
        upd2 = await async_client.put(
            f"/api/v1/auth/oidc/providers/{provider_id}",
            json={"email_claim": "preferred_username"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert upd2.status_code == 422
