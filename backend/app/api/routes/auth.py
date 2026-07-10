import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt as _jwt
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from jwt.exceptions import PyJWTError
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.api.routes.settings import get_external_login_url
from backend.app.core.auth import (
    ALGORITHM,
    SECRET_KEY,
    Permission,
    RequirePermissionIfAuthEnabled,
    _is_token_fresh,
    _validate_api_key,
    authenticate_user,
    authenticate_user_by_email,
    create_access_token,
    create_websocket_token,
    get_current_active_user,
    get_password_hash,
    get_user_by_email,
    get_user_by_username,
    is_jti_revoked,
    resolve_session_max_minutes,
    revoke_jti,
    security,
)
from backend.app.core.database import async_session, get_db
from backend.app.core.permissions import ALL_PERMISSIONS
from backend.app.models.auth_ephemeral import AuthEphemeralToken, AuthRateLimitEvent, EventType, TokenType
from backend.app.models.group import Group
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.schemas.auth import (
    EncryptionRowCounts,
    EncryptionStatusResponse,
    ForgotPasswordConfirmRequest,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    GroupBrief,
    LDAPProvisionRequest,
    LDAPSearchResultResponse,
    LoginRequest,
    LoginResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
    SetupRequest,
    SetupResponse,
    SMTPSettings,
    TestSMTPRequest,
    TestSMTPResponse,
    UserResponse,
    _validate_password_complexity,
)
from backend.app.services.email_service import (
    create_password_reset_link_email_from_template,
    get_smtp_settings,
    save_smtp_settings,
    send_email,
)

_logger = logging.getLogger(__name__)


def _user_to_response(user: User) -> UserResponse:
    """Convert a User model to UserResponse schema."""
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        is_admin=user.is_admin,
        auth_source=getattr(user, "auth_source", "local"),
        groups=[GroupBrief(id=g.id, name=g.name) for g in user.groups],
        permissions=sorted(user.get_permissions()),
        created_at=user.created_at.isoformat(),
    )


def _api_key_to_user_response(api_key) -> UserResponse:
    """Create a synthetic admin UserResponse for a valid API key."""
    return UserResponse(
        id=0,
        username=f"api-key:{api_key.key_prefix}",
        email=None,
        role="admin",
        is_active=True,
        is_admin=True,
        groups=[],
        permissions=sorted(ALL_PERMISSIONS),
        created_at=api_key.created_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# M-R9-A: Real client IP resolution for rate limiting behind reverse proxies.
# Set TRUSTED_PROXY_IPS (comma-separated) to enable X-Forwarded-For trust.
# Without this env var client.host is used directly (safe default).
# ---------------------------------------------------------------------------
_TRUSTED_PROXY_IPS: frozenset[str] = frozenset(
    ip.strip() for ip in os.environ.get("TRUSTED_PROXY_IPS", "").split(",") if ip.strip()
)


# #1589: read at call time, not import time, so tests can monkeypatch os.environ
# between cases without re-importing the module.
def _local_login_env_bypass() -> bool:
    """Return True when ``BAMBUDDY_LOCAL_LOGIN`` env var is set truthy.

    Bypasses the ``local_login_enabled`` DB setting on the local-credentials
    code path AND the forgot-password endpoint so a server admin can recover
    an install whose SSO provider is unreachable. Accepted truthy values:
    ``true``, ``1``, ``yes`` (case-insensitive).
    """
    return os.environ.get("BAMBUDDY_LOCAL_LOGIN", "").strip().lower() in {"true", "1", "yes"}


def _get_client_ip(request: Request) -> str:
    """Return the real client IP for rate-limiting purposes.

    When TRUSTED_PROXY_IPS is configured and the direct TCP peer is a trusted
    proxy, X-Forwarded-For is evaluated right-to-left: the rightmost IP that is
    NOT itself a trusted proxy is the true client address (M-R10-A fix).

    Standard nginx with proxy_add_x_forwarded_for *appends* the client IP, so
    the rightmost entry is always the one added by the last trusted proxy —
    i.e. the real client. Walking right-to-left and skipping known proxies is
    safe for multi-hop chains as well.

    Falls back to request.client.host when TRUSTED_PROXY_IPS is unset (direct
    deployment without a reverse proxy).
    """
    # I5: Use a per-request unique token instead of "unknown" when the transport
    # layer provides no client address.  This prevents all such requests from
    # sharing one rate-limit bucket, and avoids collision with a literal username
    # "unknown".  The token is not stable across requests, which is intentional:
    # we cannot track the IP so we also cannot rate-limit by it meaningfully.
    direct_ip = request.client.host if request.client else f"__no_ip_{secrets.token_hex(8)}__"
    if _TRUSTED_PROXY_IPS and direct_ip in _TRUSTED_PROXY_IPS:
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        ips = [ip.strip() for ip in forwarded_for.split(",") if ip.strip()]
        # Walk right-to-left; skip IPs that belong to trusted proxies.
        for ip in reversed(ips):
            if ip not in _TRUSTED_PROXY_IPS:
                return ip
        # Edge case: every entry is a trusted proxy — fall back to leftmost.
        if ips:
            return ips[0]
    return direct_ip


router = APIRouter(prefix="/auth", tags=["authentication"])


async def is_auth_enabled(db: AsyncSession) -> bool:
    """Check if authentication is enabled."""
    result = await db.execute(select(Settings).where(Settings.key == "auth_enabled"))
    setting = result.scalar_one_or_none()
    if setting is None:
        return False
    return setting.value.lower() == "true"


async def is_advanced_auth_enabled(db: AsyncSession) -> bool:
    """Check if advanced authentication is enabled."""
    result = await db.execute(select(Settings).where(Settings.key == "advanced_auth_enabled"))
    setting = result.scalar_one_or_none()
    if setting is None:
        return False
    return setting.value.lower() == "true"


async def set_advanced_auth_enabled(db: AsyncSession, enabled: bool) -> None:
    """Set advanced authentication enabled status."""
    from backend.app.core.db_dialect import upsert_setting

    await upsert_setting(db, Settings, "advanced_auth_enabled", "true" if enabled else "false")


async def set_auth_enabled(db: AsyncSession, enabled: bool) -> None:
    """Set authentication enabled status."""
    from backend.app.core.db_dialect import upsert_setting

    await upsert_setting(db, Settings, "auth_enabled", "true" if enabled else "false")
    # Note: Don't commit here - let get_db handle it or commit explicitly in the route


async def is_setup_completed(db: AsyncSession) -> bool:
    """Check if setup has been completed."""
    result = await db.execute(select(Settings).where(Settings.key == "setup_completed"))
    setting = result.scalar_one_or_none()
    return setting and setting.value.lower() == "true"


async def set_setup_completed(db: AsyncSession, completed: bool) -> None:
    """Set setup completed status."""
    from backend.app.core.db_dialect import upsert_setting

    await upsert_setting(db, Settings, "setup_completed", "true" if completed else "false")
    # Note: Don't commit here - let get_db handle it or commit explicitly in the route


@router.post("/setup", response_model=SetupResponse)
async def setup_auth(request: SetupRequest, db: AsyncSession = Depends(get_db)):
    """First-time setup: enable/disable authentication and create admin user."""
    import logging

    logger = logging.getLogger(__name__)

    try:
        # If auth is currently enabled, block unauthenticated setup changes.
        # Use the admin panel (/disable endpoint) to modify auth when it's already on.
        if await is_auth_enabled(db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Authentication is already configured. Use the admin panel to modify auth settings.",
            )

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

                # Enforce password complexity only when actually creating a new admin.
                # Schema-level validation was removed so that re-enabling auth with an
                # existing admin (or LDAP) doesn't reject whatever placeholder the form sends.
                try:
                    _validate_password_complexity(request.admin_password)
                except ValueError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=str(exc),
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
                except Exception as e:  # SEC-AUTH-EXC: rollback + raise 500 (fail-closed); no user is created on error
                    await db.rollback()
                    logger.error("Failed to create admin user: %s", e, exc_info=True)
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to create admin user",
                    )

        if request.auth_enabled:
            # Enabling auth flips cloud-credential storage from the global
            # Settings rows to User.cloud_token. Carry any token linked while
            # auth was off across to the owning admin, or /cloud/* silently
            # degrades to local presets with no indication anything broke
            # (#2530). Only migrate when there is exactly one obvious owner:
            # handing another admin's session a Bambu credential is not a
            # guess worth making.
            from backend.app.api.routes.cloud import (
                get_stored_token,
                migrate_global_cloud_token_to_user,
            )

            if admin_created:
                cloud_owner = admin_user
            elif len(existing_admin_users) == 1:
                cloud_owner = existing_admin_users[0]
            else:
                cloud_owner = None

            if cloud_owner is not None:
                if await migrate_global_cloud_token_to_user(db, cloud_owner):
                    logger.info("Migrated global Bambu Cloud credentials to admin '%s'", cloud_owner.username)
            else:
                global_token, _, _ = await get_stored_token(db, None)
                if global_token:
                    logger.warning(
                        "A Bambu Cloud account is linked globally but %s admins exist; "
                        "leaving it unassigned. Re-link the account from Settings after login.",
                        len(existing_admin_users),
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
    except Exception as e:  # SEC-AUTH-EXC: rollback + raise 500 (fail-closed); setup state stays unchanged
        logger.error("Setup error: %s", e, exc_info=True)
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Setup failed",
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
        # Mirror of the migration in setup_auth: with auth off the cloud routes
        # read the global Settings rows and never look at User.cloud_token, so
        # hand this admin's credential over rather than stranding it (#2530).
        from backend.app.api.routes.cloud import migrate_user_cloud_token_to_global

        if await migrate_user_cloud_token_to_global(db, user):
            logger.info("Migrated Bambu Cloud credentials from admin '%s' to global storage", user.username)

        await set_auth_enabled(db, False)
        await db.commit()
        logger.info("Authentication disabled by admin user: %s", user.username)
        return {"message": "Authentication disabled successfully", "auth_enabled": False}
    except Exception as e:  # SEC-AUTH-EXC: rollback + raise 500 (fail-closed); auth_enabled stays at its prior value
        await db.rollback()
        logger.error("Failed to disable authentication: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to disable authentication",
        )


@router.post("/login", response_model=LoginResponse)
async def login(raw_request: Request, request: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    """Login and get access token.

    Supports username or email-based login. Username lookup is case-insensitive.

    When 2FA is enabled for the user the response contains ``requires_2fa=True``
    and a short-lived ``pre_auth_token`` instead of the final JWT.  The client
    must then call ``POST /auth/2fa/verify`` (or first ``POST /auth/2fa/email/send``
    to trigger an email OTP) to obtain the real access token.
    """
    # Check if auth is enabled
    auth_enabled = await is_auth_enabled(db)
    if not auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authentication is not enabled",
        )

    # Rate-limit repeated login failures — two independent buckets (M-R5-B / M-R6-A):
    #   1. Per-username (10/15 min): prevents password brute-force on a known account.
    #   2. Per-IP     (20/15 min): prevents an attacker from locking out arbitrary accounts
    #      (DoS) by sending failures for many usernames from a single address.
    from backend.app.api.routes.mfa import MAX_LOGIN_ATTEMPTS, check_rate_limit, record_failed_attempt

    await check_rate_limit(db, request.username, event_type=EventType.LOGIN_ATTEMPT, max_attempts=MAX_LOGIN_ATTEMPTS)
    client_ip = _get_client_ip(raw_request)
    await check_rate_limit(db, client_ip, event_type=EventType.LOGIN_IP, max_attempts=20)

    # Initialize `user` up front so every downstream branch can read/write
    # it without UnboundLocalError. The LDAP success path sets it inside its
    # own block; the local-credentials and email-credentials paths set it
    # below. The original code relied on the local-credentials path running
    # unconditionally to bind `user`; #1589 made that path skippable, so the
    # init has to live here.
    user = None
    # Check if LDAP is enabled
    ldap_user = None
    ldap_settings = await _get_ldap_settings(db)
    if ldap_settings:
        try:
            from backend.app.services.ldap_service import (
                authenticate_ldap_user,
                parse_ldap_config,
            )

            ldap_config = parse_ldap_config(ldap_settings)
            if ldap_config:
                ldap_user = authenticate_ldap_user(ldap_config, request.username, request.password)
                if ldap_user:
                    # LDAP auth succeeded — find or create local user
                    user = await get_user_by_username(db, ldap_user.username)
                    if user and user.auth_source != "ldap":
                        # Username exists as local user — don't override
                        user = None
                        ldap_user = None
                    elif not user:
                        if not ldap_config.auto_provision:
                            # User doesn't exist and auto-provision is off
                            ldap_user = None
                        else:
                            # Auto-provision LDAP user
                            user = await _provision_ldap_user(db, ldap_user, ldap_config)

                    if user and ldap_user:
                        # Update email and group mappings on each login
                        await _sync_ldap_user(db, user, ldap_user, ldap_config)
        except Exception as e:  # SEC-AUTH-EXC: LDAP failure sets ldap_user=None, downstream local-auth path runs with its own credential check (no implicit grant)
            import logging

            logging.getLogger(__name__).warning("LDAP authentication error, falling back to local: %s", e)
            ldap_user = None

    # #1589: local username/password gate. LDAP keeps its own switch
    # (ldap_enabled) and is not affected — a delegated directory has its
    # own policy and lockouts and is closer to SSO than to local creds.
    # The env-var BAMBUDDY_LOCAL_LOGIN=true bypasses this gate so a server
    # admin can recover an install whose SSO provider is unreachable
    # without editing the DB.
    from backend.app.models.settings import Settings as _Settings_for_local_login

    local_login_allowed = ldap_user is not None or _local_login_env_bypass()
    if not local_login_allowed:
        setting_row = await db.execute(
            select(_Settings_for_local_login).where(_Settings_for_local_login.key == "local_login_enabled")
        )
        row = setting_row.scalar_one_or_none()
        # Default True when the row is absent — matches AppSettings default
        # so fresh installs and tests behave like every release before #1589.
        local_login_allowed = row is None or row.value.lower() == "true"

    # Try username-based authentication (skip if already authenticated via LDAP)
    if not ldap_user and local_login_allowed:
        user = await authenticate_user(db, request.username, request.password)

    # If username auth failed and advanced auth is enabled, try email-based authentication
    if not user and not ldap_user and local_login_allowed:
        advanced_auth = await is_advanced_auth_enabled(db)
        if advanced_auth:
            user = await authenticate_user_by_email(db, request.username, request.password)

    if not user:
        await record_failed_attempt(db, request.username, event_type=EventType.LOGIN_ATTEMPT)
        await record_failed_attempt(db, client_ip, event_type=EventType.LOGIN_IP)
        # Same generic 401 either way — never tell the client whether the
        # username exists or whether local login was disabled. The Settings
        # UI and /auth/advanced-auth/status are the channels for that state;
        # leaking it here would help credential-stuffing distinguish "local
        # disabled" from "wrong password" across an install fleet.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Reload user with groups for proper permission calculation
    result = await db.execute(select(User).where(User.id == user.id).options(selectinload(User.groups)))
    user = result.scalar_one()

    # L-R6-A: Password was correct — reset login failure counters for both buckets
    from backend.app.api.routes.mfa import clear_failed_attempts

    await clear_failed_attempts(db, user.username, event_type=EventType.LOGIN_ATTEMPT)
    await clear_failed_attempts(db, client_ip, event_type=EventType.LOGIN_IP)

    # --- 2FA check ---
    # Determine which 2FA methods are active for this user.

    from backend.app.models.settings import Settings as _Settings
    from backend.app.models.user_totp import UserTOTP

    totp_result = await db.execute(select(UserTOTP).where(UserTOTP.user_id == user.id))
    user_totp = totp_result.scalar_one_or_none()
    totp_enabled = user_totp is not None and user_totp.is_enabled

    email_2fa_result = await db.execute(select(_Settings).where(_Settings.key == f"user_{user.id}_email_2fa_enabled"))
    email_2fa_setting = email_2fa_result.scalar_one_or_none()
    email_otp_enabled = (
        email_2fa_setting is not None and email_2fa_setting.value.lower() == "true" and user.email is not None
    )

    if totp_enabled or email_otp_enabled:
        # Import here to avoid circular imports
        from backend.app.api.routes.mfa import create_pre_auth_token

        # Bind the pre_auth_token to an HttpOnly cookie so XSS cannot steal the
        # token from JS memory and complete 2FA from a different client.
        challenge_id = secrets.token_urlsafe(32)
        pre_auth_token = await create_pre_auth_token(db, user.username, challenge_id=challenge_id)
        response.set_cookie(
            key="2fa_challenge",
            value=challenge_id,
            httponly=True,
            # H-1: only transmit over HTTPS so the binding cookie can't be intercepted
            # on mixed-content deployments.  Falls back to False on plain HTTP so tests
            # and local development still work (the client wouldn't send it otherwise).
            secure=raw_request.url.scheme == "https",
            samesite="lax",
            max_age=300,
            path="/api/v1/auth/2fa",
        )
        methods: list[str] = []
        if totp_enabled:
            methods.append("totp")
        if email_otp_enabled:
            methods.append("email")
        # Backup codes are always available when TOTP is set up
        if totp_enabled:
            methods.append("backup")

        return LoginResponse(
            requires_2fa=True,
            pre_auth_token=pre_auth_token,
            two_fa_methods=methods,
        )

    # No 2FA — issue full token immediately. Session lifetime honours the
    # admin-configurable ceiling (#1706); resolver clamps to [1h, 720h].
    access_token_expires = timedelta(minutes=await resolve_session_max_minutes(db))
    access_token = create_access_token(data={"sub": user.username}, expires_delta=access_token_expires)

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        user=_user_to_response(user),
    )


@router.post("/ws-token")
async def mint_websocket_token(
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.WEBSOCKET_CONNECT),
):
    """Mint a short-lived token for ``/api/v1/ws`` connections (GHSA-r2qv follow-up).

    The WebSocket endpoint cannot read ``Authorization`` headers from
    browsers (the WebSocket handshake does not let JS attach custom
    headers), so we use the same opaque-token-in-query-param pattern
    as ``/camera/stream`` — the token is minted here behind the standard
    permission gate, then appended as ``?token=<value>`` on the
    ``ws://...`` URL. The WebSocket endpoint validates it *before*
    calling ``websocket.accept()``.

    Returns ``{"token": <opaque string>}``. The token is valid for 60
    minutes; the SPA refreshes it on reconnect if expired. API keys can
    mint tokens too — their scope flags decide whether ``WEBSOCKET_CONNECT``
    passes via the standard allowlist (``can_read_status`` covers it).
    """
    username = current_user.username if current_user is not None else None
    return {"token": await create_websocket_token(username)}


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    db: AsyncSession = Depends(get_db),
):
    """Get current user information.

    Accepts JWT tokens (via Authorization: Bearer header) and API keys
    (via X-API-Key header or Authorization: Bearer bb_xxx).
    API keys return a synthetic admin user with all permissions.
    """
    import jwt
    from jwt.exceptions import PyJWTError as JWTError

    # Check for API key via X-API-Key header
    if x_api_key:
        api_key = await _validate_api_key(db, x_api_key)
        if api_key:
            return _api_key_to_user_response(api_key)

    # Check for Bearer token (could be JWT or API key)
    if credentials is not None:
        token = credentials.credentials
        # Check if it's an API key (starts with bb_)
        if token.startswith("bb_"):
            api_key = await _validate_api_key(db, token)
            if api_key:
                return _api_key_to_user_response(api_key)
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
            if not jti or await is_jti_revoked(jti):  # B1: logout bypass fix
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
        # Reload with groups for proper permission calculation
        result = await db.execute(select(User).where(User.id == user.id).options(selectinload(User.groups)))
        user = result.scalar_one()
        # L-R8-A: reject tokens issued before the last password change
        if not _is_token_fresh(iat, user):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return _user_to_response(user)

    # No credentials provided
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/logout")
async def logout(
    raw_request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
):
    """Logout — revokes the current JWT so it cannot be reused after logout."""
    if credentials is not None:
        raw_token = credentials.credentials
        # Nit2: Verify signature before revoking to prevent DoS-revoke attacks
        # (an attacker crafting a token with an arbitrary jti cannot force
        # revocation of a legitimate token because the signature check rejects it).
        # Expired tokens are still accepted — the user is logging out and their
        # token may have just expired; we still want to record the revocation.
        try:
            verified = _jwt.decode(
                raw_token,
                SECRET_KEY,
                algorithms=[ALGORITHM],
                options={"verify_exp": False},  # allow expired tokens at logout
            )
            jti: str | None = verified.get("jti")
            exp = verified.get("exp")
            username: str | None = verified.get("sub")
            if jti and exp:
                expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
                try:
                    await revoke_jti(jti, expires_at, username)
                except Exception as exc:  # SEC-AUTH-EXC: JTI-revoke failure on logout is logged only; logout removes access, never grants it (token stays valid until natural expiry — degraded but never escalation)
                    _logger.error("Failed to revoke JTI on logout for user %s: %s", username, exc)
        except PyJWTError:
            client_ip = _get_client_ip(raw_request)
            ua = raw_request.headers.get("user-agent", "<unknown>")
            _logger.error(
                "Logout received token that failed signature verification — skipping revocation "
                "(possible tamper attempt; ip=%s ua=%s)",
                client_ip,
                ua,
            )

    return {"message": "Logged out successfully"}


# Advanced Authentication Endpoints


@router.post("/smtp/test", response_model=TestSMTPResponse)
async def test_smtp_connection(
    test_request: TestSMTPRequest,
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
    db: AsyncSession = Depends(get_db),
):
    """Test SMTP connection using saved settings (admin only when auth enabled)."""
    import logging

    logger = logging.getLogger(__name__)

    try:
        smtp_settings = await get_smtp_settings(db)
        if not smtp_settings:
            return TestSMTPResponse(success=False, message="SMTP settings not configured. Save SMTP settings first.")

        # Send test email
        send_email(
            smtp_settings=smtp_settings,
            to_email=test_request.test_recipient,
            subject="BamBuddy SMTP Test",
            body_text="This is a test email from BamBuddy. If you received this, your SMTP settings are working correctly!",
            body_html="<p>This is a test email from <strong>BamBuddy</strong>.</p><p>If you received this, your SMTP settings are working correctly!</p>",
        )

        logger.info(f"Test email sent successfully to {test_request.test_recipient}")
        return TestSMTPResponse(success=True, message="Test email sent successfully")
    except Exception as e:  # SEC-AUTH-EXC: SMTP test diagnostic returns success=False; no auth-relevant outcome (route is admin-gated by SETTINGS_UPDATE upstream)
        logger.error("Failed to send test email: %s", e)
        return TestSMTPResponse(success=False, message="Failed to send test email")


@router.get("/smtp", response_model=SMTPSettings | None)
async def get_smtp_config(
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
    db: AsyncSession = Depends(get_db),
):
    """Get SMTP settings (admin only when auth enabled). Password is not returned."""
    smtp_settings = await get_smtp_settings(db)
    if smtp_settings:
        # Don't return password in response
        smtp_settings.smtp_password = None
    return smtp_settings


@router.post("/smtp", response_model=dict)
async def save_smtp_config(
    smtp_settings: SMTPSettings,
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
    db: AsyncSession = Depends(get_db),
):
    """Save SMTP settings (admin only when auth enabled)."""
    import logging

    logger = logging.getLogger(__name__)

    try:
        await save_smtp_settings(db, smtp_settings)
        await db.commit()
        logger.info(f"SMTP settings updated by admin user: {current_user.username if current_user else 'anonymous'}")
        return {"message": "SMTP settings saved successfully"}
    except Exception as e:  # SEC-AUTH-EXC: rollback + raise 500 (fail-closed); SMTP settings unchanged on error
        await db.rollback()
        logger.error("Failed to save SMTP settings: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save SMTP settings",
        )


@router.post("/advanced-auth/enable", response_model=dict)
async def enable_advanced_auth(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Enable advanced authentication (admin only).

    Requires SMTP settings to be configured and tested first.
    """
    import logging

    logger = logging.getLogger(__name__)

    # Reload user with groups for proper is_admin check
    result = await db.execute(select(User).where(User.id == current_user.id).options(selectinload(User.groups)))
    user = result.scalar_one()

    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can enable advanced authentication",
        )

    # Verify SMTP settings are configured
    smtp_settings = await get_smtp_settings(db)
    if not smtp_settings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SMTP settings must be configured before enabling advanced authentication",
        )

    try:
        await set_advanced_auth_enabled(db, True)
        await db.commit()
        logger.info(f"Advanced authentication enabled by admin user: {user.username}")
        return {"message": "Advanced authentication enabled successfully", "advanced_auth_enabled": True}
    except Exception as e:  # SEC-AUTH-EXC: rollback + raise 500 (fail-closed); advanced-auth setting unchanged on error
        await db.rollback()
        logger.error("Failed to enable advanced authentication: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to enable advanced authentication",
        )


@router.post("/advanced-auth/disable", response_model=dict)
async def disable_advanced_auth(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Disable advanced authentication (admin only)."""
    import logging

    logger = logging.getLogger(__name__)

    # Reload user with groups for proper is_admin check
    result = await db.execute(select(User).where(User.id == current_user.id).options(selectinload(User.groups)))
    user = result.scalar_one()

    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can disable advanced authentication",
        )

    try:
        await set_advanced_auth_enabled(db, False)
        await db.commit()
        logger.info(f"Advanced authentication disabled by admin user: {user.username}")
        return {"message": "Advanced authentication disabled successfully", "advanced_auth_enabled": False}
    except Exception as e:  # SEC-AUTH-EXC: rollback + raise 500 (fail-closed); advanced-auth setting unchanged on error
        await db.rollback()
        logger.error("Failed to disable advanced authentication: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to disable advanced authentication",
        )


@router.get("/advanced-auth/status")
async def get_advanced_auth_status(db: AsyncSession = Depends(get_db)):
    """Get advanced authentication status.

    Surfaces ``local_login_enabled`` and ``autologin_provider_id`` (#1589)
    so the LoginPage can decide whether to render the credentials form and
    whether to redirect unauthenticated visitors directly to an SSO
    provider, in a single query. ``BAMBUDDY_LOCAL_LOGIN=true`` flips the
    reported value back to True so the recovery path is visible.
    """
    from backend.app.models.oidc_provider import OIDCProvider
    from backend.app.models.settings import Settings as _Settings_for_local_login

    advanced_auth_enabled = await is_advanced_auth_enabled(db)
    smtp_configured = await get_smtp_settings(db) is not None

    setting_row = await db.execute(
        select(_Settings_for_local_login).where(_Settings_for_local_login.key == "local_login_enabled")
    )
    row = setting_row.scalar_one_or_none()
    db_local_enabled = row is None or row.value.lower() == "true"
    local_login_enabled = db_local_enabled or _local_login_env_bypass()

    # Autologin provider must be both flagged AND enabled — disabling a
    # provider should not silently keep redirecting visitors to it.
    autologin = await db.execute(
        select(OIDCProvider.id).where(OIDCProvider.is_autologin.is_(True), OIDCProvider.is_enabled.is_(True)).limit(1)
    )
    autologin_provider_id = autologin.scalar_one_or_none()

    return {
        "advanced_auth_enabled": advanced_auth_enabled,
        "smtp_configured": smtp_configured,
        "local_login_enabled": local_login_enabled,
        "autologin_provider_id": autologin_provider_id,
    }


# TTL for password-reset tokens (H-6)
_RESET_TOKEN_TTL = timedelta(hours=1)

# Rate-limit for password-reset email sends per identifier (M-A)
_MAX_PWD_RESET_SENDS = 3
_PWD_RESET_SEND_WINDOW = timedelta(minutes=15)
# L-NEW-6: per-IP cap to prevent mass-reset flooding across many addresses
_MAX_PWD_RESET_SENDS_PER_IP = 10


async def _send_reset_email_or_delete_token(
    reset_token: str,
    smtp_settings,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str,
    log_label: str,
) -> None:
    """Background task: send a password-reset email and delete the token on failure.

    C1: FastAPI silently swallows BackgroundTask exceptions.  This wrapper
    catches send failures, deletes the single-use token so it cannot be used
    (user is not locked out forever — they can request a new link), and logs at
    ERROR so operators are alerted without leaking details to the caller.
    """
    try:
        send_email(smtp_settings, to_email, subject, text_body, html_body)
        _logger.info("Password reset email sent (%s) to %s", log_label, to_email)
    except Exception as exc:  # SEC-AUTH-EXC: email-send failure → defensive token cleanup so a stuck token doesn't block re-request; no access granted, just frees future workflow
        _logger.error(
            "Password reset email failed (%s) to %s — deleting token to unblock re-request: %s",
            log_label,
            to_email,
            exc,
        )
        try:
            async with async_session() as db:
                await db.execute(
                    delete(AuthEphemeralToken).where(
                        AuthEphemeralToken.token == reset_token,
                        AuthEphemeralToken.token_type == TokenType.PASSWORD_RESET,
                    )
                )
                await db.commit()
        except Exception as db_exc:  # SEC-AUTH-EXC: nested cleanup failure logged only; no access decision made in this branch (already handling a prior failure)
            _logger.error("Failed to delete reset token after send failure: %s", db_exc)


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(
    request: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Request password reset via email (advanced auth only).

    H-6: Issues a short-lived single-use reset token and emails the user a
    secure link instead of a plaintext temporary password.  The new password is
    set only when the user clicks the link and POSTs to /forgot-password/confirm.
    """
    # #1589: forgot-password is a local-credentials flow — useless when local
    # login is disabled (the reset wouldn't grant access anyway). Same gate as
    # /auth/login, with the same env-var bypass for SSO-broken recovery.
    if not _local_login_env_bypass():
        from backend.app.models.settings import Settings as _Settings_for_local_login

        setting_row = await db.execute(
            select(_Settings_for_local_login).where(_Settings_for_local_login.key == "local_login_enabled")
        )
        row = setting_row.scalar_one_or_none()
        if row is not None and row.value.lower() != "true":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Local login is disabled — use SSO instead.",
            )
    # Check if advanced auth is enabled
    advanced_auth = await is_advanced_auth_enabled(db)
    if not advanced_auth:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Advanced authentication is not enabled",
        )

    # M-A: Rate-limit by normalised email to prevent reset-email flooding.
    # Apply unconditionally (before the user lookup) so unknown emails are also
    # throttled — this prevents both flooding and timing-based enumeration.
    identifier = request.email.lower()
    cutoff = datetime.now(timezone.utc) - _PWD_RESET_SEND_WINDOW
    rate_result = await db.execute(
        select(AuthRateLimitEvent).where(
            AuthRateLimitEvent.username == identifier,
            AuthRateLimitEvent.event_type == EventType.PASSWORD_RESET_SEND,
            AuthRateLimitEvent.occurred_at > cutoff,
        )
    )
    if len(rate_result.scalars().all()) >= _MAX_PWD_RESET_SENDS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many password reset requests. Please wait {_PWD_RESET_SEND_WINDOW.seconds // 60} minutes.",
        )

    # L-NEW-6: per-IP rate limit — prevents mass-reset flooding across many
    # different email addresses from a single source IP.
    client_ip = _get_client_ip(raw_request)
    ip_rate_result = await db.execute(
        select(AuthRateLimitEvent).where(
            AuthRateLimitEvent.username == client_ip,
            AuthRateLimitEvent.event_type == EventType.PASSWORD_RESET_IP,
            AuthRateLimitEvent.occurred_at > cutoff,
        )
    )
    if len(ip_rate_result.scalars().all()) >= _MAX_PWD_RESET_SENDS_PER_IP:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many password reset requests. Please wait {_PWD_RESET_SEND_WINDOW.seconds // 60} minutes.",
        )

    # Nit7: Always record the IP-level event (prevents spray attacks across many
    # different email addresses from one IP).  The email-level event is only
    # recorded when we actually send an email to a local user — LDAP/OIDC users
    # do not consume a slot because this flow is a no-op for them.
    db.add(AuthRateLimitEvent(username=client_ip, event_type=EventType.PASSWORD_RESET_IP))
    await db.commit()

    # Get SMTP settings
    smtp_settings = await get_smtp_settings(db)
    if not smtp_settings:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Email service is not configured",
        )

    # Find user by email — always return success to prevent email enumeration.
    user = await get_user_by_email(db, request.email)

    # M-1: exclude LDAP and OIDC users — they must use their respective provider.
    if user and user.is_active and user.auth_source not in ("ldap", "oidc"):
        try:
            # Record email-level slot only for local users who will actually receive
            # the reset email (Nit7: don't waste the user's quota for LDAP/OIDC no-ops).
            db.add(AuthRateLimitEvent(username=identifier, event_type=EventType.PASSWORD_RESET_SEND))

            now = datetime.now(timezone.utc)
            # Prune any outstanding reset tokens for this user before issuing a new one.
            await db.execute(
                delete(AuthEphemeralToken).where(
                    AuthEphemeralToken.token_type == TokenType.PASSWORD_RESET,
                    AuthEphemeralToken.username == user.username,
                )
            )
            reset_token = secrets.token_urlsafe(32)
            db.add(
                AuthEphemeralToken(
                    token=reset_token,
                    token_type=TokenType.PASSWORD_RESET,
                    username=user.username,
                    expires_at=now + _RESET_TOKEN_TTL,
                )
            )
            await db.commit()

            login_url = await get_external_login_url(db)
            # M-B: Deliver token in the URL fragment so it never reaches the server
            # in access-logs or Referer headers (mirrors H-4 for the OIDC token).
            reset_url = f"{login_url}#reset_token={reset_token}"

            subject, text_body, html_body = await create_password_reset_link_email_from_template(
                db, user.username, reset_url
            )
            # L-R9-B: send asynchronously so response time is independent of
            # whether the user exists (prevents email-existence timing oracle).
            # C1: wrapper deletes the token if SMTP fails so the user can re-request.
            background_tasks.add_task(
                _send_reset_email_or_delete_token,
                reset_token,
                smtp_settings,
                user.email,
                subject,
                text_body,
                html_body,
                "forgot_password",
            )
            _logger.info("Password reset email queued for %s", user.email)
        except Exception as e:  # SEC-AUTH-EXC: forgot-password response is intentionally generic regardless of outcome (user-enumeration defence); email failure does not grant access
            _logger.error("Failed to send password reset email: %s", e)
            # Don't reveal error to caller for security

    return ForgotPasswordResponse(
        message="If the email address is associated with an account, a password reset email has been sent."
    )


@router.post("/forgot-password/confirm", response_model=ForgotPasswordResponse)
async def forgot_password_confirm(request: ForgotPasswordConfirmRequest, db: AsyncSession = Depends(get_db)):
    """Complete a password reset by supplying the token from the reset email.

    H-6: Atomically consumes the single-use token (DELETE…RETURNING) and sets
    the new password.  Expired or already-used tokens are silently rejected with
    the same response to prevent oracle attacks.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        delete(AuthEphemeralToken)
        .where(
            AuthEphemeralToken.token == request.token,
            AuthEphemeralToken.token_type == TokenType.PASSWORD_RESET,
        )
        .returning(AuthEphemeralToken.username, AuthEphemeralToken.expires_at)
    )
    row = result.one_or_none()
    await db.commit()
    if row is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired password reset token")

    username, expires_at = row
    # SQLite returns naive datetimes; treat them as UTC.
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if now > expires_at:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired password reset token")

    user = await get_user_by_username(db, username)
    # M-1: block LDAP/OIDC users — they authenticate via their provider, not local password.
    if not user or not user.is_active or user.auth_source in ("ldap", "oidc"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired password reset token")

    user.password_hash = get_password_hash(request.new_password)
    user.password_changed_at = now  # M-R7-B: invalidate all prior JWTs
    await db.commit()
    _logger.info("Password reset completed for user '%s'", username)

    return ForgotPasswordResponse(message="Password has been reset successfully.")


@router.post("/reset-password", response_model=ResetPasswordResponse)
async def reset_user_password(
    request: ResetPasswordRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Reset a user's password and send them an email (admin only, advanced auth only)."""
    # Reload user with groups for proper is_admin check
    result = await db.execute(select(User).where(User.id == current_user.id).options(selectinload(User.groups)))
    admin_user = result.scalar_one()

    if not admin_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can reset user passwords",
        )

    # Check if advanced auth is enabled
    advanced_auth = await is_advanced_auth_enabled(db)
    if not advanced_auth:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Advanced authentication is not enabled",
        )

    # Get SMTP settings
    smtp_settings = await get_smtp_settings(db)
    if not smtp_settings:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Email service is not configured",
        )

    # Find user to reset
    result = await db.execute(select(User).where(User.id == request.user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # M-1: block LDAP/OIDC users — passwords are managed by their respective providers.
    if user.auth_source in ("ldap", "oidc"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot reset password for LDAP/OIDC users — authentication is managed by their provider",
        )

    if not user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have an email address configured",
        )

    try:
        # H-B: Issue a single-use reset link instead of generating a plaintext password.
        # The admin never sees the credential — the user sets their own password.
        now = datetime.now(timezone.utc)
        await db.execute(
            delete(AuthEphemeralToken).where(
                AuthEphemeralToken.token_type == TokenType.PASSWORD_RESET,
                AuthEphemeralToken.username == user.username,
            )
        )
        reset_token = secrets.token_urlsafe(32)
        db.add(
            AuthEphemeralToken(
                token=reset_token,
                token_type=TokenType.PASSWORD_RESET,
                username=user.username,
                expires_at=now + _RESET_TOKEN_TTL,
            )
        )
        await db.commit()

        login_url = await get_external_login_url(db)
        reset_url = f"{login_url}#reset_token={reset_token}"

        subject, text_body, html_body = await create_password_reset_link_email_from_template(
            db, user.username, reset_url
        )
        background_tasks.add_task(
            _send_reset_email_or_delete_token,
            reset_token,
            smtp_settings,
            user.email,
            subject,
            text_body,
            html_body,
            "admin_reset",
        )

        _logger.info("Admin password reset link queued for user '%s' by admin '%s'", user.username, admin_user.username)
        return ResetPasswordResponse(message=f"Password reset link sent to {user.email}")
    except Exception as e:  # SEC-AUTH-EXC: rollback + raise 500 (fail-closed); reset token state unchanged on error
        await db.rollback()
        _logger.error("Failed to send admin password reset for user '%s': %s", user.username, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send password reset link. Check server logs.",  # L-R7-B: no internal details
        )


# LDAP Authentication Helpers


async def _get_ldap_settings(db: AsyncSession) -> dict[str, str] | None:
    """Get LDAP settings from the database. Returns None if LDAP is not enabled."""
    ldap_keys = [
        "ldap_enabled",
        "ldap_server_url",
        "ldap_bind_dn",
        "ldap_bind_password",
        "ldap_search_base",
        "ldap_user_filter",
        "ldap_security",
        "ldap_group_mapping",
        "ldap_auto_provision",
        "ldap_ca_cert_path",
        "ldap_default_group",
    ]
    result = await db.execute(select(Settings).where(Settings.key.in_(ldap_keys)))
    settings = {s.key: s.value for s in result.scalars().all()}
    if settings.get("ldap_enabled", "false").lower() != "true":
        return None
    return settings


async def _provision_ldap_user(db: AsyncSession, ldap_user, ldap_config) -> User:
    """Create a new local user from LDAP authentication."""
    import logging

    from backend.app.services.ldap_service import resolve_group_mapping

    logger = logging.getLogger(__name__)

    new_user = User(
        username=ldap_user.username,
        email=ldap_user.email,
        password_hash=None,
        role="user",
        auth_source="ldap",
        is_active=True,
    )

    # Map LDAP groups to BamBuddy groups, falling back to the configured default group
    # when the user is authenticated but has no matching group mapping (#921-follow-up).
    mapped_group_names = resolve_group_mapping(ldap_user.groups, ldap_config.group_mapping)
    if not mapped_group_names and ldap_config.default_group:
        mapped_group_names = [ldap_config.default_group]
        logger.warning(
            "LDAP user %s has no mapped groups — assigning configured default group '%s'",
            ldap_user.username,
            ldap_config.default_group,
        )
    if mapped_group_names:
        groups_result = await db.execute(select(Group).where(Group.name.in_(mapped_group_names)))
        new_user.groups = list(groups_result.scalars().all())

    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    logger.info("Auto-provisioned LDAP user: %s (groups: %s)", new_user.username, mapped_group_names)
    return new_user


async def _sync_ldap_user(db: AsyncSession, user: User, ldap_user, ldap_config) -> None:
    """Sync LDAP user attributes (email, groups) on each login.

    Group sync only touches BamBuddy groups that LDAP is configured to manage —
    that is, the values of `group_mapping` plus `default_group`. Any group
    outside that set is assumed to be a manual admin assignment and is
    preserved across logins (#1292). Manual assignments to a BamBuddy group
    that IS LDAP-managed are still overridden by LDAP truth, because revoking
    access in LDAP must propagate to BamBuddy on next login.
    """
    import logging

    from backend.app.services.ldap_service import resolve_group_mapping

    logger = logging.getLogger(__name__)

    changed = False

    # Update email if changed
    if ldap_user.email and ldap_user.email != user.email:
        user.email = ldap_user.email
        changed = True

    # Compute the set of BamBuddy groups LDAP is allowed to manage. Anything
    # outside this set is left alone so manual admin assignments survive logins.
    ldap_managed_names: set[str] = set(ldap_config.group_mapping.values())
    if ldap_config.default_group:
        ldap_managed_names.add(ldap_config.default_group)

    # Resolve what LDAP says the user should currently be in.
    mapped_group_names = resolve_group_mapping(ldap_user.groups, ldap_config.group_mapping)
    if not mapped_group_names and ldap_config.default_group:
        mapped_group_names = [ldap_config.default_group]
        logger.warning(
            "LDAP user %s has no mapped groups — assigning configured default group '%s'",
            user.username,
            ldap_config.default_group,
        )

    if mapped_group_names:
        groups_result = await db.execute(select(Group).where(Group.name.in_(mapped_group_names)))
        new_ldap_groups = list(groups_result.scalars().all())
    else:
        new_ldap_groups = []

    # Preserve manual assignments to non-LDAP-managed groups; replace only
    # the LDAP-managed slice with the resolved set.
    preserved_manual_groups = [g for g in user.groups if g.name not in ldap_managed_names]
    new_groups = preserved_manual_groups + new_ldap_groups

    current_group_ids = {g.id for g in user.groups}
    new_group_ids = {g.id for g in new_groups}
    if current_group_ids != new_group_ids:
        user.groups = new_groups
        changed = True

    if changed:
        await db.commit()
        logger.info("Synced LDAP user attributes: %s", user.username)


@router.post("/ldap/test")
async def test_ldap(
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
    db: AsyncSession = Depends(get_db),
):
    """Test LDAP connection using saved settings (admin only when auth enabled)."""
    import logging

    from backend.app.services.ldap_service import parse_ldap_config, test_ldap_connection

    logger = logging.getLogger(__name__)

    ldap_settings = await _get_ldap_settings(db)
    if not ldap_settings:
        # LDAP might not be enabled yet but settings might still exist — read all keys
        ldap_keys = [
            "ldap_enabled",
            "ldap_server_url",
            "ldap_bind_dn",
            "ldap_bind_password",
            "ldap_search_base",
            "ldap_user_filter",
            "ldap_security",
            "ldap_group_mapping",
            "ldap_auto_provision",
        ]
        result = await db.execute(select(Settings).where(Settings.key.in_(ldap_keys)))
        ldap_settings = {s.key: s.value for s in result.scalars().all()}
        # Force enabled for test
        ldap_settings["ldap_enabled"] = "true"

    config = parse_ldap_config(ldap_settings)
    if not config:
        return {"success": False, "message": "LDAP server URL is not configured"}

    success, message = test_ldap_connection(config)
    if success:
        logger.info("LDAP connection test successful")
    else:
        logger.warning("LDAP connection test failed: %s", message)
    return {"success": success, "message": message}


@router.get("/ldap/status")
async def get_ldap_status(db: AsyncSession = Depends(get_db)):
    """Get LDAP authentication status."""
    # Only fetch the minimum keys needed — never load secrets
    ldap_keys = ["ldap_enabled", "ldap_server_url"]
    result = await db.execute(select(Settings).where(Settings.key.in_(ldap_keys)))
    settings = {s.key: s.value for s in result.scalars().all()}
    return {
        "ldap_enabled": settings.get("ldap_enabled", "false").lower() == "true",
        "ldap_configured": bool(settings.get("ldap_server_url")),
    }


# =============================================================================
# Manual LDAP user provisioning (#1298)
# =============================================================================
# Admins can search the directory and provision users directly from the UI
# without enabling auto-provision on login. The two endpoints below pair with
# the new "LDAP" tab in the user-create modal.


@router.get("/ldap/search", response_model=list[LDAPSearchResultResponse])
async def search_ldap_directory(
    q: str,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.USERS_CREATE),
    db: AsyncSession = Depends(get_db),
):
    """Search the LDAP directory for users matching `q`.

    Returns up to 25 candidates. The query is matched (case-insensitively, with
    wildcards on both sides) against sAMAccountName, uid, mail, displayName,
    and cn — covering both AD and OpenLDAP layouts. Each result is annotated
    with `already_provisioned` so the UI can grey out usernames that already
    exist as BamBuddy users.

    Requires USERS_CREATE permission. Minimum query length is 2 characters.
    """
    from sqlalchemy import func as sa_func

    from backend.app.services.ldap_service import parse_ldap_config, search_ldap_users

    query = q.strip()
    if len(query) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query must be at least 2 characters",
        )

    ldap_settings = await _get_ldap_settings(db)
    if not ldap_settings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LDAP is not enabled",
        )

    config = parse_ldap_config(ldap_settings)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LDAP server URL is not configured",
        )

    try:
        results = search_ldap_users(config, query, limit=25)
    except Exception as e:  # SEC-AUTH-EXC: raise 503 (fail-closed); route gated upstream by USERS_CREATE permission so detail leak is admin-only
        _logger.exception("LDAP directory search failed")
        # Admin-only endpoint — surface the underlying reason so the operator
        # can fix it (auth_middleware already restricted access to USERS_CREATE).
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LDAP search failed: {type(e).__name__}: {e}",
        )

    if not results:
        return []

    # Annotate `already_provisioned` so the SPA can dim/disable rows that map
    # to an existing local row. Case-insensitive lookup mirrors create_user.
    usernames_lower = [r.username.lower() for r in results]
    existing_query = await db.execute(select(User.username).where(sa_func.lower(User.username).in_(usernames_lower)))
    existing_lower = {str(name).lower() for name in existing_query.scalars().all()}

    return [
        LDAPSearchResultResponse(
            username=r.username,
            email=r.email,
            display_name=r.display_name,
            dn=r.dn,
            already_provisioned=r.username.lower() in existing_lower,
        )
        for r in results
    ]


@router.post("/ldap/provision", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def provision_ldap_user(
    payload: LDAPProvisionRequest,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.USERS_CREATE),
    db: AsyncSession = Depends(get_db),
):
    """Provision a BamBuddy user from an existing LDAP directory entry.

    Re-resolves the username via the service-account bind (rather than trusting
    the request body) so group mappings and email come from a fresh LDAP read.
    Applies the same group-mapping / default-group logic as the auto-provision
    login path (`_provision_ldap_user`), so behavior stays identical regardless
    of whether the user was created here or on first login.

    Requires USERS_CREATE.
    """
    from sqlalchemy import func as sa_func

    from backend.app.services.ldap_service import lookup_ldap_user, parse_ldap_config

    username = payload.username.strip()
    if not username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username is required",
        )

    ldap_settings = await _get_ldap_settings(db)
    if not ldap_settings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LDAP is not enabled",
        )

    config = parse_ldap_config(ldap_settings)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LDAP server URL is not configured",
        )

    # Look up via service bind. Service-bind failures bubble up as 503; missing
    # entries surface as 404 to distinguish "directory unreachable" from
    # "username doesn't exist in the directory" in the UI.
    try:
        ldap_user = lookup_ldap_user(config, username)
    except Exception as e:  # SEC-AUTH-EXC: raise 503 (fail-closed); LDAP provision never succeeds on lookup failure
        _logger.exception("LDAP lookup failed during provision")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LDAP lookup failed: {type(e).__name__}: {e}",
        )

    if ldap_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{username}' not found in LDAP directory",
        )

    # Reject duplicates — the canonical username from LDAP is what gets stored,
    # so the conflict check uses that rather than the request payload.
    existing = await db.execute(select(User).where(sa_func.lower(User.username) == sa_func.lower(ldap_user.username)))
    existing_user = existing.scalar_one_or_none()
    if existing_user is not None:
        if existing_user.auth_source == "ldap":
            detail = f"LDAP user '{ldap_user.username}' is already provisioned"
        else:
            detail = f"A local user with the username '{ldap_user.username}' already exists"
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

    new_user = await _provision_ldap_user(db, ldap_user, config)

    # Reload with groups eagerly loaded so _user_to_response can serialize them
    # without lazy-load warnings (matches create_user / list_users pattern).
    result = await db.execute(select(User).where(User.id == new_user.id).options(selectinload(User.groups)))
    new_user = result.scalar_one()
    _logger.info("Manually provisioned LDAP user %s (id=%d)", new_user.username, new_user.id)
    return _user_to_response(new_user)


# =============================================================================
# Long-lived camera-stream tokens (#1108)
# =============================================================================
# Camera-only V1. Issue scope: a token a user can paste into Home Assistant /
# Frigate / a kiosk and have it keep working for days/weeks rather than
# refreshing the 60-minute ephemeral token. Permission gate: CAMERA_VIEW
# (same blast radius as the existing 60-min token-mint endpoint).


def _long_lived_token_to_response(record, *, plaintext: str | None = None) -> dict:
    """Serialise a LongLivedToken row for the SPA. Plaintext is included
    only at create time (and then never again), per the issue's "shown once"
    contract.
    """
    return {
        "id": record.id,
        "user_id": record.user_id,
        "name": record.name,
        "scope": record.scope,
        "lookup_prefix": record.lookup_prefix,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "last_used_at": record.last_used_at.isoformat() if record.last_used_at else None,
        # Plaintext is the ONLY field the user ever sees in full — copied once
        # to a clipboard / kiosk config and then forgotten.
        "token": plaintext,
    }


@router.post("/tokens", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_long_lived_camera_token(
    payload: dict,
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
    db: AsyncSession = Depends(get_db),
):
    """Mint a long-lived camera-stream token (#1108).

    Body: ``{"name": str, "expires_in_days": int, "scope": "camera_stream"}``.

    The plaintext token is returned **exactly once** in the response. The DB
    only ever stores a pbkdf2 hash, so a leaked DB dump cannot replay the
    token. Hard cap of 365 days; the issue's ``expire_in: 0`` (never) is
    explicitly rejected.
    """
    from backend.app.services.long_lived_tokens import (
        ALLOWED_SCOPES,
        MAX_TOKEN_LIFETIME_DAYS,
        create_token,
    )

    # Auth-disabled path: tokens are user-owned, but if auth is off there is
    # no user to own them. Refuse rather than silently picking a random user.
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Long-lived tokens require authentication to be enabled",
        )

    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    expires_in_days = payload.get("expires_in_days")
    if not isinstance(expires_in_days, int) or expires_in_days <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"expires_in_days must be a positive integer (max {MAX_TOKEN_LIFETIME_DAYS}; #1108: no infinite tokens)"
            ),
        )
    scope = payload.get("scope", "camera_stream")
    if scope not in ALLOWED_SCOPES:
        raise HTTPException(status_code=400, detail=f"unsupported scope: {scope!r}")

    try:
        created = await create_token(
            db,
            user_id=current_user.id,
            name=name,
            expires_in_days=expires_in_days,
            scope=scope,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _logger.info(
        "Long-lived camera token created: user=%s name=%r scope=%s expires=%s",
        current_user.username,
        name,
        scope,
        created.record.expires_at.isoformat(),
    )
    return _long_lived_token_to_response(created.record, plaintext=created.plaintext)


@router.get("/tokens", response_model=list[dict])
async def list_long_lived_tokens(
    user_id: int | None = None,
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
    db: AsyncSession = Depends(get_db),
):
    """List long-lived tokens.

    Default: caller's own tokens.
    Admins can pass ``?user_id=N`` to see another user's tokens, or omit it
    to see everything (handy for leak triage).
    """
    from backend.app.services.long_lived_tokens import list_user_tokens

    # Auth-disabled installs don't have a notion of "my tokens" — refuse so
    # we don't leak a global list to whoever can hit the API.
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Long-lived tokens require authentication to be enabled",
        )

    # Reload with groups so is_admin reflects group membership reliably.
    user_with_groups = (
        await db.execute(select(User).where(User.id == current_user.id).options(selectinload(User.groups)))
    ).scalar_one()

    if user_id is None or user_id == current_user.id:
        records = await list_user_tokens(db, current_user.id)
    elif user_with_groups.is_admin:
        records = await list_user_tokens(db, user_id)
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can list other users' tokens",
        )
    return [_long_lived_token_to_response(r) for r in records]


@router.get("/tokens/all", response_model=list[dict])
async def list_all_long_lived_tokens(
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only: every active long-lived token in the system, newest first.
    Used by the leak-triage view in admin settings.
    """
    from backend.app.services.long_lived_tokens import list_all_tokens

    if current_user is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Auth required")
    user_with_groups = (
        await db.execute(select(User).where(User.id == current_user.id).options(selectinload(User.groups)))
    ).scalar_one()
    if not user_with_groups.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin only",
        )
    records = await list_all_tokens(db)
    return [_long_lived_token_to_response(r) for r in records]


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_long_lived_token(
    token_id: int,
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
    db: AsyncSession = Depends(get_db),
):
    """Revoke a long-lived token. Owners can revoke their own; admins any."""
    from backend.app.models.long_lived_token import LongLivedToken
    from backend.app.services.long_lived_tokens import revoke_token

    if current_user is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Auth required")

    record = (await db.execute(select(LongLivedToken).where(LongLivedToken.id == token_id))).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Token not found")

    if record.user_id != current_user.id:
        # Reload for is_admin so admins can revoke any user's token (leak response).
        user_with_groups = (
            await db.execute(select(User).where(User.id == current_user.id).options(selectinload(User.groups)))
        ).scalar_one()
        if not user_with_groups.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only revoke your own tokens",
            )

    revoked = await revoke_token(db, token_id)
    if not revoked:
        # Already revoked is treated as 404 for idempotency from the UI side.
        raise HTTPException(status_code=404, detail="Token not found or already revoked")
    _logger.info(
        "Long-lived camera token revoked: id=%d by user=%s",
        token_id,
        current_user.username,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/encryption-status", response_model=EncryptionStatusResponse)
async def get_encryption_status(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> EncryptionStatusResponse:
    """Report at-rest encryption status for OIDC + TOTP secrets.

    Surfaces:
      (a) whether a key is configured and where it came from
      (b) how many rows are still legacy plaintext
      (c) whether decryption is broken (no key OR key cannot decrypt existing rows)
      (d) the count of rows skipped during the last re-encryption migration

    S2: gated on SETTINGS_UPDATE so Viewers (who only have SETTINGS_READ)
    cannot read encryption-status — admin/operator only.
    """
    from sqlalchemy import case, func, not_, select

    from backend.app.core.database import get_migration_error_count
    from backend.app.core.encryption import get_key_source, is_encryption_active, mfa_decrypt
    from backend.app.models.oidc_provider import OIDCProvider
    from backend.app.models.user_totp import UserTOTP

    key_configured = is_encryption_active()
    key_source = get_key_source() or "none"

    try:
        oidc_row = await db.execute(
            select(
                func.sum(case((not_(OIDCProvider._client_secret_enc.like("fernet:%")), 1), else_=0)),
                func.sum(case((OIDCProvider._client_secret_enc.like("fernet:%"), 1), else_=0)),
            )
        )
        legacy_oidc, encrypted_oidc = oidc_row.one()
        totp_row = await db.execute(
            select(
                func.sum(case((not_(UserTOTP._secret_enc.like("fernet:%")), 1), else_=0)),
                func.sum(case((UserTOTP._secret_enc.like("fernet:%"), 1), else_=0)),
            )
        )
        legacy_totp, encrypted_totp = totp_row.one()
    except SQLAlchemyError:
        _logger.exception("Failed to query encryption row counts")
        raise HTTPException(status_code=500, detail="Failed to retrieve encryption status")

    legacy_plaintext_rows = EncryptionRowCounts(
        oidc_providers=int(legacy_oidc or 0),
        user_totp=int(legacy_totp or 0),
    )
    encrypted_rows = EncryptionRowCounts(
        oidc_providers=int(encrypted_oidc or 0),
        user_totp=int(encrypted_totp or 0),
    )

    # B4: detect "wrong key" state — sample-decrypt one encrypted row to
    # distinguish "no key" from "key configured but cannot decrypt these rows".
    # The legacy computed-field check (key_configured=False AND encrypted>0)
    # missed the case where an operator pasted a different valid Fernet key
    # (rotation, cross-deployment restore, env override) — status would show
    # green while every encrypted row was unrecoverable.
    decryption_broken = False
    total_encrypted = encrypted_rows.oidc_providers + encrypted_rows.user_totp
    if not key_configured and total_encrypted > 0:
        decryption_broken = True
    elif key_configured and total_encrypted > 0:
        sample_value: str | None = None
        try:
            if encrypted_rows.oidc_providers > 0:
                r = await db.execute(
                    select(OIDCProvider._client_secret_enc)
                    .where(OIDCProvider._client_secret_enc.like("fernet:%"))
                    .limit(1)
                )
                sample_value = r.scalar_one_or_none()
            if sample_value is None and encrypted_rows.user_totp > 0:
                r = await db.execute(select(UserTOTP._secret_enc).where(UserTOTP._secret_enc.like("fernet:%")).limit(1))
                sample_value = r.scalar_one_or_none()
        except SQLAlchemyError:
            _logger.exception("Failed to query sample encrypted row for decryption probe")
            # Over-alert is safer than silent corruption — surface as broken.
            decryption_broken = True
            sample_value = None

        if sample_value:
            try:
                mfa_decrypt(sample_value)
            except RuntimeError:
                decryption_broken = True

    return EncryptionStatusResponse(
        key_configured=key_configured,
        key_source=key_source,
        legacy_plaintext_rows=legacy_plaintext_rows,
        encrypted_rows=encrypted_rows,
        decryption_broken=decryption_broken,
        migration_error_count=get_migration_error_count(),
    )
