"""Tests for the stats:filter_by_user permission and user filter helpers."""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from backend.app.core.permissions import ALL_PERMISSIONS, DEFAULT_GROUPS, PERMISSION_CATEGORIES, Permission


class TestStatsFilterByUserPermission:
    """Test the stats:filter_by_user permission is properly defined."""

    def test_permission_enum_exists(self):
        """The STATS_FILTER_BY_USER permission should exist in the enum."""
        assert hasattr(Permission, "STATS_FILTER_BY_USER")
        assert Permission.STATS_FILTER_BY_USER.value == "stats:filter_by_user"

    def test_permission_in_all_permissions(self):
        """The permission should be in ALL_PERMISSIONS list."""
        assert "stats:filter_by_user" in ALL_PERMISSIONS

    def test_permission_in_administrators_group(self):
        """Administrators should have the permission (via ALL_PERMISSIONS)."""
        admin_perms = DEFAULT_GROUPS["Administrators"]["permissions"]
        assert "stats:filter_by_user" in admin_perms

    def test_permission_not_in_operators_group(self):
        """Operators should NOT have the permission."""
        operator_perms = DEFAULT_GROUPS["Operators"]["permissions"]
        assert "stats:filter_by_user" not in operator_perms

    def test_permission_not_in_viewers_group(self):
        """Viewers should NOT have the permission."""
        viewer_perms = DEFAULT_GROUPS["Viewers"]["permissions"]
        assert "stats:filter_by_user" not in viewer_perms

    def test_permission_in_stats_category(self):
        """The permission should be in the Stats & History category."""
        stats_category = PERMISSION_CATEGORIES["Stats & History"]
        assert Permission.STATS_FILTER_BY_USER in stats_category


class TestValidateUserFilterPermission:
    """Test the _validate_user_filter_permission helper."""

    @pytest.fixture
    def validate(self):
        from backend.app.api.routes.archives import _validate_user_filter_permission

        return _validate_user_filter_permission

    def test_no_filter_no_check(self, validate):
        """When created_by_id is None, no permission check is done."""
        validate(None, None)  # Should not raise

    def test_no_user_no_check(self, validate):
        """When current_user is None (auth disabled), no permission check is done."""
        validate(None, 5)  # Should not raise

    def test_admin_always_allowed(self, validate):
        """Admin users should always be allowed to filter."""
        user = MagicMock()
        user.is_admin = True
        validate(user, 5)  # Should not raise

    def test_user_with_permission_allowed(self, validate):
        """Users with stats:filter_by_user permission should be allowed."""
        user = MagicMock()
        user.is_admin = False
        user.has_permission.return_value = True
        validate(user, 5)  # Should not raise
        user.has_permission.assert_called_once_with("stats:filter_by_user")

    def test_user_without_permission_denied(self, validate):
        """Users without the permission should get 403."""
        user = MagicMock()
        user.is_admin = False
        user.has_permission.return_value = False
        with pytest.raises(HTTPException) as exc_info:
            validate(user, 5)
        assert exc_info.value.status_code == 403

    def test_sentinel_minus_one_also_checked(self, validate):
        """The sentinel value -1 (no user) also requires permission."""
        user = MagicMock()
        user.is_admin = False
        user.has_permission.return_value = False
        with pytest.raises(HTTPException):
            validate(user, -1)
