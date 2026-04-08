"""2FA (TOTP + Email OTP) and OIDC authentication routes.

Security model
--------------
* Pre-auth tokens  : secrets.token_urlsafe(32) stored in-memory with a 5-minute TTL.
  They are single-use and do NOT grant access to any protected resource.
* TOTP codes       : verified with pyotp (30-second window, ±1 step tolerance).
* Email OTP codes  : 6-digit numeric, hashed with pbkdf2_sha256, 10-minute TTL,
  max 5 failed attempts per code before invalidation.
* Backup codes     : 10 × 8-char alphanumeric codes, each stored as pbkdf2_sha256 hash,
  single-use.
* OIDC state       : secrets.token_urlsafe(32) bound to provider_id + nonce, 10-minute TTL.
* OIDC exchange    : secrets.token_urlsafe(32), 2-minute TTL, single-use.
* Rate limiting    : max 5 failed 2FA verification attempts per user within 15 minutes.
"""

from __future__ import annotations

import base64
import io
import logging
import secrets
import string
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pyotp
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from jwt import PyJWKClient
from passlib.context import CryptContext
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.api.routes.settings import get_setting, set_setting
from backend.app.core.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    get_current_active_user,
    get_user_by_username,
    is_auth_enabled,
)
from backend.app.core.database import get_db
from backend.app.models.oidc_provider import OIDCProvider, UserOIDCLink
from backend.app.models.user import User
from backend.app.models.user_otp_code import UserOTPCode
from backend.app.models.user_totp import UserTOTP
from backend.app.schemas.auth import (
    BackupCodesResponse,
    EmailOTPSendRequest,
    GroupBrief,
    LoginResponse,
    OIDCAuthorizeResponse,
    OIDCExchangeRequest,
    OIDCLinkResponse,
    OIDCProviderCreate,
    OIDCProviderResponse,
    OIDCProviderUpdate,
    TOTPDisableRequest,
    TOTPEnableRequest,
    TOTPEnableResponse,
    TOTPSetupResponse,
    TwoFAStatusResponse,
    TwoFAVerifyRequest,
    TwoFAVerifyResponse,
    UserResponse,
)
from backend.app.services.email_service import get_smtp_settings, send_email

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Passlib context (same scheme as auth.py)
# ---------------------------------------------------------------------------
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------
_pre_auth_tokens: dict[str, tuple[str, datetime]] = {}  # token -> (username, expiry)
_oidc_states: dict[str, tuple[int, str, datetime]] = {}  # state -> (provider_id, nonce, expiry)
_oidc_exchange_tokens: dict[str, tuple[str, datetime]] = {}  # token -> (username, expiry)
_failed_2fa_attempts: dict[str, list[datetime]] = {}  # username -> [timestamps]
_email_otp_send_times: dict[str, list[datetime]] = {}  # username -> [send timestamps]

MAX_2FA_ATTEMPTS = 5
LOCKOUT_WINDOW = timedelta(minutes=15)
MAX_EMAIL_OTP_SENDS = 3
EMAIL_OTP_SEND_WINDOW = timedelta(minutes=10)
PRE_AUTH_TOKEN_TTL = timedelta(minutes=5)
OIDC_STATE_TTL = timedelta(minutes=10)
OIDC_EXCHANGE_TTL = timedelta(minutes=2)

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/auth", tags=["2fa", "oidc"])


# ---------------------------------------------------------------------------
# Helper: user response
# ---------------------------------------------------------------------------
def _user_to_response(user: User) -> UserResponse:
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


# ---------------------------------------------------------------------------
# Helper: QR code generation
# ---------------------------------------------------------------------------
def _generate_totp_qr_b64(provisioning_uri: str) -> str:
    """Generate a base64-encoded PNG QR code for the given TOTP provisioning URI."""
    import qrcode  # type: ignore

    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(provisioning_uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------------------
# Helper: backup code generation
# ---------------------------------------------------------------------------
def _generate_backup_codes() -> tuple[list[str], list[str]]:
    """Return (plain_codes, hashed_codes) — 10 codes of 8 alphanumeric chars each."""
    alphabet = string.ascii_uppercase + string.digits
    plain = ["".join(secrets.choice(alphabet) for _ in range(8)) for _ in range(10)]
    hashed = [pwd_context.hash(c) for c in plain]
    return plain, hashed


# ---------------------------------------------------------------------------
# Pre-auth token helpers
# ---------------------------------------------------------------------------
def _cleanup_pre_auth_tokens() -> None:
    now = datetime.now(timezone.utc)
    expired = [t for t, (_, exp) in _pre_auth_tokens.items() if exp < now]
    for t in expired:
        del _pre_auth_tokens[t]


def create_pre_auth_token(username: str) -> str:
    """Create a single-use pre-auth token for the given username."""
    _cleanup_pre_auth_tokens()
    token = secrets.token_urlsafe(32)
    _pre_auth_tokens[token] = (username, datetime.now(timezone.utc) + PRE_AUTH_TOKEN_TTL)
    return token


def consume_pre_auth_token(token: str) -> str | None:
    """Validate and consume a pre-auth token, returning the username or None."""
    _cleanup_pre_auth_tokens()
    entry = _pre_auth_tokens.get(token)
    if entry is None:
        return None
    username, expiry = entry
    if datetime.now(timezone.utc) > expiry:
        del _pre_auth_tokens[token]
        return None
    del _pre_auth_tokens[token]
    return username


# ---------------------------------------------------------------------------
# Rate-limiting helpers
# ---------------------------------------------------------------------------
def check_rate_limit(username: str) -> None:
    """Raise HTTP 429 if the user has exceeded the failed 2FA attempt limit."""
    now = datetime.now(timezone.utc)
    attempts = _failed_2fa_attempts.get(username, [])
    # Keep only recent attempts within the lockout window
    recent = [t for t in attempts if now - t < LOCKOUT_WINDOW]
    _failed_2fa_attempts[username] = recent
    if len(recent) >= MAX_2FA_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed 2FA attempts. Please try again later.",
        )


def record_failed_attempt(username: str) -> None:
    """Record a failed 2FA attempt for rate-limiting purposes."""
    now = datetime.now(timezone.utc)
    attempts = _failed_2fa_attempts.get(username, [])
    attempts.append(now)
    _failed_2fa_attempts[username] = attempts


def clear_failed_attempts(username: str) -> None:
    """Clear all failed 2FA attempts for a user on successful verification."""
    _failed_2fa_attempts.pop(username, None)


def check_email_otp_send_rate(username: str) -> None:
    """Raise HTTP 429 if the user has requested too many OTP emails recently."""
    now = datetime.now(timezone.utc)
    times = _email_otp_send_times.get(username, [])
    recent = [t for t in times if now - t < EMAIL_OTP_SEND_WINDOW]
    _email_otp_send_times[username] = recent
    if len(recent) >= MAX_EMAIL_OTP_SENDS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many OTP email requests. Please wait {EMAIL_OTP_SEND_WINDOW.seconds // 60} minutes.",
        )
    _email_otp_send_times[username] = recent + [now]


# ---------------------------------------------------------------------------
# Settings helpers (email 2FA flag)
# ---------------------------------------------------------------------------
async def _get_email_2fa_enabled(db: AsyncSession, user_id: int) -> bool:
    val = await get_setting(db, f"user_{user_id}_email_2fa_enabled")
    return val == "true"


async def _set_email_2fa_enabled(db: AsyncSession, user_id: int, enabled: bool) -> None:
    await set_setting(db, f"user_{user_id}_email_2fa_enabled", "true" if enabled else "false")


# ===========================================================================
# 2FA Endpoints
# ===========================================================================


@router.get("/2fa/status", response_model=TwoFAStatusResponse)
async def get_2fa_status(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> TwoFAStatusResponse:
    """Return the current 2FA configuration for the authenticated user."""
    result = await db.execute(select(UserTOTP).where(UserTOTP.user_id == current_user.id))
    totp_record = result.scalar_one_or_none()

    totp_enabled = totp_record is not None and totp_record.is_enabled
    backup_codes_remaining = len(totp_record.backup_codes) if totp_record else 0
    email_otp_enabled = await _get_email_2fa_enabled(db, current_user.id)

    return TwoFAStatusResponse(
        totp_enabled=totp_enabled,
        email_otp_enabled=email_otp_enabled,
        backup_codes_remaining=backup_codes_remaining,
    )


@router.post("/2fa/totp/setup", response_model=TOTPSetupResponse)
async def setup_totp(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> TOTPSetupResponse:
    """Initiate TOTP setup: generates a new secret and QR code.

    Creates (or replaces) a pending UserTOTP record with is_enabled=False.
    The caller must confirm with POST /auth/2fa/totp/enable.
    """
    if not await is_auth_enabled(db):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication is not enabled")

    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(name=current_user.username, issuer_name="Bambuddy")
    qr_b64 = _generate_totp_qr_b64(provisioning_uri)

    # Upsert a pending TOTP record (is_enabled=False)
    existing = (await db.execute(select(UserTOTP).where(UserTOTP.user_id == current_user.id))).scalar_one_or_none()

    if existing:
        existing.secret = secret
        existing.is_enabled = False
        existing.backup_codes = []
    else:
        db.add(UserTOTP(user_id=current_user.id, secret=secret, is_enabled=False))

    await db.commit()

    return TOTPSetupResponse(secret=secret, qr_code_b64=qr_b64, issuer="Bambuddy")


@router.post("/2fa/totp/enable", response_model=TOTPEnableResponse)
async def enable_totp(
    body: TOTPEnableRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> TOTPEnableResponse:
    """Confirm TOTP setup by verifying a code from the authenticator app.

    On success, enables TOTP and returns 10 single-use backup codes (shown once).
    """
    result = await db.execute(select(UserTOTP).where(UserTOTP.user_id == current_user.id))
    totp_record = result.scalar_one_or_none()

    if not totp_record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="TOTP setup not initiated. Call /auth/2fa/totp/setup first."
        )

    if not pyotp.TOTP(totp_record.secret).verify(body.code, valid_window=1):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid TOTP code")

    plain_codes, hashed_codes = _generate_backup_codes()
    totp_record.is_enabled = True
    totp_record.backup_codes = hashed_codes
    await db.commit()

    return TOTPEnableResponse(
        message="TOTP enabled successfully. Store your backup codes in a safe place.",
        backup_codes=plain_codes,
    )


@router.post("/2fa/totp/disable")
async def disable_totp(
    body: TOTPDisableRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Disable TOTP by verifying a valid TOTP code or a backup code."""
    result = await db.execute(select(UserTOTP).where(UserTOTP.user_id == current_user.id))
    totp_record = result.scalar_one_or_none()

    if not totp_record or not totp_record.is_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="TOTP is not enabled")

    # Accept either a valid TOTP code or a valid backup code
    code_valid = pyotp.TOTP(totp_record.secret).verify(body.code, valid_window=1)
    if not code_valid:
        # Check backup codes
        for hashed in totp_record.backup_codes:
            if pwd_context.verify(body.code, hashed):
                code_valid = True
                break

    if not code_valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid code")

    await db.execute(delete(UserTOTP).where(UserTOTP.user_id == current_user.id))
    await db.commit()
    return {"message": "TOTP disabled"}


@router.post("/2fa/totp/regenerate-backup-codes", response_model=BackupCodesResponse)
async def regenerate_backup_codes(
    body: TOTPEnableRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> BackupCodesResponse:
    """Generate 10 new backup codes. Requires a valid TOTP code."""
    result = await db.execute(select(UserTOTP).where(UserTOTP.user_id == current_user.id))
    totp_record = result.scalar_one_or_none()

    if not totp_record or not totp_record.is_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="TOTP is not enabled")

    if not pyotp.TOTP(totp_record.secret).verify(body.code, valid_window=1):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid TOTP code")

    plain_codes, hashed_codes = _generate_backup_codes()
    totp_record.backup_codes = hashed_codes
    await db.commit()

    return BackupCodesResponse(
        backup_codes=plain_codes,
        message="Backup codes regenerated. Store them safely — they will not be shown again.",
    )


@router.post("/2fa/email/enable")
async def enable_email_otp(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Enable email-based OTP as a second factor for the current user."""
    if not current_user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must have an email address configured to enable email OTP 2FA",
        )
    await _set_email_2fa_enabled(db, current_user.id, True)
    await db.commit()
    return {"message": "Email OTP 2FA enabled"}


@router.post("/2fa/email/disable")
async def disable_email_otp(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Disable email-based OTP 2FA for the current user."""
    await _set_email_2fa_enabled(db, current_user.id, False)
    await db.commit()
    return {"message": "Email OTP 2FA disabled"}


@router.post("/2fa/email/send")
async def send_email_otp(
    body: EmailOTPSendRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Send a 6-digit OTP code to the user's email address.

    Requires a valid pre_auth_token obtained during the login flow.
    """
    username = consume_pre_auth_token(body.pre_auth_token)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired pre-auth token")

    # Enforce rate limit BEFORE re-issuing fresh token to prevent OTP email flooding
    check_email_otp_send_rate(username)

    # Re-issue a fresh pre-auth token so the same session can proceed to verify
    fresh_token = create_pre_auth_token(username)

    user = await get_user_by_username(db, username)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    if not user.email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User has no email address configured")

    smtp_settings = await get_smtp_settings(db)
    if not smtp_settings:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Email service is not configured")

    # Invalidate all existing unused OTP codes for this user
    await db.execute(
        UserOTPCode.__table__.update()  # type: ignore[attr-defined]
        .where(UserOTPCode.user_id == user.id)
        .where(UserOTPCode.used.is_(False))
        .values(used=True)
    )

    # Generate a 6-digit code
    code = str(secrets.randbelow(1_000_000)).zfill(6)
    code_hash = pwd_context.hash(code)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=UserOTPCode.OTP_TTL_MINUTES)

    otp_record = UserOTPCode(
        user_id=user.id,
        code_hash=code_hash,
        attempts=0,
        used=False,
        expires_at=expires_at,
    )
    db.add(otp_record)
    await db.commit()

    # Send email (do not log the code)
    try:
        send_email(
            smtp_settings=smtp_settings,
            to_email=user.email,
            subject="Your Bambuddy verification code",
            body_text=f"Your Bambuddy login code is: {code}\n\nThis code expires in {UserOTPCode.OTP_TTL_MINUTES} minutes and can only be used once.",
            body_html=(
                f"<p>Your <strong>Bambuddy</strong> login verification code is:</p>"
                f"<h2 style='letter-spacing:4px'>{code}</h2>"
                f"<p>This code expires in <strong>{UserOTPCode.OTP_TTL_MINUTES} minutes</strong> and can only be used once.</p>"
                f"<p>If you did not request this code, you can safely ignore this email.</p>"
            ),
        )
    except Exception as exc:
        logger.error("Failed to send OTP email to user_id=%d: %s", user.id, exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to send OTP email")

    # Return the fresh pre-auth token so the frontend can proceed to verify
    return {"message": "Code sent to your email address", "pre_auth_token": fresh_token}


@router.post("/2fa/verify", response_model=TwoFAVerifyResponse)
async def verify_2fa(
    body: TwoFAVerifyRequest,
    db: AsyncSession = Depends(get_db),
) -> TwoFAVerifyResponse:
    """Verify a 2FA code and exchange the pre_auth_token for a full JWT.

    Accepted methods: ``totp``, ``email``, ``backup``.
    """
    username = consume_pre_auth_token(body.pre_auth_token)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired pre-auth token")

    check_rate_limit(username)

    user = await get_user_by_username(db, username)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    method = body.method.lower()

    if method == "totp":
        result = await db.execute(select(UserTOTP).where(UserTOTP.user_id == user.id))
        totp_record = result.scalar_one_or_none()
        if not totp_record or not totp_record.is_enabled:
            record_failed_attempt(username)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="TOTP is not enabled for this user")
        if not pyotp.TOTP(totp_record.secret).verify(body.code, valid_window=1):
            record_failed_attempt(username)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid TOTP code")

    elif method == "email":
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(UserOTPCode)
            .where(UserOTPCode.user_id == user.id)
            .where(UserOTPCode.used.is_(False))
            .where(UserOTPCode.expires_at > now)
            .order_by(UserOTPCode.created_at.desc())
        )
        otp_record = result.scalar_one_or_none()
        if not otp_record:
            record_failed_attempt(username)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="No valid OTP code found. Request a new one."
            )

        if otp_record.attempts >= UserOTPCode.MAX_ATTEMPTS:
            otp_record.used = True
            await db.commit()
            record_failed_attempt(username)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="OTP code has been invalidated after too many attempts"
            )

        if not pwd_context.verify(body.code, otp_record.code_hash):
            otp_record.attempts += 1
            await db.commit()
            record_failed_attempt(username)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid OTP code")

        # Mark as used
        otp_record.used = True
        await db.commit()

    elif method == "backup":
        result = await db.execute(select(UserTOTP).where(UserTOTP.user_id == user.id))
        totp_record = result.scalar_one_or_none()
        if not totp_record or not totp_record.is_enabled:
            record_failed_attempt(username)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="TOTP is not enabled for this user")

        matched_index: int | None = None
        for idx, hashed in enumerate(totp_record.backup_codes):
            if pwd_context.verify(body.code, hashed):
                matched_index = idx
                break

        if matched_index is None:
            record_failed_attempt(username)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid backup code")

        # Remove the used backup code
        updated_codes = [c for i, c in enumerate(totp_record.backup_codes) if i != matched_index]
        totp_record.backup_codes = updated_codes
        await db.commit()

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid 2FA method. Use 'totp', 'email', or 'backup'."
        )

    # Verification succeeded — clear rate limit and issue full JWT
    clear_failed_attempts(username)

    access_token = create_access_token(
        data={"sub": user.username},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    # Reload with groups for permission calculation
    result = await db.execute(select(User).where(User.id == user.id).options(selectinload(User.groups)))
    user = result.scalar_one()

    return TwoFAVerifyResponse(
        access_token=access_token,
        token_type="bearer",
        user=_user_to_response(user),
    )


@router.delete("/2fa/admin/{user_id}")
async def admin_disable_2fa(
    user_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Admin endpoint: disable all 2FA for a given user."""
    # Reload current user with groups for is_admin check
    result = await db.execute(select(User).where(User.id == current_user.id).options(selectinload(User.groups)))
    admin = result.scalar_one()
    if not admin.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    # Delete TOTP record
    await db.execute(delete(UserTOTP).where(UserTOTP.user_id == user_id))

    # Disable email 2FA setting
    await _set_email_2fa_enabled(db, user_id, False)

    # Invalidate all OTP codes
    await db.execute(
        UserOTPCode.__table__.update()  # type: ignore[attr-defined]
        .where(UserOTPCode.user_id == user_id)
        .values(used=True)
    )

    await db.commit()
    logger.info("Admin %s disabled all 2FA for user_id=%d", admin.username, user_id)
    return {"message": "2FA disabled for user"}


# ===========================================================================
# OIDC Endpoints
# ===========================================================================


@router.get("/oidc/providers", response_model=list[OIDCProviderResponse])
async def list_oidc_providers(
    db: AsyncSession = Depends(get_db),
) -> list[OIDCProviderResponse]:
    """List all enabled OIDC providers (public)."""
    result = await db.execute(select(OIDCProvider).where(OIDCProvider.is_enabled.is_(True)))
    providers = result.scalars().all()
    return [OIDCProviderResponse.model_validate(p) for p in providers]


@router.get("/oidc/providers/all", response_model=list[OIDCProviderResponse])
async def list_all_oidc_providers(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> list[OIDCProviderResponse]:
    """List ALL OIDC providers including disabled ones (admin only)."""
    result = await db.execute(select(User).where(User.id == current_user.id).options(selectinload(User.groups)))
    admin = result.scalar_one()
    if not admin.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    result2 = await db.execute(select(OIDCProvider))
    providers = result2.scalars().all()
    return [OIDCProviderResponse.model_validate(p) for p in providers]


@router.post("/oidc/providers", response_model=OIDCProviderResponse, status_code=status.HTTP_201_CREATED)
async def create_oidc_provider(
    body: OIDCProviderCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> OIDCProviderResponse:
    """Create a new OIDC provider (admin only)."""
    result = await db.execute(select(User).where(User.id == current_user.id).options(selectinload(User.groups)))
    admin = result.scalar_one()
    if not admin.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    provider = OIDCProvider(
        name=body.name,
        issuer_url=body.issuer_url.rstrip("/"),
        client_id=body.client_id,
        client_secret=body.client_secret,
        scopes=body.scopes,
        is_enabled=body.is_enabled,
        auto_create_users=body.auto_create_users,
        icon_url=body.icon_url,
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    return OIDCProviderResponse.model_validate(provider)


@router.put("/oidc/providers/{provider_id}", response_model=OIDCProviderResponse)
async def update_oidc_provider(
    provider_id: int,
    body: OIDCProviderUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> OIDCProviderResponse:
    """Update an existing OIDC provider (admin only)."""
    result = await db.execute(select(User).where(User.id == current_user.id).options(selectinload(User.groups)))
    admin = result.scalar_one()
    if not admin.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    result2 = await db.execute(select(OIDCProvider).where(OIDCProvider.id == provider_id))
    provider = result2.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")

    for field, value in body.model_dump(exclude_none=True).items():
        if field == "issuer_url" and value:
            value = value.rstrip("/")
        setattr(provider, field, value)

    await db.commit()
    await db.refresh(provider)
    return OIDCProviderResponse.model_validate(provider)


@router.delete("/oidc/providers/{provider_id}")
async def delete_oidc_provider(
    provider_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete an OIDC provider and all its user links (admin only)."""
    result = await db.execute(select(User).where(User.id == current_user.id).options(selectinload(User.groups)))
    admin = result.scalar_one()
    if not admin.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    result2 = await db.execute(select(OIDCProvider).where(OIDCProvider.id == provider_id))
    provider = result2.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")

    await db.delete(provider)
    await db.commit()
    return {"message": "Provider deleted"}


@router.get("/oidc/authorize/{provider_id}", response_model=OIDCAuthorizeResponse)
async def oidc_authorize(
    provider_id: int,
    db: AsyncSession = Depends(get_db),
) -> OIDCAuthorizeResponse:
    """Return the OIDC authorization URL for the given provider."""
    result = await db.execute(
        select(OIDCProvider).where(OIDCProvider.id == provider_id).where(OIDCProvider.is_enabled.is_(True))
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found or not enabled")

    # Fetch discovery document
    discovery_url = f"{provider.issuer_url}/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(discovery_url)
            resp.raise_for_status()
            discovery = resp.json()
    except Exception as exc:
        logger.error("Failed to fetch OIDC discovery for provider %d: %s", provider_id, exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch OIDC discovery document")

    authorization_endpoint = discovery.get("authorization_endpoint")
    if not authorization_endpoint:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="OIDC discovery document missing authorization_endpoint"
        )

    external_url = await _get_base_external_url(db)
    redirect_uri = f"{external_url}/api/v1/auth/oidc/callback"

    # Clean up expired states before adding a new one (prevent memory growth)
    now = datetime.now(timezone.utc)
    expired_states = [s for s, (_, _, exp) in _oidc_states.items() if exp < now]
    for s in expired_states:
        del _oidc_states[s]

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    _oidc_states[state] = (provider_id, nonce, now + OIDC_STATE_TTL)

    import urllib.parse

    params = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": provider.client_id,
            "redirect_uri": redirect_uri,
            "scope": provider.scopes,
            "state": state,
            "nonce": nonce,
        }
    )
    auth_url = f"{authorization_endpoint}?{params}"
    return OIDCAuthorizeResponse(auth_url=auth_url)


@router.get("/oidc/callback")
async def oidc_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Handle the OIDC authorization code callback from the identity provider."""
    external_url = await _get_base_external_url(db)
    frontend_error_url = f"{external_url}/?oidc_error="

    try:
        if error:
            logger.warning("OIDC callback received error: %s", error)
            return RedirectResponse(url=f"{frontend_error_url}oidc_provider_error", status_code=302)

        if not code or not state:
            return RedirectResponse(url=f"{frontend_error_url}missing_parameters", status_code=302)

        # Validate state
        state_entry = _oidc_states.pop(state, None)
        if not state_entry:
            return RedirectResponse(url=f"{frontend_error_url}invalid_state", status_code=302)

        provider_id, nonce, state_expiry = state_entry
        if datetime.now(timezone.utc) > state_expiry:
            return RedirectResponse(url=f"{frontend_error_url}state_expired", status_code=302)

        # Load provider
        result = await db.execute(select(OIDCProvider).where(OIDCProvider.id == provider_id))
        provider = result.scalar_one_or_none()
        if not provider:
            return RedirectResponse(url=f"{frontend_error_url}provider_not_found", status_code=302)

        redirect_uri = f"{external_url}/api/v1/auth/oidc/callback"

        # Fetch discovery document
        discovery_url = f"{provider.issuer_url}/.well-known/openid-configuration"
        async with httpx.AsyncClient(timeout=10) as client:
            disc_resp = await client.get(discovery_url)
            disc_resp.raise_for_status()
            discovery = disc_resp.json()

        token_endpoint = discovery.get("token_endpoint")
        jwks_uri = discovery.get("jwks_uri")
        if not token_endpoint or not jwks_uri:
            return RedirectResponse(url=f"{frontend_error_url}invalid_discovery_document", status_code=302)

        # Exchange authorization code for tokens
        async with httpx.AsyncClient(timeout=15) as client:
            token_resp = await client.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": provider.client_id,
                    "client_secret": provider.client_secret,
                },
                headers={"Accept": "application/json"},
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()

        id_token = token_data.get("id_token")
        if not id_token:
            return RedirectResponse(url=f"{frontend_error_url}no_id_token", status_code=302)

        # Validate ID token using JWKS
        jwks_client = PyJWKClient(jwks_uri)
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256", "ES256", "RS384", "ES384", "RS512"],
            audience=provider.client_id,
            issuer=provider.issuer_url,
        )

        # Verify nonce
        if claims.get("nonce") != nonce:
            return RedirectResponse(url=f"{frontend_error_url}nonce_mismatch", status_code=302)

        provider_sub: str = claims.get("sub", "")
        provider_email: str | None = claims.get("email")

        if not provider_sub:
            return RedirectResponse(url=f"{frontend_error_url}missing_sub_claim", status_code=302)

        # Look up existing OIDC link
        link_result = await db.execute(
            select(UserOIDCLink)
            .where(UserOIDCLink.provider_id == provider_id)
            .where(UserOIDCLink.provider_user_id == provider_sub)
        )
        link = link_result.scalar_one_or_none()

        user: User | None = None

        if link:
            user_result = await db.execute(
                select(User).where(User.id == link.user_id).options(selectinload(User.groups))
            )
            user = user_result.scalar_one_or_none()
        elif provider.auto_create_users:
            # Derive a safe username from email local-part or subject claim.
            # Strip characters that are invalid in usernames (allow only
            # alphanumeric, underscores, hyphens, dots) and cap at 30 chars.
            import re

            if provider_email:
                raw = provider_email.split("@")[0]
            else:
                raw = provider_sub[:30]
            candidate = re.sub(r"[^a-zA-Z0-9._-]", "", raw)[:30] or "oidcuser"

            # Ensure uniqueness
            username = candidate
            counter = 1
            while True:
                existing = await get_user_by_username(db, username)
                if not existing:
                    break
                username = f"{candidate}{counter}"
                counter += 1

            # Create new user with a random unusable password
            from backend.app.core.auth import get_password_hash

            new_user = User(
                username=username,
                email=provider_email,
                password_hash=get_password_hash(secrets.token_urlsafe(32)),
                role="user",
                is_active=True,
            )
            db.add(new_user)
            await db.flush()

            new_link = UserOIDCLink(
                user_id=new_user.id,
                provider_id=provider_id,
                provider_user_id=provider_sub,
                provider_email=provider_email,
            )
            db.add(new_link)
            await db.commit()

            # Reload with groups
            user_result = await db.execute(
                select(User).where(User.id == new_user.id).options(selectinload(User.groups))
            )
            user = user_result.scalar_one()
            logger.info("Auto-created user '%s' via OIDC provider %d", username, provider_id)
        else:
            return RedirectResponse(url=f"{frontend_error_url}no_linked_account", status_code=302)

        if not user or not user.is_active:
            return RedirectResponse(url=f"{frontend_error_url}account_inactive", status_code=302)

        # Issue an OIDC exchange token (short-lived, single-use)
        exchange_token = secrets.token_urlsafe(32)
        _oidc_exchange_tokens[exchange_token] = (
            user.username,
            datetime.now(timezone.utc) + OIDC_EXCHANGE_TTL,
        )

        return RedirectResponse(url=f"{external_url}/?oidc_token={exchange_token}", status_code=302)

    except Exception as exc:
        logger.error("Unexpected error in OIDC callback: %s", exc, exc_info=True)
        try:
            return RedirectResponse(url=f"{frontend_error_url}internal_error", status_code=302)
        except Exception:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="OIDC callback failed")


@router.post("/oidc/exchange", response_model=LoginResponse)
async def oidc_exchange(
    body: OIDCExchangeRequest,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """Exchange an OIDC exchange token (from the callback redirect) for a full JWT."""
    now = datetime.now(timezone.utc)
    entry = _oidc_exchange_tokens.pop(body.oidc_token, None)
    if not entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired OIDC exchange token")

    username, expiry = entry
    if now > expiry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OIDC exchange token has expired")

    user = await get_user_by_username(db, username)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    # Reload with groups
    result = await db.execute(select(User).where(User.id == user.id).options(selectinload(User.groups)))
    user = result.scalar_one()

    access_token = create_access_token(
        data={"sub": user.username},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        user=_user_to_response(user),
        requires_2fa=False,
    )


@router.get("/oidc/links", response_model=list[OIDCLinkResponse])
async def list_oidc_links(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> list[OIDCLinkResponse]:
    """List all OIDC provider links for the current user."""
    result = await db.execute(
        select(UserOIDCLink).where(UserOIDCLink.user_id == current_user.id).options(selectinload(UserOIDCLink.provider))
    )
    links = result.scalars().all()
    return [
        OIDCLinkResponse(
            id=link.id,
            provider_id=link.provider_id,
            provider_name=link.provider.name,
            provider_email=link.provider_email,
            created_at=link.created_at.isoformat(),
        )
        for link in links
    ]


@router.delete("/oidc/links/{provider_id}")
async def remove_oidc_link(
    provider_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Remove the OIDC link between the current user and a provider."""
    result = await db.execute(
        select(UserOIDCLink)
        .where(UserOIDCLink.user_id == current_user.id)
        .where(UserOIDCLink.provider_id == provider_id)
    )
    link = result.scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OIDC link not found")

    await db.delete(link)
    await db.commit()
    return {"message": "OIDC link removed"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
async def _get_base_external_url(db: AsyncSession) -> str:
    """Return the base external URL (no trailing slash, no /login suffix)."""
    import os

    external_url = await get_setting(db, "external_url")
    if external_url:
        return external_url.rstrip("/")
    return os.environ.get("APP_URL", "http://localhost:5173").rstrip("/")
