from pydantic import BaseModel


class GroupBrief(BaseModel):
    """Brief group info for embedding in user responses."""

    id: int
    name: str

    class Config:
        from_attributes = True


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserResponse"


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "user"
    group_ids: list[int] | None = None


class UserUpdate(BaseModel):
    username: str | None = None
    password: str | None = None
    role: str | None = None
    is_active: bool | None = None
    group_ids: list[int] | None = None


class UserResponse(BaseModel):
    id: int
    username: str
    role: str  # Deprecated, kept for backward compatibility
    is_active: bool
    is_admin: bool  # Computed from role and group membership
    groups: list[GroupBrief] = []
    permissions: list[str] = []  # All permissions from groups
    created_at: str

    class Config:
        from_attributes = True


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class SetupRequest(BaseModel):
    auth_enabled: bool
    admin_username: str | None = None
    admin_password: str | None = None


class SetupResponse(BaseModel):
    auth_enabled: bool
    admin_created: bool | None = None
