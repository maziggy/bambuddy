"""Group management API routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import (
    ALL_PERMISSIONS,
    PERMISSION_CATEGORIES,
    Permission,
)
from backend.app.models.group import Group
from backend.app.models.user import User
from backend.app.schemas.group import (
    GroupCreate,
    GroupDetailResponse,
    GroupResponse,
    GroupUpdate,
    PermissionCategory,
    PermissionInfo,
    PermissionsListResponse,
    UserBrief,
)

router = APIRouter(prefix="/groups", tags=["groups"])


def _permission_label(perm: Permission) -> str:
    """Convert permission enum to human-readable label."""
    # e.g., "printers:read" -> "Read Printers"
    parts = perm.value.split(":")
    if len(parts) == 2:
        resource, action = parts
        resource = resource.replace("_", " ").title()
        action = action.title()
        return f"{action} {resource}"
    return perm.value


@router.get("/permissions", response_model=PermissionsListResponse)
async def list_permissions(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.GROUPS_READ),
):
    """List all available permissions organized by category."""
    categories = []
    for name, perms in PERMISSION_CATEGORIES.items():
        categories.append(
            PermissionCategory(
                name=name,
                permissions=[PermissionInfo(value=p.value, label=_permission_label(p)) for p in perms],
            )
        )
    return PermissionsListResponse(
        categories=categories,
        all_permissions=ALL_PERMISSIONS,
    )


@router.get("", response_model=list[GroupResponse])
@router.get("/", response_model=list[GroupResponse])
async def list_groups(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.GROUPS_READ),
    db: AsyncSession = Depends(get_db),
):
    """List all groups."""
    result = await db.execute(select(Group).options(selectinload(Group.users)).order_by(Group.name))
    groups = result.scalars().all()
    return [
        GroupResponse(
            id=group.id,
            name=group.name,
            description=group.description,
            permissions=group.permissions or [],
            is_system=group.is_system,
            user_count=len(group.users),
            created_at=group.created_at,
            updated_at=group.updated_at,
        )
        for group in groups
    ]


@router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    group_data: GroupCreate,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.GROUPS_CREATE),
    db: AsyncSession = Depends(get_db),
):
    """Create a new group."""
    # Check if group name already exists
    existing = await db.execute(select(Group).where(Group.name == group_data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Group name already exists",
        )

    # Validate permissions
    invalid_perms = [p for p in group_data.permissions if p not in ALL_PERMISSIONS]
    if invalid_perms:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid permissions: {', '.join(invalid_perms)}",
        )

    group = Group(
        name=group_data.name,
        description=group_data.description,
        permissions=group_data.permissions,
        is_system=False,  # User-created groups are not system groups
    )
    db.add(group)
    await db.commit()
    await db.refresh(group)

    return GroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        permissions=group.permissions or [],
        is_system=group.is_system,
        user_count=0,
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


@router.get("/{group_id}", response_model=GroupDetailResponse)
async def get_group(
    group_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.GROUPS_READ),
    db: AsyncSession = Depends(get_db),
):
    """Get a group by ID with user list."""
    result = await db.execute(select(Group).where(Group.id == group_id).options(selectinload(Group.users)))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )

    return GroupDetailResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        permissions=group.permissions or [],
        is_system=group.is_system,
        user_count=len(group.users),
        created_at=group.created_at,
        updated_at=group.updated_at,
        users=[UserBrief(id=u.id, username=u.username, is_active=u.is_active) for u in group.users],
    )


@router.patch("/{group_id}", response_model=GroupResponse)
async def update_group(
    group_id: int,
    group_data: GroupUpdate,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.GROUPS_UPDATE),
    db: AsyncSession = Depends(get_db),
):
    """Update a group."""
    result = await db.execute(select(Group).where(Group.id == group_id).options(selectinload(Group.users)))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )

    # Check if updating name to one that already exists
    if group_data.name is not None and group_data.name != group.name:
        existing = await db.execute(select(Group).where(Group.name == group_data.name, Group.id != group_id))
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Group name already exists",
            )
        # System groups cannot have their name changed
        if group.is_system:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot rename system groups",
            )
        group.name = group_data.name

    if group_data.description is not None:
        group.description = group_data.description

    if group_data.permissions is not None:
        # Validate permissions
        invalid_perms = [p for p in group_data.permissions if p not in ALL_PERMISSIONS]
        if invalid_perms:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid permissions: {', '.join(invalid_perms)}",
            )
        group.permissions = group_data.permissions

    await db.commit()
    await db.refresh(group)

    return GroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        permissions=group.permissions or [],
        is_system=group.is_system,
        user_count=len(group.users),
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.GROUPS_DELETE),
    db: AsyncSession = Depends(get_db),
):
    """Delete a group (non-system groups only)."""
    result = await db.execute(select(Group).where(Group.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )

    if group.is_system:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete system groups",
        )

    await db.delete(group)
    await db.commit()


@router.post("/{group_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def add_user_to_group(
    group_id: int,
    user_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.GROUPS_UPDATE),
    db: AsyncSession = Depends(get_db),
):
    """Add a user to a group."""
    # Get group with users
    result = await db.execute(select(Group).where(Group.id == group_id).options(selectinload(Group.users)))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )

    # Get user
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Check if user is already in group
    if user in group.users:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is already in this group",
        )

    group.users.append(user)
    await db.commit()


@router.delete("/{group_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_user_from_group(
    group_id: int,
    user_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.GROUPS_UPDATE),
    db: AsyncSession = Depends(get_db),
):
    """Remove a user from a group."""
    # Get group with users
    result = await db.execute(select(Group).where(Group.id == group_id).options(selectinload(Group.users)))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )

    # Get user
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Check if user is in group
    if user not in group.users:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not in this group",
        )

    group.users.remove(user)
    await db.commit()
