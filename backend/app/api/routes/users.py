from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.auth import (
    RequirePermissionIfAuthEnabled,
    get_current_user_optional,
    get_password_hash,
    verify_password,
)
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.group import Group
from backend.app.models.user import User
from backend.app.schemas.auth import ChangePasswordRequest, GroupBrief, UserCreate, UserResponse, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


def _user_to_response(user: User) -> UserResponse:
    """Convert a User model to UserResponse schema."""
    return UserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        is_admin=user.is_admin,
        groups=[GroupBrief(id=g.id, name=g.name) for g in user.groups],
        permissions=sorted(user.get_permissions()),
        created_at=user.created_at.isoformat(),
    )


@router.get("", response_model=list[UserResponse])
@router.get("/", response_model=list[UserResponse])
async def list_users(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.USERS_READ),
    db: AsyncSession = Depends(get_db),
):
    """List all users."""
    result = await db.execute(select(User).options(selectinload(User.groups)).order_by(User.created_at))
    users = result.scalars().all()
    return [_user_to_response(user) for user in users]


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreate,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.USERS_CREATE),
    db: AsyncSession = Depends(get_db),
):
    """Create a new user."""
    # Check if username already exists
    existing_user = await db.execute(select(User).where(User.username == user_data.username))
    if existing_user.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists",
        )

    # Validate role
    if user_data.role not in ["admin", "user"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role must be 'admin' or 'user'",
        )

    new_user = User(
        username=user_data.username,
        password_hash=get_password_hash(user_data.password),
        role=user_data.role,
        is_active=True,
    )

    # Handle group assignments
    if user_data.group_ids:
        groups_result = await db.execute(select(Group).where(Group.id.in_(user_data.group_ids)))
        groups = groups_result.scalars().all()
        if len(groups) != len(user_data.group_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or more group IDs are invalid",
            )
        new_user.groups = list(groups)

    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    return _user_to_response(new_user)


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.USERS_READ),
    db: AsyncSession = Depends(get_db),
):
    """Get a user by ID."""
    result = await db.execute(select(User).where(User.id == user_id).options(selectinload(User.groups)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return _user_to_response(user)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.USERS_UPDATE),
    db: AsyncSession = Depends(get_db),
):
    """Update a user."""
    result = await db.execute(select(User).where(User.id == user_id).options(selectinload(User.groups)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Prevent deactivating the last admin
    if user_data.is_active is False and user.is_admin:
        # Count admins by role or Administrators group membership
        admin_count_result = await db.execute(select(User).where(User.role == "admin", User.is_active.is_(True)))
        role_admins = admin_count_result.scalars().all()

        # Also check for users in Administrators group
        admin_group_result = await db.execute(
            select(Group).where(Group.name == "Administrators").options(selectinload(Group.users))
        )
        admin_group = admin_group_result.scalar_one_or_none()
        group_admins = [u for u in (admin_group.users if admin_group else []) if u.is_active]

        # Combine unique admins
        all_admins = {u.id for u in role_admins} | {u.id for u in group_admins}
        if len(all_admins) <= 1 and user.id in all_admins:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot deactivate the last admin user",
            )

    # Prevent changing role of last admin
    if user_data.role and user_data.role != "admin" and user.role == "admin":
        admin_count_result = await db.execute(select(User).where(User.role == "admin", User.is_active.is_(True)))
        admin_count = len(admin_count_result.scalars().all())
        if admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot change role of the last admin user",
            )

    if user_data.username is not None:
        # Check if new username already exists
        existing_user = await db.execute(select(User).where(User.username == user_data.username, User.id != user_id))
        if existing_user.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already exists",
            )
        user.username = user_data.username

    if user_data.password is not None:
        user.password_hash = get_password_hash(user_data.password)

    if user_data.role is not None:
        if user_data.role not in ["admin", "user"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Role must be 'admin' or 'user'",
            )
        user.role = user_data.role

    if user_data.is_active is not None:
        user.is_active = user_data.is_active

    # Handle group assignments
    if user_data.group_ids is not None:
        groups_result = await db.execute(select(Group).where(Group.id.in_(user_data.group_ids)))
        groups = groups_result.scalars().all()
        if len(groups) != len(user_data.group_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or more group IDs are invalid",
            )
        user.groups = list(groups)

    await db.commit()
    await db.refresh(user)

    return _user_to_response(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.USERS_DELETE),
    db: AsyncSession = Depends(get_db),
):
    """Delete a user."""
    result = await db.execute(select(User).where(User.id == user_id).options(selectinload(User.groups)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Prevent deleting the last admin
    if user.is_admin:
        # Count admins by role or Administrators group membership
        admin_count_result = await db.execute(select(User).where(User.role == "admin", User.id != user_id))
        other_role_admins = admin_count_result.scalars().all()

        # Also check for users in Administrators group
        admin_group_result = await db.execute(
            select(Group).where(Group.name == "Administrators").options(selectinload(Group.users))
        )
        admin_group = admin_group_result.scalar_one_or_none()
        other_group_admins = [u for u in (admin_group.users if admin_group else []) if u.id != user_id and u.is_active]

        # Combine unique admins
        all_other_admins = {u.id for u in other_role_admins} | {u.id for u in other_group_admins}
        if len(all_other_admins) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete the last admin user",
            )

    # Prevent deleting yourself (only if auth is enabled and we have a current user)
    if current_user and user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    await db.delete(user)
    await db.commit()


@router.post("/me/change-password", response_model=dict)
async def change_own_password(
    password_data: ChangePasswordRequest,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Change the current user's password. Requires current password verification."""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to change password",
        )

    # Verify current password
    if not verify_password(password_data.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    # Validate new password
    if len(password_data.new_password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be at least 6 characters",
        )

    # Fetch user from this session to ensure changes are persisted
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Update password
    user.password_hash = get_password_hash(password_data.new_password)
    await db.commit()

    return {"message": "Password changed successfully"}
