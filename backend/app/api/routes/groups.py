"""Group management API routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.auth import RequireAdminIfAuthEnabled, RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import (
    ALL_PERMISSIONS,
    PERMISSION_CATEGORIES,
    Permission,
)
from backend.app.core.printer_scope import group_location_names, group_printer_ids
from backend.app.core.websocket import ws_manager
from backend.app.models.group import Group, group_locations, group_printers
from backend.app.models.printer import Printer
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


# Permissions whose derived label would misdescribe what is being granted.
# The derived form for USERS_READ_SLIM is "Read Slim Users", which reads as a
# property of the users rather than of the response -- and an admin ticking a
# box in the group editor has nothing else to go on (#1894).
_PERMISSION_LABEL_OVERRIDES: dict[Permission, str] = {
    Permission.USERS_READ_SLIM: "List User Names (id + username only)",
    # "Start Unreviewed Queue" says nothing about what happens without it (#1620)
    Permission.QUEUE_START_UNREVIEWED: "Print Without Review (else jobs wait for someone to start them)",
}


def _permission_label(perm: Permission) -> str:
    """Convert permission enum to human-readable label."""
    if perm in _PERMISSION_LABEL_OVERRIDES:
        return _PERMISSION_LABEL_OVERRIDES[perm]
    # e.g., "printers:read" -> "Read Printers"
    parts = perm.value.split(":")
    if len(parts) == 2:
        resource, action = parts
        resource = resource.replace("_", " ").title()
        action = action.replace("_", " ").title()
        return f"{action} {resource}"
    return perm.value


async def _printer_ids_by_group(db: AsyncSession) -> dict[int, list[int]]:
    result = await db.execute(select(group_printers.c.group_id, group_printers.c.printer_id))
    by_group: dict[int, list[int]] = {}
    for group_id, printer_id in result.all():
        by_group.setdefault(group_id, []).append(printer_id)
    return {gid: sorted(pids) for gid, pids in by_group.items()}


async def _locations_by_group(db: AsyncSession) -> dict[int, list[str]]:
    result = await db.execute(select(group_locations.c.group_id, group_locations.c.location))
    by_group: dict[int, list[str]] = {}
    for group_id, location in result.all():
        by_group.setdefault(group_id, []).append(location)
    return {gid: sorted(locs) for gid, locs in by_group.items()}


def _clean_locations(locations: list[str]) -> set[str]:
    """Trimmed, non-empty location names; refuses ones too long to ever match a printer."""
    cleaned = {loc.strip() for loc in locations if loc and loc.strip()}
    if any(len(loc) > 100 for loc in cleaned):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Location names are at most 100 characters",
        )
    return cleaned


async def _apply_printer_scope(
    db: AsyncSession,
    group: Group,
    restrict_printers: bool | None,
    printer_ids: list[int] | None,
    locations: list[str] | None = None,
) -> bool:
    """Validate and store a group's printer scope (#1727). Returns whether it changed.

    The Administrators group can't be restricted: admins see every printer
    regardless, so the setting would only mislead.
    """
    changed = False
    if restrict_printers is not None and restrict_printers != bool(group.restrict_printers):
        if restrict_printers and group.name == "Administrators":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Administrators always see every printer",
            )
        group.restrict_printers = restrict_printers
        changed = True
    if printer_ids is not None:
        wanted = set(printer_ids)
        if wanted:
            found = set((await db.execute(select(Printer.id).where(Printer.id.in_(wanted)))).scalars().all())
            missing = sorted(wanted - found)
            if missing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid printers: {', '.join(str(pid) for pid in missing)}",
                )
        current = set(await group_printer_ids(db, group.id)) if group.id is not None else set()
        if wanted != current:
            if group.id is None:
                await db.flush()
            await db.execute(delete(group_printers).where(group_printers.c.group_id == group.id))
            if wanted:
                await db.execute(
                    insert(group_printers), [{"group_id": group.id, "printer_id": pid} for pid in sorted(wanted)]
                )
            changed = True
    if locations is not None:
        # Not checked against existing printers: a location can be granted
        # before its first printer is added there.
        wanted_locations = _clean_locations(locations)
        current_locations = set(await group_location_names(db, group.id)) if group.id is not None else set()
        if wanted_locations != current_locations:
            if group.id is None:
                await db.flush()
            await db.execute(delete(group_locations).where(group_locations.c.group_id == group.id))
            if wanted_locations:
                await db.execute(
                    insert(group_locations),
                    [{"group_id": group.id, "location": loc} for loc in sorted(wanted_locations)],
                )
            changed = True
    return changed


def _group_response(group: Group, printer_ids: list[int], locations: list[str], user_count: int) -> GroupResponse:
    return GroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        permissions=group.permissions or [],
        is_system=group.is_system,
        restrict_printers=bool(group.restrict_printers),
        printer_ids=printer_ids,
        locations=locations,
        user_count=user_count,
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


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
    printers_by_group = await _printer_ids_by_group(db)
    locations_by_group = await _locations_by_group(db)
    return [
        _group_response(
            group, printers_by_group.get(group.id, []), locations_by_group.get(group.id, []), len(group.users)
        )
        for group in groups
    ]


@router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    group_data: GroupCreate,
    _admin: User | None = RequireAdminIfAuthEnabled(),
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
        restrict_printers=False,
    )
    db.add(group)
    await db.flush()
    await _apply_printer_scope(db, group, group_data.restrict_printers, group_data.printer_ids, group_data.locations)
    await db.commit()
    await db.refresh(group)

    return _group_response(group, await group_printer_ids(db, group.id), await group_location_names(db, group.id), 0)


@router.get("/{group_id}", response_model=GroupDetailResponse)
async def get_group(
    group_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.GROUPS_READ),
    db: AsyncSession = Depends(get_db),
):
    """Get a group by ID with user list. Read-only — gated on
    ``GROUPS_READ`` only."""
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
        restrict_printers=bool(group.restrict_printers),
        printer_ids=await group_printer_ids(db, group.id),
        locations=await group_location_names(db, group.id),
        user_count=len(group.users),
        created_at=group.created_at,
        updated_at=group.updated_at,
        users=[UserBrief(id=u.id, username=u.username, is_active=u.is_active) for u in group.users],
    )


@router.patch("/{group_id}", response_model=GroupResponse)
async def update_group(
    group_id: int,
    group_data: GroupUpdate,
    _admin: User | None = RequireAdminIfAuthEnabled(),
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
        # System groups (Administrators in particular) have fixed permission
        # sets that the app depends on — stripping them is a denial-of-
        # service vector that even admin callers shouldn't trigger by
        # accident through the generic edit form. Mirrors the rename block
        # immediately above.
        if group.is_system:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot modify permissions of system groups",
            )
        # Validate permissions
        invalid_perms = [p for p in group_data.permissions if p not in ALL_PERMISSIONS]
        if invalid_perms:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid permissions: {', '.join(invalid_perms)}",
            )
        group.permissions = group_data.permissions

    scope_changed = await _apply_printer_scope(
        db, group, group_data.restrict_printers, group_data.printer_ids, group_data.locations
    )

    await db.commit()
    await db.refresh(group)
    if scope_changed:
        await ws_manager.refresh_printer_scopes()

    return _group_response(
        group, await group_printer_ids(db, group.id), await group_location_names(db, group.id), len(group.users)
    )


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: int,
    _admin: User | None = RequireAdminIfAuthEnabled(),
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

    restricted = bool(group.restrict_printers)
    # SQLite doesn't enforce the FK cascade
    await db.execute(delete(group_printers).where(group_printers.c.group_id == group_id))
    await db.execute(delete(group_locations).where(group_locations.c.group_id == group_id))
    await db.delete(group)
    await db.commit()
    if restricted:
        await ws_manager.refresh_printer_scopes()


@router.post("/{group_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def add_user_to_group(
    group_id: int,
    user_id: int,
    _admin: User | None = RequireAdminIfAuthEnabled(),
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
    if group.restrict_printers:
        await ws_manager.refresh_printer_scopes()


@router.delete("/{group_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_user_from_group(
    group_id: int,
    user_id: int,
    _admin: User | None = RequireAdminIfAuthEnabled(),
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
    if group.restrict_printers:
        await ws_manager.refresh_printer_scopes()
