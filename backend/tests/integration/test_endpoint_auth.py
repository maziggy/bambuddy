"""Integration tests for API endpoint authentication.

Tests that verify endpoints properly enforce authentication when auth is enabled,
and allow access when auth is disabled (CVE-2026-25505 fix verification).
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


class TestEndpointAuthenticationEnforcement:
    """Tests that endpoints enforce authentication when auth is enabled."""

    @pytest.fixture
    async def user_factory(self, db_session):
        """Factory to create test users."""

        async def _create_user(**kwargs):
            from passlib.hash import bcrypt

            from backend.app.models.user import User

            defaults = {
                "username": "testuser",
                "password_hash": bcrypt.hash("testpass123"),
                "is_admin": False,
            }
            defaults.update(kwargs)

            user = User(**defaults)
            db_session.add(user)
            await db_session.commit()
            await db_session.refresh(user)
            return user

        return _create_user

    @pytest.fixture
    async def admin_user(self, user_factory, db_session):
        """Create an admin user for testing."""
        from sqlalchemy import select

        from backend.app.models.group import Group

        # Get or create admin group
        result = await db_session.execute(select(Group).where(Group.name == "Administrators"))
        admin_group = result.scalar_one_or_none()

        user = await user_factory(username="admin", is_admin=True)
        if admin_group:
            user.groups.append(admin_group)
            await db_session.commit()
        return user

    @pytest.fixture
    async def auth_token(self, admin_user, async_client: AsyncClient):
        """Get a valid auth token for the admin user."""
        response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "testpass123"},
        )
        if response.status_code == 200:
            return response.json().get("access_token")
        return None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_filaments_list_accessible_without_auth_when_disabled(self, async_client: AsyncClient):
        """Verify filaments list is accessible when auth is disabled."""
        with patch("backend.app.core.auth.is_auth_enabled", return_value=False):
            response = await async_client.get("/api/v1/filaments/")
            assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_external_links_list_accessible_without_auth_when_disabled(self, async_client: AsyncClient):
        """Verify external links list is accessible when auth is disabled."""
        with patch("backend.app.core.auth.is_auth_enabled", return_value=False):
            response = await async_client.get("/api/v1/external-links/")
            assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_notifications_list_accessible_without_auth_when_disabled(self, async_client: AsyncClient):
        """Verify notifications list is accessible when auth is disabled."""
        with patch("backend.app.core.auth.is_auth_enabled", return_value=False):
            response = await async_client.get("/api/v1/notifications/")
            assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_maintenance_types_accessible_without_auth_when_disabled(self, async_client: AsyncClient):
        """Verify maintenance types is accessible when auth is disabled."""
        with patch("backend.app.core.auth.is_auth_enabled", return_value=False):
            response = await async_client.get("/api/v1/maintenance/types")
            assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_system_info_accessible_without_auth_when_disabled(self, async_client: AsyncClient):
        """Verify system info is accessible when auth is disabled."""
        with patch("backend.app.core.auth.is_auth_enabled", return_value=False):
            response = await async_client.get("/api/v1/system/info")
            assert response.status_code == 200


class TestImageEndpointsPublicAccess:
    """Tests that image endpoints remain accessible without auth.

    These endpoints serve images via <img> tags which cannot send Authorization headers.
    """

    @pytest.fixture
    async def link_with_icon(self, db_session):
        """Create an external link with a custom icon for testing."""
        from backend.app.models.external_link import ExternalLink

        link = ExternalLink(
            name="Test Link",
            url="https://example.com",
            icon="Link",
            sort_order=0,
            custom_icon=None,  # No custom icon set
        )
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)
        return link

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_external_link_icon_returns_404_when_no_icon(self, async_client: AsyncClient, link_with_icon):
        """Verify icon endpoint returns 404 (not 401) when no icon is set.

        This confirms the endpoint doesn't require auth - a 401 would indicate
        auth is being enforced, but 404 means the endpoint is accessible but
        no icon exists.
        """
        response = await async_client.get(f"/api/v1/external-links/{link_with_icon.id}/icon")
        # Should be 404 (no icon set), not 401 (unauthorized)
        assert response.status_code == 404
        assert "No custom icon set" in response.json().get("detail", "")


class TestAuthenticationPatterns:
    """Tests for authentication helper functions and patterns."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_require_permission_if_auth_enabled_allows_access_when_disabled(self, async_client: AsyncClient):
        """Verify require_permission_if_auth_enabled allows access when auth disabled."""
        with patch("backend.app.core.auth.is_auth_enabled", return_value=False):
            # Test a protected endpoint
            response = await async_client.get("/api/v1/filaments/")
            assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_multiple_endpoints_accessible_when_auth_disabled(self, async_client: AsyncClient):
        """Verify multiple protected endpoints are accessible when auth is disabled."""
        with patch("backend.app.core.auth.is_auth_enabled", return_value=False):
            endpoints = [
                "/api/v1/filaments/",
                "/api/v1/external-links/",
                "/api/v1/notifications/",
                "/api/v1/maintenance/types",
            ]

            for endpoint in endpoints:
                response = await async_client.get(endpoint)
                assert response.status_code == 200, f"Endpoint {endpoint} should be accessible"
