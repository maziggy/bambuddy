from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base

if TYPE_CHECKING:
    from backend.app.models.group import Group


class User(Base):
    """User model for authentication and authorization.

    Users can belong to multiple groups, and their permissions are additive
    across all groups. The legacy 'role' field is kept for backward compatibility
    but is_admin property now also considers group membership.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(
        String(20), default="user"
    )  # "admin" or "user" (legacy, kept for backward compat)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationship to groups through association table
    groups: Mapped[list[Group]] = relationship(
        "Group",
        secondary="user_groups",
        back_populates="users",
        lazy="selectin",
    )

    @property
    def is_admin(self) -> bool:
        """Check if user is an admin.

        Returns True if:
        - User has legacy role='admin', OR
        - User belongs to the Administrators group
        """
        if self.role == "admin":
            return True
        return any(g.name == "Administrators" for g in self.groups)

    def get_permissions(self) -> set[str]:
        """Get all permissions from all groups the user belongs to.

        Returns a set of permission strings. Permissions are additive across groups.
        """
        permissions: set[str] = set()
        for group in self.groups:
            if group.permissions:
                permissions.update(group.permissions)
        return permissions

    def has_permission(self, permission: str) -> bool:
        """Check if user has a specific permission.

        Admins have all permissions. For other users, checks if the permission
        exists in any of their groups.
        """
        if self.is_admin:
            return True
        return permission in self.get_permissions()

    def has_all_permissions(self, *permissions: str) -> bool:
        """Check if user has ALL specified permissions.

        Admins have all permissions. For other users, checks if all permissions
        exist in their combined group permissions.
        """
        if self.is_admin:
            return True
        user_permissions = self.get_permissions()
        return all(p in user_permissions for p in permissions)

    def has_any_permission(self, *permissions: str) -> bool:
        """Check if user has ANY of the specified permissions.

        Admins have all permissions. For other users, checks if at least one
        permission exists in their combined group permissions.
        """
        if self.is_admin:
            return True
        user_permissions = self.get_permissions()
        return any(p in user_permissions for p in permissions)

    def __repr__(self) -> str:
        return f"<User {self.username}>"
