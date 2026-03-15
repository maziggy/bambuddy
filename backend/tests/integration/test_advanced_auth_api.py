"""Integration tests for Advanced Authentication API endpoints.

Tests the full request/response cycle for SMTP configuration, advanced auth toggle,
email-based login, forgot password, admin password reset, and user creation
with advanced authentication enabled.
"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient

# Shared SMTP settings data used across test classes
SMTP_DATA = {
    "smtp_host": "smtp.test.com",
    "smtp_port": 587,
    "smtp_username": "test@test.com",
    "smtp_password": "testpass",
    "smtp_security": "starttls",
    "smtp_auth_enabled": True,
    "smtp_from_email": "noreply@test.com",
}


async def _setup_admin(async_client: AsyncClient, username: str = "admin", password: str = "adminpass123"):
    """Enable auth and create admin user, return admin token."""
    await async_client.post(
        "/api/v1/auth/setup",
        json={
            "auth_enabled": True,
            "admin_username": username,
            "admin_password": password,
        },
    )
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    return login.json()["access_token"]


async def _setup_smtp_and_advanced_auth(async_client: AsyncClient, token: str):
    """Configure SMTP and enable advanced auth. Must mock send_email externally."""
    headers = {"Authorization": f"Bearer {token}"}
    await async_client.post("/api/v1/auth/smtp", headers=headers, json=SMTP_DATA)
    await async_client.post("/api/v1/auth/advanced-auth/enable", headers=headers)


async def _create_regular_user(
    async_client: AsyncClient, token: str, username: str = "regular", password: str = "regularpass123"
):
    """Create a regular (non-admin) user and return their token."""
    headers = {"Authorization": f"Bearer {token}"}
    await async_client.post(
        "/api/v1/users/",
        headers=headers,
        json={"username": username, "password": password, "role": "user"},
    )
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    return login.json()["access_token"]


class TestSMTPConfigAPI:
    """Integration tests for SMTP configuration endpoints."""

    @pytest.fixture
    async def admin_token(self, async_client: AsyncClient):
        return await _setup_admin(async_client, "smtpadmin", "adminpass123")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_save_smtp_settings(self, async_client: AsyncClient, admin_token: str):
        """POST /auth/smtp with valid settings returns 200."""
        response = await async_client.post(
            "/api/v1/auth/smtp",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=SMTP_DATA,
        )
        assert response.status_code == 200
        assert "saved" in response.json()["message"].lower() or "success" in response.json()["message"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_smtp_settings_masks_password(self, async_client: AsyncClient, admin_token: str):
        """GET /auth/smtp returns settings with password masked (None)."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        # Save settings first
        await async_client.post("/api/v1/auth/smtp", headers=headers, json=SMTP_DATA)

        response = await async_client.get("/api/v1/auth/smtp", headers=headers)
        assert response.status_code == 200
        result = response.json()
        assert result["smtp_host"] == "smtp.test.com"
        assert result["smtp_password"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_smtp_settings_requires_admin(self, async_client: AsyncClient, admin_token: str):
        """Non-admin user gets 403 on SMTP endpoints."""
        user_token = await _create_regular_user(async_client, admin_token, "smtpregular", "pass123456")
        headers = {"Authorization": f"Bearer {user_token}"}

        response = await async_client.post("/api/v1/auth/smtp", headers=headers, json=SMTP_DATA)
        assert response.status_code == 403

        response = await async_client.get("/api/v1/auth/smtp", headers=headers)
        assert response.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_save_smtp_settings_no_auth(self, async_client: AsyncClient, admin_token: str):
        """No token on SMTP save returns 401."""
        response = await async_client.post("/api/v1/auth/smtp", json=SMTP_DATA)
        assert response.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_test_smtp_connection(self, async_client: AsyncClient, admin_token: str):
        """POST /auth/smtp/test with mocked send_email returns success."""
        await async_client.post(
            "/api/v1/auth/smtp",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=SMTP_DATA,
        )

        with patch("backend.app.api.routes.auth.send_email"):
            response = await async_client.post(
                "/api/v1/auth/smtp/test",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={
                    "test_recipient": "recipient@test.com",
                },
            )
        assert response.status_code == 200
        assert response.json()["success"] is True


class TestAdvancedAuthToggleAPI:
    """Integration tests for enabling/disabling advanced authentication."""

    @pytest.fixture
    async def admin_token(self, async_client: AsyncClient):
        return await _setup_admin(async_client, "toggleadmin", "adminpass123")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_enable_advanced_auth(self, async_client: AsyncClient, admin_token: str):
        """Enable advanced auth after SMTP is configured returns 200."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        # Configure SMTP first
        await async_client.post("/api/v1/auth/smtp", headers=headers, json=SMTP_DATA)

        response = await async_client.post("/api/v1/auth/advanced-auth/enable", headers=headers)
        assert response.status_code == 200
        assert response.json()["advanced_auth_enabled"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_enable_advanced_auth_without_smtp(self, async_client: AsyncClient, admin_token: str):
        """Enable advanced auth without SMTP configured returns 400."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = await async_client.post("/api/v1/auth/advanced-auth/enable", headers=headers)
        assert response.status_code == 400
        assert "SMTP" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_disable_advanced_auth(self, async_client: AsyncClient, admin_token: str):
        """Disable advanced auth returns 200."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        # Enable first
        await async_client.post("/api/v1/auth/smtp", headers=headers, json=SMTP_DATA)
        await async_client.post("/api/v1/auth/advanced-auth/enable", headers=headers)

        response = await async_client.post("/api/v1/auth/advanced-auth/disable", headers=headers)
        assert response.status_code == 200
        assert response.json()["advanced_auth_enabled"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_advanced_auth_status_public(self, async_client: AsyncClient, admin_token: str):
        """GET /auth/advanced-auth/status is accessible without token."""
        response = await async_client.get("/api/v1/auth/advanced-auth/status")
        assert response.status_code == 200
        result = response.json()
        assert "advanced_auth_enabled" in result
        assert "smtp_configured" in result

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_enable_requires_admin(self, async_client: AsyncClient, admin_token: str):
        """Non-admin user gets 403 on enable/disable."""
        user_token = await _create_regular_user(async_client, admin_token, "toggleregular", "pass123456")
        headers = {"Authorization": f"Bearer {user_token}"}

        response = await async_client.post("/api/v1/auth/advanced-auth/enable", headers=headers)
        assert response.status_code == 403

        response = await async_client.post("/api/v1/auth/advanced-auth/disable", headers=headers)
        assert response.status_code == 403


class TestEmailLoginAPI:
    """Integration tests for email-based login."""

    @pytest.fixture
    async def admin_token(self, async_client: AsyncClient):
        return await _setup_admin(async_client, "emailadmin", "adminpass123")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_login_with_email(self, async_client: AsyncClient, admin_token: str):
        """Login with email address when advanced auth is enabled returns token."""
        headers = {"Authorization": f"Bearer {admin_token}"}

        with patch("backend.app.api.routes.users.send_email"):
            # Configure SMTP + advanced auth
            await _setup_smtp_and_advanced_auth(async_client, admin_token)

            # Create user with email (password auto-generated, so we set one explicitly via update)
            create_resp = await async_client.post(
                "/api/v1/users/",
                headers=headers,
                json={"username": "emailuser", "email": "emailuser@test.com", "role": "user"},
            )
            assert create_resp.status_code == 201
            user_id = create_resp.json()["id"]

            # Set a known password via admin update
            await async_client.patch(
                f"/api/v1/users/{user_id}",
                headers=headers,
                json={"password": "knownpassword123"},
            )

        # Login with email
        response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "emailuser@test.com", "password": "knownpassword123"},
        )
        assert response.status_code == 200
        assert "access_token" in response.json()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_login_with_email_case_insensitive(self, async_client: AsyncClient, admin_token: str):
        """Login with uppercase email matches case-insensitively."""
        headers = {"Authorization": f"Bearer {admin_token}"}

        with patch("backend.app.api.routes.users.send_email"):
            await _setup_smtp_and_advanced_auth(async_client, admin_token)

            create_resp = await async_client.post(
                "/api/v1/users/",
                headers=headers,
                json={"username": "caseuser", "email": "caseuser@test.com", "role": "user"},
            )
            user_id = create_resp.json()["id"]
            await async_client.patch(
                f"/api/v1/users/{user_id}",
                headers=headers,
                json={"password": "casepassword123"},
            )

        response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "CASEUSER@TEST.COM", "password": "casepassword123"},
        )
        assert response.status_code == 200
        assert "access_token" in response.json()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_login_with_email_advanced_auth_disabled(self, async_client: AsyncClient, admin_token: str):
        """Email login fails when advanced auth is disabled."""
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Create user with email but no advanced auth
        await async_client.post(
            "/api/v1/users/",
            headers=headers,
            json={"username": "noemail", "password": "noEmailPass1", "email": "noemail@test.com", "role": "user"},
        )

        # Try to login with email — should fail since advanced auth is off
        response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "noemail@test.com", "password": "noEmailPass1"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_login_with_username_still_works(self, async_client: AsyncClient, admin_token: str):
        """Username-based login still works when advanced auth is enabled."""
        headers = {"Authorization": f"Bearer {admin_token}"}

        with patch("backend.app.api.routes.users.send_email"):
            await _setup_smtp_and_advanced_auth(async_client, admin_token)

            create_resp = await async_client.post(
                "/api/v1/users/",
                headers=headers,
                json={"username": "usernameuser", "email": "usernameuser@test.com", "role": "user"},
            )
            user_id = create_resp.json()["id"]
            await async_client.patch(
                f"/api/v1/users/{user_id}",
                headers=headers,
                json={"password": "usernamepass123"},
            )

        # Login with username (not email)
        response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "usernameuser", "password": "usernamepass123"},
        )
        assert response.status_code == 200
        assert "access_token" in response.json()


class TestForgotPasswordAPI:
    """Integration tests for forgot-password flow."""

    @pytest.fixture
    async def admin_token(self, async_client: AsyncClient):
        return await _setup_admin(async_client, "forgotadmin", "adminpass123")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_forgot_password_sends_email(self, async_client: AsyncClient, admin_token: str):
        """POST /auth/forgot-password with valid email sends reset email."""
        headers = {"Authorization": f"Bearer {admin_token}"}

        with patch("backend.app.api.routes.users.send_email"):
            await _setup_smtp_and_advanced_auth(async_client, admin_token)

            # Create a user with email
            create_resp = await async_client.post(
                "/api/v1/users/",
                headers=headers,
                json={"username": "forgotuser", "email": "forgot@test.com", "role": "user"},
            )
            assert create_resp.status_code == 201

        with patch("backend.app.api.routes.auth.send_email") as mock_send:
            response = await async_client.post(
                "/api/v1/auth/forgot-password",
                json={"email": "forgot@test.com"},
            )

        assert response.status_code == 200
        mock_send.assert_called_once()
        # Verify the email was sent to the right address
        assert mock_send.call_args[0][1] == "forgot@test.com"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_forgot_password_unknown_email(self, async_client: AsyncClient, admin_token: str):
        """Unknown email still returns 200 (anti-enumeration) but send_email not called."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        await async_client.post("/api/v1/auth/smtp", headers=headers, json=SMTP_DATA)
        await async_client.post("/api/v1/auth/advanced-auth/enable", headers=headers)

        with patch("backend.app.api.routes.auth.send_email") as mock_send:
            response = await async_client.post(
                "/api/v1/auth/forgot-password",
                json={"email": "unknown@test.com"},
            )

        assert response.status_code == 200
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_forgot_password_requires_advanced_auth(self, async_client: AsyncClient, admin_token: str):
        """Forgot password returns 400 when advanced auth is disabled."""
        response = await async_client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "test@test.com"},
        )
        assert response.status_code == 400
        assert "not enabled" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_forgot_password_changes_password(self, async_client: AsyncClient, admin_token: str):
        """After forgot-password, old password stops working."""
        headers = {"Authorization": f"Bearer {admin_token}"}

        with patch("backend.app.api.routes.users.send_email"):
            await _setup_smtp_and_advanced_auth(async_client, admin_token)

            create_resp = await async_client.post(
                "/api/v1/users/",
                headers=headers,
                json={"username": "resetme", "email": "resetme@test.com", "role": "user"},
            )
            user_id = create_resp.json()["id"]
            await async_client.patch(
                f"/api/v1/users/{user_id}",
                headers=headers,
                json={"password": "originalpass123"},
            )

        # Verify login works with original password
        login_resp = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "resetme", "password": "originalpass123"},
        )
        assert login_resp.status_code == 200

        # Trigger forgot password
        with patch("backend.app.api.routes.auth.send_email"):
            await async_client.post(
                "/api/v1/auth/forgot-password",
                json={"email": "resetme@test.com"},
            )

        # Old password should no longer work
        login_resp = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "resetme", "password": "originalpass123"},
        )
        assert login_resp.status_code == 401


class TestAdminResetPasswordAPI:
    """Integration tests for admin password reset endpoint."""

    @pytest.fixture
    async def admin_token(self, async_client: AsyncClient):
        return await _setup_admin(async_client, "resetadmin", "adminpass123")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reset_password_sends_email(self, async_client: AsyncClient, admin_token: str):
        """POST /auth/reset-password sends email to user."""
        headers = {"Authorization": f"Bearer {admin_token}"}

        with patch("backend.app.api.routes.users.send_email"):
            await _setup_smtp_and_advanced_auth(async_client, admin_token)

            create_resp = await async_client.post(
                "/api/v1/users/",
                headers=headers,
                json={"username": "resetuser", "email": "resetuser@test.com", "role": "user"},
            )
            user_id = create_resp.json()["id"]

        with patch("backend.app.api.routes.auth.send_email") as mock_send:
            response = await async_client.post(
                "/api/v1/auth/reset-password",
                headers=headers,
                json={"user_id": user_id},
            )

        assert response.status_code == 200
        mock_send.assert_called_once()
        assert mock_send.call_args[0][1] == "resetuser@test.com"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reset_password_requires_admin(self, async_client: AsyncClient, admin_token: str):
        """Non-admin user gets 403 on reset-password."""
        # Create regular user before enabling advanced auth (no email required)
        user_token = await _create_regular_user(async_client, admin_token, "resetregular", "pass123456")

        with patch("backend.app.api.routes.users.send_email"):
            await _setup_smtp_and_advanced_auth(async_client, admin_token)

        response = await async_client.post(
            "/api/v1/auth/reset-password",
            headers={"Authorization": f"Bearer {user_token}"},
            json={"user_id": 1},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reset_password_requires_advanced_auth(self, async_client: AsyncClient, admin_token: str):
        """Reset password returns 400 when advanced auth is disabled."""
        headers = {"Authorization": f"Bearer {admin_token}"}

        response = await async_client.post(
            "/api/v1/auth/reset-password",
            headers=headers,
            json={"user_id": 999},
        )
        assert response.status_code == 400
        assert "not enabled" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reset_password_user_not_found(self, async_client: AsyncClient, admin_token: str):
        """Reset password with invalid user_id returns 404."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        await async_client.post("/api/v1/auth/smtp", headers=headers, json=SMTP_DATA)
        await async_client.post("/api/v1/auth/advanced-auth/enable", headers=headers)

        response = await async_client.post(
            "/api/v1/auth/reset-password",
            headers=headers,
            json={"user_id": 99999},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reset_password_user_no_email(self, async_client: AsyncClient, admin_token: str):
        """Reset password for user without email returns 400."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        # Save SMTP and enable advanced auth
        await async_client.post("/api/v1/auth/smtp", headers=headers, json=SMTP_DATA)
        await async_client.post("/api/v1/auth/advanced-auth/enable", headers=headers)

        # Disable advanced auth temporarily to create a user without email
        await async_client.post("/api/v1/auth/advanced-auth/disable", headers=headers)
        create_resp = await async_client.post(
            "/api/v1/users/",
            headers=headers,
            json={"username": "noemailuser", "password": "noemail123456", "role": "user"},
        )
        user_id = create_resp.json()["id"]

        # Re-enable advanced auth
        await async_client.post("/api/v1/auth/advanced-auth/enable", headers=headers)

        response = await async_client.post(
            "/api/v1/auth/reset-password",
            headers=headers,
            json={"user_id": user_id},
        )
        assert response.status_code == 400
        assert "email" in response.json()["detail"].lower()


class TestUserCreationAdvancedAuth:
    """Integration tests for user creation with advanced auth enabled."""

    @pytest.fixture
    async def admin_token(self, async_client: AsyncClient):
        return await _setup_admin(async_client, "createadmin", "adminpass123")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_user_advanced_auth_requires_email(self, async_client: AsyncClient, admin_token: str):
        """Creating user without email when advanced auth is on returns 400."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        await async_client.post("/api/v1/auth/smtp", headers=headers, json=SMTP_DATA)
        await async_client.post("/api/v1/auth/advanced-auth/enable", headers=headers)

        response = await async_client.post(
            "/api/v1/users/",
            headers=headers,
            json={"username": "noemailcreate", "role": "user"},
        )
        assert response.status_code == 400
        assert "email" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_user_advanced_auth_auto_password(self, async_client: AsyncClient, admin_token: str):
        """Creating user with email auto-generates password and sends welcome email."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        await async_client.post("/api/v1/auth/smtp", headers=headers, json=SMTP_DATA)
        await async_client.post("/api/v1/auth/advanced-auth/enable", headers=headers)

        with patch("backend.app.api.routes.users.send_email") as mock_send:
            response = await async_client.post(
                "/api/v1/users/",
                headers=headers,
                json={"username": "autopassuser", "email": "autopass@test.com", "role": "user"},
            )

        assert response.status_code == 201
        result = response.json()
        assert result["username"] == "autopassuser"
        assert result["email"] == "autopass@test.com"
        # Welcome email should have been sent
        mock_send.assert_called_once()
        assert mock_send.call_args[0][1] == "autopass@test.com"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_user_duplicate_email(self, async_client: AsyncClient, admin_token: str):
        """Creating two users with the same email returns 400."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        await async_client.post("/api/v1/auth/smtp", headers=headers, json=SMTP_DATA)
        await async_client.post("/api/v1/auth/advanced-auth/enable", headers=headers)

        with patch("backend.app.api.routes.users.send_email"):
            resp1 = await async_client.post(
                "/api/v1/users/",
                headers=headers,
                json={"username": "dupemail1", "email": "dupe@test.com", "role": "user"},
            )
            assert resp1.status_code == 201

            resp2 = await async_client.post(
                "/api/v1/users/",
                headers=headers,
                json={"username": "dupemail2", "email": "dupe@test.com", "role": "user"},
            )

        assert resp2.status_code == 400
        assert "email" in resp2.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_user_response_includes_email(self, async_client: AsyncClient, admin_token: str):
        """Created user response includes email field."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        await async_client.post("/api/v1/auth/smtp", headers=headers, json=SMTP_DATA)
        await async_client.post("/api/v1/auth/advanced-auth/enable", headers=headers)

        with patch("backend.app.api.routes.users.send_email"):
            response = await async_client.post(
                "/api/v1/users/",
                headers=headers,
                json={"username": "emailresp", "email": "emailresp@test.com", "role": "user"},
            )

        assert response.status_code == 201
        result = response.json()
        assert "email" in result
        assert result["email"] == "emailresp@test.com"
