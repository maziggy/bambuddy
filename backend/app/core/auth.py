from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import PyJWTError as JWTError
from passlib.context import CryptContext
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.database import async_session, get_db
from backend.app.core.permissions import Permission
from backend.app.models.api_key import APIKey
from backend.app.models.auth_ephemeral import AuthEphemeralToken
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
        except OSError as e:
            logger.warning("Failed to read JWT secret file: %s", e)

    # 3. Generate new random secret
    new_secret = secrets.token_urlsafe(64)

    # Try to save it
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        # Note: CodeQL flags this as "clear-text storage of sensitive information" but this is
        # intentional and secure - JWT secrets must be readable by the app, we set 0600 permissions,
        # and this is standard practice for self-hosted applications (same as .env files).
        secret_file.write_text(new_secret)  # nosec B105
        # Restrict permissions (owner read/write only)
        secret_file.chmod(0o600)
        logger.info("Generated new JWT secret and saved to %s", secret_file)
    except OSError as e:
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
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours (M-2: reduced from 7 days)

# HTTP Bearer token
security = HTTPBearer(auto_error=False)

# --- Slicer download tokens ---
# Short-lived, single-use tokens for slicer protocol handlers that can't send
# auth headers.  Stored in AuthEphemeralToken (token_type="slicer_download")
# so they survive server restarts and work in multi-worker deployments (M-3).
SLICER_TOKEN_EXPIRE_MINUTES = 5


async def create_slicer_download_token(resource_type: str, resource_id: int) -> str:
    """Create a short-lived, single-use download token for slicer protocol handlers."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=SLICER_TOKEN_EXPIRE_MINUTES)
    token = secrets.token_urlsafe(24)
    resource_key = f"{resource_type}:{resource_id}"
    async with async_session() as db:
        # Prune expired tokens opportunistically
        await db.execute(
            delete(AuthEphemeralToken).where(
                AuthEphemeralToken.token_type == "slicer_download",
                AuthEphemeralToken.expires_at < now,
            )
        )
        db.add(
            AuthEphemeralToken(
                token=token,
                token_type="slicer_download",
                nonce=resource_key,
                expires_at=expires_at,
            )
        )
        await db.commit()
    return token


async def verify_slicer_download_token(token: str, resource_type: str, resource_id: int) -> bool:
    """Verify and atomically consume a slicer download token.

    Returns True only if the token is valid, unexpired, and bound to the given resource.
    DELETE...RETURNING ensures the token is single-use even under concurrent requests.

    M-NEW-1 fix: nonce (resource key) is included in the WHERE clause so the DELETE
    only succeeds when the token is presented to the *correct* resource endpoint.
    Previously the token was consumed (committed) even when stored_key != expected_key,
    permanently invalidating it while returning False to the caller.
    """
    expected_key = f"{resource_type}:{resource_id}"
    now = datetime.now(timezone.utc)
    async with async_session() as db:
        result = await db.execute(
            delete(AuthEphemeralToken)
            .where(
                AuthEphemeralToken.token == token,
                AuthEphemeralToken.token_type == "slicer_download",
                AuthEphemeralToken.nonce == expected_key,
                AuthEphemeralToken.expires_at > now,
            )
            .returning(AuthEphemeralToken.id)
        )
        if result.one_or_none() is None:
            return False
        await db.commit()
        return True


# --- Camera stream tokens ---
# Reusable tokens for camera stream/snapshot endpoints loaded via <img>/<video>
# tags (these cannot send Authorization headers).  Unlike slicer tokens they are
# NOT single-use — streams reconnect on errors.  Stored in AuthEphemeralToken
# (token_type="camera_stream") for multi-worker compatibility (M-3).
CAMERA_STREAM_TOKEN_EXPIRE_MINUTES = 60


async def create_camera_stream_token() -> str:
    """Create a reusable token for camera stream/snapshot access."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=CAMERA_STREAM_TOKEN_EXPIRE_MINUTES)
    token = secrets.token_urlsafe(24)
    async with async_session() as db:
        # Prune expired tokens opportunistically
        await db.execute(
            delete(AuthEphemeralToken).where(
                AuthEphemeralToken.token_type == "camera_stream",
                AuthEphemeralToken.expires_at < now,
            )
        )
        db.add(
            AuthEphemeralToken(
                token=token,
                token_type="camera_stream",
                expires_at=expires_at,
            )
        )
        await db.commit()
    return token


async def verify_camera_stream_token(token: str) -> bool:
    """Verify a camera stream token is valid (reusable — does not consume it)."""
    now = datetime.now(timezone.utc)
    async with async_session() as db:
        result = await db.execute(
            select(AuthEphemeralToken).where(
                AuthEphemeralToken.token == token,
                AuthEphemeralToken.token_type == "camera_stream",
                AuthEphemeralToken.expires_at > now,
            )
        )
        return result.scalar_one_or_none() is not None


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
    """Create a JWT access token with jti (revocation) and iat (freshness) claims."""
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    jti = secrets.token_hex(16)
    to_encode.update({"exp": expire, "jti": jti, "iat": now})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def _is_token_fresh(iat: int | float | None, user: User) -> bool:
    """Return False if the token was issued before the user's last password change.

    Used to invalidate all sessions after a password reset/change (M-R7-B).
    All tokens without an iat claim are unconditionally rejected — every token
    issued by this server carries iat, so absence means the token is forged or
    from a pre-iat code path whose max TTL (24 h) has long since expired.
    """
    if iat is None:
        return False
    if not hasattr(user, "password_changed_at") or user.password_changed_at is None:
        return True  # No password change recorded yet (I2 migration handles this)
    token_issued_at = datetime.fromtimestamp(iat, tz=timezone.utc)
    pca = user.password_changed_at
    if pca.tzinfo is None:
        pca = pca.replace(tzinfo=timezone.utc)
    # JWT iat is whole seconds; truncate pca so tokens issued in the same second pass.
    pca = pca.replace(microsecond=0)
    return token_issued_at >= pca


async def revoke_jti(jti: str, expires_at: datetime, username: str | None = None) -> None:
    """Store a revoked JWT jti so it is rejected on future requests.

    Silently ignores duplicate inserts (e.g. double-logout with the same token).
    """
    from sqlalchemy.exc import IntegrityError

    async with async_session() as db:
        revoked = AuthEphemeralToken(
            token=jti,
            token_type="revoked_jti",
            username=username,
            expires_at=expires_at,
        )
        db.add(revoked)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()  # jti already revoked — desired state, ignore


async def is_jti_revoked(jti: str) -> bool:
    """Return True if the given jti has been revoked."""
    async with async_session() as db:
        result = await db.execute(
            select(AuthEphemeralToken).where(
                AuthEphemeralToken.token == jti,
                AuthEphemeralToken.token_type == "revoked_jti",
            )
        )
        return result.scalar_one_or_none() is not None


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    """Get a user by username (case-insensitive) with groups loaded for permission checks."""
    result = await db.execute(
        select(User).where(func.lower(User.username) == func.lower(username)).options(selectinload(User.groups))
    )
    return result.scalar_one_or_none()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    """Get a user by email (case-insensitive) with groups loaded for permission checks."""
    result = await db.execute(
        select(User).where(func.lower(User.email) == func.lower(email)).options(selectinload(User.groups))
    )
    return result.scalar_one_or_none()


async def authenticate_user(db: AsyncSession, username: str, password: str) -> User | None:
    """Authenticate a user by username and password.

    Username lookup is case-insensitive. Password is case-sensitive.
    LDAP and OIDC users must authenticate via their respective providers.
    """
    user = await get_user_by_username(db, username)
    if not user:
        return None
    if getattr(user, "auth_source", "local") in ("ldap", "oidc"):
        return None  # LDAP/OIDC users must authenticate via their provider
    if not user.password_hash or not verify_password(password, user.password_hash):
        return None
    if not user.is_active:
        return None
    return user


async def authenticate_user_by_email(db: AsyncSession, email: str, password: str) -> User | None:
    """Authenticate a user by email and password.

    Email lookup is case-insensitive. Password is case-sensitive.
    LDAP and OIDC users must authenticate via their respective providers.
    """
    user = await get_user_by_email(db, email)
    if not user:
        return None
    if getattr(user, "auth_source", "local") in ("ldap", "oidc"):
        return None  # LDAP/OIDC users must authenticate via their provider
    if not user.password_hash or not verify_password(password, user.password_hash):
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


async def _validate_api_key(db: AsyncSession, api_key_value: str) -> APIKey | None:
    """Validate an API key and return the APIKey object if valid, None otherwise.

    L-1: Pre-filter by key_prefix (first 8 chars) before running pbkdf2 so only
    O(1) candidate rows are hashed instead of the full key table.  The prefix is
    not secret (it is shown in the admin UI), so this does not reduce security.
    """
    try:
        # key_prefix is stored as "<first-8-chars>..." (e.g. "bb_Abc12...").
        # Matching on the first 8 chars of the submitted key reduces the scan to
        # at most one row in practice (2^40 collision space for 5 base64 chars).
        key_lookup = api_key_value[:8] if len(api_key_value) >= 8 else api_key_value
        result = await db.execute(
            select(APIKey).where(
                APIKey.enabled.is_(True),
                APIKey.key_prefix.like(
                    key_lookup.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%", escape="\\"
                ),
            )
        )
        api_keys = result.scalars().all()

        for api_key in api_keys:
            if verify_password(api_key_value, api_key.key_hash):
                # Check expiration
                if api_key.expires_at:
                    expires = api_key.expires_at
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=timezone.utc)
                    if expires < datetime.now(timezone.utc):
                        return None  # Expired
                # Update last_used timestamp
                api_key.last_used = datetime.now(timezone.utc)
                await db.commit()
                return api_key
    except Exception as e:
        logger.warning("API key validation error: %s", e)
    return None


async def get_current_user_optional(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
) -> User | None:
    """Get the current authenticated user from JWT token, or None if not authenticated.

    Returns None only when NO credentials are supplied.  If a token is supplied
    but invalid/revoked, raises 401 — a revoked token must not grant anonymous
    access (I6).
    """
    if credentials is None:
        return None

    _unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise _unauthorized
        jti: str | None = payload.get("jti")
        if not jti or await is_jti_revoked(jti):
            raise _unauthorized  # I6: revoked token → 401, not anonymous
        iat: int | float | None = payload.get("iat")
    except JWTError:
        raise _unauthorized

    async with async_session() as db:
        user = await get_user_by_username(db, username)
        if user is None or not user.is_active:
            raise _unauthorized
        if not _is_token_fresh(iat, user):
            raise _unauthorized
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
        jti: str | None = payload.get("jti")
        if not jti or await is_jti_revoked(jti):
            raise credentials_exception
        iat: int | float | None = payload.get("iat")
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
        if not _is_token_fresh(iat, user):
            raise credentials_exception
        return user


async def get_current_active_user(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    """Get the current active user (alias for clarity)."""
    return current_user


async def require_auth_if_enabled(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> User | None:
    """Require authentication if auth is enabled, otherwise return None.

    Accepts both JWT tokens (via Authorization: Bearer header) and API keys
    (via X-API-Key header or Authorization: Bearer bb_xxx).
    """
    async with async_session() as db:
        auth_enabled = await is_auth_enabled(db)
        if not auth_enabled:
            return None

        # Check for API key first (X-API-Key header)
        if x_api_key:
            api_key = await _validate_api_key(db, x_api_key)
            if api_key:
                return None  # API key valid, allow access

        # Check for Bearer token (could be JWT or API key)
        if credentials is not None:
            token = credentials.credentials
            # Check if it's an API key (starts with bb_)
            if token.startswith("bb_"):
                api_key = await _validate_api_key(db, token)
                if api_key:
                    return None  # API key valid, allow access
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid API key",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # Otherwise treat as JWT
            try:
                payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
                username: str = payload.get("sub")
                if username is None:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Could not validate credentials",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                jti: str | None = payload.get("jti")
                if not jti or await is_jti_revoked(jti):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Could not validate credentials",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                iat: int | float | None = payload.get("iat")
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
            if not _is_token_fresh(iat, user):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Could not validate credentials",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return user

        # No credentials provided
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )


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

    # M-NEW-2: Pre-filter by key_prefix (first 8 chars) to avoid O(n) pbkdf2 over all
    # enabled keys — same fix as in _validate_api_key (L-1 from previous review).
    key_lookup = api_key_value[:8] if len(api_key_value) >= 8 else api_key_value
    result = await db.execute(
        select(APIKey).where(
            APIKey.enabled.is_(True),
            APIKey.key_prefix.like(key_lookup + "%"),
        )
    )
    api_keys = result.scalars().all()

    for api_key in api_keys:
        # Check if key matches (verify against hash)
        if verify_password(api_key_value, api_key.key_hash):
            # Check expiration
            if api_key.expires_at:
                expires = api_key.expires_at
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if expires < datetime.now(timezone.utc):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="API key has expired",
                    )
            # Update last_used timestamp
            api_key.last_used = datetime.now(timezone.utc)
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
    # None = global key, access to all printers
    if api_key.printer_ids is None:
        return

    # Empty list or printer not in allowed list = no access
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

    Accepts both JWT tokens (via Authorization: Bearer header) and API keys
    (via X-API-Key header or Authorization: Bearer bb_xxx).

    Args:
        *permissions: Permission strings or Permission enum values to require

    Returns:
        A dependency function that validates permissions
    """
    # Convert Permission enums to strings
    perm_strings = [p.value if isinstance(p, Permission) else p for p in permissions]

    async def permission_checker(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> User | None:
        async with async_session() as db:
            # Check for API key first (X-API-Key header)
            if x_api_key:
                api_key = await _validate_api_key(db, x_api_key)
                if api_key:
                    return None  # API key valid, allow access

            credentials_exception = HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

            if credentials is None:
                raise credentials_exception

            token = credentials.credentials
            # Check if it's an API key (starts with bb_)
            if token.startswith("bb_"):
                api_key = await _validate_api_key(db, token)
                if api_key:
                    return None  # API key valid, allow access
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid API key",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # Otherwise treat as JWT
            try:
                payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
                username: str = payload.get("sub")
                if username is None:
                    raise credentials_exception
                jti: str | None = payload.get("jti")
                if not jti or await is_jti_revoked(jti):
                    raise credentials_exception
                iat: int | float | None = payload.get("iat")
            except JWTError:
                raise credentials_exception

            user = await get_user_by_username(db, username)
            if user is None or not user.is_active:
                raise credentials_exception
            if not _is_token_fresh(iat, user):
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
    Accepts both JWT tokens (via Authorization: Bearer header) and API keys
    (via X-API-Key header or Authorization: Bearer bb_xxx).

    Args:
        *permissions: Permission strings or Permission enum values to require

    Returns:
        A dependency function that validates permissions if auth is enabled
    """
    # Convert Permission enums to strings
    perm_strings = [p.value if isinstance(p, Permission) else p for p in permissions]

    async def permission_checker(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> User | None:
        async with async_session() as db:
            auth_enabled = await is_auth_enabled(db)
            if not auth_enabled:
                return None  # Auth disabled, allow access

            # Check for API key first (X-API-Key header)
            if x_api_key:
                api_key = await _validate_api_key(db, x_api_key)
                if api_key:
                    return None  # API key valid, allow access

            # Check for Bearer token (could be JWT or API key)
            if credentials is not None:
                token = credentials.credentials
                # Check if it's an API key (starts with bb_)
                if token.startswith("bb_"):
                    api_key = await _validate_api_key(db, token)
                    if api_key:
                        return None  # API key valid, allow access
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid API key",
                        headers={"WWW-Authenticate": "Bearer"},
                    )

                # Otherwise treat as JWT
                try:
                    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
                    username: str = payload.get("sub")
                    if username is None:
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Could not validate credentials",
                            headers={"WWW-Authenticate": "Bearer"},
                        )
                    jti: str | None = payload.get("jti")
                    if not jti or await is_jti_revoked(jti):
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Could not validate credentials",
                            headers={"WWW-Authenticate": "Bearer"},
                        )
                    iat: int | float | None = payload.get("iat")
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
                if not _is_token_fresh(iat, user):
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

            # No credentials provided
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return permission_checker


def RequirePermission(*permissions: str | Permission):
    """Convenience dependency that requires ALL specified permissions."""
    return Depends(require_permission(*permissions))


def RequirePermissionIfAuthEnabled(*permissions: str | Permission):
    """Convenience dependency that requires permissions if auth is enabled."""
    return Depends(require_permission_if_auth_enabled(*permissions))


def require_camera_stream_token_if_auth_enabled():
    """Dependency that validates a camera stream token query param when auth is enabled.

    Used for camera stream/snapshot endpoints that are loaded via <img> tags
    which cannot send Authorization headers. The frontend obtains a token from
    POST /printers/camera/stream-token and appends it as ?token=xxx.
    """

    async def checker(token: str | None = None) -> None:
        async with async_session() as db:
            if not await is_auth_enabled(db):
                return  # Auth disabled, allow access
        if not token or not await verify_camera_stream_token(token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Valid camera stream token required. Obtain one from POST /api/v1/printers/camera/stream-token",
            )

    return checker


RequireCameraStreamTokenIfAuthEnabled = Depends(require_camera_stream_token_if_auth_enabled())


def require_ownership_permission(
    all_permission: str | Permission,
    own_permission: str | Permission,
):
    """Dependency factory for ownership-based permission checks.

    - User with `all_permission` can modify any item
    - User with `own_permission` can only modify items where created_by_id == user.id
    - Ownerless items (created_by_id = null) require `all_permission`
    - API keys (via X-API-Key header or Bearer bb_xxx) get full access (can_modify_all=True)

    Returns:
        A dependency function that returns (user, can_modify_all).
        - can_modify_all=True: user can modify any item
        - can_modify_all=False: user can only modify their own items
    """
    all_perm = all_permission.value if isinstance(all_permission, Permission) else all_permission
    own_perm = own_permission.value if isinstance(own_permission, Permission) else own_permission

    async def checker(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> tuple[User | None, bool]:
        """Returns (user, can_modify_all).

        - can_modify_all=True: user can modify any item
        - can_modify_all=False: user can only modify their own items
        """
        async with async_session() as db:
            auth_enabled = await is_auth_enabled(db)
            if not auth_enabled:
                return None, True  # Auth disabled, allow all

            # Check for API key first (X-API-Key header)
            if x_api_key:
                api_key = await _validate_api_key(db, x_api_key)
                if api_key:
                    return None, True  # API key valid, allow all

            # Check for Bearer token (could be JWT or API key)
            if credentials is not None:
                token = credentials.credentials
                # Check if it's an API key (starts with bb_)
                if token.startswith("bb_"):
                    api_key = await _validate_api_key(db, token)
                    if api_key:
                        return None, True  # API key valid, allow all
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid API key",
                        headers={"WWW-Authenticate": "Bearer"},
                    )

                # Otherwise treat as JWT
                try:
                    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
                    username: str = payload.get("sub")
                    if username is None:
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Could not validate credentials",
                            headers={"WWW-Authenticate": "Bearer"},
                        )
                    jti: str | None = payload.get("jti")
                    if not jti or await is_jti_revoked(jti):
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Could not validate credentials",
                            headers={"WWW-Authenticate": "Bearer"},
                        )
                    iat: int | float | None = payload.get("iat")
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
                if not _is_token_fresh(iat, user):
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

            # No credentials provided
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return checker
