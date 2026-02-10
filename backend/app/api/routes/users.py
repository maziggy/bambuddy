from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.api.routes.settings import get_external_login_url
from backend.app.core.auth import (
    RequirePermissionIfAuthEnabled,
    get_current_user_optional,
    get_password_hash,
    verify_password,
)
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.archive import PrintArchive
from backend.app.models.group import Group
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.schemas.auth import ChangePasswordRequest, GroupBrief, UserCreate, UserResponse, UserUpdate
from backend.app.services.email_service import (
    create_welcome_email_from_template,
    generate_secure_password,
    get_smtp_settings,
    send_email,
)

router = APIRouter(prefix="/users", tags=["users"])


def _user_to_response(user: User) -> UserResponse:
    """Convert a User model to UserResponse schema."""
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
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
    """Create a new user.

    When advanced authentication is enabled:
    - Email is required
    - Password is auto-generated and emailed to user
    - Admin cannot set or see the password
    """
    import logging

    logger = logging.getLogger(__name__)

    # Check if advanced auth is enabled
    result = await db.execute(select(Settings).where(Settings.key == "advanced_auth_enabled"))
    advanced_auth_setting = result.scalar_one_or_none()
    advanced_auth_enabled = advanced_auth_setting and advanced_auth_setting.value.lower() == "true"

    # Check if username already exists (case-insensitive)
    existing_user = await db.execute(
        select(User).where(func.lower(User.username) == func.lower(user_data.username))
    )
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

    # Advanced auth validation
    if advanced_auth_enabled:
        if not user_data.email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email is required when advanced authentication is enabled",
            )
        # Check if email already exists (case-insensitive)
        existing_email = await db.execute(
            select(User).where(func.lower(User.email) == func.lower(user_data.email))
        )
        if existing_email.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already exists",
            )

    # Generate password if advanced auth enabled, otherwise require password
    if advanced_auth_enabled:
        password = generate_secure_password()
    else:
        if not user_data.password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password is required when advanced authentication is disabled",
            )
        password = user_data.password

    new_user = User(
        username=user_data.username,
        email=user_data.email,
        password_hash=get_password_hash(password),
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

    # Send welcome email if advanced auth enabled
    if advanced_auth_enabled and new_user.email:
        try:
            smtp_settings = await get_smtp_settings(db)
            if smtp_settings:
                login_url = await get_external_login_url(db)
                subject, text_body, html_body = await create_welcome_email_from_template(
                    db, new_user.username, password, login_url
                )
                send_email(smtp_settings, new_user.email, subject, text_body, html_body)
                logger.info(f"Welcome email sent to {new_user.email}")
            else:
                logger.warning(f"SMTP not configured, could not send welcome email to {new_user.email}")
        except Exception as e:
            logger.error(f"Failed to send welcome email: {e}")
            # Don't fail user creation if email fails

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
        # Check if new username already exists (case-insensitive)
        existing_user = await db.execute(
            select(User).where(func.lower(User.username) == func.lower(user_data.username), User.id != user_id)
        )
        if existing_user.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already exists",
            )
        user.username = user_data.username

    if user_data.email is not None:
        # Check if new email already exists (case-insensitive)
        existing_email = await db.execute(
            select(User).where(func.lower(User.email) == func.lower(user_data.email), User.id != user_id)
        )
        if existing_email.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already exists",
            )
        user.email = user_data.email

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


@router.get("/{user_id}/items-count")
async def get_user_items_count(
    user_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.USERS_READ),
    db: AsyncSession = Depends(get_db),
):
    """Get count of items created by this user."""
    # Verify user exists
    result = await db.execute(select(User).where(User.id == user_id))
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Count archives
    archives_result = await db.execute(select(func.count(PrintArchive.id)).where(PrintArchive.created_by_id == user_id))
    archives_count = archives_result.scalar() or 0

    # Count queue items
    queue_result = await db.execute(
        select(func.count(PrintQueueItem.id)).where(PrintQueueItem.created_by_id == user_id)
    )
    queue_items_count = queue_result.scalar() or 0

    # Count library files
    library_result = await db.execute(select(func.count(LibraryFile.id)).where(LibraryFile.created_by_id == user_id))
    library_files_count = library_result.scalar() or 0

    return {
        "archives": archives_count,
        "queue_items": queue_items_count,
        "library_files": library_files_count,
    }


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    delete_items: bool = Query(False, description="Delete all items created by this user"),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.USERS_DELETE),
    db: AsyncSession = Depends(get_db),
):
    """Delete a user.

    If delete_items=True, all archives, queue items, and library files created by
    this user will also be deleted. Otherwise, these items will become "ownerless"
    (created_by_id set to NULL by the foreign key constraint).
    """
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

    if delete_items:
        # Delete all items created by this user
        await db.execute(delete(PrintArchive).where(PrintArchive.created_by_id == user_id))
        await db.execute(delete(PrintQueueItem).where(PrintQueueItem.created_by_id == user_id))
        await db.execute(delete(LibraryFile).where(LibraryFile.created_by_id == user_id))
    else:
        # Explicitly set created_by_id to NULL for all items (ensures consistent behavior
        # across different database backends, including SQLite without foreign key support)
        from sqlalchemy import update

        await db.execute(update(PrintArchive).where(PrintArchive.created_by_id == user_id).values(created_by_id=None))
        await db.execute(
            update(PrintQueueItem).where(PrintQueueItem.created_by_id == user_id).values(created_by_id=None)
        )
        await db.execute(update(LibraryFile).where(LibraryFile.created_by_id == user_id).values(created_by_id=None))

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
