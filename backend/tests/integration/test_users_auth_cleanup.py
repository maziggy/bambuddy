"""Integration tests for OIDC/MFA cleanup on user deletion.

These tests verify the fix for issue #1285: deleting a user via DELETE
/api/v1/users/{id} must also remove their UserOIDCLink, UserTOTP, and
UserOTPCode rows. On PostgreSQL the FK CASCADE handles this, but SQLite
ships with FK enforcement off — without explicit DELETEs in the endpoint,
orphan rows would block SSO re-login and leak MFA secrets.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class TestDeleteUserCleansAuthRows:
    """Verify delete_user removes OIDC link + TOTP + OTP rows owned by the user."""

    @pytest.fixture
    async def auth_token(self, async_client: AsyncClient):
        """Setup auth and return admin token."""
        await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "cleanupadmin",
                "admin_password": "AdminPass1!",
            },
        )
        login_response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "cleanupadmin", "password": "AdminPass1!"},
        )
        return login_response.json()["access_token"]

    async def _create_user(self, async_client: AsyncClient, auth_token: str, username: str) -> int:
        """Helper: create a non-admin user via the API and return their id."""
        create_resp = await async_client.post(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {auth_token}"},
            json={
                "username": username,
                "password": "Password123!",
                "role": "user",
            },
        )
        assert create_resp.status_code in (200, 201), create_resp.text
        return create_resp.json()["id"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_user_removes_oidc_links(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
        auth_token: str,
    ):
        """Deleting a user must also delete their UserOIDCLink rows."""
        from backend.app.models.oidc_provider import OIDCProvider, UserOIDCLink

        user_id = await self._create_user(async_client, auth_token, "oidcclean")

        provider = OIDCProvider(
            name="CleanupProv",
            issuer_url="https://cleanup.example.com",
            client_id="cleanup_client",
            _client_secret_enc="cleanup_secret",
            scopes="openid email profile",
            is_enabled=True,
        )
        db_session.add(provider)
        await db_session.flush()
        db_session.add(
            UserOIDCLink(
                user_id=user_id,
                provider_id=provider.id,
                provider_user_id="sub-cleanup-123",
                provider_email="cleanup@example.com",
            )
        )
        await db_session.commit()

        # Sanity check: link exists before delete
        pre = await db_session.execute(select(UserOIDCLink).where(UserOIDCLink.user_id == user_id))
        assert pre.scalar_one_or_none() is not None

        # Delete via API
        resp = await async_client.delete(
            f"/api/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert resp.status_code == 204

        # Link must be gone (the bug from #1285 is when it persists on SQLite)
        await db_session.commit()
        post = await db_session.execute(select(UserOIDCLink).where(UserOIDCLink.user_id == user_id))
        assert post.scalar_one_or_none() is None, "UserOIDCLink orphan left behind — #1285 regression"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_user_removes_user_totp(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
        auth_token: str,
    ):
        """Deleting a user must also delete their UserTOTP row (MFA secret)."""
        from backend.app.models.user_totp import UserTOTP

        user_id = await self._create_user(async_client, auth_token, "totpclean")

        totp = UserTOTP(user_id=user_id, is_enabled=True)
        totp.secret = "JBSWY3DPEHPK3PXP"  # encrypts via property setter
        db_session.add(totp)
        await db_session.commit()

        pre = await db_session.execute(select(UserTOTP).where(UserTOTP.user_id == user_id))
        assert pre.scalar_one_or_none() is not None

        resp = await async_client.delete(
            f"/api/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert resp.status_code == 204

        await db_session.commit()
        post = await db_session.execute(select(UserTOTP).where(UserTOTP.user_id == user_id))
        assert post.scalar_one_or_none() is None, "UserTOTP orphan — MFA secret leaked after user delete"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_user_removes_user_otp_codes(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
        auth_token: str,
    ):
        """Deleting a user must also delete their UserOTPCode rows."""
        from backend.app.models.user_otp_code import UserOTPCode

        user_id = await self._create_user(async_client, auth_token, "otpclean")

        # Two pending OTP codes so we verify the WHERE clause hits all rows
        for _ in range(2):
            db_session.add(
                UserOTPCode(
                    user_id=user_id,
                    code_hash="$pbkdf2-sha256$dummy",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
                )
            )
        await db_session.commit()

        pre = await db_session.execute(select(UserOTPCode).where(UserOTPCode.user_id == user_id))
        assert len(pre.scalars().all()) == 2

        resp = await async_client.delete(
            f"/api/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert resp.status_code == 204

        await db_session.commit()
        post = await db_session.execute(select(UserOTPCode).where(UserOTPCode.user_id == user_id))
        assert post.scalars().all() == [], "UserOTPCode orphans left behind"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_user_with_all_auth_rows(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
        auth_token: str,
    ):
        """Combined: one user with OIDC link + TOTP + OTP code — all three cleaned up atomically."""
        from backend.app.models.oidc_provider import OIDCProvider, UserOIDCLink
        from backend.app.models.user_otp_code import UserOTPCode
        from backend.app.models.user_totp import UserTOTP

        user_id = await self._create_user(async_client, auth_token, "fullauth")

        provider = OIDCProvider(
            name="FullAuthProv",
            issuer_url="https://fullauth.example.com",
            client_id="fullauth_client",
            _client_secret_enc="fullauth_secret",
            scopes="openid email profile",
            is_enabled=True,
        )
        db_session.add(provider)
        await db_session.flush()

        db_session.add(
            UserOIDCLink(
                user_id=user_id,
                provider_id=provider.id,
                provider_user_id="sub-fullauth",
                provider_email="full@example.com",
            )
        )
        totp = UserTOTP(user_id=user_id, is_enabled=True)
        totp.secret = "JBSWY3DPEHPK3PXP"
        db_session.add(totp)
        db_session.add(
            UserOTPCode(
                user_id=user_id,
                code_hash="$pbkdf2-sha256$dummy",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
        )
        await db_session.commit()

        resp = await async_client.delete(
            f"/api/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert resp.status_code == 204

        await db_session.commit()
        link_post = await db_session.execute(select(UserOIDCLink).where(UserOIDCLink.user_id == user_id))
        totp_post = await db_session.execute(select(UserTOTP).where(UserTOTP.user_id == user_id))
        otp_post = await db_session.execute(select(UserOTPCode).where(UserOTPCode.user_id == user_id))
        assert link_post.scalar_one_or_none() is None
        assert totp_post.scalar_one_or_none() is None
        assert otp_post.scalars().all() == []
