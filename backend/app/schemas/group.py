"""Pydantic schemas for Group CRUD operations."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

from backend.app.schemas.not_null import IgnoresNullForRequiredColumns


class GroupBrief(BaseModel):
    """Brief group info for embedding in other responses."""

    id: int
    name: str

    class Config:
        from_attributes = True


class FilamentRuleSchema(BaseModel):
    """One filament rule of a group (#3522): a material type or a slicer filament id."""

    match: Literal["material", "filament_id"]
    # Stripped before the length check, so a blank value is a 422 rather than
    # a rule `clean_rules` would silently drop.
    value: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class GroupCreate(BaseModel):
    """Schema for creating a new group."""

    name: str
    description: str | None = None
    permissions: list[str] = []
    # Printer scope (#1727): when set, members only see these printers plus
    # every printer in these locations
    restrict_printers: bool = False
    printer_ids: list[int] = []
    locations: list[str] = []
    # Filament scope (#3522): when set, members may only print with the
    # filaments these rules allow. In "block" mode that is every filament no
    # rule names; in "allow" mode, only those a rule names.
    restrict_filaments: bool = False
    filament_rule_mode: Literal["allow", "block"] = "block"
    filament_rules: list[FilamentRuleSchema] = []


class GroupUpdate(IgnoresNullForRequiredColumns):
    """Schema for updating a group."""

    __orm_table__ = "groups"

    name: str | None = None
    description: str | None = None
    permissions: list[str] | None = None
    restrict_printers: bool | None = None
    printer_ids: list[int] | None = None
    locations: list[str] | None = None
    restrict_filaments: bool | None = None
    filament_rule_mode: Literal["allow", "block"] | None = None
    filament_rules: list[FilamentRuleSchema] | None = None


class GroupResponse(BaseModel):
    """Schema for group response."""

    id: int
    name: str
    description: str | None
    permissions: list[str]
    is_system: bool
    restrict_printers: bool = False
    printer_ids: list[int] = []
    locations: list[str] = []
    restrict_filaments: bool = False
    filament_rule_mode: Literal["allow", "block"] = "block"
    filament_rules: list[FilamentRuleSchema] = []
    user_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class GroupDetailResponse(GroupResponse):
    """Schema for detailed group response including users."""

    users: list["UserBrief"] = []


class UserBrief(BaseModel):
    """Brief user info for embedding in group response."""

    id: int
    username: str
    is_active: bool

    class Config:
        from_attributes = True


class PermissionInfo(BaseModel):
    """Schema for permission information."""

    value: str
    label: str


class PermissionCategory(BaseModel):
    """Schema for a category of permissions."""

    name: str
    permissions: list[PermissionInfo]


class PermissionsListResponse(BaseModel):
    """Schema for listing all permissions by category."""

    categories: list[PermissionCategory]
    all_permissions: list[str]


# Update forward references
GroupDetailResponse.model_rebuild()
