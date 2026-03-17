"""Tests for user email notification preferences and permissions."""

from backend.app.core.permissions import (
    ALL_PERMISSIONS,
    DEFAULT_GROUPS,
    PERMISSION_CATEGORIES,
    Permission,
)
from backend.app.schemas.user_notifications import (
    UserEmailPreferenceResponse,
    UserEmailPreferenceUpdate,
)


class TestNotificationsUserEmailPermission:
    """Test the NOTIFICATIONS_USER_EMAIL permission integration."""

    def test_permission_exists(self):
        """notifications:user_email permission should exist in the enum."""
        assert hasattr(Permission, "NOTIFICATIONS_USER_EMAIL")
        assert Permission.NOTIFICATIONS_USER_EMAIL == "notifications:user_email"

    def test_permission_in_all_permissions(self):
        """notifications:user_email should be in ALL_PERMISSIONS list."""
        assert "notifications:user_email" in ALL_PERMISSIONS

    def test_permission_in_notifications_category(self):
        """notifications:user_email should be in the Notifications permission category."""
        notifications_perms = PERMISSION_CATEGORIES["Notifications"]
        assert Permission.NOTIFICATIONS_USER_EMAIL in notifications_perms

    def test_administrators_have_permission(self):
        """Administrators should have notifications:user_email via ALL_PERMISSIONS."""
        admins = DEFAULT_GROUPS["Administrators"]
        assert "notifications:user_email" in admins["permissions"]

    def test_operators_have_permission(self):
        """Operators should have notifications:user_email for managing their own preferences."""
        operators = DEFAULT_GROUPS["Operators"]
        assert "notifications:user_email" in operators["permissions"]

    def test_viewers_do_not_have_permission(self):
        """Viewers (read-only) should not have notifications:user_email."""
        viewers = DEFAULT_GROUPS["Viewers"]
        assert "notifications:user_email" not in viewers["permissions"]

    def test_permission_separate_from_notifications_read(self):
        """user_email and read should be distinct permissions."""
        assert Permission.NOTIFICATIONS_USER_EMAIL != Permission.NOTIFICATIONS_READ
        assert Permission.NOTIFICATIONS_USER_EMAIL.value != Permission.NOTIFICATIONS_READ.value


class TestUserEmailPreferenceSchemas:
    """Test the user email preference Pydantic schemas."""

    def test_response_schema_defaults(self):
        """Response schema should accept all four boolean fields."""
        resp = UserEmailPreferenceResponse(
            notify_print_start=True,
            notify_print_complete=True,
            notify_print_failed=True,
            notify_print_stopped=True,
        )
        assert resp.notify_print_start is True
        assert resp.notify_print_complete is True
        assert resp.notify_print_failed is True
        assert resp.notify_print_stopped is True

    def test_response_schema_all_disabled(self):
        """Response schema should handle all-disabled preferences."""
        resp = UserEmailPreferenceResponse(
            notify_print_start=False,
            notify_print_complete=False,
            notify_print_failed=False,
            notify_print_stopped=False,
        )
        assert resp.notify_print_start is False
        assert resp.notify_print_complete is False
        assert resp.notify_print_failed is False
        assert resp.notify_print_stopped is False

    def test_update_schema_accepts_mixed(self):
        """Update schema should accept a mix of enabled/disabled."""
        update = UserEmailPreferenceUpdate(
            notify_print_start=True,
            notify_print_complete=False,
            notify_print_failed=True,
            notify_print_stopped=False,
        )
        assert update.notify_print_start is True
        assert update.notify_print_complete is False
        assert update.notify_print_failed is True
        assert update.notify_print_stopped is False

    def test_response_schema_from_attributes(self):
        """Response schema should support from_attributes (ORM mode)."""
        assert UserEmailPreferenceResponse.model_config.get("from_attributes") is True


class TestNotificationTemplateTypes:
    """Test that user print notification template types are registered."""

    def test_user_print_template_types_exist(self):
        """All four user print email template types should be in EVENT_NAMES."""
        from backend.app.api.routes.notification_templates import EVENT_NAMES

        expected_types = [
            "user_print_start",
            "user_print_complete",
            "user_print_failed",
            "user_print_stopped",
        ]
        for event_type in expected_types:
            assert event_type in EVENT_NAMES, f"{event_type} not in EVENT_NAMES"

    def test_user_print_template_display_names(self):
        """User print template display names should be descriptive."""
        from backend.app.api.routes.notification_templates import EVENT_NAMES

        assert EVENT_NAMES["user_print_start"] == "User Print Started Email"
        assert EVENT_NAMES["user_print_complete"] == "User Print Completed Email"
        assert EVENT_NAMES["user_print_failed"] == "User Print Failed Email"
        assert EVENT_NAMES["user_print_stopped"] == "User Print Stopped Email"
