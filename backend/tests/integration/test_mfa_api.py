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
from datetime import datetime, timedelta, timezone

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


async def _setup_and_login(client: AsyncClient, username: str, password: str) -> str:
    """Enable auth, create an admin user, login, and return the bearer token."""
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
    async def test_confirm_enable_email_otp_happy_path(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
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
    async def test_confirm_enable_email_otp_wrong_code(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
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
    async def test_disable_email_otp_when_enabled(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
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
            json={"password": "disemailpw1"},
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
        response = await async_client.delete("/api/v1/auth/2fa/admin/99999", headers=_auth_header(token))
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

        response = await async_client.delete(f"/api/v1/auth/2fa/admin/{user_id}", headers=_auth_header(token))
        assert response.status_code == 200

        # Status should now show TOTP disabled
        status_resp = await async_client.get("/api/v1/auth/2fa/status", headers=_auth_header(token))
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
            json={"username": "regularusr2fa", "password": "regularusr2fa1"},
            headers=_auth_header(admin_token),
        )
        assert create_resp.status_code == 201

        # Login as regular user
        login_resp = await async_client.post(
            LOGIN_URL,
            json={"username": "regularusr2fa", "password": "regularusr2fa1"},
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
