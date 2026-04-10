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
import hashlib
import io
import logging
import os
import re
import secrets
import string
import urllib.parse
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
    RequirePermissionIfAuthEnabled,
    create_access_token,
    get_current_active_user,
    get_password_hash,
    get_user_by_username,
    is_auth_enabled,
    verify_password,
)
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.auth_ephemeral import AuthEphemeralToken, AuthRateLimitEvent
from backend.app.models.group import Group
from backend.app.models.oidc_provider import OIDCProvider, UserOIDCLink
from backend.app.models.user import User
from backend.app.models.user_otp_code import UserOTPCode
from backend.app.models.user_totp import UserTOTP
from backend.app.schemas.auth import (
    BackupCodesResponse,
    EmailOTPDisableRequest,
    EmailOTPEnableConfirmRequest,
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


def _as_utc(dt: datetime) -> datetime:
    """Return *dt* with UTC timezone attached.

    SQLite/aiosqlite strips timezone info when reading DateTime(timezone=True)
    columns back – the stored value is always UTC, so we just re-attach the
    info when doing Python-level comparisons.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Passlib context (same scheme as auth.py)
# ---------------------------------------------------------------------------
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# ---------------------------------------------------------------------------
# TTL / rate-limit constants
# ---------------------------------------------------------------------------
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
# DB-backed pre-auth token helpers
# ---------------------------------------------------------------------------
async def create_pre_auth_token(db: AsyncSession, username: str) -> str:
    """Create a single-use pre-auth token stored in the DB."""
    now = datetime.now(timezone.utc)
    # Prune expired tokens opportunistically (keep table small)
    await db.execute(
        delete(AuthEphemeralToken).where(
            AuthEphemeralToken.token_type == "pre_auth",
            AuthEphemeralToken.expires_at < now,
        )
    )
    token = secrets.token_urlsafe(32)
    db.add(
        AuthEphemeralToken(
            token=token,
            token_type="pre_auth",
            username=username,
            expires_at=now + PRE_AUTH_TOKEN_TTL,
        )
    )
    await db.commit()
    return token


async def consume_pre_auth_token(db: AsyncSession, token: str) -> str | None:
    """Atomically validate and consume a pre-auth token. Returns username or None.

    Uses DELETE...RETURNING so two concurrent requests with the same token cannot
    both succeed — only the first DELETE finds the row.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        delete(AuthEphemeralToken)
        .where(
            AuthEphemeralToken.token == token,
            AuthEphemeralToken.token_type == "pre_auth",
            AuthEphemeralToken.expires_at > now,
        )
        .returning(AuthEphemeralToken.username)
    )
    row = result.one_or_none()
    if row is None:
        return None
    await db.commit()
    return row[0]


async def peek_pre_auth_token(db: AsyncSession, token: str) -> str | None:
    """Validate a pre-auth token and return the username WITHOUT consuming it.

    Used to check the token (e.g. for rate-limiting) before deciding to consume it.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(AuthEphemeralToken.username).where(
            AuthEphemeralToken.token == token,
            AuthEphemeralToken.token_type == "pre_auth",
            AuthEphemeralToken.expires_at > now,
        )
    )
    row = result.one_or_none()
    return row[0] if row is not None else None


# ---------------------------------------------------------------------------
# DB-backed rate-limiting helpers
# ---------------------------------------------------------------------------
async def check_rate_limit(db: AsyncSession, username: str) -> None:
    """Raise HTTP 429 if the user has exceeded the failed 2FA attempt limit.

    The username is normalised to lower-case so case-variant login attempts
    (which all resolve to the same user) share the same rate-limit bucket.
    """
    username_key = username.lower()
    now = datetime.now(timezone.utc)
    cutoff = now - LOCKOUT_WINDOW
    result = await db.execute(
        select(AuthRateLimitEvent).where(
            AuthRateLimitEvent.username == username_key,
            AuthRateLimitEvent.event_type == "2fa_attempt",
            AuthRateLimitEvent.occurred_at > cutoff,
        )
    )
    recent_count = len(result.scalars().all())
    if recent_count >= MAX_2FA_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed 2FA attempts. Please try again later.",
        )


async def record_failed_attempt(db: AsyncSession, username: str) -> None:
    """Record a failed 2FA attempt for rate-limiting purposes."""
    db.add(AuthRateLimitEvent(username=username.lower(), event_type="2fa_attempt"))
    await db.commit()


async def clear_failed_attempts(db: AsyncSession, username: str) -> None:
    """Delete all recorded 2FA attempts for a user on successful verification."""
    await db.execute(
        delete(AuthRateLimitEvent).where(
            AuthRateLimitEvent.username == username.lower(),
            AuthRateLimitEvent.event_type == "2fa_attempt",
        )
    )
    await db.commit()


async def check_email_otp_send_rate(db: AsyncSession, username: str) -> None:
    """Raise HTTP 429 if the user has requested too many OTP emails recently."""
    username_key = username.lower()
    now = datetime.now(timezone.utc)
    cutoff = now - EMAIL_OTP_SEND_WINDOW
    result = await db.execute(
        select(AuthRateLimitEvent).where(
            AuthRateLimitEvent.username == username_key,
            AuthRateLimitEvent.event_type == "email_send",
            AuthRateLimitEvent.occurred_at > cutoff,
        )
    )
    recent_count = len(result.scalars().all())
    if recent_count >= MAX_EMAIL_OTP_SENDS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many OTP email requests. Please wait {EMAIL_OTP_SEND_WINDOW.seconds // 60} minutes.",
        )
    db.add(AuthRateLimitEvent(username=username_key, event_type="email_send"))
    await db.commit()


# ---------------------------------------------------------------------------
# TOTP replay-protection helper
# ---------------------------------------------------------------------------
def _assert_totp_not_replayed(totp_obj: pyotp.TOTP, totp_record: UserTOTP) -> None:
    """Raise HTTP 400 if the current time-step code was already consumed.

    pyotp.verify() does not track used codes; this helper stores the 30-second
    counter of the last accepted code so the same 6-digit code cannot be used
    twice within one 30-second window (replay attack).
    """
    counter = totp_obj.timecode(datetime.now(timezone.utc))
    if totp_record.last_totp_counter is not None and totp_record.last_totp_counter == counter:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This TOTP code has already been used. Please wait for the next code.",
        )
    totp_record.last_totp_counter = counter


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
    """Disable TOTP by verifying a valid TOTP code or a backup code.

    I10: Rate-limited to prevent backup-code brute-forcing from a hijacked session.
    """
    await check_rate_limit(db, current_user.username)

    result = await db.execute(select(UserTOTP).where(UserTOTP.user_id == current_user.id))
    totp_record = result.scalar_one_or_none()

    if not totp_record or not totp_record.is_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="TOTP is not enabled")

    # Accept either a valid TOTP code or a valid backup code
    totp_obj = pyotp.TOTP(totp_record.secret)
    code_valid = totp_obj.verify(body.code, valid_window=1)
    if code_valid:
        _assert_totp_not_replayed(totp_obj, totp_record)
    else:
        # Check backup codes
        for hashed in totp_record.backup_codes:
            if pwd_context.verify(body.code, hashed):
                code_valid = True
                break

    if not code_valid:
        await record_failed_attempt(db, current_user.username)
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
    """Generate 10 new backup codes. Requires a valid TOTP code.

    I10: Rate-limited to prevent brute-forcing from a hijacked session.
    """
    await check_rate_limit(db, current_user.username)

    result = await db.execute(select(UserTOTP).where(UserTOTP.user_id == current_user.id))
    totp_record = result.scalar_one_or_none()

    if not totp_record or not totp_record.is_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="TOTP is not enabled")

    totp_obj = pyotp.TOTP(totp_record.secret)
    if not totp_obj.verify(body.code, valid_window=1):
        await record_failed_attempt(db, current_user.username)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid TOTP code")
    _assert_totp_not_replayed(totp_obj, totp_record)

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
    """Step 1 of email OTP enable: send a verification code to the user's email.

    C5: Proof of possession — the user must prove they control the registered email
    address before email 2FA is activated.  Returns a ``setup_token`` that must be
    passed to POST /auth/2fa/email/enable/confirm together with the received code.
    """
    if not current_user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must have an email address configured to enable email OTP 2FA",
        )

    smtp_settings = await get_smtp_settings(db)
    if not smtp_settings:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Email service is not configured")

    # Generate and store the setup token (reuse AuthEphemeralToken with type "email_otp_setup")
    now = datetime.now(timezone.utc)
    # Prune any existing pending setup tokens for this user
    await db.execute(
        delete(AuthEphemeralToken).where(
            AuthEphemeralToken.token_type == "email_otp_setup",
            AuthEphemeralToken.username == current_user.username,
        )
    )

    code = str(secrets.randbelow(1_000_000)).zfill(6)
    code_hash = pwd_context.hash(code)
    setup_token = secrets.token_urlsafe(32)

    db.add(
        AuthEphemeralToken(
            token=setup_token,
            token_type="email_otp_setup",
            username=current_user.username,
            # Reuse the nonce field to store the code hash
            nonce=code_hash,
            expires_at=now + timedelta(minutes=10),
        )
    )
    await db.commit()

    try:
        send_email(
            smtp_settings=smtp_settings,
            to_email=current_user.email,
            subject="Verify your Bambuddy email address for 2FA",
            body_text=(
                f"Your Bambuddy email 2FA setup code is: {code}\n\n"
                "Enter this code to confirm email-based two-factor authentication.\n"
                "The code expires in 10 minutes."
            ),
            body_html=(
                "<p>To enable <strong>email-based two-factor authentication</strong> on your Bambuddy account, "
                "enter the code below:</p>"
                f"<h2 style='letter-spacing:4px'>{code}</h2>"
                "<p>The code expires in <strong>10 minutes</strong>. "
                "If you did not request this, you can safely ignore this email.</p>"
            ),
        )
    except Exception as exc:
        logger.error("Failed to send email OTP setup code to user_id=%d: %s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to send verification email"
        )

    return {"message": "Verification code sent to your email address", "setup_token": setup_token}


@router.post("/2fa/email/enable/confirm")
async def confirm_enable_email_otp(
    body: EmailOTPEnableConfirmRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Step 2 of email OTP enable: verify the code and activate email 2FA.

    Consumes the setup_token (single-use) returned by POST /auth/2fa/email/enable.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        delete(AuthEphemeralToken)
        .where(
            AuthEphemeralToken.token == body.setup_token,
            AuthEphemeralToken.token_type == "email_otp_setup",
            AuthEphemeralToken.username == current_user.username,
        )
        .returning(AuthEphemeralToken.nonce, AuthEphemeralToken.expires_at)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired setup token")

    code_hash, setup_expires_at = row
    await db.commit()

    if now > _as_utc(setup_expires_at):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Setup token has expired. Please start over."
        )

    if not pwd_context.verify(body.code, code_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification code")

    await _set_email_2fa_enabled(db, current_user.id, True)
    await db.commit()
    return {"message": "Email OTP 2FA enabled"}


@router.post("/2fa/email/disable")
async def disable_email_otp(
    body: EmailOTPDisableRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Disable email-based OTP 2FA for the current user.

    C6: Re-authentication required — the caller must supply their account password
    to prevent a hijacked session from silently removing a second factor.
    LDAP/OIDC-only users (no local password) are exempt from this check.
    """
    if current_user.password_hash:
        if not verify_password(body.password, current_user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")
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
    # Peek (validate without consuming) first so a rate-limit rejection does not
    # permanently burn the caller's pre-auth token.
    username = await peek_pre_auth_token(db, body.pre_auth_token)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired pre-auth token")

    # Enforce rate limit BEFORE consuming the token to prevent OTP email flooding.
    await check_email_otp_send_rate(db, username)

    # Now consume the original token (prevents the same token sending multiple emails).
    consumed = await consume_pre_auth_token(db, body.pre_auth_token)
    if not consumed:
        # Raced with another request or token just expired — treat as invalid.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired pre-auth token")

    # Re-issue a fresh pre-auth token so the same session can proceed to verify
    fresh_token = await create_pre_auth_token(db, username)

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

    The pre_auth_token is NOT consumed on failed verification attempts so the
    user can retry without restarting the login flow.  It is only consumed once
    verification succeeds, preventing token replay after success.
    """
    # Peek without consuming — bad codes must not burn the session token.
    username = await peek_pre_auth_token(db, body.pre_auth_token)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired pre-auth token")

    await check_rate_limit(db, username)

    user = await get_user_by_username(db, username)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    method = body.method

    if method == "totp":
        result = await db.execute(select(UserTOTP).where(UserTOTP.user_id == user.id))
        totp_record = result.scalar_one_or_none()
        if not totp_record or not totp_record.is_enabled:
            await record_failed_attempt(db, username)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="TOTP is not enabled for this user")
        totp_obj = pyotp.TOTP(totp_record.secret)
        if not totp_obj.verify(body.code, valid_window=1):
            await record_failed_attempt(db, username)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid TOTP code")
        _assert_totp_not_replayed(totp_obj, totp_record)

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
            await record_failed_attempt(db, username)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="No valid OTP code found. Request a new one."
            )

        if otp_record.attempts >= UserOTPCode.MAX_ATTEMPTS:
            otp_record.used = True
            await db.commit()
            await record_failed_attempt(db, username)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="OTP code has been invalidated after too many attempts"
            )

        if not pwd_context.verify(body.code, otp_record.code_hash):
            otp_record.attempts += 1
            await db.commit()
            await record_failed_attempt(db, username)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid OTP code")

        # Mark as used
        otp_record.used = True
        await db.commit()

    else:  # method == "backup"
        result = await db.execute(select(UserTOTP).where(UserTOTP.user_id == user.id))
        totp_record = result.scalar_one_or_none()
        if not totp_record or not totp_record.is_enabled:
            await record_failed_attempt(db, username)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="TOTP is not enabled for this user")

        matched_index: int | None = None
        for idx, hashed in enumerate(totp_record.backup_codes):
            if pwd_context.verify(body.code, hashed):
                matched_index = idx
                break

        if matched_index is None:
            await record_failed_attempt(db, username)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid backup code")

        # Atomically remove the used backup code by writing the updated list back
        # under the same DB transaction that commits the success.
        updated_codes = [c for i, c in enumerate(totp_record.backup_codes) if i != matched_index]
        totp_record.backup_codes = updated_codes
        await db.commit()

    # Verification succeeded — consume the pre-auth token (single-use enforcement)
    # and clear the rate-limit counter.
    await consume_pre_auth_token(db, body.pre_auth_token)
    await clear_failed_attempts(db, username)

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
    _: User | None = RequirePermissionIfAuthEnabled(Permission.USERS_UPDATE),
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
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
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
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
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
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
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
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
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

    now = datetime.now(timezone.utc)
    # Prune expired OIDC states from the DB
    await db.execute(
        delete(AuthEphemeralToken).where(
            AuthEphemeralToken.token_type == "oidc_state",
            AuthEphemeralToken.expires_at < now,
        )
    )
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)

    # PKCE (S256) – required by PocketID and recommended for all OIDC flows
    code_verifier = secrets.token_urlsafe(48)  # 64-char URL-safe string
    code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()

    db.add(
        AuthEphemeralToken(
            token=state,
            token_type="oidc_state",
            provider_id=provider_id,
            nonce=nonce,
            code_verifier=code_verifier,
            expires_at=now + OIDC_STATE_TTL,
        )
    )
    await db.commit()

    params = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": provider.client_id,
            "redirect_uri": redirect_uri,
            "scope": provider.scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    auth_url = f"{authorization_endpoint}?{params}"
    return OIDCAuthorizeResponse(auth_url=auth_url)


@router.get("/oidc/callback")
async def oidc_callback(
    code: str | None = Query(default=None, max_length=512),
    state: str | None = Query(default=None, max_length=512),
    error: str | None = Query(default=None, max_length=256),
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

        # Atomically validate and consume OIDC state from DB (I6: single-use enforcement).
        # DELETE...RETURNING ensures concurrent callbacks with the same state token
        # cannot both succeed — only the first DELETE finds the row.
        now = datetime.now(timezone.utc)
        state_del = await db.execute(
            delete(AuthEphemeralToken)
            .where(
                AuthEphemeralToken.token == state,
                AuthEphemeralToken.token_type == "oidc_state",
            )
            .returning(
                AuthEphemeralToken.provider_id,
                AuthEphemeralToken.nonce,
                AuthEphemeralToken.code_verifier,
                AuthEphemeralToken.expires_at,
            )
        )
        state_row = state_del.one_or_none()
        if state_row is None:
            await db.rollback()
            return RedirectResponse(url=f"{frontend_error_url}invalid_state", status_code=302)

        provider_id, nonce, code_verifier, state_expires_at = state_row
        await db.commit()

        if now > _as_utc(state_expires_at):
            return RedirectResponse(url=f"{frontend_error_url}state_expired", status_code=302)

        # Load provider
        result = await db.execute(select(OIDCProvider).where(OIDCProvider.id == provider_id))
        provider = result.scalar_one_or_none()
        if not provider:
            return RedirectResponse(url=f"{frontend_error_url}provider_not_found", status_code=302)

        redirect_uri = f"{external_url}/api/v1/auth/oidc/callback"

        # ── Step 1: Fetch discovery document ────────────────────────────────
        discovery_url = f"{provider.issuer_url}/.well-known/openid-configuration"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                disc_resp = await client.get(discovery_url)
                disc_resp.raise_for_status()
                discovery = disc_resp.json()
        except Exception as exc:
            logger.error("OIDC discovery fetch failed for provider %d: %s", provider_id, exc)
            return RedirectResponse(url=f"{frontend_error_url}discovery_failed", status_code=302)

        token_endpoint = discovery.get("token_endpoint")
        jwks_uri = discovery.get("jwks_uri")
        if not token_endpoint or not jwks_uri:
            return RedirectResponse(url=f"{frontend_error_url}invalid_discovery_document", status_code=302)

        # ── Step 2: Exchange authorization code for tokens ───────────────────
        token_form: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": provider.client_id,
        }
        if provider.client_secret:
            token_form["client_secret"] = provider.client_secret
        if code_verifier:
            token_form["code_verifier"] = code_verifier

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                token_resp = await client.post(
                    token_endpoint,
                    data=token_form,
                    headers={"Accept": "application/json"},
                )
        except Exception as exc:
            logger.error("OIDC token exchange request failed for provider %d: %s", provider_id, exc)
            return RedirectResponse(url=f"{frontend_error_url}token_exchange_network_error", status_code=302)

        if not token_resp.is_success:
            try:
                err_body = token_resp.json()
                oidc_err = err_body.get("error", "")
                oidc_desc = err_body.get("error_description", "")
            except Exception:
                oidc_err = ""
                oidc_desc = token_resp.text[:200]
            logger.error(
                "OIDC token exchange HTTP %d for provider %d. redirect_uri=%r error=%r desc=%r",
                token_resp.status_code,
                provider_id,
                redirect_uri,
                oidc_err,
                oidc_desc,
            )
            # Encode the OIDC error code into the redirect so the user sees it in the toast.
            # URL-encode the value to prevent query-parameter injection from provider responses.
            raw_err = oidc_err[:40] if oidc_err else str(token_resp.status_code)
            safe_err = urllib.parse.quote(raw_err, safe="")
            return RedirectResponse(
                url=f"{frontend_error_url}token_exchange_{safe_err}",
                status_code=302,
            )

        try:
            token_data = token_resp.json()
        except Exception as exc:
            logger.error("OIDC token exchange non-JSON response for provider %d: %s", provider_id, exc)
            return RedirectResponse(url=f"{frontend_error_url}token_exchange_bad_response", status_code=302)

        id_token = token_data.get("id_token")
        if not id_token:
            # Only log the keys present — values may contain secrets (access_token, etc.)
            logger.error(
                "OIDC token response missing id_token for provider %d; keys present: %s",
                provider_id,
                list(token_data.keys()),
            )
            return RedirectResponse(url=f"{frontend_error_url}no_id_token", status_code=302)

        # ── Step 3: Fetch JWKS and validate ID token ─────────────────────────
        # Use the issuer from the discovery document as the canonical value (OIDC Core
        # §3.1.3.7 requires iss == discovery issuer exactly).  We strip trailing slashes
        # from both sides because some providers (e.g. older PocketID versions) are
        # inconsistent between the discovery issuer and the JWT iss claim.
        discovery_issuer: str = discovery.get("issuer", provider.issuer_url).rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=10) as jwks_http:
                jwks_resp = await jwks_http.get(jwks_uri)
                jwks_resp.raise_for_status()
                jwks_data = jwks_resp.json()

            jwks_client = PyJWKClient(jwks_uri)
            jwks_client.fetch_data = lambda: jwks_data  # type: ignore[method-assign]
            signing_key = jwks_client.get_signing_key_from_jwt(id_token)

            # Decode without PyJWT issuer check; we validate manually below with
            # trailing-slash normalisation so both strict and lenient providers work.
            claims = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256", "ES256", "RS384", "ES384", "RS512"],
                audience=provider.client_id,
                options={"verify_iss": False},
            )
        except Exception as exc:
            logger.error("OIDC JWT validation failed for provider %d: %s", provider_id, exc, exc_info=True)
            return RedirectResponse(url=f"{frontend_error_url}token_validation_failed", status_code=302)

        # Validate issuer with trailing-slash normalisation (I8: require truthy iss)
        token_iss = claims.get("iss")
        if not token_iss:
            logger.warning("OIDC token missing iss claim for provider %d", provider_id)
            return RedirectResponse(url=f"{frontend_error_url}missing_iss_claim", status_code=302)
        if token_iss.rstrip("/") != discovery_issuer:
            logger.warning(
                "OIDC issuer mismatch for provider %d: jwt iss=%r, discovery issuer=%r",
                provider_id,
                token_iss,
                discovery_issuer,
            )
            return RedirectResponse(url=f"{frontend_error_url}issuer_mismatch", status_code=302)

        # Verify nonce — fail closed: we always send a nonce, so the provider must echo it.
        # Skipping the check when nonce is absent would allow CSRF on non-nonce providers.
        token_nonce = claims.get("nonce")
        if token_nonce is None or token_nonce != nonce:
            logger.warning("OIDC nonce mismatch for provider %d (present=%r)", provider_id, token_nonce is not None)
            return RedirectResponse(url=f"{frontend_error_url}nonce_mismatch", status_code=302)

        provider_sub: str = claims.get("sub", "")
        if not provider_sub:
            return RedirectResponse(url=f"{frontend_error_url}missing_sub_claim", status_code=302)

        # C1: Only trust the email claim when the provider explicitly marks it verified.
        # Treating absent email_verified as verified enables account-takeover: an attacker
        # could register an unverified email with an IdP and auto-link to an existing account.
        # Fail closed: require email_verified == True; absent/False both drop the email.
        raw_email: str | None = claims.get("email")
        email_verified = claims.get("email_verified")
        if email_verified is not True:
            if raw_email:
                logger.info(
                    "OIDC provider %d: ignoring email for sub=%r because email_verified=%r",
                    provider_id,
                    provider_sub,
                    email_verified,
                )
            provider_email: str | None = None
        else:
            provider_email = raw_email

        # ── Step 4: Resolve / create user ────────────────────────────────────
        try:
            # 1. Look up existing OIDC link
            link_result = await db.execute(
                select(UserOIDCLink)
                .where(UserOIDCLink.provider_id == provider_id)
                .where(UserOIDCLink.provider_user_id == provider_sub)
            )
            link = link_result.scalar_one_or_none()

            user: User | None = None

            if link:
                # Existing link → load the linked user
                user_result = await db.execute(
                    select(User).where(User.id == link.user_id).options(selectinload(User.groups))
                )
                user = user_result.scalar_one_or_none()
            else:
                # 2. No OIDC link yet — check for an existing user with the same email
                email_user: User | None = None
                if provider_email:
                    eu_result = await db.execute(
                        select(User).where(User.email == provider_email).options(selectinload(User.groups))
                    )
                    email_user = eu_result.scalar_one_or_none()

                if email_user:
                    # Auto-link the existing account (works regardless of auto_create_users)
                    db.add(
                        UserOIDCLink(
                            user_id=email_user.id,
                            provider_id=provider_id,
                            provider_user_id=provider_sub,
                            provider_email=provider_email,
                        )
                    )
                    await db.commit()
                    user = email_user
                    logger.info(
                        "Auto-linked existing user '%s' to OIDC provider %d via email match",
                        email_user.username,
                        provider_id,
                    )
                elif provider.auto_create_users:
                    # 3. No existing user — create one
                    if provider_email:
                        raw = provider_email.split("@")[0]
                    else:
                        raw = provider_sub[:30]
                    candidate = re.sub(r"[^a-zA-Z0-9._-]", "", raw)[:30] or "oidcuser"

                    username = candidate
                    counter = 1
                    while True:
                        existing = await get_user_by_username(db, username)
                        if not existing:
                            break
                        username = f"{candidate}{counter}"
                        counter += 1

                    new_user = User(
                        username=username,
                        email=provider_email,
                        password_hash=get_password_hash(secrets.token_urlsafe(32)),
                        role="user",
                        is_active=True,
                    )
                    db.add(new_user)
                    await db.flush()

                    # I9: Assign new OIDC users to the default "Viewers" group so they
                    # have read-only access rather than starting with no permissions.
                    viewers_result = await db.execute(select(Group).where(Group.name == "Viewers"))
                    viewers_group = viewers_result.scalar_one_or_none()
                    if viewers_group:
                        new_user.groups.append(viewers_group)

                    db.add(
                        UserOIDCLink(
                            user_id=new_user.id,
                            provider_id=provider_id,
                            provider_user_id=provider_sub,
                            provider_email=provider_email,
                        )
                    )
                    await db.commit()

                    user_result = await db.execute(
                        select(User).where(User.id == new_user.id).options(selectinload(User.groups))
                    )
                    user = user_result.scalar_one()
                    logger.info("Auto-created user '%s' via OIDC provider %d", username, provider_id)
                else:
                    return RedirectResponse(url=f"{frontend_error_url}no_linked_account", status_code=302)

            if not user or not user.is_active:
                return RedirectResponse(url=f"{frontend_error_url}account_inactive", status_code=302)

            # Issue an OIDC exchange token (short-lived, single-use) stored in DB.
            # I7: Opportunistically prune expired exchange tokens to keep the table small.
            now2 = datetime.now(timezone.utc)
            await db.execute(
                delete(AuthEphemeralToken).where(
                    AuthEphemeralToken.token_type == "oidc_exchange",
                    AuthEphemeralToken.expires_at < now2,
                )
            )
            exchange_token = secrets.token_urlsafe(32)
            db.add(
                AuthEphemeralToken(
                    token=exchange_token,
                    token_type="oidc_exchange",
                    username=user.username,
                    expires_at=now2 + OIDC_EXCHANGE_TTL,
                )
            )
            await db.commit()

            return RedirectResponse(url=f"{external_url}/login?oidc_token={exchange_token}", status_code=302)

        except Exception as exc:
            logger.error("OIDC user resolution failed for provider %d: %s", provider_id, exc, exc_info=True)
            try:
                await db.rollback()
            except Exception:
                pass
            return RedirectResponse(url=f"{frontend_error_url}user_resolution_failed", status_code=302)

    except Exception as exc:
        exc_name = type(exc).__name__
        logger.error("Unexpected error in OIDC callback (%s): %s", exc_name, exc, exc_info=True)
        try:
            return RedirectResponse(url=f"{frontend_error_url}internal_{exc_name}", status_code=302)
        except Exception:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="OIDC callback failed")


@router.post("/oidc/exchange", response_model=LoginResponse)
async def oidc_exchange(
    body: OIDCExchangeRequest,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """Exchange an OIDC exchange token (from the callback redirect) for a full JWT.

    C4: If the resolved user has 2FA enabled the exchange returns a pre_auth_token
    (requires_2fa=True) instead of a full JWT.  The frontend must then complete the
    2FA step exactly as it would after a password-based login.
    """
    now = datetime.now(timezone.utc)
    # Atomically consume the exchange token (DELETE...RETURNING prevents replay).
    consume_result = await db.execute(
        delete(AuthEphemeralToken)
        .where(
            AuthEphemeralToken.token == body.oidc_token,
            AuthEphemeralToken.token_type == "oidc_exchange",
        )
        .returning(AuthEphemeralToken.username, AuthEphemeralToken.expires_at)
    )
    row = consume_result.one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired OIDC exchange token")

    username, expires_at = row
    await db.commit()

    if now > _as_utc(expires_at):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OIDC exchange token has expired")

    user = await get_user_by_username(db, username)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    # Reload with groups
    result = await db.execute(select(User).where(User.id == user.id).options(selectinload(User.groups)))
    user = result.scalar_one()

    # C4: Check whether the user has any 2FA method enabled.
    totp_result = await db.execute(select(UserTOTP).where(UserTOTP.user_id == user.id))
    totp_record = totp_result.scalar_one_or_none()
    totp_enabled = totp_record is not None and totp_record.is_enabled
    email_2fa_enabled = await _get_email_2fa_enabled(db, user.id)

    if totp_enabled or email_2fa_enabled:
        # User has 2FA — issue a pre_auth_token and let the frontend handle the 2FA step.
        two_fa_methods: list[str] = []
        if totp_enabled:
            two_fa_methods.append("totp")
        if email_2fa_enabled:
            two_fa_methods.append("email")
        if totp_enabled:
            two_fa_methods.append("backup")
        pre_auth_token = await create_pre_auth_token(db, user.username)
        return LoginResponse(
            requires_2fa=True,
            pre_auth_token=pre_auth_token,
            two_fa_methods=two_fa_methods,
            user=_user_to_response(user),
        )

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
    external_url = await get_setting(db, "external_url")
    if external_url:
        return external_url.rstrip("/")
    return os.environ.get("APP_URL", "http://localhost:5173").rstrip("/")
