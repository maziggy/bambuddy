from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    authenticate_user,
    create_access_token,
    get_current_active_user,
    get_password_hash,
    get_user_by_username,
)
from backend.app.core.database import get_db
from backend.app.models.group import Group
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.schemas.auth import GroupBrief, LoginRequest, LoginResponse, SetupRequest, SetupResponse, UserResponse


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


router = APIRouter(prefix="/auth", tags=["authentication"])


async def is_auth_enabled(db: AsyncSession) -> bool:
    """Check if authentication is enabled."""
    result = await db.execute(select(Settings).where(Settings.key == "auth_enabled"))
    setting = result.scalar_one_or_none()
    if setting is None:
        return False
    return setting.value.lower() == "true"


async def set_auth_enabled(db: AsyncSession, enabled: bool) -> None:
    """Set authentication enabled status."""
    from sqlalchemy import func
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    stmt = sqlite_insert(Settings).values(key="auth_enabled", value="true" if enabled else "false")
    stmt = stmt.on_conflict_do_update(
        index_elements=["key"], set_={"value": "true" if enabled else "false", "updated_at": func.now()}
    )
    await db.execute(stmt)
    # Note: Don't commit here - let get_db handle it or commit explicitly in the route


async def is_setup_completed(db: AsyncSession) -> bool:
    """Check if setup has been completed."""
    result = await db.execute(select(Settings).where(Settings.key == "setup_completed"))
    setting = result.scalar_one_or_none()
    return setting and setting.value.lower() == "true"


async def set_setup_completed(db: AsyncSession, completed: bool) -> None:
    """Set setup completed status."""
    from sqlalchemy import func
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    stmt = sqlite_insert(Settings).values(key="setup_completed", value="true" if completed else "false")
    stmt = stmt.on_conflict_do_update(
        index_elements=["key"], set_={"value": "true" if completed else "false", "updated_at": func.now()}
    )
    await db.execute(stmt)
    # Note: Don't commit here - let get_db handle it or commit explicitly in the route


@router.post("/setup", response_model=SetupResponse)
async def setup_auth(request: SetupRequest, db: AsyncSession = Depends(get_db)):
    """First-time setup: enable/disable authentication and create admin user."""
    import logging

    logger = logging.getLogger(__name__)

    try:
        # If auth_enabled is true but no users exist, allow re-setup (recovery scenario)

        admin_created = False

        if request.auth_enabled:
            # Check if admin users already exist
            admin_users_result = await db.execute(select(User).where(User.role == "admin"))
            existing_admin_users = list(admin_users_result.scalars().all())
            has_admin_users = len(existing_admin_users) > 0

            if has_admin_users:
                # Admin users already exist, just enable auth (don't create new admin)
                logger.info(
                    f"Admin users already exist ({len(existing_admin_users)} found), enabling authentication without creating new admin"
                )
                admin_created = False
            else:
                # No admin users exist, require admin credentials to create first admin
                if not request.admin_username or not request.admin_password:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Admin username and password are required when enabling authentication (no admin users exist)",
                    )

                # Check if username already exists (shouldn't happen if no admin users exist, but check anyway)
                existing_user = await get_user_by_username(db, request.admin_username)
                if existing_user:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="User with this username already exists",
                    )

                # Create admin user FIRST (before enabling auth)
                try:
                    logger.info("Creating admin user: %s", request.admin_username)
                    admin_user = User(
                        username=request.admin_username,
                        password_hash=get_password_hash(request.admin_password),
                        role="admin",
                        is_active=True,
                    )

                    # Try to add user to Administrators group if it exists
                    admin_group_result = await db.execute(select(Group).where(Group.name == "Administrators"))
                    admin_group = admin_group_result.scalar_one_or_none()
                    if admin_group:
                        admin_user.groups.append(admin_group)
                        logger.info("Added new admin user to Administrators group")

                    db.add(admin_user)
                    logger.info("Admin user added to session: %s", request.admin_username)
                    admin_created = True
                except Exception as e:
                    await db.rollback()
                    logger.error("Failed to create admin user: %s", e, exc_info=True)
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=f"Failed to create admin user: {str(e)}",
                    )

        # Set auth enabled and mark setup as completed
        await set_auth_enabled(db, request.auth_enabled)
        await set_setup_completed(db, True)
        await db.commit()

        if admin_created:
            await db.refresh(admin_user)
            logger.info("Admin user created successfully: %s", admin_user.id)

        logger.info("Setup completed: auth_enabled=%s, admin_created=%s", request.auth_enabled, admin_created)
        return SetupResponse(auth_enabled=request.auth_enabled, admin_created=admin_created)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Setup error: %s", e, exc_info=True)
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Setup failed: {str(e)}",
        )


@router.get("/status")
async def get_auth_status(db: AsyncSession = Depends(get_db)):
    """Get authentication status (public endpoint)."""
    auth_enabled = await is_auth_enabled(db)
    setup_completed = await is_setup_completed(db)
    # Only require setup if it hasn't been completed yet
    requires_setup = not setup_completed
    return {"auth_enabled": auth_enabled, "requires_setup": requires_setup}


@router.post("/disable", response_model=dict)
async def disable_auth(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Disable authentication (admin only)."""
    import logging

    logger = logging.getLogger(__name__)

    # Reload user with groups for proper is_admin check
    result = await db.execute(select(User).where(User.id == current_user.id).options(selectinload(User.groups)))
    user = result.scalar_one()

    # Only admins can disable authentication
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can disable authentication",
        )

    try:
        await set_auth_enabled(db, False)
        await db.commit()
        logger.info("Authentication disabled by admin user: %s", user.username)
        return {"message": "Authentication disabled successfully", "auth_enabled": False}
    except Exception as e:
        await db.rollback()
        logger.error("Failed to disable authentication: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to disable authentication: {str(e)}",
        )


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Login and get access token."""
    # Check if auth is enabled
    auth_enabled = await is_auth_enabled(db)
    if not auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authentication is not enabled",
        )

    user = await authenticate_user(db, request.username, request.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Reload user with groups for proper permission calculation
    result = await db.execute(select(User).where(User.id == user.id).options(selectinload(User.groups)))
    user = result.scalar_one()

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(data={"sub": user.username}, expires_delta=access_token_expires)

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        user=_user_to_response(user),
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current user information."""
    # Reload user with groups for proper permission calculation
    result = await db.execute(select(User).where(User.id == current_user.id).options(selectinload(User.groups)))
    user = result.scalar_one()
    return _user_to_response(user)


@router.post("/logout")
async def logout():
    """Logout (client should discard token)."""
    return {"message": "Logged out successfully"}
