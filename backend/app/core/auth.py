from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
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
from backend.app.models.auth_ephemeral import AuthEphemeralToken, TokenType
from backend.app.models.settings import Settings
from backend.app.models.user import User

logger = logging.getLogger(__name__)

# GHSA-r2qv-8222-hqg3 (CVSS 9.9) — API key permission enforcement is allowlist-based.
#
# Until 0.2.4.x, ``_check_apikey_permissions`` only consulted the admin denylist
# below. The three documented scope flags on ``APIKey``
# (``can_read_status`` / ``can_queue`` / ``can_control_printer`` / ``can_manage_library``)
# were enforced only by ``check_permission()`` inside ``routes/webhook.py``;
# every other route used ``require_permission_if_auth_enabled`` which fell
# through to the denylist-only path, so an API key with all flags unchecked
# could still stop prints, edit queue items, and read every endpoint not in
# this set. ``require_any_permission_if_auth_enabled`` and
# ``require_ownership_permission`` did not call this helper at all, so admin
# "any-of" routes and ownership-modify routes were entirely ungated for API keys.
#
# Fix: ``_check_apikey_permissions`` now requires every requested permission to
# be present in ``_APIKEY_SCOPE_BY_PERMISSION`` (allowlist), and gates on the
# corresponding scope flag on the API key. Unmapped permissions = 403. This
# means a Permission added to ``core/permissions.py`` without a matching entry
# in ``_APIKEY_SCOPE_BY_PERMISSION`` is automatically denied for API keys —
# the previous denylist shape allowed every new Permission to silently widen
# the API-key surface.
#
# The denylist is retained for documentation / drift-detection only — its
# entries also satisfy "not in the allowlist", so they fail closed regardless.
#
# Mapping rationale (see wiki/features/api-keys.md):
#   can_read_status     → every ``*_READ`` + camera + stats + system + websocket
#   can_queue           → queue write ops + archive reprint
#   can_control_printer → physical printer + smart-plug control
#   can_manage_library  → library upload/own + MakerWorld import (separate
#                         trust level from queue management, hence its own flag)
#   admin-only          → unmapped (default-deny); covers all create/update/
#                         delete of admin resources, settings writes, user/
#                         group/api-key/backup admin ops, discovery scan,
#                         cloud auth, library ALL-ownership perms, purges
_APIKEY_SCOPE_BY_PERMISSION: dict[Permission, str] = {
    # can_read_status — read-only access to status, history, and configuration
    Permission.PRINTERS_READ: "can_read_status",
    # Legacy flat permissions retained for back-compat with custom API keys —
    # the role bootstraps no longer use these, but custom keys may still
    # carry can_read_status scope mapping. New endpoints gate on the
    # ARCHIVES_READ_OWN / _ALL split (maziggy/bambuddy-security #2).
    Permission.ARCHIVES_READ: "can_read_status",
    Permission.ARCHIVES_READ_OWN: "can_read_status",
    Permission.ARCHIVES_READ_ALL: "can_read_status",
    Permission.QUEUE_READ: "can_read_status",
    Permission.QUEUE_READ_OWN: "can_read_status",
    Permission.QUEUE_READ_ALL: "can_read_status",
    Permission.LIBRARY_READ: "can_read_status",
    Permission.LIBRARY_READ_OWN: "can_read_status",
    Permission.LIBRARY_READ_ALL: "can_read_status",
    Permission.PROJECTS_READ: "can_read_status",
    Permission.FILAMENTS_READ: "can_read_status",
    Permission.INVENTORY_READ: "can_read_status",
    Permission.INVENTORY_VIEW_ASSIGNMENTS: "can_read_status",
    Permission.INVENTORY_FORECAST_READ: "can_read_status",
    Permission.SMART_PLUGS_READ: "can_read_status",
    Permission.CAMERA_VIEW: "can_read_status",
    Permission.MAINTENANCE_READ: "can_read_status",
    Permission.KPROFILES_READ: "can_read_status",
    Permission.NOTIFICATIONS_READ: "can_read_status",
    Permission.NOTIFICATION_TEMPLATES_READ: "can_read_status",
    Permission.EXTERNAL_LINKS_READ: "can_read_status",
    Permission.FIRMWARE_READ: "can_read_status",
    Permission.AMS_HISTORY_READ: "can_read_status",
    Permission.STATS_READ: "can_read_status",
    Permission.STATS_FILTER_BY_USER: "can_read_status",
    Permission.SYSTEM_READ: "can_read_status",
    # SETTINGS_READ stays allowed via read-status so SpoolBuddy kiosks keep
    # working (they need the UI-language setting via API key).
    Permission.SETTINGS_READ: "can_read_status",
    Permission.MAKERWORLD_VIEW: "can_read_status",
    Permission.WEBSOCKET_CONNECT: "can_read_status",
    # can_queue — queue write ops + reprint (which enqueues an existing archive)
    Permission.QUEUE_CREATE: "can_queue",
    Permission.QUEUE_UPDATE_OWN: "can_queue",
    Permission.QUEUE_UPDATE_ALL: "can_queue",
    Permission.QUEUE_DELETE_OWN: "can_queue",
    Permission.QUEUE_DELETE_ALL: "can_queue",
    Permission.QUEUE_REORDER: "can_queue",
    Permission.ARCHIVES_REPRINT_OWN: "can_queue",
    Permission.ARCHIVES_REPRINT_ALL: "can_queue",
    # can_control_printer — physical-world side effects on hardware
    Permission.PRINTERS_CONTROL: "can_control_printer",
    Permission.PRINTERS_FILES: "can_control_printer",
    Permission.PRINTERS_AMS_RFID: "can_control_printer",
    Permission.PRINTERS_CLEAR_PLATE: "can_control_printer",
    Permission.SMART_PLUGS_CONTROL: "can_control_printer",
    # can_manage_library — file-manager scope (upload/rename/delete OWN library
    # entries + MakerWorld import which downloads files into the library).
    # Bulk/ALL-ownership library ops (UPDATE_ALL / DELETE_ALL / PURGE) stay
    # admin-only because they cross the user boundary.
    Permission.LIBRARY_UPLOAD: "can_manage_library",
    Permission.LIBRARY_UPDATE_OWN: "can_manage_library",
    Permission.LIBRARY_DELETE_OWN: "can_manage_library",
    Permission.MAKERWORLD_IMPORT: "can_manage_library",
    # can_manage_inventory — inventory write scope. Covers the documented
    # spool/catalog/forecast write surface AND the SpoolBuddy kiosk endpoints
    # (NFC scan, scale reading, system command/update) which used
    # INVENTORY_UPDATE as a stand-in for "kiosk write" under the prior
    # denylist model. Read-only inventory (INVENTORY_READ etc.) stays under
    # can_read_status.
    Permission.INVENTORY_CREATE: "can_manage_inventory",
    Permission.INVENTORY_UPDATE: "can_manage_inventory",
    Permission.INVENTORY_DELETE: "can_manage_inventory",
    Permission.INVENTORY_FORECAST_WRITE: "can_manage_inventory",
    # can_access_cloud — narrow opt-in scope, gated by the router-level
    # ``_cloud_api_key_gate`` and additionally enforced here so the route-
    # level ``cloud_caller(Permission.CLOUD_AUTH)`` dep also fails closed
    # when the flag is off (defence-in-depth).
    Permission.CLOUD_AUTH: "can_access_cloud",
    # ORCA_CLOUD_AUTH folds into the same ``can_access_cloud`` scope: same
    # trust dimension (third-party cloud access for profile sync), so an
    # operator who already accepted "this key can talk to clouds for the
    # owner" doesn't need a second toggle for Orca. Splitting later requires
    # a new column + migration — easy to add if the trust dimensions diverge.
    Permission.ORCA_CLOUD_AUTH: "can_access_cloud",
}

# Retained for documentation, drift-detection, and the prior "administrative
# operations" error string. Entries here are also absent from
# ``_APIKEY_SCOPE_BY_PERMISSION``, so they fail closed via the allowlist; the
# denylist is a redundant explicit "these are admin" marker, not the load-
# bearing security check.
_APIKEY_DENIED_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        # Settings administration (cred storage; rewriting these reaches SMTP/LDAP/MQTT).
        Permission.SETTINGS_UPDATE,
        Permission.SETTINGS_BACKUP,
        Permission.SETTINGS_RESTORE,
        # User / group / API-key administration.
        Permission.USERS_READ,
        Permission.USERS_CREATE,
        Permission.USERS_UPDATE,
        Permission.USERS_DELETE,
        Permission.GROUPS_READ,
        Permission.GROUPS_CREATE,
        Permission.GROUPS_UPDATE,
        Permission.GROUPS_DELETE,
        Permission.API_KEYS_CREATE,
        Permission.API_KEYS_UPDATE,
        Permission.API_KEYS_DELETE,
        Permission.API_KEYS_READ,
        # GitHub backup admin + firmware OTA.
        Permission.GITHUB_BACKUP,
        Permission.GITHUB_RESTORE,
        Permission.FIRMWARE_UPDATE,
        # Resource administration (printer/project/filament/maintenance/k-profile/etc CRUD).
        # API keys with the operational scopes can read these resources via
        # *_READ permissions but cannot mutate the catalog/registry itself.
        Permission.PRINTERS_CREATE,
        Permission.PRINTERS_UPDATE,
        Permission.PRINTERS_DELETE,
        Permission.ARCHIVES_CREATE,
        Permission.ARCHIVES_UPDATE_OWN,
        Permission.ARCHIVES_UPDATE_ALL,
        Permission.ARCHIVES_DELETE_OWN,
        Permission.ARCHIVES_DELETE_ALL,
        Permission.ARCHIVES_PURGE,
        Permission.LIBRARY_UPDATE_ALL,
        Permission.LIBRARY_DELETE_ALL,
        Permission.LIBRARY_PURGE,
        Permission.PROJECTS_CREATE,
        Permission.PROJECTS_UPDATE,
        Permission.PROJECTS_DELETE,
        Permission.FILAMENTS_CREATE,
        Permission.FILAMENTS_UPDATE,
        Permission.FILAMENTS_DELETE,
        Permission.MAINTENANCE_CREATE,
        Permission.MAINTENANCE_UPDATE,
        Permission.MAINTENANCE_DELETE,
        Permission.KPROFILES_CREATE,
        Permission.KPROFILES_UPDATE,
        Permission.KPROFILES_DELETE,
        Permission.NOTIFICATIONS_CREATE,
        Permission.NOTIFICATIONS_UPDATE,
        Permission.NOTIFICATIONS_DELETE,
        Permission.NOTIFICATIONS_USER_EMAIL,
        Permission.NOTIFICATION_TEMPLATES_UPDATE,
        Permission.EXTERNAL_LINKS_CREATE,
        Permission.EXTERNAL_LINKS_UPDATE,
        Permission.EXTERNAL_LINKS_DELETE,
        Permission.SMART_PLUGS_CREATE,
        Permission.SMART_PLUGS_UPDATE,
        Permission.SMART_PLUGS_DELETE,
        # Network scanning — operator only (no API-key scope for this).
        Permission.DISCOVERY_SCAN,
    }
)


def _resolve_apikey_scope(perm_string: str) -> str | None:
    """Return the scope-flag attribute name gating ``perm_string`` for API keys.

    None when the permission is unmapped (= admin-only / not API-key-usable).
    """
    try:
        perm = Permission(perm_string)
    except ValueError:
        return None
    return _APIKEY_SCOPE_BY_PERMISSION.get(perm)


def _check_apikey_permissions(api_key: APIKey, perm_strings: list[str], *, require_any: bool = False) -> None:
    """Raise 403 unless ``api_key`` is allowed to use ``perm_strings``.

    Allowlist semantics: every requested permission MUST be present in
    ``_APIKEY_SCOPE_BY_PERMISSION`` AND its scope flag must be True on
    ``api_key``. Unmapped permissions = administrative = 403.

    By default ALL requested permissions must pass (mirrors
    ``require_permission`` / ``require_permission_if_auth_enabled``).
    When ``require_any=True``, only one needs to pass (mirrors
    ``require_any_permission_if_auth_enabled``).
    """
    if not perm_strings:
        # Defensive: empty perm list means the dep is auth-only, not perm-gated.
        # Routes never call us with [] today, but if they did, returning here
        # would silently allow — instead, fail closed.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API keys cannot be used for unspecified permissions",
        )

    last_failure: HTTPException | None = None
    for perm_str in perm_strings:
        scope_attr = _resolve_apikey_scope(perm_str)
        if scope_attr is None:
            failure = HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="API keys cannot be used for administrative operations",
            )
        elif not getattr(api_key, scope_attr, False):
            failure = HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API key does not have '{scope_attr}' permission",
            )
        else:
            failure = None

        if failure is None and require_any:
            return  # at least one passed
        if failure is not None and not require_any:
            raise failure
        last_failure = failure

    if require_any and last_failure is not None:
        raise last_failure


def require_energy_cost_update():
    """Dependency for ``POST /settings/electricity-price`` (#1356).

    Bypasses the ``_APIKEY_DENIED_PERMISSIONS`` ``SETTINGS_UPDATE`` block for
    API keys that explicitly opt into ``can_update_energy_cost``. Full
    ``SETTINGS_UPDATE`` for API keys stays denied — this is a narrowly-scoped
    door for the Home Assistant dynamic-tariff use case documented in
    ``wiki/features/energy.md``, not a general settings-write capability.

    Accepts:
      * Auth disabled  → always allowed (matches other settings routes)
      * JWT user with ``SETTINGS_UPDATE`` permission
      * API key with ``can_update_energy_cost = True``
    """

    async def permission_checker(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> User | None:
        async with async_session() as db:
            if not await is_auth_enabled(db):
                return None

            credentials_exception = HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

            # API key path — X-API-Key header or Bearer bb_xxx
            api_key_value: str | None = None
            if x_api_key:
                api_key_value = x_api_key
            elif credentials is not None and credentials.credentials.startswith("bb_"):
                api_key_value = credentials.credentials

            if api_key_value is not None:
                api_key = await _validate_api_key(db, api_key_value)
                if api_key is None:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid API key",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                if not api_key.can_update_energy_cost:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="API key does not have 'update_energy_cost' permission",
                    )
                return None

            # JWT path
            if credentials is None:
                raise credentials_exception

            try:
                payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
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
            if not user.has_all_permissions(Permission.SETTINGS_UPDATE.value):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Missing required permissions: {Permission.SETTINGS_UPDATE.value}",
                )
            return user

    return permission_checker


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
    from backend.app.core.paths import resolve_data_dir

    data_dir = resolve_data_dir()
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
# Hard ceiling for the admin-configurable session policy (#1706). 30 days
# matches the Pydantic le=720 on AppSettings.session_max_hours; defense in
# depth so a tampered settings row can't request an absurd lifetime.
SESSION_MAX_HOURS_HARD_CEILING = 720

# HTTP Bearer token
security = HTTPBearer(auto_error=False)


async def resolve_session_max_minutes(db: AsyncSession) -> int:
    """Return the session-lifetime ceiling (minutes) honoured by login routes.

    Reads ``session_max_hours`` from the settings table (#1706), clamps to
    [1h, 720h], and falls back to the audit-default 24h if the row is
    missing, blank, or unparseable.

    DB errors are NOT caught here — login is already in a DB transaction and
    a broken DB must abort the login rather than silently extend or shrink
    the session lifetime.
    """
    default_minutes = ACCESS_TOKEN_EXPIRE_MINUTES
    result = await db.execute(select(Settings).where(Settings.key == "session_max_hours"))
    row = result.scalar_one_or_none()
    if row is None or not row.value:
        return default_minutes
    try:
        hours = int(row.value)
    except (TypeError, ValueError):
        return default_minutes
    if hours < 1:
        return default_minutes
    if hours > SESSION_MAX_HOURS_HARD_CEILING:
        hours = SESSION_MAX_HOURS_HARD_CEILING
    return hours * 60


# --- Slicer download tokens ---
# Short-lived, single-use tokens for slicer protocol handlers that can't send
# auth headers.  Stored in AuthEphemeralToken (token_type=TokenType.SLICER_DOWNLOAD)
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
                AuthEphemeralToken.token_type == TokenType.SLICER_DOWNLOAD,
                AuthEphemeralToken.expires_at < now,
            )
        )
        db.add(
            AuthEphemeralToken(
                token=token,
                token_type=TokenType.SLICER_DOWNLOAD,
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
                AuthEphemeralToken.token_type == TokenType.SLICER_DOWNLOAD,
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


WEBSOCKET_TOKEN_EXPIRE_MINUTES = 60


async def create_websocket_token(username: str | None) -> str:
    """Create a short-lived token for ``/api/v1/ws`` connections.

    Mirrors the camera-stream-token pattern: opaque random string stored
    in ``auth_ephemeral_tokens`` with type ``"websocket"`` so the WS
    endpoint can verify it *before* calling ``websocket.accept()``.

    Records the issuing principal in the ``username`` field — for JWT
    callers this is the actual username, for API-keyed callers this is
    the empty string (handled in the route layer; we accept None at this
    interface so the auth-disabled path doesn't have to fabricate one).

    The 60-minute expiry matches camera tokens: long enough to survive
    page reloads / brief disconnects, short enough that a leaked token
    is not a credential.
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=WEBSOCKET_TOKEN_EXPIRE_MINUTES)
    token = secrets.token_urlsafe(24)
    async with async_session() as db:
        # Prune expired tokens opportunistically (same shape as camera).
        await db.execute(
            delete(AuthEphemeralToken).where(
                AuthEphemeralToken.token_type == "websocket",
                AuthEphemeralToken.expires_at < now,
            )
        )
        db.add(
            AuthEphemeralToken(
                token=token,
                token_type="websocket",
                username=username or "",
                expires_at=expires_at,
            )
        )
        await db.commit()
    return token


async def verify_websocket_token(token: str) -> str | None:
    """Verify a WebSocket connect token.

    Returns the recorded ``username`` (possibly ``""`` for API-key
    callers, never ``None`` on success) when the token is valid, or
    ``None`` when it is missing / expired / unknown. The token is
    NOT consumed — a single page reload should not need a new round
    trip to mint a replacement.
    """
    now = datetime.now(timezone.utc)
    async with async_session() as db:
        result = await db.execute(
            select(AuthEphemeralToken).where(
                AuthEphemeralToken.token == token,
                AuthEphemeralToken.token_type == "websocket",
                AuthEphemeralToken.expires_at > now,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return row.username or ""


async def verify_camera_stream_token(token: str) -> bool:
    """Verify a camera stream token is valid (reusable — does not consume it).

    Tries the ephemeral 60-minute token first (the common, browser-bound case)
    and falls through to long-lived tokens (#1108) for HA / kiosk integrations
    that paste a token once and expect it to keep working for days.
    """
    now = datetime.now(timezone.utc)
    async with async_session() as db:
        result = await db.execute(
            select(AuthEphemeralToken).where(
                AuthEphemeralToken.token == token,
                AuthEphemeralToken.token_type == "camera_stream",
                AuthEphemeralToken.expires_at > now,
            )
        )
        if result.scalar_one_or_none() is not None:
            return True

        # Long-lived path. Imported lazily so the auth module stays importable
        # at startup before the long_lived_tokens model is registered.
        from backend.app.services.long_lived_tokens import verify_token as verify_long_lived

        record = await verify_long_lived(db, token, scope="camera_stream")
        return record is not None


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
    from a pre-iat code path whose max TTL at the time (24 h) has long since
    expired. The post-#1706 admin-set ceiling does not relax this — an iat-less
    token still cannot have been issued by current code.
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
    """Check if authentication is enabled.

    Fails CLOSED on database errors. A previous version of this function
    caught every exception and returned False — silently treating an
    unavailable database as "auth is disabled" and granting unauthenticated
    access to every endpoint that called it (GHSA-6mf4-q26m-47pv, CVSS 9.8).
    An attacker could trigger that fail-open by flooding /api/v1/auth/login
    to exhaust the process's file-descriptor budget, then hit a protected
    endpoint during the window where the next DB op raised.

    Legitimate "auth was never configured" still returns False — the
    settings row is simply absent, ``scalar_one_or_none`` returns None,
    no exception. Any OTHER failure (connection error, fd exhaustion,
    schema mismatch, …) propagates so the caller can deny the request
    (503 / 500). Fail-closed is the only safe default for an auth probe.
    """
    result = await db.execute(select(Settings).where(Settings.key == "auth_enabled"))
    setting = result.scalar_one_or_none()
    if setting is None:
        return False
    return setting.value.lower() == "true"


async def _user_from_api_key(db: AsyncSession, api_key: APIKey) -> User | None:
    """Resolve the owner of a validated API key, or None for legacy ownerless keys.

    Cloud routes (and any route that needs caller identity) read the returned
    User to look up per-user state like ``cloud_token``. Legacy keys created
    before #1182 have ``user_id IS NULL`` and stay anonymous — they keep working
    against non-cloud routes for backward compatibility, but cloud routes will
    surface a "recreate this key" error rather than 200 with empty results.
    """
    if api_key.user_id is None:
        return None
    result = await db.execute(select(User).where(User.id == api_key.user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        # CASCADE on user delete should prevent a dangling user_id, but if
        # someone manually deactivates the owner the key shouldn't suddenly
        # gain an "anonymous" identity — drop the request to None so cloud
        # access fails closed.
        return None
    return user


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
    except Exception as e:  # SEC-AUTH-EXC: validation failure returns None; every caller treats None as "invalid key" → 401 (fail-closed)
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
    (via X-API-Key header or Authorization: Bearer bb_xxx). API keys return
    None for backward compatibility — routes that need the API-key owner (i.e.
    cloud routes for #1182) resolve it via their own router-level dependency
    that stashes ``request.state.api_key_owner``. Returning the owner here
    instead would silently grant API-keyed callers access to every route that
    fences via ``if current_user is None``, which is a wider surface than
    #1182 was designed to expose.
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
    """Dependency factory that requires admin role if auth is enabled.

    GHSA-r2qv follow-up (audit pattern P3): explicitly fail-closed for API
    keys. The previous implementation chained on ``require_auth_if_enabled``
    which returns ``None`` for *both* "auth disabled" *and* "valid API
    key" — the inner ``admin_checker`` then treated ``None`` as auth-
    disabled and admitted the caller. If any route had ever adopted this
    dep, any API key with no scope flags set would have satisfied an
    admin requirement. The dep distinguishes the two cases by consulting
    ``is_auth_enabled`` directly and rejecting API-keyed requests with
    403. "Admin" requires a user-identity role, which API keys do not
    carry.

    Admin semantics: uses ``User.is_admin`` (``role == "admin"`` OR
    Administrators-group membership) so a default-install operator who
    was made admin by being added to Administrators rather than by
    flipping the legacy role column passes. Earlier this check looked
    only at ``role`` and would have locked group-only admins out of the
    user-management routes once those routes started requiring it.
    """

    async def admin_checker(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> User | None:
        async with async_session() as db:
            if not await is_auth_enabled(db):
                return None  # Auth disabled — no role to check.

            # Reject API-keyed requests up front: admin is a user-role
            # concept, not a key-scope concept. The right path for
            # admin-equivalent API-key access is a specific Permission
            # (e.g. SETTINGS_UPDATE) gated by the allowlist, not the
            # admin role.
            if x_api_key or (credentials and credentials.credentials.startswith("bb_")):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin operations require a user role; API keys cannot be admins",
                )

            # Standard JWT path: validate and require admin role.
            if credentials is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            try:
                payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
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
            if not user.is_admin:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Requires admin role",
                )
            return user

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

    # Pre-filter by key_prefix to avoid O(n) pbkdf2 hashes across all enabled keys.
    key_lookup = api_key_value[:8] if len(api_key_value) >= 8 else api_key_value
    result = await db.execute(
        select(APIKey).where(
            APIKey.enabled.is_(True),
            APIKey.key_prefix.like(
                key_lookup.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",
                escape="\\",
            ),
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


async def caller_is_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> bool:
    """Return True when the request is authenticated via API key (X-API-Key or Bearer bb_xxx)."""
    if x_api_key:
        return True
    return credentials is not None and credentials.credentials.startswith("bb_")


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
                    _check_apikey_permissions(api_key, perm_strings)
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
                    _check_apikey_permissions(api_key, perm_strings)
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

            # Check for API key first (X-API-Key header). API-keyed requests
            # bypass the JWT permission check entirely — their scopes live on
            # the APIKey row (can_queue / can_control_printer / can_read_status
            # / can_access_cloud / printer_ids), and the dep returns None so
            # routes don't gain a synthetic User identity that would grant
            # access to fenced surfaces like long-lived-token management.
            # Cloud routes (#1182) resolve the API-key owner separately via
            # their own router-level dependency; see ``cloud.py``.
            if x_api_key:
                api_key = await _validate_api_key(db, x_api_key)
                if api_key:
                    _check_apikey_permissions(api_key, perm_strings)
                    return None  # API key valid, allow access

            # Check for Bearer token (could be JWT or API key)
            if credentials is not None:
                token = credentials.credentials
                # Check if it's an API key (starts with bb_)
                if token.startswith("bb_"):
                    api_key = await _validate_api_key(db, token)
                    if api_key:
                        _check_apikey_permissions(api_key, perm_strings)
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


def require_any_permission_if_auth_enabled(*permissions: str | Permission):
    """Dependency factory that requires AT LEAST ONE of the given permissions when auth is enabled."""
    perm_strings = [p.value if isinstance(p, Permission) else p for p in permissions]

    async def checker(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> User | None:
        async with async_session() as db:
            auth_enabled = await is_auth_enabled(db)
            if not auth_enabled:
                return None

            if x_api_key:
                api_key = await _validate_api_key(db, x_api_key)
                if api_key:
                    # GHSA-r2qv-8222-hqg3: previously returned None unconditionally,
                    # letting any valid API key satisfy admin "any-of" route
                    # dependencies. require_any → at-least-one must pass the scope check.
                    _check_apikey_permissions(api_key, perm_strings, require_any=True)
                    return None

            if credentials is not None:
                token = credentials.credentials
                if token.startswith("bb_"):
                    api_key = await _validate_api_key(db, token)
                    if api_key:
                        _check_apikey_permissions(api_key, perm_strings, require_any=True)
                        return None
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid API key",
                        headers={"WWW-Authenticate": "Bearer"},
                    )

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

                if not user.has_any_permission(*perm_strings):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Missing required permissions: {', '.join(perm_strings)}",
                    )
                return user

            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return checker


def RequireAnyPermissionIfAuthEnabled(*permissions: str | Permission):
    """Convenience dependency that requires AT LEAST ONE of the given permissions when auth is enabled."""
    return Depends(require_any_permission_if_auth_enabled(*permissions))


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

    - User with ``all_permission`` can modify any item
    - User with ``own_permission`` can only modify items where created_by_id == user.id
    - Ownerless items (created_by_id = null) require ``all_permission``
    - API keys (via X-API-Key header or Bearer bb_xxx) must satisfy the
      ``all_permission``'s API-key scope flag (e.g. ``can_queue`` for
      ``QUEUE_UPDATE_ALL``) and then receive ``can_modify_all=True``.
      OWN/ALL ownership pairs map to the same scope flag in
      ``_APIKEY_SCOPE_BY_PERMISSION`` so checking ``all_permission`` is the
      correct gate; API keys have no per-row ownership identity. Pre-
      GHSA-r2qv-8222-hqg3 fix this returned ``(None, True)`` for any valid
      key with no scope check — see ``core/auth.py`` allowlist commentary.

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

            # GHSA-r2qv-8222-hqg3: previously API keys received (None, True)
            # unconditionally on ownership-modify routes — a "queue-only" key
            # could delete any user's archives, library files, queue items.
            # OWN and ALL ownership perms both map to the same scope flag
            # (e.g. both QUEUE_UPDATE_OWN and QUEUE_UPDATE_ALL → can_queue),
            # so checking ``all_perm`` against the api_key's scope is the
            # correct gate. API keys don't have per-row ownership identity, so
            # on pass we keep can_modify_all=True (preserves prior intent,
            # narrows access to keys with the right scope flag).
            if x_api_key:
                api_key = await _validate_api_key(db, x_api_key)
                if api_key:
                    _check_apikey_permissions(api_key, [all_perm])
                    return None, True

            # Check for Bearer token (could be JWT or API key)
            if credentials is not None:
                token = credentials.credentials
                # Check if it's an API key (starts with bb_)
                if token.startswith("bb_"):
                    api_key = await _validate_api_key(db, token)
                    if api_key:
                        _check_apikey_permissions(api_key, [all_perm])
                        return None, True
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
