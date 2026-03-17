"""Integration tests for per-user cloud credentials and cloud endpoint permissions.

Regression tests for:
- Per-user cloud token storage (when auth enabled)
- Global fallback (when auth disabled)
- Cloud endpoints use CLOUD_AUTH permission (not SETTINGS_READ)
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


class TestPerUserCloudCredentials:
    """Tests that cloud credentials are stored per-user when auth is enabled."""

    @pytest.fixture
    async def user_with_cloud_auth(self, db_session):
        """Create a user with CLOUD_AUTH permission via a group."""
        from backend.app.core.auth import get_password_hash
        from backend.app.models.group import Group
        from backend.app.models.user import User

        group = Group(
            name="CloudUsers",
            permissions=["cloud:auth", "filaments:read", "printers:read", "firmware:read"],
        )
        db_session.add(group)
        await db_session.flush()

        user = User(
            username="clouduser",
            password_hash=get_password_hash("testpass123"),
            role="user",
        )
        db_session.add(user)
        await db_session.flush()
        user.groups.append(group)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    @pytest.fixture
    async def second_user_with_cloud_auth(self, db_session):
        """Create a second user with CLOUD_AUTH permission."""
        from sqlalchemy import select

        from backend.app.core.auth import get_password_hash
        from backend.app.models.group import Group
        from backend.app.models.user import User

        result = await db_session.execute(select(Group).where(Group.name == "CloudUsers"))
        group = result.scalar_one_or_none()
        if not group:
            group = Group(
                name="CloudUsers2",
                permissions=["cloud:auth", "filaments:read", "printers:read", "firmware:read"],
            )
            db_session.add(group)
            await db_session.flush()

        user = User(
            username="clouduser2",
            password_hash=get_password_hash("testpass456"),
            role="user",
        )
        db_session.add(user)
        await db_session.flush()
        user.groups.append(group)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    @pytest.fixture
    async def cloud_auth_token(self, user_with_cloud_auth, async_client: AsyncClient):
        """Get auth token for user with cloud permissions."""
        response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "clouduser", "password": "testpass123"},
        )
        if response.status_code == 200:
            return response.json().get("access_token")
        return None

    @pytest.fixture
    async def second_auth_token(self, second_user_with_cloud_auth, async_client: AsyncClient):
        """Get auth token for second user."""
        response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "clouduser2", "password": "testpass456"},
        )
        if response.status_code == 200:
            return response.json().get("access_token")
        return None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cloud_status_returns_not_authenticated_by_default(self, async_client: AsyncClient):
        """Cloud status should show not authenticated when no token is stored."""
        with patch("backend.app.core.auth.is_auth_enabled", return_value=False):
            response = await async_client.get("/api/v1/cloud/status")
            assert response.status_code == 200
            data = response.json()
            assert data["is_authenticated"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cloud_status_accessible_when_auth_disabled(self, async_client: AsyncClient):
        """Cloud endpoints should work when auth is disabled (global fallback)."""
        with patch("backend.app.core.auth.is_auth_enabled", return_value=False):
            response = await async_client.get("/api/v1/cloud/status")
            assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cloud_status_requires_auth_when_enabled(self, async_client: AsyncClient):
        """Cloud endpoints should require auth when auth is enabled."""
        with patch("backend.app.core.auth.is_auth_enabled", return_value=True):
            response = await async_client.get("/api/v1/cloud/status")
            assert response.status_code == 401


class TestCloudEndpointPermissions:
    """Tests that cloud endpoints use CLOUD_AUTH permission, not SETTINGS_READ.

    Uses JWT tokens created directly (not via login endpoint) to avoid
    test infrastructure complexity with user creation across sessions.
    """

    @pytest.fixture
    async def settings_only_setup(self, async_client: AsyncClient):
        """Create user with settings:read but NOT cloud:auth, return JWT."""
        from backend.app.core.auth import create_access_token, get_password_hash
        from backend.app.core.database import async_session
        from backend.app.models.group import Group
        from backend.app.models.user import User

        async with async_session() as db:
            group = Group(name="SettingsReaders", permissions=["settings:read"])
            db.add(group)
            user = User(
                username="settingsuser",
                password_hash=get_password_hash("testpass123"),
                role="user",
            )
            db.add(user)
            await db.commit()
            await db.refresh(group)
            await db.refresh(user)

            from sqlalchemy import text

            await db.execute(
                text("INSERT INTO user_groups (user_id, group_id) VALUES (:uid, :gid)"),
                {"uid": user.id, "gid": group.id},
            )
            await db.commit()

        return create_access_token(data={"sub": "settingsuser"})

    @pytest.fixture
    async def cloud_only_setup(self, async_client: AsyncClient):
        """Create user with cloud:auth but NOT settings:read, return JWT."""
        from backend.app.core.auth import create_access_token, get_password_hash
        from backend.app.core.database import async_session
        from backend.app.models.group import Group
        from backend.app.models.user import User

        async with async_session() as db:
            group = Group(name="CloudOnly", permissions=["cloud:auth"])
            db.add(group)
            user = User(
                username="cloudonly",
                password_hash=get_password_hash("testpass123"),
                role="user",
            )
            db.add(user)
            await db.commit()
            await db.refresh(group)
            await db.refresh(user)

            from sqlalchemy import text

            await db.execute(
                text("INSERT INTO user_groups (user_id, group_id) VALUES (:uid, :gid)"),
                {"uid": user.id, "gid": group.id},
            )
            await db.commit()

        return create_access_token(data={"sub": "cloudonly"})

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cloud_settings_requires_cloud_auth_not_settings_read(
        self, async_client: AsyncClient, settings_only_setup, cloud_only_setup
    ):
        """GET /cloud/settings should require CLOUD_AUTH, not SETTINGS_READ.

        Regression test: previously used SETTINGS_READ which blocked users who
        had cloud:auth permission but not settings:read.
        """
        with patch("backend.app.core.auth.is_auth_enabled", return_value=True):
            # User with only settings:read should be denied
            response = await async_client.get(
                "/api/v1/cloud/settings",
                headers={"Authorization": f"Bearer {settings_only_setup}"},
            )
            assert response.status_code == 403

            # User with cloud:auth should be allowed (will get 401 since no cloud token,
            # but NOT 403 — permission check passes)
            response = await async_client.get(
                "/api/v1/cloud/settings",
                headers={"Authorization": f"Bearer {cloud_only_setup}"},
            )
            assert response.status_code == 401  # No cloud token, but permission OK

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cloud_status_requires_cloud_auth(
        self, async_client: AsyncClient, settings_only_setup, cloud_only_setup
    ):
        """GET /cloud/status should require CLOUD_AUTH."""
        with patch("backend.app.core.auth.is_auth_enabled", return_value=True):
            # settings:read only → 403
            response = await async_client.get(
                "/api/v1/cloud/status",
                headers={"Authorization": f"Bearer {settings_only_setup}"},
            )
            assert response.status_code == 403

            # cloud:auth → 200
            response = await async_client.get(
                "/api/v1/cloud/status",
                headers={"Authorization": f"Bearer {cloud_only_setup}"},
            )
            assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cloud_fields_requires_cloud_auth(
        self, async_client: AsyncClient, settings_only_setup, cloud_only_setup
    ):
        """GET /cloud/fields should require CLOUD_AUTH, not SETTINGS_READ."""
        with patch("backend.app.core.auth.is_auth_enabled", return_value=True):
            # settings:read only → 403
            response = await async_client.get(
                "/api/v1/cloud/fields",
                headers={"Authorization": f"Bearer {settings_only_setup}"},
            )
            assert response.status_code == 403

            # cloud:auth → 200
            response = await async_client.get(
                "/api/v1/cloud/fields",
                headers={"Authorization": f"Bearer {cloud_only_setup}"},
            )
            assert response.status_code == 200


class TestCloudTokenStorage:
    """Unit-level tests for the token storage functions."""

    @pytest.mark.asyncio
    async def test_get_stored_token_returns_none_when_no_user_no_global(self, db_session):
        """get_stored_token with user=None and no global token returns (None, None)."""
        from backend.app.api.routes.cloud import get_stored_token

        token, email = await get_stored_token(db_session, user=None)
        assert token is None
        assert email is None

    @pytest.mark.asyncio
    async def test_store_and_get_global_token(self, db_session):
        """store_token with user=None stores in global Settings table."""
        from backend.app.api.routes.cloud import get_stored_token, store_token

        await store_token(db_session, "test-token-123", "test@example.com", user=None)
        token, email = await get_stored_token(db_session, user=None)
        assert token == "test-token-123"
        assert email == "test@example.com"

    @pytest.mark.asyncio
    async def test_store_and_get_per_user_token(self, db_session):
        """store_token with user stores on the user record."""
        from backend.app.api.routes.cloud import get_stored_token, store_token
        from backend.app.core.auth import get_password_hash
        from backend.app.models.user import User

        user = User(username="tokentest", password_hash=get_password_hash("pass"), role="user")
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        await store_token(db_session, "user-token-abc", "user@example.com", user=user)

        # Re-fetch user to verify persistence
        from sqlalchemy import select

        result = await db_session.execute(select(User).where(User.id == user.id))
        refreshed = result.scalar_one()
        assert refreshed.cloud_token == "user-token-abc"
        assert refreshed.cloud_email == "user@example.com"

    @pytest.mark.asyncio
    async def test_per_user_token_does_not_affect_global(self, db_session):
        """Storing per-user token should not affect global Settings."""
        from backend.app.api.routes.cloud import get_stored_token, store_token
        from backend.app.core.auth import get_password_hash
        from backend.app.models.user import User

        user = User(username="isolationtest", password_hash=get_password_hash("pass"), role="user")
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Store per-user token
        await store_token(db_session, "per-user-token", "per-user@test.com", user=user)

        # Global should still be empty
        global_token, global_email = await get_stored_token(db_session, user=None)
        assert global_token is None
        assert global_email is None

    @pytest.mark.asyncio
    async def test_clear_per_user_token(self, db_session):
        """clear_token with user clears only that user's credentials."""
        from backend.app.api.routes.cloud import clear_token, get_stored_token, store_token
        from backend.app.core.auth import get_password_hash
        from backend.app.models.user import User

        user = User(username="cleartest", password_hash=get_password_hash("pass"), role="user")
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        await store_token(db_session, "to-clear", "clear@test.com", user=user)
        await clear_token(db_session, user=user)

        from sqlalchemy import select

        result = await db_session.execute(select(User).where(User.id == user.id))
        refreshed = result.scalar_one()
        assert refreshed.cloud_token is None
        assert refreshed.cloud_email is None

    @pytest.mark.asyncio
    async def test_clear_global_token(self, db_session):
        """clear_token with user=None clears from global Settings."""
        from backend.app.api.routes.cloud import clear_token, get_stored_token, store_token

        await store_token(db_session, "global-token", "global@test.com", user=None)
        await clear_token(db_session, user=None)

        token, email = await get_stored_token(db_session, user=None)
        assert token is None
        assert email is None

    @pytest.mark.asyncio
    async def test_two_users_independent_tokens(self, db_session):
        """Two users should have completely independent cloud tokens."""
        from backend.app.api.routes.cloud import get_stored_token, store_token
        from backend.app.core.auth import get_password_hash
        from backend.app.models.user import User

        user_a = User(username="user_a", password_hash=get_password_hash("pass"), role="user")
        user_b = User(username="user_b", password_hash=get_password_hash("pass"), role="user")
        db_session.add_all([user_a, user_b])
        await db_session.commit()
        await db_session.refresh(user_a)
        await db_session.refresh(user_b)

        await store_token(db_session, "token-a", "a@test.com", user=user_a)
        await store_token(db_session, "token-b", "b@test.com", user=user_b)

        # Verify each user reads their own token (re-fetch from DB)
        from sqlalchemy import select

        result_a = await db_session.execute(select(User).where(User.id == user_a.id))
        result_b = await db_session.execute(select(User).where(User.id == user_b.id))
        fresh_a = result_a.scalar_one()
        fresh_b = result_b.scalar_one()

        token_a, email_a = await get_stored_token(db_session, user=fresh_a)
        token_b, email_b = await get_stored_token(db_session, user=fresh_b)

        assert token_a == "token-a"
        assert email_a == "a@test.com"
        assert token_b == "token-b"
        assert email_b == "b@test.com"
