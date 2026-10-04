"""Group model for permission-based access control."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Table, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from backend.app.core.database import Base

if TYPE_CHECKING:
    from backend.app.models.user import User


# Many-to-many association table between users and groups
user_groups = Table(
    "user_groups",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("group_id", Integer, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
)

# Printers a group with ``restrict_printers`` set is allowed to see and control
# (#1727). Rows are only meaningful while the flag is on; turning it off keeps
# them so the selection survives toggling. SQLite doesn't enforce the FK
# cascades, so printer and group deletes remove their rows explicitly.
group_printers = Table(
    "group_printers",
    Base.metadata,
    Column("group_id", Integer, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
    Column("printer_id", Integer, ForeignKey("printers.id", ondelete="CASCADE"), primary_key=True),
)

# Locations a restricted group may use (#1727): every printer whose
# ``location`` matches, now or later, so a printer added to "Lab A" reaches
# the Lab A team without anyone ticking it. Matched exactly against
# ``printers.location``; a name no printer carries any more simply grants
# nothing. Like ``group_printers``, kept while the flag is off.
group_locations = Table(
    "group_locations",
    Base.metadata,
    Column("group_id", Integer, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
    Column("location", String(100), primary_key=True),
)


class Group(Base):
    """Group model for organizing users and assigning permissions.

    Groups contain a list of permissions that are granted to all members.
    Users can belong to multiple groups, and their permissions are additive.
    System groups (Administrators, Operators, Viewers) cannot be deleted.

    Permissions say *what* a member may do; ``restrict_printers`` says *where*.
    A group without the flag doesn't narrow printer access at all, so it can be
    combined with a team group that does. See ``core/printer_scope.py``.
    """

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    restrict_printers: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationship to users through association table
    users: Mapped[list[User]] = relationship(
        "User",
        secondary=user_groups,
        back_populates="groups",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Group {self.name}>"
