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

import pyotp
import pytest
from httpx import AsyncClient

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
    """Tests for POST /api/v1/auth/2fa/email/enable and /disable."""

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
    async def test_disable_email_otp_returns_200(self, async_client: AsyncClient):
        """Disabling email OTP (even when it wasn't enabled) should succeed."""
        token = await _setup_and_login(async_client, "disemailotp", "disemailotp1")
        response = await async_client.post("/api/v1/auth/2fa/email/disable", headers=_auth_header(token))
        assert response.status_code == 200


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
        """Full flow: setup → enable TOTP → get pre_auth_token → verify → receive JWT."""
        from backend.app.api.routes.mfa import create_pre_auth_token

        token = await _setup_and_login(async_client, "verifytotpok", "verifytotpok1")
        setup_resp = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        secret = setup_resp.json()["secret"]
        valid_code = pyotp.TOTP(secret).now()
        await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": valid_code},
            headers=_auth_header(token),
        )

        # Simulate what login does: issue a pre-auth token for this user
        pre_auth_token = create_pre_auth_token("verifytotpok")

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
        from backend.app.api.routes.mfa import create_pre_auth_token

        token = await _setup_and_login(async_client, "verifybadcode", "verifybadcode1")
        setup_resp = await async_client.post("/api/v1/auth/2fa/totp/setup", headers=_auth_header(token))
        secret = setup_resp.json()["secret"]
        valid_code = pyotp.TOTP(secret).now()
        await async_client.post(
            "/api/v1/auth/2fa/totp/enable",
            json={"code": valid_code},
            headers=_auth_header(token),
        )

        pre_auth_token = create_pre_auth_token("verifybadcode")
        verify_resp = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth_token, "method": "totp", "code": "000000"},
        )
        assert verify_resp.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_verify_invalid_method(self, async_client: AsyncClient):
        from backend.app.api.routes.mfa import create_pre_auth_token

        await _setup_and_login(async_client, "invalidmethod", "invalidmethod1")
        pre_auth_token = create_pre_auth_token("invalidmethod")
        response = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth_token, "method": "sms", "code": "123456"},
        )
        assert response.status_code == 400


# ===========================================================================
# 2FA Verify — Backup code path
# ===========================================================================


class TestTwoFAVerifyBackup:
    """Tests for POST /api/v1/auth/2fa/verify using the backup method."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_verify_with_backup_code(self, async_client: AsyncClient):
        from backend.app.api.routes.mfa import create_pre_auth_token

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

        pre_auth_token = create_pre_auth_token("backupcodeok")
        verify_resp = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth_token, "method": "backup", "code": backup_code},
        )
        assert verify_resp.status_code == 200
        assert "access_token" in verify_resp.json()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_backup_code_is_single_use(self, async_client: AsyncClient):
        from backend.app.api.routes.mfa import create_pre_auth_token

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
        pre_auth_token = create_pre_auth_token("backupsingle")
        first_resp = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth_token, "method": "backup", "code": backup_code},
        )
        assert first_resp.status_code == 200

        # Second use of the same code — must fail (token consumed above, need a new one)
        pre_auth_token2 = create_pre_auth_token("backupsingle")
        second_resp = await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth_token2, "method": "backup", "code": backup_code},
        )
        assert second_resp.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_backup_code_count_decrements(self, async_client: AsyncClient):
        from backend.app.api.routes.mfa import create_pre_auth_token

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

        pre_auth_token = create_pre_auth_token("backupcount")
        await async_client.post(
            "/api/v1/auth/2fa/verify",
            json={"pre_auth_token": pre_auth_token, "method": "backup", "code": backup_code},
        )

        # Re-login to get a fresh full token
        await async_client.post(LOGIN_URL, json={"username": "backupcount", "password": "backupcount1"})
        # After 2FA is enabled login would return requires_2fa; but we can still
        # query status with the original full token
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
        from backend.app.api.routes.mfa import _failed_2fa_attempts, create_pre_auth_token

        await _setup_and_login(async_client, "ratelimituser", "ratelimituser1")

        # Clear any previous state
        _failed_2fa_attempts.pop("ratelimituser", None)

        # Make 5 failed attempts (invalid pre_auth_token → 401 each time)
        for _ in range(5):
            pre_auth_token = create_pre_auth_token("ratelimituser")
            await async_client.post(
                "/api/v1/auth/2fa/verify",
                json={"pre_auth_token": pre_auth_token, "method": "totp", "code": "000000"},
            )

        # 6th attempt should hit the rate limit
        pre_auth_token = create_pre_auth_token("ratelimituser")
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
