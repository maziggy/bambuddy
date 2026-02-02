"""Integration tests for Authentication API endpoints.

Tests the full request/response cycle for /api/v1/auth/ and /api/v1/users/ endpoints.
"""

import pytest
from httpx import AsyncClient


class TestAuthStatusAPI:
    """Integration tests for /api/v1/auth/status endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_auth_status_disabled(self, async_client: AsyncClient):
        """Verify auth status returns disabled when not configured."""
        response = await async_client.get("/api/v1/auth/status")

        assert response.status_code == 200
        result = response.json()
        assert "auth_enabled" in result
        assert result["auth_enabled"] is False
        assert result["requires_setup"] is True


class TestAuthSetupAPI:
    """Integration tests for /api/v1/auth/setup endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_setup_auth_disabled(self, async_client: AsyncClient):
        """Verify auth can be set up with auth disabled (no password required)."""
        response = await async_client.post(
            "/api/v1/auth/setup",
            json={"auth_enabled": False},
        )

        assert response.status_code == 200
        result = response.json()
        assert result["auth_enabled"] is False
        assert result["admin_created"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_setup_auth_enabled_requires_credentials(self, async_client: AsyncClient):
        """Verify enabling auth requires admin username and password."""
        response = await async_client.post(
            "/api/v1/auth/setup",
            json={"auth_enabled": True},
        )

        assert response.status_code == 400
        assert "Admin username and password are required" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_setup_auth_enabled_with_credentials(self, async_client: AsyncClient):
        """Verify auth can be enabled with admin credentials."""
        response = await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "testadmin",
                "admin_password": "testpassword123",
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["auth_enabled"] is True
        assert result["admin_created"] is True


class TestAuthLoginAPI:
    """Integration tests for /api/v1/auth/login endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_login_auth_disabled(self, async_client: AsyncClient):
        """Verify login fails when auth is not enabled."""
        response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "password"},
        )

        assert response.status_code == 400
        assert "Authentication is not enabled" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_login_success(self, async_client: AsyncClient):
        """Verify login succeeds with valid credentials after setup."""
        # First enable auth
        await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "logintest",
                "admin_password": "loginpassword123",
            },
        )

        # Now login
        response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "logintest", "password": "loginpassword123"},
        )

        assert response.status_code == 200
        result = response.json()
        assert "access_token" in result
        assert result["token_type"] == "bearer"
        assert result["user"]["username"] == "logintest"
        assert result["user"]["role"] == "admin"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_login_invalid_credentials(self, async_client: AsyncClient):
        """Verify login fails with invalid credentials."""
        # First enable auth
        await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "invalidtest",
                "admin_password": "correctpassword",
            },
        )

        # Try login with wrong password
        response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "invalidtest", "password": "wrongpassword"},
        )

        assert response.status_code == 401
        assert "Incorrect username or password" in response.json()["detail"]


class TestAuthMeAPI:
    """Integration tests for /api/v1/auth/me endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_me_without_token(self, async_client: AsyncClient):
        """Verify /me fails without authentication token."""
        response = await async_client.get("/api/v1/auth/me")

        assert response.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_me_with_valid_token(self, async_client: AsyncClient):
        """Verify /me returns user info with valid token."""
        # Setup and login
        await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "metest",
                "admin_password": "mepassword123",
            },
        )

        login_response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "metest", "password": "mepassword123"},
        )
        token = login_response.json()["access_token"]

        # Get current user
        response = await async_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        result = response.json()
        assert result["username"] == "metest"
        assert result["role"] == "admin"
        assert result["is_active"] is True


class TestUsersAPI:
    """Integration tests for /api/v1/users/ endpoints."""

    @pytest.fixture
    async def auth_token(self, async_client: AsyncClient):
        """Setup auth and return admin token."""
        await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "usersadmin",
                "admin_password": "adminpassword123",
            },
        )

        login_response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "usersadmin", "password": "adminpassword123"},
        )
        return login_response.json()["access_token"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_users_requires_auth(self, async_client: AsyncClient):
        """Verify listing users requires authentication when auth is enabled."""
        # First enable auth
        await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "authreqadmin",
                "admin_password": "adminpassword123",
            },
        )

        # Now try to list users without a token
        response = await async_client.get("/api/v1/users/")

        assert response.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_users_as_admin(self, async_client: AsyncClient, auth_token: str):
        """Verify admin can list users."""
        response = await async_client.get(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200
        result = response.json()
        assert isinstance(result, list)
        assert len(result) >= 1  # At least the admin user

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_user(self, async_client: AsyncClient, auth_token: str):
        """Verify admin can create a new user."""
        response = await async_client.post(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {auth_token}"},
            json={
                "username": "newuser",
                "password": "newuserpassword",
                "role": "user",
            },
        )

        assert response.status_code == 201
        result = response.json()
        assert result["username"] == "newuser"
        assert result["role"] == "user"
        assert result["is_active"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_user_duplicate_username(self, async_client: AsyncClient, auth_token: str):
        """Verify creating user with duplicate username fails."""
        # Create first user
        await async_client.post(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {auth_token}"},
            json={
                "username": "duplicateuser",
                "password": "password123",
                "role": "user",
            },
        )

        # Try to create duplicate
        response = await async_client.post(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {auth_token}"},
            json={
                "username": "duplicateuser",
                "password": "password456",
                "role": "user",
            },
        )

        assert response.status_code == 400
        assert "Username already exists" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_user(self, async_client: AsyncClient, auth_token: str):
        """Verify admin can update a user."""
        # Create user
        create_response = await async_client.post(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {auth_token}"},
            json={
                "username": "updateuser",
                "password": "password123",
                "role": "user",
            },
        )
        user_id = create_response.json()["id"]

        # Update user
        response = await async_client.patch(
            f"/api/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {auth_token}"},
            json={"role": "admin"},
        )

        assert response.status_code == 200
        assert response.json()["role"] == "admin"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_user(self, async_client: AsyncClient, auth_token: str):
        """Verify admin can delete a user."""
        # Create user
        create_response = await async_client.post(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {auth_token}"},
            json={
                "username": "deleteuser",
                "password": "password123",
                "role": "user",
            },
        )
        user_id = create_response.json()["id"]

        # Delete user
        response = await async_client.delete(
            f"/api/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 204


class TestAuthDisableAPI:
    """Integration tests for /api/v1/auth/disable endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_disable_auth(self, async_client: AsyncClient):
        """Verify admin can disable authentication."""
        # Setup auth
        await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "disableadmin",
                "admin_password": "adminpassword123",
            },
        )

        # Login to get token
        login_response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "disableadmin", "password": "adminpassword123"},
        )
        token = login_response.json()["access_token"]

        # Disable auth
        response = await async_client.post(
            "/api/v1/auth/disable",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert response.json()["auth_enabled"] is False

        # Verify auth is now disabled
        status_response = await async_client.get("/api/v1/auth/status")
        assert status_response.json()["auth_enabled"] is False


class TestGroupsAPI:
    """Integration tests for /api/v1/groups/ endpoints."""

    @pytest.fixture
    async def auth_token(self, async_client: AsyncClient):
        """Setup auth and return admin token."""
        await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "groupsadmin",
                "admin_password": "adminpassword123",
            },
        )

        login_response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "groupsadmin", "password": "adminpassword123"},
        )
        return login_response.json()["access_token"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_groups(self, async_client: AsyncClient, auth_token: str):
        """Verify listing groups returns default groups."""
        response = await async_client.get(
            "/api/v1/groups/",
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200
        groups = response.json()
        assert isinstance(groups, list)
        # Should have default groups: Administrators, Operators, Viewers
        group_names = [g["name"] for g in groups]
        assert "Administrators" in group_names
        assert "Operators" in group_names
        assert "Viewers" in group_names

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_permissions(self, async_client: AsyncClient, auth_token: str):
        """Verify getting available permissions."""
        response = await async_client.get(
            "/api/v1/groups/permissions",
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200
        permissions = response.json()
        assert isinstance(permissions, dict)
        # Should have permission categories
        assert "Printers" in permissions or len(permissions) > 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_group(self, async_client: AsyncClient, auth_token: str):
        """Verify creating a new group."""
        response = await async_client.post(
            "/api/v1/groups/",
            headers={"Authorization": f"Bearer {auth_token}"},
            json={
                "name": "Custom Group",
                "description": "A custom test group",
                "permissions": ["printers:read", "archives:read"],
            },
        )

        assert response.status_code == 201
        group = response.json()
        assert group["name"] == "Custom Group"
        assert group["description"] == "A custom test group"
        assert "printers:read" in group["permissions"]
        assert group["is_system"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_group(self, async_client: AsyncClient, auth_token: str):
        """Verify updating a group."""
        # Create a group first
        create_response = await async_client.post(
            "/api/v1/groups/",
            headers={"Authorization": f"Bearer {auth_token}"},
            json={
                "name": "Update Test Group",
                "permissions": ["printers:read"],
            },
        )
        group_id = create_response.json()["id"]

        # Update the group
        response = await async_client.patch(
            f"/api/v1/groups/{group_id}",
            headers={"Authorization": f"Bearer {auth_token}"},
            json={
                "description": "Updated description",
                "permissions": ["printers:read", "printers:control"],
            },
        )

        assert response.status_code == 200
        group = response.json()
        assert group["description"] == "Updated description"
        assert "printers:control" in group["permissions"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cannot_delete_system_group(self, async_client: AsyncClient, auth_token: str):
        """Verify system groups cannot be deleted."""
        # Get the Administrators group
        list_response = await async_client.get(
            "/api/v1/groups/",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        admin_group = next(g for g in list_response.json() if g["name"] == "Administrators")

        # Try to delete it
        response = await async_client.delete(
            f"/api/v1/groups/{admin_group['id']}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 400
        assert "system group" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_custom_group(self, async_client: AsyncClient, auth_token: str):
        """Verify custom groups can be deleted."""
        # Create a group
        create_response = await async_client.post(
            "/api/v1/groups/",
            headers={"Authorization": f"Bearer {auth_token}"},
            json={"name": "Delete Test Group"},
        )
        group_id = create_response.json()["id"]

        # Delete it
        response = await async_client.delete(
            f"/api/v1/groups/{group_id}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 204


class TestUserGroupsAPI:
    """Integration tests for user-group assignments."""

    @pytest.fixture
    async def auth_token(self, async_client: AsyncClient):
        """Setup auth and return admin token."""
        await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "usergroupadmin",
                "admin_password": "adminpassword123",
            },
        )

        login_response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "usergroupadmin", "password": "adminpassword123"},
        )
        return login_response.json()["access_token"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_user_with_groups(self, async_client: AsyncClient, auth_token: str):
        """Verify creating a user with group assignments."""
        # Get Operators group ID
        groups_response = await async_client.get(
            "/api/v1/groups/",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        operators_group = next(g for g in groups_response.json() if g["name"] == "Operators")

        # Create user with group
        response = await async_client.post(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {auth_token}"},
            json={
                "username": "groupuser",
                "password": "password123",
                "group_ids": [operators_group["id"]],
            },
        )

        assert response.status_code == 201
        user = response.json()
        assert any(g["name"] == "Operators" for g in user["groups"])

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_user_to_group(self, async_client: AsyncClient, auth_token: str):
        """Verify adding a user to a group."""
        # Create a user
        user_response = await async_client.post(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {auth_token}"},
            json={"username": "addtogroup", "password": "password123"},
        )
        user_id = user_response.json()["id"]

        # Get Viewers group
        groups_response = await async_client.get(
            "/api/v1/groups/",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        viewers_group = next(g for g in groups_response.json() if g["name"] == "Viewers")

        # Add user to group
        response = await async_client.post(
            f"/api/v1/groups/{viewers_group['id']}/users/{user_id}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 204

        # Verify user is in group
        user_check = await async_client.get(
            f"/api/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert any(g["name"] == "Viewers" for g in user_check.json()["groups"])


class TestChangePasswordAPI:
    """Integration tests for /api/v1/users/me/change-password endpoint."""

    @pytest.fixture
    async def user_token(self, async_client: AsyncClient):
        """Setup auth and return regular user token."""
        # Enable auth with admin
        await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "pwchangeadmin",
                "admin_password": "adminpassword123",
            },
        )

        admin_login = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "pwchangeadmin", "password": "adminpassword123"},
        )
        admin_token = admin_login.json()["access_token"]

        # Create a regular user
        await async_client.post(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"username": "pwchangeuser", "password": "oldpassword123"},
        )

        # Login as regular user
        user_login = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "pwchangeuser", "password": "oldpassword123"},
        )
        return user_login.json()["access_token"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_change_password_success(self, async_client: AsyncClient, user_token: str):
        """Verify user can change their own password."""
        response = await async_client.post(
            "/api/v1/users/me/change-password",
            headers={"Authorization": f"Bearer {user_token}"},
            json={
                "current_password": "oldpassword123",
                "new_password": "newpassword456",
            },
        )

        assert response.status_code == 200
        assert "success" in response.json()["message"].lower()

        # Verify can login with new password
        login_response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "pwchangeuser", "password": "newpassword456"},
        )
        assert login_response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_change_password_wrong_current(self, async_client: AsyncClient, user_token: str):
        """Verify changing password fails with wrong current password."""
        response = await async_client.post(
            "/api/v1/users/me/change-password",
            headers={"Authorization": f"Bearer {user_token}"},
            json={
                "current_password": "wrongpassword",
                "new_password": "newpassword456",
            },
        )

        assert response.status_code == 400
        assert "incorrect" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_change_password_requires_auth(self, async_client: AsyncClient):
        """Verify changing password requires authentication."""
        response = await async_client.post(
            "/api/v1/users/me/change-password",
            json={
                "current_password": "oldpassword",
                "new_password": "newpassword",
            },
        )

        assert response.status_code == 401


class TestAuthMiddlewarePublicRoutes:
    """Tests for auth middleware public route configuration.

    These routes must be accessible without authentication, even when auth is enabled,
    because browser elements like <img src> and <video src> don't send Authorization headers.
    """

    @pytest.fixture
    async def enabled_auth(self, async_client: AsyncClient):
        """Enable auth for testing middleware behavior."""
        await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "middlewareadmin",
                "admin_password": "adminpassword123",
            },
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_auth_status_is_public(self, async_client: AsyncClient, enabled_auth):
        """Verify /api/v1/auth/status is accessible without auth."""
        response = await async_client.get("/api/v1/auth/status")
        assert response.status_code == 200
        assert "auth_enabled" in response.json()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_auth_login_is_public(self, async_client: AsyncClient, enabled_auth):
        """Verify /api/v1/auth/login is accessible without auth."""
        response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "middlewareadmin", "password": "adminpassword123"},
        )
        # Should not return 401 (unauthorized) - it should either succeed or return
        # a different error (like 400 for wrong credentials)
        assert response.status_code != 401 or "token" in response.json()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_auth_setup_is_public(self, async_client: AsyncClient):
        """Verify /api/v1/auth/setup is accessible without auth (needed for setup/recovery)."""
        # Don't enable auth first - test that setup endpoint itself is accessible
        response = await async_client.post(
            "/api/v1/auth/setup",
            json={"auth_enabled": False},
        )
        # Should not be 401
        assert response.status_code != 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_updates_version_is_public(self, async_client: AsyncClient, enabled_auth):
        """Verify /api/v1/updates/version is accessible without auth."""
        response = await async_client.get("/api/v1/updates/version")
        # Should not be 401
        assert response.status_code != 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_protected_route_requires_auth(self, async_client: AsyncClient, enabled_auth):
        """Verify non-public routes return 401 without token."""
        response = await async_client.get("/api/v1/printers/")
        assert response.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_protected_route_works_with_token(self, async_client: AsyncClient, enabled_auth):
        """Verify non-public routes work with valid token."""
        # Login to get token
        login_response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "middlewareadmin", "password": "adminpassword123"},
        )
        token = login_response.json()["access_token"]

        # Access protected route
        response = await async_client.get(
            "/api/v1/printers/",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
