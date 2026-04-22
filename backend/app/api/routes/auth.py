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
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.api.routes.settings import get_external_login_url
from backend.app.core.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ALGORITHM,
    SECRET_KEY,
    Permission,
    RequirePermissionIfAuthEnabled,
    _is_token_fresh,
    _validate_api_key,
    authenticate_user,
    authenticate_user_by_email,
    create_access_token,
    get_current_active_user,
    get_password_hash,
    get_user_by_email,
    get_user_by_username,
    is_jti_revoked,
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
    ForgotPasswordConfirmRequest,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    GroupBrief,
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
                except Exception as e:
                    await db.rollback()
                    logger.error("Failed to create admin user: %s", e, exc_info=True)
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to create admin user",
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
        await set_auth_enabled(db, False)
        await db.commit()
        logger.info("Authentication disabled by admin user: %s", user.username)
        return {"message": "Authentication disabled successfully", "auth_enabled": False}
    except Exception as e:
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
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning("LDAP authentication error, falling back to local: %s", e)
            ldap_user = None

    # Try username-based authentication (skip if already authenticated via LDAP)
    if not ldap_user:
        user = await authenticate_user(db, request.username, request.password)

    # If username auth failed and advanced auth is enabled, try email-based authentication
    if not user and not ldap_user:
        advanced_auth = await is_advanced_auth_enabled(db)
        if advanced_auth:
            user = await authenticate_user_by_email(db, request.username, request.password)

    if not user:
        await record_failed_attempt(db, request.username, event_type=EventType.LOGIN_ATTEMPT)
        await record_failed_attempt(db, client_ip, event_type=EventType.LOGIN_IP)
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

    # No 2FA — issue full token immediately
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(data={"sub": user.username}, expires_delta=access_token_expires)

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        user=_user_to_response(user),
    )


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
                except Exception as exc:
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
    except Exception as e:
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
    except Exception as e:
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
    except Exception as e:
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
    except Exception as e:
        await db.rollback()
        logger.error("Failed to disable advanced authentication: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to disable advanced authentication",
        )


@router.get("/advanced-auth/status")
async def get_advanced_auth_status(db: AsyncSession = Depends(get_db)):
    """Get advanced authentication status."""
    advanced_auth_enabled = await is_advanced_auth_enabled(db)
    smtp_configured = await get_smtp_settings(db) is not None
    return {
        "advanced_auth_enabled": advanced_auth_enabled,
        "smtp_configured": smtp_configured,
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
    except Exception as exc:
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
        except Exception as db_exc:
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
        except Exception as e:
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
    except Exception as e:
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
    """Sync LDAP user attributes (email, groups) on each login."""
    import logging

    from backend.app.services.ldap_service import resolve_group_mapping

    logger = logging.getLogger(__name__)

    changed = False

    # Update email if changed
    if ldap_user.email and ldap_user.email != user.email:
        user.email = ldap_user.email
        changed = True

    # Sync group mappings — always update to match LDAP state (including revocation).
    # Fall back to the configured default group when the user has no mapped groups,
    # so authenticated LDAP users are never left permission-less.
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
        new_groups = list(groups_result.scalars().all())
    else:
        new_groups = []
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
