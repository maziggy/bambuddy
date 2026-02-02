from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import PyJWTError as JWTError
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.database import async_session, get_db
from backend.app.core.permissions import Permission
from backend.app.models.api_key import APIKey
from backend.app.models.settings import Settings
from backend.app.models.user import User

logger = logging.getLogger(__name__)

# Password hashing
# Use pbkdf2_sha256 instead of bcrypt to avoid 72-byte limit and passlib initialization issues
# pbkdf2_sha256 is a secure password hashing algorithm without bcrypt's limitations
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def _get_jwt_secret() -> str:
    """Get the JWT secret key from environment, file, or generate a new one.

    Priority:
    1. JWT_SECRET_KEY environment variable
    2. .jwt_secret file in data directory
    3. Generate new random secret and save to file

    Returns:
        The JWT secret key
    """
    # 1. Check environment variable first
    env_secret = os.environ.get("JWT_SECRET_KEY")
    if env_secret:
        logger.info("Using JWT secret from JWT_SECRET_KEY environment variable")
        return env_secret

    # 2. Check for secret file in data directory
    # Use DATA_DIR env var (same as rest of app), fallback to data/ subdirectory
    data_dir_env = os.environ.get("DATA_DIR")
    if data_dir_env:
        data_dir = Path(data_dir_env)
    else:
        # Fallback to data/ subdirectory under project root (not project root itself!)
        data_dir = Path(__file__).parent.parent.parent.parent / "data"
    secret_file = data_dir / ".jwt_secret"

    if secret_file.exists():
        try:
            secret = secret_file.read_text().strip()
            if secret and len(secret) >= 32:
                logger.info("Using JWT secret from %s", secret_file)
                return secret
        except Exception as e:
            logger.warning("Failed to read JWT secret file: %s", e)

    # 3. Generate new random secret
    new_secret = secrets.token_urlsafe(64)

    # Try to save it
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        secret_file.write_text(new_secret)
        # Restrict permissions (owner read/write only)
        secret_file.chmod(0o600)
        logger.info("Generated new JWT secret and saved to %s", secret_file)
    except Exception as e:
        logger.warning(
            "Could not save JWT secret to file (%s). "
            "Secret will be regenerated on restart, invalidating existing tokens. "
            "Set JWT_SECRET_KEY environment variable for persistence.",
            e,
        )

    return new_secret


# JWT settings
SECRET_KEY = _get_jwt_secret()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

# HTTP Bearer token
security = HTTPBearer(auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash.

    Uses pbkdf2_sha256 which handles long passwords automatically.
    """
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password.

    Uses pbkdf2_sha256 which is secure and has no password length limit.
    """
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    """Get a user by username with groups loaded for permission checks."""
    result = await db.execute(select(User).where(User.username == username).options(selectinload(User.groups)))
    return result.scalar_one_or_none()


async def authenticate_user(db: AsyncSession, username: str, password: str) -> User | None:
    """Authenticate a user by username and password."""
    user = await get_user_by_username(db, username)
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    if not user.is_active:
        return None
    return user


async def is_auth_enabled(db: AsyncSession) -> bool:
    """Check if authentication is enabled."""
    try:
        result = await db.execute(select(Settings).where(Settings.key == "auth_enabled"))
        setting = result.scalar_one_or_none()
        if setting is None:
            return False
        return setting.value.lower() == "true"
    except Exception:
        # If settings table doesn't exist or query fails, assume auth is disabled
        return False


async def get_current_user_optional(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
) -> User | None:
    """Get the current authenticated user from JWT token, or None if not authenticated."""
    if credentials is None:
        return None

    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            return None
    except JWTError:
        return None

    async with async_session() as db:
        user = await get_user_by_username(db, username)
        if user is None or not user.is_active:
            return None
        return user


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
) -> User:
    """Get the current authenticated user from JWT token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise credentials_exception
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    async with async_session() as db:
        user = await get_user_by_username(db, username)
        if user is None:
            raise credentials_exception
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is disabled",
            )
        return user


async def get_current_active_user(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    """Get the current active user (alias for clarity)."""
    return current_user


async def require_auth_if_enabled(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
) -> User | None:
    """Require authentication if auth is enabled, otherwise return None."""
    async with async_session() as db:
        auth_enabled = await is_auth_enabled(db)
        if not auth_enabled:
            return None

        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            token = credentials.credentials
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username: str = payload.get("sub")
            if username is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Could not validate credentials",
                    headers={"WWW-Authenticate": "Bearer"},
                )
        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user = await get_user_by_username(db, username)
        if user is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user


def require_role(required_role: str):
    """Dependency factory for role-based access control."""

    async def role_checker(current_user: Annotated[User, Depends(get_current_user)]) -> User:
        if current_user.role != required_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {required_role} role",
            )
        return current_user

    return role_checker


def require_admin_if_auth_enabled():
    """Dependency factory that requires admin role if auth is enabled."""

    async def admin_checker(
        current_user: Annotated[User | None, Depends(require_auth_if_enabled)] = None,
    ) -> User | None:
        if current_user is None:
            return None  # Auth not enabled, allow access
        if current_user.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Requires admin role",
            )
        return current_user

    return admin_checker


def generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key.

    Returns:
        tuple: (full_key, key_hash, key_prefix)
            - full_key: The complete API key (only shown once on creation)
            - key_hash: Hashed version for storage and verification
            - key_prefix: First 8 characters for display purposes
    """
    # Generate a secure random API key (32 bytes = 64 hex characters)
    full_key = f"bb_{secrets.token_urlsafe(32)}"
    key_hash = get_password_hash(full_key)
    key_prefix = full_key[:8] + "..." if len(full_key) > 8 else full_key
    return full_key, key_hash, key_prefix


async def get_api_key(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    db: AsyncSession = Depends(get_db),
) -> APIKey:
    """Get and validate API key from request headers.

    Checks both 'Authorization: Bearer <key>' and 'X-API-Key: <key>' headers.
    """
    api_key_value = None
    if x_api_key:
        api_key_value = x_api_key
    elif authorization and authorization.startswith("Bearer "):
        api_key_value = authorization.replace("Bearer ", "")

    if not api_key_value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required. Provide 'X-API-Key' header or 'Authorization: Bearer <key>'",
        )

    # Get all API keys and check them
    result = await db.execute(select(APIKey).where(APIKey.enabled.is_(True)))
    api_keys = result.scalars().all()

    for api_key in api_keys:
        # Check if key matches (verify against hash)
        if verify_password(api_key_value, api_key.key_hash):
            # Check expiration
            if api_key.expires_at and api_key.expires_at < datetime.now():
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="API key has expired",
                )
            # Update last_used timestamp
            api_key.last_used = datetime.now()
            await db.commit()
            return api_key

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key",
    )


def check_permission(api_key: APIKey, permission: str) -> None:
    """Check if API key has the required permission.

    Args:
        api_key: The API key object
        permission: One of 'queue', 'control_printer', 'read_status'

    Raises:
        HTTPException: If permission is not granted
    """
    permission_map = {
        "queue": "can_queue",
        "control_printer": "can_control_printer",
        "read_status": "can_read_status",
    }

    if permission not in permission_map:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unknown permission: {permission}",
        )

    attr_name = permission_map[permission]
    if not getattr(api_key, attr_name, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"API key does not have '{permission}' permission",
        )


def check_printer_access(api_key: APIKey, printer_id: int) -> None:
    """Check if API key has access to the specified printer.

    Args:
        api_key: The API key object
        printer_id: The printer ID to check access for

    Raises:
        HTTPException: If access is denied
    """
    # If printer_ids is None or empty, access to all printers
    if api_key.printer_ids is None or len(api_key.printer_ids) == 0:
        return

    # Check if printer_id is in allowed list
    if printer_id not in api_key.printer_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"API key does not have access to printer {printer_id}",
        )


# Convenience dependencies - these are functions that return Depends objects
def RequireAdmin():
    """Dependency that requires admin role."""
    return Depends(require_role("admin"))


def RequireAdminIfAuthEnabled():
    """Dependency that requires admin role if auth is enabled."""
    return Depends(require_admin_if_auth_enabled())


def require_permission(*permissions: str | Permission):
    """Dependency factory that requires user to have ALL specified permissions.

    Args:
        *permissions: Permission strings or Permission enum values to require

    Returns:
        A dependency function that validates permissions
    """
    # Convert Permission enums to strings
    perm_strings = [p.value if isinstance(p, Permission) else p for p in permissions]

    async def permission_checker(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
    ) -> User:
        credentials_exception = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
        if credentials is None:
            raise credentials_exception

        try:
            token = credentials.credentials
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username: str = payload.get("sub")
            if username is None:
                raise credentials_exception
        except JWTError:
            raise credentials_exception

        async with async_session() as db:
            user = await get_user_by_username(db, username)
            if user is None or not user.is_active:
                raise credentials_exception

            if not user.has_all_permissions(*perm_strings):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Missing required permissions: {', '.join(perm_strings)}",
                )
            return user

    return permission_checker


def require_permission_if_auth_enabled(*permissions: str | Permission):
    """Dependency factory that checks permissions only if auth is enabled.

    This provides backward compatibility - when auth is disabled, all access is allowed.

    Args:
        *permissions: Permission strings or Permission enum values to require

    Returns:
        A dependency function that validates permissions if auth is enabled
    """
    # Convert Permission enums to strings
    perm_strings = [p.value if isinstance(p, Permission) else p for p in permissions]

    async def permission_checker(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
    ) -> User | None:
        async with async_session() as db:
            auth_enabled = await is_auth_enabled(db)
            if not auth_enabled:
                return None  # Auth disabled, allow access

            if credentials is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            try:
                token = credentials.credentials
                payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
                username: str = payload.get("sub")
                if username is None:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Could not validate credentials",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
            except JWTError:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Could not validate credentials",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            user = await get_user_by_username(db, username)
            if user is None or not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Could not validate credentials",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            if not user.has_all_permissions(*perm_strings):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Missing required permissions: {', '.join(perm_strings)}",
                )
            return user

    return permission_checker


def RequirePermission(*permissions: str | Permission):
    """Convenience dependency that requires ALL specified permissions."""
    return Depends(require_permission(*permissions))


def RequirePermissionIfAuthEnabled(*permissions: str | Permission):
    """Convenience dependency that requires permissions if auth is enabled."""
    return Depends(require_permission_if_auth_enabled(*permissions))


def require_ownership_permission(
    all_permission: str | Permission,
    own_permission: str | Permission,
):
    """Dependency factory for ownership-based permission checks.

    - User with `all_permission` can modify any item
    - User with `own_permission` can only modify items where created_by_id == user.id
    - Ownerless items (created_by_id = null) require `all_permission`

    Returns:
        A dependency function that returns (user, can_modify_all).
        - can_modify_all=True: user can modify any item
        - can_modify_all=False: user can only modify their own items
    """
    all_perm = all_permission.value if isinstance(all_permission, Permission) else all_permission
    own_perm = own_permission.value if isinstance(own_permission, Permission) else own_permission

    async def checker(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
    ) -> tuple[User | None, bool]:
        """Returns (user, can_modify_all).

        - can_modify_all=True: user can modify any item
        - can_modify_all=False: user can only modify their own items
        """
        async with async_session() as db:
            auth_enabled = await is_auth_enabled(db)
            if not auth_enabled:
                return None, True  # Auth disabled, allow all

            if credentials is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            try:
                token = credentials.credentials
                payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
                username: str = payload.get("sub")
                if username is None:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Could not validate credentials",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
            except JWTError:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Could not validate credentials",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            user = await get_user_by_username(db, username)
            if user is None or not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Could not validate credentials",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            if user.has_permission(all_perm):
                return user, True
            if user.has_permission(own_perm):
                return user, False

            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {own_perm} or {all_perm}",
            )

    return checker
