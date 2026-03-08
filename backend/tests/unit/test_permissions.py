"""Tests for the permission system definitions and consistency."""

from backend.app.core.permissions import (
    ALL_PERMISSIONS,
    DEFAULT_GROUPS,
    PERMISSION_CATEGORIES,
    Permission,
)


class TestPermissionEnum:
    """Test the Permission enum values."""

    def test_clear_plate_permission_exists(self):
        """printers:clear_plate permission should exist in the enum."""
        assert hasattr(Permission, "PRINTERS_CLEAR_PLATE")
        assert Permission.PRINTERS_CLEAR_PLATE == "printers:clear_plate"

    def test_clear_plate_in_all_permissions(self):
        """printers:clear_plate should be in ALL_PERMISSIONS list."""
        assert "printers:clear_plate" in ALL_PERMISSIONS

    def test_clear_plate_in_printers_category(self):
        """printers:clear_plate should be in the Printers permission category."""
        printers_perms = PERMISSION_CATEGORIES["Printers"]
        assert Permission.PRINTERS_CLEAR_PLATE in printers_perms

    def test_clear_plate_separate_from_control(self):
        """clear_plate and control should be distinct permissions."""
        assert Permission.PRINTERS_CLEAR_PLATE != Permission.PRINTERS_CONTROL
        assert Permission.PRINTERS_CLEAR_PLATE.value != Permission.PRINTERS_CONTROL.value


class TestDefaultGroups:
    """Test the default group definitions."""

    def test_operators_have_clear_plate(self):
        """Operators group should include printers:clear_plate."""
        operators = DEFAULT_GROUPS["Operators"]
        assert "printers:clear_plate" in operators["permissions"]

    def test_operators_have_control_and_clear_plate(self):
        """Operators group should have both printers:control and printers:clear_plate."""
        operators = DEFAULT_GROUPS["Operators"]
        assert "printers:control" in operators["permissions"]
        assert "printers:clear_plate" in operators["permissions"]

    def test_administrators_have_all_permissions(self):
        """Administrators should have all permissions including clear_plate."""
        admins = DEFAULT_GROUPS["Administrators"]
        assert "printers:clear_plate" in admins["permissions"]

    def test_viewers_do_not_have_clear_plate(self):
        """Viewers group (read-only) should not include printers:clear_plate."""
        viewers = DEFAULT_GROUPS["Viewers"]
        assert "printers:clear_plate" not in viewers["permissions"]


class TestPermissionCategoriesCompleteness:
    """Test that all enum permissions appear in exactly one category."""

    def test_all_permissions_categorized(self):
        """Every Permission enum member should appear in a category."""
        categorized = set()
        for perms in PERMISSION_CATEGORIES.values():
            categorized.update(perms)
        for perm in Permission:
            assert perm in categorized, f"{perm} not in any category"

    def test_no_duplicate_categorization(self):
        """No permission should appear in multiple categories."""
        seen = {}
        for cat_name, perms in PERMISSION_CATEGORIES.items():
            for perm in perms:
                assert perm not in seen, f"{perm} in both '{seen[perm]}' and '{cat_name}'"
                seen[perm] = cat_name


class TestInventoryViewAssignmentsPermission:
    """Test the INVENTORY_VIEW_ASSIGNMENTS permission."""

    def test_view_assignments_permission_exists(self):
        """inventory:view_assignments permission should exist in the enum."""
        assert hasattr(Permission, "INVENTORY_VIEW_ASSIGNMENTS")
        assert Permission.INVENTORY_VIEW_ASSIGNMENTS == "inventory:view_assignments"

    def test_view_assignments_in_all_permissions(self):
        """inventory:view_assignments should be in ALL_PERMISSIONS list."""
        assert "inventory:view_assignments" in ALL_PERMISSIONS

    def test_view_assignments_in_inventory_category(self):
        """inventory:view_assignments should be in the Inventory permission category."""
        inventory_perms = PERMISSION_CATEGORIES["Inventory"]
        assert Permission.INVENTORY_VIEW_ASSIGNMENTS in inventory_perms

    def test_view_assignments_separate_from_read(self):
        """view_assignments and read should be distinct permissions."""
        assert Permission.INVENTORY_VIEW_ASSIGNMENTS != Permission.INVENTORY_READ
        assert Permission.INVENTORY_VIEW_ASSIGNMENTS.value != Permission.INVENTORY_READ.value

    def test_operators_have_view_assignments(self):
        """Operators group should include inventory:view_assignments."""
        operators = DEFAULT_GROUPS["Operators"]
        assert "inventory:view_assignments" in operators["permissions"]

    def test_viewers_have_view_assignments(self):
        """Viewers group should include inventory:view_assignments."""
        viewers = DEFAULT_GROUPS["Viewers"]
        assert "inventory:view_assignments" in viewers["permissions"]

    def test_administrators_have_view_assignments(self):
        """Administrators should have all permissions including view_assignments."""
        admins = DEFAULT_GROUPS["Administrators"]
        assert "inventory:view_assignments" in admins["permissions"]
